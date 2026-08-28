"""
backend/retriever.py
---------------------
Pre-retrieval financial query expansion + hybrid dense/BM25 fusion via
Reciprocal Rank Fusion (ADR-002, ADR-005).

Query pipeline (§4.2):
  1. GAAP synonym expansion (BM25 side only — dense stays semantically clean)
  2. Self-repair expansions from past rephrase events
  3. Query classifier: simple | temporal | cross_company | complex
  4. Routing:
       simple       -> structured SQLite cache fast-path (<100ms, no RAG)
       cross_company -> per-filing RAG via asyncio.gather (parallel)
       temporal/complex -> hybrid RAG
  5. Retrieval memory weights applied after RRF fusion
"""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from .config import CONTEXT_TOP_K, DUAL_AGREEMENT_MULTIPLIER, RETRIEVAL_TOP_K, RRF_K
from .indexer import query_bm25, query_dense, list_indexed_filings
from .models import Chunk, ChunkType, PageNumMethod, RetrievalResult, RetrievedChunk
from .cache import lookup_metric, COMMON_METRICS
from .learning import get_chunk_weight
from .learning.query_patterns import classify_query, find_similar_pattern
from .learning.self_repair import get_repair_expansions

# GAAP synonym expansion dictionary (BM25-only; §3, ADR-005)
FINANCIAL_SYNONYMS: Dict[str, str] = {
    "capital expenditure": "Purchases of property, plant and equipment PP&E capital spending",
    "capex": "Purchases of property, plant and equipment PP&E capital spending",
    "operating cash flow": "Net cash provided by operating activities cash flows from operating activities",
    "free cash flow": "Net cash provided by operating activities purchases of property plant and equipment",
    "revenue": "Net sales Total revenue Revenues",
    "gross profit": "Gross margin Gross profit Net sales Cost of sales",
    "operating income": "Operating income Income from operations",
    "net income": "Net income Net earnings Net income including noncontrolling interest",
    "total debt": "Short-term debt Long-term debt Obligations",
    "eps": "Earnings per share diluted basic net income per share",
    "gross margin": "Gross profit Net sales Cost of goods sold gross margin percentage",
    "r&d": "Research and development expense",
    # Balance sheet items — common FinanceBench question targets
    "ppne": "property plant and equipment net PP&E",
    "net ppne": "property plant and equipment net PP&E",
    "property, plant": "property plant and equipment PP&E net",
    "current assets": "Total current assets cash inventories receivable",
    "total current assets": "Total current assets cash inventories receivable",
    "depreciation and amortization": "depreciation amortization D&A cash flows",
    "ebitda": "operating income depreciation amortization EBITDA",
}

# Map simple query keywords to cache metric keys
_METRIC_KEY_MAP = {
    "revenue": "revenue", "net sales": "revenue", "total revenue": "revenue",
    "net income": "net_income", "net earnings": "net_income",
    "capex": "capex", "capital expenditure": "capex",
    "operating income": "operating_income",
    "gross profit": "gross_profit",
    "eps": "eps_diluted", "earnings per share": "eps_diluted",
}


def expand_query(query: str) -> str:
    """Expands with GAAP synonyms + learned self-repair terms (BM25 side only)."""
    lower = query.lower()
    extras = [exp for term, exp in FINANCIAL_SYNONYMS.items() if term in lower]

    # Learned expansions from past rephrase events
    repair = get_repair_expansions(query)
    extras.extend(repair)

    return f"{query} {' '.join(extras)}" if extras else query


def _try_cache_fast_path(question: str, doc_name: str) -> Optional[RetrievalResult]:
    """
    For 'simple' queries: look up the answer in the structured SQLite cache.
    Returns a synthetic RetrievalResult with a single cache-hit chunk, or None.
    """
    if doc_name == "ALL":
        return None  # Cache only supports single-filing lookups

    lower = question.lower()
    metric_key = None
    for term, key in _METRIC_KEY_MAP.items():
        if term in lower:
            metric_key = key
            break

    if not metric_key:
        return None

    row = lookup_metric(doc_name, metric_key)
    if not row or not row.get("value"):
        return None

    # Synthesize a chunk from the cached value
    units = row.get("units") or ""
    period = row.get("period") or ""
    text = (
        f"{metric_key.replace('_', ' ').title()}"
        f"{', ' + period if period else ''}: {row['value']}"
        f"{' (' + units + ')' if units else ''} [from structured cache]"
    )
    chunk = Chunk(
        chunk_id=f"{doc_name}::cache::{metric_key}",
        doc_name=doc_name,
        page_num=row.get("page_num") or 0,
        page_num_method=PageNumMethod.UNKNOWN,
        chunk_type=ChunkType.TABLE_ROW,
        text=text,
        units=units or None,
    )
    rc = RetrievedChunk(chunk=chunk, score=2.0, dual_agreement=True)
    return RetrievalResult(
        query=question,
        expanded_query=question,
        chunks=[rc],
        agreement_ratio=1.0,
    )


