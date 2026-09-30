"""
Table-geometry-aware extraction of product -> IS standard rows from BIS PDFs.

WHY THIS REPLACES THE OLD PARSER
--------------------------------
`bis_supplement.parse_qco_rows` worked on flat `page.get_text("text")` output.
For each line containing an IS number it scanned up to 8 lines backwards for
any line holding a 3+ letter word and called that the product name.

On the real PDF that heuristic shifted the entire table by one row. Ground
truth from `find_tables()` on page 2 of the Simplified Procedure list:

    Sr. No. | IS Number         | Product/Title
    1       | IS 15410          | Containers for Packaging of Natural Mineral Water
    2       | IS 13334 (Part 1) | Skimmed Milk Powder
    3       | IS 1165           | Milk Powder
    4       | IS 9537 (Part 3)  | Conduits for electrical installations
    5       | IS 694            | Polyvinyl chloride insulated unsheathed cables

What the old parser stored:

    IS 15410          -> "Product/Title"        (the header row)
    IS 13334 (Part 1) -> "Containers ..."       (row 1's product)
    IS 1165           -> "Skimmed Milk Powder"  (row 2's product)
    IS 9537 (Part 3)  -> "Milk Powder"          (row 3's product)
    IS 694            -> "Conduits ..."         (row 4's product)

Every product was attached to the PREVIOUS row's standard number. A uniform
off-by-one is the worst kind of data error: every row looks plausible and every
row is wrong. Reading real cell geometry removes the guesswork entirely --
cells in the same table row genuinely belong together.

SOURCE CONTROL
--------------
The old pipeline fed EVERY downloaded PDF through the same product parser, so
documents with no product/standard table at all still produced rows:

  * Group-2_23042026.pdf  -- laboratory empanelment list
                             (Sl.No | Name of Lab | State | Status | OSL Code)
  * qc-order-June-2021-2.pdf -- hallmarking district list, Hindi
                             (क्रम सं. | राज्य/संघ राज्य क्षेत्र | जिला)

Those are now explicitly denied rather than silently mis-parsed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from backend.qco_quality import normalize

log = logging.getLogger("bis.qco_tables")


# ── Column role detection ────────────────────────────────────────
# Columns are located by header text, never by position, so a PDF that adds a
# "Remarks" column or reorders cells cannot silently shift the mapping.

_COLUMN_PATTERNS: dict[str, re.Pattern[str]] = {
    "serial": re.compile(
        r"^(?:sr?\.?\s*no\.?|sl\.?\s*no\.?|s\.?\s*no\.?|serial|"
        r"क्रम\s*सं\.?|क्रमांक)$",
        re.I,
    ),
    "is_number": re.compile(
        r"(?:^|\b)(?:is\s*(?:no\.?|number)|indian\s*standard|standard\s*no\.?|"
        r"आईएस|भारतीय\s*मानक)(?:\b|$)",
        re.I,
    ),
    "product": re.compile(
        r"(?:product|title|description|item|particulars?|commodity|goods|"
        r"उत्पाद|वस्तु)",
        re.I,
    ),
}

# Real-world IS references are messier than the obvious "IS 456:2000" shape.
# Every alternative below was rejected by the first version of this pattern
# despite being genuine data in the source table:
#
#   IS 1                        single digit -- The National Flag of India
#   IS/IEC 60079-11             hyphenated part suffix on a 5-digit number
#   IS 15111 (Part 1 and 2)     multi-part
#   IS 2039 (Part 1 to 3)       part range
#   IS 14927 (Part 2/Sec 1)     part + section
_IS_NUMBER_RE = re.compile(
    r"^IS(?:\s*/\s*(?:IEC|ISO|IEEE))?\s*\d{1,5}"
    r"(?:\s*-\s*\d{1,3})?"
    r"(?:\s*\(\s*Parts?\s*[0-9IVXivx]+"
    r"(?:\s*(?:and|to|&|,|/|-)\s*(?:Sec\s*)?[0-9IVXivx]+)*\s*\))?"
    r"(?:\s*[:\-]\s*\d{4})?$",
    re.I,
)


@dataclass
class ProductTableSource:
    """A PDF known to contain a product -> IS standard table."""

    filename: str
    reference: str
    source_url: str
    # Whether this document legally establishes mandatory/voluntary status.
    # The Simplified Procedure list does NOT: it lists products eligible for a
    # faster certification route and is silent on mandatory coverage.
    establishes_status: bool


PRODUCT_TABLE_SOURCES: list[ProductTableSource] = [
    ProductTableSource(
        filename="List-of-Products-Under-Simplified-Procedure.pdf",
        reference="List of Products Under Simplified Procedure",
        source_url=(
            "https://www.bis.gov.in/wp-content/uploads/2024/11/"
            "List-of-Products-Under-Simplified-Procedure.pdf"
        ),
        establishes_status=False,
    ),
]


# PDFs that must never be fed to the product parser, with the reason recorded
# so a future maintainer does not "helpfully" add them back.
DENIED_PDFS: dict[str, str] = {
    "Group-2_23042026.pdf": (
        "laboratory empanelment list (Sl.No | Name of Lab | State | Status | "
        "OSL Code) - contains no product->standard mapping"
    ),
    "qc-order-June-2021-2.pdf": (
        "hallmarking district list in Hindi (क्रम सं. | राज्य | जिला) - "
        "contains no product->standard mapping"
    ),
    "Guidance-document-on-QCOs-Revised-1.pdf": (
        "prose guidance document - no extractable tables"
    ),
}


def _map_columns(header_cells: list[str]) -> dict[str, int] | None:
    """
    Map column roles to indices using header text.

    Returns None when the row is not a recognisable header, which is how
    continuation tables on later pages (no repeated header) are detected.
    """
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header_cells):
        text = normalize(cell)
        if not text:
            continue
        for role, pattern in _COLUMN_PATTERNS.items():
            if role in mapping:
                continue
            if pattern.search(text):
                mapping[role] = index
                break

    # A usable product table needs at least the standard number and the product.
    if "is_number" in mapping and "product" in mapping:
        return mapping
    return None


def _extract_rows(
    raw_rows: list[tuple[list[str | None], int, dict[str, int]]],
) -> tuple[list[dict], int]:
    """
    Turn table cells into records, merging wrapped continuation rows.

    A continuation row has no serial and no IS number but does carry product
    text -- it is the tail of the previous row's title, wrapped by the PDF
    renderer. The Simplified Procedure list has rows like
    '500 kVA, 33 kV Part 3 Natural/Synthetic Organic Ester Liquid Immersed'
    and 'performance requirements' that belong to the row above.

    Crucially this runs over the WHOLE document in reading order, not per
    table. A continuation frequently lands as the first row of the next page's
    table, and processing tables independently dropped exactly those rows
    because there was no preceding record to attach them to.
    """
    records: list[dict] = []
    merged = 0

    for cells, page_number, columns in raw_rows:
        def cell(role: str) -> str:
            index = columns.get(role)
            if index is None or index >= len(cells):
                return ""
            return normalize(cells[index])

        serial = cell("serial")
        is_number = cell("is_number")
        product = cell("product")

        # Header row repeated on a later page.
        if _map_columns([c or "" for c in cells]) is not None:
            continue

        if not serial and not is_number and product:
            if records:
                records[-1]["product_name"] = (
                    f"{records[-1]['product_name']} {product}".strip()
                )
                merged += 1
            else:
                log.warning(
                    "Continuation row with no preceding record on page %d: %r",
                    page_number, product[:80],
                )
            continue

        if not is_number or not product:
            continue

        records.append(
            {
                "serial": serial,
                "is_standard_number": is_number,
                "product_name": product,
                "page_number": page_number,
            }
        )

    return records, merged


def parse_product_table(
    pdf_path: Path,
    source: ProductTableSource,
) -> tuple[list[dict], dict[str, int]]:
    """
    Extract product -> standard rows from one PDF using real table geometry.

    Returns (rows, stats). `stats` records what was skipped and whether the
    serial-number sequence came out contiguous, which is the cheapest available
    self-check that no rows were silently lost.
    """
    import pymupdf as fitz

    stats: dict[str, int] = {
        "pages": 0,
        "tables": 0,
        "raw_rows": 0,
        "rows": 0,
        "continuations_merged": 0,
        "malformed_is_number": 0,
        "serial_gaps": 0,
    }

    # (cells, page_number, column_mapping) in document reading order, so
    # continuation rows can attach across page boundaries.
    pending: list[tuple[list[str | None], int, dict[str, int]]] = []
    columns: dict[str, int] | None = None

    with fitz.open(pdf_path) as document:
        stats["pages"] = document.page_count

        for page_index in range(document.page_count):
            page = document[page_index]
            for table in page.find_tables().tables:
                extracted = table.extract()
                if not extracted:
                    continue

                stats["tables"] += 1
                stats["raw_rows"] += len(extracted)

                # The header appears once, on the first table. Later pages
                # continue the same table without repeating it, so the mapping
                # is remembered.
                header_mapping = _map_columns([c or "" for c in extracted[0]])
                if header_mapping is not None:
                    columns = header_mapping
                    body = extracted[1:]
                elif columns is not None:
                    body = extracted
                else:
                    # No header seen yet and this table has none: assume the
                    # canonical 3-column shape only if the width matches.
                    if len(extracted[0]) == 3:
                        columns = {"serial": 0, "is_number": 1, "product": 2}
                        body = extracted
                    else:
                        log.warning(
                            "%s page %d: table with %d columns and no header - skipped",
                            pdf_path.name, page_index + 1, len(extracted[0]),
                        )
                        continue

                pending.extend(
                    (cells, page_index + 1, columns) for cells in body
                )

    rows, merged = _extract_rows(pending)
    stats["continuations_merged"] = merged

    # Validate and finalise.
    final: list[dict] = []
    for record in rows:
        is_number = record["is_standard_number"]
        if not _IS_NUMBER_RE.match(is_number):
            stats["malformed_is_number"] += 1
            log.debug("Malformed IS number skipped: %r", is_number)
            continue

        final.append(
            {
                "product_name": record["product_name"],
                "is_standard_number": is_number,
                "standard_title": "",
                # Status is deliberately left blank. qco_quality.validate_row
                # decides what may be claimed, based on the source document.
                "mandatory_or_voluntary": "",
                "scheme_type": "",
                "qco_reference": source.reference,
                "source_url": source.source_url,
                "penalty_clause": "",
                "page_number": record["page_number"],
                "extraction_method": "table_parse",
            }
        )

    stats["rows"] = len(final)

    # Serial continuity self-check: the source numbers its products 1..N, so a
    # gap means rows were dropped by the parser rather than absent upstream.
    serials = [
        int(match.group(0))
        for record in rows
        if (match := re.search(r"\d+", record.get("serial") or ""))
    ]
    if serials:
        expected = set(range(min(serials), max(serials) + 1))
        stats["serial_gaps"] = len(expected - set(serials))

    log.info(
        "%s: %d rows from %d tables across %d pages "
        "(%d continuations merged, %d malformed IS numbers, %d serial gaps)",
        pdf_path.name, stats["rows"], stats["tables"], stats["pages"],
        stats["continuations_merged"], stats["malformed_is_number"],
        stats["serial_gaps"],
    )
    return final, stats


def parse_all_product_tables(pdf_dir: Path) -> tuple[list[dict], dict[str, dict]]:
    """Parse every allowlisted product PDF found in `pdf_dir`."""
    all_rows: list[dict] = []
    all_stats: dict[str, dict] = {}

    for source in PRODUCT_TABLE_SOURCES:
        path = pdf_dir / source.filename
        if not path.exists():
            log.warning("Missing product PDF: %s", path)
            continue
        rows, stats = parse_product_table(path, source)
        all_rows.extend(rows)
        all_stats[source.filename] = stats

    for filename, reason in DENIED_PDFS.items():
        if (pdf_dir / filename).exists():
            log.info("Skipping %s by policy: %s", filename, reason)

    return all_rows, all_stats
