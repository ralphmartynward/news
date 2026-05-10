from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import feedparser
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.landing import (
    CATEGORY_ORDER,
    SOURCE_LABELS,
    _entry_category,
    _entry_source,
    _french_long_date,
    _french_short_date,
    _summarise,
)

PARIS = ZoneInfo("Europe/Paris")
TEMPLATE_DIR = Path("templates")


def _build_entry(e: Any) -> dict[str, Any]:
    published = (
        datetime(*e.published_parsed[:6], tzinfo=ZoneInfo("UTC"))
        if getattr(e, "published_parsed", None)
        else None
    )
    source_key = _entry_source(e)
    summary_source = e.get("content", [{}])[0].get("value", "") or e.get("summary", "")
    return {
        "url": e.link,
        "title": e.title,
        "source_label": SOURCE_LABELS.get(source_key, source_key or "Source"),
        "published_label": _french_short_date(published) if published else "",
        "summary": _summarise(summary_source) if summary_source else "",
    }


def render(feed_path: Path) -> tuple[str, str]:
    """Return (subject, html) for today's email, derived from feed.xml."""
    parsed = feedparser.parse(str(feed_path))

    grouped: dict[str, list[dict[str, Any]]] = {cat: [] for cat, _, _ in CATEGORY_ORDER}
    for e in parsed.entries:
        cat = _entry_category(e)
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(_build_entry(e))

    sections = []
    for cat, label, title in CATEGORY_ORDER:
        entries = grouped.get(cat, [])
        if entries:
            sections.append({"label": label, "title": title, "entries": entries})

    now_paris = datetime.now(PARIS)
    date_long = _french_long_date(now_paris)
    entry_count = sum(len(s["entries"]) for s in sections)
    subject = f"Toulouse News — {date_long}"

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("email.html.j2")
    html = template.render(
        subject=subject,
        date_long=date_long,
        sections=sections,
        entry_count=entry_count,
    )
    return subject, html
