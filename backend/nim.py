"""
Model layer — Groq for chat, NVIDIA NIM for embeddings.

  chat(messages)              → Groq  (openai/gpt-oss-20b)
  embed(texts, input_type)    → NVIDIA NIM  (nvidia/nemotron-3-embed-1b, 2048-dim)

Both providers expose OpenAI-compatible endpoints, so the standard `openai`
SDK is used for both. Two separate client instances keep the concerns apart.
"""

import logging
from typing import Literal

from backend.config import (
    NVIDIA_API_KEY, NIM_BASE_URL, NIM_EMBED_MODEL,
    GROQ_API_KEY, GROQ_BASE_URL, GROQ_CHAT_MODEL,
    EMBED_TRUNCATE, EMBED_DIM,
    CHAT_TEMPERATURE, CHAT_MAX_TOKENS,
)

log = logging.getLogger("bis.nim")

InputType = Literal["query", "passage"]


class NIMError(RuntimeError):
    """Raised when a model provider cannot be reached or is misconfigured."""


# ── Lazy clients ─────────────────────────────────────────────────
_groq_client  = None   # chat
_nvidia_client = None  # embeddings


def _get_groq_client():
    """Shared OpenAI-SDK client pointed at Groq."""
    global _groq_client
    if _groq_client is None:
        if not GROQ_API_KEY:
            raise NIMError(
                "GROQ_API_KEY is not set. Add it to .env "
                "(get a key at https://console.groq.com)."
            )
        from openai import OpenAI
        _groq_client = OpenAI(base_url=GROQ_BASE_URL, api_key=GROQ_API_KEY)
        log.info("Groq client initialised (%s, model=%s)", GROQ_BASE_URL, GROQ_CHAT_MODEL)
    return _groq_client


def _get_nvidia_client():
    """Shared OpenAI-SDK client pointed at NVIDIA NIM (embeddings only)."""
    global _nvidia_client
    if _nvidia_client is None:
        if not NVIDIA_API_KEY:
            raise NIMError(
                "NVIDIA_API_KEY is not set. Add it to .env "
                "(get a key at https://build.nvidia.com)."
            )
        from openai import OpenAI
        _nvidia_client = OpenAI(base_url=NIM_BASE_URL, api_key=NVIDIA_API_KEY)
        log.info("NVIDIA NIM client initialised (%s)", NIM_BASE_URL)
    return _nvidia_client


# Keep backward compat for any code that calls get_client() or _require_key()
def get_client():
    return _get_nvidia_client()


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


# ── Embeddings ───────────────────────────────────────────────────

def embed(texts: list[str], input_type: InputType) -> list[list[float]]:
    """
    Embed a batch of texts with nemotron-3-embed-1b.

    This model is ASYMMETRIC: documents must be embedded with
    input_type="passage" and search queries with input_type="query".
    Mixing them up quietly wrecks retrieval quality, so the argument is
    required rather than defaulted.

    `input_type` and `truncate` are not part of the OpenAI schema, so they ride
    along in extra_body as top-level JSON fields.
    """
    if not texts:
        return []

    resp = get_client().embeddings.create(
        model=NIM_EMBED_MODEL,
        input=texts,
        encoding_format="float",
        extra_body={"input_type": input_type, "truncate": EMBED_TRUNCATE},
    )
    # The API may return items out of order; sort by index to be safe.
    ordered = sorted(resp.data, key=lambda d: d.index)
    vectors = [d.embedding for d in ordered]

    if vectors and len(vectors[0]) != EMBED_DIM:
        log.warning(
            "Unexpected embedding dimension %d (expected %d) — the vector "
            "index and query embeddings must agree.",
            len(vectors[0]), EMBED_DIM,
        )
    return vectors


def embed_one(text: str, input_type: InputType) -> list[float]:
    """Convenience wrapper for a single string."""
    vectors = embed([text], input_type)
    if not vectors:
        raise NIMError("Embedding request returned no vectors.")
    return vectors[0]
