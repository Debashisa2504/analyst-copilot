FROM python:3.11-slim

WORKDIR /app

# System deps for lxml / sentence-transformers / chromadb
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer-cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model so container startup is fast
# (no internet required at runtime after this)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY backend/ ./backend/
COPY eval/ ./eval/

# Create directories (Azure File Share will override data/ at runtime)
RUN mkdir -p filings data/chroma data/bm25 data/eval_logs data/footnote_graphs data/intelligence

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=10s --start-period=40s --retries=5 \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "backend.main"]
