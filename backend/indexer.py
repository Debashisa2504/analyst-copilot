"""
backend/indexer.py
-------------------
Manages the two parallel indexes (ADR-002):
  - dense vector store  (Azure PostgreSQL + pgvector, async query path)
  - sparse keyword store (BM25, pickled per filing, sync)

Both are queried independently by retriever.py and fused with RRF.
index_chunks() is synchronous (called from the ingest CLI and /upload endpoint).
query_dense()  is asynchronous (called from the FastAPI query path).
"""
from __future__ import annotations

import pickle
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from pgvector import HalfVector
from rank_bm25 import BM25Okapi

from .config import (
    AZURE_EMBED_BATCH_TOKEN_BUDGET, AZURE_EMBED_CHARS_PER_TOKEN, AZURE_EMBED_TPM_LIMIT,
    AZURE_EMBED_TPM_UTILIZATION, AZURE_EMBEDDING_DEPLOYMENT, AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION, AZURE_OPENAI_ENDPOINT, BM25_DIR, CHUNKS_TABLE,
    EMBEDDING_MODEL, EMBEDDING_PROVIDER,
)
from .db import get_sync_conn, get_async_conn, validate_table_name
from .models import Chunk

# PostgreSQL upsert batch size
_PG_BATCH = 500


def _upsert_sql(table: str) -> str:
    table = validate_table_name(table)
    return f"""
    INSERT INTO {table}
        (chunk_id, doc_name, page_num, page_num_method, chunk_type, units, section_type, text, embedding)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (chunk_id) DO UPDATE SET
        text      = EXCLUDED.text,
        embedding = EXCLUDED.embedding
"""


