"""Instagram image generator — three post formats.

Format 1: Story (1080x1920) — L'Essentiel items
  Two floating dark pill boxes over full-bleed photo, CTA pill in centre.

Format 2: Weekend carousel (1080x1080 slides) — runs Friday/Saturday
  Cover slide "Que faire ce week-end a Toulouse ?" + one slide per event.

Format 3: Individual post (1080x1080) — all other non-news clusters
  Full-bleed photo, category pill centred, multi-colour title (Toulouse=pink, key noun=green).
"""
from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STORY_W, STORY_H = 1080, 1920
POST_W,  POST_H  = 1080, 1080

COL_BG        = (15,  23,  42)        # #0f172a
COL_GREEN     = (74,  222, 128)       # #4ade80
COL_PINK      = (244, 114, 182)       # #f472b6
COL_WHITE     = (255, 255, 255)
COL_DARK      = (15,  23,  42)

# Semi-transparent dark pill box background (RGBA)
COL_PILL_BG   = (15, 23, 42, 210)

SITE_LABEL = "news.lavillerose.com"

FRENCH_MONTHS = [
    "janvier","fevrier","mars","avril","mai","juin",
    "juillet","aout","septembre","octobre","novembre","decembre",
]
FRENCH_DAYS = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]

SOURCE_LABELS: dict[str, str] = {
    "lessentiel":      "L'Essentiel",
    "clutch":          "Clutch",
    "office_tourisme": "Office de Tourisme",
    "toulouscope":     "Toulouscope",
    "actu_toulouse":   "Actu Toulouse",
}

CAT_PILLS: dict[str, str] = {
    "event":   "AGENDA",
    "place":   "LIEU",
    "culture": "CULTURE",
    "news":    "INFO",
}

_FONT_BOLD_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
]
_FONT_REGULAR_PATHS = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
]

REQUEST_TIMEOUT_S = 10
_FAVICON_PATH = Path(__file__).parent.parent / "assets" / "favicon.png"
_favicon_cache: dict = {}


# ---------------------------------------------------------------------------
# Font / image helpers
# ---------------------------------------------------------------------------

