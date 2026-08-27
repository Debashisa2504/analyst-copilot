"""
scripts/dedupe_chunks.py
-------------------------
Removes duplicate chunks from PostgreSQL (same doc_name + text, different chunk_id)
caused by the parser visiting nested DOM elements (div→p→span) multiple times.

Keeps the chunk with the lowest chunk_id (first ingested) for each unique
(doc_name, text) pair and deletes all duplicates.

NO re-embedding needed — reads and writes only the chunks table.

After running, call rebuild_bm25 to regenerate BM25 pickles from the clean data:
    python -m scripts.dedupe_chunks
    python -m scripts.rebuild_bm25

Usage:
    python -m scripts.dedupe_chunks            # dry run first (shows counts)
    python -m scripts.dedupe_chunks --apply    # actually deletes duplicates
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_sync_conn


_COUNT_SQL = """
    SELECT COUNT(*) FROM chunks
    WHERE chunk_id NOT IN (
        SELECT MIN(chunk_id)
        FROM chunks
        GROUP BY doc_name, text
    )
"""

_DELETE_SQL = """
    DELETE FROM chunks
    WHERE chunk_id NOT IN (
        SELECT MIN(chunk_id)
        FROM chunks
        GROUP BY doc_name, text
    )
"""

_TOTAL_SQL = "SELECT COUNT(*) FROM chunks"

_PER_DOC_SQL = """
    SELECT doc_name,
           COUNT(*) AS total,
           COUNT(*) - COUNT(DISTINCT text) AS duplicates
    FROM chunks
    GROUP BY doc_name
    HAVING COUNT(*) - COUNT(DISTINCT text) > 0
    ORDER BY duplicates DESC
    LIMIT 20
"""


def main():
    parser = argparse.ArgumentParser(description="Deduplicate chunks in PostgreSQL.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete duplicates (default: dry run only)")
    args = parser.parse_args()

    with get_sync_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_TOTAL_SQL)
            total_before = cur.fetchone()[0]

            cur.execute(_COUNT_SQL)
            dup_count = cur.fetchone()[0]

            print(f"Total chunks in DB : {total_before:,}")
            print(f"Duplicate chunks   : {dup_count:,}  ({dup_count/total_before*100:.1f}%)")
            print(f"Unique chunks      : {total_before - dup_count:,}")

            if dup_count == 0:
                print("\nNo duplicates found — DB is already clean.")
                return

            print("\nTop filings by duplicate count:")
            cur.execute(_PER_DOC_SQL)
            for row in cur.fetchall():
                doc, total, dups = row
                print(f"  {doc:<45} total={total:>5}  duplicates={dups:>5}")

            if not args.apply:
                print(f"\nDry run — no changes made.")
                print(f"Run with --apply to delete {dup_count:,} duplicate chunks.")
                return

            print(f"\nDeleting {dup_count:,} duplicate chunks ...")
            cur.execute(_DELETE_SQL)
            deleted = cur.rowcount
        conn.commit()

    print(f"Deleted {deleted:,} duplicate rows.")
    print("Run 'python -m scripts.rebuild_bm25' to regenerate BM25 indexes.")


if __name__ == "__main__":
    main()
