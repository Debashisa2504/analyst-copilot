"""
scripts/reparse_pages.py
-------------------------
Re-parses all (or one) filing HTML files and updates ONLY the page_num column
in PostgreSQL — no re-embedding, no BM25 rebuild (call rebuild_bm25 after).

Why this exists:
  The parser's offset detection previously only scanned prose elements for a
  printed footer page number, missing page numbers in SEC layout-table cells.
  The fix makes the parser do a full pre-scan pass (prose + table cells) before
  emitting any segment.  This script applies that fix to data already in the DB.

Usage:
    python -m scripts.reparse_pages                        # all non-reserved
    python -m scripts.reparse_pages --doc BOEING_2018_10K  # one filing
    python -m scripts.reparse_pages --all                  # including reserved

Typical workflow after running this script:
    python -m scripts.reparse_pages
    python -m scripts.rebuild_bm25
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.chunker import chunk_segments
from backend.config import BM25_DIR, EVAL_LOG_DIR, FILINGS_DIR
from backend.db import get_sync_conn
from backend.parser import parse_filing

_RESERVED_PATH = EVAL_LOG_DIR / "reserved_filings.json"

_UPDATE_SQL = "UPDATE chunks SET page_num = %s WHERE chunk_id = %s"
_COUNT_SQL  = "SELECT COUNT(*) FROM chunks WHERE doc_name = %s"


def _load_reserved() -> set:
    if _RESERVED_PATH.exists():
        return set(json.loads(_RESERVED_PATH.read_text()))
    return set()


def reparse_filing(path: Path, dry_run: bool = False) -> dict:
    """
    Re-parse one filing and UPDATE page_num for every chunk.
    Returns a summary dict.
    """
    doc_name = path.stem
    html = path.read_text(encoding="utf-8", errors="ignore")
    parsed = parse_filing(html, doc_name=doc_name)
    chunks = chunk_segments(parsed.segments, doc_name=doc_name)

    if not chunks:
        return {"doc_name": doc_name, "chunks": 0, "updated": 0, "offset": parsed.page_offset}

    # Build (page_num, chunk_id) pairs for the UPDATE
    updates = [(c.page_num, c.chunk_id) for c in chunks]

    updated = 0
    if not dry_run:
        with get_sync_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(_UPDATE_SQL, updates)
                updated = cur.rowcount
            conn.commit()
    else:
        updated = len(updates)

    return {
        "doc_name": doc_name,
        "chunks": len(chunks),
        "updated": updated,
        "offset": parsed.page_offset,
        "page_method": parsed.page_num_method.value,
    }


def reparse_all(force: bool = False, dry_run: bool = False) -> None:
    reserved = _load_reserved() if not force else set()
    if reserved:
        print(f"[reparse] Skipping {len(reserved)} reserved filing(s). Use --all to include them.")

    files = sorted(FILINGS_DIR.glob("*.htm")) + sorted(FILINGS_DIR.glob("*.html"))
    if not files:
        print(f"No filings found in {FILINGS_DIR}")
        return

    total_updated = 0
    for path in files:
        if path.stem in reserved:
            print(f"[reparse] SKIP (reserved): {path.stem}")
            continue
        try:
            result = reparse_filing(path, dry_run=dry_run)
            flag = "[DRY RUN] " if dry_run else ""
            print(
                f"{flag}[reparse] {result['doc_name']}: "
                f"{result['chunks']} chunks, offset={result['offset']:+d}, "
                f"method={result['page_method']}, updated={result['updated']}"
            )
            total_updated += result["updated"]
        except Exception as exc:
            print(f"[reparse] ERROR on {path.stem}: {exc}")

    action = "Would update" if dry_run else "Updated"
    print(f"\n[reparse] Done. {action} {total_updated} rows across {len(files) - len(reserved)} filings.")
    if not dry_run:
        print("[reparse] Run 'python -m scripts.rebuild_bm25' to regenerate BM25 indexes.")


def main():
    parser = argparse.ArgumentParser(description="Update page_num in DB from re-parsed HTML.")
    parser.add_argument("--doc",      default=None, help="Re-parse only this doc_name stem (e.g. BOEING_2018_10K)")
    parser.add_argument("--all",      action="store_true", help="Include reserved filings")
    parser.add_argument("--dry-run",  action="store_true", help="Show what would change, don't write to DB")
    args = parser.parse_args()

    if args.doc:
        # Find the file — try .htm and .html extensions
        candidate = FILINGS_DIR / f"{args.doc}.htm"
        if not candidate.exists():
            candidate = FILINGS_DIR / f"{args.doc}.html"
        if not candidate.exists():
            print(f"Filing not found: {args.doc}(.htm/.html) in {FILINGS_DIR}", file=sys.stderr)
            sys.exit(1)
        result = reparse_filing(candidate, dry_run=args.dry_run)
        flag = "[DRY RUN] " if args.dry_run else ""
        print(
            f"{flag}[reparse] {result['doc_name']}: "
            f"{result['chunks']} chunks, offset={result['offset']:+d}, "
            f"method={result.get('page_method','?')}, updated={result['updated']}"
        )
        if not args.dry_run:
            print("[reparse] Run 'python -m scripts.rebuild_bm25 --doc " + args.doc + "' to regenerate BM25.")
    else:
        reparse_all(force=args.all, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
