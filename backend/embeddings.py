"""
Embedding provider layer.

WHY THIS EXISTS
---------------
Two defects in the previous setup, both invisible at runtime:

1. PRODUCTION HAD NO DENSE RETRIEVAL AT ALL.
   Embeddings came from Ollama on `http://localhost:11434`. `render.yaml`
   deploys the backend to Render, where no Ollama process exists, so every
   embedding call raised ConnectError. `hybrid_retrieve` caught it and fell
   back to BM25-only -- reasonable -- but then assigned keyword hits a
   synthetic similarity of `0.85 * normalised_bm25`, which sails past
   SIM_THRESHOLD (0.30). The deployed system was doing keyword-only retrieval
   while reporting 0.85 confidence, and the refusal path could never fire.

2. THE QUERY/DOCUMENT ASYMMETRY WAS IGNORED.
   `nim.py` documented nomic-embed-text as "a symmetric model, so queries and
   passages share the same embedding space" and accepted `input_type` as a
   no-op. That is not correct: nomic-embed-text v1.5 is prefix-conditioned and
   expects `search_document:` on passages and `search_query:` on queries.
   Ollama's /api/embed does not add them. Retrieval quality was being left on
   the table with nothing to reveal it.

Providers are therefore selected by env var, and every provider must state
explicitly how it handles the document/query distinction.

WHY GEMINI IS THE DEFAULT
-------------------------
  * genuine free tier, so production gets real dense retrieval at no cost
  * native RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY task types, which is a
    first-class fix for defect 2 rather than a prefix convention
  * 100+ languages, which the Hindi/Hinglish requirement needs
"""

from __future__ import annotations

import logging
import math
import os
import time
from typing import Literal, Protocol

import httpx

log = logging.getLogger("bis.embeddings")

InputType = Literal["query", "passage"]


class EmbeddingError(RuntimeError):
    """Raised when an embedding provider is unreachable or misconfigured."""


# ── Vector helpers ───────────────────────────────────────────────

def l2_normalise(vector: list[float]) -> list[float]:
    """
    Scale a vector to unit length.

    Required for Gemini at any output dimensionality below the native 3072:
    Matryoshka truncation drops trailing components and the API does NOT
    re-normalise, so a truncated 768-dim vector comes back with an L2 norm of
    about 0.59. Cosine distance is scale-invariant, so this would not corrupt
    results today, but it silently breaks the moment anyone switches the index
    to L2 or inner-product distance. Normalising here keeps cosine and dot
    product interchangeable and matches Google's own guidance.
    """
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        return vector
    return [component / norm for component in vector]


def _retry_request(
    send: callable,
    *,
    attempts: int = 4,
    base_delay: float = 1.5,
    provider: str = "embedding",
) -> httpx.Response:
    """
    Issue a request with exponential backoff on rate limits and 5xx.

    Free embedding tiers are rate limited per minute, and a re-index of a few
    thousand chunks will hit that ceiling. Failing the whole ingest on the
    first 429 would make re-indexing effectively impossible.
    """
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            response = send()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            delay = base_delay * (2 ** attempt)
            log.warning(
                "%s request failed (%s) - retrying in %.1fs (%d/%d)",
                provider, type(exc).__name__, delay, attempt + 1, attempts,
            )
            time.sleep(delay)
            continue

        if response.status_code in (429, 500, 502, 503, 504):
            last_error = httpx.HTTPStatusError(
                f"HTTP {response.status_code}", request=response.request,
                response=response,
            )
            if attempt == attempts - 1:
                break
            # Honour Retry-After when the service supplies it.
            retry_after = response.headers.get("Retry-After")
            delay = (
                float(retry_after)
                if retry_after and retry_after.replace(".", "").isdigit()
                else base_delay * (2 ** attempt)
            )
            log.warning(
                "%s returned HTTP %d - retrying in %.1fs (%d/%d)",
                provider, response.status_code, delay, attempt + 1, attempts,
            )
            time.sleep(delay)
            continue

        return response

    raise EmbeddingError(f"{provider} unavailable after {attempts} attempts: {last_error}")


# ── Provider protocol ────────────────────────────────────────────

class EmbeddingProvider(Protocol):
    """A source of embeddings for documents and queries."""

    name: str
    model: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


# ── Gemini ───────────────────────────────────────────────────────

