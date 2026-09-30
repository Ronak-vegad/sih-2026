"""
Central configuration for the BIS RAG backend.
All tuneable knobs live here.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (one level up from backend/)
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Paths ────────────────────────────────────────────────────────
ROOT_DIR       = Path(__file__).parent.parent
DATA_DIR       = ROOT_DIR / "data"
QCO_DB_PATH    = DATA_DIR / "qco_structured.db"
RAW_JSON_PATH  = DATA_DIR / "raw_documents.json"
CHROMA_DIR     = DATA_DIR / "chroma_db"

# ── Embedding provider ───────────────────────────────────────────
# Selected by env var so development can stay offline while production gets
# real dense retrieval. See backend/embeddings.py.
#
#   gemini  (default) hosted, free tier, native RETRIEVAL_DOCUMENT /
#                     RETRIEVAL_QUERY task types, 100+ languages
#   openai            hosted, symmetric, cheap and very reliable
#   ollama            local only; requires `ollama serve`
#
# WARNING: 'ollama' must not be used in a deployment that has no Ollama
# process reachable. It previously was, on Render, and the result was silent
# keyword-only retrieval reporting 0.85 confidence.
EMBED_PROVIDER = os.getenv("EMBED_PROVIDER", "gemini").strip().lower()

# Gemini — free key at https://aistudio.google.com/app/apikey
GOOGLE_API_KEY     = os.getenv("GOOGLE_API_KEY", "")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")

# OpenAI — alternative hosted provider
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY", "")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

# Ollama — local development only
OLLAMA_BASE_URL    = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# Legacy aliases — kept so existing imports don't break during migration
NVIDIA_API_KEY  = ""           # not used
NIM_EMBED_MODEL = OLLAMA_EMBED_MODEL

# ── Groq (chat / LLM) ───────────────────────────────────────────
# Groq provides ultra-fast inference via an OpenAI-compatible API.
# Get a free key at https://console.groq.com
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL    = "https://api.groq.com/openai/v1"
GROQ_CHAT_MODEL  = "openai/gpt-oss-20b"

# ── Embedding geometry ───────────────────────────────────────────
# gemini-embedding-001 emits 3072 dims natively and L2-normalised. Narrower
# widths are Matryoshka truncations which the API does NOT re-normalise (a
# 768-dim vector comes back with norm ~0.59), so embeddings.l2_normalise is
# applied client-side whenever EMBED_DIM != 3072.
#
# Defaults per provider if you change EMBED_PROVIDER:
#   gemini  3072 (native) or 768/1536 via truncation
#   openai  1536 (text-embedding-3-small)
#   ollama  768  (nomic-embed-text)
#
# Changing this value invalidates the index: re-run scripts/reindex.py.
_DEFAULT_EMBED_DIM = {"gemini": 3072, "openai": 1536, "ollama": 768}
EMBED_DIM        = int(
    os.getenv("EMBED_DIM", _DEFAULT_EMBED_DIM.get(EMBED_PROVIDER, 768))
)
EMBED_MAX_TOKENS = 2048        # gemini-embedding-001 input limit
# Gemini free tier: ~1500 texts/min (100 RPM). Keep batches small and add
# EMBED_BATCH_SLEEP seconds between calls during ingestion to stay inside limits.
EMBED_BATCH_SIZE  = 20         # texts per embeddings API call
EMBED_BATCH_SLEEP = float(os.getenv("EMBED_BATCH_SLEEP", "65"))  # seconds between batches

# ── LLM ─────────────────────────────────────────────────────────
CHAT_TEMPERATURE = 0.1         # low temp for factual accuracy
CHAT_MAX_TOKENS  = 2048
# The Groq client previously had no timeout and no retry budget, so a slow or
# hung upstream held the request open indefinitely with nothing to break the
# stall. Both are now explicit.
CHAT_TIMEOUT     = float(os.getenv("CHAT_TIMEOUT", "45"))
CHAT_MAX_RETRIES = int(os.getenv("CHAT_MAX_RETRIES", "2"))
# Thinking toggle — some models emit <think>...</think> traces without this.
THINKING_TOGGLE  = ""

# ── Retrieval ────────────────────────────────────────────────────
# 500 tokens (~2000 chars) sits far inside the embedding model's 4096-token
# input ceiling, so no chunk loses its tail when vectorised.
CHUNK_SIZE          = 500      # tokens (approx chars / 4)
CHUNK_OVERLAP       = 50
TOP_K_RETRIEVAL     = 12       # candidates from hybrid fusion
TOP_K_FINAL         = 5        # chunks sent to LLM
SIM_THRESHOLD       = 0.30     # below this → "not enough info" fallback
BM25_WEIGHT         = 0.4      # weight for BM25 in RRF fusion
DENSE_WEIGHT        = 0.6      # weight for dense vector in RRF

# ── Anti-hallucination ───────────────────────────────────────────
# Matches: "IS 302", "IS 1417:2016", "IS 302 (Part 1) : 2012", "IS/IEC 60598".
# NOTE: matched CASE-SENSITIVELY on purpose — a case-insensitive match would
# also hit ordinary prose like "the fee is 1000 rupees" and mangle answers.
#
# \d{1,5} rather than \d{2,5}: single-digit standards are real (IS 1 is the
# National Flag of India, IS 8 is coal tar pitch), and requiring two digits let
# them bypass the anti-hallucination guardrail entirely — the model could
# invent "IS 1" and nothing would check it against the retrieved context. The
# same widening is already applied in qco_tables.py and retrieval.py.
IS_NUMBER_PATTERN   = (
    r"\bIS(?:\s*/\s*(?:IEC|ISO))?\s*\d{1,5}"
    r"(?:\s*\(\s*Part\s*[0-9IVXivx]+\s*\))?"
    r"(?:\s*[:\-]\s*\d{4})?"
)
MAX_HISTORY_TURNS   = 6        # conversation history window

# ── Follow-up resolution ─────────────────────────────────────────
# A question like "is it mandatory?" only makes sense against the previous
# turn, so topic terms are carried forward into the RETRIEVAL query (never into
# the answer prompt). See backend/followup.py.
FOLLOWUP_MAX_TERMS  = 6        # most terms carried into a follow-up query
FOLLOWUP_LOOKBACK   = 4        # messages of history scanned for the topic

# ── Structured QCO lookup ────────────────────────────────────────
# Words that carry no product meaning — ignored when matching QCO rows so a
# question like "how do I apply for certification" doesn't match every row.
QCO_STOPWORDS = {
    "the", "and", "for", "are", "what", "which", "how", "does", "can", "you",
    "any", "all", "its", "with", "from", "into", "under", "about", "need",
    "needs", "needed", "require", "requires", "required", "apply", "get",
    "tell", "give", "know", "please", "india", "indian", "bis", "standard",
    "standards", "number", "applicable", "applies", "mandatory", "voluntary",
    "certification", "certified", "certify", "product", "products", "item",
    "items", "list", "info", "information", "detail", "details", "there",
    "this", "that", "have", "has", "was", "were", "will", "would", "should",
}
QCO_MAX_ROWS = 8
