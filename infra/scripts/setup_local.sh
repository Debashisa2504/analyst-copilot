#!/usr/bin/env bash
# =============================================================================
# setup_local.sh — Local development environment setup
# =============================================================================
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

echo "======================================================================"
echo " Analyst Copilot — Local Dev Setup"
echo "======================================================================"

# Python virtualenv
echo "[1/4] Setting up Python virtualenv…"
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "    ✓ Python deps installed"

# .env
echo "[2/4] Creating .env from .env.example…"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    ⚠ Edit .env and add your LLM API key before starting."
else
  echo "    .env already exists — skipping"
fi

# Frontend
echo "[3/4] Installing frontend dependencies…"
cd frontend && npm install --silent && cd ..
echo "    ✓ Frontend deps installed"

# Directories
echo "[4/4] Creating data directories…"
mkdir -p filings data/chroma data/bm25 data/eval_logs data/footnote_graphs data/intelligence
echo "    ✓ Directories ready"

echo ""
echo "======================================================================"
echo " Setup complete! To start the dev servers:"
echo ""
echo "   Terminal 1 (backend):"
echo "     source .venv/bin/activate"
echo "     python -m backend.main"
echo ""
echo "   Terminal 2 (frontend):"
echo "     cd frontend && npm run dev"
echo ""
echo "   Or with Docker:"
echo "     cp .env.example .env  # fill in API key"
echo "     docker compose up --build"
echo ""
echo "   Then ingest a filing:"
echo "     python -m backend.ingest --file <your_filing.htm>"
echo ""
echo "   Run tests:"
echo "     python -m pytest tests/ -v"
echo "======================================================================"
