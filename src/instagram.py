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
import re
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
_ASSETS_DIR = Path(__file__).parent.parent / "assets"
_favicon_cache: dict = {}

_SEASON_BY_MONTH = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
}


def _story_background_path(today: date, weather_code: int | None) -> Path | None:
    """Pick the season/weather-matched backdrop, falling back sensibly.

    Only "winter" has a "snowy" variant; every other season falls back to
    "cloudy" if the weather bucket for that season doesn't exist as a file.
    """
    from src.weather import weather_bucket

    season = _SEASON_BY_MONTH[today.month]
    bucket = weather_bucket(weather_code) if weather_code is not None else "sunny"

    for candidate in (bucket, "cloudy", "sunny"):
        for ext in ("jpg", "png"):
            p = _ASSETS_DIR / f"toulouse_1080x1920_{season}_{candidate}.{ext}"
            if p.exists():
                return p
    return None


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
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        if img.width < 100 or img.height < 100:  # tracking pixel / placeholder
            return None
        return img
    except Exception:
        return None


def _cover_crop(img, w: int, h: int):
    from PIL import Image, ImageOps
    return ImageOps.fit(img, (w, h), method=Image.Resampling.LANCZOS)


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


def _french_date_range(start_iso: str, end_iso: str | None) -> str:
    """Return a human-readable date range in French.

    Single day  → '23 juin 2026'
    Same month  → 'du 17 au 21 juin 2026'
    Cross-month → 'du 17 juin au 19 juillet 2026'
    Open-ended  → 'jusqu'au 20 août 2026'
    Already started, ongoing → 'jusqu'au 20 août 2026'
    """
    try:
        s = date.fromisoformat(start_iso[:10])
    except Exception:
        return ""
    try:
        e = date.fromisoformat(end_iso[:10]) if end_iso else s
    except Exception:
        e = s

    today = date.today()
    sm, em = FRENCH_MONTHS[s.month - 1], FRENCH_MONTHS[e.month - 1]

    if s == e:
        return f"{s.day} {sm} {s.year}"
    if s <= today:
        # Event already started or starts today — show end only.
        # Avoids "du 7 au 12" when the event began days ago but event_start
        # was set to today via the published_at fallback.
        return f"jusqu'au {e.day} {em} {e.year}"
    if s.month == e.month and s.year == e.year:
        return f"du {s.day} au {e.day} {em} {s.year}"
    return f"du {s.day} {sm} au {e.day} {em} {e.year}"


def _space_w(draw, font) -> int:
    b = draw.textbbox((0, 0), "i i", font=font)
    a = draw.textbbox((0, 0), "ii",  font=font)
    return max(4, (b[2] - b[0]) - (a[2] - a[0]))


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_VENUE_RE = re.compile(
    # Venue type keyword (case-insensitive via inline flag)
    r"(?i:jardins?|parcs?|stades?|stadiums?|salles?|allées?|allees?|"
    r"rues?|places?|esplanade|halles?|palais|lacs?|bastide|hangar|espace|"
    r"couloir|avenue|boulevard|campus|quartier|médiathèques?|mediatheques?|"
    r"bibliothèques?|maisons?|musées?|cinémas?|théâtres?|chapelles?)"
    # Optional article/preposition before proper name
    r"(?:\s+(?i:de\s+la|de\s+l\w*|d'\w*|du|de)\s*)?"
    # Proper name: capitalized words with optional hyphens (Saint-Pierre, Lacroix-Falgarde)
    r"\s*[A-ZÀÂÉÈÊËÎÏÔÙÛÇ][a-zàâéèêëîïôùûç]+(?:-[A-ZÀ-ÿa-zà-ÿ][a-zàâéèêëîïôùûç]*)?"
    r"(?:\s+(?:[A-ZÀÂÉÈÊËÎÏÔÙÛÇ][a-zàâéèêëîïôùûç]+(?:-[A-Za-zÀ-ÿ][a-zàâéèêëîïôùûç]*)?|de|du|la|le|les|l'))*",
    re.UNICODE,
)

# Locative prepositions — case-sensitive so 'dans le ciel' (lowercase) doesn't match.
# 'dans la/le' removed: too ambiguous ('dans le ciel', 'dans la ville').
# Stop-word lookahead trims trailing 'pour les fans', 'avec entrée libre', etc.
_CAPTION_VENUE_RE = re.compile(
    r"\b(?:au|chez|à\s+la|à\s+l[''e]\s*)\s+"
    r"([A-ZÀÂÉÈÊËÎÏÔÙÛÇ][^,\.\n!?]*?)(?=\s+(?:pour|avec|lors|où|dont|afin|qui\s+[a-z])|[,\.\n!?]|$)",
    re.MULTILINE | re.UNICODE,
)


