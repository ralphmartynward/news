"""Inbox fetcher — reads parsed newsletter emails from Cloudflare KV.

The Cloudflare Email Worker (worker-newsletters/) parses incoming mail to
newsletters@lavillerose.com and stores each as a JSON entry in the
NEWSLETTERS KV namespace with a 7-day TTL.

Per-sender extractors split newsletters into individual article items.
Unrecognised senders get a single item (title=subject, text=body).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

CF_ACCOUNT_ID = "ed16e312ebd79c520a405e778f8643ed"
CF_KV_NAMESPACE_ID = "f4b82aa62a454db5b38aeed094e415fb"
CF_API_BASE = "https://api.cloudflare.com/client/v4"
REQUEST_TIMEOUT_S = 20
TEXT_CHARS_CAP = 8000       # generic fallback cap
LESSENTIEL_TEXT_CAP = 5000  # per article, full content (feeds synthesis)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TRACKING_DOMAINS = ("list-manage.com", "mailchi.mp", "sendgrid.net", "click.", "track.", "unsubscribe")
_LINK_RE = re.compile(r'<a\s+[^>]*href="(https?://[^"]+)"', re.IGNORECASE)

# Transactional email detection (subscription confirmations, welcome emails, etc.)
_TRANSACTIONAL_SUBJECT_RE = re.compile(
    r"(confirmation\s+d[e’]\s*inscription|bienvenue|welcome\s+to|newsletter\s+confirm"
    r"|confirmer\s+votre\s+(email|adresse|inscription)|please\s+(confirm|verify)\s+your"
    r"|forwarding\s+confirmation|mail\s+forwarding|gmail\s+forwarding"
    r"|delivery\s+(failure|status|notification)|undelivered\s+mail|mailer.daemon"
    r"|out\s+of\s+office|automatic\s+reply|réponse\s+automatique)",
    re.IGNORECASE,
)
_SYSTEM_SENDER_DOMAINS = frozenset({
    "google.com", "accounts.google.com", "googlemail.com",
    "microsoft.com", "outlook.com", "hotmail.com",
    "apple.com", "icloud.com",
})
_TRANSACTIONAL_BODY_RE = re.compile(
    r"(e-mail\s+de\s+confirmation|confirmer\s+votre\s+inscription"
    r"|vous\s+[êe]tes\s+(bien\s+)?inscrit|cliquez\s+ici\s+pour\s+(confirmer|valider)"
    r"|please\s+(confirm|verify)\s+your\s+(email|subscription))",
    re.IGNORECASE,
)

# Clutch day-section and event markers
_CLUTCH_DAY_RE = re.compile(r"⚡+\s*([A-ZÀ-ÿ]+(?:\s+[A-ZÀ-ÿ]+)*\s+\d+\s+[A-ZÀ-ÿ]+(?:\s+[A-ZÀ-ÿ]+)*)\s*⚡+", re.IGNORECASE)
_CLUTCH_STOP_MARKERS = ("- CITY GUIDE -", "CITY GUIDE", "- LA VID", "Clutcho", "+ d'événements")


def _is_transactional(subject: str, text: str, from_addr: str = "") -> bool:
    """Return True for clearly non-editorial emails (subscription confirmations, etc.)."""
    if _TRANSACTIONAL_SUBJECT_RE.search(subject):
        return True
    if _TRANSACTIONAL_BODY_RE.search(text[:3000]):
        return True
    if from_addr and "@" in from_addr:
        domain = from_addr.rsplit("@", 1)[-1].strip(">").lower()
        if any(domain == d or domain.endswith("." + d) for d in _SYSTEM_SENDER_DOMAINS):
            return True
    return False


def _strip_html(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html or "")).strip()


def _representative_url(html: str) -> str | None:
    for url in _LINK_RE.findall(html or ""):
        if not any(t in url.lower() for t in _TRACKING_DOMAINS):
            return url
    return None


# ── Per-sender extractors ──────────────────────────────────────────────────

def _extract_lessentiel(
    html: str,
    _text: str,
    received_at: datetime,
    from_addr: str,
) -> list[dict[str, Any]]:
    """Split one L'Essentiel newsletter into individual article items.

    Structure per article:
      <table class="tmob">
        ...
        <h1>N - Title text 🚧</h1>
        <p class="first-paragraph">Lead text...</p>
        ...
        <a href="mailto:...%0Ahttps://www.lessentiel.fr/newsletter/toulouse/YYYY-MM-DD%23{id}">
        ...
      </table>
    Promo blocks have anchor #autopromo (non-numeric) and are filtered out.
    """
    try:
        from bs4 import BeautifulSoup  # already in requirements.txt
    except ImportError:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    anchor_re = re.compile(
        r"lessentiel\.fr/newsletter/toulouse/(\d{4}-\d{2}-\d{2})(?:%23|#)(\d+)"
    )
    numbered_re = re.compile(r"^\d+\s*[-–—]\s*")

    for table in soup.find_all("table", class_="tmob"):
        h1 = table.find("h1")
        if not h1:
            continue

        title_raw = h1.get_text(strip=True)
        # Only process numbered articles ("1 - Title", "2 - Title" …)
        if not numbered_re.match(title_raw):
            continue
        title = numbered_re.sub("", title_raw).strip()
        if not title:
            continue

        # Full article content: extract all readable text from the content
        # area (lead + sub-headings + bullet points). This feeds synthesis,
        # not the end-user; Claude will distil it into 4-6 sentences.
        content_div = table.find("div", style=lambda s: s and "margin-top:20px" in s)
        if content_div:
            # remove share / social blocks
            for share_div in content_div.find_all("div", style=lambda s: s and "text-align:center" in (s or "")):
                share_div.decompose()
            summary = content_div.get_text(separator="\n", strip=True)
        else:
            lead = table.find("p", class_="first-paragraph")
            summary = lead.get_text(separator=" ", strip=True) if lead else ""

        # URL: L'Essentiel has no standalone article pages — the newsletter
        # IS the article. Use the newsletter date page with the article anchor
        # so each article has a unique URL (required: url is the cache primary
        # key, so shared URLs would collapse all articles to one cache entry).
        # The anchor also filters out promo blocks (#autopromo is non-numeric).
        url: str | None = None
        for a in table.find_all("a", href=True):
            m = anchor_re.search(a["href"])
            if m:
                date_str, anchor_id = m.group(1), m.group(2)
                url = f"https://www.lessentiel.fr/newsletter/toulouse/{date_str}#{anchor_id}"
                break

        if not url:
            continue  # promo block — no numeric anchor found

        items.append({
            "source": "lessentiel",
            "url": url,
            "title": title,
            "published_at": received_at.isoformat(),
            "raw_html": None,
            "extracted_text": summary[:LESSENTIEL_TEXT_CAP],
            "item_type": "news",
            "event_date": None,
            "metadata": {"from": from_addr},
        })

    return items


_OT_DATE_RE = re.compile(
    r"^(DU\s+\d+\s+AU\s+\d+\s+[A-ZÉÈÊËÀÙÏÎÔÛÂ]+|"
    r"(?:LUNDI|MARDI|MERCREDI|JEUDI|VENDREDI|SAMEDI|DIMANCHE)\s+\d+\s+[A-ZÉÈÊËÀÙÏÎÔÛÂ]+)",
    re.IGNORECASE,
)
_OT_SKIP_RE = re.compile(
    r"(voir le contenu|se d[eé]sabonner|unsubscribe|©|copyright"
    r"|MailingFS|EN SAVOIR PLUS|Les immanquables|Et bien plus encore"
    r"|/\*|\*/)",
    re.IGNORECASE,
)


def _extract_officetourisme(
    html: str,
    text: str,
    received_at: datetime,
    from_addr: str,
) -> list[dict[str, Any]]:
    """Extract individual events from the Office de Tourisme weekend digest.

    The email is HTML-only (base64). We parse the HTML, strip boilerplate, then
    split on 'EN SAVOIR PLUS' links — each block is one event with its date,
    name, audience tags, and description. Each becomes a separate item so that
    individual events appear as distinct cards in the digest.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["style", "script", "head"]):
            tag.decompose()

        # Collect (text_block, href) pairs — one per "EN SAVOIR PLUS" link
        event_blocks: list[tuple[str, str]] = []
        current_lines: list[str] = []
        fallback_url = f"https://www.toulouse-tourisme.com/agenda#{received_at.strftime('%Y-%m-%d')}"

        for node in soup.recursiveChildGenerator():
            if isinstance(node, str):
                line = node.strip()
                if line and not _OT_SKIP_RE.search(line):
                    current_lines.append(line)
            elif hasattr(node, "name"):
                if node.name == "a" and "EN SAVOIR PLUS" in node.get_text():
                    href = node.get("href", "") or fallback_url
                    block_text = "\n".join(current_lines).strip()
                    if block_text:
                        event_blocks.append((block_text, href))
                    current_lines = []

        if not event_blocks:
            return []

        items: list[dict[str, Any]] = []
        date_slug = received_at.strftime("%Y-%m-%d")

        for idx, (block, href) in enumerate(event_blocks):
            lines = [l for l in block.splitlines() if l.strip()]
            if not lines:
                continue

            # First line matching the date pattern → date label; next line is the event name
            title = ""
            date_label = ""
            desc_lines: list[str] = []
            i = 0
            while i < len(lines):
                if not date_label and _OT_DATE_RE.match(lines[i]):
                    date_label = lines[i].strip()
                    if i + 1 < len(lines):
                        # Strip audience tags (// EN FAMILLE //)
                        raw_name = re.sub(r"\s*//.*", "", lines[i + 1]).strip()
                        title = raw_name
                        i += 2
                    else:
                        i += 1
                else:
                    desc_lines.append(lines[i])
                    i += 1

            if not title:
                title = lines[0]
            if date_label:
                title = f"{title} – {date_label.title()}"

            excerpt = "\n".join(desc_lines)[:LESSENTIEL_TEXT_CAP]

            # Use a stable unique URL: tracking href if present, else fallback with index
            url = href if href.startswith("http") else f"{fallback_url}-{idx}"

            items.append({
                "source": "office_tourisme",
                "url": url,
                "title": title,
                "published_at": received_at.isoformat(),
                "raw_html": None,
                "extracted_text": excerpt,
                "item_type": "event",
                "event_date": None,
                "metadata": {"from": from_addr},
            })

        return items

    except Exception:
        return []


