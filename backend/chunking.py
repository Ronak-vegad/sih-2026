"""
Structure-aware chunking for the BIS corpus.

WHY THIS REPLACED THE 2000-CHAR SPLITTER
----------------------------------------
The previous chunker was `text[start:end]` with an overlap. Four things were
wrong with that, each of them measured against this corpus rather than assumed:

1. PAGE MARKERS WERE THROWN AWAY.
   The scraper emits `[Page N]` into raw_text for all 5 PDFs. The splitter
   treated those as ordinary characters, so a retrieved passage could not say
   which page it came from. Clause-level citation and any PDF click-through
   viewer are impossible without it, so pages are now parsed into
   `page_start`/`page_end` and the marker lines are removed from the text.

2. NAV BOILERPLATE WAS INDEXED AS CONTENT.
   Every HTML page begins with its site navigation -- 9 lines on
   "Product Certification Process" but 47 on "Hallmarking FAQ". Those lines are
   near-identical across documents, so they embed to nearly the same vector and
   compete with real content for retrieval slots. Fortunately the scraper emits
   a `## <Heading>` line at the nav/content boundary in all 10 real HTML docs,
   so the strip is an exact structural rule, not a fragile line-count guess.

3. TABLES WERE SHREDDED INTO NOISE.
   Two PDFs are tables that extract one cell per line. "IS 15410" and
   "Containers for Packaging of Natural Mineral Water" landed in different
   chunks, so neither was retrievable by the other's terms -- roughly 1,100
   standard/product pairs unusable. Rows are now reassembled before chunking.

4. CLAUSE STRUCTURE WAS INVISIBLE.
   The QCO guidance document is cleanly numbered (`1.`, `1.1`, `1.2.1`, and one
   dotless `3 `). Splitting mid-clause produced chunks that began halfway
   through a sentence and carried no clause label.

A NOTE ON ROW PARSING
---------------------
Table rows are found by FOLLOWING THE DOCUMENT'S OWN Sl.No. SEQUENCE, not by
matching `^\\d+\\.`. Two measured reasons:

  * The laboratories table drops the trailing dot from row 100 onward
    ("100 Vehicle Research & Development"), so a dot-anchored regex merges
    rows 100-350 into row 99.
  * OSL codes are bare 7-digit numbers on their own line, so a bare-integer
    regex treats them as row starts.

Following the sequence with a small lookahead window is immune to both, and it
tolerates the genuine numbering gaps in the source (the products table really
does skip 403, 468 and 515 -- verified against the PDF, not a parser bug).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from backend.config import CHUNK_SIZE, CHUNK_OVERLAP, IS_NUMBER_PATTERN

log = logging.getLogger("bis.chunking")


# ── Sizing ──────────────────────────────────────────────────────
# CHUNK_SIZE is in tokens; the corpus is mostly English prose where ~4 chars
# per token holds well enough for a chunking budget.
MAX_CHARS = CHUNK_SIZE * 4
OVERLAP_CHARS = CHUNK_OVERLAP * 4

# Table units leave headroom for the context header that is prefixed later, so
# that a full table chunk still fits the budget without being hard-split.
TABLE_UNIT_CHARS = MAX_CHARS - 200

# A document that yields less than this after boilerplate removal is not a
# document, it is a navigation menu. See QuarantineReason.
MIN_DOC_CHARS = 200

# Above this fraction of boilerplate, the "document" is a scrape failure.
MAX_BOILERPLATE_RATIO = 0.90


# ── Character-level cleanup ─────────────────────────────────────
# The products table wraps every cell in U+202D LEFT-TO-RIGHT OVERRIDE and
# U+202C POP DIRECTIONAL FORMATTING. Those are invisible, survive JSON, and
# break every regex that anchors on ^ or \b.
_BIDI_CONTROLS = {
    0x200E, 0x200F,                          # LRM, RLM
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # embedding / override / pop
    0x2066, 0x2067, 0x2068, 0x2069,          # isolates
    0x200B, 0x200C, 0x200D, 0xFEFF,          # zero-width + BOM
}
_CONTROL_TRANSLATION = {cp: None for cp in _BIDI_CONTROLS}

# Private Use Area. Broken embedded PDF fonts map glyphs here, so these
# codepoints carry no recoverable text -- only a false sense of content.
_PUA_RE = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd]")

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def clean_line(line: str) -> str:
    """Remove invisible controls, PUA glyphs and NBSP from a single line."""
    line = line.translate(_CONTROL_TRANSLATION)
    line = _PUA_RE.sub("", line)
    line = line.replace("\u00a0", " ").replace("\t", " ")
    return re.sub(r" {2,}", " ", line).strip()


def devanagari_ratio(line: str) -> float:
    """Fraction of a line's letters that are Devanagari."""
    letters = _LETTER_RE.findall(line)
    if not letters:
        return 0.0
    devanagari = len(_DEVANAGARI_RE.findall(line))
    return devanagari / len(letters)


