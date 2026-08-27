"""
backend/chunker.py
-------------------
Groups parsed Segments into searchable Chunks.

Prose is combined up to CHUNK_SIZE_WORDS with a CHUNK_OVERLAP_WORDS
sliding window so meaning isn't lost across a boundary. Table-row facts
and footnotes are atomic: they are never merged with prose or split,
because splitting a serialized fact ("Row, Col: Value") would recreate
the exact column-shifting problem the parser exists to prevent.

Exception: if an atomic or prose segment exceeds CHUNK_SIZE_WORDS (e.g.
a very long footnote or an unbroken wall of legal text), it is split on
word boundaries so individual chunks stay under the embedding API's
8192-token limit.
"""
from __future__ import annotations

from typing import List

from .config import CHUNK_SIZE_WORDS, CHUNK_OVERLAP_WORDS
from .models import Chunk, ChunkType, Segment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _word_split(text: str, max_words: int) -> List[str]:
    """Split text into parts of at most max_words words each."""
    words = text.split()
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


def _make_chunk(doc_name: str, chunk_id: str, seg: Segment, text: str, chunk_type: ChunkType) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        doc_name=doc_name,
        page_num=seg.page_num,
        page_num_method=seg.page_num_method,
        chunk_type=chunk_type,
        text=text,
        units=seg.units,
    )


def _emit_atomic(seg: Segment, doc_name: str, atomic_index: int, out: List[Chunk]) -> None:
    """Emit a TABLE_ROW or FOOTNOTE segment, splitting into parts if oversized."""
    parts = (
        _word_split(seg.text, CHUNK_SIZE_WORDS)
        if len(seg.text.split()) > CHUNK_SIZE_WORDS
        else [seg.text]
    )
    for i, part_text in enumerate(parts):
        suffix = f"_p{i}" if len(parts) > 1 else ""
        out.append(_make_chunk(
            doc_name,
            f"{doc_name}::{seg.segment_type.value}::{atomic_index}{suffix}",
            seg, part_text, seg.segment_type,
        ))


def _emit_oversized_prose(seg: Segment, doc_name: str, start_index: int, out: List[Chunk]) -> int:
    """Split an oversized prose segment and emit all parts. Returns the next chunk index."""
    for part_text in _word_split(seg.text, CHUNK_SIZE_WORDS):
        out.append(_make_chunk(
            doc_name, f"{doc_name}::prose::{start_index}", seg, part_text, ChunkType.PROSE,
        ))
        start_index += 1
    return start_index


# ---------------------------------------------------------------------------
# Prose buffer — encapsulates sliding-window state so chunk_segments stays simple
# ---------------------------------------------------------------------------

class _ProseBuffer:
    def __init__(self, doc_name: str) -> None:
        self._doc_name = doc_name
        self._segs: List[Segment] = []
        self._word_count: int = 0
        self._chunk_index: int = 0
        self.out: List[Chunk] = []

    @property
    def chunk_index(self) -> int:
        return self._chunk_index

    def set_chunk_index(self, value: int) -> None:
        self._chunk_index = value

    def would_overflow(self, words: int) -> bool:
        return bool(self._segs) and self._word_count + words > CHUNK_SIZE_WORDS

    def add(self, seg: Segment) -> None:
        self._segs.append(seg)
        self._word_count += len(seg.text.split())

    def flush(self) -> None:
        if not self._segs:
            return
        text = " ".join(s.text for s in self._segs)
        units = next((s.units for s in self._segs if s.units), None)
        self.out.append(Chunk(
            chunk_id=f"{self._doc_name}::prose::{self._chunk_index}",
            doc_name=self._doc_name,
            page_num=self._segs[0].page_num,
            page_num_method=self._segs[0].page_num_method,
            chunk_type=ChunkType.PROSE,
            text=text,
            units=units,
        ))
        self._chunk_index += 1
        self._roll_overlap()

    def _roll_overlap(self) -> None:
        """Keep the last CHUNK_OVERLAP_WORDS worth of segments for the next window."""
        kept, kept_words = [], 0
        for seg in reversed(self._segs):
            w = len(seg.text.split())
            if kept_words + w > CHUNK_OVERLAP_WORDS:
                break
            kept.insert(0, seg)
            kept_words += w
        self._segs = kept
        self._word_count = kept_words


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chunk_segments(segments: List[Segment], doc_name: str) -> List[Chunk]:
    buf = _ProseBuffer(doc_name)
    atomic_index = 0

    for seg in segments:
        if seg.segment_type in (ChunkType.TABLE_ROW, ChunkType.FOOTNOTE):
            buf.flush()
            _emit_atomic(seg, doc_name, atomic_index, buf.out)
            atomic_index += 1
            continue

        words = len(seg.text.split())
        if words > CHUNK_SIZE_WORDS:
            buf.flush()
            buf.set_chunk_index(_emit_oversized_prose(seg, doc_name, buf.chunk_index, buf.out))
            continue

        if buf.would_overflow(words):
            buf.flush()
        buf.add(seg)

    buf.flush()
    return buf.out
