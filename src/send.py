from __future__ import annotations

import requests

RESEND_BASE = "https://api.resend.com"


class SendError(RuntimeError):
    pass


def send_broadcast(
    *,
    api_key: str,
    audience_id: str,
    sender: str,
    subject: str,
    html: str,
) -> dict:
    """Create a Resend Broadcast and send it immediately. Returns the create response."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    create = requests.post(
        f"{RESEND_BASE}/broadcasts",
        headers=headers,
        json={
            "audience_id": audience_id,
            "from": sender,
            "subject": subject,
            "html": html,
        },
        timeout=30,
    )
    if not create.ok:
        raise SendError(
            f"Broadcast create failed ({create.status_code}): {create.text}"
        )
    payload = create.json()
    broadcast_id = payload.get("id")
    if not broadcast_id:
        raise SendError(f"Broadcast create returned no id: {payload}")

    send = requests.post(
        f"{RESEND_BASE}/broadcasts/{broadcast_id}/send",
        headers=headers,
        timeout=30,
    )
    if not send.ok:
        raise SendError(
            f"Broadcast send failed ({send.status_code}): {send.text}"
        )

    return payload
