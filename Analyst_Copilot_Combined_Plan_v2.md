# The Analyst Copilot — Combined Plan (v2)
### PRISM's differentiators, layered on top of a real-data-validated core

*Status: merged plan — combines the PRISM-derived architecture plan (v1) with the Version 1 build (parser fixes, offline resilience, React SPA, Docker). Supersedes v1. Code-level verification of the Version 1 build's fixes is a follow-up step, not yet done.*

---

## 1. What This Merge Is

Two independent efforts converged on this:

- **Our plan (v1)**: PRISM architecture — footnote graph, Intelligence Layer, learning loops, live self-scoring dashboard — revised for HTML-native ingestion, a two-track ingestion model, and a tune/held-out eval split.
- **Version 1 build**: a working implementation of the leaner deterministic core (parser, chunker, indexer, retriever, two-pass answerer), **validated against real SEC filings**, with three real bugs found and fixed, plus offline resilience (provider-agnostic LLM client + Ollama fallback + degraded evidence-only mode), a React/Vite/Tailwind SPA, and Docker packaging.

Neither replaces the other. The Version 1 build's core is **more correct** than what either planning doc specified — it's the first thing in this whole process that's actually been run against real filing HTML. PRISM's Intelligence Layer, learning loops, and dashboard are still valuable **differentiators for judges** that don't yet exist in either build. This document is the core they should both build from going forward.

**Principle for the merge:** where the Version 1 build and our plan describe the same component, the Version 1 build's version wins by default, because it's tested. Where our plan adds a capability the Version 1 build doesn't have, it's layered on top, not swapped in to replace something working.

---

## 2. Problem Statement & Scoring Rubric (unchanged, for reference)

Build a chatbot QA system over SEC filings. Given a filing and a question, return a precise answer plus exact source location, or an honest "not found in this filing."

| System output | Score |
|---|---|
| Correct answer, correct location | +1 |
| "Not found in this filing" | 0 |
| Correct answer, wrong location | 0 |
| Confidently wrong answer | −1 |

Required surface: "Add filing" upload (<10 min, visible processing status), chat box, evidence/citation on every answer, ability to decline. Submission: code + README, running system, 1-page approach note.

---

## 3. Architecture Decision Summary (merged)

