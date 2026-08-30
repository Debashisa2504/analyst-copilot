from backend.chunker import chunk_segments
from backend.config import TABLE_CHUNK_SIZE_WORDS
from backend.models import ChunkType, PageNumMethod, Segment


def _prose_segment(text, page=1):
    return Segment(text=text, page_num=page, page_num_method=PageNumMethod.WORD_COUNT,
                    segment_type=ChunkType.PROSE)


def _table_segment(text, page=1, section_type="other"):
    return Segment(text=text, page_num=page, page_num_method=PageNumMethod.MARKER,
                    is_table=True, segment_type=ChunkType.TABLE_ROW, section_type=section_type)


def test_table_facts_not_split():
    segments = [_prose_segment("word " * 10), _table_segment("Revenue, 2018: 100"),
                _prose_segment("word " * 10)]
    chunks = chunk_segments(segments, "DOC")
    table_chunks = [c for c in chunks if c.chunk_type == ChunkType.TABLE_ROW]
    assert len(table_chunks) == 1
    assert table_chunks[0].text == "Revenue, 2018: 100"


def test_prose_chunking_respects_size():
    segments = [_prose_segment("word " * 300), _prose_segment("word " * 300)]
    chunks = chunk_segments(segments, "DOC")
    assert len(chunks) >= 2


def test_chunk_metadata():
    segments = [_prose_segment("hello world", page=5)]
    chunks = chunk_segments(segments, "DOC")
    assert chunks[0].doc_name == "DOC"
    assert chunks[0].page_num == 5


def test_empty_filing():
    assert chunk_segments([], "DOC") == []


def test_same_section_rows_grouped_into_one_chunk():
    segments = [
        _table_segment("Revenue, 2018: 100", section_type="income_statement"),
        _table_segment("Net income, 2018: 20", section_type="income_statement"),
        _table_segment("Operating income, 2018: 30", section_type="income_statement"),
    ]
    chunks = chunk_segments(segments, "DOC")
    table_chunks = [c for c in chunks if c.chunk_type == ChunkType.TABLE_ROW]
    assert len(table_chunks) == 1
    assert table_chunks[0].section_type == "income_statement"
    assert table_chunks[0].text.startswith("[Income Statement]\n")
    assert "Revenue, 2018: 100" in table_chunks[0].text
    assert "Net income, 2018: 20" in table_chunks[0].text
    assert "Operating income, 2018: 30" in table_chunks[0].text


def test_section_change_flushes_chunk():
    segments = [
        _table_segment("Revenue, 2018: 100", section_type="income_statement"),
        _table_segment("Net cash from operations, 2018: 50", section_type="cash_flow"),
    ]
    chunks = chunk_segments(segments, "DOC")
    table_chunks = [c for c in chunks if c.chunk_type == ChunkType.TABLE_ROW]
    assert len(table_chunks) == 2
    assert table_chunks[0].section_type == "income_statement"
    assert table_chunks[1].section_type == "cash_flow"


def test_other_section_rows_not_labeled():
    segments = [_table_segment("Some row, 2018: 5", section_type="other")]
    chunks = chunk_segments(segments, "DOC")
    table_chunks = [c for c in chunks if c.chunk_type == ChunkType.TABLE_ROW]
    assert len(table_chunks) == 1
    assert table_chunks[0].text == "Some row, 2018: 5"


def test_table_section_flushes_on_word_budget():
    # Each row is ~30 words; force more than one flush within the same section.
    row_text = "Label " + ("word " * 30) + "2018: 100"
    segments = [
        _table_segment(row_text, section_type="income_statement")
        for _ in range(TABLE_CHUNK_SIZE_WORDS // 30 + 3)
    ]
    chunks = chunk_segments(segments, "DOC")
    table_chunks = [c for c in chunks if c.chunk_type == ChunkType.TABLE_ROW]
    assert len(table_chunks) >= 2
    for c in table_chunks:
        assert len(c.text.split()) <= TABLE_CHUNK_SIZE_WORDS + 40  # allow header/row slack