def _extract_clutch(
    html: str,
    text: str,
    received_at: datetime,
    from_addr: str,
) -> list[dict[str, Any]]:
    """Split one Clutch newsletter into per-day event items.

    Structure: day headers ⚡ LUNDI 12 MAI ⚡ followed by event blocks
    (each starting with ➤), separated by --- lines.
    """
    parts = _CLUTCH_DAY_RE.split(text)
    if len(parts) < 3:
        return []

    items: list[dict[str, Any]] = []
    date_slug = received_at.strftime("%Y-%m-%d")

    for i in range(1, len(parts), 2):
        day_label = " ".join(parts[i].split()).title()
        if i + 1 >= len(parts):
            break
        content = parts[i + 1]

        # Trim at promotional / non-editorial sections
        for marker in _CLUTCH_STOP_MARKERS:
            idx = content.find(marker)
            if idx != -1:
                content = content[:idx]

        content = content.strip()
        if not content or "➤" not in content:
            continue

        # Split by separator lines (--- blocks) and keep blocks with ➤
        event_blocks = []
        for block in re.split(r"\n\s*-{5,}\s*\n", content):
            block = block.strip()
            if "➤" in block:
                event_blocks.append(block)

        if not event_blocks:
            continue

        excerpt = "\n\n".join(event_blocks)[:LESSENTIEL_TEXT_CAP]
        day_slug = re.sub(r"[^a-z0-9]+", "-", day_label.lower()).strip("-")

        items.append({
            "source": "clutch",
            "url": f"https://www.clutchmag.fr/evenements#{date_slug}-{day_slug}",
            "title": f"Plans Clutch – {day_label}",
            "published_at": received_at.isoformat(),
            "raw_html": None,
            "extracted_text": excerpt,
            "item_type": "event",
            "event_date": None,
            "metadata": {"from": from_addr, "day": day_label},
        })

    return items


