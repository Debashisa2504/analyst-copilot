# The Analyst Copilot — Grounded Filing Intelligence

> "An assistant that invents a number is worse than no assistant at all. So ours would rather say *'not found in this filing.'*"

**Team: The AI Monkeys** · Aakansha Sharma · Debashisa Behera · _[Yashaswi Sahu]_
The Analyst Copilot Hackathon 2026

---

## 1. The Problem

An annual filing runs past a hundred pages and dozens of tables. The numbers that matter live inside financial statements, footnotes and management commentary — not in plain sentences. Analysts burn hours finding figures that are, in principle, already written down somewhere.

The obvious move is to point a RAG pipeline at the PDF. That fails, and it fails *expensively*: a wrong figure flows straight into a valuation or a credit decision.

**Our insight:** the scoring rubric isn't really about accuracy — it's about **calibration**. A confidently wrong answer costs −1; an honest abstention costs 0. That's a two-point swing for the same question. So the system is engineered around one question: *can we prove this answer, or can't we?*

| Capability | Generic RAG | The Analyst Copilot |
|---|---|---|
| Retrieve relevant passages | ✅ | ✅ |
| Cite a page number | ✅ | ✅ |
| **Table rows kept intact** (label never split from its values) | ❌ | ✅ cell-by-cell fact serialisation |
| **Whole financial statements in one chunk** | ❌ | ✅ section-aware grouping |
| **Calculations without top-k loss** | ❌ | ✅ SQL over structured `financial_facts` |
| **Independent audit of every answer** | ❌ | ✅ two-pass draft → verify |
| **Principled abstention** | ❌ | ✅ dual-condition precision gate |
| **Page recovery on filings with no page markers** | ❌ | ✅ dual-method + footer-anchor offset |
| **Footnote resolution** ("subject to the conditions in (3)") | ❌ | ✅ NetworkX footnote graph |
| **Malformed EDGAR HTML / SGML / iXBRL** | ❌ | ✅ multi-pattern unwrapper + lxml |
| **A/B evaluation of chunking strategies** | ❌ | ✅ isolated corpus per strategy |

---

## 2. The Precision Gate

This is the heart of the system. Every answer must survive **two independent models** before it reaches the analyst:

