from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CACHE_PATH = Path("data/venue_geocode_cache.json")
USER_AGENT = "toulouse-news-geocoder/1.0 (+https://news.lavillerose.com)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
BAN_URL = "https://api-adresse.data.gouv.fr/search/"
NOMINATIM_DELAY_SECONDS = 1.1  # Nominatim usage policy: max 1 req/sec

# Toulouse-metro bounding box (lat_min, lat_max, lon_min, lon_max). Any
# geocode result outside this is rejected as wrong regardless of confidence --
# confirmed a free-text query for a real local venue ("Cales de Radoub") can
# silently match a same-ish-named place on the other side of France when
# there's no good local hit, rather than returning nothing.
BBOX = (43.40, 43.85, 1.20, 1.65)

# Real venues confirmed absent from both Nominatim and the BAN geocoder under
# this name -- add entries here as they're found rather than re-querying a
# known miss forever.
MANUAL_OVERRIDES: dict[str, tuple[float, float]] = {
    "cales de radoub": (43.6127, 1.4295),  # Canal du Midi docks, near Ponts Jumeaux
}


def _in_bbox(lat: float, lon: float) -> bool:
    lat_min, lat_max, lon_min, lon_max = BBOX
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def _norm(venue: str) -> str:
    return " ".join(venue.split()).strip().lower()


def _strip_qualifiers(venue: str) -> str:
    """Drop trailing " - Hall M" / " – Salle X" / "(...)" suffixes that
    confuse a geocoder but aren't part of the venue's actual name."""
    v = re.split(r"\s+[-–—]\s+", venue)[0]
    v = re.sub(r"\s*\([^)]*\)\s*$", "", v)
    return v.strip()


def _nominatim(query: str) -> tuple[float, float] | None:
    url = NOMINATIM_URL + "?" + urllib.parse.urlencode({
        "q": query, "format": "json", "limit": 1, "countrycodes": "fr",
    })
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data:
        return None
    lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
    return (lat, lon) if _in_bbox(lat, lon) else None


def _ban(query: str) -> tuple[float, float] | None:
    """French government's free address geocoder -- no rate limit, weaker
    for POI/venue names than for addresses, used as a fallback only."""
    url = BAN_URL + "?" + urllib.parse.urlencode({"q": query, "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    feats = data.get("features") or []
    if not feats:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"]
    return (lat, lon) if _in_bbox(lat, lon) else None


def load_cache() -> dict[str, Any]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def geocode_venue(venue: str, cache: dict[str, Any]) -> tuple[float, float] | None:
    """Resolve a free-text venue name to (lat, lon), or None if unresolvable.

    Tries, in order: manual override -> cache -> Nominatim (with a
    ", Toulouse, France" suffix) -> Nominatim on a qualifier-stripped
    version -> BAN. Every result is bbox-checked -- see BBOX above.
    Manual override is checked before the cache so adding an entry for a
    previously-cached miss takes effect immediately, without needing to
    edit the cache file too.

    `cache` is mutated in place (including explicit misses as `None`, so
    they aren't retried every run) -- call save_cache(cache) once after a
    batch, not per venue.
    """
    key = _norm(venue)
    if not key:
        return None
    if key in MANUAL_OVERRIDES:
        result = MANUAL_OVERRIDES[key]
    elif key in cache:
        v = cache[key]
        return tuple(v) if v else None
    else:
        result = None
        stripped = _strip_qualifiers(venue)
        queries = [f"{venue}, Toulouse, France"]
        if stripped != venue:
            queries.append(f"{stripped}, Toulouse, France")
        for query in queries:
            try:
                result = _nominatim(query)
            except Exception as e:
                print(f"geocode: nominatim error for {query!r} — {type(e).__name__}: {e}")
                result = None
            time.sleep(NOMINATIM_DELAY_SECONDS)
            if result:
                break
        if not result:
            try:
                result = _ban(f"{venue} Toulouse")
            except Exception as e:
                print(f"geocode: ban error for {venue!r} — {type(e).__name__}: {e}")
                result = None

    cache[key] = list(result) if result else None
    return result


def geocode_venues(venues: list[str]) -> dict[str, tuple[float, float] | None]:
    """Batch-geocode a list of (possibly repeated) venue names, deduping
    against the persistent cache first so repeat runs only pay for venues
    seen for the first time."""
    cache = load_cache()
    results: dict[str, tuple[float, float] | None] = {}
    before = json.dumps(cache, sort_keys=True)
    for v in venues:
        if v in results:
            continue
        results[v] = geocode_venue(v, cache)
    if json.dumps(cache, sort_keys=True) != before:
        save_cache(cache)
    return results
