from __future__ import annotations

import uuid
from typing import Any

import numpy as np

SAME_CLUSTER_THRESHOLD = 0.78
NEAR_DUPLICATE_THRESHOLD = 0.92


def _normalize(vecs: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def assign_clusters(
    new_items: list[dict[str, Any]],
    new_embeddings: list[list[float]],
    cached: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """For each new item, decide skip / cluster_id by comparing its embedding
    against cached items. Returns the kept items with cluster_id + embedding
    + skip flag attached. Skipped items are excluded from the result."""
    if not new_items:
        return []

    new_emb = _normalize(np.array(new_embeddings, dtype=np.float32))

    if cached:
        cached_emb = _normalize(np.array([c["embedding"] for c in cached], dtype=np.float32))
        # similarity matrix: rows = new, cols = cached
        sims = new_emb @ cached_emb.T
    else:
        sims = np.zeros((len(new_items), 0), dtype=np.float32)

    kept: list[dict[str, Any]] = []
    for i, item in enumerate(new_items):
        if sims.shape[1] > 0:
            best_idx = int(np.argmax(sims[i]))
            best_sim = float(sims[i, best_idx])
            best = cached[best_idx]
        else:
            best_idx = -1
            best_sim = 0.0
            best = None

        if best is not None and best_sim >= NEAR_DUPLICATE_THRESHOLD and best["shown_in_feed"]:
            # near-duplicate of something we already showed → skip entirely
            continue

        if best is not None and best_sim >= SAME_CLUSTER_THRESHOLD:
            cluster_id = best["cluster_id"]
        else:
            cluster_id = f"cluster:{uuid.uuid4()}"

        out = dict(item)
        out["embedding"] = new_embeddings[i]
        out["cluster_id"] = cluster_id
        kept.append(out)

    return kept
