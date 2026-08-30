"""
scripts/ingest_facts.py
------------------------
CLI for populating the financial_facts table from already-downloaded filings.

No re-embedding required — reads directly from the existing .htm files in
filings/ and calls facts_indexer.ingest_filing_facts() for each one.

Usage
-----
# All indexed filings (reads BM25 pickle list to know what's indexed):
python -m scripts.ingest_facts

# Single filing:
python -m scripts.ingest_facts --doc AMD_2015_10K

# Specific subset (e.g. the 25-filing eval sample):
python -m scripts.ingest_facts --subset data/eval_logs/eval_sample_25filings.jsonl

# Dry-run: just show what would be ingested without touching the DB:
python -m scripts.ingest_facts --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time

# psycopg3 async requires SelectorEventLoop on Windows
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from backend.db import setup_schema
from backend.facts_indexer import ingest_filing_facts
from backend.indexer import list_indexed_filings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest filing tables into financial_facts DB table."
    )
    parser.add_argument(
        "--doc",
        metavar="DOC_NAME",
        help="Ingest a single filing (e.g. AMD_2015_10K). Omit to ingest all.",
    )
    parser.add_argument(
        "--subset",
        metavar="JSONL_PATH",
        help="Ingest only the unique doc_names found in a questions JSONL file "
             "(e.g. data/eval_logs/eval_sample_25filings.jsonl).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List filings that would be ingested without writing to DB.",
    )
    args = parser.parse_args()

    if args.doc:
        filings = [args.doc.replace(".htm", "").replace(".html", "")]
    elif args.subset:
        from pathlib import Path
        subset_path = Path(args.subset)
        if not subset_path.exists():
            print(f"[ingest_facts] ERROR: subset file not found: {subset_path}")
            sys.exit(1)
        seen: set[str] = set()
        with open(subset_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    doc = json.loads(line).get("doc_name", "")
                    if doc:
                        seen.add(doc)
        filings = sorted(seen)
        print(f"[ingest_facts] Subset mode — {len(filings)} unique filings from {subset_path.name}")
    else:
        filings = list_indexed_filings()

    if not filings:
        print("[ingest_facts] No filings found. Run scripts/ingest first.")
        sys.exit(1)

    print(f"[ingest_facts] {len(filings)} filing(s) to process.")

    if args.dry_run:
        for f in filings:
            print(f"  (dry-run) would ingest: {f}")
        return

    # Ensure the financial_facts table exists
    setup_schema()

    total_rows = 0
    errors = 0
    t0 = time.time()

    for i, doc_name in enumerate(filings, start=1):
        try:
            n = ingest_filing_facts(doc_name)
            total_rows += n
            print(f"[{i:>3}/{len(filings)}] {doc_name:<40} {n:>6} rows")
        except FileNotFoundError:
            print(f"[{i:>3}/{len(filings)}] {doc_name:<40}  SKIP (no .htm file)")
        except Exception as exc:
            errors += 1
            print(f"[{i:>3}/{len(filings)}] {doc_name:<40}  ERROR: {exc}")

    elapsed = time.time() - t0
    print(
        f"\n[ingest_facts] Done — {total_rows:,} rows inserted across "
        f"{len(filings)} filings in {elapsed:.1f}s. Errors: {errors}"
    )


if __name__ == "__main__":
    main()
