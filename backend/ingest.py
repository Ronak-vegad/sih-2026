"""
Ingestion pipeline: chunk → embed → store in ChromaDB.
Run once (or re-run to refresh).  Idempotent — clears & rebuilds collection.

Usage:
    python -m backend.ingest
"""

import json
import re
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import nim
from backend.chunking import chunk_corpus
from backend.config import (
    RAW_JSON_PATH, CHROMA_DIR,
    EMBED_BATCH_SIZE, EMBED_BATCH_SLEEP, EMBED_DIM,
    CHUNK_SIZE, CHUNK_OVERLAP,
)
from backend.embeddings import get_provider, fingerprint, collection_name

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bis.ingest")


# ── Chunking ────────────────────────────────────────────────────

def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Recursive character-based splitter that approximates token count as
    chars / 4. Prefers splitting on double-newlines, then single, then spaces.
    """
    # Approximate character limits
    max_chars     = chunk_size * 4
    overlap_chars = overlap * 4

    if len(text) <= max_chars:
        return [text.strip()] if text.strip() else []

    separators = ["\n\n", "\n", ". ", " ", ""]
    for sep in separators:
        if sep == "":
            # Hard split
            chunks = []
            start = 0
            while start < len(text):
                end = start + max_chars
                chunks.append(text[start:end].strip())
                start = end - overlap_chars
            return [c for c in chunks if c]

        parts = text.split(sep)
        if len(parts) < 2:
            continue

        chunks: list[str] = []
        current = ""
        for part in parts:
            candidate = (current + sep + part) if current else part
            if len(candidate) > max_chars and current:
                chunks.append(current.strip())
                # Start new chunk with overlap: take last overlap_chars of current
                current = current[-overlap_chars:] + sep + part if current else part
            else:
                current = candidate
        if current.strip():
            chunks.append(current.strip())

        if all(len(c) <= max_chars for c in chunks):
            return [c for c in chunks if c]

    return [text.strip()]


# ── Embeddings via Ollama ───────────────────────────────────────────────────

def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed corpus chunks as DOCUMENTS (not queries).

    The distinction is real and matters: with Gemini this selects
    RETRIEVAL_DOCUMENT, and with Ollama/nomic it applies the
    'search_document:' prefix. Embedding passages with the query-side task
    degrades retrieval with no visible symptom, which is why the two paths are
    kept explicit rather than collapsed into one call.
    """
    return nim.embed(texts, input_type="passage")


# ── ChromaDB ────────────────────────────────────────────────────

