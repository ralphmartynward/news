"""
export_map_events.py

Exports geocoded, dated events from the `clusters` table (data/items_seen.db)
to docs/events.json for the events map (docs/map.html).

Only exports events that have both a start date and coordinates — a cluster
without either isn't usable on a date-driven map. Excludes events that ended
more than 1 day ago to keep the export small and relevant.

Usage:
    python -m src.export_map_events
"""

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "items_seen.db"
OUT_PATH = ROOT / "docs" / "events.json"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cutoff = (date.today() - timedelta(days=1)).isoformat()
    cur.execute(
        """
        SELECT cluster_id, event_name, title, venue, lat, lon,
               event_start, event_end, image_url, primary_url, highlight
        FROM clusters
        WHERE lat IS NOT NULL AND lon IS NOT NULL
          AND event_start IS NOT NULL
          AND event_start >= ?
        ORDER BY event_start ASC
        """,
        (cutoff,),
    )

    events = []
    for r in cur.fetchall():
        name = r["event_name"] or r["title"] or "Événement"
        events.append({
            "id": r["cluster_id"],
            "name": name,
            "venue": r["venue"],
            "lat": r["lat"],
            "lon": r["lon"],
            "start": r["event_start"],
            "end": r["event_end"],
            "image": r["image_url"],
            "url": r["primary_url"],
            "summary": r["highlight"],
        })

    OUT_PATH.write_text(json.dumps(events, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {OUT_PATH} — {len(events)} events "
          f"({events[0]['start'] if events else '—'} to {events[-1]['start'] if events else '—'})")


if __name__ == "__main__":
    main()
