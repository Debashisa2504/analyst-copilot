"""
backend/learning/retrieval_memory.py
--------------------------------------
Chunk-level retrieval memory: adjusts per-chunk weights based on
user feedback and applies those weights at retrieval time.

Weight formula (§6.3):
    weight = base_weight + 0.1 * correct_count - 0.15 * wrong_count
    clamped to [0.1, 2.0]

The adjusted weight is stored in SQLite and applied as a multiplier
on top of the RRF score in retriever.py.
"""
from __future__ import annotations

from typing import Dict, List

from . import _conn, record_feedback, get_chunk_weight


def apply_memory_weights(scored_chunks: list) -> list:
    """
    Re-ranks a list of RetrievedChunk objects by multiplying each
    chunk's RRF score by its stored memory weight.

    Call this after RRF fusion in retriever.py, before returning results.
    Returns the same list, re-sorted by weighted score.
    """
    for rc in scored_chunks:
        w = get_chunk_weight(rc.chunk.chunk_id)
        rc.score = rc.score * w
    scored_chunks.sort(key=lambda rc: rc.score, reverse=True)
    return scored_chunks


def feedback_summary() -> List[Dict]:
    """Returns all chunk weights with non-default values (for debugging/dashboard)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT chunk_id, weight, correct, wrong FROM chunk_weights "
            "WHERE weight != 1.0 ORDER BY weight DESC"
        ).fetchall()
    return [dict(r) for r in rows]
