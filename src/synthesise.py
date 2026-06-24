from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
EXCERPT_CHARS = 1500
PROMPT_PATH = Path("prompts/synthesise.md")


class SynthesiseError(RuntimeError):
    pass


def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _format_items(items: list[dict[str, Any]]) -> str:
    blocks = []
    for it in items:
        title = it.get("title", "").strip()
        source = it.get("source", "").strip()
        excerpt = (it.get("extracted_text") or it.get("summary") or "").strip()[:EXCERPT_CHARS]
        blocks.append(f"Source: {source}\nTitle: {title}\nExcerpt:\n{excerpt}")
    return "\n\n---\n\n".join(blocks)


def _build_prompt(items: list[dict[str, Any]]) -> str:
    import datetime as _dt
    template = PROMPT_PATH.read_text(encoding="utf-8")
    sources = sorted({it.get("source", "") for it in items if it.get("source")})
    return (
        template.replace("{N}", str(len(items)))
        .replace("{sources}", ", ".join(sources))
        .replace("{items}", _format_items(items))
        .replace("{year}", str(_dt.datetime.now().year))
    )


def _extract_json(text: str) -> str:
    """Extract a JSON object from a model response. Handles bare JSON, JSON
    wrapped in markdown code fences, and JSON with leading/trailing text."""
    s = text.strip()
    # strip ```json ... ``` or ``` ... ``` fences
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    s = s.strip()
    # locate object boundaries
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise SynthesiseError(f"no JSON object found in response: {text[:200]!r}")
    return s[start:end + 1]


def synthesise(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Send a cluster's items to Claude and return the synthesis JSON."""
    if not items:
        raise SynthesiseError("synthesise called with empty items")

    prompt = _build_prompt(items)
    msg = _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = msg.content[0].text
    extracted = _extract_json(raw_text)
    try:
        data = json.loads(extracted)
    except json.JSONDecodeError as e:
        raise SynthesiseError(f"Claude returned non-JSON: {extracted[:200]!r}") from e

    if data.get("skip"):
        return None

    title = (data.get("title") or "").strip()
    summary = (data.get("summary") or "").strip()
    if not title or not summary:
        raise SynthesiseError(f"Claude response missing title or summary: {data}")

    category = (data.get("category") or "").strip().lower()
    if category not in ("news", "event", "place", "culture"):
        category = None  # will fall back to source default at entry-build time

    event_start = (data.get("event_start") or None)
    event_end = (data.get("event_end") or None)
    event_name = (data.get("event_name") or None)
    ig_caption = (data.get("ig_caption") or None)
    ig_hashtags_raw = data.get("ig_hashtags")
    ig_hashtags = json.dumps(ig_hashtags_raw, ensure_ascii=False) if ig_hashtags_raw else None

    return {
        "title": title,
        "summary": summary,
        "framing_note": (data.get("framing_note") or None),
        "read_for": data.get("read_for") or None,
        "category": category,
        "event_start": event_start,
        "event_end": event_end,
        "event_name": event_name,
        "ig_caption": ig_caption,
        "ig_hashtags": ig_hashtags,
    }


def extract_event_dates(cluster: dict[str, Any]) -> tuple[str | None, str | None]:
    """Lightweight date extraction for event clusters whose items have been pruned.

    Uses the cluster's stored title + summary rather than re-fetching source items.
    Returns (event_start, event_end) as ISO strings or (None, None).
    """
    title = (cluster.get("title") or "").strip()
    summary = (cluster.get("summary") or "").strip()
    if not title:
        return None, None

    prompt = (
        "Extract the event date(s) from this Toulouse event digest entry.\n"
        f"Title: {title}\n"
        f"Summary: {summary[:400]}\n\n"
        "Rules:\n"
        "- Only extract event_start from EXPLICIT day numbers (e.g. 'le 23 mai', 'samedi 14 juin'). "
        "Do NOT resolve relative expressions like 'ce soir', 'demain', 'samedi prochain', 'ce week-end' "
        "— these are only valid at publication time and will produce wrong dates during backfill. "
        "Return null if only relative expressions are present.\n"
        "- event_end: only for genuinely continuous multi-day events, not discrete separate dates.\n"
        f"- Assume year {__import__('datetime').datetime.now().year} unless stated.\n\n"
        'Return ONLY: {"event_start": "YYYY-MM-DD" or null, "event_end": "YYYY-MM-DD" or null}'
    )
    try:
        msg = _client().messages.create(
            model=MODEL,
            max_tokens=60,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            data = json.loads(raw[start:end + 1])
            return data.get("event_start") or None, data.get("event_end") or None
    except Exception:
        pass
    return None, None
