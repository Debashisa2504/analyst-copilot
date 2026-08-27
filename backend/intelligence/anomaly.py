"""
backend/intelligence/anomaly.py
---------------------------------
Per-company historical fingerprint.
Builds a range of key metrics from previously indexed filings for the
same company, then flags when a new filing falls outside that range.

Storage: SQLite table in DATA_DIR/anomaly_fingerprints.db
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import DATA_DIR

_DB = DATA_DIR / "anomaly_fingerprints.db"

TRACKED_METRICS = [
    "revenue", "net_income", "operating_income",
    "gross_margin_pct", "net_margin_pct", "capex",
    "total_assets", "total_debt",
]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_DB))
    c.row_factory = sqlite3.Row
    return c


def _init():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS fingerprints (
                company     TEXT NOT NULL,
                metric      TEXT NOT NULL,
                period      TEXT NOT NULL,
                value       REAL NOT NULL,
                doc_name    TEXT NOT NULL,
                PRIMARY KEY (company, metric, period)
            )
        """)
        c.commit()


_init()


def store_metric_observation(
    company: str, metric: str, period: str, value: float, doc_name: str
) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO fingerprints (company, metric, period, value, doc_name) "
            "VALUES (?,?,?,?,?)",
            (company, metric, period, value, doc_name),
        )
        c.commit()


def get_historical_range(company: str, metric: str) -> Optional[Dict[str, float]]:
    """Returns {min, max, mean, count} for a metric across all stored periods."""
    with _conn() as c:
        rows = c.execute(
            "SELECT value FROM fingerprints WHERE company=? AND metric=?",
            (company, metric),
        ).fetchall()
    if len(rows) < 2:
        return None
    vals = [r["value"] for r in rows]
    return {
        "min": min(vals),
        "max": max(vals),
        "mean": sum(vals) / len(vals),
        "count": len(vals),
    }


def detect_anomalies(
    company: str, current_metrics: Dict[str, float], sigma_threshold: float = 2.0
) -> List[Dict[str, Any]]:
    """
    Compares current_metrics against historical range.
    Returns list of {metric, current_value, historical_min, historical_max, severity}.
    Uses a simple min/max band (not sigma) since we rarely have >10 data points.
    """
    anomalies = []
    for metric, current_val in current_metrics.items():
        hist = get_historical_range(company, metric)
        if hist is None or hist["count"] < 2:
            continue
        lo = hist["min"]
        hi = hist["max"]
        band = (hi - lo) * 0.1  # 10% buffer to avoid noise flags
        if current_val < lo - band or current_val > hi + band:
            pct_dev = abs(current_val - hist["mean"]) / (abs(hist["mean"]) or 1) * 100
            anomalies.append({
                "metric": metric,
                "current_value": current_val,
                "historical_min": lo,
                "historical_max": hi,
                "historical_mean": hist["mean"],
                "deviation_pct": round(pct_dev, 1),
                "severity": "high" if pct_dev > 25 else "medium",
                "description": (
                    f"{metric} is {current_val:,.0f}, outside historical range "
                    f"[{lo:,.0f} – {hi:,.0f}] ({pct_dev:.0f}% from mean)."
                ),
            })
    return anomalies
