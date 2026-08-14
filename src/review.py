"""Human review page for Instagram candidates — generated alongside the day's
manifests so a bad image/caption can be caught before the delayed posting run.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.instagram import (
    POST_H, POST_W, SOURCE_LABELS, STORY_H, STORY_W,
    _french_date_range, _good_image_url, _image_dims, _render_story,
)

TEMPLATE_DIR = Path("templates")
REPO_EDIT_BASE = "https://github.com/ralphmartynward/news/edit/main"
UPCOMING_DAYS_AHEAD = 7

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


def _collect_upcoming(conn, ig_dir: Path, today_iso: str, days_ahead: int = UPCOMING_DAYS_AHEAD) -> list[dict[str, Any]]:
    """Events already in the DB that will become eligible as a Story on a future
    date, projected from *current* re-post eligibility (ig_story_at, event
    span). This is a best-effort projection, not a guarantee: it assumes no
    new articles arrive and no events get posted in the meantime.
    """
    from src import cache as cache_mod

    today = date.fromisoformat(today_iso)
    seen: set[str] = set()
    days: list[dict[str, Any]] = []
    preview_dir = ig_dir / "upcoming"

    for offset in range(1, days_ahead + 1):
        target_iso = (today + timedelta(days=offset)).isoformat()
        events = [
            e for e in cache_mod.load_events_on_date(conn, target_iso)
            if _good_image_url(e.get("image_url")) and e["cluster_id"] not in seen
        ]
        if not events:
            continue

        day_cards: list[dict[str, Any]] = []
        for ev in events:
            seen.add(ev["cluster_id"])
            safe_cid = ev["cluster_id"].replace(":", "_")
            filename = f"{safe_cid}_preview.jpg"
            try:
                img = _render_story(ev)
                preview_dir.mkdir(parents=True, exist_ok=True)
                img.save(str(preview_dir / filename), "JPEG", quality=90)
                file_ref = f"upcoming/{filename}"
            except Exception:
                file_ref = None

            width, height = _image_dims(ev.get("image_url")) or (None, None)
            quality, quality_detail = _quality("story", width, height)
            day_cards.append({
                "title": ev.get("title") or "(sans titre)",
                "file": file_ref,
                "date_range": _french_date_range(ev.get("event_start") or target_iso, ev.get("event_end")),
                "source_label": SOURCE_LABELS.get(ev.get("source") or "", ev.get("source") or "inconnu"),
                "quality": quality,
                "quality_detail": quality_detail,
            })

        days.append({"date_iso": target_iso, "cards": day_cards})

    return days


def render_review_page(conn, ig_dir: Path, date_iso: str) -> Path:
    """Build docs/instagram/<date>/review.html from whatever manifests exist for that day."""
    cards = _collect_cards(ig_dir)
    for c in cards:
        c["edit_url"] = c["edit_url"].replace("{date}", date_iso)

    groups: list[dict[str, Any]] = []
    for fmt, label in _FORMAT_LABELS.items():
        fmt_cards = [c for c in cards if c["format"] == fmt]
        if fmt_cards:
            groups.append({"format": fmt, "label": label, "cards": fmt_cards})

    upcoming_days = _collect_upcoming(conn, ig_dir, date_iso)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = env.get_template("review.html.j2").render(
        date_iso=date_iso,
        groups=groups,
        total_count=len(cards),
        low_res_count=sum(1 for c in cards if c["quality"] == "low"),
        upcoming_days=upcoming_days,
    )
    out_path = ig_dir / "review.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