def is_devanagari_line(line: str, threshold: float = 0.30) -> bool:
    """
    True for lines that are predominantly Devanagari.

    These are dropped rather than indexed. The reason is not that Hindi does
    not matter -- it is that the Hindi in this corpus is mojibaked by broken
    PDF font encodings and is not the text it appears to be. The Hallmarking
    QCO renders "आदेश" as "आदेि" and the guidance document renders
    "मार्गदर्शन" as "मार्गदर्गन". Embedding corrupted Devanagari produces
    vectors for words that do not exist, which cannot match a correctly spelled
    Hindi query but can still win retrieval slots. The English on the same
    pages extracts cleanly and is kept.

    Recovering this text needs OCR over the source PDFs, which is tracked
    separately. Until then, dropping it is the honest option and the count is
    recorded in chunk metadata as `hindi_dropped`.
    """
    return devanagari_ratio(line) >= threshold


# ── Page markers ────────────────────────────────────────────────

_PAGE_MARKER_RE = re.compile(r"^\[Page\s+(\d+)\]$")


@dataclass
class Line:
    """A source line with the page it came from."""

    text: str
    page: int  # 0 when the document has no page markers (HTML)


def parse_pages(raw_text: str) -> list[Line]:
    """
    Split raw text into page-tagged lines, consuming `[Page N]` markers.

    The marker itself is removed: it is navigation metadata, not content, and
    leaving it in the text let it be embedded and quoted back to users.
    """
    lines: list[Line] = []
    page = 0
    for raw in raw_text.split("\n"):
        cleaned = clean_line(raw)
        marker = _PAGE_MARKER_RE.match(cleaned)
        if marker:
            page = int(marker.group(1))
            continue
        lines.append(Line(text=cleaned, page=page))
    return lines


# ── Boilerplate removal ─────────────────────────────────────────

_HTML_CONTENT_MARKER = re.compile(r"^##+\s+(.*)$")
_LAST_UPDATED_RE = re.compile(r"^Last Updated on\s+.*$", re.IGNORECASE)

# PDF-rendered web pages. Bullet glyphs mark nav list items; [image] is the
# scraper's placeholder for a stripped <img>.
_PDF_NAV_RE = re.compile(
    r"^(?:\[image\]\s*)+$"
    r"|^[•○■▪]\s*$"
    r"|^(?:Hide|Show)\s+[—–-]\s*MAIN MENU$"
    r"|^MAIN MENU(?:\s+FOR\s+BUREAU\s+OF)?$"
    r"|^INDIAN STANDARDS HEADER$"
    r"|^(?:आगंतुक की गणना|विज़िटर काउंट).*$",
    re.IGNORECASE,
)

_IMAGE_TOKEN_RE = re.compile(r"\[image\]")


@dataclass
class Cleaned:
    """Result of stripping boilerplate from one document."""

    lines: list[Line]
    heading: str = ""
    nav_lines_removed: int = 0
    hindi_lines_removed: int = 0
    raw_line_count: int = 0


