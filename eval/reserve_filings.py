"""
eval/reserve_filings.py
------------------------
Filing-level holdout (§8.2): selects 10–12 filings from the corpus and
writes their names to data/eval_logs/reserved_filings.json.
These filings must NOT be ingested until thresholds are frozen.

Selection is question-count-aware: filings with more linked practice
questions are preferred so the holdout carries maximum eval signal.
Filings with zero linked questions are skipped entirely — they can't
contribute anything to the before/after accuracy checks.

Usage:
    python -m eval.reserve_filings                          # auto-select 10 filings
    python -m eval.reserve_filings --n 12                  # choose 12
    python -m eval.reserve_filings --questions path/to.jsonl  # custom questions file
    python -m eval.reserve_filings --list                  # show current reserved set
    python -m eval.reserve_filings --clear                 # remove reservation

The companion ingest.py respects this file:
    python -m backend.ingest --all   skips reserved filings automatically.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from backend.config import FILINGS_DIR, EVAL_LOG_DIR

RESERVED_PATH = EVAL_LOG_DIR / "reserved_filings.json"
SEED = 42


def load_reserved() -> list:
    if RESERVED_PATH.exists():
        return json.loads(RESERVED_PATH.read_text())
    return []


def save_reserved(names: list) -> None:
    EVAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    RESERVED_PATH.write_text(json.dumps(names, indent=2))


def _load_question_counts(questions_path: Path) -> dict:
    """
    Returns {doc_name: question_count} by scanning a practice-questions.jsonl.
    Used to steer auto_select() toward filings that actually have linked questions.
    """
    counts: dict = {}
    if not questions_path.exists():
        return counts
    with open(questions_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                doc = row.get("doc_name", "").replace(".htm", "").replace(".html", "")
                if doc:
                    counts[doc] = counts.get(doc, 0) + 1
            except json.JSONDecodeError:
                continue
    return counts


def auto_select(n: int = 10, questions_path: Path | None = None) -> list:
    """
    Question-count-aware selection of n filings for the holdout set.

    Preference order:
    1. Only filings that have ≥1 linked practice question are considered.
       A reserved filing with zero questions contributes nothing to the
       before/after accuracy checks, so it wastes a holdout slot.
    2. Among eligible filings, weighted-random sampling biased toward
       filings with more questions so the holdout carries maximum signal.
    3. Seeded (SEED=42) for full reproducibility.

    After running, do a short manual spot-check (§8.3):
    - Do the 10–12 names span different sectors/companies?
    - Is at least one a large, structurally complex filing?
    """
    all_filings = sorted(
        {p.stem for p in FILINGS_DIR.glob("*.htm")} |
        {p.stem for p in FILINGS_DIR.glob("*.html")}
    )

    if not all_filings:
        print(f"No filings found in {FILINGS_DIR}", file=sys.stderr)
        return []

    if questions_path is None:
        questions_path = Path("practice-questions.jsonl")

    q_counts = _load_question_counts(questions_path)

    # Only consider filings that have at least one linked question.
    eligible = [f for f in all_filings if q_counts.get(f, 0) >= 1]
    skipped = len(all_filings) - len(eligible)
    if skipped:
        print(f"  Skipping {skipped} filing(s) with no linked questions.")

    if not eligible:
        print("WARNING: No filings have linked questions. Falling back to plain random.")
        eligible = all_filings

    if len(eligible) <= n:
        print(f"Warning: only {len(eligible)} eligible filing(s); reserving all.")
        return sorted(eligible)

    # Weighted sampling: weight = question count so data-rich filings are preferred.
    rng = random.Random(SEED)
    pool = list(eligible)
    selected = []

    for _ in range(n):
        weights = [q_counts.get(f, 1) for f in pool]
        total = sum(weights)
        r = rng.uniform(0, total)
        cumulative = 0.0
        for i, (filing, weight) in enumerate(zip(pool, weights)):
            cumulative += weight
            if r <= cumulative:
                selected.append(filing)
                pool.pop(i)
                break

    return sorted(selected)


def main():
    parser = argparse.ArgumentParser(
        description="Reserve filings for the filing-level holdout eval (§8.2)."
    )
    parser.add_argument(
        "--n", type=int, default=10,
        help="Number of filings to reserve (default 10)"
    )
    parser.add_argument(
        "--questions", default="practice-questions.jsonl",
        help="Path to practice-questions.jsonl for question-count weighting"
    )
    parser.add_argument("--list", action="store_true", help="List currently reserved filings")
    parser.add_argument("--clear", action="store_true", help="Clear the reservation")
    parser.add_argument("--filings", nargs="+", help="Manually specify filing names to reserve")
    args = parser.parse_args()

    if args.list:
        reserved = load_reserved()
        if reserved:
            print(f"Reserved filings ({len(reserved)}):")
            for f in reserved:
                print(f"  {f}")
        else:
            print("No filings reserved yet.")
        return

    if args.clear:
        save_reserved([])
        print("Reservation cleared.")
        return

    if load_reserved():
        print("ERROR: Reserved filings already set. Use --list to view or --clear to reset.")
        sys.exit(1)

    if args.filings:
        reserved = args.filings
    else:
        reserved = auto_select(n=args.n, questions_path=Path(args.questions))

    if not reserved:
        sys.exit(1)

    save_reserved(reserved)
    print(f"\nReserved {len(reserved)} filings (seed={SEED}, question-count-weighted):")
    for f in reserved:
        print(f"  {f}")
    print(f"\nSaved to: {RESERVED_PATH}")
    print("These will be EXCLUDED from 'python -m backend.ingest --all'")
    print("until thresholds are frozen via eval/tune_thresholds.py")
    print("\nNext: run a quick manual spot-check (§8.3) —")
    print("  - Do these span different sectors/companies?")
    print("  - Is at least one a large, structurally complex filing?")


def is_reserved(doc_name: str) -> bool:
    return doc_name in load_reserved()


if __name__ == "__main__":
    main()
