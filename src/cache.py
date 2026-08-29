from __future__ import annotations

import sqlite3
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DB_PATH = Path("data/items_seen.db")
RETENTION_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    url TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    item_type TEXT NOT NULL,
    summary TEXT,
    extracted_text TEXT,
    embedding BLOB NOT NULL,
    cluster_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    shown_in_feed INTEGER NOT NULL DEFAULT 0,
    image_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_seen_at ON items(seen_at);
CREATE INDEX IF NOT EXISTS idx_cluster_id ON items(cluster_id);

CREATE TABLE IF NOT EXISTS clusters (
    cluster_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    framing_note TEXT,
    read_for TEXT,
    category TEXT,
    last_synthesised_at TEXT NOT NULL
);
"""

# Migrations: add columns if missing (CREATE IF NOT EXISTS doesn't add them
# to pre-existing tables). New columns must be nullable.
MIGRATIONS = [
    "ALTER TABLE clusters ADD COLUMN category TEXT",
    "ALTER TABLE clusters ADD COLUMN emailed_at TEXT",
    "ALTER TABLE clusters ADD COLUMN event_start TEXT",
    "ALTER TABLE clusters ADD COLUMN event_end TEXT",
    "ALTER TABLE clusters ADD COLUMN event_name TEXT",
    "ALTER TABLE clusters ADD COLUMN primary_url TEXT",
    "ALTER TABLE items ADD COLUMN image_url TEXT",
    "ALTER TABLE clusters ADD COLUMN ig_story_at TEXT",
    "ALTER TABLE clusters ADD COLUMN ig_caption TEXT",
    "ALTER TABLE clusters ADD COLUMN ig_caption_long TEXT",
    "ALTER TABLE clusters ADD COLUMN ig_hashtags TEXT",
    "ALTER TABLE clusters ADD COLUMN ig_mention TEXT",
    "ALTER TABLE clusters ADD COLUMN venue TEXT",
    "ALTER TABLE clusters ADD COLUMN listicle_items TEXT",
    "ALTER TABLE clusters ADD COLUMN highlight TEXT",
    "ALTER TABLE clusters ADD COLUMN image_url TEXT",
]


def _pack(emb: list[float]) -> bytes:
    return struct.pack(f"{len(emb)}f", *emb)


def _unpack(data: bytes) -> list[float]:
    return list(struct.unpack(f"{len(data) // 4}f", data))


def open_cache(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            # column already exists; safe to ignore
            pass
    conn.commit()
    return conn


_SYSTEM_SOURCES = ("google.com", "accounts.google.com", "googlemail.com",
                   "microsoft.com", "outlook.com", "hotmail.com", "apple.com", "icloud.com",
                   # gmail.com: purge fallback items from Gmail-forwarded emails whose HTML
                   # was empty (old KV entries). NOT in _SYSTEM_SENDER_DOMAINS so future
                   # emails still pass the transactional check and reach content detection.
                   "gmail.com",
                   # Fallback source keys produced when a newsletter extractor returns empty
                   "tourinsoft",                # OfficeTourisme fallback (extractor era)
                   "tourinsoft.com",            # OfficeTourisme fallback (post-retirement, generic per-sender fallback)
                   "newsletter-lebonbon",       # Le Bonbon fallback (extractor era, no .fr)
                   "newsletter-lebonbon.fr",    # Le Bonbon fallback (post-extractor, full domain)
                   "le_bonbon",                 # Le Bonbon extracted items (source removed)
                   )


def purge_newsletter_fallbacks(conn: sqlite3.Connection) -> int:
    """Remove single-item fallback entries for newsletters whose extractor is now fixed.

    When a newsletter extractor returns [] (e.g. L'Essentiel before the URL-ordering
    fix), the fallback handler creates one item using the email subject as title and a
    tracking URL.  Properly extracted items have canonical /newsletter/…#ID URLs.
    This deletes fallback items whose URL does NOT match the canonical pattern.
    """
    cur = conn.execute(
        "DELETE FROM items WHERE source = 'lessentiel' "
        "AND url NOT LIKE '%lessentiel.fr/newsletter/toulouse/%'"
    )
    conn.commit()
    return cur.rowcount


def purge_system_sources(conn: sqlite3.Connection) -> int:
    """Remove cached items whose source key matches known system/transactional senders."""
    placeholders = ",".join("?" * len(_SYSTEM_SOURCES))
    cur = conn.execute(
        f"DELETE FROM items WHERE source IN ({placeholders})",
        _SYSTEM_SOURCES,
    )
    conn.commit()
    return cur.rowcount


def prune(conn: sqlite3.Connection, *, days: int = RETENTION_DAYS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM items WHERE seen_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def _row_to_item(r: sqlite3.Row) -> dict[str, Any]:
    keys = r.keys()
    return {
        "url": r["url"],
        "source": r["source"],
        "title": r["title"],
        "published_at": r["published_at"],
        "item_type": r["item_type"],
        "summary": r["summary"],
        "extracted_text": r["extracted_text"],
        "embedding": _unpack(r["embedding"]),
        "cluster_id": r["cluster_id"],
        "seen_at": r["seen_at"],
        "shown_in_feed": bool(r["shown_in_feed"]),
        "image_url": r["image_url"] if "image_url" in keys else None,
    }


def load_recent(conn: sqlite3.Connection, *, days: int = RETENTION_DAYS) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM items WHERE seen_at >= ?",
        (cutoff,),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def cluster_items(conn: sqlite3.Connection, cluster_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM items WHERE cluster_id = ? ORDER BY published_at",
        (cluster_id,),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def upsert_cluster(
    conn: sqlite3.Connection,
    cluster_id: str,
    *,
    title: str,
    summary: str,
    framing_note: str | None,
    read_for: list[dict[str, str]] | None,
    category: str | None = None,
    event_start: str | None = None,
    event_end: str | None = None,
    event_name: str | None = None,
    primary_url: str | None = None,
    ig_caption: str | None = None,
    ig_caption_long: str | None = None,
    ig_hashtags: str | None = None,
    venue: str | None = None,
    ig_mention: str | None = None,
    listicle_items: str | None = None,
    highlight: str | None = None,
    image_url: str | None = None,
) -> None:
    import json as _json

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO clusters
           (cluster_id, title, summary, framing_note, read_for, category,
            event_start, event_end, event_name, primary_url, last_synthesised_at,
            ig_caption, ig_caption_long, ig_hashtags, venue, ig_mention, listicle_items, highlight, image_url)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cluster_id) DO UPDATE SET
             title               = excluded.title,
             summary             = excluded.summary,
             framing_note        = excluded.framing_note,
             read_for            = excluded.read_for,
             category            = excluded.category,
             event_start         = excluded.event_start,
             event_end           = excluded.event_end,
             event_name          = excluded.event_name,
             primary_url         = COALESCE(excluded.primary_url, clusters.primary_url),
             last_synthesised_at = excluded.last_synthesised_at,
             ig_caption          = COALESCE(excluded.ig_caption, clusters.ig_caption),
             ig_caption_long     = COALESCE(excluded.ig_caption_long, clusters.ig_caption_long),
             ig_hashtags         = COALESCE(excluded.ig_hashtags, clusters.ig_hashtags),
             venue               = COALESCE(excluded.venue, clusters.venue),
             ig_mention          = COALESCE(excluded.ig_mention, clusters.ig_mention),
             listicle_items      = COALESCE(excluded.listicle_items, clusters.listicle_items),
             highlight           = COALESCE(excluded.highlight, clusters.highlight),
             image_url           = COALESCE(excluded.image_url, clusters.image_url)""",
        (
            cluster_id,
            title,
            summary,
            framing_note,
            _json.dumps(read_for) if read_for else None,
            category,
            event_start,
            event_end,
            event_name,
            primary_url,
            now,
            ig_caption,
            ig_caption_long,
            ig_hashtags,
            venue,
            ig_mention,
            listicle_items,
            highlight,
            image_url,
        ),
    )
    conn.commit()


