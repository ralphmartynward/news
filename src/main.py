from __future__ import annotations

import os
import sys
from pathlib import Path

from src import cache as cache_mod
from src.fetchers import actu_toulouse
from src.feed import write_atom
from src.landing import render as render_landing
from src.render_email import render as render_email
from src.send import SendError, send_broadcast

FEED_OUTPUT = Path("docs/feed.xml")
LANDING_OUTPUT = Path("docs/index.html")
CACHE_PATH = Path("data/items_seen.db")


def _embed_text_for_item(item: dict) -> str:
    title = item.get("title", "")
    body = item.get("extracted_text", "") or ""
    return f"{title}\n\n{body}".strip()


def _cluster_today(items: list[dict]) -> tuple[list[dict], int, int]:
    """Embed today's items, dedup against the rolling cache, return kept items.
    Returns (kept_items, skipped_count, new_cluster_count)."""
    from src import cluster as cluster_mod
    from src import embed as embed_mod

    conn = cache_mod.open_cache(CACHE_PATH)
    pruned = cache_mod.prune(conn)
    if pruned:
        print(f"cache: pruned {pruned} items older than 7 days")

    cached = cache_mod.load_recent(conn)
    print(f"cache: {len(cached)} items in 7-day window")

    embeddings = embed_mod.embed_batch([_embed_text_for_item(i) for i in items])
    kept = cluster_mod.assign_clusters(items, embeddings, cached)

    skipped = len(items) - len(kept)
    cached_cluster_ids = {c["cluster_id"] for c in cached}
    new_clusters = sum(1 for k in kept if k["cluster_id"] not in cached_cluster_ids)

    cache_mod.upsert(conn, kept)
    cache_mod.mark_shown(conn, [k["url"] for k in kept])
    cache_mod.vacuum(conn)
    conn.close()

    return kept, skipped, new_clusters


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    items = actu_toulouse.fetch()
    print(f"actu_toulouse: {len(items)} items in last 24h")

    if os.environ.get("OPENAI_API_KEY", "").strip():
        items, skipped, new_clusters = _cluster_today(items)
        print(f"cluster: kept {len(items)} ({skipped} skipped as near-duplicates, {new_clusters} new clusters)")
    else:
        print("cluster: skipped (OPENAI_API_KEY not set) — no dedup this run")

    write_atom(items, FEED_OUTPUT)
    print(f"wrote {FEED_OUTPUT} ({FEED_OUTPUT.stat().st_size} bytes)")

    render_landing(FEED_OUTPUT, LANDING_OUTPUT)
    print(f"wrote {LANDING_OUTPUT} ({LANDING_OUTPUT.stat().st_size} bytes)")

    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    audience_id = os.environ.get("RESEND_AUDIENCE_ID", "").strip()
    sender = os.environ.get("EMAIL_FROM_ADDRESS", "").strip()

    if not (api_key and audience_id and sender):
        print("email send: skipped (RESEND_API_KEY / RESEND_AUDIENCE_ID / EMAIL_FROM_ADDRESS not all set)")
        return

    if not items:
        print("email send: skipped (no items today)")
        return

    subject, html = render_email(FEED_OUTPUT)
    try:
        result = send_broadcast(
            api_key=api_key,
            audience_id=audience_id,
            sender=sender,
            subject=subject,
            html=html,
        )
        print(f"email send: broadcast {result.get('id')} dispatched · subject: {subject!r}")
    except SendError as e:
        print(f"email send: FAILED — {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