def _extract_venue(summary: str) -> str:
    """Return the first venue/location phrase from the summary (regex fallback)."""
    m = _VENUE_RE.search(summary[:300])
    if not m:
        return ""
    return m.group(0).strip()[:55]


def _venue_from_caption(ig_caption: str) -> str:
    """Extract venue from the AI-generated ig_caption.

    Priority:
    1. Locative preposition + uppercase proper name ('au Stadium', 'à la Halle aux Grains')
    2. Venue-type keyword anywhere in the caption ('médiathèque d'Empalot', 'place Saint-Pierre')
    """
    for line in (ig_caption or "").split("\n"):
        m = _CAPTION_VENUE_RE.search(line.strip())
        if m:
            return m.group(1).strip()[:55]
    # Try venue-keyword regex on the full caption text
    return _extract_venue(ig_caption or "")

def _segment_title(title: str, event_name: str | None = None) -> list[tuple[str, tuple]]:
    """Assign colours to words.

    With event_name: words that appear in the event name → pink, rest → white.
    Without: Toulouse → pink, first other proper noun → green.
    """
    words = title.split()
    result: list[tuple[str, tuple]] = []
    if event_name:
        event_words = {w.lower().strip(".,!?:;«»\"'") for w in event_name.split() if len(w) > 2}
        for word in words:
            clean = word.lower().strip(".,!?:;«»\"'")
            result.append((word, COL_PINK if clean in event_words else COL_WHITE))
    else:
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
    """1080x1920. Gradient bottom-band + white text — same visual language as _render_post."""
    from PIL import Image, ImageDraw

    W, H = STORY_W, STORY_H
    PAD    = 60
    TEXT_W = W - PAD * 2

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    photo = _download_image(cluster.get("image_url"))
    if photo:
        base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))
    base = _apply_gradient(base, int(H * 0.42), H, start_alpha=0, end_alpha=255)

    draw = ImageDraw.Draw(base)

    # favicon top-right
    FSIZE = 68
    _paste_favicon(base, W - 52 - FSIZE, 52, size=FSIZE)

    f_cat      = _load_font(28, bold=True)
    f_headline = _load_font(64, bold=True)   # event_name — short & punchy
    f_sub      = _load_font(32)              # ig_caption lines
    f_meta     = _load_font(26)              # venue · date
    f_tiny     = _load_font(22)

    title      = cluster.get("title", "")
    event_name = cluster.get("event_name") or None
    ig_caption = cluster.get("ig_caption") or ""
    venue      = (cluster.get("venue") or "").strip() or _venue_from_caption(cluster.get("ig_caption") or "") or _extract_venue(cluster.get("summary") or "")

    # Headline: event_name if available, otherwise full title
    headline    = event_name if event_name else title
    headline_segs = [(w, COL_PINK) for w in headline.split()] if event_name else \
                    _segment_title(title)

    # Description: ig_caption split into lines (Claude generates \n-separated lines)
    caption_lines_raw = [l.strip() for l in ig_caption.split("\n") if l.strip()][:3]

    lh_h    = draw.textbbox((0, 0), "Ag", font=f_headline)[3]
    lh_s    = draw.textbbox((0, 0), "Ag", font=f_sub)[3]
    lh_m    = draw.textbbox((0, 0), "A", font=f_meta)[3]
    cat_h   = draw.textbbox((0, 0), "A", font=f_cat)[3] + 20
    site_h  = draw.textbbox((0, 0), "A", font=f_tiny)[3]

    headline_lines = _wrap_text(headline, f_headline, TEXT_W, draw)[:3]
    caption_lines  = []
    for raw in caption_lines_raw:
        caption_lines += _wrap_text(raw, f_sub, TEXT_W, draw)[:1]
    caption_lines = caption_lines[:3]

    event_date_str = _french_date_range(
        cluster.get("event_start") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        cluster.get("event_end"),
    )
    meta_parts = []
    if venue:
        meta_parts.append(venue)
    meta_parts.append(event_date_str + "  ·  Toulouse" if event_date_str else "Toulouse")
    meta_str = "  ·  ".join(meta_parts) if venue else (event_date_str + "  ·  Toulouse" if event_date_str else "Toulouse")

    total_h = (cat_h + 14
               + len(headline_lines) * (lh_h + 8) + 10
               + len(caption_lines) * (lh_s + 6) + (8 if caption_lines else 0)
               + lh_m + 10
               + site_h + 8)
    y = H - PAD - total_h

    # category pill (centred)
    cat_raw   = cluster.get("category", "place")
    cat_label = CAT_PILLS.get(cat_raw, "INFO")
    cb        = draw.textbbox((0, 0), cat_label, font=f_cat)
    pill_w    = (cb[2] - cb[0]) + 44
    pill_h_px = (cb[3] - cb[1]) + 20
    pill_x    = (W - pill_w) // 2
    pill_bg   = COL_PINK if cat_raw == "event" else COL_GREEN
    draw.rounded_rectangle([pill_x, y, pill_x + pill_w, y + pill_h_px],
                            radius=pill_h_px // 2, fill=pill_bg)
    draw.text((pill_x + 22, y + 10), cat_label, font=f_cat, fill=COL_DARK)
    y += pill_h_px + 14

    # headline (event_name in pink, or full title with colour logic)
    y, _ = _draw_multicolor_lines(draw, PAD, y, headline_segs, f_headline, TEXT_W, line_bonus=8)
    y += 10

    # description lines from ig_caption
    for line in caption_lines:
        draw.text((PAD, y), line, font=f_sub, fill=(255, 255, 255, 210))
        y += lh_s + 6
    if caption_lines:
        y += 8

    # venue · date in green
    draw.text((PAD, y), meta_str, font=f_meta, fill=COL_GREEN)
    y += lh_m + 10

    draw.text((PAD, y), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 70))

    return base.convert("RGB")


