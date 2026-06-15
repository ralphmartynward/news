"""Instagram image generator.

For each non-news cluster synthesised today, generates:
  - story  (1080×1920) for L'Essentiel items
  - post   (1080×1080) for everything else

Output: docs/instagram/YYYY-MM-DD/{cluster_id}_{format}.png + manifest.json
"""
from __future__ import annotations

import io
import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STORY_W, STORY_H = 1080, 1920
POST_W, POST_H   = 1080, 1080

COL_BG        = (15,  23,  42)       # #0f172a
COL_GREEN     = (74, 222, 128)       # #4ade80
COL_WHITE     = (255, 255, 255)
COL_MUTED     = (255, 255, 255, 150) # semi-transparent white
COL_DARK_TEXT = (15,  23,  42)
COL_OVERLAY   = (15,  23,  42, 200)

SITE_LABEL = "news.lavillerose.com"

SOURCE_LABELS: dict[str, str] = {
    "lessentiel":     "L'Essentiel",
    "clutch":         "Clutch",
    "office_tourisme":"Office de Tourisme",
    "toulouscope":    "Toulouscope",
    "actu_toulouse":  "Actu Toulouse",
}

CATEGORY_LABELS: dict[str, str] = {
    "event":   "Agenda",
    "place":   "À découvrir",
    "culture": "Culture & Patrimoine",
}

_FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]
_FONT_REGULAR_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

REQUEST_TIMEOUT_S = 10


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    paths = _FONT_BOLD_PATHS if bold else _FONT_REGULAR_PATHS
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _download_image(url: str):
    """Download URL and return PIL Image, or None on failure."""
    from PIL import Image
    if not url:
        return None
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT_S, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        return None


def _cover_crop(img, w: int, h: int):
    """Resize and center-crop img to exactly w×h."""
    from PIL import ImageOps
    return ImageOps.fit(img, (w, h), method=0)


def _apply_gradient(base, from_y: int, to_y: int, start_alpha: int = 0, end_alpha: int = 220):
    """Apply a vertical dark gradient overlay between from_y and to_y."""
    from PIL import Image
    if base.mode != "RGBA":
        base = base.convert("RGBA")
    w, h = base.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    height = to_y - from_y
    if height <= 0:
        return base
    import struct as _struct
    # Build gradient pixel row by row
    pixels = overlay.load()
    for y in range(from_y, min(to_y, h)):
        t = (y - from_y) / height
        alpha = int(start_alpha + (end_alpha - start_alpha) * t)
        for x in range(w):
            pixels[x, y] = (*COL_BG, min(255, alpha))
    return Image.alpha_composite(base, overlay).convert("RGBA")


def _draw_pill(draw, x: int, y: int, text: str, font, bg=COL_GREEN, fg=COL_DARK_TEXT, pad_x=20, pad_h=10):
    """Draw a rounded pill badge. Returns the right edge x."""
    from PIL import ImageDraw
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    rx1, ry1 = x, y
    rx2, ry2 = x + tw + pad_x * 2, y + th + pad_h * 2
    r = (ry2 - ry1) // 2
    draw.rounded_rectangle([rx1, ry1, rx2, ry2], radius=r, fill=bg)
    draw.text((rx1 + pad_x, ry1 + pad_h), text, font=font, fill=fg)
    return rx2


