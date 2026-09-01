from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

STATE_PATH = Path("data/ticketmaster_seen.json")
API_BASE = "https://app.ticketmaster.com/discovery/v2/events.json"
REQUEST_TIMEOUT_S = 20
PAGE_SIZE = 200
INTER_PAGE_DELAY_S = 0.5  # well under the 5 req/s limit
MAX_PAGES = 5  # size*page < 1000 API depth limit; Toulouse-area volume is far below this

# Toulouse city centre. 15km is the minimum that keeps both MEETT
# (~13km, Aussonne) and Le Bikini (~7km, Ramonville) in range -- wider
# than a pure in-town radius would need, but deliberately not the 30km
# tried earlier, which pulled in unrelated towns across the metro area.
LATLONG = "43.6047,1.4442"
RADIUS_KM = "15"

CANCELLED_STATUSES = {"cancelled", "canceled"}
MAX_EVENT_SPAN_DAYS = 3  # skip multi-day "sale"/ongoing-promo listings, not real one-off events


def _load_state() -> dict[str, str]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _signature(item: dict[str, Any]) -> str:
    """Cheap change-detection key: id + status/date-relevant fields. Doesn't
    need to be exhaustive -- just enough to notice a date shift or the event
    getting cancelled (which _parse_event already filters, so a cancelled
    event simply disappears from the current fetch and its state entry gets
    dropped on the next save)."""
    return f"{item.get('_event_start')}|{item.get('_event_end')}|{item.get('image_url')}"


def _best_image(images: list[dict[str, Any]] | None) -> str | None:
    if not images:
        return None
    candidates = [img for img in images if not img.get("fallback") and img.get("url")]
    if not candidates:
        candidates = [img for img in images if img.get("url")]
    if not candidates:
        return None
    candidates.sort(key=lambda img: (img.get("width") or 0) * (img.get("height") or 0), reverse=True)
    return candidates[0]["url"]


def _venue_name(event: dict[str, Any]) -> str | None:
    venues = (event.get("_embedded") or {}).get("venues") or []
    if not venues:
        return None
    name = venues[0].get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _venue_id(event: dict[str, Any]) -> str | None:
    venues = (event.get("_embedded") or {}).get("venues") or []
    return venues[0].get("id") if venues else None


def _norm_show_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", name).strip().upper()


def _venue_city(event: dict[str, Any]) -> str | None:
    venues = (event.get("_embedded") or {}).get("venues") or []
    if not venues:
        return None
    city = (venues[0].get("city") or {}).get("name")
    return city.strip() if isinstance(city, str) and city.strip() else None


def _segment(event: dict[str, Any]) -> str | None:
    classifications = event.get("classifications") or []
    for c in classifications:
        if c.get("primary"):
            segment = (c.get("segment") or {}).get("name")
            genre = (c.get("genre") or {}).get("name")
            if segment and genre and genre != "Undefined":
                return f"{segment} — {genre}"
            return segment
    return None


def _build_excerpt(event: dict[str, Any], start: str | None, end: str | None) -> str:
    lines = []
    if start:
        lines.append(f"Dates: du {start} au {end}" if end and end != start else f"Dates: {start}")
    venue = _venue_name(event)
    city = _venue_city(event)
    if venue:
        lines.append(f"Lieu: {venue}" + (f", {city}" if city and city.lower() != "toulouse" else ""))
    segment = _segment(event)
    if segment:
        lines.append(f"Type: {segment}")
    price_ranges = event.get("priceRanges") or []
    if price_ranges:
        pr = price_ranges[0]
        lines.append(f"Prix: {pr.get('min')}-{pr.get('max')} {pr.get('currency', '')}".strip())
    info = (event.get("info") or event.get("pleaseNote") or "").strip()
    if info:
        lines.append("")
        lines.append(info)
    return "\n".join(lines)


def _parse_event(event: dict[str, Any]) -> dict[str, Any] | None:
    status = ((event.get("dates") or {}).get("status") or {}).get("code", "").lower()
    if status in CANCELLED_STATUSES:
        return None
    name = (event.get("name") or "").strip()
    if not name:
        return None
    dates = event.get("dates") or {}
    start = (dates.get("start") or {}).get("localDate") or None
    end = (dates.get("end") or {}).get("localDate") or None
    if end == start:
        end = None
    if start and end:
        try:
            span_days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if span_days > MAX_EVENT_SPAN_DAYS:
                return None
        except ValueError:
            pass
    return {
        "source": "ticketmaster",
        "url": event.get("url") or f"https://www.ticketmaster.fr/event/{event.get('id', '')}",
        "title": name,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "raw_html": None,
        "extracted_text": _build_excerpt(event, start, end),
        "item_type": "event",
        "event_date": None,
        "image_url": _best_image(event.get("images")),
        "metadata": {},
        "_event_start": start,
        "_event_end": end,
        "_event_name": name,
        "_venue_id": _venue_id(event),
        "_event_id": event.get("id"),
    }


