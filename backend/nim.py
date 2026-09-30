"""
Model layer — Groq for chat, pluggable provider for embeddings.

  chat(messages)              → Groq (llama-3.1-8b-instant)
  embed(texts, input_type)    → backend.embeddings (Gemini by default)

Groq uses the standard `openai` SDK pointed at its OpenAI-compatible endpoint.

Embeddings used to be implemented inline here against Ollama, with the comment
"nomic-embed-text is a symmetric model ... no input_type distinction needed".
That was wrong on both counts: the model is prefix-conditioned, and Ollama is
unreachable in deployment. Embeddings now live in backend/embeddings.py, which
handles the document/query asymmetry explicitly per provider. These wrappers
remain so existing call sites keep working.
"""

import logging
from typing import Literal

from backend.config import (
    GROQ_API_KEY, GROQ_BASE_URL, GROQ_CHAT_MODEL,
    CHAT_TEMPERATURE, CHAT_MAX_TOKENS, CHAT_TIMEOUT, CHAT_MAX_RETRIES,
)
from backend.embeddings import (
    EmbeddingError,
    embed as _embed_impl,
    embed_one as _embed_one_impl,
)

log = logging.getLogger("bis.nim")

InputType = Literal["query", "passage"]


# Kept as an alias so existing `except NIMError` handlers still catch
# embedding failures after the move to backend.embeddings.
NIMError = EmbeddingError


# ── Lazy clients ─────────────────────────────────────────────────
_groq_client = None   # chat


def _get_groq_client():
    """Shared OpenAI-SDK client pointed at Groq."""
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            raise NIMError(
                "GROQ_API_KEY is not set. Add it to .env "
                "(get a free key at https://console.groq.com)."
            )
        from openai import OpenAI
        # An explicit timeout and retry budget: a hung LLM call previously had
        # no ceiling at all, so a slow upstream would pin a request open
        # indefinitely.
        _groq_client = OpenAI(
            base_url=GROQ_BASE_URL,
            api_key=GROQ_API_KEY,
            timeout=CHAT_TIMEOUT,
            max_retries=CHAT_MAX_RETRIES,
        )
        log.info("Groq client initialised (%s, model=%s)", GROQ_BASE_URL, GROQ_CHAT_MODEL)
    return _groq_client


# Keep get_client() for any legacy callers (returns None — not used for embeddings anymore)
def get_client():
    return _get_groq_client()


# ── Chat (Groq) ─────────────────────────────────────────────────

def chat(messages: list[dict]) -> str:
    """Chat completion via Groq."""
    resp = _get_groq_client().chat.completions.create(
        model=GROQ_CHAT_MODEL,
        messages=messages,
        temperature=CHAT_TEMPERATURE,
        max_tokens=CHAT_MAX_TOKENS,
    )
    return resp.choices[0].message.content or ""


def chat_stream(messages: list[dict]):
    """
    Streaming chat completion via Groq — yields raw text delta strings.
    The caller must concatenate them to get the full answer.
    """
    stream = _get_groq_client().chat.completions.create(
        model=GROQ_CHAT_MODEL,
        messages=messages,
        temperature=CHAT_TEMPERATURE,
        max_tokens=CHAT_MAX_TOKENS,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


# ── Embeddings (Ollama) ──────────────────────────────────────────

def embed(texts: list[str], input_type: InputType = "passage") -> list[list[float]]:
    """
    Embed a batch of texts with the configured provider.

    `input_type` is significant and is NOT a compatibility no-op: 'passage'
    selects the document-side task type/prefix and 'query' the query-side one.
    Getting it wrong degrades retrieval with no visible symptom.

    Raises NIMError (an alias of EmbeddingError) if the provider is
    unreachable or misconfigured.
    """
    return _embed_impl(texts, input_type)


def embed_one(text: str, input_type: InputType = "passage") -> list[float]:
    """Convenience wrapper for a single string."""
    return _embed_one_impl(text, input_type)
