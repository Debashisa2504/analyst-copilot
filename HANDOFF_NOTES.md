# Analyst Copilot — Engineering Handoff Notes
**Date**: 2026-08-27
**Status**: Mid-fix, active work paused due to quota expiry
**Prepared by**: Claude Sonnet 4.6 (AI assistant)

---

## 1. PROJECT OVERVIEW

This is a RAG (Retrieval-Augmented Generation) system for answering financial questions from SEC filings (10-K, 10-Q, 8-K). The eval benchmark is **FinanceBench**.

### Architecture Summary
```
HTML filing → parser.py → chunker.py → indexer.py → PostgreSQL (pgvector) + BM25 pickles
                                                             ↓
User question → retriever.py (hybrid RRF) → answerer.py (LLM) → AnswerResponse
```

**Key files:**
- `backend/parser.py` — HTML/iXBRL parser, segments filing into chunks
- `backend/chunker.py` — Groups segments into Chunk objects (sliding window)
- `backend/indexer.py` — Dense (pgvector) + sparse (BM25) indexing
- `backend/retriever.py` — Hybrid retrieval: BM25 + dense vector, fused via RRF
- `backend/answerer.py` — Two-pass LLM: draft answer → verify
- `backend/models.py` — Pydantic data models
- `eval/harness.py` — Batch evaluation runner
- `eval/scorer.py` — FinanceBench rubric scoring (+1/0/-1)

---

## 2. SCORING RUBRIC (CRITICAL TO UNDERSTAND)

FinanceBench scoring:
- **+1**: Correct answer AND correct page location
- **0**: Abstained, OR correct answer with wrong page
- **-1**: Wrong answer (confidently incorrect)

**Important finding**: ALL questions in `data/eval_logs/tune_questions.jsonl` have `evidence_page_num` populated (non-null). The correct key in evidence objects is `evidence_page_num`, NOT `page_num`. The harness correctly reads `ev.get("evidence_page_num")`.

To score +1, the system must return both:
1. The correct answer
2. The correct printed page number (from the filing PDF)

---

## 3. FIXES APPLIED IN THIS SESSION (ALL COMPLETE)

### Fix 1 — Pydantic int→str crash (models.py)
**Problem**: LLM sometimes returns `"value": 2118` (integer) but `DraftAnswer.value` expects `str` → validation crash → abstention.
**Fix**: Added `@field_validator` to coerce int/float → str in `DraftAnswer` and `VerifyResult`.
**Status**: ✅ Done

### Fix 2 — Cross-company misrouting (query_patterns.py)
**Problem**: The word "between" in queries like "between FY2023 and Q2 FY2024" triggered `cross_company` classification, causing the retriever to ignore `doc_name` and query ALL filings with only 3 chunks each.
**Fix**: Removed "between" and "both" from `cross_words` list.
**Status**: ✅ Done

### Fix 3 — ABSTAIN_THRESHOLD too high (config.py)
**Problem**: Default threshold was 0.75; most financial calculations gave confidence < 0.75 → abstain.
**Fix**: Lowered to 0.55.
**Status**: ✅ Done (may need further tuning with `python -m eval.tune_thresholds`)

### Fix 4 — Wrong answer field used (answerer.py)
**Problem**: For directional questions ("Did X increase?"), the system was returning `verify.correct_value` ("-16") instead of `draft.answer` ("Yes, it decreased").
**Fix**: Changed final answer to always use `draft.answer`.
**Status**: ✅ Done

### Fix 5 — Scorer can't match None vs "no acquisitions" (scorer.py)
**Problem**: Predicted "None" and ground truth "Ulta Beauty did not make any acquisitions" were not matched as equivalent negations.
**Fix**: Added `_both_negative()` function with negation regex.
**Status**: ✅ Done

### Fix 6 — BM25 tokenizer kept punctuation (indexer.py)
**Problem**: `_tokenize` used `text.lower().split()` which kept punctuation attached. "property," ≠ "property", "equipment?" ≠ "equipment,". BM25 couldn't match query tokens to chunk tokens.
**Fix**: Changed to `re.findall(r'\b\w+\b', text.lower())`.
**Status**: ✅ Done. **Requires rebuilding BM25 pickles** (see Section 5).

