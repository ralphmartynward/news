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

API_BASE = "https://graph.facebook.com/v25.0"
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
    """Full publish flow for a feed post. Returns published media ID."""
    creation_id = create_image_container(ig_user_id, token, image_url, caption)
    print(f"  ig: container created: {creation_id}")
    time.sleep(PUBLISH_DELAY_S)
    media_id = publish_container(ig_user_id, token, creation_id)
    print(f"  ig: published: {media_id}")
    return media_id


def post_story(ig_user_id: str, token: str, image_url: str) -> str:
    """Publish a 1080x1920 image as an Instagram Story. Captions not supported."""
    result = _post(
        f"{ig_user_id}/media",
        token,
        image_url=image_url,
        media_type="STORIES",
    )
    creation_id = result["id"]
    print(f"  ig story: container created: {creation_id}")
    time.sleep(PUBLISH_DELAY_S)
    media_id = publish_container(ig_user_id, token, creation_id)
    print(f"  ig story: published: {media_id}")
    return media_id


def post_carousel(ig_user_id: str, token: str, image_urls: list[str], caption: str) -> str:
    """Publish a carousel post. Returns published media ID."""
    item_ids: list[str] = []
    for url in image_urls:
        result = _post(f"{ig_user_id}/media", token, image_url=url, is_carousel_item="true")
        item_ids.append(result["id"])
        print(f"  ig carousel: item container {result['id']}")
        time.sleep(2)

    carousel = _post(f"{ig_user_id}/media", token,
                     media_type="CAROUSEL",
                     children=",".join(item_ids),
                     caption=caption)
    print(f"  ig carousel: container {carousel['id']}")
    time.sleep(PUBLISH_DELAY_S)
    media_id = publish_container(ig_user_id, token, carousel["id"])
    print(f"  ig carousel: published {media_id}")
    return media_id


def _build_weekend_caption(events: list[dict[str, Any]], sat_str: str, sun_str: str) -> str:
    lines = [f"Que faire ce week-end a Toulouse ? ({sat_str} - {sun_str})", ""]
    for i, ev in enumerate(events[:9], 1):
        lines.append(f"{i}. {ev.get('title', '')}")
    lines += ["", "Tous les details sur news.lavillerose.com",
              "", "#toulouse #lavillerose #toulouse2026 #week-end #sortiraToulouse"]
    return "\n".join(lines)


def _build_caption(cluster: dict[str, Any]) -> str:
    import json as _json
    title      = cluster.get("title", "")
    ig_caption = cluster.get("ig_caption") or ""
    if not ig_caption:
        summary = cluster.get("summary", "")
        ig_caption = (summary.split(". ")[0].rstrip(".") + ".") if summary else ""

    # Hashtags: use Claude-generated ones, fall back to generic
    raw = cluster.get("ig_hashtags")
    try:
        tags = _json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        tags = []
    if not tags:
        tags = ["toulouse", "lavillerose", "actutoulouse"]
    hashtag_str = " ".join(f"#{t.lstrip('#')}" for t in tags[:8])

    lines = [title]
    if ig_caption and ig_caption != title:
        lines += ["", ig_caption]
    lines += ["", "📍 Toulouse · news.lavillerose.com", "", hashtag_str]
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
        fmt       = entry.get("format", "post")

        print(f"instagram_graph: posting {filename} [{fmt}]")
        try:
            if fmt == "story":
                media_id = post_story(ig_user_id, token, image_url)
            else:
                media_id = post_image(ig_user_id, token, image_url, _build_caption(entry))
            results.append({"cluster_id": entry.get("cluster_id"), "media_id": media_id, "file": filename})
        except InstagramAPIError as e:
            print(f"  ig: FAILED {filename} — {e}")

    return results


def post_weekend_carousel_from_manifest(manifest_path: Path, ig_user_id: str, token: str,
                                        base_url: str = "https://news.lavillerose.com") -> str | None:
    """Post the weekend carousel from a weekend_manifest.json."""
    if not manifest_path.exists():
        print(f"instagram_graph: weekend manifest not found at {manifest_path}")
        return None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    date_slug = manifest_path.parent.name
    slides    = manifest.get("slides", [])
    sat_str   = manifest.get("saturday", "")
    sun_str   = manifest.get("sunday",   "")

    image_urls = [f"{base_url}/instagram/{date_slug}/{s['file']}" for s in slides]
    events     = [s for s in slides if s.get("type") == "event"]

    from datetime import date as _date
    try:
        sat = _date.fromisoformat(sat_str)
        sun = _date.fromisoformat(sun_str)
        from src.instagram import FRENCH_MONTHS
        sat_label = f"{sat.day} {FRENCH_MONTHS[sat.month-1]}"
        sun_label = f"{sun.day} {FRENCH_MONTHS[sun.month-1]}"
    except Exception:
        sat_label, sun_label = sat_str, sun_str

    caption = _build_weekend_caption(events, sat_label, sun_label)
    print(f"instagram_graph: posting weekend carousel ({len(image_urls)} slides)")
    try:
        return post_carousel(ig_user_id, token, image_urls, caption)
    except InstagramAPIError as e:
        print(f"  ig carousel: FAILED — {e}")
        return None
