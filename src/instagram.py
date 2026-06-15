"""Instagram image generator.

For each non-news cluster synthesised today, generates:
  - story  (1080×1920) for L'Essentiel items
  - post   (1080×1080) for everything else

Output: docs/instagram/YYYY-MM-DD/{cluster_id}_{format}.png + manifest.json
"""
from __future__ import annotations

import io
import json
from datetime import date, datetime, timezone
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
COL_PINK      = (244, 114, 182)      # #f472b6
COL_WHITE     = (255, 255, 255)
COL_DARK_TEXT = (15,  23,  42)

SITE_LABEL = "news.lavillerose.com"

FRENCH_MONTHS = ["janvier","fevrier","mars","avril","mai","juin",
                 "juillet","aout","septembre","octobre","novembre","decembre"]

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


def _french_date(iso_date: str) -> str:
    """'2026-06-20' -> '20 juin 2026'"""
    try:
        d = date.fromisoformat(iso_date)
        return f"{d.day} {FRENCH_MONTHS[d.month - 1]} {d.year}"
    except Exception:
        return ""


def _subtitle(cluster: dict[str, Any]) -> str:
    """Build the small subtitle line: date + location."""
    parts = []
    ev = _french_date(cluster.get("event_start") or "")
    if ev:
        parts.append(ev)
    parts.append("Toulouse")
    return "  ·  ".join(parts)


# ---------------------------------------------------------------------------
# Template renderers — full-bleed photo, text overlaid at bottom
# ---------------------------------------------------------------------------

def _render_post(cluster: dict[str, Any]) -> "Image":
    """1080×1080 square post. Photo fills full frame, gradient at bottom 45%."""
    from PIL import Image, ImageDraw

    W, H = POST_W, POST_H
    PAD   = 52
    TEXT_W = W - PAD * 2

    # --- background + photo ---
    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    photo = _download_image(cluster.get("image_url"))
    if photo:
        base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))

    # gradient: transparent from 40% down to solid at bottom
    base = _apply_gradient(base, int(H * 0.38), H, start_alpha=0, end_alpha=215)

    draw = ImageDraw.Draw(base)

    f_badge = _load_font(22, bold=True)
    f_title = _load_font(52, bold=True)
    f_sub   = _load_font(24)
    f_tiny  = _load_font(17)

    # --- top-right source badge ---
    source_label = SOURCE_LABELS.get(cluster.get("source", ""), cluster.get("source", ""))
    badge_bg = COL_GREEN if cluster.get("category") != "event" else COL_PINK
    bbox_b   = draw.textbbox((0, 0), source_label, font=f_badge)
    pill_w   = (bbox_b[2] - bbox_b[0]) + 16 * 2
    draw2    = draw
    _draw_pill(draw2, W - PAD - pill_w, 44, source_label, f_badge,
               bg=badge_bg, fg=COL_DARK_TEXT, pad_x=16, pad_h=8)

    # --- title (bottom area) ---
    title_lines = _wrap_text(cluster.get("title", ""), f_title, TEXT_W, draw2)[:3]
    # measure total title block height to anchor from bottom
    lh = draw2.textbbox((0, 0), title_lines[0] if title_lines else "A", font=f_title)
    line_h = lh[3] - lh[1]
    sub_h  = draw2.textbbox((0, 0), "A", font=f_sub)[3]
    site_h = draw2.textbbox((0, 0), "A", font=f_tiny)[3]
    total_h = len(title_lines) * (line_h + 10) + 18 + sub_h + 16 + site_h
    y = H - PAD - total_h

    for line in title_lines:
        draw2.text((PAD, y), line, font=f_title, fill=COL_WHITE)
        y += line_h + 10
    y += 8

    # subtitle: date · Toulouse
    sub = _subtitle(cluster)
    draw2.text((PAD, y), sub, font=f_sub, fill=(255, 255, 255, 170))
    y += sub_h + 16

    # watermark
    draw2.text((PAD, y), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 80))

    return base.convert("RGB")


def _render_story(cluster: dict[str, Any]) -> "Image":
    """1080×1920 story. Photo fills full frame, text overlaid bottom third."""
    from PIL import Image, ImageDraw

    W, H = STORY_W, STORY_H
    PAD   = 64
    TEXT_W = W - PAD * 2

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    photo = _download_image(cluster.get("image_url"))
    if photo:
        base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))

    # gradient: bottom 45%
    base = _apply_gradient(base, int(H * 0.45), H, start_alpha=0, end_alpha=225)

    draw = ImageDraw.Draw(base)

    f_badge = _load_font(24, bold=True)
    f_title = _load_font(62, bold=True)
    f_sub   = _load_font(28)
    f_tiny  = _load_font(20)

    # top-right source badge
    source_label = SOURCE_LABELS.get(cluster.get("source", ""), cluster.get("source", ""))
    badge_bg = COL_GREEN if cluster.get("category") != "event" else COL_PINK
    bbox_b   = draw.textbbox((0, 0), source_label, font=f_badge)
    pill_w   = (bbox_b[2] - bbox_b[0]) + 18 * 2
    _draw_pill(draw, W - PAD - pill_w, 56, source_label, f_badge,
               bg=badge_bg, fg=COL_DARK_TEXT, pad_x=18, pad_h=10)

    # title anchored from bottom
    title_lines = _wrap_text(cluster.get("title", ""), f_title, TEXT_W, draw)[:4]
    lh     = draw.textbbox((0, 0), title_lines[0] if title_lines else "A", font=f_title)[3]
    sub_h  = draw.textbbox((0, 0), "A", font=f_sub)[3]
    site_h = draw.textbbox((0, 0), "A", font=f_tiny)[3]
    total_h = len(title_lines) * (lh + 14) + 22 + sub_h + 20 + site_h
    y = H - PAD - total_h

    for line in title_lines:
        draw.text((PAD, y), line, font=f_title, fill=COL_WHITE)
        y += lh + 14
    y += 10

    sub = _subtitle(cluster)
    draw.text((PAD, y), sub, font=f_sub, fill=(255, 255, 255, 170))
    y += sub_h + 20

    draw.text((PAD, y), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 80))

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
        filename = f"{cid}_{fmt}.jpg"
        out_path = out_dir / filename

        try:
            if fmt == "story":
                img = _render_story(cl)
            else:
                img = _render_post(cl)
            img.save(str(out_path), "JPEG", quality=90)
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