### Fix 7 — Duplicate chunks flooding BM25 results (parser.py + indexer.py + retriever.py)
**Problem**: Parser visited nested DOM elements (div→p→span) multiple times, creating 15+ identical chunks with different IDs. These monopolized all BM25 top-k slots.
**Root fix**: Added leaf-element check in `parser.py`: skip elements that have child text-bearing elements.
**Bandaid** (still useful after root fix): Added text-deduplication in `query_bm25()` and `_hybrid_retrieve()`.
**Status**: ✅ Root fix + dedup both done. **Requires re-running `dedupe_chunks.py` and `rebuild_bm25.py`** (see Section 5).

---

## 4. IN-PROGRESS WORK — PAGE NUMBER OFFSET (PARTIALLY DONE, NEEDS VALIDATION)

### The Problem
FinanceBench page numbers are **printed PDF page numbers**. Our parser assigns **electronic page numbers** (CSS marker count from element 1). These differ by a constant offset per document (e.g., Boeing: electronic page 52 = printed page 51, offset = -1).

The old `maybe_lock_offset()` logic in `parser.py` only scanned **prose elements** (p/div/span/li) for footer page numbers, missing page numbers embedded in **SEC layout-table cells**. Result: offset stayed 0, all chunks stored with wrong page numbers → score never reaches +1.

### What Was Changed
In `backend/parser.py`, the `parse_filing()` function now has a **pre-scan pass** BEFORE the main element traversal:
- Walks all leaf elements (prose AND single-cell table rows)
- Tracks the electronic page counter (CSS markers) identically to the main loop
- Finds the first printed page number and computes `offset = printed - electronic`
- Sets `offset` before any segment is emitted, so ALL segments get the correct printed page

**The `maybe_lock_offset()` function was removed.** Now `effective_page = current_page + offset` everywhere (no `offset_locked` conditional).

### Current Issue With Pre-scan (UNRESOLVED)
Boeing's 10-K uses **iXBRL format** (Inline XBRL). This wraps every financial value in multiple `<div>`, `<span>`, and `<ix:nonfraction>` tags. Standalone `<div>14</div>` elements appear throughout the document — these are **TOC page references and financial data values**, NOT footer page numbers.

The pre-scan currently picks up `<div>14</div>` at electronic page 2 (a TOC entry saying "Item 1. Business is on page 14") and incorrectly sets offset = 14 - 2 = +12.

The CORRECT offset for Boeing is **-1** (electronic 52 = printed 51).

### Findings About Boeing's Actual Page Structure

From direct HTML analysis:
- PP&E data ("Property, plant and equipment, net, 12,645") is at **electronic page 52**
- Boeing's footer pattern (last standalone `<div>` number per page) shows consistent **offset = -2**:
  - Electronic 52 → footer "50" (printed 50)
  - Electronic 53 → footer "51" (printed 51)
- But FinanceBench says PP&E is on **printed page 51**

**Ambiguity**: FinanceBench's page 51 could correspond to electronic page 53 (offset=-2) rather than 52. This means the chunker's table serialization might be assigning the PP&E facts to electronic page 52, but the actual PDF reader would see them on printed page 51.

This 1-page discrepancy may be because:
1. The page-break marker bumps the counter BEFORE the table is processed, so the table is at electronic 52 but the FOOTER of that page is "50" (meaning the page the human reads is "51" on the NEXT electronic page)
2. OR FinanceBench counts the cover page differently

### What Needs To Be Resolved

1. **Determine if offset = -2 or -1 for Boeing**: Check whether the PP&E table is at electronic page 52 (footer "50") or 53 (footer "51"). The parser assigns `current_page + offset` where `current_page` is the value WHEN the table element is encountered, not the footer value.

2. **Fix the pre-scan false positive problem**: The pre-scan must not trigger on TOC page references (`<div>14</div>`) or iXBRL data values.

### Proposed Fix For Pre-scan (NOT YET IMPLEMENTED)

**Option A — Require "Page N" keyword**: Only detect page numbers with explicit "Page N" pattern in pre-scan, not standalone numbers. Problem: most SEC filings don't write "Page 51" explicitly.

**Option B — Scan from AFTER the first several CSS pages**: Skip first 5-10 electronic pages to avoid cover/TOC areas. Footer numbers always appear in the content section, not cover pages.

**Option C — Require monotonically increasing sequence**: Valid footer numbers form a sequence 1, 2, 3... in order. If collected page numbers don't form such a sequence, discard them.

