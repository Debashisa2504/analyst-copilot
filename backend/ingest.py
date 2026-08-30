"""
backend/ingest.py
------------------
CLI for bulk corpus ingestion: parse -> chunk -> index every filing in
FILINGS_DIR (or a single named file, or a named subset).

Respects eval/reserve_filings.py: files listed in
data/eval_logs/reserved_filings.json are automatically skipped during
--all ingestion so the filing-level holdout (Sec 8.2) remains unseen.

Usage:
    python -m backend.ingest --all
    python -m backend.ingest --file 3M_2018_10K.htm
    python -m backend.ingest --all --force   # ingest reserved files too
    python -m backend.ingest --subset data/eval_logs/eval_sample_25filings.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .chunker import chunk_segments
from .config import FILINGS_DIR, EVAL_LOG_DIR
from .indexer import index_chunks
from .parser import parse_filing

_RESERVED_PATH = EVAL_LOG_DIR / "reserved_filings.json"


def _load_reserved() -> set:
    if _RESERVED_PATH.exists():
        return set(json.loads(_RESERVED_PATH.read_text()))
    return set()


def _load_subset_doc_names(path: Path) -> set:
    """
    Reads the unique doc_names from a questions JSONL file (e.g.
    data/eval_logs/eval_sample_25filings.jsonl), mirroring
    scripts/ingest_facts.py's --subset so both ingestion paths select the
    exact same filing set for an apples-to-apples before/after comparison.
    """
    seen: set = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            doc = json.loads(line).get("doc_name", "")
            if doc:
                seen.add(doc)
    return seen


def ingest_file(path: Path) -> int:
    doc_name = path.stem
    html = path.read_text(encoding="utf-8", errors="ignore")
    parsed = parse_filing(html, doc_name=doc_name)
    chunks = chunk_segments(parsed.segments, doc_name=doc_name)
    index_chunks(chunks, doc_name=doc_name)
    print(
        f"[ingest] {doc_name}: {len(parsed.segments)} segments -> {len(chunks)} chunks "
        f"(page method: {parsed.page_num_method.value}, offset: {parsed.page_offset})"
    )
    return len(chunks)


def _already_indexed(doc_name: str) -> bool:
    """True if BM25 pkl exists — written last in ingest_file, so it confirms full success."""
    from .config import BM25_DIR
    return (BM25_DIR / f"{doc_name}.pkl").exists()


def ingest_all(force: bool = False, resume: bool = False, subset: set | None = None) -> None:
    reserved = _load_reserved() if not force else set()
    if reserved:
        print(f"[ingest] Skipping {len(reserved)} reserved filing(s) for eval holdout.")
        print("         Use --force to ingest them (only after thresholds are frozen).")

    files = sorted(FILINGS_DIR.glob("*.htm")) + sorted(FILINGS_DIR.glob("*.html"))
    if subset is not None:
        files = [p for p in files if p.stem in subset]
        print(f"[ingest] Subset mode -- {len(files)}/{len(subset)} requested filing(s) found on disk.")
    if not files:
        print(f"No filings found in {FILINGS_DIR}")
        return

    total = 0
    skipped = 0
    already_done = 0
    for path in files:
        if path.stem in reserved:
            print(f"[ingest] SKIP (reserved): {path.stem}")
            skipped += 1
            continue
        if resume and _already_indexed(path.stem):
            print(f"[ingest] SKIP (already indexed): {path.stem}")
            already_done += 1
            continue
        for attempt in range(3):
            try:
                total += ingest_file(path)
                break
            except Exception as exc:
                import time
                wait = 120 * (attempt + 1)
                print(f"[ingest] ERROR on {path.stem} (attempt {attempt + 1}/3): {exc}")
                if attempt < 2:
                    print(f"[ingest] Waiting {wait}s before retry ...")
                    time.sleep(wait)
                else:
                    print(f"[ingest] Skipping {path.stem} after 3 failed attempts.")

    print(
        f"[ingest] Done. {len(files) - skipped - already_done} filings ingested "
        f"({skipped} reserved, {already_done} skipped/resumed), {total} total chunks."
    )


def main():
    parser = argparse.ArgumentParser(description="Ingest SEC filings into the search index.")
    parser.add_argument("--all", action="store_true", help="Ingest every non-reserved filing in FILINGS_DIR")
    parser.add_argument("--file", type=str, help="Ingest a single filing by filename")
    parser.add_argument("--force", action="store_true", help="Ignore reserved-filing list (use after threshold freeze)")
    parser.add_argument("--resume", action="store_true", help="Skip filings that already have a BM25 index (safe restart after crash)")
    parser.add_argument(
        "--subset",
        metavar="JSONL_PATH",
        help="Ingest only the unique doc_names found in a questions JSONL file "
             "(e.g. data/eval_logs/eval_sample_25filings.jsonl). Implies --all's "
             "reserved-filing/--force/--resume behavior, scoped to that subset.",
    )
    args = parser.parse_args()

    if args.subset:
        subset_path = Path(args.subset)
        if not subset_path.exists():
            print(f"Subset file not found: {subset_path}", file=sys.stderr)
            sys.exit(1)
        subset = _load_subset_doc_names(subset_path)
        ingest_all(force=args.force, resume=args.resume, subset=subset)
    elif args.all:
        ingest_all(force=args.force, resume=args.resume)
    elif args.file:
        path = FILINGS_DIR / args.file
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            sys.exit(1)
        ingest_file(path)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