def strip_boilerplate(lines: list[Line], source_type: str) -> Cleaned:
    """
    Drop navigation, image placeholders and mojibaked Devanagari.

    For HTML the nav/content boundary is exact: the scraper emits a `## Heading`
    line, and everything above it is the site menu. Verified across all 10 real
    HTML documents, where the menu runs from 9 to 47 lines -- which is why a
    fixed line count would have been wrong.
    """
    raw_count = len(lines)
    heading = ""
    nav_removed = 0

    if source_type == "html":
        boundary = None
        for index, line in enumerate(lines):
            match = _HTML_CONTENT_MARKER.match(line.text)
            if match:
                boundary = index
                heading = match.group(1).strip()
                break
        if boundary is not None:
            nav_removed = boundary + 1
            lines = lines[boundary + 1 :]

    kept: list[Line] = []
    hindi_removed = 0
    for line in lines:
        text = line.text
        if not text:
            continue
        if _LAST_UPDATED_RE.match(text):
            nav_removed += 1
            continue
        if _PDF_NAV_RE.match(text):
            nav_removed += 1
            continue
        if is_devanagari_line(text):
            hindi_removed += 1
            continue
        # An inline [image] token inside a real sentence is noise, not a line.
        text = _IMAGE_TOKEN_RE.sub(" ", text).strip()
        text = re.sub(r" {2,}", " ", text)
        if not text:
            nav_removed += 1
            continue
        kept.append(Line(text=text, page=line.page))

    return Cleaned(
        lines=kept,
        heading=heading,
        nav_lines_removed=nav_removed,
        hindi_lines_removed=hindi_removed,
        raw_line_count=raw_count,
    )


# ── Table row reassembly ────────────────────────────────────────

@dataclass
class TableProfile:
    """
    How to rebuild rows for one shredded table.

    Profiles are matched on header text found IN THE DOCUMENT rather than on
    the document title, so a re-scrape that changes the filename still parses.
    """

    name: str
    header_markers: tuple[str, ...]
    columns: tuple[str, ...]
    # Some rows extract with the first two columns fused into one line, e.g.
    # "IS 15500 (Part 3) Deepwell handpumps, components and special tools".
    # Labelling that whole string as the IS number would put a product title
    # inside a standard-number field, so the designation is split back out.
    split_first_cell: re.Pattern | None = None
    # Which column absorbs line wrapping. In the products table the trailing
    # Product/Title wraps; in the laboratories table the Name of Lab wraps and
    # the trailing State/Status/OSL Code columns are short and fixed.
    wrapping_cell: str = "last"


# A cell holding only a standard number, and a continuation line that opens
# with the part designator belonging to it.
_BARE_IS_RE = re.compile(r"^IS(?:\s*/\s*(?:IEC|ISO))?\s*\d{1,5}$")
_PART_PREFIX_RE = re.compile(r"^(\(\s*Part\s*[0-9IVXivx]+\s*\))\s*(\S.*)$")

# An IS designation at the start of a cell: "IS 694", "IS 13334 (Part 1)",
# "IS/IEC 60598", "IS 1417:2016". \d{1,5} rather than \d{2,5} because IS 1 and
# IS 44 are real standards in this very table.
_IS_DESIGNATION_RE = re.compile(
    r"^(IS(?:\s*/\s*(?:IEC|ISO))?\s*\d{1,5}"
    r"(?:\s*\(\s*Part\s*[0-9IVXivx]+\s*\))?"
    r"(?:\s*[:\-]\s*\d{4})?)\s+(\S.*)$"
)

PRODUCTS_TABLE = TableProfile(
    name="products_simplified_procedure",
    header_markers=("Sr. No.", "Product/Title"),
    columns=("Sr. No.", "IS Number", "Product/Title"),
    split_first_cell=_IS_DESIGNATION_RE,
)

LABS_TABLE = TableProfile(
    name="empanelled_laboratories",
    header_markers=("Name of Lab", "OSL Code"),
    columns=("Sl. No.", "Name of Lab", "State", "Status", "OSL Code"),
    wrapping_cell="first",
)

TABLE_PROFILES = (PRODUCTS_TABLE, LABS_TABLE)

# A row's serial number may sit alone, or share its line with the next
# column(s): "1028 IS 11673 (Part 2)" and "10. Central Institute of Fisheries".
_ROW_START_RE = re.compile(r"^(\d{1,4})[.)]?(?:\s+(.*))?$")

# How far ahead of the expected serial number a match is still accepted. The
# products table genuinely skips 403, 468 and 515 in the source PDF, so a
# strict +1 rule would stall there and discard the remaining ~600 rows.
_SERIAL_LOOKAHEAD = 4


