"""One-off backfill: merge event clusters that duplicate an office_tourisme
event but predate the cross-source dedup fix (src/event_dedup.py).

For each current office_tourisme event cluster, finds other event clusters
covering the same real-world event (date-window overlap + name similarity +
embedding similarity) anywhere in the clusters table, and merges them into
the office_tourisme cluster: moves their items over, re-synthesises the
survivor from the combined item set, and deletes the duplicate cluster row.

Usage:
    python -m src.backfill_event_dedup            # dry run, prints matches only
    python -m src.backfill_event_dedup --apply     # actually merge
"""
from __future__ import annotations

import difflib
import sys
import unicodedata
from datetime import date as _date, timedelta as _timedelta

import numpy as np

from src import cache as cache_mod
from src import embed as embed_mod

TEXT_MATCH_THRESHOLD = 0.45
EMBED_MATCH_THRESHOLD = 0.72
PAD_DAYS = 3


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower().strip()


def _venue_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


def _cluster_source(conn, cluster_id: str) -> str | None:
    row = conn.execute(
        "SELECT source FROM items WHERE cluster_id=? ORDER BY published_at LIMIT 1", (cluster_id,)
    ).fetchone()
    return row[0] if row else None


def _all_event_clusters(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT cluster_id, title, event_name, event_start, event_end, venue
           FROM clusters WHERE category='event' AND event_start IS NOT NULL"""
    ).fetchall()
    return [dict(r) for r in rows]


def _merge_cluster(conn, loser_id: str, winner_id: str) -> None:
    conn.execute("UPDATE items SET cluster_id=? WHERE cluster_id=?", (winner_id, loser_id))
    conn.execute("DELETE FROM clusters WHERE cluster_id=?", (loser_id,))
    conn.commit()


def _resynthesise(conn, cluster_id: str) -> None:
    from src.synthesise import synthesise

    items = cache_mod.cluster_items(conn, cluster_id)
    if not items:
        return
    result = synthesise(items)
    if result is None:
        return
    primary = min(items, key=lambda i: i["published_at"])
    event_start = result.get("event_start")
    event_end = result.get("event_end")
    if event_end and not event_start:
        event_start = primary["published_at"][:10]
    image_candidates = sorted(
        (i for i in items if i.get("image_url")),
        key=lambda i: (i.get("source") != "office_tourisme", i["published_at"]),
    )
    image_url = image_candidates[0]["image_url"] if image_candidates else None
    cache_mod.upsert_cluster(
        conn, cluster_id,
        title=result["title"], summary=result["summary"],
        framing_note=result["framing_note"], read_for=result["read_for"],
        category=result["category"], event_start=event_start, event_end=event_end,
        event_name=result.get("event_name"), primary_url=primary["url"],
        ig_caption=result.get("ig_caption"), ig_caption_long=result.get("ig_caption_long"),
        ig_hashtags=result.get("ig_hashtags"), venue=result.get("venue"),
        ig_mention=result.get("ig_mention"), listicle_items=result.get("listicle_items"),
        highlight=result.get("highlight"), image_url=image_url,
    )


def find_merges(conn) -> list[tuple[dict, list[tuple[dict, float]]]]:
    """Returns [(office_tourisme_cluster, [(dup_cluster, similarity), ...]), ...]."""
    all_events = _all_event_clusters(conn)
    by_id = {e["cluster_id"]: e for e in all_events}
    ot_events = [e for e in all_events if _cluster_source(conn, e["cluster_id"]) == "office_tourisme"]

    plan: list[tuple[dict, list[tuple[dict, float]]]] = []
    consumed: set[str] = set()

    for ot in ot_events:
        ot_id = ot["cluster_id"]
        start = ot["event_start"]
        end = ot.get("event_end") or start
        lo = (_date.fromisoformat(start) - _timedelta(days=PAD_DAYS)).isoformat()
        hi = (_date.fromisoformat(end) + _timedelta(days=PAD_DAYS)).isoformat()

        name_a = _norm(ot.get("event_name") or ot["title"])
        venue_a = _norm(ot.get("venue") or "")
        candidates = [
            e for e in all_events
            if e["cluster_id"] != ot_id
            and e["cluster_id"] not in consumed
            and e["event_start"] and e["event_start"] <= hi
            and (e.get("event_end") or e["event_start"]) >= lo
        ]
        # Same-venue same-weekend events are strong evidence on their own —
        # they're often named after the organiser on one side and the venue
        # on the other (e.g. "Pool Party Les Siestes" vs "Pool Party
        # Nakache"), where name/embedding similarity alone can miss the
        # match. Venues are compared loosely (substring or high ratio)
        # since one side may add/drop a first name, "Piscine"/"Château" etc.
        venue_matched = [
            c for c in candidates
            if venue_a and _venue_match(venue_a, _norm(c.get("venue") or ""))
        ]
        name_candidates = [
            c for c in candidates
            if c not in venue_matched
            and difflib.SequenceMatcher(
                None, name_a, _norm(c.get("event_name") or c["title"])
            ).ratio() >= TEXT_MATCH_THRESHOLD
        ]

        matches: list[tuple[dict, float]] = [(c, 1.0) for c in venue_matched]

        if name_candidates:
            emb_a = np.array(embed_mod.embed_text(ot.get("event_name") or ot["title"]), dtype=np.float32)
            emb_a /= (np.linalg.norm(emb_a) or 1.0)
            cand_embs = embed_mod.embed_batch([c.get("event_name") or c["title"] for c in name_candidates])
            for c, emb in zip(name_candidates, cand_embs):
                v = np.array(emb, dtype=np.float32)
                v /= (np.linalg.norm(v) or 1.0)
                sim = float(emb_a @ v)
                if sim >= EMBED_MATCH_THRESHOLD:
                    matches.append((c, sim))

        if matches:
            for c, _ in matches:
                consumed.add(c["cluster_id"])
            plan.append((ot, matches))

    return plan


def main() -> None:
    apply = "--apply" in sys.argv
    conn = cache_mod.open_cache(cache_mod.DEFAULT_DB_PATH)
    plan = find_merges(conn)

    if not plan:
        print("No duplicate event clusters found.")
        return

    total = 0
    for ot, matches in plan:
        print(f"\noffice_tourisme: '{ot['title'][:60]}' ({ot['cluster_id']}) "
              f"[{ot['event_start']} -> {ot.get('event_end')}]")
        for c, sim in matches:
            src = _cluster_source(conn, c["cluster_id"])
            print(f"  {'MERGE' if apply else 'WOULD MERGE'} <- [{src}] '{c['title'][:60]}' "
                  f"({c['cluster_id']}) sim={sim:.2f}")
            total += 1
        if apply:
            for c, _ in matches:
                _merge_cluster(conn, c["cluster_id"], ot["cluster_id"])
            _resynthesise(conn, ot["cluster_id"])

    print(f"\n{'Merged' if apply else 'Would merge'} {total} duplicate cluster(s) "
          f"into {len(plan)} office_tourisme cluster(s).")
    if not apply:
        print("Dry run only — rerun with --apply to actually merge.")


if __name__ == "__main__":
    main()
