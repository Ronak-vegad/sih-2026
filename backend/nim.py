"""
NVIDIA NIM model layer — the single place the app talks to NVIDIA.

  chat(messages)              → nvidia/nemotron-3-super-120b-a12b
  embed(texts, input_type)    → nvidia/nemotron-3-embed-1b   (2048-dim)

Both go through NIM's OpenAI-compatible endpoint, so the standard `openai`
SDK is the client. NVIDIA currently hosts no reranking model, so there is no
rerank() here — ordering comes from the BM25 + dense fusion in retrieval.py.
"""

import logging
from typing import Literal

from backend.config import (
    NVIDIA_API_KEY, NIM_BASE_URL,
    NIM_CHAT_MODEL, NIM_EMBED_MODEL,
    EMBED_TRUNCATE, EMBED_DIM,
    CHAT_TEMPERATURE, CHAT_MAX_TOKENS,
)

log = logging.getLogger("bis.nim")

InputType = Literal["query", "passage"]


class NIMError(RuntimeError):
    """Raised when NVIDIA NIM cannot be reached or is misconfigured."""


# ── Lazy client ──────────────────────────────────────────────────
_client = None


def _require_key() -> str:
    if not NVIDIA_API_KEY:
        raise NIMError(
            "NVIDIA_API_KEY is not set. Add it to .env "
            "(get a key at https://build.nvidia.com)."
        )
    return NVIDIA_API_KEY


def get_client():
    """Shared OpenAI-SDK client pointed at NVIDIA NIM."""
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(base_url=NIM_BASE_URL, api_key=_require_key())
        log.info("NVIDIA NIM client initialised (%s)", NIM_BASE_URL)
    return _client


# ── Chat ─────────────────────────────────────────────────────────

def chat(messages: list[dict]) -> str:
    """
    Chat completion via Nemotron 3 Super.

    The caller is responsible for putting `detailed thinking off` at the top of
    the system message — without it this hybrid reasoning model can spill its
    deliberation into the answer text. Any structured reasoning the server
    returns in `reasoning_content` is deliberately ignored; only `content` is
    ever shown to the user.
    """
    resp = get_client().chat.completions.create(
        model=NIM_CHAT_MODEL,
        messages=messages,
        temperature=CHAT_TEMPERATURE,
        max_tokens=CHAT_MAX_TOKENS,
    )
    return resp.choices[0].message.content or ""


def chat_stream(messages: list[dict]):
    """
    Streaming chat completion — yields raw text delta strings as they arrive.
    The caller must concatenate them to get the full answer.
    """
    stream = get_client().chat.completions.create(
        model=NIM_CHAT_MODEL,
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
