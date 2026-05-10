from __future__ import annotations

import os
import sys
from pathlib import Path

from src.fetchers import actu_toulouse
from src.feed import write_atom
from src.landing import render as render_landing
from src.render_email import render as render_email
from src.send import SendError, send_broadcast

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
