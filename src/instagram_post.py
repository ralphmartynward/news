"""Standalone Instagram Graph API poster.

Run AFTER the daily digest has been committed and GitHub Pages has deployed,
so that the image URLs are publicly accessible when Instagram fetches them.

Usage:
    python -m src.instagram_post
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
INSTAGRAM_DIR = Path("docs/instagram")
SITE_BASE = "https://news.lavillerose.com"


def main() -> None:
    ig_token   = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    ig_user_id = os.environ.get("IG_USER_ID", "").strip()

    if not (ig_token and ig_user_id):
        print("instagram_post: skipped (IG_ACCESS_TOKEN / IG_USER_ID not set)")
        return

    import requests
    from src.instagram_graph import (
        API_BASE,
        run_from_manifest as ig_post,
        post_weekend_carousel_from_manifest as ig_weekend,
    )

    # Resolve IG account ID via connected Facebook Page (System User approach)
    pages_r = requests.get(f"{API_BASE}/me/accounts",
                           params={"access_token": ig_token,
                                   "fields": "id,name,instagram_business_account"},
                           timeout=30).json()
    print(f"ig debug pages: {pages_r}")
    for page in pages_r.get("data", []):
        iba = page.get("instagram_business_account")
        if iba:
            resolved_id = iba["id"]
            print(f"ig: resolved Instagram account id={resolved_id} via page {page['name']}")
            if resolved_id != ig_user_id:
                print(f"ig: IG_USER_ID secret ({ig_user_id}) differs — using resolved id {resolved_id}")
                ig_user_id = resolved_id
            break

    now           = datetime.now(PARIS)
    today_slug    = now.date().isoformat()
    today_weekday = now.weekday()  # 4=Fri, 5=Sat
    ig_dir        = INSTAGRAM_DIR / today_slug

    manifest = ig_dir / "manifest.json"
    if manifest.exists():
        ig_post(manifest, ig_user_id, ig_token, base_url=SITE_BASE)
    else:
        print(f"instagram_post: no manifest at {manifest}")

    if today_weekday in (4, 5):
        weekend_manifest = ig_dir / "weekend_manifest.json"
        if weekend_manifest.exists():
            ig_weekend(weekend_manifest, ig_user_id, ig_token, base_url=SITE_BASE)
        else:
            print(f"instagram_post: no weekend manifest at {weekend_manifest}")


if __name__ == "__main__":
    main()
