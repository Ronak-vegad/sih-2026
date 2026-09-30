"""
rebuild_qco.py — Rebuild the structured QCO table from trusted sources only.

    python rebuild_qco.py            # rebuild
    python rebuild_qco.py --dry-run  # report what would change, write nothing

WHY THIS WAS REWRITTEN
----------------------
The previous version tried to clean the table in place and silently failed.
Its garbage filter was anchored (`^[\\d\\s.,;:()/-]{1,12}$`) but every junk
product name in the database is wrapped in U+202D ... U+202C bidi control
characters, so the anchors never matched and 0 rows were deleted. It then
re-extracted from *every* PDF in data/pdfs using the line-proximity parser,
which is the parser that created the corruption in the first place.

This version rebuilds from scratch with two trusted inputs:

  1. SEED_QCO_ROWS   -- 60 hand-curated rows, each citing a real Quality
                        Control Order. A human made the product<->standard
                        association, so it is trustworthy.
  2. table_parse     -- rows recovered by backend.qco_tables using real PDF
                        table geometry, which fixed a systematic off-by-one
                        that had attached every product to the PREVIOUS row's
                        standard number.

Every row is then passed through backend.qco_quality.validate_row, which
normalises invisible characters, rejects junk names, and -- most importantly --
refuses to report mandatory/voluntary status unless the citing document
actually establishes it.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from backend.config import QCO_DB_PATH, DATA_DIR            # noqa: E402
from backend.qco_quality import validate_rows, STATUS_UNKNOWN  # noqa: E402
from backend.qco_tables import parse_all_product_tables     # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("rebuild_qco")

PDF_DIR = DATA_DIR / "pdfs"

SCHEMA = """
CREATE TABLE IF NOT EXISTS qco_standards (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name           TEXT NOT NULL,
    is_standard_number     TEXT NOT NULL,
    standard_title         TEXT,
    mandatory_or_voluntary TEXT,
    scheme_type            TEXT,
    qco_reference          TEXT,
    source_url             TEXT,
    penalty_clause         TEXT,
    -- Added by the data-quality rebuild:
    extraction_method      TEXT NOT NULL,  -- curated | table_parse
    status_basis           TEXT NOT NULL,  -- established | unknown
    status_note            TEXT,
    page_number            INTEGER,
    UNIQUE(product_name, is_standard_number)
);
CREATE INDEX IF NOT EXISTS idx_qco_is_number ON qco_standards(is_standard_number);
CREATE INDEX IF NOT EXISTS idx_qco_product   ON qco_standards(product_name);
CREATE INDEX IF NOT EXISTS idx_qco_basis     ON qco_standards(status_basis);
"""

COLUMNS = (
    "product_name", "is_standard_number", "standard_title",
    "mandatory_or_voluntary", "scheme_type", "qco_reference",
    "source_url", "penalty_clause",
    "extraction_method", "status_basis", "status_note", "page_number",
)


def collect_rows() -> tuple[list[dict], dict[str, int]]:
    """Gather candidate rows from every trusted source."""
    from scraper.bis_supplement import SEED_QCO_ROWS

    candidates: list[dict] = []

    for row in SEED_QCO_ROWS:
        candidates.append({**row, "extraction_method": "curated", "page_number": None})
    log.info("Curated seed rows: %d", len(SEED_QCO_ROWS))

    table_rows, stats = parse_all_product_tables(PDF_DIR)
    candidates.extend(table_rows)
    for filename, stat in stats.items():
        log.info(
            "Table-parsed %s: %d rows (%d continuations merged, "
            "%d malformed IS numbers)",
            filename, stat["rows"], stat["continuations_merged"],
            stat["malformed_is_number"],
        )

    return candidates, {"curated": len(SEED_QCO_ROWS), "table_parse": len(table_rows)}


def previous_summary() -> dict:
    """Snapshot of the existing table, for the before/after report."""
    if not Path(QCO_DB_PATH).exists():
        return {"total": 0}
    conn = sqlite3.connect(QCO_DB_PATH)
    try:
        total = conn.execute("SELECT COUNT(*) FROM qco_standards").fetchone()[0]
        mandatory = conn.execute(
            "SELECT COUNT(*) FROM qco_standards "
            "WHERE lower(mandatory_or_voluntary) = 'mandatory'"
        ).fetchone()[0]
        return {"total": total, "mandatory": mandatory}
    except sqlite3.Error:
        return {"total": 0}
    finally:
        conn.close()


def write_rows(rows: list[dict]) -> int:
    """Replace the table contents with `rows`."""
    Path(QCO_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(QCO_DB_PATH)
    try:
        # Drop rather than DELETE: the schema itself gained columns, and a
        # stale schema is how the previous corruption stayed invisible.
        conn.execute("DROP TABLE IF EXISTS qco_standards")
        conn.executescript(SCHEMA)

        placeholders = ",".join("?" * len(COLUMNS))
        conn.executemany(
            f"INSERT OR IGNORE INTO qco_standards ({','.join(COLUMNS)}) "
            f"VALUES ({placeholders})",
            [tuple(row.get(column) for column in COLUMNS) for row in rows],
        )
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM qco_standards").fetchone()[0]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would change without writing the database",
    )
    args = parser.parse_args()

    before = previous_summary()
    candidates, source_counts = collect_rows()
    clean, rejected = validate_rows(candidates)

    established = sum(1 for r in clean if r["status_basis"] == "established")
    by_method: dict[str, int] = {}
    for row in clean:
        by_method[row["extraction_method"]] = by_method.get(row["extraction_method"], 0) + 1

    print("\n" + "=" * 72)
    print("QCO TABLE REBUILD" + ("  (DRY RUN)" if args.dry_run else ""))
    print("=" * 72)
    print(f"  Previous table            : {before.get('total', 0)} rows "
          f"({before.get('mandatory', 0)} claimed 'Mandatory')")
    print(f"  Candidates collected      : {len(candidates)}")
    for source, count in source_counts.items():
        print(f"      from {source:<18}: {count}")
    print(f"  Rejected by quality gate  : {len(candidates) - len(clean)}")
    for reason, count in sorted(rejected.items(), key=lambda kv: -kv[1]):
        print(f"      {reason:<34}: {count}")
    print(f"  Accepted                  : {len(clean)}")
    for method, count in sorted(by_method.items()):
        print(f"      {method:<34}: {count}")
    print(f"  Status ESTABLISHED by a QCO: {established}")
    print(f"  Status UNKNOWN (honest)    : {len(clean) - established}")

    if args.dry_run:
        print("\n  Dry run - database not modified.")
        print("=" * 72)
        return 0

    written = write_rows(clean)
    print(f"  Rows written              : {written}")
    print("=" * 72)
    print("\nSample rows (product <-> standard pairing is now geometry-derived):")
    print(f"  {'IS Standard':<22} | {'Product':<46} | Status")
    print("  " + "-" * 92)

    conn = sqlite3.connect(QCO_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute(
            "SELECT is_standard_number, product_name, mandatory_or_voluntary "
            "FROM qco_standards ORDER BY status_basis ASC, id LIMIT 12"
        ):
            status = row["mandatory_or_voluntary"] or ""
            if status == STATUS_UNKNOWN:
                status = "status not established"
            print(f"  {row['is_standard_number'][:22]:<22} | "
                  f"{row['product_name'][:46]:<46} | {status[:28]}")
    finally:
        conn.close()
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
