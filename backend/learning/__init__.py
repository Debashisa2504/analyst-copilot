"""
backend/learning/__init__.py
------------------------------
Learning loop shared DB connection and primitive operations.
Higher-level logic lives in the submodules:
  - retrieval_memory.py  (chunk weight application)
  - query_patterns.py    (pattern matching + query classification)
  - self_repair.py       (rephrase detection + synonym pair extraction)
"""
from __future__ import annotations

import sqlite3
from ..config import DATA_DIR

_LEARN_DB = DATA_DIR / "learning.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_LEARN_DB))
    c.row_factory = sqlite3.Row
    return c


def _init():
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS chunk_weights (
                chunk_id TEXT PRIMARY KEY,
                weight REAL DEFAULT 1.0,
                correct INTEGER DEFAULT 0,
                wrong INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS query_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                doc_name TEXT,
                outcome TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS rephrase_pairs (
                original TEXT NOT NULL,
                rephrased TEXT NOT NULL,
                count INTEGER DEFAULT 1,
                PRIMARY KEY (original, rephrased)
            );
        """)
        c.commit()


_init()


def record_feedback(chunk_id: str, verdict: str) -> None:
    """
    Updates per-chunk weight based on user feedback.
    verdict: 'correct' | 'wrong' | 'clarify'
    """
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO chunk_weights (chunk_id) VALUES (?)", (chunk_id,))
        if verdict == "correct":
            c.execute(
                "UPDATE chunk_weights SET correct=correct+1, "
                "weight=MIN(2.0, weight+0.1) WHERE chunk_id=?",
                (chunk_id,),
            )
        elif verdict == "wrong":
            c.execute(
                "UPDATE chunk_weights SET wrong=wrong+1, "
                "weight=MAX(0.1, weight-0.15) WHERE chunk_id=?",
                (chunk_id,),
            )
        c.commit()


def get_chunk_weight(chunk_id: str) -> float:
    with _conn() as c:
        row = c.execute(
            "SELECT weight FROM chunk_weights WHERE chunk_id=?", (chunk_id,)
        ).fetchone()
    return float(row["weight"]) if row else 1.0


def store_query_pattern(question: str, doc_name: str, outcome: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO query_patterns (question, doc_name, outcome) VALUES (?,?,?)",
            (question, doc_name, outcome),
        )
        c.commit()


def record_rephrase(original: str, rephrased: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO rephrase_pairs (original, rephrased) VALUES (?,?) "
            "ON CONFLICT(original, rephrased) DO UPDATE SET count=count+1",
            (original, rephrased),
        )
        c.commit()
