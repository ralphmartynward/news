"""One-off "Comment ça marche" slides for the Instagram highlight + pinned post.

Not part of the daily pipeline: run by hand, post manually.

    python -m src.instagram_explainer            # writes ./instagram_explainer/

Outputs the same 5 slides in two formats:
  story_N.png  1080x1920 — post as Stories, then add them to a highlight
  post_N.png   1080x1350 — post as one carousel and pin it (text stays inside
                           the central square so the grid crop never cuts it)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from src.instagram import (
    COL_BG, COL_GREEN, COL_PINK, COL_WHITE, SITE_LABEL,
    POST_H, POST_W, STORY_H, STORY_W,
    _ASSETS_DIR, _apply_gradient, _cover_crop, _load_font, _paste_favicon, _wrap_text,
)

OUT_DIR = Path(__file__).parent.parent / "instagram_explainer"

# Each slide: kicker, title lines as [(text, colour)] runs, optional body
# paragraph, optional bullet list (text, colour). No emoji: the PIL font has
# no emoji glyphs.
SLIDES: list[dict] = [
    {
        "kicker": "COMMENT ÇA MARCHE",
        "title": [[("Tout ce qui se passe à ", COL_WHITE)], [("Toulouse", COL_PINK), (", chaque jour", COL_WHITE)]],
        "body": "Concerts, expos, sorties, sport, soirées : on les rassemble pour vous, au même endroit.",
        "arrow": True,
    },
    {
        "kicker": "CHAQUE MATIN",
        "title": [[("Une ", COL_WHITE), ("Story", COL_GREEN), (" par", COL_WHITE)], [("événement du jour", COL_WHITE)]],
        "body": "Ouvrez nos Stories le matin : tout ce qui a lieu aujourd'hui à Toulouse, en un coup d'œil. Plus besoin de chercher.",
    },
    {
        "kicker": "TOUS LES GENRES",
        "title": [[("Pour toutes les ", COL_WHITE), ("envies", COL_GREEN)]],
        "bullets": [
            ("Concerts et spectacles", COL_PINK),
            ("Expositions et culture", COL_GREEN),
            ("Festivals et marchés", COL_PINK),
            ("Sport", COL_GREEN),
            ("Soirées et bars", COL_PINK),
        ],
    },
    {
        "kicker": "LE WEEK-END",
        "title": [[("Le vendredi,", COL_WHITE)], [("que faire ce ", COL_WHITE), ("week-end", COL_GREEN), (" ?", COL_WHITE)]],
        "body": "Un carrousel avec la sélection des meilleures sorties, publié chaque week-end.",
    },
    {
        "kicker": "ET SUR LE SITE",
        "title": [[("Tout l'", COL_WHITE), ("agenda", COL_GREEN), (",", COL_WHITE)], [("la newsletter", COL_PINK), (" et l'actu", COL_WHITE)]],
        "body": "Retrouvez le calendrier complet et le résumé quotidien de l'actualité toulousaine.",
        "cta": True,
    },
]


def _backdrop(W: int, H: int):
    from PIL import Image

    base = Image.new("RGB", (W, H), COL_BG).convert("RGBA")
    for name in ("toulouse_1080x1920_autumn_sunny.png", "toulouse_1080x1920_summer_sunny.jpg"):
        p = _ASSETS_DIR / name
        if p.exists():
            base.paste(_cover_crop(Image.open(p).convert("RGB"), W, H).convert("RGBA"), (0, 0))
            break
    # Dim the whole photo so text stays readable, then deepen toward the bottom.
    base = Image.alpha_composite(base, Image.new("RGBA", (W, H), (15, 23, 42, 165)))
    return _apply_gradient(base, int(H * 0.5), H, start_alpha=0, end_alpha=170)


def _render(slide: dict, W: int, H: int, idx: int, total: int):
    from PIL import ImageDraw

    base = _backdrop(W, H)
    draw = ImageDraw.Draw(base)
    PAD = 80
    _paste_favicon(base, W - 52 - 68, 52, size=68)

    f_kicker = _load_font(34, bold=True)
    f_title  = _load_font(84, bold=True)
    f_body   = _load_font(42)
    f_bullet = _load_font(52, bold=True)
    f_tiny   = _load_font(22)

    def th(font):
        return draw.textbbox((0, 0), "Ag", font=font)[3]

    body_lines = _wrap_text(slide["body"], f_body, W - PAD * 2, draw) if slide.get("body") else []
    bullets = slide.get("bullets", [])

    # Stack height first so the block can be vertically centred.
    title_h = len(slide["title"]) * (th(f_title) + 14)
    block_h = th(f_kicker) + 40 + title_h + 20
    if body_lines:
        block_h += len(body_lines) * (th(f_body) + 12)
    if bullets:
        block_h += len(bullets) * (th(f_bullet) + 30)
    if slide.get("cta"):
        block_h += 40 + 110
    if slide.get("arrow"):
        block_h += 70
    y = (H - block_h) // 2

    # Kicker pill
    kw = draw.textbbox((0, 0), slide["kicker"], font=f_kicker)[2]
    kh = th(f_kicker)
    kx = (W - kw) // 2
    draw.rounded_rectangle([kx - 30, y - 14, kx + kw + 30, y + kh + 20], radius=40, fill=(74, 222, 128, 255))
    draw.text((kx, y), slide["kicker"], font=f_kicker, fill=COL_BG)
    y += kh + 40 + 20

    # Title runs, centred per line
    for runs in slide["title"]:
        total_w = sum(draw.textbbox((0, 0), t, font=f_title)[2] for t, _ in runs)
        x = (W - total_w) // 2
        for text, col in runs:
            draw.text((x, y), text, font=f_title, fill=col)
            x += draw.textbbox((0, 0), text, font=f_title)[2]
        y += th(f_title) + 14
    y += 20

    for line in body_lines:
        lw = draw.textbbox((0, 0), line, font=f_body)[2]
        draw.text(((W - lw) // 2, y), line, font=f_body, fill=(255, 255, 255, 230))
        y += th(f_body) + 12

    for text, col in bullets:
        lw = draw.textbbox((0, 0), text, font=f_bullet)[2]
        x = (W - lw) // 2
        r = 11
        cy = y + th(f_bullet) // 2 + 4
        draw.ellipse([x - 44 - r, cy - r, x - 44 + r, cy + r], fill=col)
        draw.text((x, y), text, font=f_bullet, fill=COL_WHITE)
        y += th(f_bullet) + 30

    if slide.get("arrow"):
        ax, ay = W // 2, y + 36
        draw.line([(ax - 22, ay), (ax + 10, ay)], fill=COL_WHITE, width=5)
        draw.polygon([(ax + 10, ay - 14), (ax + 28, ay), (ax + 10, ay + 14)], fill=COL_WHITE)

    if slide.get("cta"):
        y += 40
        cw = draw.textbbox((0, 0), SITE_LABEL, font=f_bullet)[2]
        draw.rounded_rectangle([(W - cw) // 2 - 50, y, (W + cw) // 2 + 50, y + 100], radius=50, fill=COL_PINK)
        draw.text(((W - cw) // 2, y + 18), SITE_LABEL, font=f_bullet, fill=COL_BG)

    # Progress dots (dim) + site label, kept clear of the story UI / grid crop
    dots_y = H - 150 if H == STORY_H else H - 190
    gap, r = 34, 8
    x0 = (W - gap * (total - 1)) // 2
    for i in range(total):
        # Opaque dim grey: an alpha fill on the RGBA canvas replaces pixels
        # rather than blending, so it came out white after the RGB convert.
        col = COL_WHITE if i == idx else (110, 120, 140)
        draw.ellipse([x0 + i * gap - r, dots_y - r, x0 + i * gap + r, dots_y + r], fill=col)
    if not slide.get("cta"):
        tw = draw.textbbox((0, 0), SITE_LABEL, font=f_tiny)[2]
        draw.text(((W - tw) // 2, dots_y + 30), SITE_LABEL, font=f_tiny, fill=(255, 255, 255, 110))

    return base.convert("RGB")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i, slide in enumerate(SLIDES):
        _render(slide, STORY_W, STORY_H, i, len(SLIDES)).save(OUT_DIR / f"story_{i + 1}.png")
        _render(slide, POST_W, POST_H, i, len(SLIDES)).save(OUT_DIR / f"post_{i + 1}.png")
        print(f"slide {i + 1}/{len(SLIDES)} written")
    print(f"done -> {OUT_DIR}")


if __name__ == "__main__":
    sys.exit(main())
