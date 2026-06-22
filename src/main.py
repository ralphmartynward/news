from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src import cache as cache_mod
from src.fetchers import actu_toulouse, inbox, toulouscope
from src.feed import write_atom
from src.landing import PARIS, _french_long_date, render as render_landing, render_calendar_page
from src.render_email import render as render_email
from src.send import SendError, send_broadcast

FEED_OUTPUT = Path("docs/feed.xml")
LANDING_OUTPUT = Path("docs/index.html")
ARCHIVE_DIR = Path("docs/archive")
CALENDAR_OUTPUT = Path("docs/calendar.html")
CALENDAR_ICS_OUTPUT = Path("docs/calendar.ics")
SITEMAP_OUTPUT = Path("docs/sitemap.xml")
INSTAGRAM_DIR = Path("docs/instagram")
CACHE_PATH = Path("data/items_seen.db")
SITE_BASE = "https://news.lavillerose.com"

FETCHERS = [
    ("actu_toulouse", lambda: actu_toulouse.fetch(within_hours=36)),
    ("toulouscope", toulouscope.fetch),
    ("inbox", inbox.fetch),
]

FEED_ENTRY_LIMIT = 200  # covers 7 days of cache at ~30 clusters/day
RAW_SUMMARY_CHARS = 1500


def _embed_text_for_item(item: dict[str, Any]) -> str:
    return f"{item.get('title', '')}\n\n{item.get('extracted_text') or ''}".strip()


def _item_to_entry(item: dict[str, Any]) -> dict[str, Any]:
    """Fallback when no clustering/synthesis: each item is its own entry."""
    summary = (item.get("extracted_text") or "")[:RAW_SUMMARY_CHARS]
    return {
        "id": item["url"],
        "title": item["title"],
        "url": item["url"],
        "published_at": item["published_at"],
        "updated_at": item.get("seen_at", item["published_at"]),
        "authors": [item["source"]],
        "item_type": item.get("item_type", "news"),
        "summary": summary,
        "framing_note": None,
        "read_for": None,
        "sources": [{"source": item["source"], "url": item["url"], "title": item["title"]}],
    }


def _cluster_to_entry(cluster_id: str, items: list[dict[str, Any]], synth: dict[str, Any] | None) -> dict[str, Any]:
    primary = min(items, key=lambda i: i["published_at"])
    # Use seen_at (digest processing time) as updated_at so the landing page
    # filters by when items entered the digest, not the source publication date.
    # Articles published late yesterday but fetched this morning should appear
    # on today's landing page, not yesterday's.
    seen_ats = [i["seen_at"] for i in items if i.get("seen_at")]
    latest_at = max(seen_ats) if seen_ats else max(i["published_at"] for i in items)
    if synth:
        title = synth["title"]
        summary = synth["summary"]
        framing_note = synth["framing_note"]
        read_for = synth["read_for"]
        # Synthesised category trumps source default; falls back to source if
        # synthesis didn't return a valid category.
        item_type = synth.get("category") or primary.get("item_type", "news")
    else:
        title = primary["title"]
        summary = (primary.get("extracted_text") or "")[:RAW_SUMMARY_CHARS]
        framing_note = None
        read_for = None
        item_type = primary.get("item_type", "news")
    return {
        "id": cluster_id,
        "title": title,
        "url": primary["url"],
        "published_at": primary["published_at"],
        "updated_at": latest_at,
        "authors": sorted({i["source"] for i in items}),
        "item_type": item_type,
        "summary": summary,
        "framing_note": framing_note,
        "read_for": read_for,
        "sources": [
            {"source": i["source"], "url": i["url"], "title": i["title"]}
            for i in sorted(items, key=lambda x: x["published_at"])
        ],
    }


def _entries_from_cache(conn) -> list[dict[str, Any]]:
    cached = cache_mod.load_recent(conn)
    by_cluster: dict[str, list[dict[str, Any]]] = {}
    for it in cached:
        by_cluster.setdefault(it["cluster_id"], []).append(it)

    entries = []
    for cluster_id, items in by_cluster.items():
        synth = cache_mod.load_cluster(conn, cluster_id)
        entries.append(_cluster_to_entry(cluster_id, items, synth))

    entries.sort(key=lambda e: e["updated_at"], reverse=True)
    return entries[:FEED_ENTRY_LIMIT]