**Option D — Use the LAST standalone number per electronic page** (most reliable): Instead of stopping at the first match (which hits TOC refs early), take the LAST standalone `<div>` number on each page. This is the actual footer. Then use the first page where this last-number sequence is consecutive (N, N+1, N+2) to determine the offset.

**Recommended**: Option D. It's robust and doesn't require document-type detection.

---

## 5. IMMEDIATE NEXT STEPS (IN ORDER)

### Step A — Clean up duplicate chunks in PostgreSQL
```bash
cd analyst-copilot
.venv/Scripts/python.exe -m scripts.dedupe_chunks          # dry run first
.venv/Scripts/python.exe -m scripts.dedupe_chunks --apply  # actually delete
```

### Step B — Rebuild BM25 pickles (new tokenizer + clean data)
```bash
.venv/Scripts/python.exe -m scripts.rebuild_bm25
```

### Step C — Fix the pre-scan page number detection (NEEDS CODING)

The current pre-scan in `backend/parser.py` lines ~348-387 has a bug: it picks up TOC page references as footer numbers.

**Recommended implementation** (Option D above):

```python
# In parse_filing(), replace the current pre-scan loop with:

# Collect last standalone-number div per electronic page
_SOLO_NUM_RE = re.compile(r'^\d{1,4}$')
_page_last_num = {}   # {electronic_page: int}
_p = 1
for _el in (el for el in body.descendants if isinstance(el, Tag)):
    if use_markers:
        _s = _style_of(_el)
        if ("page-break-before" in _s or "page-break-after" in _s) and "always" in _s:
            _p += 1
    if _el.name == "div":
        _t = _el.get_text(" ", strip=True)
        if _t and _SOLO_NUM_RE.match(_t):
            _page_last_num[_p] = int(_t)

# Find offset from first consecutive sequence of footer numbers
offset = 0
offset_locked = False
_pages = sorted(_page_last_num.keys())
for _i, _ep in enumerate(_pages[:-2]):
    _n0 = _page_last_num[_ep]
    _n1 = _page_last_num.get(_ep + 1)
    _n2 = _page_last_num.get(_ep + 2)
    if _n1 == _n0 + 1 and _n2 == _n0 + 2:
        # Found 3 consecutive footer numbers at 3 consecutive electronic pages
        offset = _n0 - _ep
        offset_locked = True
        break
```

This approach:
- Takes the LAST standalone `<div>` number seen on each electronic page
- Finds the first run of 3 consecutive pages where the footer numbers are also consecutive (N, N+1, N+2)
- Computes offset from that stable calibration point
- Immune to TOC references (which are scattered, not consecutive)
- Immune to financial data (which is also non-consecutive as a sequence)

### Step D — Re-parse all filings to update page_num in PostgreSQL
```bash
.venv/Scripts/python.exe -m scripts.reparse_pages --dry-run  # preview
.venv/Scripts/python.exe -m scripts.reparse_pages             # update DB
.venv/Scripts/python.exe -m scripts.rebuild_bm25              # rebuild BM25
```

This script (`scripts/reparse_pages.py`) was created and updates ONLY the `page_num` column — no re-embedding needed.

### Step E — Tune the ABSTAIN_THRESHOLD
```bash
.venv/Scripts/python.exe -m eval.tune_thresholds
```
The threshold was manually set to 0.55, but should be tuned properly.

### Step F — Run full eval
```bash
.venv/Scripts/python.exe -m eval.run_eval --mode tune --max 10
```

### Step G — After all fixes pass eval: ingest reserved filings
```bash
.venv/Scripts/python.exe -m backend.ingest --all --force
.venv/Scripts/python.exe -m eval.run_eval --mode after
```

---

## 6. KEY FILES MODIFIED IN THIS SESSION

