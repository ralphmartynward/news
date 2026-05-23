from __future__ import annotations

import json as _json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import feedparser
from jinja2 import Environment, FileSystemLoader, select_autoescape

PARIS = ZoneInfo("Europe/Paris")
TEMPLATE_DIR = Path("templates")
SUMMARY_MAX_CHARS = 500
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
    "office_tourisme": "Office de Tourisme",
    "newsletter": "Newsletter",
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


_UTM = {"utm_source": "lavillerose.com", "utm_medium": "referral", "utm_campaign": "toulouse-news"}


def _with_utm(url: str) -> str:
    if not url or not url.startswith("http"):
        return url
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in _UTM.items():
        q.setdefault(k, v)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _calendar_events_json(calendar_events: list[dict[str, Any]] | None) -> str:
    """Serialize calendar events to a JSON string safe for embedding in a <script> tag."""
    items = []
    for ev in (calendar_events or []):
        if not ev.get("event_start"):
            continue
        items.append({
            "event_start": ev["event_start"],
            "event_end": ev.get("event_end") or None,
            "title": ev.get("title") or "",
            "event_name": ev.get("event_name") or ev.get("title") or "",
            "summary": _summarise(ev.get("summary") or ""),
            "url": _with_utm(ev.get("url") or ""),
            "source_label": SOURCE_LABELS.get(ev.get("source", ""), ev.get("source", "") or "Source"),
        })
    return _json.dumps(items, ensure_ascii=False)


def _pagination_chips(
    archive_dates: list[dict[str, str]],
    current_date: date | None,
) -> list[dict]:
    """Return context-aware pagination chips for the archive nav.

    Each chip is one of:
      {'date': '2026-05-21', 'short': '21 mai', 'is_current': bool}
      {'is_ellipsis': True}

    On today's page (current_date=None): show 3 most-recent + … + oldest.
    On an archive page: show 1 neighbour each side of current + … + oldest,
    with the current chip marked is_current=True.
    """
    n = len(archive_dates)
    if n == 0:
        return []

    current_iso = current_date.isoformat() if current_date else None
    current_idx: int | None = None
    if current_iso:
        for i, arc in enumerate(archive_dates):
            if arc["date"] == current_iso:
                current_idx = i
                break

    result: list[dict] = []

    if current_idx is None:
        # Today's page — show 3 newest + … + oldest
        shown = set()
        for arc in archive_dates[:3]:
            result.append({**arc, "is_current": False, "is_ellipsis": False})
            shown.add(arc["date"])
        if n > 4:
            result.append({"is_ellipsis": True})
        if n > 3 and archive_dates[-1]["date"] not in shown:
            result.append({**archive_dates[-1], "is_current": False, "is_ellipsis": False})
    else:
        # Archive page — show window [idx-1 .. idx+1], then … + oldest
        lo = max(0, current_idx - 1)
        hi = min(n - 1, current_idx + 1)
        shown = set()
        for i in range(lo, hi + 1):
            result.append({**archive_dates[i], "is_current": (i == current_idx), "is_ellipsis": False})
            shown.add(archive_dates[i]["date"])
        if hi < n - 2:
            result.append({"is_ellipsis": True})
        if n > 0 and archive_dates[-1]["date"] not in shown:
            result.append({**archive_dates[-1], "is_current": (current_idx == n - 1), "is_ellipsis": False})

    return result


def _build_calendar_days(
    events: list[dict[str, Any]],
    *,
    days_past: int = 0,
    days_future: int = 14,
) -> list[dict[str, Any]]:
    """Group events into an ordered list of day dicts for template rendering.

    Multi-day events (event_end set) appear on each day they span, capped at
    14 days to guard against bad date data. Events outside the window are dropped.
    """
    today = datetime.now(PARIS).date()
    today_iso = today.isoformat()
    window_start = today - timedelta(days=days_past)
    window_end = today + timedelta(days=days_future)

    by_date: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        start_str = ev.get("event_start") or ""
        end_str = ev.get("event_end") or start_str
        try:
            start_d = date.fromisoformat(start_str)
            end_d = date.fromisoformat(end_str)
        except ValueError:
            continue
        end_d = min(end_d, start_d + timedelta(days=3))
        ev_out = dict(ev)
        ev_out["source_label"] = SOURCE_LABELS.get(ev.get("source", ""), ev.get("source", "") or "Source")
        d = start_d
        while d <= end_d:
            if window_start <= d <= window_end:
                key = d.isoformat()
                by_date.setdefault(key, [])
                if not any(e["cluster_id"] == ev["cluster_id"] for e in by_date[key]):
                    by_date[key].append(ev_out)
            d += timedelta(days=1)

    result = []
    for date_iso in sorted(by_date.keys()):
        d = date.fromisoformat(date_iso)
        result.append({
            "date_iso": date_iso,
            "date_label": f"{FRENCH_WEEKDAYS[d.weekday()]} {d.day} {FRENCH_MONTHS[d.month - 1]}",
            "is_today": date_iso == today_iso,
            "is_past": date_iso < today_iso,
            "events": by_date[date_iso],
        })
    return result


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
    summary_text = e.get("summary", "") or (
        e.get("content", [{}])[0].get("value", "") if e.get("content") else ""
    )
    return {
        "url": e.link,
        "title": e.title,
        "source_key": source_key,
        "source_label": SOURCE_LABELS.get(source_key, source_key or "Source"),
        "published_label": _french_short_date(published) if published else "",
        "published_iso": published.isoformat() if published else "",
        "summary": _summarise(summary_text) if summary_text else "",
    }