def get_collection(reset: bool = False):
    """
    Open (or recreate) the collection for the CURRENT embedding model.

    The collection name encodes the embedding fingerprint, so each model gets
    its own index. That makes a model upgrade non-destructive and instantly
    reversible: build the new collection, verify it, and flip EMBED_PROVIDER
    back if the scores regress. The old index is still sitting there intact.

    The fingerprint is also written into the collection metadata so the query
    side can refuse to search an index built by a different model.
    """
    import chromadb

    provider = get_provider()
    name = collection_name(provider)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    if reset:
        try:
            client.delete_collection(name)
            log.info("Deleted existing ChromaDB collection %r.", name)
        except Exception:
            pass

    return client.get_or_create_collection(
        name=name,
        metadata={
            "hnsw:space": "cosine",
            "embedding_fingerprint": fingerprint(provider),
            "embedding_provider": provider.name,
            "embedding_model": provider.model,
            "embedding_dim": provider.dim,
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )


# ── Main ingestion ───────────────────────────────────────────────

# ── Main ingestion ───────────────────────────────────────────────

def run_ingestion(reset: bool = True):
    log.info("Loading documents from %s", RAW_JSON_PATH)
    with open(RAW_JSON_PATH, encoding="utf-8") as f:
        documents = json.load(f)

    # Filter out documents with useless content (< 100 chars)
    documents = [d for d in documents if len(d.get("raw_text", "")) >= 100]
    log.info("Using %d documents (filtered out short/empty ones)", len(documents))

    # ── Structure-aware chunking ─────────────────────────────────
    # chunk_corpus uses clause/table/paragraph structure instead of fixed-size
    # sliding windows.  It also deduplicates identical chunks, strips nav
    # boilerplate, reassembles shredded table rows, and attaches rich metadata
    # (clause number, page range, IS numbers, sector, amendment) to every chunk.
    log.info("Chunking with structure-aware chunker...")
    all_chunk_objs, report = chunk_corpus(documents)

    quarantined = sum(1 for r in report if r["quarantined"])
    log.info(
        "Chunking complete: %d chunks from %d docs (%d quarantined)",
        len(all_chunk_objs), len(documents), quarantined,
    )
    for r in report:
        if r["quarantined"]:
            log.warning("  QUARANTINED: %r — %s", r["title"], r["reason"])
        else:
            log.info(
                "  %-50s → %3d chunks (%.0f%% boilerplate)",
                r["title"][:50], r["chunks"],
                r.get("boilerplate_ratio", 0) * 100,
            )

    if not all_chunk_objs:
        log.error("No chunks produced — aborting ingestion.")
        return 0

    # Convert Chunk objects to parallel lists for ChromaDB upsert
    all_texts:     list[str]  = []
    all_ids:       list[str]  = []
    all_metadatas: list[dict] = []

    for chunk in all_chunk_objs:
        all_texts.append(chunk.text)
        all_ids.append(chunk.chunk_id)
        # chunk.metadata already has all fields from chunk_document; make sure
        # every value is a ChromaDB-safe scalar (str/int/float/bool).
        safe_meta = {}
        for k, v in chunk.metadata.items():
            if isinstance(v, (str, int, float, bool)):
                safe_meta[k] = v
            elif isinstance(v, list):
                safe_meta[k] = ", ".join(str(x) for x in v)
            elif v is None:
                pass  # ChromaDB rejects None values
            else:
                safe_meta[k] = str(v)
        all_metadatas.append(safe_meta)

    log.info("Total chunks to embed: %d", len(all_texts))

    # Embed in batches via the configured provider
    provider = get_provider()
    log.info(
        "Embedding with %s (%s, %d-dim)...",
        provider.name, provider.model, provider.dim,
    )
    all_embeddings: list[list[float]] = []
    for i in range(0, len(all_texts), EMBED_BATCH_SIZE):
        batch = all_texts[i : i + EMBED_BATCH_SIZE]
        log.info("  Embedding batch %d-%d / %d", i + 1, i + len(batch), len(all_texts))
        embs = embed_texts(batch)
        if len(embs) != len(batch):
            raise RuntimeError(
                f"Embedding count mismatch: sent {len(batch)}, got {len(embs)}"
            )
        all_embeddings.extend(embs)
        # Sleep between batches to stay within Gemini free-tier rate limits.
        # Skip the sleep after the last batch.
        if i + EMBED_BATCH_SIZE < len(all_texts):
            log.info("  Rate-limit pause: %.0fs...", EMBED_BATCH_SLEEP)
            time.sleep(EMBED_BATCH_SLEEP)

    if all_embeddings:
        dim = len(all_embeddings[0])
        log.info("Embedding dimension: %d", dim)
        if dim != EMBED_DIM:
            log.warning("Expected %d dims, got %d — check the model slug.", EMBED_DIM, dim)

    # Store in ChromaDB
    log.info("Storing in ChromaDB at %s ...", CHROMA_DIR)
    collection = get_collection(reset=reset)

    UPSERT_BATCH = 500
    for i in range(0, len(all_texts), UPSERT_BATCH):
        collection.upsert(
            ids=all_ids[i : i + UPSERT_BATCH],
            documents=all_texts[i : i + UPSERT_BATCH],
            embeddings=all_embeddings[i : i + UPSERT_BATCH],
            metadatas=all_metadatas[i : i + UPSERT_BATCH],
        )

    final_count = collection.count()
    log.info("Ingestion complete. ChromaDB collection has %d chunks.", final_count)
    return final_count


if __name__ == "__main__":
    run_ingestion(reset=True)

