"""
backend/intelligence/contradiction.py
---------------------------------------
Cross-section contradiction detector.
Scans multiple mentions of the same metric across filing sections
(income statement, MD&A, notes, segment table) and flags discrepancies >0.5%.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_MONEY_RE = re.compile(
    r"\b([\$]?\s*[\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?\b",
    re.IGNORECASE,
)

# Canonical metric patterns to hunt across sections
METRIC_PATTERNS: Dict[str, List[str]] = {
    "revenue": [
        r"net\s+sales", r"total\s+revenue", r"revenues?",
        r"net\s+revenues?",
    ],
    "net_income": [
        r"net\s+income", r"net\s+earnings",
        r"net\s+income\s+including\s+noncontrolling",
    ],
    "operating_income": [
        r"operating\s+income", r"income\s+from\s+operations",
    ],
    "capex": [
        r"purchases?\s+of\s+property",
        r"capital\s+expenditures?",
        r"PP&E",
    ],
    "total_assets": [r"total\s+assets"],
    "eps_diluted": [r"diluted\s+(?:earnings|net\s+income)\s+per\s+share", r"diluted\s+EPS"],
}

_SCALE = {"million": 1e6, "billion": 1e9, "thousand": 1e3, None: 1.0}


def _extract_value(text: str, patterns: List[str]) -> Optional[float]:
    """Find the first numeric value near any of the metric patterns."""
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        # Search ±200 chars around the match for a number
        start = max(0, m.start() - 100)
        end = min(len(text), m.end() + 100)
        snippet = text[start:end]
        nm = _MONEY_RE.search(snippet)
        if nm:
            raw = nm.group(1).replace(",", "").replace("$", "").strip()
            scale_word = (nm.group(2) or "").lower() or None
            try:
                return float(raw) * _SCALE.get(scale_word, 1.0)
            except ValueError:
                continue
    return None


def _section_texts(segments) -> Dict[str, str]:
    """Split segments into rough section buckets by keyword heuristics."""
    buckets: Dict[str, List[str]] = {
        "income_statement": [],
        "mda": [],
        "notes": [],
        "segment": [],
    }
    for seg in segments:
        t = seg.text.lower()
        if any(k in t for k in ["management", "discussion", "results of operations"]):
            buckets["mda"].append(seg.text)
        elif any(k in t for k in ["note ", "footnote", "see note"]):
            buckets["notes"].append(seg.text)
        elif any(k in t for k in ["segment", "business unit", "geographic"]):
            buckets["segment"].append(seg.text)
        else:
            buckets["income_statement"].append(seg.text)
    return {k: " ".join(v) for k, v in buckets.items() if v}


def detect_contradictions(segments, threshold: float = 0.005) -> List[Dict[str, Any]]:
    """
    Returns list of contradiction dicts:
    {metric, section_a, value_a, section_b, value_b, discrepancy_pct}
    for any pair where abs(a-b)/max(a,b) > threshold (default 0.5%).
    """
    sections = _section_texts(segments)
    flags: List[Dict[str, Any]] = []

    for metric, patterns in METRIC_PATTERNS.items():
        found: Dict[str, float] = {}
        for section_name, text in sections.items():
            val = _extract_value(text, patterns)
            if val is not None and val > 0:
                found[section_name] = val

        section_names = list(found.keys())
        for i in range(len(section_names)):
            for j in range(i + 1, len(section_names)):
                sA, sB = section_names[i], section_names[j]
                vA, vB = found[sA], found[sB]
                denom = max(abs(vA), abs(vB))
                if denom == 0:
                    continue
                disc = abs(vA - vB) / denom
                if disc > threshold:
                    flags.append({
                        "metric": metric,
                        "section_a": sA,
                        "value_a": vA,
                        "section_b": sB,
                        "value_b": vB,
                        "discrepancy_pct": round(disc * 100, 2),
                        "severity": "high" if disc > 0.02 else "medium",
                    })

    return flags
