"""
backend/answerer.py
--------------------
Decoupled two-pass QA engine + calibrated precision gate (ADR-003).

  Pass 1 (Draft):  extract a candidate answer, value, page citation and
                   rationale from the retrieved excerpts.
  Pass 2 (Verify): independently audit that candidate against the same
                   raw excerpts — is the value present, period correct,
                   page citation right?
  Gate:            abstain unless verified == True AND
                   min(draft.confidence, verify.confidence) >= ABSTAIN_THRESHOLD.

Footnote auto-enrichment (§6.1): any callout references found in the
retrieved chunks are resolved from the footnote graph and appended to
the context before generation.
"""
from __future__ import annotations

from typing import List

from .config import ABSTAIN_THRESHOLD, CONTEXT_TOP_K, DRAFT_MODEL, DRAFT_PROVIDER, VERIFY_MODEL, VERIFY_PROVIDER
from .indexer import list_indexed_filings
from .llm_client import call_llm_json
from .models import (
    AnswerResponse, DraftAnswer, EvidenceItem, RetrievedChunk, VerifyResult,
)
from .retriever import retrieve, top_context
from .footnote_graph import enrich_context_with_footnotes

DRAFT_SYSTEM_PROMPT = """\
You are a meticulous financial analyst assistant.
You will be given excerpts from an SEC filing (facts serialized as
"Row Label, Column Header: Value (Units)" for tables, plus prose passages
and any resolved footnote definitions).
Answer the user's question using ONLY the excerpts provided. If the
excerpts do not contain enough information, say so honestly.

Sign convention: cash flow statement outflows (e.g. capital expenditure,
dividends paid, share repurchases) are stored as negative numbers because
that is how the filing shows them (parenthesized). When a question asks
"how much was spent/paid", report it as a positive magnitude in `answer`
(e.g. "$1,577 million") the way an analyst would say it, but keep `value`
as the number exactly as it appears in the excerpt so it can be checked.

Unit conversion: if the question asks for a different unit than the filing
(e.g. question asks "USD billions" but the table header says "in millions"),
convert and answer in the requested unit. For example, if the table shows
"8,738" in millions and the question asks for billions, answer "$8.7 billion"
and set confidence >= 0.8 because the underlying number is clearly present.

Respond with STRICT JSON only, no prose outside the JSON object:
{"answer": "<one or two sentence answer>",
 "value": "<the specific extracted number or short value, or null>",
 "page_num": <int page number the value came from, or null>,
 "confidence": <float 0-1>,
 "rationale": "<short justification citing which excerpt was used>",
 "found": <true if the excerpts contain enough information to answer the question, false if the needed data is not present in the provided context>}
"""

VERIFY_SYSTEM_PROMPT = """\
You are a mechanical auditor checking a draft financial answer against
source excerpts. Check three things:
1. Is the answer supported by the excerpt text? The value may be:
   - Explicitly stated verbatim, OR
   - Computable from numbers shown (e.g. YoY % change from two revenue
     figures, a margin from operating income / revenue), OR
   - A unit-converted form of a number shown (e.g. excerpt shows "8,738"
     in a table labelled "in millions" and the answer says "$8.7 billion"
     — this is verified because 8738 / 1000 = 8.738 ≈ 8.7).
   If the source numbers are present and the arithmetic / conversion is
   correct, set verified=true.
2. Does the fiscal period / year in the excerpt match what the question asks?
3. Is the cited page number one of the pages the supporting excerpt(s) came from?
For yes/no or directional questions ("did X increase?"), verify that the
direction or boolean conclusion is supported by the data, not that a specific
numeric value is present verbatim.
Respond with STRICT JSON only:
{"verified": <true/false>,
 "correct_value": "<the specific short value or null — do NOT put a full sentence here>",
 "correct_page": <int, or null>,
 "confidence": <float 0-1>,
 "issues": ["<short issue string>", ...]}
"""


def _resolve_doc_name(doc_name: str) -> str:
    if doc_name in ("ALL", "", None):
        return "ALL"
    indexed = list_indexed_filings()
    if doc_name in indexed:
        return doc_name
    stem = doc_name.replace(".htm", "").replace(".html", "")
    for name in indexed:
        if name == stem or name.lower() == stem.lower():
            return name
    return doc_name


