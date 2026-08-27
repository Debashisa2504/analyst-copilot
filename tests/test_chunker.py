from backend.chunker import chunk_segments
from backend.models import ChunkType, PageNumMethod, Segment


def _prose_segment(text, page=1):
    return Segment(text=text, page_num=page, page_num_method=PageNumMethod.WORD_COUNT,
                    segment_type=ChunkType.PROSE)


def _table_segment(text, page=1):
    return Segment(text=text, page_num=page, page_num_method=PageNumMethod.MARKER,
                    is_table=True, segment_type=ChunkType.TABLE_ROW)


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
