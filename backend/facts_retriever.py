"""
backend/facts_retriever.py
---------------------------
Structured-data retrieval path for financial calculation questions (Plan B).

For qualitative/narrative questions this module returns None and the pipeline
falls back to standard chunk-based RAG.  For quantitative/calculation
questions it returns a formatted table of ALL rows from the relevant financial
statements for that filing — no top-k truncation, no embedding distance needed.

Flow
----
1. classify_statements_needed(question)
   → LLM (VERIFY_MODEL) decides which statements are needed:
     income_statement | cash_flow | balance_sheet
   → Returns [] for qualitative questions → caller skips structured path.

2. fetch_facts_for_question(question, doc_name)
   → SQL-queries financial_facts for the filing + statement types.
   → Formats result as compact columnar text for the LLM context window.
"""
from __future__ import annotations

from typing import List, Optional

import psycopg

from .config import DATABASE_URL, VERIFY_MODEL, VERIFY_PROVIDER
from .llm_client import call_llm_json

_CLASSIFY_SYSTEM = """\
You are a financial data router. Given a financial question, decide which
financial statements contain the data needed to answer it.

Reply with strict JSON only:
{"statements": ["income_statement", "cash_flow", "balance_sheet"]}

Rules:
- Use "income_statement" for revenue, operating income, net income, EPS,
  margins, depreciation on the income statement, D&A % of revenue.
- Use "cash_flow" for operating/investing/financing cash flows, capex,
  D&A from cash flow statement, free cash flow, EBITDA calculation.
- Use "balance_sheet" for assets, liabilities, equity, PP&E, current assets,
  working capital, book value.
- Use [] for purely qualitative questions (legal matters, strategy,
  acquisitions narrative, management commentary) that don't require
  reading a financial statement table.
- Include multiple statements when the question requires data from more than one
  (e.g. EBITDA = operating income [income_statement] + D&A [cash_flow]).
"""


def classify_statements_needed(question: str) -> List[str]:
    """
    Uses the LLM to decide which financial statement tables are needed.
    Returns a list of statement type strings, or [] for narrative questions.
    Falls back to [] on any error (safe default: use chunk-based RAG only).
    """
    try:
        result = call_llm_json(
            _CLASSIFY_SYSTEM,
            f"Question: {question}",
            VERIFY_MODEL,
            provider=VERIFY_PROVIDER,
        )
        stmts = result.get("statements", [])
        valid = {"income_statement", "cash_flow", "balance_sheet"}
        return [s for s in stmts if s in valid]
    except Exception:
        return []


def _format_as_table(rows: list) -> str:
    """
    Format SQL result rows as compact columnar text sections.
    Each section: === Statement Type (in units, page N) ===
                    Row Label [Col Header]: value
    page_num is the most common page across rows in the section,
    giving the LLM a concrete page to cite in its answer.
    """
    sections: dict[str, list] = {}
    for stmt_type, row_label, col_header, value_text, units, page_num in rows:
        sections.setdefault(stmt_type, []).append(
            (row_label, col_header, value_text, units, page_num)
        )

    lines: List[str] = []
    for stmt_type, facts in sections.items():
        header = stmt_type.replace("_", " ").title()
        unit_label = next((u for _, _, _, u, _ in facts if u), "")
        unit_str = f" (in {unit_label})" if unit_label else ""
        # Most common non-null page across this statement's rows
        pages = [p for _, _, _, _, p in facts if p is not None]
        page_str = f", page {max(set(pages), key=pages.count)}" if pages else ""
        lines.append(f"=== {header}{unit_str}{page_str} ===")
        for row_label, col_header, value_text, _, _ in facts:
            col_str = f" [{col_header}]" if col_header else ""
            lines.append(f"  {row_label}{col_str}: {value_text}")
        lines.append("")  # blank line between sections

    return "\n".join(lines).rstrip()


def fetch_facts_for_question(question: str, doc_name: str) -> Optional[str]:
    """
    Classify which statements are needed, SQL-query financial_facts, and
    return a formatted text block for the LLM context.

    Returns None if:
    - The question is qualitative (LLM returns [])
    - No matching rows found in the DB
    """
    needed = classify_statements_needed(question)
    if not needed:
        return None

    stem = doc_name.replace(".htm", "").replace(".html", "")

    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            """
            SELECT statement_type, row_label, column_header, value_text, units, page_num
            FROM financial_facts
            WHERE doc_name = %s AND statement_type = ANY(%s)
            ORDER BY statement_type, id
            """,
            (stem, needed),
        ).fetchall()

    if not rows:
        return None

    return _format_as_table(rows)
