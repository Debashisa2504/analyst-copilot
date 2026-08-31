# Approach Note — The Analyst Copilot

**Team: The AI Monkeys** · Aakansha Sharma · Debashisa Behera · _[member 3]_

---

## 1. The insight that shaped everything

We started by reading the scoring rubric carefully, because it tells you what the problem
actually is:

| System returns | Score |
|---|---|
| Correct answer, correct location | **+1** |
| "Not found in this filing" | **0** |
| Correct answer, wrong location | **0** |
| Confidently wrong answer | **−1** |

A wrong answer and a right answer are **two points apart**, but a wrong answer and an
abstention are only **one point apart**. So the highest-value engineering is not "make the
model smarter" — it is **make the system know when it cannot prove an answer.**

That reframes the whole build. Every design decision below serves one question:
*can we prove this, or can't we?*

We also drew a second conclusion from the rubric: **location is half the score.** An answer
with the wrong page earns the same zero as no answer at all. Page recovery therefore got the
same engineering attention as retrieval — most teams treat it as an afterthought.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  INGESTION  (offline, once per filing, ~1–2 min)                            │
└─────────────────────────────────────────────────────────────────────────────┘

   SEC .htm filing
        │
        ▼
   ┌──────────────┐   SGML / iXBRL unwrap  →  lxml parse
   │  parser.py   │   Tables → atomic facts ("Net revenue, 2015: 3,991")
   │              │   Page recovery (CSS markers | word-count + footer anchor)
   └──────┬───────┘   Footnote marker ↔ definition linking
          │
          ├──────────────────────────┐
          ▼                          ▼
   ┌──────────────┐          ┌──────────────────┐
   │  chunker.py  │          │ facts_indexer.py │
   │              │          │                  │
   │ prose: 400w  │          │ every table cell │
   │ window /50w  │          │ → relational row │
   │ overlap      │          │                  │
   │ tables:      │          └────────┬─────────┘
   │ section-     │                   │
   │ grouped      │                   ▼
   └──────┬───────┘          ┌──────────────────┐
          │                  │ financial_facts  │  company, fiscal_year,
          ▼                  │     (SQL)        │  statement_type, row_label,
   ┌──────────────┐          └──────────────────┘  value_numeric, page_num
   │  indexer.py  │
   │              │
   │  DENSE  →  pgvector (halfvec 3072, HNSW cosine)
   │  SPARSE →  BM25 pickle per filing
   └──────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│  QUERY  (online, per question)                                              │
└─────────────────────────────────────────────────────────────────────────────┘

   Question
        │
        ▼
   ┌───────────────┐  GAAP synonym expansion → BM25 side ONLY
   │ retriever.py  │  (dense query stays semantically clean)
   │               │
   │   dense ──┐   │
   │           ├── Reciprocal Rank Fusion (k=60)
   │   BM25 ───┘        × 1.5  if both retrievers agree
   │                    × 2.0  BM25 weight on calculation questions
   └───────┬───────┘
           │  top-8 chunks
           ▼
   ┌──────────────────────┐   Calculation question?
   │ facts_retriever.py   │──► SQL: pull COMPLETE statements, no top-k limit
   └───────┬──────────────┘
           │
           ▼
   ┌──────────────────────┐   Footnote markers in chunks → resolved definitions
   │ footnote_graph.py    │
   └───────┬──────────────┘
           │  assembled context
           ▼
   ┌─────────────────────────────────────────────────┐
   │              answerer.py                        │
   │                                                 │
   │  Pass 1  DRAFT   (Gemini)                       │
   │          → answer, value, page, confidence,     │
   │            found                                │
   │                                                 │
   │  Pass 2  VERIFY  (GPT-4o — different model)     │
   │          → value present? period right?         │
   │            page right? arithmetic sound?        │
   │                                                 │
   │  ─────────── PRECISION GATE ───────────────     │
   │  abstain if (not found AND not verified)        │
   │          or (not verified AND conf < 0.55)      │
   └─────────────────────────────────────────────────┘
           │
           ▼
   Answer + page + evidence   ──OR──   "not found in this filing"
