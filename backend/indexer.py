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
    AZURE_EMBEDDING_DEPLOYMENT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_ENDPOINT, BM25_DIR, CHUNKS_TABLE, EMBEDDING_MODEL, EMBEDDING_PROVIDER,
)
from .db import get_sync_conn, get_async_conn, validate_table_name
from .models import Chunk

# Azure OpenAI embedding batch size.
# 512 chunks × ~45 tokens/chunk ≈ 23K tokens per batch (well under 150K TPM).
_AZURE_EMBED_BATCH = 512

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


def _embed_azure(texts: List[str]) -> List[List[float]]:
    """Calls Azure OpenAI embeddings API in safe batches with rate-limit handling."""
    import time
    from openai import AzureOpenAI, RateLimitError

    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    embeddings: List[List[float]] = []
    for start in range(0, len(texts), _AZURE_EMBED_BATCH):
        batch = texts[start : start + _AZURE_EMBED_BATCH]
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
        if start + _AZURE_EMBED_BATCH < len(texts):
            # 23K tokens per batch; 150K TPM → need ~9s minimum between batches.
            time.sleep(10)
    return embeddings


# ---------------------------------------------------------------------------
# Dense index — PostgreSQL + pgvector
# ---------------------------------------------------------------------------

def index_chunks(chunks: List[Chunk], doc_name: str, table: str = CHUNKS_TABLE) -> None:
    """
    Upserts chunks into PostgreSQL (dense) and rebuilds the per-filing BM25 pickle (sparse).
    Synchronous — called from the ingest CLI and the /upload endpoint.

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

    upsert_sql = _upsert_sql(table)
    with get_sync_conn() as conn:
        with conn.cursor() as cur:
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
                    "section_type": chunk.section_type or "other",
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
