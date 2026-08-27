"""
backend/learning/query_patterns.py
-------------------------------------
Query pattern library: stores past questions with their routing
decisions and outcomes so similar future questions can reuse them.

Matching uses token overlap (fast, no embedding call needed).
For production, replace with embedding similarity lookup.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from . import _conn, store_query_pattern

_STOP = {"the", "a", "an", "is", "was", "were", "what", "how", "much",
         "did", "does", "in", "of", "for", "and", "or", "to", "from"}


def _tokenize(text: str) -> set:
    return {w for w in re.findall(r"\b\w+\b", text.lower()) if w not in _STOP}


def find_similar_pattern(question: str, threshold: float = 0.6) -> Optional[Dict]:
    """
    Returns the best-matching stored pattern if Jaccard similarity >= threshold.
    Returns None if no good match found.
    """
    tokens = _tokenize(question)
    with _conn() as c:
        rows = c.execute(
            "SELECT question, doc_name, outcome FROM query_patterns "
            "ORDER BY created_at DESC LIMIT 200"
        ).fetchall()

    best_score = 0.0
    best_row = None
    for row in rows:
        stored_tokens = _tokenize(row["question"])
        if not stored_tokens:
            continue
        intersection = len(tokens & stored_tokens)
        union = len(tokens | stored_tokens)
        jaccard = intersection / union if union else 0.0
        if jaccard > best_score:
            best_score = jaccard
            best_row = row

    if best_score >= threshold and best_row:
        return {
            "matched_question": best_row["question"],
            "doc_name": best_row["doc_name"],
            "outcome": best_row["outcome"],
            "similarity": round(best_score, 3),
        }
    return None


def classify_query(question: str) -> str:
    """
    Simple keyword-based query classifier.
    Returns: 'simple' | 'temporal' | 'cross_company' | 'complex'

    'simple'        -> single numeric fact lookup (likely in SQLite cache)
    'temporal'      -> asks about a specific year/quarter/period
    'cross_company' -> asks about multiple companies
    'complex'       -> narrative/reasoning question
    """
    q = question.lower()

    # Cross-company
    cross_words = ["compare", "versus", "vs", "both companies", "all companies"]
    if any(w in q for w in cross_words):
        return "cross_company"

    # Temporal: explicit fiscal period
    if re.search(r"\b(fy|q[1-4]|fiscal|year|quarter)\s*\d{4}\b", q) or \
       re.search(r"\b20\d{2}\b", q):
        return "temporal"

    # Simple lookup: single number asks
    simple_words = [
        "what was", "what is", "how much", "total", "revenue",
        "net income", "capex", "eps", "earnings per share",
    ]
    complex_words = ["why", "explain", "describe", "discuss", "risk", "strategy",
                     "outlook", "guidance", "compared to", "trend"]

    simple_hits = sum(1 for w in simple_words if w in q)
    complex_hits = sum(1 for w in complex_words if w in q)

    if simple_hits > complex_hits:
        return "simple"
    return "complex"
