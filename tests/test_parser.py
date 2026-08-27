from backend.parser import (
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
