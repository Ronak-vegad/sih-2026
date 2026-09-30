"""
inspect_chunks.py — audit the chunker without embedding or indexing anything.

    python scripts/inspect_chunks.py                 # summary + quarantine report
    python scripts/inspect_chunks.py --doc 4         # every chunk of one document
    python scripts/inspect_chunks.py --grep "IS 15410"
    python scripts/inspect_chunks.py --longest 5

WHY THIS EXISTS SEPARATELY FROM reindex.py
------------------------------------------
Chunking decides what the system can possibly retrieve, and a chunking bug is
invisible from the outside: the index builds, queries return results, scores
look normal, and the answers are quietly built from shredded text. Embedding
the corpus costs API calls and several minutes, so the structure has to be
checkable first and on its own.

It also exists because the counts cannot be trusted by themselves. A previous
validation gate on this project reported three "rejections" that were actually
good data ("Propiconazole E.c.", "…1 500 V d.c.", "IS 1"), and acting on the
count alone would have deleted it. Every quarantine and drop is therefore
printed with its reason and enough text to judge individually.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.chunking import chunk_corpus  # noqa: E402
from backend.config import RAW_JSON_PATH  # noqa: E402


def load_documents() -> list[dict]:
    with open(RAW_JSON_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def print_report(report: list[dict]) -> None:
    print("\n" + "=" * 108)
    print("PER-DOCUMENT CHUNKING REPORT")
    print("=" * 108)
    header = (
        f"{'#':>3} {'type':<5} {'title':<44} {'raw':>7} {'kept':>7} "
        f"{'boiler':>7} {'hindi':>6} {'chunks':>7}"
    )
    print(header)
    print("-" * 108)
    for entry in report:
        marker = "!!" if entry["quarantined"] else "  "
        print(
            f"{entry['doc_index']:>3} {entry['source_type']:<5} "
            f"{entry['title'][:44]:<44} "
            f"{entry.get('raw_chars', 0):>7} "
            f"{entry.get('content_chars', 0):>7} "
            f"{entry.get('boilerplate_ratio', 0):>6.0%} "
            f"{entry.get('hindi_lines_removed', 0):>6} "
            f"{entry.get('chunks', 0):>5}{marker}"
        )
    print("-" * 108)


def print_quarantines(report: list[dict]) -> None:
    quarantined = [entry for entry in report if entry["quarantined"]]
    print("\n" + "=" * 108)
    print(f"QUARANTINED DOCUMENTS ({len(quarantined)})")
    print("=" * 108)
    if not quarantined:
        print("None.")
        return
    print(
        "Audit each of these individually against the source before trusting "
        "the count.\n"
    )
    for entry in quarantined:
        print(f"  [{entry['doc_index']}] {entry['title']}")
        print(f"      category    : {entry['category']} / {entry['source_type']}")
        print(f"      raw chars   : {entry.get('raw_chars', 0)}")
        print(f"      kept chars  : {entry.get('content_chars', 0)}")
        print(f"      reason      : {entry['reason']}")
        print()


def print_structure(report: list[dict]) -> None:
    print("=" * 108)
    print("STRUCTURE DETECTED")
    print("=" * 108)
    for entry in report:
        if entry["quarantined"]:
            continue
        bits = []
        if entry.get("table_profile"):
            bits.append(f"table={entry['table_profile']} rows={entry.get('table_rows')}")
        if entry.get("clause_numbering"):
            bits.append("clause-numbered")
        if entry.get("pages"):
            bits.append(f"pages={entry['pages']}")
        if entry.get("duplicates_dropped"):
            bits.append(f"dupes_dropped={entry['duplicates_dropped']}")
        if bits:
            print(f"  [{entry['doc_index']}] {entry['title'][:50]:<52} {', '.join(bits)}")
    print()


def print_metadata_coverage(chunks: list) -> None:
    print("=" * 108)
    print("METADATA COVERAGE")
    print("=" * 108)
    total = len(chunks)
    if not total:
        print("No chunks.")
        return

    fields = [
        "page_start", "clause", "section", "is_numbers",
        "status", "amendment", "sector", "parent_id",
    ]
    for name in fields:
        populated = sum(1 for chunk in chunks if chunk.metadata.get(name))
        print(f"  {name:<16} {populated:>5} / {total}  ({populated / total:>5.1%})")

    kinds: dict[str, int] = {}
    for chunk in chunks:
        kind = chunk.metadata.get("content_kind", "?")
        kinds[kind] = kinds.get(kind, 0) + 1
    print(f"\n  content_kind     {kinds}")

    sizes = sorted(len(chunk.text) for chunk in chunks)
    print(
        f"  chunk chars      min={sizes[0]} "
        f"median={sizes[len(sizes) // 2]} max={sizes[-1]}"
    )

    distinct_is = {
        number
        for chunk in chunks
        for number in chunk.metadata.get("is_numbers", "").strip("|").split("|")
        if number
    }
    print(f"  distinct IS numbers captured: {len(distinct_is)}")
    print()


def show_chunk(chunk, limit: int = 700) -> None:
    meta = chunk.metadata
    print("-" * 108)
    print(f"{chunk.chunk_id}  [{meta.get('content_kind')}]")
    location = []
    if meta.get("page_start"):
        span = (
            f"p{meta['page_start']}"
            if meta["page_start"] == meta.get("page_end")
            else f"p{meta['page_start']}-{meta.get('page_end')}"
        )
        location.append(span)
    if meta.get("clause"):
        location.append(f"clause {meta['clause']}")
    if meta.get("section"):
        location.append(f"section {meta['section']!r}")
    print(f"  at        : {', '.join(location) or '(unlocated)'}")
    print(f"  parent    : {meta.get('parent_id')}")
    facets = {
        key: meta.get(key)
        for key in ("status", "amendment", "sector", "is_numbers")
        if meta.get(key)
    }
    print(f"  facets    : {facets or '(none)'}")
    text = chunk.text
    print(f"  chars     : {len(text)}")
    print()
    print("  " + text[:limit].replace("\n", "\n  "))
    if len(text) > limit:
        print(f"  ... [+{len(text) - limit} chars]")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", type=int, help="show every chunk of this doc index")
    parser.add_argument("--grep", help="show chunks containing this text")
    parser.add_argument("--longest", type=int, help="show the N longest chunks")
    parser.add_argument("--samples", type=int, default=0, help="show N sample chunks")
    args = parser.parse_args()

    documents = load_documents()
    chunks, report = chunk_corpus(documents)

    print_report(report)
    print()
    print_quarantines(report)
    print_structure(report)
    print_metadata_coverage(chunks)

    kept = sum(1 for entry in report if not entry["quarantined"])
    print("=" * 108)
    print(
        f"TOTAL: {len(chunks)} chunks from {kept} of {len(documents)} documents"
    )
    print("=" * 108)

    if args.doc is not None:
        selected = [c for c in chunks if c.metadata["doc_index"] == args.doc]
        print(f"\nAll {len(selected)} chunks of doc {args.doc}:\n")
        for chunk in selected:
            show_chunk(chunk)

    if args.grep:
        needle = args.grep.lower()
        selected = [c for c in chunks if needle in c.text.lower()]
        print(f"\n{len(selected)} chunks contain {args.grep!r}:\n")
        for chunk in selected[:20]:
            show_chunk(chunk)

    if args.longest:
        selected = sorted(chunks, key=lambda c: -len(c.text))[: args.longest]
        print(f"\n{args.longest} longest chunks:\n")
        for chunk in selected:
            show_chunk(chunk)

    if args.samples:
        step = max(1, len(chunks) // args.samples)
        print(f"\n{args.samples} sample chunks:\n")
        for chunk in chunks[::step][: args.samples]:
            show_chunk(chunk)

    return 0


if __name__ == "__main__":
    sys.exit(main())