def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    for p in (_FONT_BOLD_PATHS if bold else _FONT_REGULAR_PATHS):
        try:
            return ImageFont.truetype(p, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _download_image(url: str):
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
    from PIL import ImageOps
    return ImageOps.fit(img, (w, h), method=0)


def _apply_gradient(base, from_y: int, to_y: int, start_alpha: int = 0, end_alpha: int = 220):
    from PIL import Image
    if base.mode != "RGBA":
        base = base.convert("RGBA")
    w, h = base.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    height = to_y - from_y
    if height <= 0:
        return base
    pixels = overlay.load()
    for y in range(from_y, min(to_y, h)):
        t = (y - from_y) / height
        alpha = int(start_alpha + (end_alpha - start_alpha) * t)
        for x in range(w):
            pixels[x, y] = (*COL_BG, min(255, alpha))
    return Image.alpha_composite(base, overlay).convert("RGBA")


def _french_date(iso_date: str) -> str:
    try:
        d = date.fromisoformat(iso_date)
        return f"{d.day} {FRENCH_MONTHS[d.month - 1]} {d.year}"
    except Exception:
        return ""


def _space_w(draw, font) -> int:
    b = draw.textbbox((0, 0), "i i", font=font)
    a = draw.textbbox((0, 0), "ii",  font=font)
    return max(4, (b[2] - b[0]) - (a[2] - a[0]))


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _segment_title(title: str) -> list[tuple[str, tuple]]:
    """Assign brand colours to words: Toulouse=pink, first other proper noun=green."""
    words = title.split()
    result: list[tuple[str, tuple]] = []
    green_used = False
    for i, word in enumerate(words):
        clean = word.strip(".,!?:;«»\"'")
        if clean.lower() == "toulouse":
            result.append((word, COL_PINK))
        elif not green_used and i > 0 and clean and clean[0].isupper() and len(clean) > 3:
            result.append((word, COL_GREEN))
            green_used = True
        else:
            result.append((word, COL_WHITE))
    return result


def _draw_multicolor_lines(draw, x_start: int, y: int, segments, font,
                           max_width: int, line_bonus: int = 12):
    """Word-wrap and draw multi-colour segmented text. Returns (final_y, n_lines)."""
    sw = _space_w(draw, font)
    lines: list[list[tuple[str, tuple]]] = []
    cur_line: list[tuple[str, tuple]] = []
    cur_w = 0
    for word, col in segments:
        ww = draw.textbbox((0, 0), word, font=font)[2]
        needed = (sw + ww) if cur_line else ww
        if cur_w + needed > max_width and cur_line:
            lines.append(cur_line)
            cur_line, cur_w = [(word, col)], ww
        else:
            cur_line.append((word, col))
            cur_w += needed
    if cur_line:
        lines.append(cur_line)

    lh = draw.textbbox((0, 0), "Ag", font=font)[3] - draw.textbbox((0, 0), "Ag", font=font)[1]
    for line in lines:
        x = x_start
        for i, (word, col) in enumerate(line):
            draw.text((x, y), word, font=font, fill=col)
            x += draw.textbbox((0, 0), word, font=font)[2] + (sw if i < len(line) - 1 else 0)
        y += lh + line_bonus
    return y, len(lines)


def _draw_pill_box_centered(draw, W: int, top_y: int, lines: list[str], font,
                             bg=COL_PILL_BG, fg=COL_WHITE,
                             h_pad: int = 44, v_pad: int = 36, corner_r: int = 38):
    """Draw a centred rounded-rect pill box with text. Returns bottom y."""
    sw = _space_w(draw, font)
    lh = draw.textbbox((0, 0), "Ag", font=font)[3] - draw.textbbox((0, 0), "Ag", font=font)[1]
    line_gap = 10
    total_text_h = len(lines) * lh + max(0, len(lines) - 1) * line_gap
    max_lw = max((draw.textbbox((0, 0), l, font=font)[2] for l in lines), default=100)
    box_w  = min(max_lw + h_pad * 2, W - 80)
    bx1 = (W - box_w) // 2
    bx2 = bx1 + box_w
    by1 = top_y
    by2 = top_y + total_text_h + v_pad * 2
    draw.rounded_rectangle([bx1, by1, bx2, by2], radius=corner_r, fill=bg)
    y = by1 + v_pad
    for line in lines:
        lw = draw.textbbox((0, 0), line, font=font)[2]
        draw.text(((W - lw) // 2, y), line, font=font, fill=fg)
        y += lh + line_gap
    return by2


def _paste_favicon(base, x: int, y: int, size: int = 64):
    """Paste the favicon using its own alpha channel — no background, no circle mask."""
    from PIL import Image
    if size not in _favicon_cache:
        try:
            img = Image.open(str(_FAVICON_PATH)).convert("RGBA").resize((size, size), Image.LANCZOS)
            _favicon_cache[size] = img
        except Exception:
            _favicon_cache[size] = None
    img = _favicon_cache[size]
    if img:
        # Use the image's own alpha as the mask so the transparent background stays transparent
        base.paste(img, (x, y), img)


def _wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# Format 1 — Story
# ---------------------------------------------------------------------------

def _render_story(cluster: dict[str, Any]) -> "Image":
    """1080x1920. Two floating pill boxes + CTA over full-bleed photo."""
    from PIL import Image, ImageDraw

    W, H = STORY_W, STORY_H
    MAX_BOX_W = W - 100   # pill box max text area

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    photo = _download_image(cluster.get("image_url"))
    if photo:
        base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))
    # subtle full-image darken so pill boxes read clearly
    base = _apply_gradient(base, int(H * 0.62), H, start_alpha=0, end_alpha=210)

    draw = ImageDraw.Draw(base)

    # favicon top-right
    FSIZE = 68
    _paste_favicon(base, W - 52 - FSIZE, 48, size=FSIZE)

    f_title   = _load_font(52, bold=True)
    f_summary = _load_font(38)
    f_cta     = _load_font(30, bold=True)
    f_tiny    = _load_font(20)

    title   = cluster.get("title", "")
    summary = cluster.get("summary", "")
    # Up to 3 sentences for the bottom pill box
    sentences_s = [s.strip() for s in summary.replace("\n", " ").split(". ") if s.strip()]
    first_sentence = "  ".join((s.rstrip(".") + ".") for s in sentences_s[:3]) if sentences_s else ""

    # ── TOP PILL BOX (hook / title) ──
    title_lines = _wrap_text(title, f_title, MAX_BOX_W - 88, draw)[:4]
    bot_title = _draw_pill_box_centered(draw, W, 110, title_lines, f_title,
                                        h_pad=44, v_pad=38, corner_r=42)

    # ── CTA PILL (centre) ──
    cat_raw = cluster.get("category", "place")
    cta_labels = {"event": "AGENDA", "place": "À DÉCOUVRIR", "culture": "CULTURE"}
    cta_text = cta_labels.get(cat_raw, "DÉCOUVRIR")
    cta_bg   = COL_GREEN if cat_raw != "event" else COL_PINK
    cta_font = f_cta
    b = draw.textbbox((0, 0), cta_text, font=cta_font)
    cta_w = (b[2] - b[0]) + 56
    cta_h = (b[3] - b[1]) + 28
    cta_y = (H // 2) - cta_h // 2
    cta_x = (W - cta_w) // 2
    draw.rounded_rectangle([cta_x, cta_y, cta_x + cta_w, cta_y + cta_h],
                            radius=cta_h // 2, fill=cta_bg)
    draw.text((cta_x + 28, cta_y + 14), cta_text, font=cta_font, fill=COL_DARK)

    # ── BOTTOM PILL BOX (context / summary) ──
    sum_lines = _wrap_text(first_sentence, f_summary, MAX_BOX_W - 88, draw)[:4]
    _draw_pill_box_centered(draw, W, H - 110 - (len(sum_lines) * 48 + 72 + 80),
                            sum_lines, f_summary, h_pad=44, v_pad=36, corner_r=38)

    # watermark
    draw.text(((W - draw.textbbox((0, 0), SITE_LABEL, font=f_tiny)[2]) // 2,
               H - 48), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 70))

    return base.convert("RGB")


# ---------------------------------------------------------------------------
# Format 3 — Individual post
# ---------------------------------------------------------------------------

def _render_post(cluster: dict[str, Any]) -> "Image":
    """1080x1080. Full-bleed photo, category pill centred, multi-colour title."""
    from PIL import Image, ImageDraw

    W, H = POST_W, POST_H
    PAD    = 52
    TEXT_W = W - PAD * 2

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    photo = _download_image(cluster.get("image_url"))
    if photo:
        base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))
    base = _apply_gradient(base, int(H * 0.62), H, start_alpha=0, end_alpha=220)

    draw = ImageDraw.Draw(base)

    f_cat   = _load_font(24, bold=True)
    f_title = _load_font(50, bold=True)
    f_sub   = _load_font(23)
    f_tiny  = _load_font(17)

    # favicon top-right
    FSIZE = 60
    _paste_favicon(base, W - PAD - FSIZE, 40, size=FSIZE)

    title   = cluster.get("title", "")
    summary = cluster.get("summary", "")
    # First 1-2 sentences for the context line
    sentences = [s.strip() for s in summary.replace("\n", " ").split(". ") if s.strip()]
    context = (sentences[0].rstrip(".") + ".") if sentences else ""
    context2 = (sentences[1].rstrip(".") + ".") if len(sentences) > 1 else ""

    segs = _segment_title(title)
    lh   = draw.textbbox((0, 0), "Ag", font=f_title)[3]
    sub_h  = draw.textbbox((0, 0), "A", font=f_sub)[3]
    ctx_h  = draw.textbbox((0, 0), "A", font=f_sub)[3]
    site_h = draw.textbbox((0, 0), "A", font=f_tiny)[3]
    cat_pill_h = draw.textbbox((0, 0), "A", font=f_cat)[3] + 20
    title_lines = _wrap_text(title, f_title, TEXT_W, draw)[:3]
    ctx_lines   = _wrap_text(context, f_sub, TEXT_W, draw)[:2]
    ctx2_lines  = _wrap_text(context2, f_sub, TEXT_W, draw)[:1] if context2 else []
    all_ctx     = (ctx_lines + ctx2_lines)[:2]

    ev_date = _french_date(cluster.get("event_start") or "")
    date_line_h = sub_h + 10 if ev_date else 0

    total_h = (cat_pill_h + 18
               + len(title_lines) * (lh + 10) + 12
               + len(all_ctx) * (ctx_h + 6) + 10
               + date_line_h + site_h + 8)
    y = H - PAD - total_h

    # category pill (centred)
    cat_label = CAT_PILLS.get(cluster.get("category", ""), "INFO")
    cb    = draw.textbbox((0, 0), cat_label, font=f_cat)
    pill_w = (cb[2] - cb[0]) + 40
    pill_h_px = (cb[3] - cb[1]) + 20
    pill_x = (W - pill_w) // 2
    pill_bg = COL_PINK if cluster.get("category") == "event" else COL_GREEN
    draw.rounded_rectangle([pill_x, y, pill_x + pill_w, y + pill_h_px],
                            radius=pill_h_px // 2, fill=pill_bg)
    draw.text((pill_x + 20, y + 10), cat_label, font=f_cat, fill=COL_DARK)
    y += pill_h_px + 18

    # multi-colour title
    y, _ = _draw_multicolor_lines(draw, PAD, y, segs, f_title, TEXT_W, line_bonus=10)
    y += 10

    # context lines (summary sentences)
    for line in all_ctx:
        draw.text((PAD, y), line, font=f_sub, fill=(255, 255, 255, 180))
        y += ctx_h + 6
    y += 4

    # date line if event
    if ev_date:
        draw.text((PAD, y), ev_date + "  ·  Toulouse", font=f_sub, fill=(255, 255, 255, 130))
        y += sub_h + 10

    draw.text((PAD, y), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 70))

    return base.convert("RGB")


