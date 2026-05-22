"""Toulouscope fetcher — scrapes the homepage for article URLs and extracts
content via trafilatura. Article URLs encode the publish date in the path:
`/YYYY/MM/DD/<slug>-<id>.php`.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
import trafilatura

LISTING_URL = "https://www.toulouscope.fr/"
SITE_BASE = "https://www.toulouscope.fr"
ARTICLE_RE = re.compile(r'href="(/(\d{4})/(\d{2})/(\d{2})/[^"#?]+\.php)"')
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT_S = 20
INTER_REQUEST_DELAY_S = 0.5
DEFAULT_WITHIN_DAYS = 2  # 48h window keeps yesterday's articles without re-surfacing a week of backlog


def _fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_S)
    r.raise_for_status()
    return r.text


def _article_urls(listing_html: str, cutoff: datetime) -> list[tuple[str, datetime]]:
    seen: dict[str, datetime] = {}
    for match in ARTICLE_RE.finditer(listing_html):
        path, year, month, day = match.group(1), match.group(2), match.group(3), match.group(4)
        try:
            pub = datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
        except ValueError:
            continue
        if pub < cutoff:
            continue
        url = SITE_BASE + path
        if url not in seen:
            seen[url] = pub
    return sorted(seen.items(), key=lambda kv: kv[1], reverse=True)


def fetch(within_hours: int | None = None) -> list[dict[str, Any]]:
    days = (
        max(1, within_hours // 24)
        if within_hours is not None
        else DEFAULT_WITHIN_DAYS
    )
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    listing_html = _fetch_html(LISTING_URL)
    candidates = _article_urls(listing_html, cutoff)

    items: list[dict[str, Any]] = []
    for url, url_date in candidates:
        try:
            html = _fetch_html(url)
        except requests.RequestException:
            continue
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not text:
            continue
        md = trafilatura.extract_metadata(html)
        title = (md.title if md and md.title else "").strip()
        if not title:
            continue
        # Prefer trafilatura's parsed date (often more precise than URL day);
        # fall back to URL-encoded date.
        published_at = url_date
        if md and md.date:
            try:
                published_at = datetime.fromisoformat(md.date).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        items.append(
            {
                "source": "toulouscope",
                "url": url,
                "title": title,
                "published_at": published_at.isoformat(),
                "raw_html": None,
                "extracted_text": text,
                "item_type": "place",
                "event_date": None,
                "metadata": {},
            }
        )
        time.sleep(INTER_REQUEST_DELAY_S)
    return items


if __name__ == "__main__":
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    fetched = fetch()
    print(f"toulouscope: {len(fetched)} items")
    for item in fetched[:5]:
        print(f"  [{item['published_at']}] {item['title']}")
        print(f"    {item['url']}")
