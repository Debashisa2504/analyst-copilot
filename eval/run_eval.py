"""
eval/run_eval.py
------------------
CLI for all three evaluation modes:

  --mode tune    Run against tune_questions.jsonl (filing scope).
                 Use this during threshold tuning — never touch heldout.

  --mode before  Run heldout_questions.jsonl in GLOBAL scope (doc_name=ALL).
                 Run this BEFORE ingesting the reserved filings.
                 The system should abstain on these — if it produces confident
                 answers by pulling evidence from a different filing, that is
                 a -1 and the retriever/verify gate needs fixing.

  --mode after   Run heldout_questions.jsonl in FILING scope (doc_name=<filing>).
                 Run this AFTER ingesting reserved filings via Track B.
                 This is the real accuracy score on genuinely unseen filings.

Usage:
    python -m eval.run_eval --mode tune
    python -m eval.run_eval --mode before
    python -m eval.run_eval --mode after
    python -m eval.run_eval --mode tune --max 10   # quick smoke-test
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.config import EVAL_LOG_DIR, PROJECT_ROOT
from eval.harness import run_evaluation

TUNE_PATH = EVAL_LOG_DIR / "tune_questions.jsonl"
HELDOUT_PATH = EVAL_LOG_DIR / "heldout_questions.jsonl"


def main():
    parser = argparse.ArgumentParser(description="Run the FinanceBench-style evaluation suite.")
    parser.add_argument(
        "--mode",
        choices=["tune", "before", "after"],
        default="tune",
        help=(
            "tune   = tune_questions.jsonl, filing scope (default)\n"
            "before = heldout_questions.jsonl, global scope (pre-ingest abstention check)\n"
            "after  = heldout_questions.jsonl, filing scope (post-ingest accuracy)"
        ),
    )
    parser.add_argument("--max", type=int, default=None, help="Limit the number of questions")
    parser.add_argument(
        "--questions",
        type=str,
        default=None,
        help="Override questions file path (ignores --mode file selection)",
    )
    args = parser.parse_args()

    # Resolve questions file and retrieval scope from mode
    if args.questions:
        questions_path = Path(args.questions)
        scope = "filing"
    elif args.mode == "tune":
        questions_path = TUNE_PATH
        scope = "filing"
    elif args.mode == "before":
        questions_path = HELDOUT_PATH
        scope = "global"
    else:  # after
        questions_path = HELDOUT_PATH
        scope = "filing"

    if not questions_path.exists():
        print(f"ERROR: {questions_path} not found.")
        if args.mode in ("before", "after") and not args.questions:
            print("  Run 'python -m eval.split' first to generate heldout_questions.jsonl")
        elif args.mode == "tune" and not args.questions:
            print("  Run 'python -m eval.split' first to generate tune_questions.jsonl")
        raise SystemExit(1)

    print(f"[eval] mode={args.mode}  scope={scope}  questions={questions_path}")
    if args.mode == "before":
        print("[eval] NOTE: expecting mostly abstentions -- confident wrong answers score -1")

    summary = run_evaluation(questions_path, max_questions=args.max, scope=scope)

    print(json.dumps(summary.to_dict(), indent=2))

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = EVAL_LOG_DIR / f"eval_{args.mode}_{ts}.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for record in summary.records:
            f.write(json.dumps(record.__dict__) + "\n")
    print(f"[eval] wrote per-question log to {out_path}")


if __name__ == "__main__":
    main()
