from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

SITEMAP_INDEX = "https://www.toulouse-tourisme.com/sitemap_index.xml"
STATE_PATH = Path("data/tourinsoft_seen.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT_S = 20
CRAWL_DELAY_S = 20  # respect toulouse-tourisme.com robots.txt crawl-delay
MAX_CHANGED_PER_RUN = 300  # safety valve: a lastmod-format change shouldn't trigger a 692-page crawl.
# 300 * CRAWL_DELAY_S ~= 100 min added to the digest job while the initial
# ~692-event backlog clears (about 3 daily runs); after that, daily deltas
# are small and this cap is rarely hit.

_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S)
_URL_BLOCK_RE = re.compile(r"<url>(.*?)</url>", re.S)
_LOCALE_AGENDA_RE = re.compile(r"/(en|es)/agenda/")
_LDJSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)


def _fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_S)
    r.raise_for_status()
    return r.text


def _discover_agenda_sitemaps() -> list[str]:
    xml = _fetch(SITEMAP_INDEX)
    return [loc for loc in _LOC_RE.findall(xml) if "agenda-sitemap" in loc]


def _list_event_urls(sitemap_urls: list[str]) -> dict[str, str]:
    """French-canonical event URLs -> lastmod. The sitemap also contains
    /en/agenda/ and /es/agenda/ locale variants of the same events (via
    xhtml:link hreflang alternates inside each <url> block, not adjacent to
    <loc>/<lastmod> — parse per-block, not with a single adjacent regex),
    which we don't want duplicating every event 3x."""
    urls: dict[str, str] = {}
    for sm_url in sitemap_urls:
        xml = _fetch(sm_url)
        for block in _URL_BLOCK_RE.findall(xml):
            loc_m = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.S)
            lastmod_m = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, re.S)
            if not loc_m or not lastmod_m:
                continue
            loc = loc_m.group(1)
            if "/agenda/" not in loc or _LOCALE_AGENDA_RE.search(loc):
                continue
            urls[loc] = lastmod_m.group(1)
    return urls


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


def _find_event_jsonld(html: str) -> dict[str, Any] | None:
    for block in _LDJSON_RE.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            if cand.get("@type") == "Event":
                return cand
            for node in cand.get("@graph", []) or []:
                if isinstance(node, dict) and node.get("@type") == "Event":
                    return node
    return None


def _event_image_url(image: Any) -> str | None:
    """Schema.org allows `image` to be a string, an ImageObject, or a list of
    either — Tourinsoft's markup uses a list of plain URL strings."""
    if isinstance(image, str):
        return image or None
    if isinstance(image, dict):
        url = image.get("url")
        return url if isinstance(url, str) and url else None
    if isinstance(image, list):
        for entry in image:
            found = _event_image_url(entry)
            if found:
                return found
    return None


def _venue_name(location: Any) -> str | None:
    if isinstance(location, dict):
        name = location.get("name")
        return name.strip() if isinstance(name, str) and name.strip() else None
    return None


def _build_excerpt(event: dict[str, Any], start: str | None, end: str | None) -> str:
    lines = []
    if start:
        lines.append(f"Dates: du {start} au {end}" if end and end != start else f"Dates: {start}")
    venue = _venue_name(event.get("location"))
    if venue:
        lines.append(f"Lieu: {venue}")
    description = (event.get("description") or "").strip()
    if description:
        lines.append("")
        lines.append(description)
    return "\n".join(lines)


def _parse_event(url: str, html: str) -> dict[str, Any] | None:
    event = _find_event_jsonld(html)
    if not event:
        return None
    name = (event.get("name") or "").strip()
    if not name:
        return None
    start = (event.get("startDate") or "")[:10] or None
    end = (event.get("endDate") or "")[:10] or None
    if end == start:
        end = None
    return {
        "source": "office_tourisme",
        "url": event.get("url") or url,
        "title": name,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "raw_html": None,
        "extracted_text": _build_excerpt(event, start, end),
        "item_type": "event",
        "event_date": None,
        "image_url": _event_image_url(event.get("image")),
        "metadata": {},
        "_event_start": start,
        "_event_end": end,
        "_event_name": name,
    }


def fetch() -> list[dict[str, Any]]:
    try:
        sitemap_urls = _discover_agenda_sitemaps()
        if not sitemap_urls:
            print("tourinsoft: no agenda sitemap found in sitemap index", file=sys.stderr)
            return []
        current = _list_event_urls(sitemap_urls)
    except Exception as e:
        print(f"tourinsoft: sitemap discovery failed — {type(e).__name__}: {e}", file=sys.stderr)
        return []

    state = _load_state()
    changed = [url for url, lastmod in current.items() if state.get(url) != lastmod]
    truncated = len(changed) > MAX_CHANGED_PER_RUN
    if truncated:
        print(
            f"tourinsoft: {len(changed)} changed URLs exceeds cap of {MAX_CHANGED_PER_RUN}, "
            "truncating (possible lastmod format change)",
            file=sys.stderr,
        )
        changed = changed[:MAX_CHANGED_PER_RUN]

    items: list[dict[str, Any]] = []
    fetched_ok: list[str] = []
    for i, url in enumerate(changed):
        try:
            html = _fetch(url)
            parsed = _parse_event(url, html)
            if parsed:
                items.append(parsed)
            fetched_ok.append(url)
        except Exception as e:
            print(f"tourinsoft: failed on {url} — {type(e).__name__}: {e}", file=sys.stderr)
        if i < len(changed) - 1:
            time.sleep(CRAWL_DELAY_S)

    # Save progress for whatever was actually fetched, truncated or not —
    # a truncated run is expected (multi-day backfill), not a failure, and
    # gating the save on "not truncated" meant it would never save while
    # changed > cap, so the backfill would re-fetch the same first batch
    # forever instead of advancing.
    for url in fetched_ok:
        state[url] = current[url]
    try:
        _save_state(state)
    except Exception as e:
        print(f"tourinsoft: failed to save state — {type(e).__name__}: {e}", file=sys.stderr)

    return items


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    fetched = fetch()
    print(f"tourinsoft: {len(fetched)} new/changed events")
    for item in fetched[:5]:
        print(f"  [{item.get('_event_start')} -> {item.get('_event_end')}] {item['title']}")
        print(f"    {item['url']}")
        print(f"    image: {item['image_url']}")
