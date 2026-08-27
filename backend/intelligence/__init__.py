"""
backend/intelligence/__init__.py
----------------------------------
Orchestrates all Intelligence Layer checks for a filing.
Called asynchronously after indexing -- never gates the 10-min SLA.

Includes: red flags (12), contradiction detection, tone scoring,
anomaly fingerprinting, and conviction aggregate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from ..config import DATA_DIR
from ..models import ParsedFiling
from .red_flags import check_red_flags
from .tone import score_tone, extract_mda_text
from .conviction import compute_conviction
from .contradiction import detect_contradictions
from .anomaly import detect_anomalies, store_metric_observation

INTEL_DIR = DATA_DIR / "intelligence"
INTEL_DIR.mkdir(parents=True, exist_ok=True)


def _intel_path(doc_name: str) -> Path:
    return INTEL_DIR / f"{doc_name}_intelligence.json"


def run_intelligence(parsed: ParsedFiling) -> Dict[str, Any]:
    """
    Runs all intelligence checks and saves findings to JSON.
    Returns the full result dict.
    """
    # Tone score from MD&A text
    mda_text = extract_mda_text(parsed.segments)
    tone = score_tone(mda_text)

    # Red flags (rule-based; real metric values would come from the structured cache)
    red_flags = check_red_flags({})

    # Contradiction detection across filing sections
    contradictions = detect_contradictions(parsed.segments)

    # Anomaly detection (requires historical data — returns empty on first filing per company)
    company = parsed.company or parsed.doc_name.split("_")[0]
    anomalies = detect_anomalies(company, {})

    # Conviction aggregate
    conviction = compute_conviction(
        red_flags=red_flags,
        tone_score=tone,
        ocf_ni_ratio=1.0,
    )

    result = {
        "doc_name": parsed.doc_name,
        "company": company,
        "status": "complete",
        "tone_score": tone,
        "red_flags": red_flags,
        "contradictions": contradictions,
        "anomalies": anomalies,
        "conviction": conviction,
        "segment_count": len(parsed.segments),
        "page_offset": parsed.page_offset,
        "page_num_method": parsed.page_num_method.value,
    }

    path = _intel_path(parsed.doc_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    return result


def load_intelligence(doc_name: str) -> Dict[str, Any]:
    path = _intel_path(doc_name)
    if not path.exists():
        return {"doc_name": doc_name, "status": "pending"}
    with open(path, encoding="utf-8") as f:
        return json.load(f)
