from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from feedgen.feed import FeedGenerator

FEED_BASE_URL = "https://news.lavillerose.com"
FEED_TITLE = "Toulouse News"
FEED_SUBTITLE = "Auto-generated daily Toulouse digest"
FEED_AUTHOR = "Ralph Ward"
FEED_LANGUAGE = "fr"


def build_feed(items: list[dict[str, Any]]) -> FeedGenerator:
    fg = FeedGenerator()
    fg.id(FEED_BASE_URL + "/")
    fg.title(FEED_TITLE)
    fg.subtitle(FEED_SUBTITLE)
    fg.author({"name": FEED_AUTHOR})
    fg.link(href=FEED_BASE_URL + "/feed.xml", rel="self")
    fg.link(href=FEED_BASE_URL + "/", rel="alternate")
    fg.language(FEED_LANGUAGE)

    latest = max(
        (datetime.fromisoformat(i["published_at"]) for i in items),
        default=datetime.now(timezone.utc),
    )
    fg.updated(latest)

    for item in items:
        published = datetime.fromisoformat(item["published_at"])
        fe = fg.add_entry()
        fe.id(item["url"])
        fe.title(item["title"])
        fe.link(href=item["url"], rel="alternate")
        fe.published(published)
        fe.updated(published)
        fe.author({"name": item["source"]})
        fe.category({"term": item["item_type"]})
        fe.content(item["extracted_text"], type="text")

    return fg


def write_atom(items: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fg = build_feed(items)
    fg.atom_file(str(path), pretty=True)