def _delete_sql(table: str) -> str:
    table = validate_table_name(table)
    return f"DELETE FROM {table} WHERE doc_name = %s"


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_embedding_model():
    """Lazily loads the local sentence-transformers model (EMBEDDING_PROVIDER=local only)."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBEDDING_MODEL)


def _embed_texts(texts: List[str]) -> List[List[float]]:
    if EMBEDDING_PROVIDER == "azure_openai":
        return _embed_azure(texts)
    model = get_embedding_model()
    return model.encode(texts, show_progress_bar=False).tolist()


def _estimate_tokens(text: str) -> int:
    """Cheap token-count estimate (no tokenizer dependency needed for pacing)."""
    return max(1, int(len(text) / AZURE_EMBED_CHARS_PER_TOKEN))


def _build_token_budget_batches(texts: List[str], token_budget: int) -> List[List[str]]:
    """
    Groups texts into batches whose estimated token sum stays under token_budget.
    Real chunk sizes vary widely post-Plan-A (a short footnote vs. an 800-word
    table section), so a fixed chunk-count-per-batch can no longer reliably
    predict request size the way it could when chunks were all small and atomic.
    A single oversized text becomes its own one-item batch rather than blowing
    the budget of a shared batch.
    """
    batches: List[List[str]] = []
    current: List[str] = []
    current_tokens = 0
    for text in texts:
        t = _estimate_tokens(text)
        if current and current_tokens + t > token_budget:
            batches.append(current)
            current, current_tokens = [], 0
        current.append(text)
        current_tokens += t
    if current:
        batches.append(current)
    return batches


def _embed_azure(texts: List[str]) -> List[List[float]]:
    """
    Calls Azure OpenAI embeddings API in token-budget-sized batches, paced to
    the configured TPM quota (see AZURE_EMBED_* in config.py), with rate-limit
    retry handling as a safety net on top of that pacing.
    """
    import time
    from openai import AzureOpenAI, RateLimitError

    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    embeddings: List[List[float]] = []
    batches = _build_token_budget_batches(texts, AZURE_EMBED_BATCH_TOKEN_BUDGET)
    safe_tpm = AZURE_EMBED_TPM_LIMIT * AZURE_EMBED_TPM_UTILIZATION
    delay_seconds = (AZURE_EMBED_BATCH_TOKEN_BUDGET / safe_tpm) * 60

    for i, batch in enumerate(batches):
        for attempt in range(10):
            try:
                response = client.embeddings.create(input=batch, model=AZURE_EMBEDDING_DEPLOYMENT)
                embeddings.extend(e.embedding for e in response.data)
                break
            except RateLimitError:
                print(f"[embed] Rate limit hit — waiting 30s before retry {attempt + 1}/10 ...")
                time.sleep(30)
        else:
            raise RuntimeError("Azure embedding rate limit exceeded after 10 retries.")
        if i + 1 < len(batches):
            time.sleep(delay_seconds)
    return embeddings


# ---------------------------------------------------------------------------
# Dense index — PostgreSQL + pgvector
# ---------------------------------------------------------------------------

def index_chunks(chunks: List[Chunk], doc_name: str, table: str = CHUNKS_TABLE) -> None:
    """
    Replaces a filing's chunks in PostgreSQL (dense) and rebuilds its BM25
    pickle (sparse). Synchronous — called from the ingest CLI and the
    /upload endpoint.

    Idempotent per doc_name: existing rows for doc_name are deleted before
    the new set is inserted, so re-ingesting a filing whose chunk count or
    chunk_id scheme changed (e.g. after a chunking-logic fix) can never
    leave stale orphaned rows behind -- it's always a clean replace, not a
    same-id-only upsert. Mirrors the delete-then-insert pattern
    facts_indexer.ingest_filing_facts() already uses for financial_facts.

    `table` defaults to config.CHUNKS_TABLE, so pointing CHUNKS_TABLE at an
    alternate table (e.g. "chunks_plan_a") redirects every caller here without
    touching call sites.
    """
    if not chunks:
        return

    texts = [c.text for c in chunks]
    embeddings = _embed_texts(texts)

    # ---- Dense (PostgreSQL + pgvector) ----
    rows = [
        (
            c.chunk_id,
            c.doc_name,
            c.page_num,
            c.page_num_method.value,
            c.chunk_type.value,
            c.units or None,
            c.section_type or "other",
            c.text,
            HalfVector(embeddings[i]),
        )
        for i, c in enumerate(chunks)
    ]

    delete_sql = _delete_sql(table)
    upsert_sql = _upsert_sql(table)
    with get_sync_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(delete_sql, (doc_name,))
            for start in range(0, len(rows), _PG_BATCH):
                cur.executemany(upsert_sql, rows[start : start + _PG_BATCH])
        conn.commit()

    # ---- Sparse (BM25) ----
    tokenized = [_tokenize(t) for t in texts]
    bm25 = BM25Okapi(tokenized)
    with open(_bm25_path(doc_name), "wb") as f:
        pickle.dump({"bm25": bm25, "chunks": chunks}, f)


async def query_dense(
    query: str, doc_name: str, top_k: int = 10, table: str = CHUNKS_TABLE
) -> List[Dict[str, Any]]:
    """
    Async cosine-similarity vector search against PostgreSQL, optionally scoped to one filing.
    The <=> operator is pgvector's cosine distance (0 = identical, 2 = opposite).

    `table` defaults to config.CHUNKS_TABLE (see index_chunks docstring).
    """
    vec = HalfVector(_embed_texts([query])[0])
    table = validate_table_name(table)

    if doc_name == "ALL":
        sql = (
            "SELECT chunk_id, text, doc_name, page_num, page_num_method, chunk_type, units, section_type, "
            f"embedding <=> %s AS distance "
            f"FROM {table} ORDER BY distance LIMIT %s"
        )
        params = (vec, top_k)
    else:
        sql = (
            "SELECT chunk_id, text, doc_name, page_num, page_num_method, chunk_type, units, section_type, "
            f"embedding <=> %s AS distance "
            f"FROM {table} WHERE doc_name = %s ORDER BY distance LIMIT %s"
        )
        params = (vec, doc_name, top_k)

    async with await get_async_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

    hits = []
    for chunk_id, text, doc, page, method, ctype, units, section_type, distance in rows:
        hits.append({
            "chunk_id": chunk_id,
            "text": text,
            "metadata": {
                "doc_name": doc,
                "page_num": page,
                "page_num_method": method or "unknown",
                "chunk_type": ctype or "prose",
                "units": units or "",
                "section_type": section_type or "other",
            },
            "distance": float(distance),
        })
    return hits


# ---------------------------------------------------------------------------
# Sparse index — BM25 (unchanged)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    # Extract word tokens only — strips punctuation so "property," == "property"
    # and query tokens like "equipment?" match chunk tokens like "equipment,".
    # Both index-time and query-time use this same function, keeping them aligned.
    import re
    return re.findall(r'\b\w+\b', text.lower())


def _bm25_path(doc_name: str) -> Path:
    return BM25_DIR / f"{doc_name}.pkl"


def _load_bm25_files(doc_name: str) -> List[Path]:
    if doc_name == "ALL":
        return list(BM25_DIR.glob("*.pkl"))
    path = _bm25_path(doc_name)
    return [path] if path.exists() else []


def query_bm25(query: str, doc_name: str, top_k: int = 10) -> List[Dict[str, Any]]:
    """Tokenized BM25 keyword search across one filing or the whole corpus."""
    tokens = _tokenize(query)
    hits: List[Dict[str, Any]] = []

    for path in _load_bm25_files(doc_name):
        if not path.exists():
            continue
        with open(path, "rb") as f:
            payload = pickle.load(f)
        bm25: BM25Okapi = payload["bm25"]
        chunks: List[Chunk] = payload["chunks"]
        scores = bm25.get_scores(tokens)
        for score, chunk in zip(scores, chunks):
            if score <= 0:
                continue
            hits.append({
                "chunk_id": chunk.chunk_id,
                "text": chunk.text,
                "metadata": {
                    "doc_name": chunk.doc_name,
                    "page_num": chunk.page_num,
                    "page_num_method": chunk.page_num_method.value,
                    "chunk_type": chunk.chunk_type.value,
                    "units": chunk.units or "",
                    "section_type": getattr(chunk, "section_type", None) or "other",
                },
                "score": float(score),
            })

    hits.sort(key=lambda h: h["score"], reverse=True)

    # Deduplicate by text — identical text from different chunk IDs wastes top-k slots.
    seen: set = set()
    deduped: list = []
    for h in hits:
        t = h["text"].strip()
        if t not in seen:
            seen.add(t)
            deduped.append(h)
    return deduped[:top_k]


def list_indexed_filings() -> List[str]:
    return sorted(p.stem for p in BM25_DIR.glob("*.pkl"))