class GeminiProvider:
    """
    Google Gemini embeddings via the REST API.

    Asymmetry is handled natively through `taskType`: RETRIEVAL_DOCUMENT when
    indexing, RETRIEVAL_QUERY when searching. The two produce vectors in a
    shared space that is tuned for the asymmetric case, which is precisely what
    question-answering retrieval needs.
    """

    name = "gemini"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    # batchEmbedContents accepts many requests per call; keep well inside it so
    # a single failure retries cheaply.
    BATCH_LIMIT = 100

    def __init__(self, api_key: str, model: str, dim: int, timeout: float = 120.0):
        if not api_key:
            raise EmbeddingError(
                "GOOGLE_API_KEY is not set. Add it to .env "
                "(free key at https://aistudio.google.com/app/apikey)."
            )
        self.model = model
        self.dim = dim
        self._api_key = api_key
        self._timeout = timeout
        # Native output width. Anything narrower is Matryoshka-truncated and
        # must be re-normalised (see l2_normalise).
        self._native_dim = 3072

    @property
    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}

    def _request_body(self, text: str, task_type: str) -> dict:
        body: dict = {
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": text}]},
            "taskType": task_type,
        }
        if self.dim != self._native_dim:
            body["outputDimensionality"] = self.dim
        return body

    def _finalise(self, vector: list[float]) -> list[float]:
        if self.dim != self._native_dim:
            return l2_normalise(vector)
        return vector

    def _embed_batch(self, texts: list[str], task_type: str) -> list[list[float]]:
        url = f"{self.BASE_URL}/models/{self.model}:batchEmbedContents"
        payload = {"requests": [self._request_body(t, task_type) for t in texts]}

        response = _retry_request(
            lambda: httpx.post(
                url, headers=self._headers, json=payload, timeout=self._timeout
            ),
            provider="Gemini",
        )
        if response.status_code != 200:
            raise EmbeddingError(
                f"Gemini batchEmbedContents returned HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )

        embeddings = response.json().get("embeddings", [])
        if len(embeddings) != len(texts):
            raise EmbeddingError(
                f"Gemini embedding count mismatch: sent {len(texts)}, "
                f"received {len(embeddings)}."
            )
        return [self._finalise(item["values"]) for item in embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_LIMIT):
            batch = texts[start : start + self.BATCH_LIMIT]
            vectors.extend(self._embed_batch(batch, "RETRIEVAL_DOCUMENT"))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        url = f"{self.BASE_URL}/models/{self.model}:embedContent"
        response = _retry_request(
            lambda: httpx.post(
                url,
                headers=self._headers,
                json=self._request_body(text, "RETRIEVAL_QUERY"),
                timeout=self._timeout,
            ),
            provider="Gemini",
        )
        if response.status_code != 200:
            raise EmbeddingError(
                f"Gemini embedContent returned HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )
        return self._finalise(response.json()["embedding"]["values"])


# ── OpenAI ───────────────────────────────────────────────────────

class OpenAIProvider:
    """
    OpenAI embeddings. Symmetric by design -- text-embedding-3-* models need no
    task prefix, so documents and queries are embedded identically.
    """

    name = "openai"

    def __init__(self, api_key: str, model: str, dim: int, timeout: float = 120.0):
        if not api_key:
            raise EmbeddingError("OPENAI_API_KEY is not set.")
        self.model = model
        self.dim = dim
        self._api_key = api_key
        self._timeout = timeout

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key=self._api_key, timeout=self._timeout)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client().embeddings.create(
            model=self.model, input=texts, dimensions=self.dim
        )
        return [item.embedding for item in response.data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts) if texts else []

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


# ── Ollama ───────────────────────────────────────────────────────

class OllamaProvider:
    """
    Local Ollama embeddings, for offline development.

    nomic-embed-text is prefix-conditioned, NOT symmetric as the previous code
    claimed. Ollama does not add the prefixes itself, so they are applied here:
    `search_document:` when indexing and `search_query:` when searching. This
    is the fix for defect 2 described in the module docstring.
    """

    name = "ollama"

    # Models that require Nomic's task prefixes.
    _PREFIXED_MODELS = ("nomic-embed-text",)
    DOCUMENT_PREFIX = "search_document: "
    QUERY_PREFIX = "search_query: "

    def __init__(self, base_url: str, model: str, dim: int, timeout: float = 120.0):
        self.model = model
        self.dim = dim
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def _needs_prefix(self) -> bool:
        return any(self.model.startswith(m) for m in self._PREFIXED_MODELS)

    def _embed(self, texts: list[str], prefix: str) -> list[list[float]]:
        if self._needs_prefix:
            texts = [f"{prefix}{t}" for t in texts]

        response = _retry_request(
            lambda: httpx.post(
                f"{self._base_url}/api/embed",
                json={"model": self.model, "input": texts},
                timeout=self._timeout,
            ),
            provider="Ollama",
        )
        if response.status_code != 200:
            raise EmbeddingError(
                f"Ollama /api/embed returned HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )

        vectors = response.json().get("embeddings", [])
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"Ollama embedding count mismatch: sent {len(texts)}, "
                f"received {len(vectors)}."
            )
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, self.DOCUMENT_PREFIX) if texts else []

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], self.QUERY_PREFIX)[0]


# ── Factory + fingerprint ────────────────────────────────────────

_PROVIDER_CACHE: EmbeddingProvider | None = None