| Decision area | Chosen approach | Source | Why |
|---|---|---|---|
| Data format assumption | Native SEC EDGAR HTML/iXBRL, parsed deterministically | Both | Matches actual corpus; avoids unnecessary vision calls |
| Table ingestion | Cell-by-cell fact serialization, **with colspan-interval expansion and spacer-cell skipping** | Version 1 build (validated) | Original 1:1 positional formula fails on real SEC tables; this is the tested fix |
| Retrieval engine | Hybrid RRF (ChromaDB dense + BM25 sparse), 1.5× dual-agreement boost | Both (matched) | Dense catches paraphrase, BM25 nails exact GAAP line items |
| Query expansion | Static GAAP synonym dictionary, **fed to BM25 only** (dense embedding stays undiluted) | Version 1 build (more precise) | Zero latency; avoids diluting semantic search with keyword stuffing |
| QA orchestration | Decoupled Draft → Verify (two independent LLM passes), gate = `min(draft.confidence, verify.confidence)` | Both (matched) | Second independent audit catches unsupported values before they reach the user |
| Precision control | Calibrated abstention, θ = 0.75 | Both (matched) | Wrong answer −1, abstention 0 — guessing is strictly dominated |
| Page citation | CSS markers + anchor-offset correction, ±12-page tolerance at scoring | Both (matched) | Bridges SEC EDGAR vs. printed PDF pagination |
| LLM provider | Provider-agnostic client (`llm_client.py`): Anthropic \| OpenAI \| Gemini \| Ollama, selectable via env var | Version 1 build (new) | Avoids locking into one vendor's rate limits/quota; de-risks the Azure-quota concern raised earlier |
| Offline resilience | Ollama local fallback on primary failure; degraded evidence-only mode (raw evidence, labeled unverified) if all providers fail | Version 1 build (new) | Live demo never hard-fails; only degrades visibly |
| UI | React (Vite + Tailwind) SPA + FastAPI JSON API | Both (matched) | "Product, not report"; matches brief |
| Vision fallback scope | Narrowed to embedded chart/graph **images only** — malformed tables are now a solved deterministic-parsing problem, not a vision problem | Merged (Version 1 build's bug #3 fix removes half of our original fallback list) | Real fix > LLM workaround where possible |
| Ingestion timing model | Two-track: known corpus pre-loaded offline (no clock) vs. live "Add filing" (10-min SLA, HTML-native, minimal/no LLM calls) | Our plan (new) | Matches the brief's explicit framing: SLA applies to "a filing it has never seen" |
| Differentiator layer | PRISM's footnote **graph**, Intelligence Layer (red flags/contradiction/tone/anomaly), learning loops, live self-scoring dashboard — added on top, async/non-blocking | Our plan (new) | Judged-hackathon differentiation beyond the core rubric-optimized system |
| Eval discipline | Stratified tune/held-out split of the 136 practice questions before any threshold tuning | Our plan (new) | Prevents reporting a score that's really just memorization of the answer key |

---

## 4. System Architecture — Two Pipelines

### 4.1 Ingestion pipeline (two tracks, converging on shared storage)

**Track A — Known corpus, pre-loaded offline.** No timer. Run at leisure, including the full Intelligence Layer, ahead of the live session.

**Track B — Live "Add filing," timed, demoed.** This is what the 10-minute SLA is actually graded against. Must be assured:

```
SEC HTML filing
  -> parser.py        (page recovery, table-to-fact serialization w/ colspan handling,
                        footnote linking, defensive attrs handling)
  -> chunker.py        (400-word prose windows, 50-word overlap;
                        table/footnote chunks kept atomic)
  -> indexer.py        (ChromaDB dense index + per-filing BM25 pickle)
  -> [queryable — 10-min gate ends here]
  -> [async, non-blocking] footnote graph builder (NetworkX)
  -> [async, non-blocking] Intelligence Layer (red flags, contradiction, tone, anomaly, conviction score)
```

Vision fallback (GPT-4o vision or equivalent) is invoked **only** for embedded chart/graph `<img>` elements — not for tables, since the colspan/spacer-cell fix handles those deterministically now.

### 4.2 Query pipeline (per question)

```
React UI --POST /answer--> retriever.py
  1. GAAP synonym expansion (BM25 side only)
  2. Query classifier: simple lookup / complex narrative / cross-company / temporal
  3. Routing:
       simple      -> structured-cache fast path (SQLite, <100ms)
       complex     -> hybrid RAG: dense (ChromaDB) + BM25, fused via RRF (k=60),
                      1.5x dual-agreement boost
       cross-company -> company index -> per-filing RAG, results merged
       temporal    -> filter by filing_date metadata
  4. Footnote resolver: scan retrieved chunks for callouts, auto-append resolved
     text from the footnote graph
  5. answerer.py:
       Pass 1 (Draft):  candidate answer + value + page + confidence, strict JSON
       Pass 2 (Verify): independent audit against the SAME raw excerpts —
                        value literally present? period matches? page citation
                        actually the source?
       Gate: verified == True AND min(draft.conf, verify.conf) >= 0.75
             -> return answer
             else -> "not found in this filing"
  6. On provider failure: retry LLM_FALLBACK_PROVIDER (Ollama) ->
     if that also fails: return raw evidence, labeled "LLM unavailable —
     showing raw retrieved evidence only, unverified"
```

---

## 5. Ingestion Detail — Validated Parser Behavior

This section documents the **tested** parser behavior, incorporating the three real bugs found against actual SEC filings (3M 2018 10-K, JPMorgan 2022 10-K — 16MB, largest in corpus).

| Bug | Symptom | Fix |
|---|---|---|
| `tag.attrs` is `None` on real markup | `AttributeError` crash before any filing could be ingested | Defensive `_style_of()` helper treats missing attrs as an empty dict |
| Mutating a live BS4 generator (`.decompose()` during `body.descendants` iteration) | **Silent** truncation — 3M's 10MB filing produced 255 segments instead of several thousand, no error raised | Snapshot `list(body.descendants)` into a plain list before the loop |
| `colspan`/spacer-cell misalignment on real tables | Zero correct facts extracted from the real capex table — positional 1:1 header/value mapping assumption fails | Expand `colspan` into logical column intervals on the header row; skip spacer/bare-currency data cells; match each value to the header interval that actually contains its column |

**Accounting-convention gotcha (scorer-level, also validated):** ground truth reports CapEx as positive magnitude (`$1577.00`); filings store it as a negative outflow `(1,577)` — correct accounting convention. A naive signed comparison marks a correct extraction as wrong. Fix: scorer falls back to absolute-value comparison when signed comparison fails; Draft prompt given explicit sign-convention guidance for the answer text, while the underlying value field stays exactly as extracted (auditable against source).

**Result after fixes:** 3M's 2018 10-K correctly recovers `"Purchases of property, plant and equipment (PP&E), 2018: -1577 (in millions)"` at page 60 — one page off ground truth (59), inside the ±12-page tolerance.

**Measured so far:** parse+chunk timing on the largest filing (JPMorgan 2022 10-K, 16MB) ≈ 10 seconds, well inside the 10-minute budget, before embedding. 27 unit tests passing across parser/chunker/scorer. **The full 136-question benchmark has not yet been run end-to-end with live LLM calls** — this is the top-priority next step (see §11).

---

## 6. PRISM Differentiator Layer (added on top, async/non-blocking)

These run after a filing is queryable — they never gate the 10-minute SLA.

### 6.1 Footnote graph
NetworkX directed graph: `footnote_id -> {page_number, full_text, filing_id}`. Query-time resolver scans retrieved chunks for reference patterns (`(3)`, `Note 12`, `see Note 3`) and auto-appends resolved text before generation.

### 6.2 Intelligence Layer
Runs once per filing, in the background, stores findings in `intelligence.json`:

| Component | What it does |
|---|---|
| Red flag detector | 12 checks (AR growing faster than revenue, auditor changes, going-concern language, related-party spikes, goodwill ratio, OCF/net-income sign mismatch, revenue-recognition changes, segment changes, non-GAAP without comparison, risk-factor growth, inventory/COGS mismatch, DSO trend) |
| Contradiction detector | Cross-references a metric across every section it appears in (income statement, MD&A, notes, segment table); flags discrepancies >0.5% |
| Tone analyzer | Scores MD&A hedging vs. confident language, 0–100, tracked over multiple filings per company |
| Anomaly fingerprinter | Builds a per-company historical range for key metrics; flags new filings outside that range |
| Conviction score | Weighted aggregate (earnings quality 30%, management credibility 25%, disclosure transparency 25%, red-flag count 20%) — explicitly labeled a data-quality signal, not investment advice |

### 6.3 Learning loops
| Loop | Mechanism |
|---|---|
| Retrieval memory | `weight = base_similarity + 0.1×correct − 0.15×wrong`, applied to ChromaDB chunk metadata on feedback |
| Query pattern library | Stores question embedding + routing decision + outcome; new questions matched against it for routing/retrieval reuse |
| Failure self-repair | On rephrase-after-abstain, stores a synonym pair; future matching queries auto-expanded |

### 6.4 Structured fast-path cache
SQLite table of pre-extracted key metrics (revenue, EPS, margin, capex, etc.) per company/period, populated by one batched LLM call per filing (not per page). Simple lookups resolve in <100ms without invoking RAG.

---

## 7. Offline Resilience & Fallback (from the Version 1 build, kept as-is)

- **Embedding model**: `all-MiniLM-L6-v2` downloads once (~90MB) from Hugging Face, cached to disk; fully offline after that. Pre-warm before any offline demo/session.
- **Draft/Verify LLM calls**: `llm_client.py` is provider-agnostic (`LLM_PROVIDER = anthropic | openai | gemini | ollama`). `LLM_FALLBACK_PROVIDER` env var retries against a local Ollama daemon (`ollama serve` + `ollama pull llama3.1`, fully offline) if the primary fails, using Ollama's native `/api/chat` with `format: "json"`.
- **Total outage**: `answerer.py` does not 500. It returns the raw retrieved evidence, clearly labeled "LLM unavailable — showing raw retrieved evidence only, unverified," with `abstained=True`.
- **Caveat (unresolved)**: Ollama integration is written-to-spec and mock-tested, not yet exercised against a live local daemon. **One real end-to-end check is required before the demo.**

---

## 8. Evaluation Discipline

Two separate holdouts, not one — they test different things and both matter.

### 8.1 Question-level split (within the known corpus)
- **Split the 136 practice questions before any tuning.** Stratified ~75/25 (tune-set / held-out-set), stratified by company/sector and by question type (simple lookup / narrative reasoning / footnote-dependent).
- Tune confidence thresholds, RRF weights, abstain cutoffs **only against the tune-set**.
- Freeze thresholds, run once against held-out set, report **that** score in the approach note.
- Large tune/held-out divergence = signal thresholds are overfit; pull back toward conservative values.
- This tests generalization to **unseen questions on already-indexed filings** — it does not exercise the live parse-and-index path at all.

### 8.2 Filing-level holdout (a genuinely unseen document)
- Before ingesting anything for tuning, set aside **a small number of filings** (e.g. 5–8 of the 78) and **do not ingest them** until thresholds are frozen from §8.1.
- Their associated practice questions are excluded from both the tune-set and the question-level held-out set — they belong to this holdout entirely.
- Once thresholds are frozen, run these filings through the **actual Track B live-ingestion path** (parse → chunk → index → query), exactly as the "Add filing" demo would, and score their questions.
- This is the only part of self-evaluation that tests what the brief actually describes — a filing "it has never seen" — end to end, including parser edge cases that only show up on a document that was never part of tuning.
- If this score is meaningfully worse than the §8.1 held-out score, that's a signal the system (or the tuned thresholds) is quietly fit to quirks of the 78-filing corpus, not genuinely robust to new documents.

**Neither split has been run yet** — the full 136-question benchmark hasn't been executed at all (no LLM credentials in the build sandbox). This is now the top-priority next step, and both splits need to happen *before* the first full run, not after.

---

## 9. Frontend

React + Vite + Tailwind SPA (Version 1 build's structure, kept as the base; PRISM dashboard elements added).

| Component | Responsibility | Source |
|---|---|---|
| `Sidebar.jsx` | Filing-scope selector + drag-drop upload with visible processing status | Version 1 build |
| `QuestionForm.jsx` | Chat-style question input | Version 1 build |
| `AnswerCard.jsx` | Answer text, confidence bars, citation badge | Version 1 build |
| `CitationBadge.jsx` | Monospace "SEC EDGAR Page 49" badge | Version 1 build |
| `ConfidenceMeter.jsx` | Confidence / retrieval-agreement bars | Version 1 build |
| `EvidenceViewer.jsx` | Expandable per-excerpt grounding text | Version 1 build |
| `ScoreDashboard.jsx` *(new)* | Live +1/0/−1 running tally, tune-set vs. held-out shown separately | PRISM plan |
| `FilingIntelligenceBadge.jsx` *(new)* | Red-flag count, tone trend, conviction score on filing cards; "still enriching" state while Intelligence Layer runs async | PRISM plan |
| `FeedbackRow.jsx` *(new)* | 👍 / 👎 / 🔄 buttons feeding the learning loops | PRISM plan |

Design direction (Version 1 build's, kept): near-black background, single restrained blue accent for citations/active state, Inter for prose, JetBrains Mono for data (page numbers, doc names, evidence text) — visually distinguishes citation from prose, matching the backend's structural distinction.

Streaming: Draft generation responses stream via Server-Sent Events (FastAPI `StreamingResponse` → React `fetch`/`ReadableStream`).

---

## 10. API Contract (FastAPI)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Service metadata, indexed-filing count |
| GET | `/health` | `{status: ready\|empty, indexed_count}` |
| GET | `/filings` | List of indexed `doc_names` for the scope selector |
| POST | `/upload` | `multipart/form-data`, one or more `.htm`/`.html` files → parses, chunks, indexes immediately (Track B path) |
| POST | `/answer` | `{question, doc_name, top_k}` → `AnswerResponse` |
| GET | `/filings/{id}/intelligence` *(new)* | Red flags, tone trend, conviction score, enrichment status |
| POST | `/feedback` *(new)* | `{answer_id, verdict: correct\|wrong\|clarify, note?}` → feeds learning loops |

---

## 11. Tech Stack by Section

### Ingestion
| Component | Technology |
|---|---|
| HTML parsing | BeautifulSoup4 (Python), with defensive attrs handling + snapshot-before-iterate pattern |
| Table serialization | Custom BS4 traversal, colspan-interval expansion, spacer-cell skip |
| Footnote graph | NetworkX, serialized per filing |
| Chunking | Custom (400w prose / 50w overlap; table/footnote atomic) |
| Dense embeddings | sentence-transformers `all-MiniLM-L6-v2` (local, cached after first download) |
| Vector store | ChromaDB (local persistent, cosine distance) |
| Sparse index | `rank_bm25` (BM25Okapi), pickled per filing |
| Structured cache | SQLite |
| Vision fallback (narrow: chart images only) | Provider's vision endpoint via `llm_client.py` |
| Intelligence Layer LLM calls | Async, via `llm_client.py`, cost-optimized model tier where available |

### Query pipeline
| Component | Technology |
|---|---|
| Retrieval fusion | ChromaDB + rank_bm25, RRF (k=60), 1.5× dual-agreement boost |
| Footnote resolution | NetworkX graph traversal |
| LLM orchestration | `llm_client.py` — provider-agnostic (Anthropic \| OpenAI \| Gemini \| Ollama fallback) |
| API layer | FastAPI, `StreamingResponse` for SSE |

### Learning loops
| Component | Technology |
|---|---|
| Retrieval memory | ChromaDB metadata updates |
| Query pattern library | ChromaDB (separate collection) or SQLite + embedding lookup |
| Failure self-repair | SQLite table of synonym pairs |

### Evaluation
| Component | Technology |
|---|---|
| Scoring engine | Python (`eval/scorer.py`), ±12-page tolerance, absolute-value fallback for signed-number mismatches |
| Filing-level holdout | Python (`eval/reserve_filings.py` — new), excludes chosen filings from bulk ingest until frozen |
| Split management | Python (`eval/split.py` — new), stratified, seeded, question-level only |
| Threshold tuning | Grid search over tune-set only (`eval/tune_thresholds.py`) |
| Harness | `eval/harness.py` / `eval/run_eval.py`, reports tune-set and held-out scores separately |

### Frontend
| Component | Technology |
|---|---|
| Framework | React (Vite) |
| Styling | Tailwind CSS |
| Streaming | SSE via `fetch`/`ReadableStream` |
| Charts | Recharts or Chart.js |
| Data fetching/polling | TanStack Query |

### Infrastructure
| Component | Technology |
|---|---|
| Containerization | Docker + `docker-compose.yml` (backend + frontend services, bind-mounted `./filings` and `./data`) |
| Local LLM fallback | Ollama daemon (`localhost:11434`) |

---

## 12. Setup

### Docker (recommended for the live demo)
```
cp .env.example .env   # fill in ANTHROPIC_API_KEY (or OPENAI/GEMINI)
docker compose up --build
# Backend:  http://localhost:8000
# Frontend: http://localhost:5173
```
Filings and search index persist on the host via bind mounts (`./filings`, `./data`).

**Not yet build-tested** (no Docker daemon in the build sandbox) — run once, ahead of time, on the machine that will run the live session.

### Manual setup
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in LLM API key
# copy filings (.htm) into ./filings, then:
python -m backend.ingest --all
python -m backend.main        # http://127.0.0.1:8000
```
```
cd frontend
npm install
npm run dev                   # http://localhost:5173
```
### Tests and evaluation
```
python -m pytest tests/ -v                    # 27 passing
python -m eval.reserve_filings                # NEW — pick 5-8 filings, exclude from ingestion (§8.2)
python -m eval.split                           # NEW — stratified tune/held-out split of remaining questions (§8.1)
python -m eval.tune_thresholds                 # tune-set only — never touches either held-out set
python -m eval.run_eval --held-out             # NEW — frozen thresholds, question-level held-out score
python -m eval.run_eval --filing-holdout       # NEW — ingest reserved filings via live Track B path, score their questions
```

---

## 13. Project File Structure (combined)

```
analyst-copilot/
├── backend/
│   ├── config.py              # paths, model names, thresholds (env-driven)
│   ├── models.py               # Pydantic schemas + enums
│   ├── parser.py                # HTML parsing, table serialization (colspan-aware),
│   │                             # page recovery, defensive attrs handling
│   ├── chunker.py                # atomic chunking (400w / 50w overlap)
│   ├── indexer.py                 # ChromaDB + BM25 index management
│   ├── retriever.py                # GAAP expansion (BM25-only) + RRF fusion
│   ├── llm_client.py                # provider-agnostic Draft/Verify + Ollama fallback
│   ├── answerer.py                   # two-pass QA orchestration + abstention gate
│   ├── footnote_graph.py             # NEW — NetworkX footnote graph builder + resolver
│   ├── intelligence/                 # NEW — PRISM Intelligence Layer
│   │   ├── red_flags.py              #   12 checks
│   │   ├── contradiction.py          #   cross-section metric comparison
│   │   ├── tone.py                   #   MD&A hedging/confidence scoring
│   │   ├── anomaly.py                #   per-company historical fingerprint
│   │   └── conviction.py             #   weighted aggregate score
│   ├── learning/                     # NEW — PRISM learning loops
│   │   ├── retrieval_memory.py       #   chunk weight updates from feedback
│   │   ├── query_patterns.py         #   pattern library store/match/route
│   │   └── self_repair.py            #   synonym pair builder from rephrase events
│   ├── cache.py                       # NEW — structured fast-path cache (SQLite)
│   ├── ingest.py                       # CLI bulk ingestion
│   ├── main.py                          # FastAPI app
│   └── Dockerfile
├── eval/
│   ├── scorer.py                # FinanceBench-style rubric scoring
│   ├── reserve_filings.py        # NEW — filing-level holdout (§8.2)
│   ├── split.py                    # NEW — question-level tune/held-out split (§8.1)
│   ├── tune_thresholds.py         # grid search, tune-set only
│   ├── harness.py                  # batch evaluation runner
│   └── run_eval.py                  # CLI
├── tests/                        # unit tests (parser, chunker, scorer)
├── frontend/                     # React + Vite + Tailwind SPA
│   ├── src/
│   │   ├── components/
│   │   │   ├── Sidebar.jsx
│   │   │   ├── QuestionForm.jsx
│   │   │   ├── AnswerCard.jsx
│   │   │   ├── CitationBadge.jsx
│   │   │   ├── ConfidenceMeter.jsx
│   │   │   ├── EvidenceViewer.jsx
│   │   │   ├── ScoreDashboard.jsx        # NEW
│   │   │   ├── FilingIntelligenceBadge.jsx  # NEW
│   │   │   └── FeedbackRow.jsx            # NEW
│   │   └── ...
│   ├── package.json
│   ├── vite.config.js
│   └── Dockerfile
├── filings/                      # .htm corpus (known 78 + live-added)
├── data/                          # generated: chroma/, bm25/, eval_logs/, footnote_graphs/, intelligence/
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── README.md
├── ARCHITECTURE.md
├── APPROACH_NOTE.md / .pdf
└── DEMO_SCRIPT.md
```

---

## 14. Known Limitations & Immediate Next Steps

Priority order:

1. **Run the eval-discipline pipeline — split first, never all 136 at once.** Order matters: (a) reserve 5–8 filings entirely uningested for the filing-level holdout (§8.2), (b) split the remaining questions into tune-set / held-out-set via `eval/split.py` (§8.1), (c) run `eval/tune_thresholds.py` against the tune-set only, (d) freeze thresholds and run once against the question-level held-out set, (e) ingest the reserved filings through the live Track B path and score their questions separately. No credentials were available in the build sandbox — this needs real API access before anything else here can be trusted.
2. **Verify the Version 1 build's three parser fixes directly against the code** (not yet done — only the doc description has been reviewed).
3. **One live Docker build** on the actual demo machine — currently YAML-validated only.
4. **One live Ollama fallback check** — currently mock-tested only.
5. **Decide the primary LLM provider for the demo** — `llm_client.py` supports four, but one needs to be the confirmed default with a working key, tested ahead of time.
6. **Scan all filings for `<img>` count** to confirm the narrowed vision-fallback scope (chart images only) is actually rare, as assumed.
7. **Build out the Intelligence Layer, footnote graph, and learning loops** — currently spec'd in this doc, not yet implemented in either codebase.
8. **Page-estimation drift** (1–3 pages observed on real filings) — currently absorbed by ±12-page tolerance; worth tightening if a stricter tolerance is ever used.

---

*Next step: implement §6 (PRISM differentiator layer) on top of the Version 1 build's validated core, then run the eval discipline in §8 before finalizing the approach note.*
