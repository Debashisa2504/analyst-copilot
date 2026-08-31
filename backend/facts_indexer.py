"""
backend/facts_indexer.py
-------------------------
Parses every HTML filing and stores each table cell as a structured row in
the financial_facts table (Plan B, ADR-005).

Rather than relying on chunk-based retrieval (which caps out at top-k rows
and can never return a full 50-row income statement in one shot), this module
reads the same .htm files already in filings/ and inserts every fact into a
relational table that can be queried with SQL — no top-k limit, no embeddings.

Key functions
-------------
parse_doc_name(doc_name)      → metadata dict (company, year, quarter, …)
ingest_filing_facts(doc_name) → parse HTML, insert into financial_facts, return count
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
from bs4 import BeautifulSoup, Tag

from .config import (
    DATABASE_URL, FILINGS_DIR, MIN_PAGE_BREAK_MARKERS,
    STATEMENT_HEADING_PAGE_SPAN, WORDS_PER_PAGE_FALLBACK,
)
from .parser import (
    _build_header_intervals,
    _extract_html_from_sgml,
    _find_header_row,
    _is_spacer_cell,
    _lookup_header,
    _row_cells_with_columns,
    _style_of,
    count_page_break_markers,
    extract_unit_header,
    is_numeric_text,
    normalize_accounting_number,
)
from .statement_utils import (
    STATEMENT_KEYWORDS,
    detect_statement_type as _detect_statement_type,
    text_is_statement_heading as _text_is_statement_heading,
)

# --------------------------------------------------------------------------
# doc_name → metadata
# --------------------------------------------------------------------------
# Matches: COMPANY_2015_10K  |  COMPANY_2023Q2_10Q  |  3M_2018_10K
_DOC_RE = re.compile(
    r"^(?P<company>[A-Z0-9]+)_(?P<year>\d{4})(?:Q(?P<quarter>\d))?_(?P<ftype>10[KQ]|8K)",
    re.IGNORECASE,
)
_FTYPE_MAP = {"10K": "10-K", "10Q": "10-Q", "8K": "8-K"}


def parse_doc_name(doc_name: str) -> dict:
    """
    Parse a filing stem (e.g. 'AMD_2015_10K') into metadata fields.
    Returns a dict with: company, fiscal_year, fiscal_quarter,
    period_type ('annual' | 'quarterly'), filing_type ('10-K' | '10-Q' | '8-K').
    """
    stem = doc_name.replace(".htm", "").replace(".html", "")
    m = _DOC_RE.match(stem)
    if not m:
        return {
            "company": stem,
            "fiscal_year": 0,
            "fiscal_quarter": None,
            "period_type": "unknown",
            "filing_type": "unknown",
        }
    quarter = int(m.group("quarter")) if m.group("quarter") else None
    ftype_raw = m.group("ftype").upper()
    return {
        "company": m.group("company").upper(),
        "fiscal_year": int(m.group("year")),
        "fiscal_quarter": quarter,
        "period_type": "quarterly" if quarter else "annual",
        "filing_type": _FTYPE_MAP.get(ftype_raw, ftype_raw),
    }


# --------------------------------------------------------------------------
# Statement-type detection from surrounding heading text
# --------------------------------------------------------------------------
# STATEMENT_KEYWORDS + the two matcher functions now live in
# backend/statement_utils.py, shared with backend/parser.py so the two
# systems never drift on what counts as each of the four section labels.

# --------------------------------------------------------------------------
# Structured table parsing (mirrors parse_table_to_facts but returns dicts)
# --------------------------------------------------------------------------

def _parse_structured_facts(
    table_tag: Tag, units: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Like parser.parse_table_to_facts but returns structured dicts instead of
    serialized strings, so we can store each field in its own DB column.

    Each dict: {row_label, column_header, value_text, value_numeric, units}
    value_text is the normalized string (accounting negatives converted to '-N').
    value_numeric is the parsed float or None for non-numeric cells.
    """
    rows = table_tag.find_all("tr")
    if not rows:
        return []

    header_row = _find_header_row(rows) or rows[0]
    header_intervals = _build_header_intervals(header_row)
    data_rows = [r for r in rows if r is not header_row]

    facts: List[Dict[str, Any]] = []
    for row in data_rows:
        cells = _row_cells_with_columns(row)
        if not cells:
            continue

        label_idx = next(
            (i for i, (_, text, _) in enumerate(cells) if not _is_spacer_cell(text)),
            None,
        )
        if label_idx is None:
            continue
        row_label = cells[label_idx][1]

        for col, text, _span in cells[label_idx + 1:]:
            if _is_spacer_cell(text):
                continue
            header_text = _lookup_header(header_intervals, col) if header_intervals else None
            col_header = header_text or f"col_{col}"

            if is_numeric_text(text):
                value_str = normalize_accounting_number(text)
                try:
                    value_numeric: Optional[float] = float(value_str.replace(",", ""))
                except ValueError:
                    value_numeric = None
            else:
                value_str = text
                value_numeric = None

            unit_suffix = f" ({units})" if units else ""
            facts.append(
                {
                    "row_label": row_label,
                    "column_header": col_header,
                    "value_text": f"{value_str}{unit_suffix}",
                    "value_numeric": value_numeric,
                    "units": units,
                }
            )

    return facts


