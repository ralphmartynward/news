from __future__ import annotations

import difflib
import sqlite3
import unicodedata
from typing import Any

TEXT_MATCH_THRESHOLD = 0.45
EMBED_MATCH_THRESHOLD = 0.72


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().strip()


def find_duplicate_event_cluster(
    conn: sqlite3.Connection, item: dict[str, Any], item_embedding: list[float]
) -> str | None:
    """Find an existing event cluster covering the same real-world event as
    `item`, so a new source's mention of an already-known event (e.g. three
    separate "Rose Festival" entries from different sources) merges into one
    cluster instead of spawning a duplicate.

    Only meaningful for items carrying structured `_event_start` (currently
    set only by src/fetchers/tourinsoft.py). Returns an existing cluster_id
    to merge into, or None if no confident match is found.
    """
    from src import cache as cache_mod, embed as embed_mod
    import numpy as np

    start = item.get("_event_start")
    if not start:
        return None

    candidates = cache_mod.find_event_cluster_candidates(conn, start, item.get("_event_end"))
    if not candidates:
        return None

    name_a = _norm(item.get("_event_name") or item.get("title", ""))
    soft = [
        c for c in candidates
        if difflib.SequenceMatcher(
            None, name_a, _norm(c.get("event_name") or c.get("title", ""))
        ).ratio() >= TEXT_MATCH_THRESHOLD
    ]
    if not soft:
        return None

    cand_texts = [c.get("event_name") or c.get("title", "") for c in soft]
    cand_embs = embed_mod.embed_batch(cand_texts)

    a = np.array(item_embedding, dtype=np.float32)
    a = a / (np.linalg.norm(a) or 1.0)

    best_cluster_id = None
    best_sim = 0.0
    for cand, emb in zip(soft, cand_embs):
        v = np.array(emb, dtype=np.float32)
        v = v / (np.linalg.norm(v) or 1.0)
        sim = float(a @ v)
        if sim > best_sim:
            best_sim, best_cluster_id = sim, cand["cluster_id"]

    if best_cluster_id and best_sim >= EMBED_MATCH_THRESHOLD:
        return best_cluster_id
    return None
