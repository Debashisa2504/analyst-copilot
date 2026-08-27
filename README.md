# The Analyst Copilot

Grounded question-answering over SEC 10-K/10-Q/8-K filings, powered by the PRISM architecture.

Given a filing and a question, returns a precise answer with an exact page citation, or an honest "not found in this filing." Never confidently wrong.

---

## Architecture at a glance

```
SEC HTML Filing
  → parser.py          # colspan-aware table serialization, page recovery, footnote linking
  → chunker.py         # 400-word prose windows (50-word overlap); table/footnote chunks atomic
  → indexer.py         # ChromaDB dense (all-MiniLM-L6-v2) + BM25 sparse, pickled per filing
  → [queryable — 10-min gate ends here]
  → footnote_graph.py  # NetworkX footnote graph (async, non-blocking)
  → intelligence/      # Red flags, tone, conviction score (async, non-blocking)

Question → retriever.py
  → GAAP synonym expansion (BM25 side only — dense query stays semantically clean)
  → Hybrid RRF (k=60) + 1.5× dual-agreement multiplier
  → answerer.py
      Pass 1 (Draft):  candidate answer + value + page + confidence
      Pass 2 (Verify): independent audit — value present? period matches? page correct?
      Gate: verified AND min(draft.conf, verify.conf) >= 0.75 → answer
            else → "not found in this filing"
```

**Scoring rubric** (FinanceBench):

| Output | Score |
|---|---|
| Correct answer + correct page | +1 |
| Honest abstention | 0 |
| Correct answer + wrong page | 0 |
| Confidently wrong | −1 |

---

## Prerequisites

- Python 3.11+
- Node.js 20+
- Docker (for Docker Compose / Azure deployment)
- An LLM API key (Anthropic, OpenAI, or Google Gemini)
- Azure CLI + subscription (for Azure deployment only)

---

## Local setup (Docker — recommended)

```bash
git clone <repo>
cd analyst-copilot

cp .env.example .env
# Edit .env: set LLM_PROVIDER and the matching API key

docker compose up --build
```

- Backend API: http://localhost:8000
- Frontend UI: http://localhost:5173
- Health:      http://localhost:8000/health

### Local setup (manual)

```bash
# Run the setup script
./infra/scripts/setup_local.sh

# OR manually:
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in API key

# Terminal 1 — backend
python -m backend.main

# Terminal 2 — frontend
cd frontend && npm run dev
```

---

## Ingesting filings

```bash
# Copy .htm files from SEC EDGAR into ./filings/, then:
python -m backend.ingest --all

# Or ingest a single file:
python -m backend.ingest --file 3M_2018_10K.htm

# Or upload via the UI (drag-and-drop in the sidebar)
# Or via the API:
curl -X POST http://localhost:8000/upload \
  -F "files=@3M_2018_10K.htm"
```

Ingest time: ~10 seconds for a 16MB filing (parse + chunk + embed + index).
The Intelligence Layer (red flags, tone, conviction) runs in the background.

---

## Asking questions

**Via UI:** Open http://localhost:5173, select a scope, type a question.

**Via API:**
```bash
curl -X POST http://localhost:8000/answer \
  -H "Content-Type: application/json" \
  -d '{"question":"What was capital expenditure in FY2018?","doc_name":"3M_2018_10K","top_k":10}'
```

**Response shape:**
```json
{
  "answer": "$1,577 million",
  "doc_name": "3M_2018_10K",
  "page_num": 59,
  "abstained": false,
  "confidence": 0.91,
  "retrieval_agreement": 0.7,
  "evidence": [
    {
      "text": "Purchases of property, plant and equipment (PP&E), 2018: -1577 (in millions)",
      "page_num": 59,
      "doc_name": "3M_2018_10K",
      "chunk_type": "table_row"
    }
  ]
}
```

---

## Running tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

Expected: 56+ tests passing in < 1 second.

---

## API reference

| Method | Path | Description |
|---|---|---|
| GET | `/` | Service metadata, indexed-filing count |
| GET | `/health` | `{status: ready\|empty, indexed_count}` |
| GET | `/filings` | List of indexed filing names |
| POST | `/upload` | Upload `.htm`/`.html` files; immediately indexes them |
| POST | `/answer` | `{question, doc_name, top_k}` → `AnswerResponse` |
| GET | `/filings/{id}/intelligence` | Red flags, tone score, conviction (async; may be `pending`) |
| POST | `/feedback` | `{chunk_ids, verdict: correct\|wrong\|clarify}` → learning loop |

