"""
backend/statement_utils.py
----------------------------
Single source of truth for financial-statement-type keyword matching.

Both the chunk-level parser (backend/parser.py, Plan A section-aware
chunking) and the structured facts indexer (backend/facts_indexer.py,
Plan B) need to answer the same question -- "is this heading/table an
income statement, cash flow statement, balance sheet, or something
else?" -- and both label their output with the same four values
(income_statement | cash_flow | balance_sheet | other), because the LLM
at query time uses those labels to decide what to fetch from each
system. Keeping one copy of the keyword list here means the two can
never drift apart.

Two-tier matching:
  detect_statement_type()      -- broad substring match. Safe for text
                                   that is unambiguously a heading (an
                                   <h1>-<h6> tag, a table's own caption).
  text_is_statement_heading()  -- strict "starts with" match. Required
                                   for ambiguous prose (a <p>/<div> that
                                   might just be a bold heading, or might
                                   be a narrative cross-reference like
                                   "See consolidated statement of cash
                                   flows." -- which contains the phrase
                                   but isn't a heading).
"""
from __future__ import annotations

from typing import Dict, List

STATEMENT_KEYWORDS: Dict[str, List[str]] = {
    "income_statement": [
        "statements of operations",
        "statement of operations",
        "statements of income",
        "statement of income",
        "statements of earnings",
        "statement of earnings",
        "consolidated statements of income",
        "consolidated statement of income",
        "consolidated statements of operations",
        "consolidated statement of operations",
        "consolidated statements of earnings",
        "consolidated statement of earnings",
    ],
    "cash_flow": [
        "statements of cash flows",
        "statement of cash flows",
        "cash flow statement",
        "consolidated statements of cash flows",
        "consolidated statement of cash flows",
    ],
    "balance_sheet": [
        "balance sheet",
        "statements of financial position",
        "statement of financial position",
        "consolidated balance sheets",
        "consolidated balance sheet",
        "consolidated statements of financial position",
        "consolidated statement of financial position",
    ],
}


def detect_statement_type(text: str) -> str:
    """Broad match -- used for genuine headings (h-tags, table captions)."""
    lower = text.lower()
    for stmt_type, keywords in STATEMENT_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return stmt_type
    return "other"


def text_is_statement_heading(text: str) -> str:
    """
    Strict match for ambiguous prose elements -- text must START WITH a
    keyword to qualify. Avoids false-positives like 'See consolidated
    statement of cash flows.' which is a cross-reference, not a heading.
    """
    lower = text.lower().strip()
    for stmt_type, keywords in STATEMENT_KEYWORDS.items():
        if any(lower == kw or lower.startswith(kw) for kw in keywords):
            return stmt_type
    return "other"
