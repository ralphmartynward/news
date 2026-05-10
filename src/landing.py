from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import feedparser
from jinja2 import Environment, FileSystemLoader, select_autoescape

PARIS = ZoneInfo("Europe/Paris")
TEMPLATE_DIR = Path("templates")
SUMMARY_MAX_CHARS = 220
WORKER_SUBSCRIBE_URL = os.environ.get("WORKER_SUBSCRIBE_URL", "").strip()

# (key, eyebrow label, section title)
CATEGORY_ORDER: list[tuple[str, str, str]] = [
    ("news", "Actualité", "Les infos du jour"),
    ("event", "Agenda", "Ça se passe à Toulouse"),
    ("place", "À découvrir", "Lieux, ouvertures, bonnes adresses"),
    ("culture", "Culture", "Sorties culturelles"),
]

SOURCE_LABELS: dict[str, str] = {
    "actu_toulouse": "Actu Toulouse",
    "la_depeche": "La Dépêche",
    "lessentiel": "L'Essentiel",
    "le_bonbon": "Le Bonbon",
    "clutch": "Clutch",
    "toulouse_secret": "Toulouse Secret",
    "toulouscope": "Toulouscope",
    "openagenda": "OpenAgenda",
}

FRENCH_MONTHS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
FRENCH_WEEKDAYS = [
    "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche",
]


def _summarise(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    cut = text[:SUMMARY_MAX_CHARS]
    last_space = cut.rfind(" ")
    if last_space > SUMMARY_MAX_CHARS * 0.6:
        cut = cut[:last_space]
    return cut.rstrip(",.;:") + "…"


def _french_long_date(d: datetime) -> str:
    weekday = FRENCH_WEEKDAYS[d.weekday()]
    month = FRENCH_MONTHS[d.month - 1]
    return f"{weekday} {d.day} {month} {d.year}"


def _french_short_date(d: datetime) -> str:
    paris_d = d.astimezone(PARIS)
    return f"{paris_d.day} {FRENCH_MONTHS[paris_d.month - 1]}, {paris_d.strftime('%H:%M')}"


def _entry_category(entry: Any) -> str:
    tags = getattr(entry, "tags", None) or []
    for tag in tags:
        term = getattr(tag, "term", None)
        if term:
            return term
    return "news"


def _entry_source(entry: Any) -> str:
    author = getattr(entry, "author", "") or ""
    return author.strip()


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
        "source_key": source_key,
        "source_label": SOURCE_LABELS.get(source_key, source_key or "Source"),
        "published_label": _french_short_date(published) if published else "",
        "summary": _summarise(summary_source) if summary_source else "",
    }


def render(feed_path: Path, out_path: Path) -> None:
    parsed = feedparser.parse(str(feed_path))

    grouped: dict[str, list[dict[str, Any]]] = {cat: [] for cat, _, _ in CATEGORY_ORDER}
    sources_seen: set[str] = set()
    for e in parsed.entries:
        cat = _entry_category(e)
        if cat not in grouped:
            grouped[cat] = []
        entry = _build_entry(e)
        grouped[cat].append(entry)
        if entry["source_key"]:
            sources_seen.add(entry["source_key"])

    sections = []
    for cat, label, title in CATEGORY_ORDER:
        entries = grouped.get(cat, [])
        if entries:
            sections.append({"label": label, "title": title, "entries": entries})

    now_paris = datetime.now(PARIS)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("landing.html.j2")
    html = template.render(
        date_long=_french_long_date(now_paris),
        sections=sections,
        entry_count=sum(len(s["entries"]) for s in sections),
        source_count=len(sources_seen),
        worker_subscribe_url=WORKER_SUBSCRIBE_URL,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