# ---------------------------------------------------------------------------
# Format 2 — Weekend carousel
# ---------------------------------------------------------------------------

def _next_weekend(today: date | None = None) -> tuple[date, date]:
    """Return (saturday, sunday) of the upcoming or current weekend."""
    if today is None:
        today = date.today()
    wd = today.weekday()  # Mon=0 … Sun=6
    days_to_sat = (5 - wd) % 7
    saturday = today + timedelta(days=days_to_sat)
    return saturday, saturday + timedelta(days=1)


def _render_weekend_cover(events: list[dict], sat: date, sun: date) -> "Image":
    """Cover slide: city backdrop + 'Que faire ce week-end a Toulouse ?'"""
    from PIL import Image, ImageDraw

    W, H = POST_W, POST_H
    PAD  = 60

    # Use the first event image as backdrop, else plain dark bg
    backdrop_url = next((e.get("image_url") for e in events if e.get("image_url")), None)
    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    if backdrop_url:
        photo = _download_image(backdrop_url)
        if photo:
            base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))
    base = _apply_gradient(base, 0,            int(H * 0.25), start_alpha=100, end_alpha=0)
    base = _apply_gradient(base, int(H * 0.55), H,           start_alpha=0,   end_alpha=215)

    draw = ImageDraw.Draw(base)

    # favicon top-right
    FSIZE = 64
    _paste_favicon(base, W - 52 - FSIZE, 44, size=FSIZE)

    f_title  = _load_font(62, bold=True)
    f_date   = _load_font(36, bold=True)
    f_tiny   = _load_font(18)

    # main title
    title_lines = ["Que faire ce", "week-end à", "Toulouse ?"]
    f_t = f_title
    lh  = draw.textbbox((0, 0), "A", font=f_t)[3]
    title_total_h = len(title_lines) * (lh + 8)
    date_h = draw.textbbox((0, 0), "A", font=f_date)[3]
    dots_h = 30
    total  = title_total_h + 20 + date_h + 32 + dots_h
    y = H - PAD - total

    for line in title_lines:
        lw = draw.textbbox((0, 0), line, font=f_t)[2]
        # "Toulouse" in pink
        if "Toulouse" in line:
            before, after = line.split("Toulouse", 1)
            # centre the whole line first
            x = (W - lw) // 2
            if before:
                draw.text((x, y), before, font=f_t, fill=COL_WHITE)
                x += draw.textbbox((0, 0), before, font=f_t)[2]
            draw.text((x, y), "Toulouse", font=f_t, fill=COL_PINK)
            x += draw.textbbox((0, 0), "Toulouse", font=f_t)[2]
            if after:
                draw.text((x, y), after, font=f_t, fill=COL_WHITE)
        else:
            draw.text(((W - lw) // 2, y), line, font=f_t, fill=COL_WHITE)
        y += lh + 8
    y += 12

    # date range in green
    sat_str = f"{sat.day} {FRENCH_MONTHS[sat.month-1]}"
    sun_str = f"{sun.day} {FRENCH_MONTHS[sun.month-1]}"
    date_str = f"{sat_str} - {sun_str}"
    dw = draw.textbbox((0, 0), date_str, font=f_date)[2]
    draw.text(((W - dw) // 2, y), date_str, font=f_date, fill=COL_GREEN)
    y += date_h + 32

    # carousel dots + arrow
    n_dots = min(len(events) + 1, 8)
    dot_r, dot_gap = 7, 20
    dots_total_w = n_dots * dot_r * 2 + (n_dots - 1) * dot_gap
    dx = (W - dots_total_w - 60) // 2
    for i in range(n_dots):
        col = COL_WHITE if i == 0 else (255, 255, 255, 90)
        cx = dx + i * (dot_r * 2 + dot_gap) + dot_r
        draw.ellipse([cx - dot_r, y, cx + dot_r, y + dot_r * 2], fill=col)
    # arrow
    arr_x = W - PAD - 40
    arr_y = y + dot_r
    draw.line([(arr_x - 30, arr_y), (arr_x, arr_y)], fill=COL_WHITE, width=3)
    draw.polygon([(arr_x, arr_y - 8), (arr_x + 12, arr_y), (arr_x, arr_y + 8)], fill=COL_WHITE)

    # watermark
    tw = draw.textbbox((0, 0), SITE_LABEL, font=f_tiny)[2]
    draw.text(((W - tw) // 2, H - 34), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 70))

    return base.convert("RGB")


def _render_weekend_event_slide(event: dict, n: int) -> "Image":
    """One event slide for the weekend carousel."""
    from PIL import Image, ImageDraw

    W, H = POST_W, POST_H
    PAD  = 52

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    photo = _download_image(event.get("image_url"))
    if photo:
        base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))
    base = _apply_gradient(base, int(H * 0.60), H, start_alpha=0, end_alpha=225)

    draw = ImageDraw.Draw(base)

    # favicon top-right
    FSIZE = 56
    _paste_favicon(base, W - PAD - FSIZE, 40, size=FSIZE)

    f_num   = _load_font(28, bold=True)
    f_title = _load_font(48, bold=True)
    f_sub   = _load_font(24)
    f_tiny  = _load_font(17)

    # number pill top-left — dark semi-transparent, white text
    num_str = str(n)
    nb  = draw.textbbox((0, 0), num_str, font=f_num)
    nw, nh = nb[2] - nb[0] + 28, nb[3] - nb[1] + 16
    draw.rounded_rectangle([PAD, 44, PAD + nw, 44 + nh], radius=nh // 2, fill=(15, 23, 42, 200))
    draw.text((PAD + 14, 44 + 8), num_str, font=f_num, fill=COL_WHITE)

    # title (multi-colour)
    segs = _segment_title(event.get("title", ""))
    lh   = draw.textbbox((0, 0), "Ag", font=f_title)[3]
    sub_h = draw.textbbox((0, 0), "A", font=f_sub)[3]
    sample = _wrap_text(event.get("title", ""), f_title, W - PAD*2, draw)[:3]
    n_lines = len(sample)
    total_h = n_lines * (lh + 10) + 14 + sub_h + 12 + 20
    y = H - PAD - total_h

    y, _ = _draw_multicolor_lines(draw, PAD, y, segs, f_title, W - PAD*2, line_bonus=10)
    y += 6

    parts = []
    ev = _french_date(event.get("event_start") or "")
    if ev:
        parts.append(ev)
    parts.append("Toulouse")
    draw.text((PAD, y), "  ·  ".join(parts), font=f_sub, fill=(255, 255, 255, 160))
    y += sub_h + 12

    draw.text((PAD, y), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 70))

    return base.convert("RGB")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def _format_for(source: str) -> str:
    return "story" if source == "lessentiel" else "post"


def run(conn, out_dir: Path) -> list[dict[str, Any]]:
    """Generate Format 1 (story) + Format 3 (post) images for today's clusters."""
    from src import cache as cache_mod

    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    clusters  = cache_mod.load_instagram_clusters(conn, today_iso)

    if not clusters:
        print("instagram: no non-news clusters for today")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []

    for cl in clusters:
        cid      = cl["cluster_id"]
        source   = cl.get("source") or "unknown"
        fmt      = _format_for(source)
        safe_cid = cid.replace(":", "_")
        filename = f"{safe_cid}_{fmt}.jpg"

        try:
            img = _render_story(cl) if fmt == "story" else _render_post(cl)
            img.save(str(out_dir / filename), "JPEG", quality=90)
            print(f"  instagram: {filename} [{cl.get('category')}] {cl['title'][:50]}")
            manifest.append({
                "cluster_id": cid, "format": fmt, "source": source,
                "category": cl.get("category"), "title": cl.get("title"),
                "image_url": cl.get("image_url"), "file": filename,
            })
        except Exception as e:
            print(f"  instagram: FAILED {cid} — {type(e).__name__}: {e}")

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"instagram: {len(manifest)} image(s) written to {out_dir}")
    return manifest


def render_weekend_carousel(conn, out_dir: Path) -> list[dict[str, Any]]:
    """Generate Format 2 (weekend carousel) slides. Run on Fridays/Saturdays."""
    from src import cache as cache_mod

    sat, sun = _next_weekend()
    events   = cache_mod.load_weekend_events(conn, sat.isoformat(), sun.isoformat())

    if not events:
        print(f"instagram weekend: no events found for {sat} - {sun}")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    slides: list[dict[str, Any]] = []

    # Cover slide
    cover = _render_weekend_cover(events, sat, sun)
    cover_file = "weekend_cover.jpg"
    cover.save(str(out_dir / cover_file), "JPEG", quality=92)
    slides.append({"file": cover_file, "type": "cover"})
    print(f"  instagram weekend: cover slide ({sat} - {sun})")

    # Event slides
    for i, ev in enumerate(events[:9], start=1):
        slide = _render_weekend_event_slide(ev, i)
        fname = f"weekend_event_{i:02d}.jpg"
        slide.save(str(out_dir / fname), "JPEG", quality=92)
        slides.append({"file": fname, "type": "event",
                        "cluster_id": ev.get("cluster_id"), "title": ev.get("title")})
        print(f"  instagram weekend: slide {i} — {ev.get('title','')[:50]}")

    manifest = {"type": "weekend_carousel", "saturday": sat.isoformat(),
                "sunday": sun.isoformat(), "slides": slides}
    (out_dir / "weekend_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"instagram weekend: {len(slides)} slides for {sat} - {sun}")
    return slides