def _filter_recurring_residencies(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop long-running theatre/comedy residencies: Ticketmaster represents
    each performance date as its own separate single-date event (no end
    date on any individual one), so a per-event span check can't see that
    "LES BONOBOS" running weekly from Sept to Dec is the same non-unique
    show, not a one-off. Group by (show name, venue) and check the spread
    across ALL of a show's dates instead -- a real one-off event is its own
    group of size 1 with zero spread, so it always survives untouched."""
    groups: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
    undated: list[dict[str, Any]] = []
    for it in items:
        if not it.get("_event_start"):
            undated.append(it)
            continue
        key = (_norm_show_name(it["_event_name"]), it.get("_venue_id"))
        groups[key].append(it)

    kept = list(undated)
    for group_items in groups.values():
        starts = [date.fromisoformat(gi["_event_start"]) for gi in group_items]
        spread_days = (max(starts) - min(starts)).days
        if spread_days > MAX_EVENT_SPAN_DAYS:
            continue
        kept.extend(group_items)
    return kept


def fetch() -> list[dict[str, Any]]:
    api_key = os.environ.get("TICKETMASTER_API_KEY", "").strip()
    if not api_key:
        print("ticketmaster: skipped (TICKETMASTER_API_KEY not set)", file=sys.stderr)
        return []

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items: list[dict[str, Any]] = []

    page = 0
    while page < MAX_PAGES:
        try:
            r = requests.get(
                API_BASE,
                params={
                    "apikey": api_key,
                    "latlong": LATLONG,
                    "radius": RADIUS_KM,
                    "unit": "km",
                    "startDateTime": now,
                    "size": PAGE_SIZE,
                    "page": page,
                    "sort": "date,asc",
                    # Without this, the API silently defaults to a locale that
                    # excludes almost all French-catalog events (countryCode=FR
                    # alone returned just 1 event across all of France in
                    # testing; adding locale=* revealed 800+ real Toulouse-area
                    # events instead).
                    "locale": "*",
                },
                timeout=REQUEST_TIMEOUT_S,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"ticketmaster: request failed on page {page} — {type(e).__name__}: {e}", file=sys.stderr)
            break

        events = (data.get("_embedded") or {}).get("events") or []
        for event in events:
            try:
                parsed = _parse_event(event)
                if parsed:
                    items.append(parsed)
            except Exception as e:
                print(f"ticketmaster: failed parsing event {event.get('id')} — {type(e).__name__}: {e}", file=sys.stderr)

        total_pages = (data.get("page") or {}).get("totalPages", 1)
        page += 1
        if page >= total_pages:
            break
        time.sleep(INTER_PAGE_DELAY_S)

    before = len(items)
    items = _filter_recurring_residencies(items)
    dropped = before - len(items)
    if dropped:
        print(f"ticketmaster: dropped {dropped} performance(s) belonging to recurring residencies", file=sys.stderr)

    # Incremental tracking: without this, the full ~500-800 event catalog
    # gets re-embedded/re-synthesised/re-emailed every time an item's row
    # ages out of the 7-day items cache -- not just once, but recurring
    # roughly every 7 days forever. Only pass through genuinely new or
    # changed events; a cancelled event (already filtered out of `items`
    # by _parse_event) naturally drops out of the saved state too.
    state = _load_state()
    new_state: dict[str, str] = {}
    changed: list[dict[str, Any]] = []
    for it in items:
        event_id = it.get("_event_id")
        sig = _signature(it)
        if event_id:
            new_state[event_id] = sig
        if not event_id or state.get(event_id) != sig:
            changed.append(it)
    try:
        _save_state(new_state)
    except Exception as e:
        print(f"ticketmaster: failed to save state — {type(e).__name__}: {e}", file=sys.stderr)

    if len(changed) != len(items):
        print(f"ticketmaster: {len(items)} current events, {len(changed)} new/changed", file=sys.stderr)

    return changed


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    fetched = fetch()
    print(f"ticketmaster: {len(fetched)} events")
    for item in fetched[:5]:
        print(f"  [{item.get('_event_start')} -> {item.get('_event_end')}] {item['title']}")
        print(f"    {item['url']}")
        print(f"    image: {item['image_url']}")
