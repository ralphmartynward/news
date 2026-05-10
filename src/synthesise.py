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


def synthesise(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Send a cluster's items to Claude and return the synthesis JSON."""
    if not items:
        raise SynthesiseError("synthesise called with empty items")

    prompt = _build_prompt(items)
    msg = _client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "{"},
        ],
    )

    raw = "{" + msg.content[0].text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SynthesiseError(f"Claude returned non-JSON: {raw[:200]}") from e

    title = (data.get("title") or "").strip()
    summary = (data.get("summary") or "").strip()
    if not title or not summary:
        raise SynthesiseError(f"Claude response missing title or summary: {data}")

    return {
        "title": title,
        "summary": summary,
        "framing_note": (data.get("framing_note") or None),
        "read_for": data.get("read_for") or None,
    }