def _to_chunk(hit: dict) -> Chunk:
    meta = hit["metadata"]
    return Chunk(
        chunk_id=hit["chunk_id"],
        doc_name=meta["doc_name"],
        page_num=meta["page_num"],
        page_num_method=PageNumMethod(meta.get("page_num_method", "unknown")),
        chunk_type=ChunkType(meta.get("chunk_type", "prose")),
        text=hit["text"],
        units=meta.get("units") or None,
    )


async def _hybrid_retrieve(query: str, doc_name: str, top_k: int) -> RetrievalResult:
    """Core hybrid RRF retrieval for a single doc_name scope."""
    expanded = expand_query(query)

    dense_hits = await query_dense(query, doc_name, top_k=top_k)   # semantic: unexpanded, async
    bm25_hits = query_bm25(expanded, doc_name, top_k=top_k)         # keyword: expanded, sync

    dense_rank = {h["chunk_id"]: i for i, h in enumerate(dense_hits)}
    bm25_rank = {h["chunk_id"]: i for i, h in enumerate(bm25_hits)}

    by_id: Dict[str, dict] = {}
    for h in dense_hits:
        by_id[h["chunk_id"]] = h
    for h in bm25_hits:
        by_id.setdefault(h["chunk_id"], h)

    scored: List[RetrievedChunk] = []
    for chunk_id, hit in by_id.items():
        d_rank = dense_rank.get(chunk_id)
        b_rank = bm25_rank.get(chunk_id)
        rrf = 0.0
        if d_rank is not None:
            rrf += 1.0 / (RRF_K + d_rank + 1)
        if b_rank is not None:
            rrf += 1.0 / (RRF_K + b_rank + 1)

        dual = d_rank is not None and b_rank is not None
        base_score = rrf * DUAL_AGREEMENT_MULTIPLIER if dual else rrf

        # Apply retrieval memory weight (§6.3)
        mem_weight = get_chunk_weight(chunk_id)
        final_score = base_score * mem_weight

        scored.append(RetrievedChunk(
            chunk=_to_chunk(hit),
            dense_rank=d_rank,
            bm25_rank=b_rank,
            score=final_score,
            dual_agreement=dual,
        ))

    scored.sort(key=lambda rc: rc.score, reverse=True)

    # Deduplicate by text: identical text in multiple chunks (parser/chunking artefact)
    # wastes top-k slots. Keep only the highest-scoring chunk per unique text.
    seen_texts: set = set()
    deduped: list = []
    for rc in scored:
        t = rc.chunk.text.strip()
        if t not in seen_texts:
            seen_texts.add(t)
            deduped.append(rc)
    scored = deduped

    overlap = len(set(dense_rank) & set(bm25_rank))
    denom = min(len(dense_rank), len(bm25_rank)) or 1

    return RetrievalResult(
        query=query,
        expanded_query=expanded,
        chunks=scored[:top_k],
        agreement_ratio=overlap / denom,
    )


async def retrieve(
    query: str, doc_name: str = "ALL", top_k: int = RETRIEVAL_TOP_K
) -> RetrievalResult:
    """
    Full async query pipeline:
      1. Classify query type.
      2. Route: simple → cache, cross_company → parallel RAG, else → hybrid RAG.
    """
    q_type = classify_query(query)

    # Simple: try cache fast-path first
    if q_type == "simple" and doc_name != "ALL":
        cached = _try_cache_fast_path(query, doc_name)
        if cached:
            return cached

    # Cross-company: query all filings concurrently (asyncio.gather) then merge
    if q_type == "cross_company" or doc_name == "ALL":
        filings = list_indexed_filings()
        if not filings:
            return RetrievalResult(query=query, expanded_query=query, chunks=[], agreement_ratio=0.0)

        per_k = max(3, top_k // len(filings))
        # Semaphore limits concurrent DB connections (avoid overwhelming the PG server)
        sem = asyncio.Semaphore(5)

        async def _limited(filing: str) -> RetrievalResult:
            async with sem:
                return await _hybrid_retrieve(query, filing, top_k=per_k)

        results = await asyncio.gather(*[_limited(f) for f in filings])
        per_filing: List[RetrievedChunk] = []
        for r in results:
            per_filing.extend(r.chunks)

        per_filing.sort(key=lambda rc: rc.score, reverse=True)
        return RetrievalResult(
            query=query,
            expanded_query=expand_query(query),
            chunks=per_filing[:top_k],
            agreement_ratio=0.0,  # not meaningful across filings
        )

    # Temporal + complex: standard hybrid RAG
    return await _hybrid_retrieve(query, doc_name, top_k)


def top_context(result: RetrievalResult, n: int = CONTEXT_TOP_K) -> List[RetrievedChunk]:
    return result.chunks[:n]
