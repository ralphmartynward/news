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
    last_synthesised_at TEXT NOT NULL
);
"""


def _pack(emb: list[float]) -> bytes:
    return struct.pack(f"{len(emb)}f", *emb)


def _unpack(data: bytes) -> list[float]:
    return list(struct.unpack(f"{len(data) // 4}f", data))


def open_cache(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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
) -> None:
    import json as _json

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO clusters "
        "(cluster_id, title, summary, framing_note, read_for, last_synthesised_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            cluster_id,
            title,
            summary,
            framing_note,
            _json.dumps(read_for) if read_for else None,
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


def vacuum(conn: sqlite3.Connection) -> None:
    conn.execute("VACUUM")
