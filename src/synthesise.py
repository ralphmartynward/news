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
    template = PROMPT_PATH.read_text(encoding="utf-8")
    sources = sorted({it.get("source", "") for it in items if it.get("source")})
    return (
        template.replace("{N}", str(len(items)))
        .replace("{sources}", ", ".join(sources))
        .replace("{items}", _format_items(items))
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

    return {
        "title": title,
        "summary": summary,
        "framing_note": (data.get("framing_note") or None),
        "read_for": data.get("read_for") or None,
        "category": category,
    }