def detect_table_profile(lines: list[Line]) -> TableProfile | None:
    """Identify a shredded table from its own header cells."""
    head = " \n".join(line.text for line in lines[:80])
    for profile in TABLE_PROFILES:
        if all(marker in head for marker in profile.header_markers):
            return profile
    return None


@dataclass
class TableRow:
    serial: int
    cells: list[str]
    page: int

    def render(self, columns: tuple[str, ...]) -> str:
        """One row as a self-contained labelled line."""
        values = [str(self.serial), *self.cells]
        pairs = []
        for column, value in zip(columns, values):
            value = value.strip(" ,;")
            if value:
                pairs.append(f"{column}: {value}")
        return " | ".join(pairs)


def _merge_cell_fragments(fragments: list[str], profile: TableProfile) -> list[str]:
    """
    Collapse wrapped cell fragments down to the profile's column count.

    Long product titles and lab names wrap across lines, so a row usually
    yields more fragments than it has columns. Which end to collapse from is a
    property of the table, not a guess: collapsing from the wrong end re-fuses
    the IS number back into the title.
    """
    if profile.split_first_cell and fragments:
        match = profile.split_first_cell.match(fragments[0])
        if match:
            fragments = [match.group(1), match.group(2), *fragments[1:]]
        elif len(fragments) > 1:
            # "IS 16098" on one line and "(Part 2) Structured-wall plastics..."
            # on the next: the part designator belongs to the standard number,
            # not to the product title.
            part = _PART_PREFIX_RE.match(fragments[1])
            if part and _BARE_IS_RE.match(fragments[0]):
                fragments = [
                    f"{fragments[0]} {part.group(1)}",
                    part.group(2),
                    *fragments[2:],
                ]

    wanted = len(profile.columns) - 1  # the serial is held separately
    if len(fragments) <= wanted:
        return fragments

    if profile.wrapping_cell == "last":
        head = fragments[: wanted - 1]
        tail = " ".join(part.strip() for part in fragments[wanted - 1 :] if part.strip())
        return [*head, tail]

    keep = fragments[-(wanted - 1) :] if wanted > 1 else []
    head = " ".join(
        part.strip() for part in fragments[: len(fragments) - len(keep)] if part.strip()
    )
    return [head, *keep]


def build_table_units(rows: list[TableRow], profile: TableProfile) -> list[Unit]:
    """
    Group rows into units sized by characters, never by a fixed row count.

    A fixed rows-per-chunk count produced units that overflowed the chunk
    budget, which sent them through the sentence-based hard splitter and cut
    them mid-row -- one chunk ended "Sr. No.: 1" and the next began
    "halothrin WP". Splitting a row in half is the exact failure this rewrite
    exists to remove, so rows are accumulated up to the budget instead and a
    row boundary is the only place a table chunk can ever break.
    """
    units: list[Unit] = []
    group: list[TableRow] = []
    rendered: list[str] = []
    group_chars = 0

    def flush() -> None:
        nonlocal group, rendered, group_chars
        if not group:
            return
        units.append(
            Unit(
                text="\n".join(rendered),
                page_start=group[0].page,
                page_end=group[-1].page,
                section=f"rows {group[0].serial}-{group[-1].serial}",
                kind="table_rows",
            )
        )
        group, rendered, group_chars = [], [], 0

    for row in rows:
        line = row.render(profile.columns)
        if group and group_chars + len(line) + 1 > TABLE_UNIT_CHARS:
            flush()
        group.append(row)
        rendered.append(line)
        group_chars += len(line) + 1

    flush()
    return units


def parse_table_rows(lines: list[Line], profile: TableProfile) -> list[TableRow]:
    """
    Rebuild table rows by following the document's serial-number sequence.

    See the module docstring for why the sequence is followed instead of
    pattern-matching row prefixes.
    """
    rows: list[TableRow] = []
    expected = 1
    current: TableRow | None = None
    fragments: list[str] = []

    def flush() -> None:
        nonlocal current, fragments
        if current is not None:
            current.cells = _merge_cell_fragments(fragments, profile)
            rows.append(current)
        current, fragments = None, []

    for line in lines:
        match = _ROW_START_RE.match(line.text)
        serial = int(match.group(1)) if match else None

        if serial is not None and expected <= serial <= expected + _SERIAL_LOOKAHEAD:
            flush()
            current = TableRow(serial=serial, cells=[], page=line.page)
            remainder = (match.group(2) or "").strip()
            if remainder:
                fragments.append(remainder)
            expected = serial + 1
            continue

        if current is not None and line.text:
            fragments.append(line.text)

    flush()
    return rows


