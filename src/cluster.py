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
    against cached items AND against already-processed items in this batch.
    Returns the kept items with cluster_id + embedding attached.
    Skipped (near-duplicate) items are excluded from the result."""
    if not new_items:
        return []

    new_emb = _normalize(np.array(new_embeddings, dtype=np.float32))

    # Reference pool starts with cached items; grows as new items are kept so
    # that items earlier in this batch can cluster with items arriving later.
    ref_items: list[dict[str, Any]] = list(cached)
    ref_emb_list: list[list[float]] = [c["embedding"] for c in cached]

    kept: list[dict[str, Any]] = []

    for i, item in enumerate(new_items):
        best_sim = 0.0
        best: dict[str, Any] | None = None

        if ref_emb_list:
            ref_emb = _normalize(np.array(ref_emb_list, dtype=np.float32))
            row_sims = new_emb[i] @ ref_emb.T
            best_idx = int(np.argmax(row_sims))
            best_sim = float(row_sims[best_idx])
            best = ref_items[best_idx]

        if best is not None and best_sim >= NEAR_DUPLICATE_THRESHOLD and best.get("shown_in_feed", False):
            # near-duplicate of something already shown → skip entirely
            continue

        if best is not None and best_sim >= SAME_CLUSTER_THRESHOLD:
            cluster_id = best["cluster_id"]
        else:
            cluster_id = f"cluster:{uuid.uuid4()}"

        out = dict(item)
        out["embedding"] = new_embeddings[i]
        out["cluster_id"] = cluster_id
        kept.append(out)

        # Add to reference pool so subsequent items in this batch can cluster against it
        ref_items.append({"cluster_id": cluster_id, "shown_in_feed": False})
        ref_emb_list.append(new_embeddings[i])

    return kept
