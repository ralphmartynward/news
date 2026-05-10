from __future__ import annotations

import os

from openai import OpenAI

MODEL = "text-embedding-3-small"
MAX_INPUT_CHARS = 4000  # ~500 words; embedding model truncates anyway, this caps cost


def _client() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def embed_text(text: str) -> list[float]:
    return embed_batch([text])[0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    cleaned = [t[:MAX_INPUT_CHARS] if t else " " for t in texts]
    resp = _client().embeddings.create(model=MODEL, input=cleaned)
    return [d.embedding for d in resp.data]