def _wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    """Word-wrap text to fit max_width. Returns list of lines."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _text_block_height(lines: list[str], font, line_spacing: int = 8, draw=None) -> int:
    if not lines:
        return 0
    sample = draw.textbbox((0, 0), lines[0], font=font) if draw else (0, 0, 0, 30)
    lh = sample[3] - sample[1]
    return len(lines) * lh + (len(lines) - 1) * line_spacing


# ---------------------------------------------------------------------------
# Template renderers
# ---------------------------------------------------------------------------

def _render_story(cluster: dict[str, Any]) -> "Image":
    """1080×1920 story image."""
    from PIL import Image, ImageDraw

    W, H = STORY_W, STORY_H
    TEXT_X = 64
    TEXT_W = W - TEXT_X * 2

    # Base layer
    base = Image.new("RGB", (W, H), COL_BG)

    # Article image fills top square (1080×1080)
    photo = _download_image(cluster.get("image_url"))
    if photo:
        photo = _cover_crop(photo, W, W)
        base.paste(photo, (0, 0))
        base = base.convert("RGBA")
        base = _apply_gradient(base, W // 2, W, start_alpha=0, end_alpha=240)
        base = _apply_gradient(base, W, H, start_alpha=240, end_alpha=240)
    else:
        base = base.convert("RGBA")

    draw = ImageDraw.Draw(base)

    # Fonts
    f_source  = _load_font(26, bold=True)
    f_title   = _load_font(54, bold=True)
    f_summary = _load_font(30)
    f_tiny    = _load_font(22)

    y = W + 60  # start below image

    # Source + category badges
    source_label = SOURCE_LABELS.get(cluster.get("source", ""), cluster.get("source", ""))
    cat_label    = CATEGORY_LABELS.get(cluster.get("category", ""), "")
    rx = _draw_pill(draw, TEXT_X, y, source_label, f_source, bg=COL_GREEN, fg=COL_DARK_TEXT)
    if cat_label:
        _draw_pill(draw, rx + 12, y, cat_label, f_source,
                   bg=(255, 255, 255, 30), fg=(255, 255, 255, 200))
    y += 66

    # Title
    title = cluster.get("title", "")
    title_lines = _wrap_text(title, f_title, TEXT_W, draw)[:4]
    for line in title_lines:
        draw.text((TEXT_X, y), line, font=f_title, fill=COL_WHITE)
        bbox = draw.textbbox((TEXT_X, y), line, font=f_title)
        y += (bbox[3] - bbox[1]) + 12
    y += 24

    # Summary (2 lines max)
    summary = cluster.get("summary", "")[:300]
    sum_lines = _wrap_text(summary, f_summary, TEXT_W, draw)[:3]
    for line in sum_lines:
        draw.text((TEXT_X, y), line, font=f_summary, fill=(255, 255, 255, 170))
        bbox = draw.textbbox((TEXT_X, y), line, font=f_summary)
        y += (bbox[3] - bbox[1]) + 8

    # Footer
    footer_y = H - 72
    draw.line([(TEXT_X, footer_y - 20), (W - TEXT_X, footer_y - 20)], fill=(255, 255, 255, 40), width=1)
    draw.text((TEXT_X, footer_y), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 100))
    date_str = datetime.now(timezone.utc).strftime("%-d %b %Y") if hasattr(datetime, "strftime") else ""
    try:
        date_str = datetime.now(timezone.utc).strftime("%-d %b %Y")
    except Exception:
        date_str = datetime.now(timezone.utc).strftime("%d %b %Y").lstrip("0")
    draw.text((W - TEXT_X, footer_y), date_str, font=f_tiny, fill=(255, 255, 255, 100), anchor="ra")

    return base.convert("RGB")


def _render_post(cluster: dict[str, Any]) -> "Image":
    """1080×1080 square post image."""
    from PIL import Image, ImageDraw

    W, H = POST_W, POST_H
    IMAGE_H = 520  # top photo area
    TEXT_X = 56
    TEXT_W = W - TEXT_X * 2

    base = Image.new("RGB", (W, H), COL_BG)

    photo = _download_image(cluster.get("image_url"))
    if photo:
        photo = _cover_crop(photo, W, IMAGE_H)
        base.paste(photo, (0, 0))
        base = base.convert("RGBA")
        base = _apply_gradient(base, IMAGE_H // 2, IMAGE_H + 40, start_alpha=0, end_alpha=230)
        base = _apply_gradient(base, IMAGE_H + 40, H, start_alpha=230, end_alpha=230)
    else:
        base = base.convert("RGBA")

    draw = ImageDraw.Draw(base)

    f_source  = _load_font(24, bold=True)
    f_title   = _load_font(46, bold=True)
    f_summary = _load_font(26)
    f_tiny    = _load_font(20)

    y = IMAGE_H + 36

    source_label = SOURCE_LABELS.get(cluster.get("source", ""), cluster.get("source", ""))
    cat_label    = CATEGORY_LABELS.get(cluster.get("category", ""), "")
    rx = _draw_pill(draw, TEXT_X, y, source_label, f_source, bg=COL_GREEN, fg=COL_DARK_TEXT)
    if cat_label:
        _draw_pill(draw, rx + 12, y, cat_label, f_source,
                   bg=(255, 255, 255, 30), fg=(255, 255, 255, 200))
    y += 58

    title = cluster.get("title", "")
    title_lines = _wrap_text(title, f_title, TEXT_W, draw)[:3]
    for line in title_lines:
        draw.text((TEXT_X, y), line, font=f_title, fill=COL_WHITE)
        bbox = draw.textbbox((TEXT_X, y), line, font=f_title)
        y += (bbox[3] - bbox[1]) + 10
    y += 20

    summary = cluster.get("summary", "")[:200]
    sum_lines = _wrap_text(summary, f_summary, TEXT_W, draw)[:2]
    for line in sum_lines:
        draw.text((TEXT_X, y), line, font=f_summary, fill=(255, 255, 255, 160))
        bbox = draw.textbbox((TEXT_X, y), line, font=f_summary)
        y += (bbox[3] - bbox[1]) + 6

    footer_y = H - 52
    draw.line([(TEXT_X, footer_y - 16), (W - TEXT_X, footer_y - 16)], fill=(255, 255, 255, 40), width=1)
    draw.text((TEXT_X, footer_y), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 100))
    try:
        date_str = datetime.now(timezone.utc).strftime("%-d %b %Y")
    except Exception:
        date_str = datetime.now(timezone.utc).strftime("%d %b %Y").lstrip("0")
    draw.text((W - TEXT_X, footer_y), date_str, font=f_tiny, fill=(255, 255, 255, 100), anchor="ra")

    return base.convert("RGB")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def _format_for(source: str) -> str:
    return "story" if source == "lessentiel" else "post"


def run(conn, out_dir: Path) -> list[dict[str, Any]]:
    """Generate Instagram images for today's non-news clusters.

    Returns a list of manifest entries (one per image generated).
    """
    from src import cache as cache_mod

    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    clusters = cache_mod.load_instagram_clusters(conn, today_iso)

    if not clusters:
        print("instagram: no non-news clusters for today")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []

    for cl in clusters:
        cid     = cl["cluster_id"]
        source  = cl.get("source") or "unknown"
        fmt     = _format_for(source)
        filename = f"{cid}_{fmt}.png"
        out_path = out_dir / filename

        try:
            if fmt == "story":
                img = _render_story(cl)
            else:
                img = _render_post(cl)
            img.save(str(out_path), "PNG", optimize=True)
            print(f"  instagram: {filename} [{cl.get('category')}] {cl['title'][:50]}")
            manifest.append({
                "cluster_id": cid,
                "format": fmt,
                "source": source,
                "category": cl.get("category"),
                "title": cl.get("title"),
                "image_url": cl.get("image_url"),
                "file": filename,
            })
        except Exception as e:
            print(f"  instagram: FAILED {cid} — {type(e).__name__}: {e}")

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"instagram: {len(manifest)} image(s) written to {out_dir}")
    return manifest