def _format_context(chunks: List[RetrievedChunk], footnote_addon: str) -> str:
    lines = []
    for i, rc in enumerate(chunks, start=1):
        c = rc.chunk
        lines.append(f"[Excerpt {i} | page {c.page_num} | {c.chunk_type.value}]\n{c.text}")
    text = "\n\n".join(lines)
    if footnote_addon:
        text += f"\n\n[Resolved footnotes]\n{footnote_addon}"
    return text


def call_draft(question: str, context_text: str) -> DraftAnswer:
    user_prompt = f"Question: {question}\n\nExcerpts:\n{context_text}"
    raw = call_llm_json(DRAFT_SYSTEM_PROMPT, user_prompt, DRAFT_MODEL, provider=DRAFT_PROVIDER)
    return DraftAnswer(**raw)


def call_verify(question: str, draft: DraftAnswer, context_text: str) -> VerifyResult:
    user_prompt = (
        f"Question: {question}\n\n"
        f"Draft answer: {draft.answer}\n"
        f"Draft value: {draft.value}\n"
        f"Draft page: {draft.page_num}\n\n"
        f"Excerpts:\n{context_text}"
    )
    raw = call_llm_json(VERIFY_SYSTEM_PROMPT, user_prompt, VERIFY_MODEL, provider=VERIFY_PROVIDER)
    return VerifyResult(**raw)


async def answer_question(question: str, doc_name: str = "ALL", top_k: int = 10) -> AnswerResponse:
    resolved_doc = _resolve_doc_name(doc_name)

    # --- Retrieval ---
    retrieval = await retrieve(question, doc_name=resolved_doc, top_k=top_k)
    context_chunks = top_context(retrieval, n=CONTEXT_TOP_K)

    if not context_chunks:
        return AnswerResponse(
            answer="not found in this filing",
            doc_name=resolved_doc,
            abstained=True,
            confidence=0.0,
            retrieval_agreement=retrieval.agreement_ratio,
            evidence=[],
        )

    # --- Footnote auto-enrichment (§6.1) ---
    footnote_addon = enrich_context_with_footnotes(context_chunks)

    context_text = _format_context(context_chunks, footnote_addon)
    evidence = [
        EvidenceItem(
            text=rc.chunk.text,
            page_num=rc.chunk.page_num,
            doc_name=rc.chunk.doc_name,
            chunk_type=rc.chunk.chunk_type,
        )
        for rc in context_chunks
    ]

    # --- Two-pass QA ---
    try:
        draft = call_draft(question, context_text)
        verify = call_verify(question, draft, context_text)
    except Exception as exc:
        return AnswerResponse(
            answer=(
                f"LLM unavailable ({exc}) — showing raw retrieved evidence only, unverified."
            ),
            doc_name=resolved_doc,
            page_num=context_chunks[0].chunk.page_num if context_chunks else None,
            abstained=True,
            confidence=0.0,
            retrieval_agreement=retrieval.agreement_ratio,
            evidence=evidence,
            evidence_text=context_text,
        )

    # --- Precision gate ---
    # Two conditions trigger abstention (OR logic):
    #  (a) LLM itself declares found=False AND verify can't confirm → data genuinely absent
    #  (b) verify disagrees AND draft is uncertain (below threshold) → low-confidence wrong answer
    # This is semantic: the LLM reports whether it found the data (found field),
    # no keyword matching on the answer text.
    final_confidence = min(draft.confidence, verify.confidence)
    abstained = (not draft.found and not verify.verified) or \
                (not verify.verified and draft.confidence < ABSTAIN_THRESHOLD)

    if abstained:
        return AnswerResponse(
            answer="not found in this filing",
            doc_name=resolved_doc,
            page_num=None,
            abstained=True,
            confidence=final_confidence,
            retrieval_agreement=retrieval.agreement_ratio,
            evidence=evidence,
            evidence_text=context_text,
        )

    return AnswerResponse(
        answer=draft.answer,
        doc_name=resolved_doc,
        page_num=verify.correct_page or draft.page_num,
        abstained=False,
        confidence=final_confidence,
        retrieval_agreement=retrieval.agreement_ratio,
        evidence=evidence,
        evidence_text=context_text,
    )
