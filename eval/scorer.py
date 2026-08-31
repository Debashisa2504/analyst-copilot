"""
eval/scorer.py
---------------
FinanceBench-style rubric scoring engine.

    +1  correct answer AND correct page location
     0  honest abstention
     0  correct answer BUT wrong page location
    -1  confidently wrong answer

Answer matching strategy (two-tier):
  1. Fast numeric check  — if both answers contain numbers, compare magnitudes.
     Handles sign conventions (CapEx shown as negative in filing, positive in GT)
     and unit differences (8738 millions ≈ 8.7 billions within 1% tolerance).
  2. LLM semantic judge  — for text/qualitative answers where numeric check is
     not applicable, ask the VERIFY-LLM (same provider as QA pipeline) whether
     the two answers convey the same financial fact. This replaces brittle regex
     heuristics and handles:
       - Paraphrasing ("declined" vs "decreased")
       - Directional equivalence ("Yes. It decreased." == "Yes, VaR fell by $16M")
       - Partial entity matches ("gain on completion of JV" == "gain associated
         with completion of Consumer Healthcare JV transaction")
       - Negation equivalence ("no acquisitions" == "did not make any acquisitions")
"""
from __future__ import annotations

import re
from typing import Any, List

from backend.config import PAGE_LOCATION_TOLERANCE

_MD_RE       = re.compile(r"[*_`]")
_PAREN_NEG_RE = re.compile(r"\(\s*([\d,]+(?:\.\d+)?)\s*\)")

# ──────────────────────────────────────────────────────────────────────────────
# Normalisation helpers
# ──────────────────────────────────────────────────────────────────────────────

def normalize_answer(text: Any) -> str:
    """Strip markdown, currency symbols, commas; convert (1,034) → -1034."""
    text = str(text) if text is not None else ""
    text = _MD_RE.sub("", text)
    text = _PAREN_NEG_RE.sub(lambda m: f"-{m.group(1)}", text)
    text = text.replace("$", "").replace(",", "").replace("%", "")
    return text.strip().lower()


def extract_numbers(text: Any) -> List[float]:
    """Return all floats found in the normalized answer."""
    normalized = normalize_answer(text)
    values = []
    for match in re.findall(r"-?\d+(?:\.\d+)?", normalized):
        try:
            values.append(float(match))
        except ValueError:
            continue
    return values


# ──────────────────────────────────────────────────────────────────────────────
# Tier 1 – Fast numeric comparison (no LLM)
# ──────────────────────────────────────────────────────────────────────────────

# FinanceBench reports magnitudes in a variety of units (millions, billions,
# thousands).  Allow up to 0.6 % relative error so that rounding differences
# like 8738 M ≈ 8.7 B (exact = 8.738 B, rounded to 8.7 → 0.4 % error) pass.
_REL_TOL = 0.006   # 0.6 %
_ABS_TOL = 0.01    # for values near zero


def _is_bare_number(text: str) -> bool:
    """
    True if, after removing numeric tokens and punctuation, almost nothing
    else is left (e.g. "$1577.00" or "1500 million") -- i.e. the number IS
    the whole answer, so a numeric mismatch is definitive on its own and
    doesn't need an LLM second opinion. False for answers with real
    surrounding text ("Yes, ... in Q2 of FY2022"), where a numeric mismatch
    could be an incidental digit or a different-but-equivalent supporting
    figure, and the semantic judge should get a look.
    """
    normalized = normalize_answer(text)
    stripped = re.sub(r"-?\d+(?:\.\d+)?", "", normalized)
    stripped = re.sub(r"[\s.,;:()\-]", "", stripped)
    return len(stripped) <= 12  # allows a short unit word like "million"/"usd"


def _numbers_match(pred_nums: List[float], gt_nums: List[float]) -> bool:
    """True when any pred number is within tolerance of any GT number."""
    for p in pred_nums:
        for g in gt_nums:
            denom = max(abs(g), abs(p), 1e-9)
            if abs(p - g) / denom <= _REL_TOL or abs(p - g) <= _ABS_TOL:
                return True
            # Sign-agnostic (CapEx outflow shown as positive in GT, negative in filing)
            if abs(abs(p) - abs(g)) / max(abs(g), abs(p), 1e-9) <= _REL_TOL:
                return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Tier 2 – LLM semantic judge
# ──────────────────────────────────────────────────────────────────────────────

