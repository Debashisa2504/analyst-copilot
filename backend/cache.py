"""
backend/cache.py
-----------------
SQLite-backed structured cache for common financial metrics extracted
once per filing (revenue, EPS, margins, capex, etc.).

Simple lookups resolve in <100ms without invoking RAG or LLM calls.
Cache is populated by a batched LLM call at ingest time (async, non-blocking).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from .config import DATA_DIR

_DB_PATH = DATA_DIR / "metrics_cache.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                doc_name    TEXT NOT NULL,
                metric_key  TEXT NOT NULL,
                value       TEXT,
                page_num    INTEGER,
                units       TEXT,
                period      TEXT,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (doc_name, metric_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS synonym_pairs (
                original    TEXT NOT NULL,
                expanded    TEXT NOT NULL,
                hits        INTEGER DEFAULT 1,
                PRIMARY KEY (original, expanded)
            )
            """
        )
        conn.commit()


# Initialise at import time
_init_db()


# --------------------------------------------------------------------------
# Metrics cache
# --------------------------------------------------------------------------

COMMON_METRICS = [
    "revenue", "net_income", "gross_profit", "operating_income",
    "capex", "operating_cash_flow", "free_cash_flow", "total_assets",
    "total_debt", "eps_basic", "eps_diluted", "gross_margin_pct",
    "operating_margin_pct", "net_margin_pct",
]


def store_metric(
    doc_name: str,
    metric_key: str,
    value: str,
    page_num: Optional[int] = None,
    units: Optional[str] = None,
    period: Optional[str] = None,
) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO metrics
                (doc_name, metric_key, value, page_num, units, period)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (doc_name, metric_key, value, page_num, units, period),
        )
        conn.commit()


def lookup_metric(
    doc_name: str, metric_key: str
) -> Optional[Dict[str, Any]]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM metrics WHERE doc_name=? AND metric_key=?",
            (doc_name, metric_key),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_cached_docs() -> list:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT doc_name FROM metrics"
        ).fetchall()
    return [r["doc_name"] for r in rows]


# --------------------------------------------------------------------------
# Synonym pairs (learning loop: failure self-repair)
# --------------------------------------------------------------------------

def store_synonym_pair(original: str, expanded: str) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO synonym_pairs (original, expanded)
            VALUES (?, ?)
            ON CONFLICT(original, expanded)
            DO UPDATE SET hits = hits + 1
            """,
            (original.lower().strip(), expanded.lower().strip()),
        )
        conn.commit()


def get_learned_expansions(query: str) -> list:
    lower = query.lower()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT expanded FROM synonym_pairs WHERE ? LIKE '%' || original || '%' ORDER BY hits DESC LIMIT 5",
            (lower,),
        ).fetchall()
    return [r["expanded"] for r in rows]
