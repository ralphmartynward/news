from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata
from typing import Any

TEXT_MATCH_THRESHOLD = 0.45
EMBED_MATCH_THRESHOLD = 0.72


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().strip()


_MIN_NAME_SUBSTRING_LEN = 8

# Filler words common in French event copy -- excluded so token-overlap
# matching isn't fooled by shared articles/prepositions alone.
_STOPWORDS = {"de", "du", "des", "la", "le", "les", "l", "d", "a", "au", "aux",
              "et", "en", "un", "une", "pour", "avec", "son", "sa", "ses"}


def _name_mentioned_in(haystack_norm: str, name: str) -> bool:
    """Whether a candidate's (fairly distinctive) event name is literally
    quoted in an item's own text. Newsletter articles like L'Essentiel often
    lead with an unrelated-looking quote/hook title and only mention the
    event's proper name in the body — a plain title-vs-title ratio misses
    that, so this substring check is a second, independent path to a match."""
    name_norm = _norm(name)
    if len(name_norm) < _MIN_NAME_SUBSTRING_LEN:
        return False
    return name_norm in haystack_norm


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s) if len(t) > 2 and t not in _STOPWORDS}


def _name_tokens_present_in(haystack_norm: str, name: str) -> bool:
    """Whether ALL of a candidate's distinctive event-name words appear
    somewhere in the item's text, not necessarily contiguously. A headline-
    style item title ("HEXAGONE MMA fait son grand retour à Toulouse : une
    soirée de combats XXL annoncée au Zénith") can score low on both a plain
    ratio and the literal-substring check above against a plainer event_name
    ("Hexagone MMA Toulouse") purely because the words are split apart by
    other headline text -- but every distinctive word from the event name
    still shows up somewhere in the item. Requires at least 2 distinctive
    tokens so a single common word can't trigger a match on its own; the
    embedding-similarity gate below is still the real arbiter, this is only
    a pre-filter for which candidates get embedded and compared.

    Requires >=3 distinctive tokens, not 2 -- a thin generic branded name
    like "Journées du Patrimoine" (only "journees"/"patrimoine" survive
    stopword removal) would otherwise flag nearly every heritage-day article
    as a soft candidate for every OTHER heritage-day sub-event, and that
    genre of name is confirmed to embed deceptively similarly across
    genuinely distinct events (see src/backfill_event_dedup.py's docstring
    for the same failure mode found there).
    """
    name_tokens = _tokens(_norm(name))
    if len(name_tokens) < 3:
        return False
    haystack_tokens = _tokens(haystack_norm)
    return name_tokens.issubset(haystack_tokens)


def find_duplicate_event_cluster(
    conn: sqlite3.Connection, item: dict[str, Any], item_embedding: list[float]
) -> str | None:
    """Find an existing event cluster covering the same real-world event as
    `item`, so a new source's mention of an already-known event (e.g. three
    separate "Rose Festival" entries from different sources) merges into one
    cluster instead of spawning a duplicate.

    Prefers the item's own structured `_event_start` (currently set only by
    src/fetchers/tourinsoft.py and src/fetchers/ticketmaster.py) to scope the
    date-range search tightly. Other sources (e.g. L'Essentiel, Toulouscope)
    never set this, so without a fallback they could never be checked against
    an existing Tourinsoft/Ticketmaster-sourced event cluster and would always
    spawn a duplicate (e.g. two separate "Festival Sign'Ô" clusters, one per
    source). Falls back to the item's `published_at` date with a much wider
    pad — text-ratio + embedding-similarity thresholds below are the real
    guard against false merges, not the date window.
    """
    from src import cache as cache_mod, embed as embed_mod
    import numpy as np

    start = item.get("_event_start")
    pad_days = 3
    if not start:
        start = (item.get("published_at") or "")[:10]
        pad_days = 45
    if not start:
        return None

    candidates = cache_mod.find_event_cluster_candidates(
        conn, start, item.get("_event_end"), pad_days=pad_days
    )
    if not candidates:
        return None

    name_a = _norm(item.get("_event_name") or item.get("title", ""))
    item_text_norm = _norm(
        " ".join(filter(None, [item.get("title"), item.get("summary"), item.get("extracted_text")]))
    )
    soft = [
        c for c in candidates
        if difflib.SequenceMatcher(
            None, name_a, _norm(c.get("event_name") or c.get("title", ""))
        ).ratio() >= TEXT_MATCH_THRESHOLD
        or _name_mentioned_in(item_text_norm, c.get("event_name") or "")
        or _name_tokens_present_in(item_text_norm, c.get("event_name") or "")
    ]
    if not soft:
        return None

    # Embed the candidate's full canonical text (name/title + synthesised
    # summary), not just its bare name — comparing a short label against the
    # item's full article text under-scores real matches (confirmed: a true
    # "Festival Sign'Ô" duplicate scored 0.60 against the bare event_name vs.
    # 0.81 against event_name+summary, straddling EMBED_MATCH_THRESHOLD).
    cand_texts = [
        f"{c.get('event_name') or c.get('title', '')}\n\n{c.get('summary') or ''}".strip()
        for c in soft
    ]
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
