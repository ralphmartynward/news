from __future__ import annotations

import sys
from pathlib import Path

from src.fetchers import actu_toulouse
from src.feed import write_atom
from src.landing import render as render_landing

FEED_OUTPUT = Path("docs/feed.xml")
LANDING_OUTPUT = Path("docs/index.html")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    items = actu_toulouse.fetch()
    print(f"actu_toulouse: {len(items)} items in last 24h")

    write_atom(items, FEED_OUTPUT)
    print(f"wrote {FEED_OUTPUT} ({FEED_OUTPUT.stat().st_size} bytes)")

    render_landing(FEED_OUTPUT, LANDING_OUTPUT)
    print(f"wrote {LANDING_OUTPUT} ({LANDING_OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
