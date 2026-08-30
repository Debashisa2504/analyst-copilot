"""
HTML/iXBRL parsing engine for SEC filings (10-K / 10-Q / 8-K).

Responsibilities (see ARD-001, ARD-004):
  1. DOM boilerplate cleanup.
  2. Dual-method page recovery: CSS page-break markers, with a
     word-count fallback, plus an anchor-offset correction derived
     from the first printed footer page number found in the document.
  3. Cell-by-cell table -> fact-sentence serialization, so a table
     row's label is never separated from its values across chunks.
  4. Footnote marker <-> footnote-definition linking.
"""
from __future__ import annotations

import re
from typing import List, Optional

from bs4 import BeautifulSoup, Tag

from .config import MIN_PAGE_BREAK_MARKERS, WORDS_PER_PAGE_FALLBACK
from .models import ParsedFiling, PageNumMethod, Segment, ChunkType
from .statement_utils import (
    STATEMENT_KEYWORDS as SECTION_KEYWORDS,
    detect_statement_type as _classify_section,
    text_is_statement_heading as _classify_section_strict,
)

# --------------------------------------------------------------------------
# Regexes
# --------------------------------------------------------------------------
_UNIT_HEADER_RE = re.compile(
    r"\(?\s*(?:amounts?\s+)?in\s+(millions|thousands|billions)"
    r"(?:,?\s*except[^)\n]*)?\)?",
    re.IGNORECASE,
)
_PRINTED_PAGE_RE = re.compile(r"(?:^|\n)\s*(\d{1,4})\s*(?:\n|$)")
_PAGE_WORD_RE = re.compile(r"\bpage\s+(\d{1,4})\b", re.IGNORECASE)
_FOOTNOTE_MARKER_RE = re.compile(r"\((\d{1,2})\)")
_FOOTNOTE_DEFINITION_RE = re.compile(
    r"^\s*\(?(\d{1,2})\)?\s*[.\-:]\s*(.+)$", re.MULTILINE
)
_ACCOUNTING_NUMBER_RE = re.compile(
    r"\$?\s*\(\s*([\d,]+(?:\.\d+)?)\s*\)|\(\s*([\d,]+(?:\.\d+)?)\s*\)"
)
_NUMERIC_RE = re.compile(r"^-?\$?\s*\(?\s*[\d,]+(?:\.\d+)?\s*\)?%?$")

# --------------------------------------------------------------------------
# Section detection (Plan A: section-aware table chunking)
# --------------------------------------------------------------------------
# SECTION_KEYWORDS / _classify_section (broad match, for genuine heading
# tags) / _classify_section_strict (starts-with match, for ambiguous short
# prose) all come from backend/statement_utils.py, shared with
# facts_indexer.py so the two systems never drift on section labels.

# Headings run a few words to a short line; a long paragraph that happens to
# contain one of the phrases above (e.g. an MD&A cross-reference) shouldn't
# flip the active section, so only short text is classified.
_HEADING_TEXT_MAX_CHARS = 200

