from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from feedgen.feed import FeedGenerator

FEED_BASE_URL = "https://news.lavillerose.com"
FEED_TITLE = "Toulouse News"
FEED_SUBTITLE = "Auto-generated daily Toulouse digest"
FEED_AUTHOR = "Ralph Ward"
FEED_LANGUAGE = "fr"

UTM_PARAMS = {
    "utm_source": "lavillerose.com",
    "utm_medium": "referral",
    "utm_campaign": "toulouse-news",
}

SOURCE_LABELS: dict[str, str] = {
    "actu_toulouse": "Actu Toulouse",
    "la_depeche": "La Dépêche",
    "lessentiel": "L'Essentiel",
    "le_bonbon": "Le Bonbon",
    "clutch": "Clutch",
    "toulouse_secret": "Toulouse Secret",
    "toulouscope": "Toulouscope",
    "openagenda": "OpenAgenda",
    "newsletter": "Newsletter",
}


def _with_utm(url: str) -> str:
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in UTM_PARAMS.items():
        q.setdefault(k, v)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _source_label(key: str) -> str:
    return SOURCE_LABELS.get(key, key or "Source")


def _content_html(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    summary = entry.get("summary", "").strip()
    if summary:
        parts.append(f"<p>{escape(summary)}</p>")

    framing = entry.get("framing_note")
    if framing:
        parts.append(f"<p><em>{escape(framing)}</em></p>")

    read_for = entry.get("read_for") or []
    if read_for:
        items_html = "".join(
            f"<li><strong>{escape(_source_label(r.get('source', '')))}</strong>: "
            f"{escape(r.get('angle', ''))}</li>"
            for r in read_for
        )
        parts.append(f"<p><strong>À lire pour:</strong></p><ul>{items_html}</ul>")

    sources = entry.get("sources") or []
    if sources:
        links_html = ", ".join(
            f'<a href="{escape(_with_utm(s["url"]))}">{escape(_source_label(s.get("source", "")))}</a>'
            for s in sources
        )
        parts.append(f'<p><strong>Sources:</strong> {links_html}</p>')

    return "\n".join(parts)


def _parse(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str)


def write_atom(entries: list[dict[str, Any]], path: Path) -> None:
    fg = FeedGenerator()
    fg.id(FEED_BASE_URL + "/")
    fg.title(FEED_TITLE)
    fg.subtitle(FEED_SUBTITLE)
    fg.author({"name": FEED_AUTHOR})
    fg.link(href=FEED_BASE_URL + "/feed.xml", rel="self")
    fg.link(href=FEED_BASE_URL + "/", rel="alternate")
    fg.language(FEED_LANGUAGE)

    latest = max(
        (_parse(e["updated_at"]) for e in entries),
        default=datetime.now(timezone.utc),
    )
    fg.updated(latest)

    for entry in entries:
        fe = fg.add_entry()
        fe.id(entry["id"])
        fe.title(entry["title"])
        fe.link(href=_with_utm(entry["url"]), rel="alternate")
        fe.published(_parse(entry["published_at"]))
        fe.updated(_parse(entry["updated_at"]))
        for author in entry.get("authors", []):
            fe.author({"name": author})
        fe.category({"term": entry.get("item_type", "news")})

        summary = entry.get("summary", "").strip()
        if summary:
            fe.summary(summary)

        fe.content(_content_html(entry), type="html")

    path.parent.mkdir(parents=True, exist_ok=True)
    fg.atom_file(str(path), pretty=True)
