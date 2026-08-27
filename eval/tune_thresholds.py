"""
eval/tune_thresholds.py
------------------------
Grid search over ABSTAIN_THRESHOLD and DUAL_AGREEMENT_MULTIPLIER
on the TUNE SET ONLY. Never touches the held-out set.

Usage:
    python -m eval.tune_thresholds                     # uses tune_questions.jsonl
    python -m eval.tune_thresholds --max 20            # first 20 questions only
    python -m eval.tune_thresholds --dry-run           # show grid without running

Output:
    data/eval_logs/tuning_results.json    — full grid results
    data/eval_logs/best_thresholds.json   — chosen thresholds (frozen after this)

After running, copy best_thresholds.json values into your .env before
running the final held-out evaluation.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from backend.config import EVAL_LOG_DIR
from .scorer import score_single, normalize_answer
from .harness import run_batch

TUNE_PATH = EVAL_LOG_DIR / "tune_questions.jsonl"
RESULTS_PATH = EVAL_LOG_DIR / "tuning_results.json"
BEST_PATH = EVAL_LOG_DIR / "best_thresholds.json"

# Search grid
ABSTAIN_THRESHOLDS = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85]
DUAL_MULTIPLIERS = [1.0, 1.25, 1.5, 1.75]


def load_tune_questions(max_q: int = None) -> list:
    if not TUNE_PATH.exists():
        print(f"ERROR: {TUNE_PATH} not found. Run eval/split.py first.", file=sys.stderr)
        sys.exit(1)
    questions = []
    with open(TUNE_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    if max_q:
        questions = questions[:max_q]
    return questions


def evaluate_grid_point(
    questions: list,
    abstain_threshold: float,
    dual_multiplier: float,
) -> Dict:
    """
    Temporarily overrides env vars, runs the batch, returns metrics.
    NOTE: requires LLM credentials and a running index.
    """
    os.environ["ABSTAIN_THRESHOLD"] = str(abstain_threshold)
    os.environ["DUAL_AGREEMENT_MULTIPLIER"] = str(dual_multiplier)

    # Re-import config with updated env
    import importlib
    import backend.config as cfg
    importlib.reload(cfg)

    results = run_batch(questions)
    total = len(results)
    correct = sum(1 for r in results if r.get("score", 0) == 1)
    wrong = sum(1 for r in results if r.get("score", 0) == -1)
    abstained = sum(1 for r in results if r.get("abstained", False))
    net_score = correct - wrong
    precision = correct / (correct + wrong) if (correct + wrong) > 0 else 1.0

    return {
        "abstain_threshold": abstain_threshold,
        "dual_multiplier": dual_multiplier,
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "abstained": abstained,
        "net_score": net_score,
        "precision": round(precision, 4),
    }


def main():
    parser = argparse.ArgumentParser(description="Grid search over tune set (§8.1).")
    parser.add_argument("--max", type=int, default=None, help="Max questions to evaluate")
    parser.add_argument("--dry-run", action="store_true", help="Print grid without running")
    args = parser.parse_args()

    questions = load_tune_questions(args.max)
    print(f"Tune set: {len(questions)} questions")
    print(f"Grid: {len(ABSTAIN_THRESHOLDS)} × {len(DUAL_MULTIPLIERS)} = "
          f"{len(ABSTAIN_THRESHOLDS)*len(DUAL_MULTIPLIERS)} points\n")

    if args.dry_run:
        print("Grid points (dry run):")
        for at in ABSTAIN_THRESHOLDS:
            for dm in DUAL_MULTIPLIERS:
                print(f"  abstain_threshold={at}  dual_multiplier={dm}")
        return

    all_results = []
    best = None

    for at in ABSTAIN_THRESHOLDS:
        for dm in DUAL_MULTIPLIERS:
            print(f"  Testing abstain={at}  dual={dm} ...", end="", flush=True)
            try:
                result = evaluate_grid_point(questions, at, dm)
                all_results.append(result)
                print(f"  net={result['net_score']:+d}  prec={result['precision']:.0%}  "
                      f"wrong={result['wrong']}")
                if best is None or result["net_score"] > best["net_score"]:
                    best = result
            except Exception as e:
                print(f"  ERROR: {e}")

    EVAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(all_results, indent=2))

    if best:
        BEST_PATH.write_text(json.dumps({
            "ABSTAIN_THRESHOLD": best["abstain_threshold"],
            "DUAL_AGREEMENT_MULTIPLIER": best["dual_multiplier"],
            "tune_net_score": best["net_score"],
            "tune_precision": best["precision"],
            "note": "Frozen thresholds — do not re-tune after viewing held-out results.",
        }, indent=2))
        print(f"\nBest: abstain={best['abstain_threshold']}  dual={best['dual_multiplier']}")
        print(f"      net_score={best['net_score']:+d}  precision={best['precision']:.0%}")
        print(f"\nSaved to: {BEST_PATH}")
        print("Add these to your .env, then run:")
        print("  python -m eval.run_eval --held-out")


if __name__ == "__main__":
    main()
