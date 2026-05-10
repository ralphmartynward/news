from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
import trafilatura

LISTING_URL = "https://actu.fr/occitanie/toulouse_31555/"
ARTICLE_RE = re.compile(
    r'href="(https?://actu\.fr/occitanie/toulouse_31555/[^"#?]+\.html)"'
)
TIME_TAG_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT_S = 20
INTER_REQUEST_DELAY_S = 0.5


def _fetch_html(url: str) -> str:
    r = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_S,
    )
    r.raise_for_status()
    return r.text


def _parse_published_at(html: str) -> datetime | None:
    m = TIME_TAG_RE.search(html)
    if m:
        try:
            return datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
        except ValueError:
            pass
    md = trafilatura.extract_metadata(html)
    if md and md.date:
        try:
            return datetime.fromisoformat(md.date).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _article_urls(listing_html: str) -> list[str]:
    seen: dict[str, None] = {}
    for url in ARTICLE_RE.findall(listing_html):
        seen.setdefault(url, None)
    return list(seen)


def fetch(within_hours: int = 24) -> list[dict[str, Any]]:
    listing_html = _fetch_html(LISTING_URL)
    urls = _article_urls(listing_html)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
    items: list[dict[str, Any]] = []
    for url in urls:
        try:
            html = _fetch_html(url)
        except requests.RequestException:
            continue
        published_at = _parse_published_at(html)
        if published_at is None or published_at < cutoff:
            continue
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not text:
            continue
        md = trafilatura.extract_metadata(html)
        title = (md.title if md and md.title else "").strip()
        items.append(
            {
                "source": "actu_toulouse",
                "url": url,
                "title": title,
                "published_at": published_at.isoformat(),
                "raw_html": None,
                "extracted_text": text,
                "item_type": "news",
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
    print(f"actu_toulouse: {len(fetched)} items in last 24h")
    for item in fetched[:5]:
        print(f"  [{item['published_at']}] {item['title']}")
        print(f"    {item['url']}")
        print(f"    {len(item['extracted_text'])} chars extracted")