_NEGATION_RE = re.compile(
    r"\b(no|none|not|never|zero|did not|no acquisitions?|no major)\b", re.IGNORECASE
)
_INCREASE_RE = re.compile(r"\b(increas|grew|growth|higher|rose|improv)\w*", re.IGNORECASE)
_DECREASE_RE = re.compile(r"\b(decreas|declin|fell|lower|drop|reduc|worsen)\w*", re.IGNORECASE)
_STOP_WORDS  = {"the","a","an","and","or","of","in","to","is","was","it","that",
                "this","for","on","at","by","with","yes","no","there","were",
                "has","have","been","be","from","its","their","which","are"}

_JUDGE_SYSTEM = """\
You are a financial answer evaluator. Given a PREDICTED answer and a \
GROUND-TRUTH answer, reply with JSON {"match": true/false} only.
Match = true when they convey the same financial fact, even if worded differently:
- Same number in different units (8738M = 8.7B), same direction (decreased/fell/declined),
  same yes/no conclusion, same key event with different phrasing, negation equivalence.
Match = false when they disagree on direction, name a different event, or give
materially different numbers that unit conversion cannot explain."""


def _fast_text_match(pred: str, gt: str) -> bool | None:
    """
    Rule-based fast path. Returns True/False if confident, None if uncertain
    (caller should fall through to LLM).
    """
    pred_n, gt_n = normalize_answer(pred), normalize_answer(gt)
    if not pred_n or not gt_n:
        return False
    # Exact / substring
    if gt_n in pred_n or pred_n in gt_n:
        return True
    # Both negative / no-X
    if bool(_NEGATION_RE.search(pred)) and bool(_NEGATION_RE.search(gt)):
        return True
    # Both directional and same direction
    pred_inc, pred_dec = bool(_INCREASE_RE.search(pred)), bool(_DECREASE_RE.search(pred))
    gt_inc,   gt_dec   = bool(_INCREASE_RE.search(gt)),   bool(_DECREASE_RE.search(gt))
    if pred_inc and gt_inc and not pred_dec and not gt_dec:
        return True
    if pred_dec and gt_dec and not pred_inc and not gt_inc:
        return True
    # Opposite direction → definitely wrong
    if (pred_inc and gt_dec) or (pred_dec and gt_inc):
        return False
    # Ambiguous → defer to LLM
    return None


def _llm_semantic_match(predicted: str, ground_truth: str) -> bool:
    """
    LLM judge for ambiguous cases. Uses the VERIFY model (same provider as
    the QA pipeline's verify pass) so it works regardless of whether the
    draft provider (Gemini) is available.
    Returns False on any error so the scorer degrades gracefully.
    """
    try:
        from backend.llm_client import call_llm_json
        from backend.config import VERIFY_MODEL, VERIFY_PROVIDER

        result = call_llm_json(
            _JUDGE_SYSTEM,
            f"Predicted: {predicted}\nGround truth: {ground_truth}",
            VERIFY_MODEL,
            provider=VERIFY_PROVIDER,
        )
        return bool(result.get("match", False))
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def answers_match(predicted: str, ground_truth: str) -> bool:
    """
    Two-tier answer comparison:
      1. Numeric fast-path — deterministic, handles units and sign conventions.
         A numeric MATCH always short-circuits to True (no LLM call needed).
         A numeric MISMATCH is only trusted as definitively wrong when both
         answers are essentially bare numbers with no other content -- e.g.
         "$1577.00" vs "1500" really is just a wrong number. When either side
         has real surrounding text, a mismatch doesn't by itself prove the
         answer wrong (an incidental digit like "Q2" can poison the numeric
         extraction, or the two answers may cite different-but-equivalent
         supporting figures for the same correct conclusion), so it falls
         through to the semantic judge instead.
      2. LLM semantic judge — handles paraphrasing, directionality, entities,
         and any case where a numeric mismatch was inconclusive.
    """
    # ── Tier 1: numeric ──────────────────────────────────────────────────────
    pred_nums = extract_numbers(predicted)
    gt_nums   = extract_numbers(ground_truth)

    if pred_nums and gt_nums:
        # Filter out obvious year-like numbers (1900–2100) — those are
        # context (e.g. "FY2022"), not the answer value.
        non_year = lambda nums: [n for n in nums if not (1900 <= n <= 2100)]
        p_vals = non_year(pred_nums)
        g_vals = non_year(gt_nums)
        if p_vals and g_vals:
            if _numbers_match(p_vals, g_vals):
                return True
            if _is_bare_number(predicted) and _is_bare_number(ground_truth):
                return False

    # ── Tier 2: LLM semantic judge ───────────────────────────────────────────
    return _llm_semantic_match(predicted, ground_truth)


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
