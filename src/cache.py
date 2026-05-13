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
    shown_in_feed INTEGER NOT NULL DEFAULT 0
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


def prune(conn: sqlite3.Connection, *, days: int = RETENTION_DAYS) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cur = conn.execute("DELETE FROM items WHERE seen_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def _row_to_item(r: sqlite3.Row) -> dict[str, Any]:
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
) -> None:
    import json as _json

    now = datetime.now(timezone.utc).isoformat()
    # ON CONFLICT DO UPDATE preserves emailed_at — INSERT OR REPLACE would
    # nuke the whole row and reset emailed_at to NULL every synthesis pass.
    conn.execute(
        """INSERT INTO clusters
           (cluster_id, title, summary, framing_note, read_for, category, last_synthesised_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cluster_id) DO UPDATE SET
             title              = excluded.title,
             summary            = excluded.summary,
             framing_note       = excluded.framing_note,
             read_for           = excluded.read_for,
             category           = excluded.category,
             last_synthesised_at = excluded.last_synthesised_at""",
        (
            cluster_id,
            title,
            summary,
            framing_note,
            _json.dumps(read_for) if read_for else None,
            category,
            now,
        ),
    )
    conn.commit()


def load_cluster(conn: sqlite3.Connection, cluster_id: str) -> dict[str, Any] | None:
    import json as _json

    r = conn.execute(
        "SELECT * FROM clusters WHERE cluster_id = ?", (cluster_id,)
    ).fetchone()
    if not r:
        return None
    return {
        "cluster_id": r["cluster_id"],
        "title": r["title"],
        "summary": r["summary"],
        "framing_note": r["framing_note"],
        "read_for": _json.loads(r["read_for"]) if r["read_for"] else None,
        "category": r["category"] if "category" in r.keys() else None,
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
            )
        )
    conn.executemany(
        "INSERT OR REPLACE INTO items "
        "(url, source, title, published_at, item_type, summary, extracted_text, "
        "embedding, cluster_id, seen_at, shown_in_feed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def mark_shown(conn: sqlite3.Connection, urls: Iterable[str]) -> None:
    conn.executemany(
        "UPDATE items SET shown_in_feed = 1 WHERE url = ?",
        [(u,) for u in urls],
    )
    conn.commit()


def clusters_to_email(conn: sqlite3.Connection) -> list[str]:
    """Cluster_ids that should appear in today's email:
    - Clusters with items but no synthesis row yet (synthesis may have failed)
    - Clusters with synthesis and emailed_at IS NULL (synthesised, not yet sent)
    Excludes clusters already marked as emailed.
    """
    rows = conn.execute(
        """SELECT DISTINCT i.cluster_id
           FROM items i
           LEFT JOIN clusters c ON c.cluster_id = i.cluster_id
           WHERE c.cluster_id IS NULL      -- synthesis never ran / failed
              OR c.emailed_at IS NULL      -- synthesised, not yet emailed
        """
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


def vacuum(conn: sqlite3.Connection) -> None:
    conn.execute("VACUUM")
