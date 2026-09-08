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

# ── NVIDIA NIM (embeddings only) ─────────────────────────────────
NVIDIA_API_KEY   = os.getenv("NVIDIA_API_KEY", "")
NIM_BASE_URL     = "https://integrate.api.nvidia.com/v1"
NIM_EMBED_MODEL  = "nvidia/nemotron-3-embed-1b"

# ── Groq (chat / LLM) ───────────────────────────────────────────
# Groq provides ultra-fast inference via an OpenAI-compatible API.
# Get a free key at https://console.groq.com
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL    = "https://api.groq.com/openai/v1"
GROQ_CHAT_MODEL  = "openai/gpt-oss-20b"

# ── Embedding ────────────────────────────────────────────────────
EMBED_DIM        = 2048        # nemotron-3-embed-1b output width
EMBED_MAX_TOKENS = 4096        # per-input ceiling enforced by the model
EMBED_TRUNCATE   = "END"       # server-side policy if an input still overruns
EMBED_BATCH_SIZE = 32          # texts per embeddings API call

# ── LLM ─────────────────────────────────────────────────────────
CHAT_TEMPERATURE = 0.1         # low temp for factual accuracy
CHAT_MAX_TOKENS  = 1024
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
IS_NUMBER_PATTERN   = (
    r"\bIS(?:\s*/\s*(?:IEC|ISO))?\s*\d{2,5}"
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
