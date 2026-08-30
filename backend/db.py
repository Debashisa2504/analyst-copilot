"""
backend/db.py
--------------
psycopg3 connection helpers and one-time schema setup for pgvector.

  get_sync_conn()   — for CLI scripts (ingest, migration) that have no event loop
  get_async_conn()  — for FastAPI query path (query_dense, etc.)
  setup_schema()    — idempotent: CREATE EXTENSION + TABLE + indexes
"""
from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector, register_vector_async

from .config import DATABASE_URL

# Matches text-embedding-3-large output dimension
EMBEDDING_DIM = 3072

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    doc_name        TEXT NOT NULL,
    page_num        INTEGER,
    page_num_method TEXT,
    chunk_type      TEXT,
    units           TEXT,
    text            TEXT NOT NULL,
    embedding       halfvec({EMBEDDING_DIM})
);
CREATE INDEX IF NOT EXISTS chunks_emb_idx
    ON chunks USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_doc_idx
    ON chunks (doc_name);
"""

_FACTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS financial_facts (
    id              BIGSERIAL PRIMARY KEY,
    doc_name        TEXT NOT NULL,
    company         TEXT NOT NULL,
    fiscal_year     INTEGER NOT NULL,
    fiscal_quarter  INTEGER,
    period_type     TEXT NOT NULL,
    filing_type     TEXT NOT NULL,
    statement_type  TEXT NOT NULL,
    row_label       TEXT NOT NULL,
    column_header   TEXT,
    value_numeric   NUMERIC,
    value_text      TEXT NOT NULL,
    units           TEXT,
    page_num        INTEGER
);
CREATE INDEX IF NOT EXISTS ff_lookup ON financial_facts (company, fiscal_year, statement_type);
CREATE INDEX IF NOT EXISTS ff_doc    ON financial_facts (doc_name, statement_type);
CREATE INDEX IF NOT EXISTS ff_label  ON financial_facts
    USING GIN (to_tsvector('english', row_label));
"""


def get_sync_conn() -> psycopg.Connection:
    """Open a synchronous psycopg3 connection with pgvector adapter registered."""
    conn = psycopg.connect(DATABASE_URL)
    register_vector(conn)
    return conn


async def get_async_conn() -> psycopg.AsyncConnection:
    """Open an async psycopg3 connection with pgvector adapter registered."""
    conn = await psycopg.AsyncConnection.connect(DATABASE_URL)
    await register_vector_async(conn)
    return conn


def setup_schema() -> None:
    """
    Creates the pgvector extension, chunks table, and financial_facts table
    if they don't exist. Safe to call multiple times (all statements use IF NOT EXISTS).

    NOTE: CREATE EXTENSION vector requires the extension to be allow-listed in
    Azure Portal → your PG server → Server parameters → azure.extensions → add 'vector'.
    """
    # CREATE EXTENSION must run outside a transaction on some PG versions
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        conn.execute(_SCHEMA_SQL)
        conn.execute(_FACTS_SCHEMA_SQL)
        conn.commit()

    print("[db] Schema ready (pgvector extension + chunks + financial_facts tables).")
