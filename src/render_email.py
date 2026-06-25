from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.landing import (
    CATEGORY_ORDER,
    SOURCE_LABELS,
    PARIS,
    _french_long_date,
    _french_short_date,
    _summarise,
)

TEMPLATE_DIR = Path("templates")


def _short_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return _french_short_date(dt.astimezone(ZoneInfo("UTC")))
    except (ValueError, KeyError):
        return ""


def render(entries: list[dict[str, Any]]) -> tuple[str, str]:
    """Build (subject, html) from a list of entry dicts (the same shape
    produced by _cluster_to_entry / _item_to_entry in main.py).

    Only entries passed in here appear in the email — callers are
    responsible for filtering to 'not yet emailed' clusters.
    """
    grouped: dict[str, list[dict[str, Any]]] = {cat: [] for cat, _, _ in CATEGORY_ORDER}

    for entry in entries:
        cat = entry.get("item_type", "news")
        if cat not in grouped:
            grouped[cat] = []
        authors = entry.get("authors", [])
        source_key = authors[0] if authors else entry.get("source", "")
        grouped[cat].append(
            {
                "url": entry["url"],
                "title": entry["title"],
                "source_label": SOURCE_LABELS.get(source_key, source_key or "Source"),
                "published_label": _short_date(entry.get("published_at", "")),
                "summary": _summarise(entry.get("summary", "")),
            }
        )

    sections = []
    for cat, label, title in CATEGORY_ORDER:
        cat_entries = grouped.get(cat, [])
        if cat_entries:
            sections.append({"key": cat, "label": label, "title": title, "entries": cat_entries})

    now_paris = datetime.now(PARIS)
    date_long = _french_long_date(now_paris)
    entry_count = sum(len(s["entries"]) for s in sections)
    subject = f"Toulouse News — {date_long}"

    try:
        from src.weather import today_line
        weather_line = today_line()
    except Exception:
        weather_line = ""

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = env.get_template("email.html.j2").render(
        subject=subject,
        date_long=date_long,
        sections=sections,
        entry_count=entry_count,
        weather_line=weather_line,
    )
    return subject, html
