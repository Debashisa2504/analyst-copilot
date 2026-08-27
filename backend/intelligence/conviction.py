"""
backend/intelligence/conviction.py
-------------------------------------
Weighted aggregate conviction score — a data-quality signal, NOT investment advice.

Weights:
    earnings_quality       30%
    management_credibility 25%
    disclosure_transparency 25%
    red_flag_deduction     20%
"""
from __future__ import annotations

from typing import Dict, List


def compute_conviction(
    red_flags: List[Dict],
    tone_score: int,           # 0-100
    ocf_ni_ratio: float = 1.0, # OCF / NI (>1.0 = good quality earnings)
) -> Dict:
    """
    Returns {
        score: int (0-100),
        earnings_quality: int,
        management_credibility: int,
        disclosure_transparency: int,
        red_flag_deduction: int,
        label: str,
        disclaimer: str,
    }
    """
    # Earnings quality: OCF/NI ratio capped at 1.5
    eq = min(100, int(max(0, ocf_ni_ratio) / 1.5 * 100))

    # Management credibility: derived from tone score (confidence = credibility signal)
    mc = tone_score

    # Disclosure transparency: inverse of red-flag count
    critical = sum(1 for f in red_flags if f["severity"] == "critical")
    high = sum(1 for f in red_flags if f["severity"] == "high")
    medium = sum(1 for f in red_flags if f["severity"] == "medium")
    dt = max(0, 100 - critical * 30 - high * 15 - medium * 7)

    # Red-flag deduction
    rfd = max(0, 100 - critical * 25 - high * 10 - medium * 5)

    raw = (eq * 0.30 + mc * 0.25 + dt * 0.25 + rfd * 0.20)
    score = max(0, min(100, int(raw)))

    if score >= 75:
        label = "Strong"
    elif score >= 55:
        label = "Moderate"
    elif score >= 35:
        label = "Weak"
    else:
        label = "Poor"

    return {
        "score": score,
        "label": label,
        "earnings_quality": eq,
        "management_credibility": mc,
        "disclosure_transparency": dt,
        "red_flag_deduction": rfd,
        "disclaimer": (
            "This conviction score is a data-quality signal derived from "
            "publicly available filings. It is NOT investment advice."
        ),
    }
