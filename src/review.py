"""Human review page for Instagram candidates — generated alongside the day's
manifests so a bad image/caption can be caught before the delayed posting run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.instagram import POST_H, POST_W, SOURCE_LABELS, STORY_H, STORY_W

TEMPLATE_DIR = Path("templates")
REPO_EDIT_BASE = "https://github.com/ralphmartynward/news/edit/main"

_FORMAT_LABELS = {
    "post": "Feed post",
    "story": "Story",
    "weekend": "Weekend carousel",
    "listicle": "Listicle carousel",
}

_TARGET_DIMS = {
    "story": (STORY_W, STORY_H),
    "post": (POST_W, POST_H),
    "weekend": (POST_W, POST_H),
    "listicle": (POST_W, POST_H),
}


def _quality(fmt: str, width: int | None, height: int | None) -> tuple[str, str]:
    """Classify how much a source photo had to be upscaled to fill its canvas."""
    if width is None or height is None:
        return "unknown", "No source photo (uses fallback background)"
    target_w, target_h = _TARGET_DIMS.get(fmt, (POST_W, POST_H))
    factor = max(target_w / width, target_h / height)
    if factor <= 1.0:
        return "ok", f"{width}×{height}"
    if factor <= 1.5:
        return "soft", f"{width}×{height} (mild upscale)"
    return "low", f"{width}×{height} (upscaled {factor:.1f}×  — will look soft/stretched)"


def _parse_hashtags(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (ValueError, TypeError):
        return [raw] if isinstance(raw, str) else []


def _card(*, fmt: str, manifest_file: str, file: str | None, title: str | None,
          caption: str | None, hashtags: Any, source: str | None,
          width: int | None, height: int | None, excluded: bool) -> dict[str, Any]:
    quality, quality_detail = _quality(fmt, width, height)
    return {
        "format": fmt,
        "format_label": _FORMAT_LABELS.get(fmt, fmt),
        "manifest_file": manifest_file,
        "edit_url": f"{REPO_EDIT_BASE}/docs/instagram/{{date}}/{manifest_file}",
        "file": file,
        "title": title or "(sans titre)",
        "caption": caption,
        "hashtags": _parse_hashtags(hashtags),
        "source_label": SOURCE_LABELS.get(source or "", source or "inconnu"),
        "quality": quality,
        "quality_detail": quality_detail,
        "excluded": bool(excluded),
    }


def _load_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _collect_cards(ig_dir: Path) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []

    posts = _load_json(ig_dir / "manifest.json") or []
    for e in posts:
        cards.append(_card(
            fmt="post", manifest_file="manifest.json", file=e.get("file"),
            title=e.get("title"), caption=e.get("ig_caption"), hashtags=e.get("ig_hashtags"),
            source=e.get("source"), width=e.get("img_width"), height=e.get("img_height"),
            excluded=e.get("excluded", False),
        ))

    stories = _load_json(ig_dir / "today_events_manifest.json") or []
    for e in stories:
        if e.get("category") == "intro":
            continue  # branded intro slide — not a review candidate
        cards.append(_card(
            fmt="story", manifest_file="today_events_manifest.json", file=e.get("file"),
            title=e.get("title"), caption=None, hashtags=None,
            source=e.get("source"), width=e.get("img_width"), height=e.get("img_height"),
            excluded=e.get("excluded", False),
        ))

    weekend = _load_json(ig_dir / "weekend_manifest.json")
    if weekend:
        for s in weekend.get("slides", []):
            if s.get("type") == "cover":
                continue
            cards.append(_card(
                fmt="weekend", manifest_file="weekend_manifest.json", file=s.get("file"),
                title=s.get("title"), caption=None, hashtags=None,
                source=None, width=s.get("img_width"), height=s.get("img_height"),
                excluded=s.get("excluded", False),
            ))

    for mfile in sorted(ig_dir.glob("*_listicle_manifest.json")):
        listicle = _load_json(mfile)
        if not listicle:
            continue
        shared_caption = listicle.get("ig_caption")
        shared_hashtags = listicle.get("ig_hashtags")
        for s in listicle.get("slides", []):
            if s.get("type") == "cover":
                continue
            cards.append(_card(
                fmt="listicle", manifest_file=mfile.name, file=s.get("file"),
                title=s.get("title"), caption=shared_caption, hashtags=shared_hashtags,
                source=None, width=s.get("img_width"), height=s.get("img_height"),
                excluded=s.get("excluded", False),
            ))

    return cards


def render_review_page(ig_dir: Path, date_iso: str) -> Path:
    """Build docs/instagram/<date>/review.html from whatever manifests exist for that day."""
    cards = _collect_cards(ig_dir)
    for c in cards:
        c["edit_url"] = c["edit_url"].replace("{date}", date_iso)

    groups: list[dict[str, Any]] = []
    for fmt, label in _FORMAT_LABELS.items():
        fmt_cards = [c for c in cards if c["format"] == fmt]
        if fmt_cards:
            groups.append({"format": fmt, "label": label, "cards": fmt_cards})

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = env.get_template("review.html.j2").render(
        date_iso=date_iso,
        groups=groups,
        total_count=len(cards),
        low_res_count=sum(1 for c in cards if c["quality"] == "low"),
    )
    out_path = ig_dir / "review.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