def _cluster_today(items: list[dict[str, Any]]):
    """Pre-filter items already in cache (same URL = same item, not a near-dupe),
    embed only genuinely new items, dedup against the rolling cache.
    Returns (touched_cluster_ids, conn). Caller closes conn."""
    from src import cluster as cluster_mod
    from src import embed as embed_mod

    conn = cache_mod.open_cache(CACHE_PATH)
    pruned = cache_mod.prune(conn)
    if pruned:
        print(f"cache: pruned {pruned} items older than 7 days")
    cleaned = cache_mod.purge_system_sources(conn)
    if cleaned:
        print(f"cache: purged {cleaned} item(s) from system/transactional senders")
    fb_cleaned = cache_mod.purge_newsletter_fallbacks(conn)
    if fb_cleaned:
        print(f"cache: purged {fb_cleaned} newsletter fallback item(s)")

    cached = cache_mod.load_recent(conn)
    print(f"cache: {len(cached)} items in 7-day window")

    cached_urls = {c["url"] for c in cached}
    new_items = [it for it in items if it["url"] not in cached_urls]
    seen_count = len(items) - len(new_items)
    if seen_count:
        print(f"cluster: {seen_count} item(s) already in cache (same URL) — passed through")

    if not new_items:
        return set(), conn

    embeddings = embed_mod.embed_batch([_embed_text_for_item(i) for i in new_items])
    kept = cluster_mod.assign_clusters(new_items, embeddings, cached)

    skipped = len(new_items) - len(kept)
    cached_cluster_ids = {c["cluster_id"] for c in cached}
    new_clusters = sum(1 for k in kept if k["cluster_id"] not in cached_cluster_ids)
    print(f"cluster: kept {len(kept)} new ({skipped} skipped as near-dupes, {new_clusters} new clusters)")

    cache_mod.upsert(conn, kept)
    cache_mod.mark_shown(conn, [k["url"] for k in kept])
    return {k["cluster_id"] for k in kept}, conn


def _synthesise_clusters(conn, touched_cluster_ids: set[str]) -> None:
    """Synthesise clusters that are touched today OR lack a synthesis row
    (backfill for clusters cached before Anthropic was available)."""
    from src.synthesise import SynthesiseError, synthesise

    # Reset event dates for clusters whose span is suspiciously long (discrete
    # dates mistaken for a continuous range). Clearing them here means they're
    # picked up by the date_backfill pass later in this same run.
    wrong_year = cache_mod.reset_wrong_year_event_dates(conn)
    if wrong_year:
        print(f"synthesise: reset {wrong_year} cluster(s) with wrong event year")

    bad_spans = cache_mod.clusters_with_bad_event_span(conn, max_days=90)
    if bad_spans:
        print(f"synthesise: fixing {len(bad_spans)} cluster(s) with bad event span")
        for cid in bad_spans:
            cluster = cache_mod.load_cluster(conn, cid)
            if cluster and cluster.get("event_start"):
                # Keep event_start, clear only event_end — this breaks the reset
                # loop: NULL event_end is never flagged as a bad span, so the
                # cluster stays on the calendar for just its start date.
                cache_mod.set_event_dates(conn, cid, cluster["event_start"], None)
            else:
                cache_mod.set_event_dates(conn, cid, None, None)

    backfill = set(cache_mod.clusters_needing_synthesis(conn))
    to_synthesise = touched_cluster_ids | backfill

    if not to_synthesise:
        print("synthesise: nothing to do (no touched clusters, no missing synth)")
    else:
        print(
            f"synthesise: {len(to_synthesise)} cluster(s) "
            f"({len(touched_cluster_ids)} touched, {len(backfill)} backfill)"
        )

        for cid in sorted(to_synthesise):
            items = cache_mod.cluster_items(conn, cid)
            if not items:
                continue
            try:
                result = synthesise(items)
                if result is None:
                    print(f"  {cid}: skipped (insufficient content)")
                    continue
                primary = min(items, key=lambda i: i["published_at"])
                event_start = result.get("event_start")
                event_end = result.get("event_end")
                # "jusqu'au DATE" pattern: Claude sets event_end only.
                # Fill event_start from the primary item's publication date
                # so the event spans from when it was published to the end date.
                if event_end and not event_start:
                    event_start = primary["published_at"][:10]
                cache_mod.upsert_cluster(
                    conn,
                    cid,
                    title=result["title"],
                    summary=result["summary"],
                    framing_note=result["framing_note"],
                    read_for=result["read_for"],
                    category=result["category"],
                    event_start=event_start,
                    event_end=event_end,
                    event_name=result.get("event_name"),
                    primary_url=primary["url"],
                )
                cat_label = f"[{result['category']}] " if result["category"] else ""
                print(f"  {cid}: {cat_label}'{result['title'][:60]}…'")
            except SynthesiseError as e:
                print(f"  {cid}: FAILED — {e}", file=sys.stderr)

    # Backfill event dates for clusters whose items are pruned — lightweight
    # extraction from stored title+summary avoids re-fetching source content.
    from src.synthesise import extract_event_dates
    date_backfill = [
        cid for cid in cache_mod.clusters_needing_event_dates(conn)
        if cid not in to_synthesise  # already handled above
    ]
    if date_backfill:
        print(f"synthesise: {len(date_backfill)} event cluster(s) need date backfill")
    for cid in date_backfill:
        cluster = cache_mod.load_cluster(conn, cid)
        if not cluster:
            continue
        try:
            start, end = extract_event_dates(cluster)
            if start:
                cache_mod.set_event_dates(conn, cid, start, end)
                print(f"  {cid}: date backfill {start}" + (f"→{end}" if end else ""))
        except Exception as e:
            print(f"  {cid}: date backfill FAILED — {e}", file=sys.stderr)


