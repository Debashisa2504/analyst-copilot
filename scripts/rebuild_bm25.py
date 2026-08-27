"""
scripts/rebuild_bm25.py
------------------------
Rebuilds all BM25 pickle files from chunks already stored in PostgreSQL.

NO embedding calls — just reads text from the DB and re-indexes BM25
with the fixed tokenizer (strips punctuation so "property," == "property").

Usage:
    python -m scripts.rebuild_bm25            # rebuild all filings
    python -m scripts.rebuild_bm25 --doc BOEING_2018_10K   # single filing

Run this whenever the _tokenize function changes.
"""
from __future__ import annotations

import argparse
import pickle
import sys
from collections import defaultdict
from pathlib import Path

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rank_bm25 import BM25Okapi

from backend.config import BM25_DIR
from backend.db import get_sync_conn
from backend.indexer import _tokenize
from backend.models import Chunk, ChunkType, PageNumMethod


_FETCH_SQL = """
    SELECT chunk_id, doc_name, page_num, page_num_method, chunk_type, units, text
    FROM chunks
    {where}
    ORDER BY doc_name, chunk_id
"""


def _fetch_chunks(doc_name: str | None = None) -> dict[str, list[Chunk]]:
    """Returns {doc_name: [Chunk, ...]} from PostgreSQL."""
    where = f"WHERE doc_name = '{doc_name}'" if doc_name else ""
    sql = _FETCH_SQL.format(where=where)

    by_doc: dict[str, list[Chunk]] = defaultdict(list)
    with get_sync_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            for row in cur.fetchall():
                chunk_id, doc, page, method, ctype, units, text = row
                chunk = Chunk(
                    chunk_id=chunk_id,
                    doc_name=doc,
                    page_num=page,
                    page_num_method=PageNumMethod(method or "unknown"),
                    chunk_type=ChunkType(ctype or "prose"),
                    units=units or None,
                    text=text,
                )
                by_doc[doc].append(chunk)
    return dict(by_doc)


def rebuild(doc_name: str | None = None) -> None:
    print(f"[rebuild_bm25] Fetching chunks from PostgreSQL ...")
    by_doc = _fetch_chunks(doc_name)

    if not by_doc:
        print("  No chunks found in DB", "for doc_name=" + doc_name if doc_name else "")
        return

    total_docs = len(by_doc)
    print(f"  Found {sum(len(v) for v in by_doc.values())} chunks across {total_docs} filings")

    for i, (doc, chunks) in enumerate(sorted(by_doc.items()), 1):
        texts = [c.text for c in chunks]
        tokenized = [_tokenize(t) for t in texts]
        bm25 = BM25Okapi(tokenized)
        path = BM25_DIR / f"{doc}.pkl"
        with open(path, "wb") as f:
            pickle.dump({"bm25": bm25, "chunks": chunks}, f)
        print(f"  [{i}/{total_docs}] {doc}: {len(chunks)} chunks → {path.name}")

    print("[rebuild_bm25] Done.")


def main():
    parser = argparse.ArgumentParser(description="Rebuild BM25 index from PostgreSQL chunks.")
    parser.add_argument("--doc", default=None, help="Rebuild only this doc_name (default: all)")
    args = parser.parse_args()
    rebuild(args.doc)


if __name__ == "__main__":
    main()
