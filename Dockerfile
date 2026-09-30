# ── Build stage ───────────────────────────────────────────────────
FROM python:3.11-slim AS base

# System deps for PyMuPDF, lxml, chromadb
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libssl-dev libxml2-dev libxslt-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/   backend/
COPY scraper/   scraper/
COPY data/      data/
COPY rebuild_qco.py .

# ── Runtime ───────────────────────────────────────────────────────
FROM base AS runtime

WORKDIR /app

# Create data directory for ChromaDB and SQLite
RUN mkdir -p data/chroma_db data/pdfs

# Environment defaults (override at runtime via -e or docker-compose)
ENV EMBED_PROVIDER=gemini \
    ALLOW_ORIGIN=http://localhost:3000 \
    EMBED_BATCH_SLEEP=10 \
    PORT=8000

# Expose the port
EXPOSE 8000

# Health check — waits up to 30s for the server to respond
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Start the FastAPI server
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --workers 1