def _close_conn(conn: Any) -> None:
    if conn is not None:
        try:
            cache_mod.vacuum(conn)
            conn.close()
        except Exception:
            pass


def _write_sitemap(archive_dates: list[dict[str, str]], out_path: Path) -> None:
    today = datetime.now(PARIS).date().isoformat()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f'  <url><loc>{SITE_BASE}/</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>',
        f'  <url><loc>{SITE_BASE}/calendar.html</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.8</priority></url>',
    ]
    for arc in archive_dates:
        d = arc["date"]
        lines.append(
            f'  <url><loc>{SITE_BASE}/archive/{d}.html</loc><lastmod>{d}</lastmod><changefreq>never</changefreq><priority>0.6</priority></url>'
        )
    lines.append("</urlset>")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fetch_all() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name, fetch_fn in FETCHERS:
        try:
            fetched = fetch_fn()
            print(f"{name}: {len(fetched)} items")
            items.extend(fetched)
        except Exception as e:  # noqa: BLE001 — one broken fetcher must not kill the run
            print(f"{name}: FAILED — {type(e).__name__}: {e}", file=sys.stderr)
    return items


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    items = _fetch_all()
    print(f"total: {len(items)} items from {len(FETCHERS)} sources")

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    conn = None
    if openai_key:
        try:
            touched, conn = _cluster_today(items)
            if anthropic_key:
                _synthesise_clusters(conn, touched)
            else:
                print("synthesise: skipped (ANTHROPIC_API_KEY not set)")
            # Generate Instagram images (Graph API posting happens after git push)
            try:
                from src.instagram import run as instagram_run, render_weekend_carousel, render_today_events
                today_slug    = datetime.now(PARIS).date().isoformat()
                today_weekday = datetime.now(PARIS).weekday()  # 4=Fri, 5=Sat
                ig_dir        = INSTAGRAM_DIR / today_slug

                instagram_run(conn, ig_dir)
                render_today_events(conn, ig_dir)

                if today_weekday == 4:  # Friday only — after Clutch+OfficeTourisme newsletters
                    render_weekend_carousel(conn, ig_dir)
            except Exception as _ig_err:
                print(f"instagram: FAILED — {type(_ig_err).__name__}: {_ig_err}", file=sys.stderr)

            entries = _entries_from_cache(conn)
            # Email only clusters not yet emailed (prevents old content resurfacing)
            email_cluster_ids = set(cache_mod.clusters_to_email(conn))
            email_entries = [e for e in entries if e["id"] in email_cluster_ids]
            print(f"email queue: {len(email_entries)} new cluster(s) to send")
        except Exception as e:  # noqa: BLE001 — fail open to keep daily email shipping
            print(f"cluster/synthesise: FAILED ({type(e).__name__}: {e}) — falling back to item-level feed", file=sys.stderr)
            entries = [_item_to_entry(it) for it in items]
            email_entries = entries
    else:
        print("cluster: skipped (OPENAI_API_KEY not set) — item-level feed")
        entries = [_item_to_entry(it) for it in items]
        email_entries = entries

    write_atom(entries, FEED_OUTPUT)
    print(f"wrote {FEED_OUTPUT} ({FEED_OUTPUT.stat().st_size} bytes)")

    # Compute archive dates (past days found in entries + existing archive files)
    today_paris = datetime.now(PARIS).date()
    past_dates: set[date] = set()
    for e in entries:
        dt = datetime.fromisoformat(e["published_at"])
        d = dt.astimezone(PARIS).date()
        if d < today_paris:
            past_dates.add(d)
    # Also include dates from archive HTML files already on disk
    for arc_file in ARCHIVE_DIR.glob("*.html"):
        try:
            d = date.fromisoformat(arc_file.stem)
            if d < today_paris:
                past_dates.add(d)
        except ValueError:
            pass
    from src.landing import FRENCH_MONTHS
    archive_dates = [
        {
            "date": d.isoformat(),
            "label": _french_long_date(datetime(d.year, d.month, d.day, tzinfo=PARIS)),
            "short": f"{d.day} {FRENCH_MONTHS[d.month - 1]}",
        }
        for d in sorted(past_dates, reverse=True)
    ]

    calendar_events = cache_mod.load_calendar_events(conn) if conn else []
    print(f"calendar: {len(calendar_events)} event(s) with structured dates")

    render_landing(FEED_OUTPUT, LANDING_OUTPUT, archive_dates=archive_dates, calendar_events=calendar_events)
    print(f"wrote {LANDING_OUTPUT} ({LANDING_OUTPUT.stat().st_size} bytes)")

    for arc in archive_dates:
        arc_date = date.fromisoformat(arc["date"])
        arc_path = ARCHIVE_DIR / f"{arc['date']}.html"
        render_landing(FEED_OUTPUT, arc_path, filter_date=arc_date, archive_dates=archive_dates, is_archive=True)
        print(f"wrote {arc_path}")

    render_calendar_page(calendar_events, CALENDAR_OUTPUT)
    print(f"wrote {CALENDAR_OUTPUT} ({CALENDAR_OUTPUT.stat().st_size} bytes)")

    from gen_ics import build_ics
    if conn:
        import sqlite3 as _sqlite3
        _conn2 = _sqlite3.connect(str(CACHE_PATH))
        _conn2.row_factory = _sqlite3.Row
        _ics_rows = [
            dict(r) for r in _conn2.execute(
                "SELECT cluster_id,event_start,event_end,event_name,title,summary,primary_url"
                " FROM clusters WHERE event_start IS NOT NULL AND event_start != ''"
                "   AND primary_url IS NOT NULL AND primary_url != '' ORDER BY event_start"
            ).fetchall()
        ]
        _conn2.close()
        CALENDAR_ICS_OUTPUT.write_bytes(build_ics(_ics_rows).encode("utf-8"))
        print(f"wrote {CALENDAR_ICS_OUTPUT} ({len(_ics_rows)} events)")

    _write_sitemap(archive_dates, SITEMAP_OUTPUT)
    print(f"wrote {SITEMAP_OUTPUT} ({len(archive_dates) + 2} URLs)")

    search_index_path = Path("docs/search-index.json")
    import json as _json
    search_index = [
        {
            "title": e.get("title", ""),
            "summary": e.get("summary", ""),
            "date": e.get("published_at", "")[:10],
            "url": e.get("url", ""),
            "source": (e.get("authors") or [""])[0],
        }
        for e in entries
    ]
    search_index_path.write_text(
        _json.dumps(search_index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {search_index_path} ({len(search_index)} entries)")

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    audience_id = os.environ.get("RESEND_AUDIENCE_ID", "").strip()
    sender = os.environ.get("EMAIL_FROM_ADDRESS", "").strip()

    if not (api_key and audience_id and sender):
        print("email send: skipped (RESEND_API_KEY / RESEND_AUDIENCE_ID / EMAIL_FROM_ADDRESS not all set)")
        _close_conn(conn)
        return

    if not email_entries:
        print("email send: skipped (no new clusters to email today)")
        _close_conn(conn)
        return

    subject, html = render_email(email_entries)
    try:
        result = send_broadcast(
            api_key=api_key,
            audience_id=audience_id,
            sender=sender,
            subject=subject,
            html=html,
        )
        print(f"email send: broadcast {result.get('id')} dispatched · subject: {subject!r}")
        # Mark clusters as emailed only after confirmed send
        if conn:
            cache_mod.mark_emailed(conn, [e["id"] for e in email_entries])
    except SendError as e:
        print(f"email send: FAILED — {e}", file=sys.stderr)
        _close_conn(conn)
        sys.exit(1)

    _close_conn(conn)


if __name__ == "__main__":
    main()