# Map sender-domain suffix → extractor function
SENDER_EXTRACTORS: dict[str, Callable[[str, str, datetime, str], list[dict[str, Any]]]] = {
    "toulouse.lessentiel.fr": _extract_lessentiel,
    "clutchmag.fr": _extract_clutch,
    "tourinsoft.com": _extract_officetourisme,
}

def _classify_sender(from_addr: str) -> tuple[str, Callable | None]:
    """Return (source_key, extractor_fn_or_None).

    For unrecognised senders, returns the root domain (e.g. "actu.fr") so
    the card shows something more informative than a generic "Newsletter" label.
    """
    if "@" not in from_addr:
        return "newsletter", None
    # Extract bare email address from "Display Name <email@domain>" format
    email = from_addr
    if "<" in from_addr:
        m = re.search(r"<([^>]+)>", from_addr)
        if m:
            email = m.group(1).strip()
    domain = email.rsplit("@", 1)[-1].strip().lower()
    for known, fn in SENDER_EXTRACTORS.items():
        if domain.endswith(known):
            return known.split(".")[-2] if "." in known else known, fn
    # Derive root domain as source key (e.g. "newsletter.actu.fr" → "actu.fr")
    parts = domain.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain, None


# ── Cloudflare KV helpers ──────────────────────────────────────────────────