| File | What Changed |
|------|-------------|
| `backend/models.py` | `field_validator` to coerce int/float→str in DraftAnswer and VerifyResult |
| `backend/learning/query_patterns.py` | Removed "between"/"both" from cross_company keywords |
| `backend/config.py` | ABSTAIN_THRESHOLD 0.75 → 0.55 |
| `backend/answerer.py` | Final answer uses `draft.answer`; loosened verify prompt |
| `backend/indexer.py` | Fixed `_tokenize` regex; added text-dedup in `query_bm25` |
| `backend/retriever.py` | Added text-dedup after RRF fusion in `_hybrid_retrieve` |
| `backend/parser.py` | Leaf-element fix (no duplicate segments); pre-scan offset detection (BUGGY — needs Step C above) |
| `eval/scorer.py` | Added `_both_negative()` for None/no-X equivalence |
| `eval/harness.py` | Added `run_batch()` function |
| `scripts/dedupe_chunks.py` | NEW: deletes duplicate rows from PostgreSQL |
| `scripts/rebuild_bm25.py` | NEW: rebuilds BM25 pickles from DB |
| `scripts/reparse_pages.py` | NEW: re-parses HTMLs and updates page_num in DB |
| `scripts/debug_retrieval.py` | NEW: diagnostic tool for retrieval debugging |

---

## 7. KNOWN ISSUES AND TECHNICAL DEBT

### 7.1 Parser Offset Detection (HIGH PRIORITY — blocks +1 scoring)
See Section 4 above. The pre-scan in `parser.py` gets wrong offset for iXBRL filings (Boeing, most modern 10-Ks). Must implement Option D from Step C.

### 7.2 iXBRL Duplicate Elements
Boeing's iXBRL wraps every value in 3–4 nested elements (`<div>`, `<span>`, `<ix:nonfraction>`). The leaf-element fix (Skip elements with child prose elements) partially addresses this, but `<ix:nonfraction>` is not in the skip list. May still produce some duplicates for iXBRL values.

**Fix**: Add `"ix:nonfraction"` and other iXBRL tag names to the skip list in parser.py.

### 7.3 BM25 Agreement Ratio Still Low
Even after dedup and tokenizer fix, the dense and BM25 indexes may not agree (different retrieval results → low RRF dual-agreement bonus). The `DUAL_AGREEMENT_MULTIPLIER = 1.5` helps when they agree but doesn't fix root disagreement.

### 7.4 Verify Prompt Too Strict
The verify LLM call still sometimes rejects correct answers with confidence below 0.55. The VERIFY_SYSTEM_PROMPT in `answerer.py` may need further tuning.

### 7.5 Single-Cell Row Heuristic in Pre-scan (partly implemented)
The structural guard (only accept single-cell rows as page number candidates in `td`/`th` elements) IS implemented in the current code. But the iXBRL false positive comes from `<div>` prose elements (TOC), not table cells. So this guard helped but didn't solve the main problem.

---

## 8. HOW TO DEBUG A SINGLE QUESTION

```bash
# Check what retrieval returns for a specific question + filing
.venv/Scripts/python.exe scripts/debug_retrieval.py \
  --doc BOEING_2018_10K \
  --question "What is Boeing's net PP&E for FY2018?"

# Check raw page numbers in DB for a filing
.venv/Scripts/python.exe -c "
from backend.db import get_sync_conn
with get_sync_conn() as conn:
    with conn.cursor() as cur:
        cur.execute(\"SELECT chunk_id, page_num, text FROM chunks WHERE doc_name='BOEING_2018_10K' AND text LIKE '%12,645%' LIMIT 5\")
        for r in cur.fetchall(): print(r)
"
```

---

## 9. ENVIRONMENT NOTES

- **Platform**: Windows 11, PowerShell shell
- **Python**: `.venv/Scripts/python.exe` (NOT the system `python` which lacks project packages)
- **DB**: Azure PostgreSQL with pgvector extension
- **Embeddings**: Azure OpenAI (text-embedding-3-small or similar, via `EMBEDDING_PROVIDER=azure_openai`)
- **LLM**: Azure OpenAI (for answerer.py)
- **BM25 pickles**: stored in `data/bm25/` directory
- **Filings**: stored in `data/filings/` as `.htm` files

**To run any command, always use**:
```bash
cd "c:\Users\Aakansha Sharma\Downloads\analyst-copilot"
.venv/Scripts/python.exe -m <module>
```

---

## 10. EVAL RESULTS SO FAR

| Run | Correct (+1) | Abstained (0) | Wrong (-1) | Net Score |
|-----|-------------|---------------|------------|-----------|
| Pre-session (all fixes) | 0 | 10 | 0 | 0 |
| After fixes 1-5 | ~3 | ~5 | ~2 | ~1 |
| Latest (not run yet) | ? | ? | ? | ? |

The fixes in Section 3 should substantially improve the correct count. The page number fix (Section 4) is needed to convert 0-scoring correct answers into +1.

---

*End of handoff document. Good luck!*
