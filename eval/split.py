"""
eval/split.py
--------------
Produces two question sets from practice-questions.jsonl:

  tune_questions.jsonl   — questions tied to the ~66-68 processed filings,
                           used to tune thresholds (§8.1). ~75% of non-reserved
                           questions, stratified by (company, question_type).

  heldout_questions.jsonl — questions tied to the 10-12 reserved filings.
                            Used TWICE with different scope settings (§8.2):
                              1. Before ingest  → run_eval --mode before
                                 (global scope: doc_name=ALL, checking the
                                  system abstains rather than hallucinating
                                  from a different filing)
                              2. After ingest   → run_eval --mode after
                                 (filing scope: doc_name=<filing>, measures
                                  real accuracy on a genuinely unseen filing)

Usage:
    python -m eval.split --questions practice-questions.jsonl
    python -m eval.split --questions practice-questions.jsonl --ratio 0.75

Output:
    data/eval_logs/tune_questions.jsonl
    data/eval_logs/heldout_questions.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

from backend.config import EVAL_LOG_DIR
from .reserve_filings import load_reserved

SEED = 42
TUNE_PATH = EVAL_LOG_DIR / "tune_questions.jsonl"
HELDOUT_PATH = EVAL_LOG_DIR / "heldout_questions.jsonl"


def _classify_question_type(question: str) -> str:
    """Rough classification: simple | narrative | footnote.

    Keyword heuristics can misfire (e.g. "why does the balance sheet show X"
    looks narrative but is a simple lookup). The §8.3 manual spot-check is
    the right place to catch obvious misclassifications before the split locks.
    """
    q = question.lower()
    if any(w in q for w in ["note", "footnote", "refer", "see note"]):
        return "footnote"
    if any(w in q for w in ["why", "explain", "describe", "discuss",
                              "strategy", "risk", "outlook"]):
        return "narrative"
    return "simple"


def load_questions(path: Path) -> list:
    questions = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    return questions


def stratified_split(questions: list, tune_ratio: float = 0.75) -> tuple:
    """
    Splits non-reserved questions into tune and a small validation slice
    using stratified sampling by (company, question_type).

    Reserved-filing questions are NOT included here — they are written
    to heldout_questions.jsonl as a complete set by main().

    Returns (tune_list, small_heldout_slice) where small_heldout_slice
    is retained only for secondary validation; the primary holdout is
    the reserved-filing set written separately.
    """
    reserved = set(load_reserved())
    rng = random.Random(SEED)

    # Split non-reserved questions into tune / small-validation slice
    non_reserved = [
        q for q in questions
        if q.get("doc_name", q.get("filing", "")).replace(".htm", "").replace(".html", "") not in reserved
    ]
    n_reserved_qs = len(questions) - len(non_reserved)
    if n_reserved_qs:
        print(f"  {n_reserved_qs} question(s) tied to reserved filings -> heldout_questions.jsonl")

    strata: dict = defaultdict(list)
    for q in non_reserved:
        company = q.get("company", q.get("ticker", "unknown"))
        qtype = _classify_question_type(q.get("question", ""))
        strata[(company, qtype)].append(q)

    tune, small_val = [], []
    for _key, items in strata.items():
        rng.shuffle(items)
        split_at = max(1, round(len(items) * tune_ratio))
        if len(items) == 1:
            tune.extend(items)
        else:
            tune.extend(items[:split_at])
            small_val.extend(items[split_at:])

    rng.shuffle(tune)
    rng.shuffle(small_val)
    return tune, small_val


def write_jsonl(questions: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Question split: tune set + heldout (reserved-filing) set."
    )
    parser.add_argument(
        "--questions", default="practice-questions.jsonl",
        help="Path to practice-questions.jsonl (default: practice-questions.jsonl)"
    )
    parser.add_argument(
        "--ratio", type=float, default=0.75,
        help="Fraction of non-reserved questions in tune set (default 0.75)"
    )
    args = parser.parse_args()

    qpath = Path(args.questions)
    if not qpath.exists():
        print(f"ERROR: {qpath} not found.", file=sys.stderr)
        sys.exit(1)

    if TUNE_PATH.exists() or HELDOUT_PATH.exists():
        print("ERROR: Split files already exist. Delete them first to re-split.")
        for p in (TUNE_PATH, HELDOUT_PATH):
            if p.exists():
                print(f"  {p}")
        sys.exit(1)

    questions = load_questions(qpath)
    print(f"Loaded {len(questions)} questions from {qpath}")

    reserved_names = set(load_reserved())

    # heldout = ALL questions for reserved filings (used for before + after checks)
    heldout = [
        q for q in questions
        if q.get("doc_name", "").replace(".htm", "").replace(".html", "") in reserved_names
    ]

    # tune = stratified sample of non-reserved questions
    tune, _small_val = stratified_split(questions, tune_ratio=args.ratio)

    write_jsonl(tune, TUNE_PATH)
    write_jsonl(heldout, HELDOUT_PATH)

    print(f"\nSplit complete (seed={SEED}, ratio={args.ratio}):")
    print(f"  Tune set:     {len(tune):>4} questions -> {TUNE_PATH}")
    print(f"  Heldout set:  {len(heldout):>4} questions -> {HELDOUT_PATH}")
    print()
    print("Workflow:")
    print("  1. Tune thresholds:  python -m eval.tune_thresholds (tune set only)")
    print("  2. Freeze thresholds.")
    print("  3. Pre-ingest check: python -m eval.run_eval --mode before")
    print("     (global scope — system should abstain, not hallucinate)")
    print("  4. Ingest reserved filings via Track B upload.")
    print("  5. Accuracy check:   python -m eval.run_eval --mode after")
    print("     (filing scope — real accuracy on unseen filings)")


if __name__ == "__main__":
    main()
