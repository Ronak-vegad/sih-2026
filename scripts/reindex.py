"""
reindex.py — Build the vector index for the current embedding model.

    python scripts/reindex.py               # build/rebuild current index
    python scripts/reindex.py --list        # show every index on disk
    python scripts/reindex.py --verify      # check the current index only
    python scripts/reindex.py --prune       # delete indexes for other models

WHY INDEXES ARE VERSIONED
-------------------------
Each embedding model gets its own collection, named after its fingerprint
(provider:model:dim). A model upgrade is therefore additive: build the new
index, compare eval scores, and if they regress just point EMBED_PROVIDER back
at the old one. Nothing was overwritten, so rollback costs a config change
instead of a full re-index.

This also makes the failure this guards against impossible to hit by accident.
Querying an index with vectors from a different model does not error --- it
returns the nearest neighbours in a space those vectors do not inhabit, with
ordinary-looking similarity scores, and the LLM then answers confidently from
irrelevant passages. There is no symptom, so the name and the stored
fingerprint are both checked before any query is served.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import CHROMA_DIR                      # noqa: E402
from backend.embeddings import (                            # noqa: E402
    collection_name, fingerprint, get_provider,
    verify_index_fingerprint, IndexMismatchError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("reindex")


def _client():
    import chromadb
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def list_indexes() -> int:
    """Show every collection on disk with its embedding fingerprint."""
    client = _client()
    collections = client.list_collections()
    current = collection_name()

    if not collections:
        print("No collections found. Build one with: python scripts/reindex.py")
        return 0

    print(f"\n{'':2} {'collection':<46} {'chunks':>7}  fingerprint")
    print("-" * 100)
    for entry in collections:
        collection = client.get_collection(entry.name)
        metadata = collection.metadata or {}
        marker = "->" if entry.name == current else "  "
        print(
            f"{marker} {entry.name:<46} {collection.count():>7}  "
            f"{metadata.get('embedding_fingerprint', '(none — predates checks)')}"
        )
    print("-" * 100)
    print(f"'->' marks the index the app will use now ({fingerprint()}).\n")
    return 0


def verify() -> int:
    """Check that the current index exists and matches the current model."""
    name = collection_name()
    try:
        collection = _client().get_collection(name)
    except Exception:
        print(f"FAIL: no index named {name!r}. Run: python scripts/reindex.py")
        return 1

    try:
        verify_index_fingerprint(collection)
    except IndexMismatchError as exc:
        print(f"FAIL: {exc}")
        return 1

    count = collection.count()
    if count == 0:
        print(f"FAIL: index {name!r} exists but is empty.")
        return 1

    print(f"OK: {name!r} — {count} chunks, fingerprint {fingerprint()}")
    return 0


def prune(assume_yes: bool = False) -> int:
    """Delete collections belonging to other embedding models."""
    client = _client()
    current = collection_name()
    stale = [c.name for c in client.list_collections() if c.name != current]

    if not stale:
        print("Nothing to prune — only the current index exists.")
        return 0

    print("These indexes belong to other embedding models:")
    for name in stale:
        print(f"  {name}")
    print(
        "\nPruning removes your ability to roll back to those models "
        "without a full re-index."
    )
    if not assume_yes:
        reply = input("Delete them? [y/N] ").strip().lower()
        if reply != "y":
            print("Aborted.")
            return 0

    for name in stale:
        client.delete_collection(name)
        log.info("Deleted collection %r", name)
    return 0


def build() -> int:
    """Run ingestion into a fresh collection for the current model."""
    from backend.ingest import run_ingestion

    provider = get_provider()
    name = collection_name(provider)

    print("=" * 72)
    print("REINDEX")
    print("=" * 72)
    print(f"  provider    : {provider.name}")
    print(f"  model       : {provider.model}")
    print(f"  dimensions  : {provider.dim}")
    print(f"  fingerprint : {fingerprint(provider)}")
    print(f"  collection  : {name}")
    print("=" * 72)

    count = run_ingestion(reset=True)

    print(f"\nIndexed {count} chunks into {name!r}.")
    return verify()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="list indexes on disk")
    group.add_argument("--verify", action="store_true", help="verify current index")
    group.add_argument("--prune", action="store_true", help="delete other models' indexes")
    parser.add_argument("--yes", action="store_true", help="skip prune confirmation")
    args = parser.parse_args()

    if args.list:
        return list_indexes()
    if args.verify:
        return verify()
    if args.prune:
        return prune(assume_yes=args.yes)
    return build()


if __name__ == "__main__":
    sys.exit(main())
