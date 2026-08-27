# Architecture

## System overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        React SPA (Vite + Tailwind)              │
│  Sidebar · QuestionForm · AnswerCard (SSE streaming)            │
│  ScoreDashboard · FilingIntelligenceBadge · FeedbackRow         │
└───────────────────────────┬─────────────────────────────────────┘
                            │ JSON / SSE
┌───────────────────────────▼─────────────────────────────────────┐
│                     FastAPI (backend/main.py)                   │
│  POST /upload   POST /answer   POST /answer/stream (SSE)        │
│  GET /filings/{id}/intelligence   POST /feedback                │
└──┬──────────────┬──────────────┬──────────────────┬────────────┘
   │              │              │                  │
   ▼              ▼              ▼                  ▼
parser.py    retriever.py   answerer.py        intelligence/
chunker.py   ├─ GAAP exp.   ├─ call_draft()   ├─ red_flags
indexer.py   ├─ classify    ├─ call_verify()  ├─ contradiction
             ├─ cache path  ├─ footnote enr.  ├─ tone
             ├─ hybrid RRF  └─ precision gate ├─ anomaly
             └─ mem weights                   └─ conviction
                  │
             learning/
             ├─ retrieval_memory.py  (chunk weights)
             ├─ query_patterns.py    (classifier + pattern store)
             └─ self_repair.py       (rephrase synonym expansion)
```

## Ingestion pipelines

### Track A — Known corpus (offline, no timer)
```
.htm files → parser.py → chunker.py → indexer.py
                                          ↓ (async, non-blocking)
                                   footnote_graph.py
                                   intelligence/ (all 5 checks)
                                   cache.py (structured metrics)
```

### Track B — Live upload (10-minute SLA)
Same as Track A but timed. The gate closes after `index_chunks()`.
Footnote graph and intelligence layer run in a daemon thread — they
never block the upload response.

## Retrieval pipeline (per query)

```
question
  → learning/query_patterns.py classify_query()
       'simple'       → cache.py lookup_metric() [<100ms, no RAG]
       'cross_company' → per-filing RAG, results merged
       'temporal'/'complex' → hybrid RAG
  → retriever.expand_query()  [GAAP synonyms + self-repair terms]
  → indexer.query_dense()     [ChromaDB, all-MiniLM-L6-v2, cosine]
  → indexer.query_bm25()      [BM25Okapi, expanded query]
  → RRF fusion (k=60) + 1.5× dual-agreement multiplier
  → learning/retrieval_memory.apply_memory_weights()
  → top 8 chunks
  → footnote_graph.enrich_context_with_footnotes()
  → answerer.call_draft()    [Pass 1: LLM extracts answer + page]
  → answerer.call_verify()   [Pass 2: LLM audits the draft]
  → precision gate: verified AND min(conf) ≥ 0.75
       → answer + citation
       → "not found in this filing"
```

## Key design decisions

| Decision | Choice | Reason |
|---|---|---|
| Table serialization | colspan-interval expansion + spacer-cell skip | 1:1 positional mapping fails on real SEC tables (3M 2018 bug) |
| Query expansion | BM25-only | Dense embedding stays semantically clean; BM25 gains exact GAAP line-item recall |
| Two-pass QA | Draft → Verify, independent LLM calls | Catches unsupported values before they reach the user |
| Abstention threshold | θ = 0.75 | Wrong answer = −1, abstention = 0; guessing is strictly dominated |
| Page citation | CSS markers + anchor-offset correction | Electronic vs. printed page offset (±12-page tolerance at scoring) |
| Offline resilience | Ollama local fallback | Live demo never hard-fails, only degrades visibly |

## Data layout

```
data/
├── chroma/                # ChromaDB persistent vector store
├── bm25/                  # Per-filing BM25 pickles (*.pkl)
├── footnote_graphs/       # Per-filing footnote JSON (*.json)
├── intelligence/          # Per-filing intelligence JSON (*.json)
├── eval_logs/
│   ├── reserved_filings.json     # Filing-level holdout list
│   ├── tune_questions.jsonl      # ~75% of questions for tuning
│   ├── heldout_questions.jsonl   # ~25% frozen held-out set
│   ├── tuning_results.json       # Grid search results
│   └── best_thresholds.json      # Frozen tuned thresholds
├── metrics_cache.db       # SQLite: structured metric fast-path + synonyms
├── anomaly_fingerprints.db # SQLite: per-company historical ranges
└── learning.db            # SQLite: chunk weights + query patterns + rephrase pairs
```