# --------------------------------------------------------------------------
# Main ingestion entry point
# --------------------------------------------------------------------------

def ingest_filing_facts(doc_name: str) -> int:
    """
    Parse the filing's HTML and upsert all table cells into financial_facts.

    Mirrors the page-tracking logic from parser.parse_filing so that each
    fact row carries an accurate page_num. Uses CSS page-break markers when
    available (>= MIN_PAGE_BREAK_MARKERS), falling back to word-count
    estimation — exactly as the main parser does.

    The function is idempotent: it deletes existing rows for the filing
    before inserting, so re-running is safe.

    Returns the number of rows inserted.
    """
    stem = doc_name.replace(".htm", "").replace(".html", "")
    filing_path = FILINGS_DIR / f"{stem}.htm"
    if not filing_path.exists():
        raise FileNotFoundError(f"Filing not found: {filing_path}")

    raw = filing_path.read_bytes().decode("utf-8", errors="replace")
    raw = _extract_html_from_sgml(raw)

    # See backend/parser.py's parse_filing() for why lxml is used instead of
    # the stdlib html.parser (malformed real-world SEC HTML recovery).
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    meta = parse_doc_name(stem)
    use_markers = count_page_break_markers(soup) >= MIN_PAGE_BREAK_MARKERS

    # --- Pre-scan: build electronic-page → printed-page offset (Option D) ---
    _SOLO_NUM_RE = re.compile(r"^\d{1,4}$")
    _page_last_num: Dict[int, int] = {}
    _p = 1
    body = soup.body or soup
    for _el in (el for el in body.descendants if isinstance(el, Tag)):
        if use_markers:
            _s = _style_of(_el)
            if ("page-break-before" in _s or "page-break-after" in _s) and "always" in _s:
                _p += 1
        if _el.name == "div":
            _t = _el.get_text(" ", strip=True)
            if _t and _SOLO_NUM_RE.match(_t):
                _page_last_num[_p] = int(_t)

    offset = 0
    _sorted_pages = sorted(_page_last_num.keys())
    for _ep in _sorted_pages[:-2]:
        _n0 = _page_last_num[_ep]
        _n1 = _page_last_num.get(_ep + 1)
        _n2 = _page_last_num.get(_ep + 2)
        if _n1 == _n0 + 1 and _n2 == _n0 + 2:
            offset = _n0 - _ep
            break

    # --- Main DOM traversal ---
    current_page = 1
    word_count = 0
    active_units: Optional[str] = None
    # Track the most recent heading/bold text seen before each table, and the
    # page it appeared on -- a heading only classifies tables within
    # STATEMENT_HEADING_PAGE_SPAN pages of it (see config).
    heading_buffer = ""
    heading_page: Optional[int] = None

    all_elements = [el for el in body.descendants if isinstance(el, Tag)]
    rows_to_insert: List[Dict[str, Any]] = []

    for element in all_elements:
        if element.decomposed:
            continue

        # Capture headings to detect statement type for the next table.
        # SEC filings use semantic <h> tags AND bold/prominent <p>/<div>/<span>
        # elements, so we check both.
        if element.name in ("h1", "h2", "h3", "h4"):
            t = element.get_text(" ", strip=True)
            if t:
                heading_buffer = t
                heading_page = current_page

        if element.name == "table":
            # Skip layout tables (those that contain nested tables)
            if element.find("table") is not None:
                continue

            effective_page = current_page + offset

            unit_scan = element.get_text(" ", strip=True)
            table_units = extract_unit_header(unit_scan) or active_units
            if table_units:
                active_units = table_units

            # Detect statement type: prefer a heading that is still in scope
            # (within STATEMENT_HEADING_PAGE_SPAN pages -- a statement heading
            # must not classify note tables pages later), then fall back to
            # scanning the table's own text (covers tables whose heading is
            # in a caption row or in the first data row).
            heading_in_scope = (
                heading_page is not None
                and current_page - heading_page <= STATEMENT_HEADING_PAGE_SPAN
            )
            stmt_type = _detect_statement_type(heading_buffer) if heading_in_scope else "other"
            if stmt_type == "other":
                stmt_type = _detect_statement_type(unit_scan[:800])
            facts = _parse_structured_facts(element, units=active_units)

            for fact in facts:
                rows_to_insert.append(
                    {
                        **meta,
                        "doc_name": stem,
                        "statement_type": stmt_type,
                        "page_num": effective_page,
                        **fact,
                    }
                )

            # Prevent the traversal from re-visiting table cells as prose
            for child in element.find_all(True):
                child.decompose()
            continue

        # Page counter updates for non-table elements
        if use_markers:
            _s = _style_of(element)
            if ("page-break-before" in _s or "page-break-after" in _s) and "always" in _s:
                current_page += 1

        if element.name in ("p", "div", "span", "li") and element.find("table") is None:
            if element.find(["p", "div", "span", "li"]) is not None:
                continue
            text = element.get_text(" ", strip=True)
            if not text or len(text) < 2:
                continue
            # Update heading_buffer only when the prose element IS a statement
            # heading (starts with keyword) — not a cross-reference like
            # "See consolidated statement of cash flows."
            if len(text) < 300:
                _stmt = _text_is_statement_heading(text)
                if _stmt != "other":
                    heading_buffer = text
                    heading_page = current_page
            maybe_units = extract_unit_header(text)
            if maybe_units:
                active_units = maybe_units
            if not use_markers:
                word_count += len(text.split())
                current_page = max(1, word_count // WORDS_PER_PAGE_FALLBACK + 1)

    # --- Write to DB (idempotent: always delete existing rows first) ---
    # NOTE: DELETE runs even when rows_to_insert is empty so stale data
    # from prior runs is removed for filings that now parse to zero facts.
    _INSERT_SQL = """
        INSERT INTO financial_facts
            (doc_name, company, fiscal_year, fiscal_quarter, period_type,
             filing_type, statement_type, row_label, column_header,
             value_numeric, value_text, units, page_num)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM financial_facts WHERE doc_name = %s", (stem,))
            cur.executemany(
                _INSERT_SQL,
                [
                    (
                        r["doc_name"], r["company"], r["fiscal_year"],
                        r["fiscal_quarter"], r["period_type"], r["filing_type"],
                        r["statement_type"], r["row_label"], r["column_header"],
                        r["value_numeric"], r["value_text"], r["units"], r["page_num"],
                    )
                    for r in rows_to_insert
                ],
            )
        conn.commit()

    return len(rows_to_insert)
