"""
backend/models.py
------------------
Pydantic schemas and enums shared across the ingestion, retrieval and
answering pipeline. Everything downstream of parsing is typed against
these models so the FastAPI layer can serialize them directly.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class PageNumMethod(str, Enum):
    MARKER = "marker"           # CSS page-break markers were reliable
    WORD_COUNT = "word_count"   # fell back to word-count pagination
    UNKNOWN = "unknown"


class ChunkType(str, Enum):
    PROSE = "prose"
    TABLE = "table"
    TABLE_ROW = "table_row"
    FOOTNOTE = "footnote"


# --------------------------------------------------------------------------
# Parsing layer
# --------------------------------------------------------------------------
class Segment(BaseModel):
    """A single unit of text produced by the parser, before chunking."""
    text: str
    page_num: int
    page_num_method: PageNumMethod = PageNumMethod.UNKNOWN
    is_table: bool = False
    table_html: Optional[str] = None
    footnote_id: Optional[str] = None
    units: Optional[str] = None
    segment_type: ChunkType = ChunkType.PROSE
    section_type: str = "other"   # 'income_statement' | 'cash_flow' | 'balance_sheet' | 'other'


class ParsedFiling(BaseModel):
    doc_name: str
    company: Optional[str] = None
    segments: List[Segment] = Field(default_factory=list)
    page_offset: int = 0
    page_num_method: PageNumMethod = PageNumMethod.UNKNOWN


# --------------------------------------------------------------------------
# Chunking / indexing layer
# --------------------------------------------------------------------------
class Chunk(BaseModel):
    chunk_id: str
    doc_name: str
    page_num: int
    page_num_method: PageNumMethod = PageNumMethod.UNKNOWN
    chunk_type: ChunkType = ChunkType.PROSE
    text: str
    units: Optional[str] = None
    section_type: str = "other"   # 'income_statement' | 'cash_flow' | 'balance_sheet' | 'other'


class RetrievedChunk(BaseModel):
    chunk: Chunk
    dense_rank: Optional[int] = None
    bm25_rank: Optional[int] = None
    score: float = 0.0
    dual_agreement: bool = False


class RetrievalResult(BaseModel):
    query: str
    expanded_query: str
    chunks: List[RetrievedChunk] = Field(default_factory=list)
    agreement_ratio: float = 0.0


# --------------------------------------------------------------------------
# QA / API layer
# --------------------------------------------------------------------------
class QuestionRequest(BaseModel):
    question: str
    doc_name: str = "ALL"
    top_k: int = 10


class EvidenceItem(BaseModel):
    text: str
    page_num: int
    doc_name: str
    chunk_type: ChunkType = ChunkType.PROSE


class DraftAnswer(BaseModel):
    answer: str
    value: Optional[str] = None
    page_num: Optional[int] = None
    confidence: float = 0.0
    rationale: Optional[str] = None
    found: bool = True  # LLM declares False when excerpts lack the needed data

    @field_validator("value", "answer", mode="before")
    @classmethod
    def coerce_to_str(cls, v):
        if v is None:
            return v
        return str(v)

    @field_validator("page_num", mode="before")
    @classmethod
    def coerce_page_num(cls, v):
        if isinstance(v, list):
            return int(v[0]) if v else None
        return v


class VerifyResult(BaseModel):
    verified: bool
    correct_value: Optional[str] = None
    correct_page: Optional[int] = None
    confidence: float = 0.0
    issues: List[str] = Field(default_factory=list)

    @field_validator("correct_value", mode="before")
    @classmethod
    def coerce_value_to_str(cls, v):
        if v is None:
            return v
        return str(v)

    @field_validator("correct_page", mode="before")
    @classmethod
    def coerce_correct_page(cls, v):
        if isinstance(v, list):
            return int(v[0]) if v else None
        return v


class AnswerResponse(BaseModel):
    answer: str
    doc_name: str
    page_num: Optional[int] = None
    abstained: bool = False
    confidence: float = 0.0
    retrieval_agreement: float = 0.0
    evidence: List[EvidenceItem] = Field(default_factory=list)
    evidence_text: Optional[str] = None  # convenience: joined evidence for the UI
