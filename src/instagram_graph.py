"""Instagram Graph API publisher.

Two-step flow:
  1. POST /{ig_user_id}/media  → create container (returns creation_id)
  2. POST /{ig_user_id}/media_publish → publish container

Images must be at a publicly accessible HTTPS URL.
Requires: instagram_business_basic + instagram_business_content_publish permissions.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

API_BASE = "https://graph.instagram.com/v19.0"
REQUEST_TIMEOUT_S = 30
PUBLISH_DELAY_S = 5  # wait between container creation and publish


class InstagramAPIError(RuntimeError):
    pass


def _post(endpoint: str, token: str, **data) -> dict[str, Any]:
    r = requests.post(
        f"{API_BASE}/{endpoint}",
        data={"access_token": token, **data},
        timeout=REQUEST_TIMEOUT_S,
    )
    body = r.json()
    if "error" in body:
        raise InstagramAPIError(f"{body['error'].get('message')} (code {body['error'].get('code')})")
    return body


def create_image_container(ig_user_id: str, token: str, image_url: str, caption: str) -> str:
    """Step 1: create media container. Returns creation_id."""
    result = _post(
        f"{ig_user_id}/media",
        token,
        image_url=image_url,
        caption=caption,
        media_type="IMAGE",
    )
    return result["id"]


def publish_container(ig_user_id: str, token: str, creation_id: str) -> str:
    """Step 2: publish the container. Returns the published media ID."""
    result = _post(
        f"{ig_user_id}/media_publish",
        token,
        creation_id=creation_id,
    )
    return result["id"]


def post_image(ig_user_id: str, token: str, image_url: str, caption: str) -> str:
    """Full publish flow. Returns published media ID."""
    creation_id = create_image_container(ig_user_id, token, image_url, caption)
    print(f"  ig: container created: {creation_id}")
    time.sleep(PUBLISH_DELAY_S)
    media_id = publish_container(ig_user_id, token, creation_id)
    print(f"  ig: published: {media_id}")
    return media_id


def _build_caption(cluster: dict[str, Any]) -> str:
    title   = cluster.get("title", "")
    summary = cluster.get("summary", "")
    # First sentence of summary only
    first_sentence = summary.split(". ")[0].rstrip(".") + "." if summary else ""
    lines = [title]
    if first_sentence and first_sentence != title:
        lines.append("")
        lines.append(first_sentence)
    lines += ["", "📍 Toulouse · news.lavillerose.com", "", "#toulouse #lavillerose #actutoulouse"]
    return "\n".join(lines)


def run_from_manifest(manifest_path: Path, ig_user_id: str, token: str,
                      base_url: str = "https://news.lavillerose.com") -> list[dict[str, Any]]:
    """Post all images listed in a manifest.json.

    manifest_path: path to docs/instagram/YYYY-MM-DD/manifest.json
    base_url: public root where docs/ is served
    Returns list of result dicts with cluster_id + media_id.
    """
    if not manifest_path.exists():
        print(f"instagram_graph: manifest not found at {manifest_path}")
        return []

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest:
        print("instagram_graph: manifest is empty")
        return []

    date_slug = manifest_path.parent.name
    results: list[dict[str, Any]] = []

    for entry in manifest:
        filename  = entry["file"]
        image_url = f"{base_url}/instagram/{date_slug}/{filename}"
        caption   = _build_caption(entry)

        print(f"instagram_graph: posting {filename}")
        try:
            media_id = post_image(ig_user_id, token, image_url, caption)
            results.append({"cluster_id": entry["cluster_id"], "media_id": media_id, "file": filename})
        except InstagramAPIError as e:
            print(f"  ig: FAILED {filename} — {e}")

    return results