# ── Clause / section structure ──────────────────────────────────

# "1. Overview of QCO", "1.2.1 BIS certification scheme...", "3 Conformity to".
# The trailing dot is optional because the guidance document omits it on
# clause 3. A capital letter or opening bracket must follow, which keeps this
# off numeric table rows and off prose like "1 500 V d.c.".
_CLAUSE_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?\s+([A-Z(\u201c\"].*)$")

# Numbered FAQ questions: "21. Can an artisan approach the A&H centre?"
_FAQ_RE = re.compile(r"^(\d{1,3})[.)]\s+(.*\?)\s*$")


def has_clause_numbering(lines: list[Line], minimum: int = 3) -> bool:
    """
    True when a document really uses dotted clause numbering.

    Requiring several dotted numbers stops ordinary numbered lists -- like the
    "1) Scheme-I of BIS" links on the certification pages -- from being
    labelled as standards clauses.
    """
    hits = 0
    for line in lines:
        match = _CLAUSE_RE.match(line.text)
        if match and "." in match.group(1):
            hits += 1
            if hits >= minimum:
                return True
    return False


@dataclass
class Unit:
    """
    An atomic block of text that must not be split across chunks.

    A unit is one clause, one FAQ question-and-answer, one table row group, or
    one run of unlabelled prose.
    """

    text: str
    page_start: int
    page_end: int
    clause: str = ""
    section: str = ""
    kind: str = "prose"
    # Every clause the unit covers. A chunk that packs clauses 9.1 and 9.2 must
    # not be cited as "clause 9.1" alone, so the full list is carried through
    # packing rather than collapsed to the first label.
    clauses: list[str] = field(default_factory=list)


def _join_lines(lines: list[str]) -> str:
    """
    Reflow PDF lines into prose.

    PDF extraction breaks lines at the page's text width, mid-sentence and
    sometimes mid-word ("sub-" / "section"). Joining with a plain newline left
    hyphenated words split, which cost retrieval matches on terms like
    "sub-section" and "non-oriented".
    """
    out = ""
    for line in lines:
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("-") and line[:1].islower():
            out = out[:-1] + line
        else:
            out = f"{out} {line}"
    return out.strip()


def build_units(lines: list[Line], use_clauses: bool) -> list[Unit]:
    """Group cleaned lines into clause / FAQ / prose units."""
    units: list[Unit] = []
    buffer: list[str] = []
    page_start = lines[0].page if lines else 0
    page_end = page_start
    clause = ""
    section = ""
    kind = "prose"

    def flush() -> None:
        nonlocal buffer, clause, kind
        text = _join_lines(buffer)
        if text:
            units.append(
                Unit(
                    text=text,
                    page_start=page_start,
                    page_end=page_end,
                    clause=clause,
                    section=section,
                    kind=kind,
                    clauses=[clause] if clause else [],
                )
            )
        buffer = []

    for line in lines:
        faq = _FAQ_RE.match(line.text)
        clause_match = _CLAUSE_RE.match(line.text) if use_clauses else None

        if faq or clause_match:
            flush()
            page_start = line.page
            if clause_match:
                clause = clause_match.group(1)
                kind = "clause"

                # A top-level number introduces a new named section, which the
                # child clauses below it inherit as context.
                if "." not in clause:
                    section = clause_match.group(2).strip()
            else:
                clause = ""
                kind = "faq"
            buffer = [line.text]
        else:
            if not buffer:
                page_start = line.page
            buffer.append(line.text)

        page_end = line.page

    flush()
    return units


# ── Derived metadata ────────────────────────────────────────────

_IS_NUMBER_RE = re.compile(IS_NUMBER_PATTERN)

_MANDATORY_RE = re.compile(
    r"\b(?:mandatory|compulsory|shall\s+conform|shall\s+bear|"
    r"no\s+person\s+shall|prohibit(?:ion|ed)?)\b",
    re.IGNORECASE,
)
_VOLUNTARY_RE = re.compile(r"\bvoluntary\b", re.IGNORECASE)

_AMENDMENT_RE = re.compile(
    r"\b((?:First|Second|Third|Fourth|Fifth|Sixth)\s+Amendment|Amendment)\b"
    r"(?:[^.\n]{0,40}?"
    r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+,?\s+\d{4}|[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|\d{4}))?",
)

# No ICS codes appear anywhere in this corpus (checked: zero occurrences of
# "ICS"), so sector is inferred from product vocabulary instead of claimed from
# a field that does not exist. It stays empty when nothing matches.
_SECTOR_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gold_jewellery", ("hallmark", "jewellery", "jeweller", "carat", "bullion", "assaying")),
    ("electrical", ("cable", "conductor", "transformer", "switchgear", "voltage",
                    "lamp", "luminaire", "led", "electrical", "wire", "battery")),
    ("food", ("milk", "biscuit", "food", "drinking water", "mineral water",
              "infant", "edible", "sugar", "tea", "coffee")),
    ("construction", ("cement", "steel", "concrete", "plywood", "tile", "bitumen",
                      "pipe", "brick", "glass")),
    ("chemicals", ("chemical", "pesticide", "technical grade", "acid", "paint",
                   "polymer", "rubber", "explosive")),
    ("textiles", ("textile", "yarn", "fabric", "sack", "jute", "cotton", "silk")),
    ("machinery", ("machine", "pump", "valve", "wrench", "tool", "bearing",
                   "cylinder", "helmet", "tyre")),
)