# ---------------------------------------------------------------------------
# Format 3 — Individual post
# ---------------------------------------------------------------------------

def _render_post(cluster: dict[str, Any]) -> "Image":
    """1080x1080. Full-bleed photo, category pill centred, multi-colour title."""
    from PIL import Image, ImageDraw

    W, H = POST_W, POST_H
    PAD    = 52
    PAD_BOTTOM = 120   # extra clearance for Instagram's action bar overlay
    TEXT_W = W - PAD * 2

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    photo = _download_image(cluster.get("image_url"))
    if photo:
        base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))
    base = _apply_gradient(base, int(H * 0.38), H, start_alpha=0, end_alpha=255)

    draw = ImageDraw.Draw(base)

    f_cat   = _load_font(24, bold=True)
    f_title = _load_font(50, bold=True)
    f_sub   = _load_font(23)
    f_tiny  = _load_font(17)

    # favicon top-right
    FSIZE = 60
    _paste_favicon(base, W - PAD - FSIZE, 40, size=FSIZE)

    title      = cluster.get("title", "")
    event_name = cluster.get("event_name") or None
    # Use Claude-generated ig_caption if available; fall back to truncated summary
    ig_caption = cluster.get("ig_caption") or ""
    if not ig_caption:
        summary   = cluster.get("summary", "")
        sentences = [s.strip() for s in summary.replace("\n", " ").split(". ") if s.strip()]
        _c0 = (sentences[0].rstrip(".") + ".") if sentences else ""
        ig_caption = (_c0[:92] + "…") if len(_c0) > 95 else _c0
    context  = ig_caption
    context2 = ""

    segs = _segment_title(title, event_name=event_name)
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
    y = H - PAD_BOTTOM - total_h

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

    # date line in green if event
    if ev_date:
        draw.text((PAD, y), ev_date + "  ·  Toulouse", font=f_sub, fill=COL_GREEN)
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

    f_title   = _load_font(62, bold=True)
    f_date    = _load_font(36, bold=True)
    f_weather = _load_font(30, bold=True)
    f_tiny    = _load_font(18)

    # Fetch weekend weather (fail silently)
    sat_weather = sun_weather = ""
    try:
        from src.weather import weekend_lines
        sat_weather, sun_weather = weekend_lines()
    except Exception:
        pass

    # main title
    title_lines = ["Que faire ce", "week-end à", "Toulouse ?"]
    f_t = f_title
    lh  = draw.textbbox((0, 0), "A", font=f_t)[3]
    title_total_h = len(title_lines) * (lh + 8)
    date_h    = draw.textbbox((0, 0), "A", font=f_date)[3]
    weather_h = draw.textbbox((0, 0), "A", font=f_weather)[3] + 8 if (sat_weather or sun_weather) else 0
    dots_h = 30
    total  = title_total_h + 20 + date_h + 16 + weather_h + 16 + dots_h
    y = H - PAD - total

    for line in title_lines:
        lw = draw.textbbox((0, 0), line, font=f_t)[2]
        if "Toulouse" in line:
            before, after = line.split("Toulouse", 1)
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
    sat_str  = f"{sat.day} {FRENCH_MONTHS[sat.month-1]}"
    sun_str  = f"{sun.day} {FRENCH_MONTHS[sun.month-1]}"
    date_str = f"{sat_str} - {sun_str}"
    dw = draw.textbbox((0, 0), date_str, font=f_date)[2]
    draw.text(((W - dw) // 2, y), date_str, font=f_date, fill=COL_GREEN)
    y += date_h + 16

    # weather line (emoji + sat + sun temperatures)
    if sat_weather or sun_weather:
        weather_str = "  ·  ".join(filter(None, [sat_weather, sun_weather]))
        ww = draw.textbbox((0, 0), weather_str, font=f_weather)[2]
        draw.text(((W - ww) // 2, y), weather_str, font=f_weather, fill=(255, 255, 255, 220))
        y += weather_h
    y += 16

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
    """One event slide for the weekend carousel — mirrors story layout."""
    from PIL import Image, ImageDraw

    W, H = POST_W, POST_H
    PAD  = 52

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    photo = _download_image(event.get("image_url"))
    if photo:
        base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))
    base = _apply_gradient(base, int(H * 0.38), H, start_alpha=0, end_alpha=255)

    draw = ImageDraw.Draw(base)

    # favicon top-right
    FSIZE = 56
    _paste_favicon(base, W - PAD - FSIZE, 40, size=FSIZE)

    f_num      = _load_font(28, bold=True)
    f_headline = _load_font(52, bold=True)
    f_sub      = _load_font(26)
    f_meta     = _load_font(22)
    f_tiny     = _load_font(17)

    # number pill top-left
    num_str = str(n)
    nb  = draw.textbbox((0, 0), num_str, font=f_num)
    nw, nh = nb[2] - nb[0] + 28, nb[3] - nb[1] + 16
    draw.rounded_rectangle([PAD, 44, PAD + nw, 44 + nh], radius=nh // 2, fill=(15, 23, 42, 200))
    draw.text((PAD + 14, 44 + 8), num_str, font=f_num, fill=COL_WHITE)

    event_name = event.get("event_name") or None
    ig_caption = event.get("ig_caption") or ""
    venue      = _venue_from_caption(event.get("ig_caption") or "") or _extract_venue(event.get("summary") or "")

    # Headline: event_name in pink, or full title with colour logic
    headline      = event_name if event_name else event.get("title", "")
    headline_segs = [(w, COL_PINK) for w in headline.split()] if event_name else \
                    _segment_title(event.get("title", ""))
    headline_lines = _wrap_text(headline, f_headline, W - PAD*2, draw)[:2]

    # Description from ig_caption
    caption_raw   = [l.strip() for l in ig_caption.split("\n") if l.strip()][:2]
    caption_lines = []
    for raw in caption_raw:
        caption_lines += _wrap_text(raw, f_sub, W - PAD*2, draw)[:1]

    ev_str = _french_date_range(event.get("event_start") or "", event.get("event_end"))
    if venue and ev_str:
        meta_str = f"{venue}  ·  {ev_str}"
    elif venue:
        meta_str = venue
    elif ev_str:
        meta_str = f"{ev_str}  ·  Toulouse"
    else:
        meta_str = "Toulouse"

    lh_h  = draw.textbbox((0, 0), "Ag", font=f_headline)[3]
    lh_s  = draw.textbbox((0, 0), "A", font=f_sub)[3]
    lh_m  = draw.textbbox((0, 0), "A", font=f_meta)[3]
    site_h = draw.textbbox((0, 0), "A", font=f_tiny)[3]

    total_h = (len(headline_lines) * (lh_h + 8) + 10
               + len(caption_lines) * (lh_s + 6) + (8 if caption_lines else 0)
               + lh_m + 8 + site_h + 8)
    y = H - PAD - total_h

    y, _ = _draw_multicolor_lines(draw, PAD, y, headline_segs, f_headline, W - PAD*2, line_bonus=8)
    y += 10

    for line in caption_lines:
        draw.text((PAD, y), line, font=f_sub, fill=(255, 255, 255, 210))
        y += lh_s + 6
    if caption_lines:
        y += 8

    draw.text((PAD, y), meta_str, font=f_meta, fill=COL_GREEN)
    y += lh_m + 8

    draw.text((PAD, y), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 70))

    return base.convert("RGB")


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run(conn, out_dir: Path) -> list[dict[str, Any]]:
    """Generate square post images for place/culture clusters synthesised today.

    Events are excluded here — they are handled by render_today_events (stories)
    and render_weekend_carousel. News is excluded at the DB query level.
    """
    from src import cache as cache_mod

    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    clusters  = cache_mod.load_instagram_clusters(conn, today_iso)
    # Only place/culture with a real image → square post. Events and listicles have dedicated renderers.
    clusters  = [c for c in clusters if c.get("category") in ("place", "culture")
                 and _good_image_url(c.get("image_url"))
                 and not c.get("listicle_items")]

    if not clusters:
        print("instagram: no place/culture clusters for today")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []

    for cl in clusters:
        cid      = cl["cluster_id"]
        source   = cl.get("source") or "unknown"
        safe_cid = cid.replace(":", "_")
        filename = f"{safe_cid}_post.jpg"

        try:
            img = _render_post(cl)
            img.save(str(out_dir / filename), "JPEG", quality=90)
            print(f"  instagram: {filename} [{cl.get('category')}] {cl['title'][:50]}")
            manifest.append({
                "cluster_id": cid, "format": "post", "source": source,
                "category": cl.get("category"), "title": cl.get("title"),
                "image_url": cl.get("image_url"), "file": filename,
                "ig_caption":  cl.get("ig_caption"),
                "ig_hashtags": cl.get("ig_hashtags"),
                "ig_mention":  cl.get("ig_mention"),
                "venue": (cl.get("venue") or "").strip() or _venue_from_caption(cl.get("ig_caption") or "") or _extract_venue(cl.get("summary") or ""),
            })
        except Exception as e:
            print(f"  instagram: FAILED {cid} — {type(e).__name__}: {e}")

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"instagram: {len(manifest)} image(s) written to {out_dir}")
    return manifest


_BAD_IMAGE_PATTERNS = ("lessentiel.fr/nl/", "lessentiel.fr/nl/l.", "/nl/b.png", "/nl/r/")

def _good_image_url(url: str | None) -> bool:
    if not url:
        return False
    return not any(p in url for p in _BAD_IMAGE_PATTERNS)


def _render_today_intro(today: date) -> "Image":
    """Cover slide posted first in the daily story sequence.

    Backdrop is a static branded photo picked by season + today's weather
    (not the event photos, and no event count in the text: a manually
    deleted story later in the day would otherwise leave a stale number here).
    """
    from PIL import Image, ImageDraw

    W, H = STORY_W, STORY_H
    PAD  = 60

    # One weather fetch feeds both the backdrop choice and the temperature line.
    weather_code: int | None = None
    weather_str = ""
    try:
        from src.weather import fetch as _fetch_weather
        forecasts = _fetch_weather(days=1)
        if forecasts:
            f = forecasts[0]
            weather_code = f["code"]
            # No emoji here — the PIL font has no emoji glyphs (same reason
            # weekend_lines() omits it), so render just the temperatures.
            weather_str = f"{f['max']}° max · {f['min']}° min"
    except Exception:
        pass

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    bg_path = _story_background_path(today, weather_code)
    if bg_path:
        try:
            photo = Image.open(bg_path).convert("RGB")
            base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))
        except Exception:
            pass
    base = _apply_gradient(base, int(H * 0.45), H, start_alpha=0, end_alpha=235)

    draw = ImageDraw.Draw(base)

    FSIZE = 68
    _paste_favicon(base, W - 52 - FSIZE, 52, size=FSIZE)

    f_title   = _load_font(64, bold=True)
    f_date    = _load_font(38, bold=True)
    f_weather = _load_font(32, bold=True)
    f_tiny    = _load_font(18)

    title_lines = ["Aujourd'hui à", "Toulouse"]
    lh = draw.textbbox((0, 0), "A", font=f_title)[3]
    title_total_h = len(title_lines) * (lh + 8)

    date_str = f"{FRENCH_DAYS[today.weekday()].capitalize()} {today.day} {FRENCH_MONTHS[today.month-1]}"
    date_h   = draw.textbbox((0, 0), "A", font=f_date)[3]
    weather_h = draw.textbbox((0, 0), "A", font=f_weather)[3] + 8 if weather_str else 0

    total = title_total_h + 20 + date_h + 16 + weather_h
    y = H - PAD - total

    for line in title_lines:
        lw = draw.textbbox((0, 0), line, font=f_title)[2]
        if "Toulouse" in line:
            before, _after = line.split("Toulouse", 1)
            x = (W - lw) // 2
            if before:
                draw.text((x, y), before, font=f_title, fill=COL_WHITE)
                x += draw.textbbox((0, 0), before, font=f_title)[2]
            draw.text((x, y), "Toulouse", font=f_title, fill=COL_PINK)
        else:
            draw.text(((W - lw) // 2, y), line, font=f_title, fill=COL_WHITE)
        y += lh + 8
    y += 12

    dw = draw.textbbox((0, 0), date_str, font=f_date)[2]
    draw.text(((W - dw) // 2, y), date_str, font=f_date, fill=COL_GREEN)
    y += date_h + 16

    if weather_str:
        ww = draw.textbbox((0, 0), weather_str, font=f_weather)[2]
        draw.text(((W - ww) // 2, y), weather_str, font=f_weather, fill=(255, 255, 255, 220))
        y += weather_h

    tw = draw.textbbox((0, 0), SITE_LABEL, font=f_tiny)[2]
    draw.text(((W - tw) // 2, H - 34), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 70))

    return base.convert("RGB")


def render_today_events(conn, out_dir: Path) -> list[dict[str, Any]]:
    """Generate Format 1 (story) images for events happening today (including multi-day).

    Each event is posted as a Story only once (ig_story_at tracks this).
    """
    from src import cache as cache_mod

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    events = [e for e in cache_mod.load_events_on_date(conn, today)
              if _good_image_url(e.get("image_url"))]

    if not events:
        print(f"instagram today events: no events with images for {today}")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    posted_ids: list[str] = []

    # Intro slide always first, so it's the first Story viewers see.
    try:
        intro_file = "intro_story.jpg"
        _render_today_intro(date.fromisoformat(today)).save(str(out_dir / intro_file), "JPEG", quality=90)
        manifest.append({
            "cluster_id": None, "format": "story", "source": "brand",
            "category": "intro", "title": "Aujourd'hui à Toulouse",
            "image_url": None, "file": intro_file,
        })
        print(f"  instagram today: {intro_file} (intro)")
    except Exception as e:
        print(f"  instagram today: intro FAILED — {type(e).__name__}: {e}")

    for ev in events:
        cid      = ev["cluster_id"]
        safe_cid = cid.replace(":", "_")
        filename = f"{safe_cid}_event_story.jpg"
        try:
            img = _render_story(ev)
            img.save(str(out_dir / filename), "JPEG", quality=90)
            print(f"  instagram today: {filename} {ev['title'][:50]}")
            manifest.append({
                "cluster_id": cid, "format": "story", "source": ev.get("source", "unknown"),
                "category": "event", "title": ev.get("title"),
                "image_url": ev.get("image_url"), "file": filename,
            })
            posted_ids.append(cid)
        except Exception as e:
            print(f"  instagram today: FAILED {cid} — {type(e).__name__}: {e}")

    (out_dir / "today_events_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Mark as story-posted so they don't reappear tomorrow
    if posted_ids:
        cache_mod.mark_ig_story_posted(conn, posted_ids, today)
    print(f"instagram today events: {len(manifest)} story(ies) written to {out_dir}")
    return manifest


# ---------------------------------------------------------------------------
# Format 4 — Listicle carousel (top-N articles)
# ---------------------------------------------------------------------------

def _fetch_listicle_images(url: str, n: int) -> list[str | None]:
    """Scrape an article page and return up to n content images in document order."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup.find_all(["nav", "header", "footer", "aside", "script", "style"]):
            tag.decompose()
        imgs: list[str] = []
        for img in soup.find_all("img"):
            src = (img.get("src") or img.get("data-src") or img.get("data-lazy-src") or "").strip()
            if not src.startswith("http"):
                continue
            try:
                w = int(img.get("width") or 0)
                h = int(img.get("height") or 0)
                if (0 < w < 200) or (0 < h < 150):
                    continue
            except Exception:
                pass
            if any(s in src.lower() for s in ("logo", "icon", "favicon", "sprite", "pixel", "tracking")):
                continue
            imgs.append(src)
            if len(imgs) >= n:
                break
        return imgs + [None] * max(0, n - len(imgs))
    except Exception as exc:
        import sys as _sys
        print(f"listicle image fetch failed for {url}: {exc}", file=_sys.stderr)
        return [None] * n


def _render_listicle_cover(cluster: dict[str, Any], n_items: int) -> "Image":
    """Cover slide for a listicle carousel — main article image + title + count badge."""
    from PIL import Image, ImageDraw

    W, H = POST_W, POST_H
    PAD  = 52

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    photo = _download_image(cluster.get("image_url"))
    if photo:
        base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))
    base = _apply_gradient(base, int(H * 0.38), H, start_alpha=0, end_alpha=255)

    draw = ImageDraw.Draw(base)

    FSIZE = 60
    _paste_favicon(base, W - PAD - FSIZE, 40, size=FSIZE)

    f_badge = _load_font(26, bold=True)
    f_title = _load_font(52, bold=True)
    f_sub   = _load_font(24)
    f_tiny  = _load_font(17)

    ig_caption = cluster.get("ig_caption") or ""
    caption_lines = [l.strip() for l in ig_caption.split("\n") if l.strip()][:2]

    title      = cluster.get("title", "")
    title_segs = _segment_title(title)

    lh_t   = draw.textbbox((0, 0), "Ag", font=f_title)[3]
    lh_s   = draw.textbbox((0, 0), "A", font=f_sub)[3]
    lh_b   = draw.textbbox((0, 0), "A", font=f_badge)[3]
    site_h = draw.textbbox((0, 0), "A", font=f_tiny)[3]
    badge_h = lh_b + 20

    title_lines = _wrap_text(title, f_title, W - PAD * 2, draw)[:3]
    total_h = (badge_h + 16
               + len(title_lines) * (lh_t + 10) + 10
               + len(caption_lines) * (lh_s + 6)
               + site_h + 8)
    y = H - 120 - total_h

    # Count badge
    badge_text = f"{n_items} sélections"
    bw = draw.textbbox((0, 0), badge_text, font=f_badge)[2] + 40
    bx = (W - bw) // 2
    draw.rounded_rectangle([bx, y, bx + bw, y + badge_h], radius=badge_h // 2, fill=COL_GREEN)
    draw.text((bx + 20, y + 10), badge_text, font=f_badge, fill=COL_DARK)
    y += badge_h + 16

    y, _ = _draw_multicolor_lines(draw, PAD, y, title_segs, f_title, W - PAD * 2, line_bonus=10)
    y += 10

    for line in caption_lines:
        draw.text((PAD, y), line, font=f_sub, fill=(255, 255, 255, 180))
        y += lh_s + 6

    draw.text((PAD, y + 4), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 70))

    return base.convert("RGB")


def _render_listicle_item_slide(item: dict[str, Any], n: int, total: int, img_url: str | None) -> "Image":
    """One item slide for a listicle carousel."""
    from PIL import Image, ImageDraw

    W, H = POST_W, POST_H
    PAD  = 52

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    photo = _download_image(img_url) if img_url else None
    if photo:
        base.paste(_cover_crop(photo, W, H).convert("RGBA"), (0, 0))
    base = _apply_gradient(base, int(H * 0.42), H, start_alpha=0, end_alpha=255)

    draw = ImageDraw.Draw(base)

    FSIZE = 56
    _paste_favicon(base, W - PAD - FSIZE, 40, size=FSIZE)

    f_num   = _load_font(26, bold=True)
    f_title = _load_font(56, bold=True)
    f_desc  = _load_font(28)
    f_tiny  = _load_font(17)

    # Number pill top-left
    num_str = f"{n} / {total}"
    nb  = draw.textbbox((0, 0), num_str, font=f_num)
    nw  = nb[2] - nb[0] + 28
    nh  = nb[3] - nb[1] + 16
    draw.rounded_rectangle([PAD, 44, PAD + nw, 44 + nh], radius=nh // 2, fill=(15, 23, 42, 200))
    draw.text((PAD + 14, 44 + 8), num_str, font=f_num, fill=COL_WHITE)

    item_title = item.get("title", "")
    item_desc  = item.get("description", "")

    title_lines = _wrap_text(item_title, f_title, W - PAD * 2, draw)[:2]
    desc_lines  = _wrap_text(item_desc,  f_desc,  W - PAD * 2, draw)[:2]

    lh_t   = draw.textbbox((0, 0), "Ag", font=f_title)[3]
    lh_d   = draw.textbbox((0, 0), "A",  font=f_desc)[3]
    site_h = draw.textbbox((0, 0), "A",  font=f_tiny)[3]

    total_h = (len(title_lines) * (lh_t + 8) + 12
               + len(desc_lines) * (lh_d + 6)
               + site_h + 8)
    y = H - PAD - total_h

    segs = [(w, COL_PINK) for w in item_title.split()]
    y, _ = _draw_multicolor_lines(draw, PAD, y, segs, f_title, W - PAD * 2, line_bonus=8)
    y += 12

    for line in desc_lines:
        draw.text((PAD, y), line, font=f_desc, fill=(255, 255, 255, 200))
        y += lh_d + 6

    draw.text((PAD, y + 4), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 70))

    return base.convert("RGB")


def render_listicle_carousels(conn, out_dir: Path) -> list[str]:
    """Generate listicle carousel images for top-N articles synthesised today."""
    from src import cache as cache_mod

    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
    clusters  = cache_mod.load_instagram_clusters(conn, today_iso)
    listicles = [c for c in clusters if c.get("listicle_items") and _good_image_url(c.get("image_url"))]

    if not listicles:
        print("instagram listicles: none today")
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_files: list[str] = []

    for cl in listicles:
        cid = cl["cluster_id"]
        try:
            raw = cl.get("listicle_items")
            items = json.loads(raw) if isinstance(raw, str) else raw
            if not items or len(items) < 3:
                continue
            items = items[:9]  # + 1 cover slide = 10 max (Instagram carousel limit)
            safe_cid = cid.replace(":", "_")

            # Scrape individual images from the article page
            item_imgs = _fetch_listicle_images(cl.get("url") or "", len(items))

            slides: list[dict[str, Any]] = []

            # Cover slide
            cover = _render_listicle_cover(cl, len(items))
            cover_file = f"{safe_cid}_listicle_cover.jpg"
            cover.save(str(out_dir / cover_file), "JPEG", quality=92)
            slides.append({"file": cover_file, "type": "cover"})

            # Item slides
            for i, (item, img_url) in enumerate(zip(items, item_imgs), 1):
                effective_img = img_url or cl.get("image_url")
                slide = _render_listicle_item_slide(item, i, len(items), effective_img)
                slide_file = f"{safe_cid}_listicle_{i:02d}.jpg"
                slide.save(str(out_dir / slide_file), "JPEG", quality=92)
                slides.append({"file": slide_file, "type": "item", "title": item.get("title", "")})
                print(f"  instagram listicle: slide {i}/{len(items)} — {item.get('title','')[:40]}")

            manifest = {
                "type": "listicle_carousel", "cluster_id": cid,
                "title": cl.get("title", ""), "ig_caption": cl.get("ig_caption"),
                "ig_hashtags": cl.get("ig_hashtags"), "ig_mention": cl.get("ig_mention"),
                "venue": cl.get("venue") or "", "slides": slides,
            }
            mfile = f"{safe_cid}_listicle_manifest.json"
            (out_dir / mfile).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest_files.append(mfile)
            print(f"instagram listicle: {len(slides)} slides for '{cl.get('title','')[:50]}'")

        except Exception as exc:
            import sys as _sys
            print(f"instagram listicle FAILED {cid} — {type(exc).__name__}: {exc}", file=_sys.stderr)

    return manifest_files


def render_weekend_carousel(conn, out_dir: Path) -> list[dict[str, Any]]:
    """Generate Format 2 (weekend carousel) slides. Run on Fridays/Saturdays."""
    from src import cache as cache_mod

    sat, sun = _next_weekend()
    events   = cache_mod.load_weekend_events(conn, sat.isoformat(), sun.isoformat())

    if not events:
        print(f"instagram weekend: no events found for {sat} - {sun}")
        return []

    # Only include events that have a usable image — no blank slides
    all_count = len(events)
    events = [e for e in events if _good_image_url(e.get("image_url"))]
    print(f"instagram weekend: {len(events)}/{all_count} events have images")
    if not events:
        print(f"instagram weekend: no events with images for {sat} - {sun}, skipping carousel")
        return []

    # Clutch events are excluded from the weekend carousel (curation choice)
    events = [e for e in events if e.get("source") != "clutch"]
    if not events:
        print(f"instagram weekend: no non-Clutch events with images for {sat} - {sun}, skipping carousel")
        return []

    # Smart sort: most timely events first so the cover uses the best image.
    #   Tier 0 — starts on sat or sun (this weekend specifically)
    #   Tier 1 — started ≤6 days ago (current week, still fresh)
    #   Tier 2 — older ongoing events
    # Within each tier: OfficeTourisme first (best promo images), then L'Essentiel, then others.
    _SOURCE_RANK = {"office_tourisme": 0, "lessentiel": 1}

    def _event_sort_key(ev: dict) -> tuple:
        try:
            days_before = (sat - date.fromisoformat((ev.get("event_start") or sat.isoformat())[:10])).days
        except Exception:
            days_before = 0
        tier = 0 if days_before <= 0 else (1 if days_before <= 6 else 2)
        src_rank = _SOURCE_RANK.get(ev.get("source") or "", 2)
        return (tier, src_rank, days_before)

    events.sort(key=_event_sort_key)

    out_dir.mkdir(parents=True, exist_ok=True)
    slides: list[dict[str, Any]] = []

    # Cover slide (1) + up to 9 event slides = 10 max (Instagram carousel limit)
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
