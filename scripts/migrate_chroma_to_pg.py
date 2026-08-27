"""
scripts/migrate_chroma_to_pg.py
---------------------------------
One-time migration: copies all vectors from local ChromaDB into Azure PostgreSQL.
No re-embedding — vectors are read as-is and inserted via pgvector.

Usage (from project root):
    python -m scripts.migrate_chroma_to_pg

Prerequisites:
  1. DATABASE_URL set in .env (Azure PostgreSQL Flexible Server)
  2. pgvector extension enabled in Azure Portal →
       your PG server → Server parameters → azure.extensions → add 'vector'
  3. pip install -r requirements.txt (psycopg[binary], pgvector already included)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pgvector import HalfVector
from backend.config import CHROMA_DIR, CHROMA_COLLECTION_NAME
from backend.db import get_sync_conn, setup_schema

_BATCH = 500  # rows per INSERT batch


def migrate() -> None:
    import chromadb

    print("[migrate] Connecting to local ChromaDB ...")
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        collection = client.get_collection(CHROMA_COLLECTION_NAME)
    except Exception as exc:
        print(f"[migrate] ERROR: could not open ChromaDB collection: {exc}")
        sys.exit(1)

    total = collection.count()
    print(f"[migrate] {total} chunks to migrate → Azure PostgreSQL.")

    if total == 0:
        print("[migrate] Nothing to migrate. Exiting.")
        return

    # Ensure schema exists
    setup_schema()

    conn = get_sync_conn()
    try:
        migrated = 0
        offset = 0

        while offset < total:
            result = collection.get(
                limit=_BATCH,
                offset=offset,
                include=["embeddings", "documents", "metadatas"],
            )
            ids = result["ids"]
            if not ids:
                break

            rows = []
            for i, chunk_id in enumerate(ids):
                meta = result["metadatas"][i]
                rows.append((
                    chunk_id,
                    meta.get("doc_name", ""),
                    meta.get("page_num", 0),
                    meta.get("page_num_method", "unknown"),
                    meta.get("chunk_type", "prose"),
                    meta.get("units") or None,
                    result["documents"][i],
                    HalfVector(result["embeddings"][i]),
                ))

            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO chunks
                        (chunk_id, doc_name, page_num, page_num_method,
                         chunk_type, units, text, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (chunk_id) DO NOTHING
                    """,
                    rows,
                )
            conn.commit()

            migrated += len(ids)
            offset += _BATCH
            pct = migrated / total * 100
            print(f"[migrate] {migrated}/{total} ({pct:.0f}%) chunks migrated ...")

        print(f"\n[migrate] Done. {migrated} chunks now in Azure PostgreSQL.")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
