"""Inbox fetcher — reads parsed newsletter emails from Cloudflare KV.

The Cloudflare Email Worker (worker-newsletters/) parses incoming mail to
newsletters@lavillerose.com and stores each as a JSON entry in the
NEWSLETTERS KV namespace with a 7-day TTL. This fetcher pulls those entries
via the Cloudflare REST API and returns them as standard-shape items.

For v1 each email = one item (title=subject, summary=first chars of text
body, source classified by from-domain). When we have real newsletter
samples, we can split per-article inside each email.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

CF_ACCOUNT_ID = "ed16e312ebd79c520a405e778f8643ed"
CF_KV_NAMESPACE_ID = "f4b82aa62a454db5b38aeed094e415fb"
CF_API_BASE = "https://api.cloudflare.com/client/v4"
REQUEST_TIMEOUT_S = 20

# Map sender domain → (source_key, item_type guess). Item type is just a
# default; synthesis will reclassify per cluster anyway.
SENDER_PROFILES: dict[str, tuple[str, str]] = {
    "lessentiel.fr": ("lessentiel", "news"),
    # add more newsletters here as you subscribe to them
}

DEFAULT_SOURCE = ("newsletter", "news")
TEXT_CHARS_CAP = 8000


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
        qs = f"?limit=100" + (f"&cursor={cursor}" if cursor else "")
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
    try:
        r = _api_get(
            f"/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NAMESPACE_ID}/values/{key}",
            token,
        )
    except requests.RequestException:
        return None
    try:
        return json.loads(r.text)
    except json.JSONDecodeError:
        return None


def _classify_sender(from_address: str) -> tuple[str, str]:
    if "@" not in from_address:
        return DEFAULT_SOURCE
    domain = from_address.rsplit("@", 1)[-1].strip().lower()
    for known_domain, profile in SENDER_PROFILES.items():
        if domain.endswith(known_domain):
            return profile
    return DEFAULT_SOURCE


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_LINK_RE = re.compile(r'<a\s+[^>]*href="(https?://[^"]+)"', re.IGNORECASE)
_TRACKING_DOMAINS = (
    "list-manage.com",
    "mailchi.mp",
    "sendgrid.net",
    "click.",
    "track.",
    "unsubscribe",
)


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html or "")
    return _WS_RE.sub(" ", text).strip()


def _representative_url(html: str) -> str | None:
    """Best-effort: pick the first non-tracking external link from the email."""
    for url in _LINK_RE.findall(html or ""):
        low = url.lower()
        if any(t in low for t in _TRACKING_DOMAINS):
            continue
        return url
    return None


def fetch(within_hours: int = 24) -> list[dict[str, Any]]:
    token = os.environ.get("CF_API_TOKEN", "").strip()
    if not token:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
    items: list[dict[str, Any]] = []

    keys = _list_keys(token)
    for key in keys:
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

        source_key, item_type = _classify_sender(payload.get("from", ""))
        html = payload.get("html") or ""
        text = (payload.get("text") or _strip_html(html))[:TEXT_CHARS_CAP]
        title = (payload.get("subject") or "(no subject)").strip()

        url = _representative_url(html) or f"mailto:{payload.get('from', 'unknown')}"

        items.append(
            {
                "source": source_key,
                "url": url,
                "title": title,
                "published_at": received_at.isoformat(),
                "raw_html": None,
                "extracted_text": text,
                "item_type": item_type,
                "event_date": None,
                "metadata": {
                    "from": payload.get("from"),
                    "kv_key": key,
                },
            }
        )

    return items


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    fetched = fetch()
    print(f"inbox: {len(fetched)} items")
    for item in fetched[:5]:
        print(f"  [{item['published_at']}] {item['source']} · {item['title']}")
        print(f"    {item['url']}")