def build_provider() -> EmbeddingProvider:
    """Construct the provider named by EMBED_PROVIDER."""
    from backend.config import (
        EMBED_PROVIDER, EMBED_DIM,
        GOOGLE_API_KEY, GEMINI_EMBED_MODEL,
        OPENAI_API_KEY, OPENAI_EMBED_MODEL,
        OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL,
    )

    provider = (EMBED_PROVIDER or "gemini").strip().lower()

    if provider == "gemini":
        return GeminiProvider(GOOGLE_API_KEY, GEMINI_EMBED_MODEL, EMBED_DIM)
    if provider == "openai":
        return OpenAIProvider(OPENAI_API_KEY, OPENAI_EMBED_MODEL, EMBED_DIM)
    if provider == "ollama":
        return OllamaProvider(OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL, EMBED_DIM)

    raise EmbeddingError(
        f"Unknown EMBED_PROVIDER {provider!r}. "
        f"Supported values: gemini, openai, ollama."
    )


def get_provider() -> EmbeddingProvider:
    """Process-wide provider singleton."""
    global _PROVIDER_CACHE
    if _PROVIDER_CACHE is None:
        _PROVIDER_CACHE = build_provider()
        log.info(
            "Embedding provider: %s (model=%s, dim=%d)",
            _PROVIDER_CACHE.name, _PROVIDER_CACHE.model, _PROVIDER_CACHE.dim,
        )
    return _PROVIDER_CACHE


def reset_provider() -> None:
    """Drop the cached provider. Used by tests and the re-index script."""
    global _PROVIDER_CACHE
    _PROVIDER_CACHE = None


def fingerprint(provider: EmbeddingProvider | None = None) -> str:
    """
    Identity of the embedding space, stored in the vector index metadata.

    Comparing this string at startup is what makes a model swap safe. Querying
    a Gemini-built index with Ollama vectors returns confident nonsense -- the
    numbers are all valid, the neighbours are all wrong, and nothing in the
    response looks unusual. Only an explicit equality check catches it, which
    is why the value is deliberately coarse and human-readable.
    """
    provider = provider or get_provider()
    return f"{provider.name}:{provider.model}:{provider.dim}"


def collection_name(provider: EmbeddingProvider | None = None) -> str:
    """
    Vector-store collection name for the current embedding model.

    Encoding the fingerprint in the NAME (not just the metadata) is what makes
    a model upgrade safe and reversible: each model owns a separate index, so
    building a new one never destroys the old one and rolling back is a config
    change rather than a re-index. Chroma allows [a-zA-Z0-9._-] and 3-63
    characters, so the fingerprint is slugified.
    """
    import re

    base = os.getenv("CHROMA_COLLECTION_PREFIX", "bis_docs")
    slug = re.sub(r"[^a-z0-9]+", "_", fingerprint(provider).lower()).strip("_")
    name = f"{base}_{slug}"
    if len(name) > 63:
        # Keep it deterministic rather than truncating blindly.
        import hashlib
        digest = hashlib.sha1(slug.encode()).hexdigest()[:10]
        name = f"{base}_{digest}"
    return name


class IndexMismatchError(RuntimeError):
    """Raised when the vector index was built by a different embedding model."""


def verify_index_fingerprint(collection) -> None:
    """
    Refuse to serve queries against an index built by a different model.

    This is the guard that makes embedding consistency enforceable rather than
    aspirational. A mismatch is uniquely nasty because nothing fails: the query
    vector has valid numbers, the index returns the mathematically nearest
    neighbours in a space those vectors do not inhabit, similarity scores look
    entirely normal, and the model then answers fluently from irrelevant
    passages. There is no symptom to notice, so the only defence is an explicit
    equality check at startup.
    """
    expected = fingerprint()
    metadata = getattr(collection, "metadata", None) or {}
    actual = metadata.get("embedding_fingerprint")

    if actual is None:
        raise IndexMismatchError(
            f"Collection {getattr(collection, 'name', '?')!r} carries no "
            f"embedding fingerprint, so it predates embedding-consistency "
            f"checks and cannot be trusted with {expected!r} query vectors. "
            f"Rebuild it: python scripts/reindex.py"
        )

    if actual != expected:
        raise IndexMismatchError(
            f"Embedding model mismatch. The index was built with {actual!r} "
            f"but queries would be embedded with {expected!r}. Searching it "
            f"would return confident nonsense. Either set EMBED_PROVIDER / "
            f"EMBED_DIM back to match the index, or rebuild it: "
            f"python scripts/reindex.py"
        )


# ── Back-compat shims ────────────────────────────────────────────
# `nim.embed` / `nim.embed_one` remain the call sites used by ingest and
# retrieval; they now delegate here.

def embed(texts: list[str], input_type: InputType = "passage") -> list[list[float]]:
    """Embed a batch, routing by input type to the correct task/prefix."""
    provider = get_provider()
    if input_type == "query":
        return [provider.embed_query(t) for t in texts]
    return provider.embed_documents(texts)


def embed_one(text: str, input_type: InputType = "passage") -> list[float]:
    """Embed a single string."""
    provider = get_provider()
    if input_type == "query":
        return provider.embed_query(text)
    return provider.embed_documents([text])[0]