# --------------------------------------------------------------------------
# SGML unwrapper
# --------------------------------------------------------------------------
def _extract_html_from_sgml(raw: str) -> str:
    """
    Some SEC EDGAR downloads are delivered as full-submission SGML text files
    that bundle the actual HTML filing inside <TEXT>...</TEXT> blocks.

    Handles three EDGAR packaging patterns:
      1. Normal (10-K/10-Q): one large HTML block inside <TEXT> → return it.
      2. Multi-doc (8-K + exhibits): multiple HTML blocks inside <TEXT>,
         one of which may be an XBRL interactive-viewer template (report.css).
         Filter the viewer out and concatenate the remaining content blocks.
      3. iXBRL (modern 8-K): actual filing content is in inline-XBRL <html>
         sections that appear OUTSIDE <TEXT> blocks in the SGML wrapper.
         The only <TEXT> block is the viewer shell.  Fall back to scanning
         the entire file for non-viewer <html> blocks.

    Returns the original string unchanged if no SGML wrapper is detected.
    """
    lower = raw.lower()
    # Fast guard: both markers must be present for an SGML wrapper
    if "<text>" not in lower or "<html" not in lower:
        return raw

    # --- Step 1: collect HTML blocks that live inside <TEXT>...</TEXT> ---
    html_blocks: List[str] = []
    pos = 0
    while True:
        tag_pos = lower.find("<text>", pos)
        if tag_pos == -1:
            break
        content_start = tag_pos + 6  # len("<text>")
        close_pos = lower.find("</text>", content_start)
        end = close_pos if close_pos != -1 else len(raw)
        block_lower = lower[content_start:end].lstrip()
        if block_lower.startswith("<html"):
            skip = (end - content_start) - len(block_lower)
            html_blocks.append(raw[content_start + skip : end])
        if close_pos == -1:
            break
        pos = close_pos + 7  # len("</text>")

    if not html_blocks:
        return raw

    # --- Step 2: filter XBRL interactive-viewer shells (report.css) ---
    # These contain JS/CSS rendering infrastructure, not filing content.
    _VIEWER = "report.css"
    content_blocks = [b for b in html_blocks if _VIEWER not in b[:600].lower()]

    if content_blocks:
        # One real content block → return it (typical 10-K/10-Q).
        # Multiple → 8-K with exhibit attachments; concatenate all.
        return "\n".join(content_blocks) if len(content_blocks) > 1 else content_blocks[0]

    # --- Step 3: all TEXT-block HTML was viewer shell (iXBRL pattern) ---
    # The actual filing lives in <html xmlns=...> sections OUTSIDE <TEXT>
    # blocks, embedded directly in the SGML outer wrapper.
    ixbrl_blocks: List[str] = []
    for m in re.finditer(r"<html[^>]*>", raw, re.IGNORECASE):
        start = m.start()
        end_tag = lower.find("</html>", start)
        end = (end_tag + 7) if end_tag != -1 else len(raw)
        block = raw[start:end]
        snippet = block[:400].lower()
        if "sec edgar submission" in snippet:   # outermost SGML wrapper
            continue
        if _VIEWER in snippet:                  # viewer shell
            continue
        ixbrl_blocks.append(block)

    if ixbrl_blocks:
        return "\n".join(ixbrl_blocks)

    # Last resort: return the largest TEXT-block HTML (original behaviour).
    return max(html_blocks, key=len)


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------
def _style_of(tag: Tag) -> str:
    """
    Safely reads a tag's inline style. Real-world SEC HTML occasionally
    produces Tag objects whose .attrs is None (malformed/self-closing
    markup quirks in html.parser) -- tag.get() would raise on those.
    """
    attrs = getattr(tag, "attrs", None) or {}
    return attrs.get("style", "") or ""


def count_page_break_markers(soup: BeautifulSoup) -> int:
    """Counts tags whose inline style contains a CSS page-break rule."""
    count = 0
    for tag in soup.find_all(style=True):
        style = _style_of(tag)
        if "page-break-before" in style or "page-break-after" in style:
            if "always" in style:
                count += 1
    return count


def extract_unit_header(text: str) -> Optional[str]:
    """Scans text for a scale indicator, e.g. '(in millions, except per share data)'."""
    match = _UNIT_HEADER_RE.search(text)
    if not match:
        return None
    return f"in {match.group(1).lower()}"


def extract_printed_page_number(text: str) -> Optional[int]:
    """
    Scans a block of text for a standalone printed page number, the kind
    that appears alone in a footer, e.g. '\\n 60 \\n', or 'Page 59'.
    """
    word_match = _PAGE_WORD_RE.search(text)
    if word_match:
        return int(word_match.group(1))

    for match in _PRINTED_PAGE_RE.finditer(text):
        value = int(match.group(1))
        # Guard against picking up stray numbers from tables (unlikely to
        # be a lone value 1-999 sitting on its own line otherwise).
        if 1 <= value <= 999:
            return value
    return None


def is_numeric_text(text: str) -> bool:
    """True if a table cell's text is a bare numeric/currency/percent value."""
    text = text.strip()
    if not text or text in {"-", "—", "–"}:
        return False
    return bool(_NUMERIC_RE.match(text))


