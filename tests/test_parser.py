from backend.models import ChunkType
from backend.parser import (
    _classify_section,
    count_page_break_markers,
    extract_printed_page_number,
    extract_unit_header,
    normalize_accounting_number,
    parse_filing,
    parse_table_to_facts,
    is_numeric_text,
)
from bs4 import BeautifulSoup


def test_in_millions():
    assert extract_unit_header("(Amounts in millions, except per share data)") == "in millions"


def test_in_thousands():
    assert extract_unit_header("in thousands") == "in thousands"


def test_no_units():
    assert extract_unit_header("no scale info here") is None


def test_marker_count_with_breaks():
    html = '<div style="page-break-before:always"></div>' * 6
    soup = BeautifulSoup(html, "html.parser")
    assert count_page_break_markers(soup) == 6


def test_marker_count_zero():
    soup = BeautifulSoup("<div>no breaks</div>", "html.parser")
    assert count_page_break_markers(soup) == 0


def test_basic_table_serialization():
    html = """
    <table>
      <tr><td>Header</td><td>2018</td><td>2017</td></tr>
      <tr><td>Purchases of property, plant and equipment (PP&E)</td><td>(1,577)</td><td>(1,373)</td></tr>
    </table>
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    facts = parse_table_to_facts(table, "3M_2018_10K", units="in millions")
    assert "Purchases of property, plant and equipment (PP&E), 2018: -1577 (in millions)" in facts
    assert "Purchases of property, plant and equipment (PP&E), 2017: -1373 (in millions)" in facts


def test_negative_numbers_preserved():
    assert normalize_accounting_number("$(1,577)") == "-1577"
    assert normalize_accounting_number("(1,373)") == "-1373"


def test_numeric_detection():
    assert is_numeric_text("(1,577)")
    assert is_numeric_text("$1,577")
    assert not is_numeric_text("Purchases of property")


def test_printed_page_number():
    assert extract_printed_page_number("\n 60 \n") == 60
    assert extract_printed_page_number("see Page 59 for details") == 59


def test_parse_filing_integration():
    html = "<html><body><p>Some narrative text about risk factors.</p></body></html>"
    result = parse_filing(html, doc_name="TEST_10K")
    assert result.doc_name == "TEST_10K"
    assert len(result.segments) >= 1


def test_classify_section():
    assert _classify_section("Consolidated Statements of Income") == "income_statement"
    assert _classify_section("Consolidated Statements of Cash Flows") == "cash_flow"
    assert _classify_section("Consolidated Balance Sheets") == "balance_sheet"
    assert _classify_section("Notes to Financial Statements") == "other"


def test_classify_section_singular_forms():
    # Singular headings (e.g. PEPSICO-style "Statement of Income" instead of
    # "Statements of Income") must classify the same as their plural form.
    assert _classify_section("Statement of Income") == "income_statement"
    assert _classify_section("Consolidated Statement of Cash Flows") == "cash_flow"
    assert _classify_section("Consolidated Statement of Financial Position") == "balance_sheet"


def test_parse_filing_prose_heading_vs_cross_reference():
    # A short paragraph that merely REFERENCES a statement ("See ... for
    # detail") must not flip the active section -- only a paragraph that
    # itself starts with the statement name (a real bold-text heading) should.
    html = """
    <html><body>
      <p>See Consolidated Statement of Cash Flows for additional detail.</p>
      <table>
        <tr><td>Header</td><td>2018</td></tr>
        <tr><td>Some other row</td><td>1</td></tr>
      </table>
      <p><b>Consolidated Statement of Income</b></p>
      <table>
        <tr><td>Header</td><td>2018</td></tr>
        <tr><td>Revenue</td><td>100</td></tr>
      </table>
    </body></html>
    """
    result = parse_filing(html, doc_name="TEST_10K")
    table_segs = [s for s in result.segments if s.segment_type == ChunkType.TABLE_ROW]
    assert table_segs[0].section_type == "other"
    assert any(s.section_type == "income_statement" for s in table_segs)


def test_parse_filing_tags_table_rows_with_section_type():
    html = """
    <html><body>
      <h2>Consolidated Statements of Income</h2>
      <table>
        <tr><td>Header</td><td>2018</td></tr>
        <tr><td>Revenue</td><td>100</td></tr>
      </table>
      <h2>Consolidated Statements of Cash Flows</h2>
      <table>
        <tr><td>Header</td><td>2018</td></tr>
        <tr><td>Net cash from operations</td><td>50</td></tr>
      </table>
    </body></html>
    """
    result = parse_filing(html, doc_name="TEST_10K")
    table_segs = [s for s in result.segments if s.segment_type == ChunkType.TABLE_ROW]
    assert any(s.section_type == "income_statement" for s in table_segs)
    assert any(s.section_type == "cash_flow" for s in table_segs)
