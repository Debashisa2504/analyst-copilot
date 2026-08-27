"""
eval/harness.py
-----------------
Batch evaluation harness: runs the QA pipeline over a practice-questions
.jsonl file and collects per-question and aggregate metrics.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

# psycopg3 async requires SelectorEventLoop on Windows (default is ProactorEventLoop)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from backend.answerer import answer_question
from eval.scorer import score_single


@dataclass
class EvalRecord:
    financebench_id: str
    question: str
    doc_name: str
    predicted_answer: str
    ground_truth_answer: str
    predicted_page: int | None
    evidence_pages: List[int]
    abstained: bool
    score: int


@dataclass
class EvalSummary:
    records: List[EvalRecord] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def net_score(self) -> int:
        return sum(r.score for r in self.records)

    @property
    def correct(self) -> int:
        return sum(1 for r in self.records if r.score == 1)

    @property
    def abstained(self) -> int:
        return sum(1 for r in self.records if r.abstained)

    @property
    def wrong(self) -> int:
        return sum(1 for r in self.records if r.score == -1)

    def precision(self) -> float:
        denom = self.correct + self.wrong
        return self.correct / denom if denom else 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "net_score": self.net_score,
            "correct": self.correct,
            "abstained": self.abstained,
            "wrong": self.wrong,
            "precision": round(self.precision(), 4),
        }


def _load_questions(path: Path, max_questions: int | None = None):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if max_questions and len(rows) >= max_questions:
                break
    return rows


def run_evaluation(
    questions_path: Path,
    max_questions: int | None = None,
    scope: str = "filing",
) -> EvalSummary:
    """
    Run the QA pipeline over a questions file and collect metrics.

    scope:
      "filing"  — use doc_name from each question (normal filing-scoped retrieval).
                  Used for tune, heldout-after-ingest, and general evaluation.
      "global"  — override doc_name to "ALL" for every question, forcing the
                  retriever to search across all indexed filings. Used for the
                  before-ingest check: the system must recognize that evidence
                  from a different filing doesn't answer this question, and
                  abstain rather than confidently misattribute an answer (-1).
    """
    rows = _load_questions(questions_path, max_questions)
    summary = EvalSummary()

    for row in rows:
        question = row["question"]
        doc_name = "ALL" if scope == "global" else row["doc_name"]
        ground_truth = row["answer"]
        evidence_pages = []
        for ev in row.get("evidence", []):
            page = ev.get("evidence_page_num")
            if page is not None:
                evidence_pages.append(page)

        response = asyncio.run(answer_question(question, doc_name=doc_name))

        score = score_single(
            predicted_answer=response.answer,
            ground_truth_answer=ground_truth,
            predicted_page=response.page_num,
            evidence_pages=evidence_pages,
            abstained=response.abstained,
        )

        summary.records.append(
            EvalRecord(
                financebench_id=row.get("financebench_id", ""),
                question=question,
                doc_name=doc_name,
                predicted_answer=response.answer,
                ground_truth_answer=ground_truth,
                predicted_page=response.page_num,
                evidence_pages=evidence_pages,
                abstained=response.abstained,
                score=score,
            )
        )

    return summary


def run_batch(questions: list, scope: str = "filing") -> list:
    """
    Runs the QA pipeline over a pre-loaded list of question dicts and returns
    a list of result dicts (compatible with tune_thresholds.py).
    """
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q) + "\n")
        tmp_path = f.name

    try:
        summary = run_evaluation(Path(tmp_path), scope=scope)
    finally:
        os.unlink(tmp_path)

    return [r.__dict__ for r in summary.records]