def normalize_accounting_number(text: str) -> str:
    """
    Converts accounting-style parenthesized negatives to a signed string:
        '$(1,577)' -> '-1577'
        '(1,577)'  -> '-1577'
    Anything else is returned with commas stripped, unchanged sign.
    """
    match = _ACCOUNTING_NUMBER_RE.search(text)
    if match:
        val_str = match.group(1) or match.group(2)
        return f"-{val_str.replace(',', '')}"
    return text.strip().replace(",", "").replace("$", "")


# --------------------------------------------------------------------------
# Table serialization  (ARD-001)
# --------------------------------------------------------------------------
_SPACER_TEXT_RE = re.compile(r"^[\s\xa0]*[\$%]?[\s\xa0]*$")
_YEAR_TOKEN_RE = re.compile(r"\b(19|20)\d{2}\b")


def _cell_colspan(cell: Tag) -> int:
    raw = cell.get("colspan") if getattr(cell, "attrs", None) else None
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _row_cells_with_columns(row: Tag) -> List[tuple]:
    """
    Walks a <tr>'s cells and returns [(logical_col_index, text, colspan), ...],
    expanding colspan so column indices line up with the header row even
    when SEC HTML splits a single logical value into a '$' cell and a
    number cell (a two-column span under one colspan=2 header).
    """
    out = []
    col = 0
    for cell in row.find_all(["td", "th"]):
        span = _cell_colspan(cell)
        text = cell.get_text(" ", strip=True).replace("\xa0", " ").strip()
        out.append((col, text, span))
        col += span
    return out


def _is_spacer_cell(text: str) -> bool:
    """True for structural filler cells: blank, or a bare '$'/'%' with no digits."""
    return bool(_SPACER_TEXT_RE.match(text))


def _find_header_row(rows: List[Tag], max_scan: int = 6) -> Optional[Tag]:
    """
    Real SEC tables often have 1-3 blank spacer rows before the row that
    actually carries the period headers (e.g. '2018', '2017', 'Q3 2019').
    Scans the first few rows for one containing a year-like token.
    """
    for row in rows[:max_scan]:
        text = row.get_text(" ", strip=True)
        if _YEAR_TOKEN_RE.search(text):
            return row
    return None


def _build_header_intervals(header_row: Tag) -> List[tuple]:
    """Returns [(start_col, end_col_exclusive, header_text), ...] from a header row,
    respecting colspan so a single '2018' header covers both its '$' and
    value sub-columns."""
    intervals = []
    for col, text, span in _row_cells_with_columns(header_row):
        if text and not _is_spacer_cell(text):
            intervals.append((col, col + span, text))
    return intervals


def _lookup_header(intervals: List[tuple], col: int) -> Optional[str]:
    for start, end, text in intervals:
        if start <= col < end:
            return text
    return None


def parse_table_to_facts(
    table_tag: Tag, doc_name: str, units: Optional[str] = None
) -> List[str]:
    """
    Serializes a 2D HTML <table> into atomic fact sentences of the form:
        "[Row Label], [Column Header]: [Normalized Value] ([Units])"

    Real SEC EDGAR tables rarely map cleanly 1:1 from header cell to value
    cell: header cells commonly use colspan across a '$' cell and a number
    cell, and rows are padded with blank spacer <td>s for indentation and
    alignment. This function expands colspan into logical column indices
    on both the header and data rows so a value cell is matched to the
    header interval that actually contains it, and skips pure spacer /
    bare-currency-symbol cells so they never get misread as data.

    This is exactly what prevents the "structural grid fragmentation"
    failure mode: a row's label is never separated from its values, and a
    stray '$' cell never gets serialized as if it were a numeric fact.
    """
    rows = table_tag.find_all("tr")
    if not rows:
        return []

    header_row = _find_header_row(rows) or rows[0]
    header_intervals = _build_header_intervals(header_row)
    data_rows = [r for r in rows if r is not header_row]

    facts: List[str] = []
    for row in data_rows:
        cells = _row_cells_with_columns(row)
        if not cells:
            continue

        # Row label = first non-spacer cell (normally column 0).
        label_idx = next((i for i, (_, text, _) in enumerate(cells) if not _is_spacer_cell(text)), None)
        if label_idx is None:
            continue
        row_label = cells[label_idx][1]
        # A label-only row (section heading with no values on this row) is
        # legitimate and simply produces zero facts -- not an error.

        for col, text, _span in cells[label_idx + 1:]:
            if _is_spacer_cell(text):
                continue
            header_text = _lookup_header(header_intervals, col) if header_intervals else None
            col_header = header_text or f"col_{col}"
            if is_numeric_text(text):
                value = normalize_accounting_number(text)
            else:
                value = text
            unit_suffix = f" ({units})" if units else ""
            facts.append(f"{row_label}, {col_header}: {value}{unit_suffix}")

    return facts


