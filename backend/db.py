"""
backend/db.py
--------------
psycopg3 connection helpers and one-time schema setup for pgvector.

  get_sync_conn()   — for CLI scripts (ingest, migration) that have no event loop
  get_async_conn()  — for FastAPI query path (query_dense, etc.)
  setup_schema()    — idempotent: CREATE EXTENSION + TABLE + indexes
"""
from __future__ import annotations

import re

import psycopg
from pgvector.psycopg import register_vector, register_vector_async

from .config import CHUNKS_TABLE, DATABASE_URL

# Matches text-embedding-3-large output dimension
EMBEDDING_DIM = 3072

# Table names are interpolated directly into SQL below (identifiers can't be
# passed as psycopg placeholders), so every caller-supplied table name is
# validated against this allowlist pattern first.
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_table_name(table: str) -> str:
    if not _TABLE_NAME_RE.match(table):
        raise ValueError(f"Invalid table name: {table!r}")
    return table


def _schema_sql(table: str) -> str:
    table = validate_table_name(table)
    return f"""
CREATE TABLE IF NOT EXISTS {table} (
    chunk_id        TEXT PRIMARY KEY,
    doc_name        TEXT NOT NULL,
    page_num        INTEGER,
    page_num_method TEXT,
    chunk_type      TEXT,
    units           TEXT,
    section_type    TEXT DEFAULT 'other',
    text            TEXT NOT NULL,
    embedding       halfvec({EMBEDDING_DIM})
);
ALTER TABLE {table} ADD COLUMN IF NOT EXISTS section_type TEXT DEFAULT 'other';
CREATE INDEX IF NOT EXISTS {table}_emb_idx
    ON {table} USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX IF NOT EXISTS {table}_doc_idx
    ON {table} (doc_name);
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


def setup_schema(table: str = CHUNKS_TABLE) -> None:
    """
    Creates the pgvector extension and the chunks table (named `table`,
    defaulting to config.CHUNKS_TABLE) if they don't exist.
    Safe to call multiple times (all statements use IF NOT EXISTS).

    Pass an alternate table name (e.g. "chunks_plan_a") to stand up a second,
    fully isolated corpus alongside the production "chunks" table -- useful
    for A/B comparing a re-chunking strategy without touching the original.

    NOTE: CREATE EXTENSION vector requires the extension to be allow-listed in
    Azure Portal → your PG server → Server parameters → azure.extensions → add 'vector'.
    """
    # CREATE EXTENSION must run outside a transaction on some PG versions
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        conn.execute(_schema_sql(table))
        conn.commit()

    print(f"[db] Schema ready (pgvector extension + '{table}' table).")
