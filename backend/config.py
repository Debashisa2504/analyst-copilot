"""
backend/config.py
------------------
Central configuration: paths, model choices, and tuned thresholds.
Every other module imports from here instead of hard-coding constants,
so the whole system's behavior can be tuned from one file / one .env.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILINGS_DIR = PROJECT_ROOT / "filings"
DATA_DIR = PROJECT_ROOT / "data"
CHROMA_DIR = DATA_DIR / "chroma"
EVAL_LOG_DIR = DATA_DIR / "eval_logs"

# --------------------------------------------------------------------------
# Corpus selection — lets an experimental re-chunking (e.g. Plan A's
# section-aware grouping) be ingested into its own PostgreSQL table + BM25
# directory, completely isolated from the production "chunks" table / data/bm25
# so the two can be A/B compared (or the experiment rolled back) without ever
# touching the original index. Leave both unset to use the production corpus.
# Example (PowerShell):
#   $env:CHUNKS_TABLE = "chunks_plan_a"; $env:BM25_DIR_NAME = "bm25_plan_a"
#   python -m backend.ingest --all
CHUNKS_TABLE = os.getenv("CHUNKS_TABLE", "chunks_section_chunked")
BM25_DIR = DATA_DIR / os.getenv("BM25_DIR_NAME", "bm25")

for d in (FILINGS_DIR, DATA_DIR, CHROMA_DIR, BM25_DIR, EVAL_LOG_DIR):
    d.mkdir(parents=True, exist_ok=True)

if CHUNKS_TABLE != "chunks_section_chunked":
    print(f"[config] Using NON-default chunks table: '{CHUNKS_TABLE}' (BM25 dir: {BM25_DIR})")

# --------------------------------------------------------------------------
# LLM provider configuration
# --------------------------------------------------------------------------
# The draft/verify pass is provider-agnostic: set LLM_PROVIDER to one of
# "anthropic" | "openai" | "gemini" and the matching *_API_KEY. This lets the
# same two-pass architecture run on whatever model the deployment has
# credentials for, while defaulting to the names used in the design docs.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure_openai")
# Optional: a second provider tried automatically if the primary one fails
# (network down, API outage, missing key). Typically "ollama" so the system
# degrades to a fully local/offline model instead of erroring out entirely.
LLM_FALLBACK_PROVIDER = os.getenv("LLM_FALLBACK_PROVIDER", "")

# Azure OpenAI (Verify pass — stronger model used as accuracy gate)
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
# API version — check your Azure OpenAI resource → Deployments for supported versions.
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

# Gemini (Draft pass — free tier, fast extraction)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Free-tier throttling: conservative default — raise GEMINI_RPM_LIMIT in .env
# to match your actual quota shown in AI Studio (avoids 429s proactively).
GEMINI_RPM_LIMIT = int(os.getenv("GEMINI_RPM_LIMIT", "8"))
GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "5"))

# Ollama: local inference daemon (https://ollama.com), no API key needed.
# Run `ollama serve` + `ollama pull <model>` first. Fully offline once the
# model is pulled.
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

# Per-pass provider routing: Draft uses Gemini (free, fast extraction);
# Verify uses Azure OpenAI GPT-4o (the accuracy gate — stronger model here
# prevents -1 scores). Each can be overridden independently via .env.
DRAFT_PROVIDER = os.getenv("DRAFT_PROVIDER", "gemini")
VERIFY_PROVIDER = os.getenv("VERIFY_PROVIDER", "azure_openai")

# DRAFT_MODEL: Gemini model name (e.g. gemini-2.0-flash, gemini-1.5-flash)
# VERIFY_MODEL: Azure deployment name (the name you gave it in Azure AI Studio)
DRAFT_MODEL = os.getenv("DRAFT_MODEL", "gemini-1.5-flash")
VERIFY_MODEL = os.getenv("VERIFY_MODEL", "gpt-4o")
# Model name to use when a call falls back to LLM_FALLBACK_PROVIDER.
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", OLLAMA_MODEL)

# Embedding provider: "azure_openai" (API-based, better quality) or "local" (CPU, no API key)
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "azure_openai")
# Azure deployment name for the embedding model (set in Azure AI Studio)
AZURE_EMBEDDING_DEPLOYMENT = os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-small")
# Fallback local model (used only when EMBEDDING_PROVIDER=local)
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# --------------------------------------------------------------------------
# Embedding rate-limit pacing (Azure OpenAI only; the "local" provider is
# unaffected -- it never calls a rate-limited API)
# --------------------------------------------------------------------------
# Set AZURE_EMBED_TPM_LIMIT to match your ACTUAL deployment's quota
# (Azure AI Foundry -> Deployments -> your embedding deployment ->
# "Rate limit (Tokens per minute)"). Batches are sized by an estimated
# token budget rather than a fixed chunk count, since real chunk sizes
# vary widely post-Plan-A (a short footnote vs. an 800-word table block).
AZURE_EMBED_TPM_LIMIT = int(os.getenv("AZURE_EMBED_TPM_LIMIT", 140_000))
# Fraction of the TPM quota to actually target, leaving headroom for the
# char-per-token estimate being approximate and for quota-window jitter.
AZURE_EMBED_TPM_UTILIZATION = float(os.getenv("AZURE_EMBED_TPM_UTILIZATION", 0.7))
# Target tokens per single embeddings.create() call.
AZURE_EMBED_BATCH_TOKEN_BUDGET = int(os.getenv("AZURE_EMBED_BATCH_TOKEN_BUDGET", 40_000))
# Rough chars-per-token for English text (OpenAI's own rule of thumb is ~4);
# biased slightly lower (more tokens per char) since financial text is dense
# with numbers/punctuation, which tends to tokenize less efficiently than prose.
AZURE_EMBED_CHARS_PER_TOKEN = float(os.getenv("AZURE_EMBED_CHARS_PER_TOKEN", 3.5))

# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------
CHUNK_SIZE_WORDS = int(os.getenv("CHUNK_SIZE_WORDS", 400))
CHUNK_OVERLAP_WORDS = int(os.getenv("CHUNK_OVERLAP_WORDS", 50))
# Section-aware table grouping (Plan A): same-section TABLE_ROW segments are
# accumulated into one chunk until this word budget is hit.
TABLE_CHUNK_SIZE_WORDS = int(os.getenv("TABLE_CHUNK_SIZE_WORDS", 800))

# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", 10))
CONTEXT_TOP_K = int(os.getenv("CONTEXT_TOP_K", 8))          # excerpts sent to the LLM
RRF_K = int(os.getenv("RRF_K", 60))                          # RRF damping constant
DUAL_AGREEMENT_MULTIPLIER = float(os.getenv("DUAL_AGREEMENT_MULTIPLIER", 1.5))
BM25_CALC_BIAS = float(os.getenv("BM25_CALC_BIAS", 2.0))     # BM25 weight boost for calculation queries

# --------------------------------------------------------------------------
# Precision gate
# --------------------------------------------------------------------------
ABSTAIN_THRESHOLD = float(os.getenv("ABSTAIN_THRESHOLD", 0.55))

# --------------------------------------------------------------------------
# Page recovery
# --------------------------------------------------------------------------
MIN_PAGE_BREAK_MARKERS = int(os.getenv("MIN_PAGE_BREAK_MARKERS", 5))
WORDS_PER_PAGE_FALLBACK = int(os.getenv("WORDS_PER_PAGE_FALLBACK", 550))
PAGE_LOCATION_TOLERANCE = int(os.getenv("PAGE_LOCATION_TOLERANCE", 12))

# --------------------------------------------------------------------------
# API server
# --------------------------------------------------------------------------
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", 8000))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

# --------------------------------------------------------------------------
# PostgreSQL + pgvector (dense vector store)
# --------------------------------------------------------------------------
# Azure PostgreSQL Flexible Server connection string.
# Format: postgresql://user:password@host.postgres.database.azure.com/dbname?sslmode=require
DATABASE_URL = os.getenv("DATABASE_URL", "")

# --------------------------------------------------------------------------
# ChromaDB (legacy — only needed if running scripts/migrate_chroma_to_pg.py)
# --------------------------------------------------------------------------
CHROMA_COLLECTION_NAME = "analyst_copilot_chunks"
CHROMA_HOST = os.getenv("CHROMA_HOST", "")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8000))