# --------------------------------------------------------------------------
# Footnote linking
# --------------------------------------------------------------------------
def link_footnotes(text: str) -> dict:
    """
    Maps footnote callout markers, e.g. '(1)', found inline in prose to
    footnote *definition* blocks, e.g. lines starting with '1.' or '(1)'.
    Returns {marker_id: definition_text}.
    """
    definitions = {}
    for match in _FOOTNOTE_DEFINITION_RE.finditer(text):
        marker_id, body = match.group(1), match.group(2).strip()
        if len(body) > 15:  # avoid matching stray numbered list items
            definitions[marker_id] = body
    return definitions


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def parse_filing(
    html_content: str, doc_name: str, company: Optional[str] = None
) -> ParsedFiling:
    """
    Main parser entry point.
      1. Counts CSS page-break markers to choose the pagination method.
      2. Extracts the first printed footer page number and derives the
         anchor offset between electronic and printed pagination.
      3. Traverses the DOM top-to-bottom, serializing tables into fact
         sentences and prose into Segment objects, tracking page numbers
         and the active unit-scale header as it goes.
    """
    html_content = _extract_html_from_sgml(html_content)

    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    marker_count = count_page_break_markers(soup)
    use_markers = marker_count >= MIN_PAGE_BREAK_MARKERS
    page_num_method = PageNumMethod.MARKER if use_markers else PageNumMethod.WORD_COUNT

    segments: List[Segment] = []
    current_page = 1
    word_count = 0
    active_units: Optional[str] = None
    active_section = "other"
    footnote_defs = link_footnotes(soup.get_text(" ", strip=True)[:200_000])

    body = soup.body or soup

    # ------------------------------------------------------------------
    # Pre-scan pass: detect the printed→electronic page offset BEFORE
    # emitting any segments.  (Option D)
    #
    # Walk all <div> elements, tracking the electronic page counter via
    # CSS page-break markers.  For each electronic page, record the LAST
    # <div> whose entire stripped text is a bare 1-4 digit number
    # (overwriting on each match → "last wins" per page).
    #
    # Then find the first run of 3 consecutive electronic pages whose
    # last-numbers are also consecutive (N, N+1, N+2).  Real footer page
    # numbers form this monotonic pattern; TOC references (scattered,
    # non-sequential across pages) and iXBRL data values (also non-
    # sequential as a page-by-page series) do not.  This makes the
    # Boeing iXBRL false-positive (<div>14</div> at electronic page 2)
    # harmless because it never participates in a 3-page run.
    # ------------------------------------------------------------------
    _SOLO_NUM_RE = re.compile(r'^\d{1,4}$')
    _page_last_num: dict = {}   # {electronic_page: int}
    _p = 1
    for _el in (el for el in body.descendants if isinstance(el, Tag)):
        if use_markers:
            _s = _style_of(_el)
            if ("page-break-before" in _s or "page-break-after" in _s) and "always" in _s:
                _p += 1
        if _el.name == "div":
            _t = _el.get_text(" ", strip=True)
            if _t and _SOLO_NUM_RE.match(_t):
                _page_last_num[_p] = int(_t)   # overwrite → keeps LAST seen per page

    offset = 0
    offset_locked = False
    _pages = sorted(_page_last_num.keys())
    for _ep in _pages[:-2]:
        _n0 = _page_last_num[_ep]
        _n1 = _page_last_num.get(_ep + 1)
        _n2 = _page_last_num.get(_ep + 2)
        if _n1 == _n0 + 1 and _n2 == _n0 + 2:
            offset = _n0 - _ep
            offset_locked = True
            break

    def bump_page_from_markers(tag: Tag):
        nonlocal current_page
        style = _style_of(tag)
        if ("page-break-before" in style or "page-break-after" in style) and "always" in style:
            current_page += 1

    # IMPORTANT: snapshot the descendants into a plain list before the loop.
    # `.descendants` is a live generator walking the tree via next-element
    # pointers; decompose()-ing nodes *during* that walk (done below, to
    # stop a processed table's cells being re-read as prose) corrupts those
    # pointers and silently truncates the rest of the document from ever
    # being visited. Iterating a materialized list sidesteps that.
    all_elements = [el for el in body.descendants if isinstance(el, Tag)]

    for element in all_elements:
        if element.decomposed:
            # Already removed as part of an earlier leaf-table's cleanup.
            continue

        if element.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            heading_text = element.get_text(" ", strip=True)
            section = _classify_section(heading_text)
            if section != "other":
                active_section = section
            continue

        if element.name == "table":
            # SEC EDGAR HTML routinely wraps the *entire page* in an outer
            # <table> used purely for layout, with the real data tables
            # nested inside it. Flattening that outer table would merge
            # every row on the page into one nonsensical "table" and (via
            # the decompose step) delete the real nested tables before
            # they're ever visited. So: only serialize a table as data if
            # it is a *leaf* -- no <table> nested inside it. Layout tables
            # are left alone and the traversal simply continues into them.
            if element.find("table") is not None:
                continue

            unit_scan = element.get_text(" ", strip=True)
            table_units = extract_unit_header(unit_scan) or active_units
            if table_units:
                active_units = table_units
            facts = parse_table_to_facts(element, doc_name, units=active_units)
            for fact in facts:
                effective_page = current_page + offset
                segments.append(
                    Segment(
                        text=fact,
                        page_num=effective_page,
                        page_num_method=page_num_method,
                        is_table=True,
                        table_html=None,
                        units=active_units,
                        segment_type=ChunkType.TABLE_ROW,
                        section_type=active_section,
                    )
                )
            # Prevent descending into this leaf table's cells again as prose.
            for child in element.find_all(True):
                child.decompose()
            continue

        if use_markers:
            bump_page_from_markers(element)

        # Only capture TRUE leaf text elements — skip any container that has child
        # p/div/span/li elements, because those children will be visited separately
        # and produce the same text, causing duplicate segments/chunks.
        if element.name in ("p", "div", "span", "li") and element.find("table") is None:
            # Skip containers: child prose tags OR iXBRL wrapper tags mean this
            # element's text will be visited again in the child iteration.
            if element.find(["p", "div", "span", "li",
                             "ix:nonfraction", "ix:nonnumeric", "ix:fraction"]) is not None:
                continue
            text = element.get_text(" ", strip=True)
            if not text or len(text) < 2:
                continue

            if len(text) <= _HEADING_TEXT_MAX_CHARS:
                # Strict "starts with" match here (not the broad h-tag match
                # above): a bold <p> heading should flip the active section,
                # but a narrative cross-reference like "See consolidated
                # statement of cash flows." must not.
                section = _classify_section_strict(text)
                if section != "other":
                    active_section = section

            maybe_page_units = extract_unit_header(text)
            if maybe_page_units:
                active_units = maybe_page_units

            if not use_markers:
                word_count += len(text.split())
                current_page = max(1, word_count // WORDS_PER_PAGE_FALLBACK + 1)

            effective_page = current_page + offset

            marker_ids = _FOOTNOTE_MARKER_RE.findall(text)
            footnote_id = marker_ids[0] if marker_ids and marker_ids[0] in footnote_defs else None

            segments.append(
                Segment(
                    text=text,
                    page_num=effective_page,
                    page_num_method=page_num_method,
                    is_table=False,
                    footnote_id=footnote_id,
                    units=active_units,
                    segment_type=ChunkType.FOOTNOTE if footnote_id else ChunkType.PROSE,
                )
            )

    return ParsedFiling(
        doc_name=doc_name,
        company=company,
        segments=segments,
        page_offset=offset,
        page_num_method=page_num_method,
    )
