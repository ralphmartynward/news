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


# Map sender-domain suffix → extractor function
SENDER_EXTRACTORS: dict[str, Callable[[str, str, datetime, str], list[dict[str, Any]]]] = {
    "toulouse.lessentiel.fr": _extract_lessentiel,
}

# Fallback source label for unrecognised senders
DEFAULT_SOURCE = "newsletter"


def _classify_sender(from_addr: str) -> tuple[str, Callable | None]:
    """Return (source_key, extractor_fn_or_None)."""
    if "@" not in from_addr:
        return DEFAULT_SOURCE, None
    domain = from_addr.rsplit("@", 1)[-1].strip().lower()
    for known, fn in SENDER_EXTRACTORS.items():
        if domain.endswith(known):
            return known.split(".")[-2] if "." in known else known, fn
    return DEFAULT_SOURCE, None


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

        # Fallback: one item per email
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