def extract_is_numbers(text: str) -> list[str]:
    """Distinct IS standard references, in order of first appearance."""
    found = (re.sub(r"\s+", " ", m.group(0)).strip() for m in _IS_NUMBER_RE.finditer(text))
    return list(dict.fromkeys(found))


def detect_status(text: str) -> str:
    """
    'mandatory', 'voluntary' or '' when the text does not say.

    Left empty rather than guessed: a wrong mandatory/voluntary label is the
    single most damaging thing this system can tell a manufacturer.
    """
    mandatory = bool(_MANDATORY_RE.search(text))
    voluntary = bool(_VOLUNTARY_RE.search(text))
    if mandatory and not voluntary:
        return "mandatory"
    if voluntary and not mandatory:
        return "voluntary"
    return ""


def detect_amendment(text: str) -> str:
    """Amendment label with its date when the chunk announces one."""
    match = _AMENDMENT_RE.search(text)
    if not match:
        return ""
    label, date = match.group(1), match.group(2)
    return f"{label} {date}".strip() if date else label


def detect_sector(text: str) -> str:
    """Coarse sector label from product vocabulary, or '' when unclear."""
    lowered = text.lower()
    best, best_hits = "", 0
    for sector, keywords in _SECTOR_KEYWORDS:
        hits = sum(1 for keyword in keywords if keyword in lowered)
        if hits > best_hits:
            best, best_hits = sector, hits
    return best


# ── Chunk assembly ──────────────────────────────────────────────

@dataclass
class Chunk:
    """A chunk ready for embedding, with its ChromaDB metadata."""

    chunk_id: str
    text: str
    metadata: dict = field(default_factory=dict)


@dataclass
class DocumentResult:
    """Chunks for one document, plus why anything was dropped."""

    chunks: list[Chunk] = field(default_factory=list)
    quarantined: bool = False
    reason: str = ""
    stats: dict = field(default_factory=dict)