```

---

## 3. The four problems we had to solve

### Problem 1 — Tables break every standard chunker

Split a financial table by character count and the row label lands in one chunk while its
numbers land in another. The model then confidently pairs "Revenue" with the wrong figure.

**Our approach:** serialise tables **cell-by-cell into atomic facts before chunking** —
`"Net revenue, 2015: 3,991 (in millions)"`. The label travels with its value permanently. We
expand `colspan` so a header spanning a `$` cell and a number cell maps to the correct column,
and skip spacer cells so a stray `$` is never read as data.

### Problem 2 — Calculations were structurally impossible

Our first version made **one chunk per table row**. A 50-row income statement became 50
chunks. A question like *"EBITDA = operating income + D&A"* needs rows from **two different
statements** — with top-8 retrieval, that could never fit. The system wasn't answering badly;
it *could not* answer, by construction.

We considered two fixes and **kept both**, because they cover different failure modes:

| | Approach | Covers |
|---|---|---|
| **A** | **Section-aware chunking** — consecutive rows of the same statement merge into one chunk (800-word budget) | A full income statement is now 1–3 chunks, so top-8 can hold two complete statements |
| **B** | **Structured `financial_facts` table** — every cell also stored relationally, queried by SQL | Calculation questions get *complete* statements with **no top-k limit at all** — retrieval can't lose a row it never had to rank |

Chunk-based RAG still handles narrative questions; SQL handles arithmetic. Using the right
tool for each was worth more than tuning either one.

### Problem 3 — Citations must land on the right page

Filings have no reliable page metadata. We use **two methods plus a correction**:
1. CSS page-break markers when the filing has them (≥5 markers → trusted).
2. Word-count estimation otherwise.
3. **Footer anchor offset** — scan for the first run of *three consecutive* printed page
   numbers, and derive the electronic→printed offset from it. Requiring three consecutive
   numbers is what rejects false positives like table-of-contents references and stray iXBRL
   values, which don't form a monotonic run.

### Problem 4 — Knowing when to decline

A single model asked "are you sure?" will usually say yes. So we use **two passes on two
different providers**: Gemini drafts, GPT-4o audits. The drafter also returns a `found` flag —
its own judgement on whether the evidence was actually present, rather than us keyword-matching
its prose.

```python
abstained = (not draft.found and not verify.verified) \
         or (not verify.verified and draft.confidence < ABSTAIN_THRESHOLD)
```

Two independent things must fail for a bad answer to escape.

---

## 4. What we tried, and what we threw away

| We started with | Problem it caused | We replaced it with |
|---|---|---|
| One chunk per table row | Top-k starvation — calculations impossible | Section-aware grouping + structured SQL facts |
| ChromaDB + local MiniLM embeddings | Weaker retrieval, separate service to run | Azure PostgreSQL + pgvector (`halfvec`, HNSW) |
| Python's `html.parser` | Fails on malformed filing-agent HTML | **lxml** (HTML5-grade error recovery) |
| Query wording deciding search scope (`"compare"` → all filings) | Silently searched beyond the filing the user selected | Scope decided **only** by the caller |
| Fixed 512-chunk embedding batches | Calibrated for tiny chunks; after chunking was fixed, one request exceeded the entire minute quota | Token-budgeted batches paced to the real TPM limit |
| Upsert-only ingestion | Re-ingesting left orphaned chunks from the old scheme | Delete-then-insert — always a clean replace |
| Prose flushed before every table row | **3,892 duplicate chunks out of 4,332** | Prose and tables buffer independently |
| Statement headings that never expired | One "Cash Flows" heading tagged **2,529 rows** across the whole document | Heading scoped to the pages it actually covers |

**The most important thing we threw away was a measurement, not a feature.** Our scorer
compared answers numerically whenever both sides contained *any* digit — so *"Yes, CVS paid
dividends in **Q2** of FY2022"* was marked **wrong** because the "2" didn't match "0.55". We
were failing correct answers. Fixing it (numeric **match** short-circuits; numeric **mismatch**
falls through to a semantic judge unless both answers are bare numbers) moved precision
**0.500 → 0.593 with zero change to the QA pipeline.** We had been measuring ourselves wrong,
and every tuning decision made before that fix was made on bad data.

---

## 5. What we measured

We evaluate on 41 analyst questions across 25 filings, drawn from the provided practice set,
scored with the official rubric. A filing-level holdout is excluded from ingestion by default
so thresholds are never tuned on filings we're judged against.

| | Starting point | After scorer fix | Current |
|---|---|---|---|
| Net score | 0 | +5 | **+9** |
| Correct (+1) | 15 | 16 | **17** |
| Honest abstentions | 9 | 8 | 10 |
| Wrong (−1) | 15 | 11 | **8** |
| **Precision** | 0.500 | 0.593 | **0.680** |

Wrong answers fell from **15 → 8** while correct answers rose. That is the shape we wanted:
the system converted confident errors into either correct answers or honest abstentions.

Diagnosis was always evidence-first, never guesswork. When chunk counts looked wrong, we
counted the literal string in the raw HTML (`"Long-Term Debt"` appeared **once** in the source
but **181 times** in the database) rather than speculating about the code. When calculations
failed, we queried `financial_facts` directly to check whether the source rows were even
present — which is how we found that one filing in the corpus was saved under the wrong fiscal
year, making three questions unanswerable regardless of model quality.

---

## 6. What we would do next

1. **Multi-step arithmetic** is our largest remaining loss bucket. The correct rows reach the
   context, but chained metrics (EBITDA less capex) can still slip. A deterministic calculator
   the model calls, rather than doing mental arithmetic, is the obvious next step.
2. **Ingest-time corpus validation** — cross-check the fiscal year inside the document against
   the filename, so a mislabelled filing is caught rather than answered confidently.
3. **Note-table classification** is heading-proximity heuristic; a learned classifier would be
   more robust across unusual layouts.

---

## 7. Principles we held to

- **Measure before optimising.** Every fix in §4 came from an observed number, not intuition.
- **Fix causes, not symptoms.** When chunk counts were wrong, we found the duplicate-emitting
  code path instead of adding a deduplication filter on top of it.
- **Prefer the honest zero.** We never tuned the abstention threshold to chase points; a system
  that guesses well today fails silently tomorrow.
- **Keep it inspectable.** Everything tunable lives in one config file, and every answer ships
  with the evidence and page it came from — so a human can always check our work.
