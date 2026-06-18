"""One-off: re-extract real images for today's L'Essentiel items.

Fetches the public newsletter archive page for today, re-runs the fixed
extractor to get real crop_thumbnail image URLs, updates items_seen.db,
regenerates Instagram story images, and posts them.

Usage:
    python -m src.fix_lessentiel_images
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS     = ZoneInfo("Europe/Paris")
DB_PATH   = Path("data/items_seen.db")
INSTAGRAM_DIR = Path("docs/instagram")
SITE_BASE = "https://news.lavillerose.com"


def _html_from_eml(eml_path: Path) -> str | None:
    import email as _email
    raw = eml_path.read_text(encoding="utf-8", errors="ignore")
    msg = _email.message_from_string(raw)
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                return payload.decode("utf-8", errors="ignore")
    return None


def _extract_image_map(html: str) -> dict[str, str]:
    """Return {article_url: image_url} from newsletter page HTML."""
    import re
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    result: dict[str, str] = {}

    anchor_re = re.compile(
        r"lessentiel\.fr/newsletter/toulouse/(\d{4}-\d{2}-\d{2})(?:%23|#)(\d+)"
    )
    numbered_re = re.compile(r"^\d+\s*[-–—\s]\s*")

    for table in soup.find_all("table", class_="tmob"):
        h1 = table.find("h1")
        if not h1 or not numbered_re.match(h1.get_text(strip=True)):
            continue

        url: str | None = None
        for a in table.find_all("a", href=True):
            m = anchor_re.search(a["href"])
            if m:
                url = f"https://www.lessentiel.fr/newsletter/toulouse/{m.group(1)}#{m.group(2)}"
                break
        if not url:
            continue

        real_img = table.find("img", src=re.compile(r"lessentiel\.fr/sites/lessentiel/files/"))
        if real_img:
            result[url] = real_img["src"]
            print(f"  found image for {url.split('#')[-1]}: {real_img['src'][:60]}")

    return result


def main() -> None:
    today = datetime.now(PARIS).date().isoformat()

    # Find the .eml file in the repo root
    eml_files = sorted(Path(".").glob("lessentiel*.eml"))
    if not eml_files:
        print("fix_lessentiel: no lessentiel*.eml file found in repo root — aborting")
        return
    eml_path = eml_files[-1]  # most recent if multiple
    print(f"fix_lessentiel: reading {eml_path}")

    html = _html_from_eml(eml_path)
    if not html:
        print("fix_lessentiel: no HTML part found in .eml — aborting")
        return

    image_map = _extract_image_map(html)
    if not image_map:
        print("fix_lessentiel: no images found in newsletter page")
        return

    print(f"fix_lessentiel: found {len(image_map)} image(s)")

    # Update DB
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    updated = 0
    for article_url, img_url in image_map.items():
        cur = conn.execute(
            "UPDATE items SET image_url = ? WHERE url = ?",
            (img_url, article_url),
        )
        if cur.rowcount:
            updated += 1
            print(f"  updated: {article_url.split('#')[-1]} → {img_url[:60]}")
    conn.commit()
    print(f"fix_lessentiel: updated {updated} item(s) in DB")

    if not updated:
        print("fix_lessentiel: no matching items in DB — nothing to regenerate")
        conn.close()
        return

    # Regenerate Instagram story images for today's clusters
    from src import cache as cache_mod
    from src.instagram import run as instagram_run

    ig_dir = INSTAGRAM_DIR / today
    print(f"fix_lessentiel: regenerating Instagram images in {ig_dir}")
    instagram_run(conn, ig_dir)
    conn.close()

    # Post via Graph API
    ig_token   = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    ig_user_id = os.environ.get("IG_USER_ID", "").strip()
    if not (ig_token and ig_user_id):
        print("fix_lessentiel: IG credentials not set — skipping post")
        return

    import requests as _req
    from src.instagram_graph import API_BASE, run_from_manifest as ig_post

    pages = _req.get(
        f"{API_BASE}/me/accounts",
        params={"access_token": ig_token, "fields": "id,name,instagram_business_account"},
        timeout=30,
    ).json()
    for page in pages.get("data", []):
        iba = page.get("instagram_business_account")
        if iba:
            resolved = iba["id"]
            if resolved != ig_user_id:
                print(f"fix_lessentiel: resolved IG id={resolved}")
                ig_user_id = resolved
            break

    manifest = ig_dir / "manifest.json"
    if manifest.exists():
        ig_post(manifest, ig_user_id, ig_token, base_url=SITE_BASE)
    else:
        print(f"fix_lessentiel: no manifest at {manifest}")


if __name__ == "__main__":
    main()