def render(
    feed_path: Path,
    out_path: Path,
    *,
    filter_date: date | None = None,
    archive_dates: list[dict[str, str]] | None = None,
    is_archive: bool = False,
    calendar_events: list[dict[str, Any]] | None = None,
) -> None:
    """Render the landing page (or an archive page).

    filter_date: Paris local date to display. Defaults to today.
    archive_dates: list of {"date": "YYYY-MM-DD", "label": "..."} for pagination.
    is_archive: True when rendering a past-day archive page.
    """
    parsed = feedparser.parse(str(feed_path))
    display_date = filter_date or datetime.now(PARIS).date()

    grouped: dict[str, list[dict[str, Any]]] = {cat: [] for cat, _, _ in CATEGORY_ORDER}
    sources_seen: set[str] = set()
    for e in parsed.entries:
        published = (
            datetime(*e.published_parsed[:6], tzinfo=ZoneInfo("UTC"))
            if getattr(e, "published_parsed", None)
            else None
        )
        # Filter by updated (digest processing date) not published (source date):
        # articles fetched today but published yesterday belong on today's page.
        digest_dt = (
            datetime(*e.updated_parsed[:6], tzinfo=ZoneInfo("UTC"))
            if getattr(e, "updated_parsed", None)
            else published
        )
        if digest_dt and digest_dt.astimezone(PARIS).date() != display_date:
            continue
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
            sections.append({"key": cat, "label": label, "title": title, "entries": entries})

    display_dt = datetime(display_date.year, display_date.month, display_date.day, tzinfo=PARIS)
    date_long = _french_long_date(display_dt)
    entry_count = sum(len(s["entries"]) for s in sections)

    if is_archive:
        canonical_url = f"https://news.lavillerose.com/archive/{display_date.isoformat()}.html"
        page_title = f"Toulouse News — {date_long} | La Ville Rose"
        page_description = f"Actualités du {date_long} à Toulouse — {entry_count} stories de sources locales."
    else:
        canonical_url = "https://news.lavillerose.com/"
        page_title = "Toulouse News — La Ville Rose"
        page_description = "L'actualité de Toulouse, agrégée chaque jour à 7h. Sources locales, dédupliquées, résumées."

    # Build JSON-LD structured data for search engines
    schema_items: list[dict] = []
    for section in sections:
        for entry in section["entries"]:
            if section["key"] == "event":
                schema_items.append({
                    "@type": "Event",
                    "name": entry["title"],
                    "description": entry["summary"] or entry["title"],
                    "url": entry["url"],
                    "startDate": display_date.isoformat(),
                    "location": {
                        "@type": "Place",
                        "name": "Toulouse",
                        "address": {
                            "@type": "PostalAddress",
                            "addressLocality": "Toulouse",
                            "addressCountry": "FR",
                        },
                    },
                    "organizer": {"@type": "Organization", "name": entry["source_label"]},
                })
            else:
                schema_items.append({
                    "@type": "NewsArticle",
                    "headline": entry["title"],
                    "description": entry["summary"] or entry["title"],
                    "url": entry["url"],
                    "datePublished": entry.get("published_iso") or display_date.isoformat(),
                    "publisher": {
                        "@type": "Organization",
                        "name": "La Ville Rose News",
                        "url": "https://news.lavillerose.com",
                    },
                })
    jsonld = _json.dumps(
        {"@context": "https://schema.org", "@graph": schema_items},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("landing.html.j2")
    # Compact calendar: upcoming events only (next 14 days), shown on index page
    calendar_days = (
        _build_calendar_days(calendar_events, days_past=0, days_future=60)
        if (calendar_events and not is_archive)
        else []
    )

    html = template.render(
        date_long=date_long,
        sections=sections,
        entry_count=entry_count,
        source_count=len(sources_seen),
        worker_subscribe_url=WORKER_SUBSCRIBE_URL,
        archive_dates=archive_dates or [],
        is_archive=is_archive,
        canonical_url=canonical_url,
        page_title=page_title,
        page_description=page_description,
        jsonld=jsonld,
        calendar_days=calendar_days,
        calendar_events_json=_calendar_events_json(calendar_events),
        pagination_chips=_pagination_chips(archive_dates or [], filter_date),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def render_calendar_page(calendar_events: list[dict[str, Any]], out_path: Path) -> None:
    """Render the standalone /calendar.html page."""
    # Full view: past 14 days + upcoming 90 days
    all_days = _build_calendar_days(calendar_events, days_past=30, days_future=365)
    today_iso = datetime.now(PARIS).date().isoformat()

    now_paris = datetime.now(PARIS)
    date_long = _french_long_date(now_paris)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = env.get_template("calendar.html.j2").render(
        all_days=all_days,
        today_iso=today_iso,
        date_long=date_long,
        event_count=len(calendar_events),
        worker_subscribe_url=WORKER_SUBSCRIBE_URL,
        calendar_events_json=_calendar_events_json(calendar_events),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