---

## Azure deployment

### One-command deploy

```bash
# Make sure az CLI is logged in
az login

./infra/scripts/deploy.sh \
  --env prod \
  --prefix acopilot \
  --location eastus \
  --provider anthropic \
  --api-key "sk-ant-..."
```

This deploys:
- Resource group, App Service Plan (B2 Linux), Web App (Python Docker)
- Azure Container Registry (ACR)
- Azure Static Web Apps (React frontend)
- Azure File Share (persistent ChromaDB + BM25 storage)
- Azure Key Vault (API keys, not in env vars)

### Step-by-step (if you prefer control)

See [DEPLOYMENT.md](docs/DEPLOYMENT.md) for the detailed walkthrough.

### CI/CD (GitHub Actions)

Push to `main` → tests → build Docker images → push to ACR → deploy to Azure.

Required GitHub Secrets (set in repo Settings → Secrets):
```
AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID
ACR_LOGIN_SERVER, ACR_USERNAME, ACR_PASSWORD
AZURE_BACKEND_APP, AZURE_RG
VITE_API_BASE        (e.g. https://acopilot-prod-api.azurewebsites.net)
SWA_DEPLOYMENT_TOKEN
```

---

## Project structure

```
analyst-copilot/
├── backend/
│   ├── config.py          # All constants and env-driven settings
│   ├── models.py          # Pydantic schemas
│   ├── parser.py          # HTML parsing, table serialization (colspan-aware)
│   ├── chunker.py         # Atomic chunking
│   ├── indexer.py         # ChromaDB + BM25
│   ├── retriever.py       # GAAP expansion + RRF fusion
│   ├── llm_client.py      # Provider-agnostic LLM (Anthropic/OpenAI/Gemini/Ollama)
│   ├── answerer.py        # Two-pass QA + abstention gate
│   ├── footnote_graph.py  # NetworkX footnote graph
│   ├── cache.py           # SQLite structured metrics cache
│   ├── intelligence/      # Red flags, tone, conviction
│   ├── learning/          # Chunk weights, query patterns, synonym pairs
│   ├── ingest.py          # CLI bulk ingestion
│   ├── main.py            # FastAPI app
│   └── Dockerfile
├── eval/
│   ├── scorer.py          # FinanceBench rubric scoring (±12-page tolerance)
│   ├── harness.py         # Batch evaluation runner
│   └── run_eval.py        # CLI
├── tests/                 # 56 unit tests
├── frontend/              # React + Vite + Tailwind SPA
│   └── src/components/
│       ├── AnswerCard.jsx
│       ├── CitationBadge.jsx
│       ├── ConfidenceMeter.jsx
│       ├── EvidenceViewer.jsx
│       ├── FeedbackRow.jsx
│       ├── FilingIntelligenceBadge.jsx
│       ├── QuestionForm.jsx
│       ├── ScoreDashboard.jsx
│       └── Sidebar.jsx
├── infra/
│   ├── azure/             # Bicep templates
│   │   ├── main.bicep
│   │   └── modules/
│   └── scripts/
│       ├── deploy.sh      # One-command Azure deployment
│       ├── setup_local.sh # Local dev setup
│       └── teardown.sh    # Remove Azure resources
├── .github/workflows/
│   └── deploy.yml         # CI/CD
├── filings/               # .htm corpus (git-ignored)
├── data/                  # Generated indexes (git-ignored)
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## LLM provider configuration

The system is provider-agnostic. Set `LLM_PROVIDER` in `.env`:

| Provider | Env var needed | Models (defaults) |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` |
| `gemini` | `GEMINI_API_KEY` | `gemini-1.5-flash` |
| `ollama` | (none — local) | `llama3.1` |

Set `LLM_FALLBACK_PROVIDER=ollama` for an offline fallback that never hard-fails.

---

## Known limitations

1. **Full 136-question benchmark not yet run** — requires live LLM credentials. Run `python -m eval.run_eval` after providing an API key.
2. **Page-estimation drift** ±1–3 pages on real filings, absorbed by the ±12-page scoring tolerance.
3. **Intelligence Layer metrics** require a real extraction pass — red flags return empty without a structured metrics dict populated by the cache.
4. **Ollama fallback** is code-complete but not live-tested — do one end-to-end check before relying on it in production.
