"""
eval/scorer.py
---------------
FinanceBench-style rubric scoring engine.

    +1  correct answer AND correct page location
     0  honest abstention
     0  correct answer BUT wrong page location
    -1  confidently wrong answer
"""
from __future__ import annotations

import re
from typing import Any, List

from backend.config import PAGE_LOCATION_TOLERANCE

_MD_RE = re.compile(r"[*_`]")
_NUMBER_RE = re.compile(r"-?\(?\$?\s*[\d,]+(?:\.\d+)?\)?%?")
_PAREN_NEG_RE = re.compile(r"\(\s*([\d,]+(?:\.\d+)?)\s*\)")


def normalize_answer(text: Any) -> str:
    """Strips markdown, currency symbols, commas; converts (1,034) -> -1034."""
    text = str(text) if text is not None else ""
    text = _MD_RE.sub("", text)
    text = _PAREN_NEG_RE.sub(lambda m: f"-{m.group(1)}", text)
    text = text.replace("$", "").replace(",", "").replace("%", "")
    return text.strip().lower()


def extract_numbers(text: Any) -> List[float]:
    """Extracts floating point values from normalized answer text."""
    normalized = normalize_answer(text)
    values = []
    for match in re.findall(r"-?\d+(?:\.\d+)?", normalized):
        try:
            values.append(float(match))
        except ValueError:
            continue
    return values


_NEGATION_RE = re.compile(
    r"\b(no|none|not|never|zero|did not|did not make|no acquisitions?|no major)\b",
    re.IGNORECASE,
)


def _both_negative(pred: str, gt: str) -> bool:
    """True when both predicted and ground-truth express the same 'none/no X' idea."""
    return bool(_NEGATION_RE.search(pred)) and bool(_NEGATION_RE.search(gt))


def answers_match(predicted: str, ground_truth: str) -> bool:
    """
    Numeric equality (|pred-gt| < 0.01) if both are numeric, else substring
    match. Falls back to comparing absolute values: FinanceBench-style
    ground truth commonly reports an outflow line item (CapEx, dividends
    paid, share buybacks) as a positive magnitude -- "capital expenditure
    was $1,577M" -- while the filing itself shows it parenthesized/negative
    in the cash flow statement, exactly as accounting convention requires.
    A sign mismatch alone, with the magnitude otherwise correct, should not
    fail an answer; the two-pass answerer is expected to phrase the sign to
    match the question, but the scorer stays robust either way.
    """
    pred_nums = extract_numbers(predicted)
    gt_nums = extract_numbers(ground_truth)

    if pred_nums and gt_nums:
        if any(abs(p - g) < 0.01 for p in pred_nums for g in gt_nums):
            return True
        return any(abs(abs(p) - abs(g)) < 0.01 for p in pred_nums for g in gt_nums)

    pred_norm = normalize_answer(predicted)
    gt_norm = normalize_answer(ground_truth)
    if not pred_norm or not gt_norm:
        return False
    if gt_norm in pred_norm or pred_norm in gt_norm:
        return True
    # "None" / "No acquisitions" / "did not make any" are all equivalent
    if _both_negative(predicted, ground_truth):
        return True
    return False


def page_matches(
    predicted_page, evidence_pages: List[int], page_tolerance: int = PAGE_LOCATION_TOLERANCE
) -> bool:
    """True if predicted_page is within `page_tolerance` of any evidence page."""
    if predicted_page is None or not evidence_pages:
        return False
    return any(abs(int(predicted_page) - int(p)) <= page_tolerance for p in evidence_pages)


def score_single(
    predicted_answer: str,
    ground_truth_answer: str,
    predicted_page,
    evidence_pages: List[int],
    abstained: bool = False,
    page_tolerance: int = PAGE_LOCATION_TOLERANCE,
) -> int:
    """
    Rubric mark:
      +1  correct answer AND correct location
       0  abstained, OR correct answer with wrong location
      -1  wrong answer
    """
    if abstained:
        return 0

    correct = answers_match(predicted_answer, ground_truth_answer)
    if not correct:
        return -1

    if page_matches(predicted_page, evidence_pages, page_tolerance):
        return 1
    return 0