def _api_get(path: str, token: str) -> requests.Response:
    r = requests.get(
        f"{CF_API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT_S,
    )
    r.raise_for_status()
    return r


def _list_keys(token: str) -> list[str]:
    keys: list[str] = []
    cursor: str | None = None
    while True:
        qs = "?limit=100" + (f"&cursor={cursor}" if cursor else "")
        r = _api_get(
            f"/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}/keys{qs}",
            token,
        )
        data = r.json()
        keys.extend(item["name"] for item in data.get("result", []))
        cursor = data.get("result_info", {}).get("cursor")
        if not cursor:
            break
    return keys


def _get_value(token: str, key: str) -> dict[str, Any] | None:
    from urllib.parse import quote

    try:
        r = _api_get(
            f"/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}/values/{quote(key, safe='')}",
            token,
        )
    except requests.RequestException:
        return None
    try:
        return json.loads(r.text)
    except json.JSONDecodeError:
        return None


# ── Main fetch ─────────────────────────────────────────────────────────────

def fetch(within_hours: int = 24) -> list[dict[str, Any]]:
    token = os.environ.get("CF_API_TOKEN", "").strip()
    if not token:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
    items: list[dict[str, Any]] = []

    for key in _list_keys(token):
        payload = _get_value(token, key)
        if not payload:
            continue

        received_str = payload.get("receivedAt") or ""
        try:
            received_at = datetime.fromisoformat(received_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if received_at < cutoff:
            continue

        from_addr = payload.get("from", "")
        html = payload.get("html") or ""
        text = payload.get("text") or _strip_html(html)
        source_key, extractor = _classify_sender(from_addr)

        if extractor:
            extracted = extractor(html, text, received_at, from_addr)
            if extracted:
                items.extend(extracted)
                continue

        # Fallback: one item per email — skip transactional/system emails
        subject = (payload.get("subject") or "").strip()
        if _is_transactional(subject, text, from_addr):
            print(f"inbox: skipping transactional email — {subject[:80]!r}")
            continue

        url = _representative_url(html) or f"mailto:{from_addr or 'unknown'}"
        items.append({
            "source": source_key,
            "url": url,
            "title": (payload.get("subject") or "(no subject)").strip(),
            "published_at": received_at.isoformat(),
            "raw_html": None,
            "extracted_text": text[:TEXT_CHARS_CAP],
            "item_type": "news",
            "event_date": None,
            "metadata": {"from": from_addr, "kv_key": key},
        })

    return items


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    fetched = fetch()
    print(f"inbox: {len(fetched)} items")
    for item in fetched[:10]:
        print(f"  [{item['source']}] {item['title']}")
        print(f"    {item['url']}")