- **Pass 1 — Draft.** Extract the answer, the value, the page it came from, a confidence, and a `found` flag (the model's own judgement on whether the evidence was actually present).
- **Pass 2 — Verify.** A *different model* audits that draft against the same excerpts: is the value really there? Does the fiscal period match the question? Is the cited page right? Does the arithmetic hold?

```python
abstained = (not draft.found and not verify.verified) \
         or (not verify.verified and draft.confidence < ABSTAIN_THRESHOLD)
```

Two independent things must fail for a bad answer to escape: the drafter must believe it found the data **and** the auditor must fail to catch it. Because the two passes run on different providers by default (Gemini drafts, GPT-4o verifies), the audit is never the same model marking its own homework.

When the gate trips, the user gets a plain **"not found in this filing"** — never a hedge, never a guess.

---

## 3. Architecture

Four components, communicating through PostgreSQL. Ingestion and query are fully decoupled.

```
SEC HTML / iXBRL filing                     Frontend
        │                                   React + Vite SPA
        ▼                                   Upload · Chat · Evidence
  Ingestion Pipeline                                 ▲
  (backend/parser → chunker → indexer)               │ REST + SSE
        │                                            ▼
        └──────────────────────────────►  FastAPI Backend
                                          (backend/main.py)
                       │                             ▲
                       ▼                             │
              PostgreSQL + pgvector  ────────────────┘
              BM25 pickles (per filing)
```

### Ingestion — step by step

| Step | Module | What it does |
|---|---|---|
| 1. Unwrap | `parser.py` | Real EDGAR downloads are SGML submission wrappers, multi-document 8-Ks, or iXBRL viewer shells. Three detection patterns pull out the actual filing HTML. Parsed with **lxml**, not stdlib `html.parser`, because filing-agent HTML is riddled with unclosed tags. |
| 2. Table serialisation | `parser.py` | Each table is flattened **cell-by-cell** into atomic facts — `"Net revenue, 2015: 3,991 (in millions)"` — *before* chunking. Colspan is expanded so a header spanning a `$` cell and a number cell maps to the right column, and pure spacer cells are never misread as data. This is what stops a row label being severed from its values. |
| 3. Page recovery | `parser.py` | CSS page-break markers when the filing has them; word-count estimation when it doesn't. Then a pre-scan finds the first run of three *consecutive* printed footer numbers and derives the electronic→printed page offset — which is what makes citations land on the page an analyst would actually turn to. |
| 4. Chunking | `chunker.py` | Prose: 400-word sliding window, 50-word overlap. Tables: consecutive rows of the **same statement section** accumulate into one chunk (up to 800 words), so a full income statement is 1–3 chunks instead of 50. |
| 5. Dual indexing | `indexer.py` | **Dense** → Azure PostgreSQL + pgvector (`halfvec`, HNSW cosine). **Sparse** → BM25, pickled per filing. Embedding calls are batched by *token budget* and paced to the deployment's real TPM quota. |
| 6. Structured facts | `facts_indexer.py` | Every table cell also lands in a relational `financial_facts` table — company, fiscal year, statement type, row label, numeric value, page. Read directly by SQL, so calculation questions never depend on retrieval luck. |
| 7. Background enrichment | `footnote_graph.py`, `intelligence/` | Footnote graph, red flags, tone, conviction. Async — never blocks the query path. |

### Query — step by step

| Step | Module | What it does |
|---|---|---|
| 1. Expansion | `retriever.py` | GAAP synonyms (`capex` → *"Purchases of property, plant and equipment"*) are added to the **BM25 query only** — the dense query stays semantically clean, because polluting an embedding with synonym soup blurs its meaning. |
| 2. Hybrid fusion | `retriever.py` | Dense and BM25 are fused by **Reciprocal Rank Fusion** (k=60) — rank-based, so no score normalisation is needed between two incomparable scales. Chunks both retrievers independently agree on get a **1.5× dual-agreement boost**. Calculation-shaped questions get a **2× BM25 bias**, because exact terms like *"D&A"* matter more than semantic similarity there. |
| 3. Structured injection | `facts_retriever.py` | For calculation questions, an LLM router decides which statements are needed, and the **complete** rows are pulled by SQL and prepended to the context. No top-k truncation. |
| 4. Footnote enrichment | `footnote_graph.py` | Footnote markers inside retrieved chunks are resolved and appended, so a reference to "(3)" doesn't lose its meaning. |
| 5. Answer + gate | `answerer.py` | Two-pass draft → verify → precision gate (§2). |

### Database

PostgreSQL + pgvector for the corpus; SQLite for lightweight learning state.

| Table | Purpose |
|---|---|
| `chunks_section_chunked` | One row per chunk — text, page, chunk type, statement section, `halfvec(3072)` embedding |
| `financial_facts` | One row per table cell — company, fiscal year/quarter, statement type, row label, column header, `value_numeric`, units, page |
| BM25 pickles (`data/bm25_*/`) | Per-filing sparse index, rebuilt atomically with each ingest |
| SQLite (`learning`, `cache`) | Retrieval memory weights, query patterns, self-repair expansions, structured metric cache |

---

## 4. Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI · PostgreSQL + pgvector · psycopg3 |
| Parsing | BeautifulSoup + **lxml** · NetworkX (footnote graph) |
| Retrieval | pgvector HNSW (dense) · `rank_bm25` (sparse) · Reciprocal Rank Fusion |
| AI | Azure OpenAI `gpt-4o` (verify) · Google Gemini `1.5-flash` (draft) · `text-embedding-3-large` — provider-agnostic with per-pass routing and an optional local Ollama fallback |
| Frontend | React + Vite |
| Evaluation | Custom FinanceBench-rubric harness (numeric + LLM semantic matching) |

---

## 5. Setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- PostgreSQL with the `vector` extension (`CREATE EXTENSION vector`)
- Azure OpenAI and/or Gemini credentials

### Steps

```powershell
# 1. Clone and enter the repo
git clone <repo-url>
cd analyst-copilot

# 2. Python environment
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 3. Create .env in the project root (see .env.example for all options):
DATABASE_URL=postgresql://user:password@host:5432/analyst?sslmode=require
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-key>
AZURE_EMBEDDING_DEPLOYMENT=text-embedding-3-large
GEMINI_API_KEY=<your-key>
DRAFT_PROVIDER=gemini
VERIFY_PROVIDER=azure_openai
AZURE_EMBED_TPM_LIMIT=140000        # match your deployment's real quota

# 4. Initialise the schema (idempotent — safe to re-run)
python -c "from backend.db import setup_schema; setup_schema()"

# 5. Place filings — drop .htm files from sec.gov/edgar into ./filings/
#    Filename convention drives metadata: COMPANY_YEAR_FORM.htm
#    e.g. AMD_2015_10K.htm, BESTBUY_2024Q2_10Q.htm

# 6. Frontend packages
cd frontend; npm install; cd ..
```

---

## 6. Running the System

### Step 1 — Ingest filings

```powershell
python -m backend.ingest --all                       # whole corpus
python -m backend.ingest --file AMD_2015_10K.htm     # one filing
python -m backend.ingest --all --resume              # skip already-indexed (safe restart)

python -m scripts.ingest_facts                       # structured facts — no embeddings, fast
```

Ingestion is **idempotent**: a filing's existing rows are deleted before the new set is written, so re-running after a logic change never leaves stale chunks behind. Wall-clock is dominated by embedding rate-limit pacing — roughly **1–2 minutes per filing**, comfortably inside the 10-minute requirement.

Filings can also be added through the UI, which runs the same pipeline and reports status live.

### Step 2 — Start the API (Terminal 1)

```powershell
python -m backend.main
```

API: http://127.0.0.1:8000 · Interactive docs: http://127.0.0.1:8000/docs

### Step 3 — Start the chatbot (Terminal 2)

```powershell
cd frontend; npm run dev
```

Chatbot: **http://localhost:5173** — pick a filing scope, ask in plain English, get an answer with its page.

### Running the evaluation

```powershell
python -m eval.run_eval --questions data/eval_logs/eval_sample_25filings.jsonl
python -m pytest tests/ -q                           # 35 unit tests
```

---

## 7. Evaluation

Scored with the hackathon rubric, which prices a wrong answer at what it actually costs:

| What the system returns | Score |
|---|---|
| Correct answer, correct location | **+1** |
| "Not found in this filing" | **0** |
| Correct answer, wrong location | **0** |
| Confidently wrong answer | **−1** |

**Answer matching is two-tier.** A deterministic numeric comparison handles units and sign conventions (`8,738 million` ≡ `$8.7 billion`; capex shown as `(1,577)` ≡ `$1,577 million`) and short-circuits on a confirmed match. Everything else falls through to an LLM semantic judge, so *"declined"* ≡ *"decreased"* and a stray digit — the "2" in "Q2" — can't poison a correct answer into a false negative. Page matching allows ±12 pages.

**Current results — 41 questions across 25 filings** (subset of the 136-question practice set):

| Metric | Value |
|---|---|
| Net score | **+9** |
| Correct (+1) | 17 |
| Honest abstentions (0) | 10 |
| Correct answer, wrong page (0) | 6 |
| Wrong (−1) | 8 |
| Precision | **0.680** |

`eval/reserve_filings.py` holds a filing-level holdout that ingestion skips by default, so thresholds are never tuned against filings the system will be judged on.

We report these numbers as they are. The 8 abstentions are the design working: those are questions where the system declined rather than guessing.

---

## 8. API Reference

| Endpoint | Description |
|---|---|
| `GET /` | Service metadata, indexed-filing count |
| `GET /health` | `{status: ready\|empty, indexed_count}` |
| `GET /filings` | List of indexed filing names |
| `POST /upload` | Upload `.htm`/`.html`; parses, chunks, embeds and indexes immediately |
| `POST /answer` | `{question, doc_name, top_k}` → answer + page + evidence |
| `POST /answer/stream` | Same, streamed as Server-Sent Events (retrieval → draft → verify) |
| `GET /filings/{id}/intelligence` | Red flags, tone, conviction (async; may return `pending`) |
| `POST /feedback` | `{chunk_ids, verdict}` → feeds the retrieval-memory learning loop |

```jsonc
// POST /answer  →
{
  "answer": "The capital expenditure for 3M in FY2018 was $1,577 million.",
  "doc_name": "3M_2018_10K",
  "page_num": 59,
  "abstained": false,
  "confidence": 0.91,
  "retrieval_agreement": 0.7,
  "evidence": [{
    "text": "Purchases of property, plant and equipment (PP&E), 2018: -1577 (in millions)",
    "page_num": 59,
    "chunk_type": "table_row"
  }]
}
```

---

## 9. Project Structure

```
analyst-copilot/
├── backend/
│   ├── config.py            All tunables, env-driven — one file to tune the system
│   ├── models.py            Pydantic schemas
│   ├── parser.py            SGML/iXBRL unwrap · table→fact serialisation · page recovery
│   ├── chunker.py           Prose sliding window + section-aware table grouping
│   ├── statement_utils.py   Shared statement-type detection — one source of truth
│   ├── indexer.py           pgvector dense + BM25 sparse · token-budgeted embedding
│   ├── retriever.py         GAAP expansion · RRF fusion · calculation bias
│   ├── answerer.py          Two-pass QA + precision gate
│   ├── facts_indexer.py     Tables → relational financial_facts
│   ├── facts_retriever.py   SQL statement retrieval for calculations
│   ├── footnote_graph.py    NetworkX footnote resolution
│   ├── cache.py             SQLite structured metric cache
│   ├── llm_client.py        Provider-agnostic LLM with fallback
│   ├── intelligence/        Red flags · tone · conviction · anomaly · contradiction
│   ├── learning/            Retrieval memory · query patterns · self-repair
│   ├── ingest.py            CLI bulk ingestion
│   └── main.py              FastAPI app (REST + SSE)
├── eval/
│   ├── scorer.py            Rubric scoring — numeric + LLM semantic matching
│   ├── harness.py           Batch runner
│   ├── run_eval.py          CLI
│   └── reserve_filings.py   Filing-level holdout
├── scripts/
│   ├── ingest_facts.py      Populate financial_facts (no embeddings)
│   ├── rebuild_bm25.py      Rebuild sparse indexes from Postgres
│   └── dedupe_chunks.py     Duplicate cleanup utility
├── frontend/src/            React + Vite SPA — upload, chat, evidence viewer
├── tests/                   35 unit tests
├── filings/                 .htm corpus (git-ignored)
└── data/                    Generated indexes + eval logs (git-ignored)
```

---

## 10. Engineering Notes & Bugs Fixed

### Chunking — the one that mattered most
- **Row-per-chunk made calculations structurally impossible.** A 50-row income statement became 50 chunks; top-8 retrieval could never hold a whole statement, so EBITDA-style questions were unanswerable *by construction*. Rebuilt chunking to group consecutive same-section table rows into one chunk. A full statement now fits in 1–3 chunks.
- **Found a duplicate-chunk explosion.** `3M_2018_10K` produced 4,332 chunks of which **3,892 were duplicates** — the phrase "Long-Term Debt" appeared once in the source HTML but **181 times** in the database. Root cause: prose was force-flushed before *every* table row, and the sliding-window overlap re-emitted the same leftover segment each time. Removing that forced flush took the filing to **304 chunks, 0 duplicates**.
- Diagnosed this by counting the literal string in the raw HTML and comparing against the DB — not by guessing at the code.

### Parsing
- Switched BeautifulSoup from stdlib `html.parser` to **lxml** for HTML5-grade error recovery on malformed filing-agent markup.
- **Statement headings never expired.** One "Consolidated Statements of Cash Flows" heading tagged *every subsequent table in the document* — JnJ had **2,529 rows** classified as cash-flow (a real one has ~40), and PepsiCo had 2,214 as balance-sheet. This flooded the structured-facts context with irrelevant rows on every calculation question. Fixed by scoping a heading to the pages it actually covers.
- Extracted `statement_utils.py` so the chunk parser and the facts indexer share **one** keyword list and matcher — they had started to drift, and drift here means the two systems disagree about what a "cash flow statement" is.
- Two-tier heading matching: broad substring match for real `<h*>` tags, strict *starts-with* for ambiguous prose — so *"See consolidated statement of cash flows."* (a cross-reference) no longer flips the active section.

### Ingestion & indexing
- **Made ingestion idempotent.** It was an upsert keyed on `chunk_id`, so re-ingesting after a chunking change left orphaned rows from the old scheme silently mixed in with the new. Now deletes the filing's rows first — always a clean replace.
- **Rebuilt embedding batching.** Fixed 512-chunk batches were calibrated for ~45-token chunks; once chunking was fixed, real chunks were 7–8× larger and a single request exceeded the entire per-minute quota. Batches are now assembled against a **token budget** and paced from the deployment's actual TPM limit.
- Added `CHUNKS_TABLE` / `BM25_DIR_NAME` isolation so a competing chunking strategy can be ingested into its own table and BM25 directory and A/B evaluated — without touching the production index or risking a rollback.

### Retrieval
- Removed `cross_company` from the query classifier. Query *wording* ("compare", "versus") could silently widen retrieval scope beyond the filing the user actually selected. Scope is now decided **only** by the caller.
- Added a BM25 bias for calculation-shaped questions, where exact terms beat semantic similarity.

### Evaluation harness
- **The scorer was manufacturing false negatives.** When both answers contained *any* number, it compared numerically and never fell through to the semantic judge — so *"Yes, CVS paid dividends in **Q2** of FY2022"* was marked wrong because the "2" didn't match "0.55". Correct-conclusion answers citing different supporting figures were also failed.
- Fixed so a numeric **match** short-circuits to correct, but a numeric **mismatch** only stands when both answers are bare numbers; anything with real surrounding text goes to the semantic judge. **Precision went 0.500 → 0.593** with no change to the QA pipeline itself — we had been measuring ourselves wrong.

### Known remaining issues

| Priority | Issue | Notes |
|---|---|---|
| High | Multi-step calculations | Metrics like *EBITDA less capex* can still slip arithmetically even when the correct source rows are retrieved and present in context |
| Medium | Metric-applicability questions | *"Compute X, or explain why X doesn't apply"* is genuinely ambiguous; the model sometimes takes the escape hatch when it should just compute |
| Medium | Note-table classification | Statement-type detection is heading-proximity heuristic; an unusual layout can still mislabel a note table |
| Medium | Corpus trust | Filenames drive fiscal-year metadata — we found a filing saved under the wrong year, which yields confident answers about the wrong period. Worth a validation pass on ingest |
| Low | Page drift | ±1–3 pages on filings without CSS page-break markers, absorbed by the ±12-page tolerance |
| Low | Rate-limit coupling | The scorer's semantic judge shares a deployment with the verify pass; a large eval run can contend with itself |

---

Questions or anything unclear in setup — reach out and we're happy to walk through it.