def _hard_split(text: str) -> list[str]:
    """
    Split an over-long unit on sentence boundaries, with overlap.

    Only reached when a single clause or prose run exceeds the chunk budget on
    its own, so overlap is applied here rather than between every chunk.
    """
    if len(text) <= MAX_CHARS:
        return [text]

    sentences = re.split(r"(?<=[.;:])\s+", text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > MAX_CHARS and current:
            parts.append(current)
            current = (current[-OVERLAP_CHARS:] + " " + sentence).strip()
        else:
            current = candidate
    if current:
        parts.append(current)

    # A single sentence longer than the budget still has to be cut somewhere.
    final: list[str] = []
    for part in parts:
        while len(part) > MAX_CHARS:
            final.append(part[:MAX_CHARS])
            part = part[MAX_CHARS - OVERLAP_CHARS :]
        if part:
            final.append(part)
    return final


def _clause_label(clauses: list[str]) -> str:
    """Human-readable clause span for a chunk that may cover several."""
    if not clauses:
        return ""
    if len(clauses) == 1:
        return f"Clause {clauses[0]}"
    return f"Clauses {clauses[0]}\u2013{clauses[-1]}"


def _context_header(title: str, section: str, clause_label: str) -> str:
    """
    Short provenance line prefixed to each chunk before embedding.

    A chunk that reads "9.1 The latest version of Indian Standards ... shall
    apply" is meaningless in isolation, and its embedding is correspondingly
    vague. Naming the document and section restores the context the splitter
    would otherwise have thrown away, and it is also what the LLM needs in
    order to cite accurately.
    """
    parts = [title]
    if section and section.lower() not in title.lower():
        parts.append(section)
    if clause_label:
        parts.append(clause_label)
    return " — ".join(parts)


def _normalise_for_dedupe(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def chunk_document(doc: dict, doc_index: int) -> DocumentResult:
    """
    Turn one scraped document into chunks with structural metadata.

    Returns a quarantined result -- never a partial one -- when the document
    turns out to be a scrape failure rather than content.
    """
    raw_text = doc.get("raw_text", "") or ""
    title = doc.get("title", "") or ""
    source_type = (doc.get("source_type") or "").lower()
    category = doc.get("category", "") or ""
    source_url = doc.get("source_url", "") or ""

    lines = parse_pages(raw_text)
    cleaned = strip_boilerplate(lines, source_type)
    content_chars = sum(len(line.text) for line in cleaned.lines)
    raw_chars = len(raw_text)
    boilerplate_ratio = 1.0 - (content_chars / raw_chars) if raw_chars else 1.0

    stats = {
        "raw_chars": raw_chars,
        "content_chars": content_chars,
        "boilerplate_ratio": round(boilerplate_ratio, 3),
        "nav_lines_removed": cleaned.nav_lines_removed,
        "hindi_lines_removed": cleaned.hindi_lines_removed,
        "pages": max((line.page for line in lines), default=0),
    }

    if content_chars < MIN_DOC_CHARS:
        return DocumentResult(
            quarantined=True,
            reason=(
                f"only {content_chars} chars of content survive boilerplate "
                f"removal (minimum {MIN_DOC_CHARS})"
            ),
            stats=stats,
        )

    if boilerplate_ratio > MAX_BOILERPLATE_RATIO:
        return DocumentResult(
            quarantined=True,
            reason=(
                f"{boilerplate_ratio:.0%} of the document is navigation or "
                f"unreadable text, which means the scrape returned a site "
                f"page rather than the intended document"
            ),
            stats=stats,
        )

    # ── Units ───────────────────────────────────────────────────
    profile = detect_table_profile(cleaned.lines)
    if profile:
        rows = parse_table_rows(cleaned.lines, profile)
        stats["table_profile"] = profile.name
        stats["table_rows"] = len(rows)
        units = build_table_units(rows, profile)
    else:
        use_clauses = has_clause_numbering(cleaned.lines)
        stats["clause_numbering"] = use_clauses
        units = build_units(cleaned.lines, use_clauses=use_clauses)

    # ── Pack units into chunks ──────────────────────────────────
    chunks: list[Chunk] = []
    seen: set[str] = set()
    duplicates = 0

    def emit(unit: Unit, text: str) -> None:
        nonlocal duplicates
        key = hashlib.sha1(_normalise_for_dedupe(text).encode()).hexdigest()
        if key in seen:
            duplicates += 1
            return
        seen.add(key)

        clauses = unit.clauses or ([unit.clause] if unit.clause else [])
        header = _context_header(title, unit.section, _clause_label(clauses))
        body = f"{header}\n\n{text}" if header else text
        is_numbers = extract_is_numbers(text)
        clause_top = clauses[0].split(".")[0] if clauses else ""
        index = len(chunks)

        chunks.append(
            Chunk(
                chunk_id=f"doc{doc_index:03d}_chunk{index:04d}",
                text=body,
                metadata={
                    # Provenance
                    "source_url": source_url,
                    "title": title,
                    "category": category,
                    "source_type": source_type,
                    "doc_index": doc_index,
                    "chunk_index": index,
                    # Citation targets
                    "page_start": unit.page_start,
                    "page_end": unit.page_end,
                    # `clause` is the chunk's primary anchor; `clauses` is
                    # every clause it actually covers, so a chunk holding 9.1
                    # and 9.2 is never cited as 9.1 alone.
                    "clause": clauses[0] if clauses else "",
                    "clauses": ("|" + "|".join(clauses) + "|") if clauses else "",
                    "clause_count": len(clauses),
                    "clause_top": clause_top,
                    "section": unit.section,
                    # Parent-child link. Children of a clause share the parent
                    # id, so a retrieved sub-clause can be expanded to its
                    # whole section without another search.
                    "parent_id": (
                        f"doc{doc_index:03d}_sec{clause_top}"
                        if clause_top
                        else f"doc{doc_index:03d}_sec_root"
                    ),
                    # Filterable facets. Chroma metadata values must be
                    # scalars, so the IS list is stored pipe-delimited with
                    # leading and trailing pipes for unambiguous matching.
                    "is_numbers": ("|" + "|".join(is_numbers) + "|") if is_numbers else "",
                    "is_number_count": len(is_numbers),
                    "status": detect_status(text),
                    "amendment": detect_amendment(text),
                    "sector": detect_sector(text),
                    "content_kind": unit.kind,
                    "char_count": len(body),
                },
            )
        )

    pending: list[Unit] = []
    pending_len = 0

    def flush_pending() -> None:
        nonlocal pending, pending_len
        if not pending:
            return
        merged = Unit(
            text="\n\n".join(unit.text for unit in pending),
            page_start=pending[0].page_start,
            page_end=pending[-1].page_end,
            clause=pending[0].clause,
            section=pending[0].section,
            kind=pending[0].kind,
            clauses=[c for unit in pending for c in unit.clauses],
        )
        emit(merged, merged.text)
        pending, pending_len = [], 0

    for unit in units:
        # Table units are already sized and carry their own row-range label, so
        # merging two of them would leave the chunk claiming the first one's
        # row range while holding both.
        if unit.kind == "table_rows":
            flush_pending()
            emit(unit, unit.text)
            continue

        if len(unit.text) > MAX_CHARS:
            flush_pending()
            for piece in _hard_split(unit.text):
                emit(unit, piece)
            continue

        # Never pack across a top-level section boundary. A chunk that held
        # clauses 3.1 through 8 was labelled "Conformity to Indian Standard"
        # from its first unit while actually covering three further sections,
        # which is exactly the mislabelled citation this rewrite has to avoid.
        starts_new_section = unit.clause and "." not in unit.clause

        # Otherwise keep a section's clauses together while they fit, so
        # "4. Date of commencement" and its 4.1 body stay in one chunk.
        if pending and (
            starts_new_section or pending_len + len(unit.text) + 2 > MAX_CHARS
        ):
            flush_pending()
        pending.append(unit)
        pending_len += len(unit.text) + 2

    flush_pending()

    stats["units"] = len(units)
    stats["duplicates_dropped"] = duplicates
    stats["chunks"] = len(chunks)

    return DocumentResult(chunks=chunks, stats=stats)


def chunk_corpus(documents: list[dict]) -> tuple[list[Chunk], list[dict]]:
    """
    Chunk every document, returning chunks and a per-document report.

    The report is returned rather than only logged so the ingest script and the
    audit script can both show exactly what was dropped and why. A silent drop
    is how the previous data corruption stayed invisible.
    """
    all_chunks: list[Chunk] = []
    report: list[dict] = []

    for doc_index, doc in enumerate(documents):
        result = chunk_document(doc, doc_index)
        entry = {
            "doc_index": doc_index,
            "title": doc.get("title", ""),
            "source_type": doc.get("source_type", ""),
            "category": doc.get("category", ""),
            "quarantined": result.quarantined,
            "reason": result.reason,
            "chunks": len(result.chunks),
            **result.stats,
        }
        report.append(entry)

        if result.quarantined:
            log.warning(
                "QUARANTINED %r: %s", doc.get("title", ""), result.reason
            )
            continue

        all_chunks.extend(result.chunks)

    return all_chunks, report