def set_event_dates(
    conn: sqlite3.Connection,
    cluster_id: str,
    event_start: str,
    event_end: str | None = None,
) -> None:
    conn.execute(
        "UPDATE clusters SET event_start = ?, event_end = ? WHERE cluster_id = ?",
        (event_start, event_end, cluster_id),
    )
    conn.commit()


def load_cluster(conn: sqlite3.Connection, cluster_id: str) -> dict[str, Any] | None:
    import json as _json

    r = conn.execute(
        "SELECT * FROM clusters WHERE cluster_id = ?", (cluster_id,)
    ).fetchone()
    if not r:
        return None
    keys = r.keys()
    return {
        "cluster_id": r["cluster_id"],
        "title": r["title"],
        "summary": r["summary"],
        "framing_note": r["framing_note"],
        "read_for": _json.loads(r["read_for"]) if r["read_for"] else None,
        "category": r["category"] if "category" in keys else None,
        "event_start": r["event_start"] if "event_start" in keys else None,
        "event_end": r["event_end"] if "event_end" in keys else None,
        "event_name": r["event_name"] if "event_name" in keys else None,
        "last_synthesised_at": r["last_synthesised_at"],
    }


def upsert(conn: sqlite3.Connection, items: Iterable[dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for it in items:
        rows.append(
            (
                it["url"],
                it["source"],
                it["title"],
                it["published_at"],
                it["item_type"],
                it.get("summary"),
                it.get("extracted_text"),
                _pack(it["embedding"]),
                it["cluster_id"],
                it.get("seen_at", now),
                int(it.get("shown_in_feed", False)),
                it.get("image_url"),
            )
        )
    conn.executemany(
        "INSERT OR REPLACE INTO items "
        "(url, source, title, published_at, item_type, summary, extracted_text, "
        "embedding, cluster_id, seen_at, shown_in_feed, image_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def mark_shown(conn: sqlite3.Connection, urls: Iterable[str]) -> None:
    conn.executemany(
        "UPDATE items SET shown_in_feed = 1 WHERE url = ?",
        [(u,) for u in urls],
    )
    conn.commit()


def clusters_to_email(conn: sqlite3.Connection, within_hours: int = 48) -> list[str]:
    """Cluster_ids that should appear in today's email.

    Restricted to clusters that have at least one item with seen_at within
    the last `within_hours` hours.  This prevents old clusters (e.g. from
    a day when the email send failed) from resurfacing in subsequent emails
    even though their emailed_at is still NULL.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=within_hours)).isoformat()
    rows = conn.execute(
        """SELECT DISTINCT i.cluster_id
           FROM items i
           LEFT JOIN clusters c ON c.cluster_id = i.cluster_id
           WHERE i.seen_at >= ?
             AND (c.cluster_id IS NULL OR c.emailed_at IS NULL)
        """,
        (cutoff,),
    ).fetchall()
    return [r["cluster_id"] for r in rows]


def mark_emailed(conn: sqlite3.Connection, cluster_ids: list[str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "UPDATE clusters SET emailed_at = ? WHERE cluster_id = ?",
        [(now, cid) for cid in cluster_ids],
    )
    conn.commit()


def clusters_needing_synthesis(conn: sqlite3.Connection) -> list[str]:
    """Cluster_ids that lack a clusters row OR have one but no category.
    The category-null case captures clusters synthesised before category
    was a synthesis output."""
    rows = conn.execute(
        "SELECT DISTINCT i.cluster_id "
        "FROM items i "
        "LEFT JOIN clusters c ON c.cluster_id = i.cluster_id "
        "WHERE c.cluster_id IS NULL OR c.category IS NULL"
    ).fetchall()
    return [r["cluster_id"] for r in rows]


def reset_wrong_year_event_dates(conn: sqlite3.Connection, expected_year: int | None = None) -> int:
    """Clear event_start/end for clusters whose year doesn't match expected_year.

    Synthesis sometimes infers the wrong year when the source text says 'samedi 23 mai'
    without an explicit year.  Resetting lets the backfill re-extract with the correct
    year assumption.  Defaults to the current calendar year.
    """
    if expected_year is None:
        expected_year = datetime.now(timezone.utc).year
    cur = conn.execute(
        "UPDATE clusters SET event_start = NULL, event_end = NULL "
        "WHERE category = 'event' AND event_start IS NOT NULL "
        "AND event_start NOT LIKE ?",
        (f"{expected_year}%",),
    )
    conn.commit()
    return cur.rowcount


def clusters_with_bad_event_span(conn: sqlite3.Connection, max_days: int = 7) -> list[str]:
    """Event clusters whose event_end is more than max_days after event_start.
    These are likely the result of Claude spanning discrete dates into a continuous
    range (e.g. 'May 22 and June 14' → event_end=June 14). Clearing them forces
    re-extraction with the corrected prompt."""
    rows = conn.execute(
        "SELECT cluster_id FROM clusters "
        "WHERE category = 'event' AND event_end IS NOT NULL "
        "AND julianday(event_end) - julianday(event_start) > ?",
        (max_days,),
    ).fetchall()
    return [r["cluster_id"] for r in rows]


def clusters_needing_event_dates(conn: sqlite3.Connection) -> list[str]:
    """Event clusters that are missing a structured event_start date."""
    rows = conn.execute(
        "SELECT cluster_id FROM clusters WHERE category = 'event' AND event_start IS NULL"
    ).fetchall()
    return [r["cluster_id"] for r in rows]


def load_calendar_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All event clusters with a structured event_start date, with their primary item URL/source."""
    rows = conn.execute(
        """SELECT c.cluster_id, c.title, c.summary, c.event_start, c.event_end, c.event_name,
                  COALESCE(c.primary_url,
                    (SELECT url FROM items WHERE cluster_id = c.cluster_id ORDER BY published_at LIMIT 1)
                  ) AS url,
                  (SELECT source FROM items WHERE cluster_id = c.cluster_id ORDER BY published_at LIMIT 1) AS source
           FROM clusters c
           WHERE c.category = 'event' AND c.event_start IS NOT NULL
           ORDER BY c.event_start"""
    ).fetchall()
    return [dict(r) for r in rows]


def load_weekend_events(conn: sqlite3.Connection, earliest_start: str, date_to: str) -> list[dict[str, Any]]:
    """Event clusters that START within [earliest_start, date_to].

    Deliberately filters on event_start, not on whether a long-running event's
    span merely overlaps the window — a festival running since May that's
    still "ongoing" isn't specifically relevant to this weekend, and would
    otherwise crowd out genuinely new/timely events within the slide cap.
    """
    rows = conn.execute(
        """SELECT c.cluster_id, c.title, c.summary, c.category, c.event_start, c.event_name,
                  c.ig_caption, c.venue, c.highlight,
                  COALESCE(c.primary_url,
                    (SELECT url FROM items WHERE cluster_id = c.cluster_id ORDER BY published_at LIMIT 1)
                  ) AS url,
                  (SELECT source FROM items WHERE cluster_id = c.cluster_id ORDER BY published_at LIMIT 1) AS source,
                  COALESCE(c.image_url,
                    (SELECT image_url FROM items
                     WHERE cluster_id = c.cluster_id AND image_url IS NOT NULL
                     ORDER BY published_at LIMIT 1)
                  ) AS image_url
           FROM clusters c
           WHERE c.category = 'event'
             AND c.event_start >= ?
             AND c.event_start <= ?
           ORDER BY c.event_start""",
        (earliest_start, date_to),
    ).fetchall()
    return [dict(r) for r in rows]


def load_events_on_date(conn: sqlite3.Connection, date_iso: str) -> list[dict[str, Any]]:
    """Event clusters eligible for today's Instagram Story.

    Short events (span <= 4 days, or no end date) repost daily while active —
    Stories disappear after 24h so this is intentional. Long events (span > 4
    days) are eligible only once, ever: during a 3-day pre-event teaser window
    ending on their opening day, and only if never posted before. There is no
    "repost after a gap" behaviour for long events — once ig_story_at is set,
    they never reappear.
    The image filter in render_today_events naturally caps volume to image-having events only.
    Covers single-day and multi-day events (event_start <= date <= event_end).
    """
    # eff_start: use the cluster's explicit event_start when set; fall back to the
    # item's published_at date for events that only use relative date language
    # ("ce soir", "aujourd'hui", "cet après-midi") which synthesis correctly
    # leaves as NULL rather than guessing. This fallback assumes publication
    # time correlates with the event being imminent, which holds for news/
    # newsletter sources but NOT office_tourisme (Tourinsoft): its
    # published_at is just whenever the scraper happened to run, unrelated
    # to the event's real date. Exclude office_tourisme items as the fallback
    # anchor — an office_tourisme-only cluster with no event_start stays
    # eff_start NULL and is correctly excluded below rather than mislabelled
    # as "happening today".
    rows = conn.execute(
        """SELECT cluster_id, title, summary, category, event_start, event_end,
                  event_name, ig_caption, ig_hashtags, venue, ig_mention, highlight, url, source, image_url
           FROM (
             SELECT c.cluster_id, c.title, c.summary, c.category, c.event_start, c.event_end,
                    c.event_name, c.ig_caption, c.ig_hashtags, c.venue, c.ig_mention, c.highlight, c.ig_story_at,
                    COALESCE(c.primary_url,
                      (SELECT url FROM items WHERE cluster_id = c.cluster_id ORDER BY published_at LIMIT 1)
                    ) AS url,
                    (SELECT source FROM items WHERE cluster_id = c.cluster_id ORDER BY published_at LIMIT 1) AS source,
                    COALESCE(c.image_url,
                      (SELECT image_url FROM items
                       WHERE cluster_id = c.cluster_id AND image_url IS NOT NULL
                       ORDER BY published_at LIMIT 1)
                    ) AS image_url,
                    COALESCE(c.event_start,
                      DATE((SELECT published_at FROM items
                            WHERE cluster_id = c.cluster_id AND source != 'office_tourisme'
                            ORDER BY published_at LIMIT 1))
                    ) AS eff_start
             FROM clusters c
             WHERE c.category = 'event'
           )
           WHERE eff_start IS NOT NULL
             AND (
             -- short events (span <= 4 days, or no end date): active window, daily repost
             (
               (event_end IS NULL OR julianday(event_end) - julianday(eff_start) <= 4)
               AND eff_start <= ?
               AND (event_end >= ? OR (event_end IS NULL AND eff_start >= ?))
               AND (ig_story_at IS NULL OR ig_story_at < ?)
             )
             OR (
               -- long events (span > 4 days): one-shot pre-event teaser window
               -- ending on opening day, only if never posted before. Note this
               -- does NOT require eff_start <= today — the window runs before
               -- the event starts too.
               event_end IS NOT NULL AND julianday(event_end) - julianday(eff_start) > 4
               AND ig_story_at IS NULL
               AND julianday(eff_start) - julianday(?) BETWEEN 0 AND 3
             )
           )
           ORDER BY eff_start""",
        (date_iso, date_iso, date_iso, date_iso, date_iso),
    ).fetchall()
    return [dict(r) for r in rows]


def find_event_cluster_candidates(
    conn: sqlite3.Connection, start: str, end: str | None, pad_days: int = 3
) -> list[dict[str, Any]]:
    """Event clusters (category='event') whose date span overlaps
    [start-pad, end_or_start+pad]. Queries `clusters` directly rather than
    joining `items`, so it still finds a match even after the cluster's
    original items have been pruned from the 7-day `items` window."""
    from datetime import date as _date, timedelta as _timedelta

    lo = (_date.fromisoformat(start) - _timedelta(days=pad_days)).isoformat()
    hi = (_date.fromisoformat(end or start) + _timedelta(days=pad_days)).isoformat()
    rows = conn.execute(
        """SELECT c.cluster_id, c.title, c.event_name, c.event_start, c.event_end
           FROM clusters c
           WHERE c.category = 'event' AND c.event_start IS NOT NULL
             AND c.event_start <= ?
             AND COALESCE(c.event_end, c.event_start) >= ?""",
        (hi, lo),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_ig_story_posted(conn: sqlite3.Connection, cluster_ids: list[str], posted_at: str) -> None:
    """Record that these clusters have been posted as Instagram Stories."""
    conn.executemany(
        "UPDATE clusters SET ig_story_at = ? WHERE cluster_id = ?",
        [(posted_at, cid) for cid in cluster_ids],
    )
    conn.commit()


def load_instagram_clusters(conn: sqlite3.Connection, since_iso: str) -> list[dict[str, Any]]:
    """Clusters synthesised since `since_iso` (ISO datetime), for regular posts +
    listicle carousels. Excludes category='news' EXCEPT when it carries real
    listicle_items — a "que faire ce week-end" roundup is correctly "news"
    (it's not a single dedicated event) but is still legitimate listicle
    carousel content; only bare hard-news with no listicle structure is excluded.

    Returns each cluster with its primary source and image_url pulled from items.
    """
    rows = conn.execute(
        """SELECT c.cluster_id, c.title, c.summary, c.category,
                  c.ig_caption, c.ig_caption_long, c.ig_hashtags, c.venue, c.ig_mention, c.listicle_items, c.highlight,
                  COALESCE(c.primary_url,
                    (SELECT url FROM items WHERE cluster_id = c.cluster_id ORDER BY published_at LIMIT 1)
                  ) AS url,
                  (SELECT source FROM items WHERE cluster_id = c.cluster_id ORDER BY published_at LIMIT 1) AS source,
                  COALESCE(c.image_url,
                    (SELECT image_url FROM items
                     WHERE cluster_id = c.cluster_id AND image_url IS NOT NULL
                     ORDER BY published_at LIMIT 1)
                  ) AS image_url
           FROM clusters c
           WHERE (c.category != 'news' OR c.listicle_items IS NOT NULL)
             AND c.category IS NOT NULL
             AND c.last_synthesised_at >= ?
           ORDER BY c.last_synthesised_at DESC""",
        (since_iso,),
    ).fetchall()
    return [dict(r) for r in rows]


def vacuum(conn: sqlite3.Connection) -> None:
    conn.execute("VACUUM")
