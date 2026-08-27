"""
backend/intelligence/tone.py
------------------------------
Scores MD&A language for hedging vs. confident tone.
Returns a 0–100 score (0 = maximum hedging, 100 = maximum confidence).
"""
from __future__ import annotations

import re
from typing import List

_HEDGE_TERMS = [
    "may", "might", "could", "possibly", "potentially", "uncertain",
    "expect", "anticipate", "believe", "estimate", "approximately",
    "subject to", "risks", "no assurance", "cannot guarantee",
    "volatility", "challenging", "headwinds", "difficult",
    "depending on", "if", "assuming", "contingent",
]

_CONFIDENT_TERMS = [
    "will", "committed", "delivered", "achieved", "strong",
    "record", "exceeded", "outperformed", "growth", "increase",
    "expanded", "confident", "demonstrated", "successful",
    "improved", "robust", "solid", "positive",
]


def score_tone(mda_text: str) -> int:
    """
    Returns an integer 0–100 where 100 = maximally confident tone.
    Uses a simple term-frequency ratio. For production, replace with an
    embedding-based classifier or a prompted LLM call.
    """
    if not mda_text or len(mda_text) < 50:
        return 50

    lower = mda_text.lower()
    words = re.findall(r"\b\w+\b", lower)
    total = len(words) or 1

    hedge_hits = sum(lower.count(t) for t in _HEDGE_TERMS)
    conf_hits = sum(lower.count(t) for t in _CONFIDENT_TERMS)

    # Normalize per 1000 words
    hedge_rate = hedge_hits / total * 1000
    conf_rate = conf_hits / total * 1000

    # Score: 50 is neutral; shift toward 100 for more confidence, toward 0 for more hedging
    raw = 50 + (conf_rate - hedge_rate) * 2
    return max(0, min(100, int(raw)))


def extract_mda_text(segments) -> str:
    """Extracts prose segments likely to be from the MD&A section."""
    mda_keywords = ["management", "discussion", "analysis", "results of operations"]
    mda_parts: List[str] = []
    in_mda = False
    for seg in segments:
        lower = seg.text.lower()
        if any(k in lower for k in mda_keywords):
            in_mda = True
        if in_mda and not seg.is_table:
            mda_parts.append(seg.text)
        if in_mda and len(" ".join(mda_parts)) > 20_000:
            break
    return " ".join(mda_parts)
