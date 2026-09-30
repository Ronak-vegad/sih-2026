"""
Data-quality gate for the structured QCO table.

WHY THIS EXISTS
---------------
The QCO table is the highest-trust path in the whole system: `main.py` lets a
structured hit bypass the confidence gate, `generation.py` labels those rows
"authoritative" to the model and cites them at similarity 1.0, and the
IS-number guardrail treats their values as verified context. A bad row is
therefore not merely noise — it is laundered into a confident, cited answer
that no downstream check can catch.

An audit of the shipped table found 2,139 of 2,208 rows (96.9%) unusable:

  * 1,063 rows whose `product_name` was a bare table serial number ("10", "11")
  * 978 rows with <= 3 real characters
  * 2,112 rows carrying invisible U+202D / U+202C bidi control characters
  * wrapped-table-cell fragments as product names: "purposes", "radial ply",
    "dimensions", "applications"
  * scraped navigation strings and image placeholders: "[image]",
    "क्या नया है", "लाइसेंस प्रदान करना।[image]"

`rebuild_qco.py` already carried a garbage filter intended to remove these, but
it silently deleted nothing: its `_NUMERIC_ONLY` pattern is anchored
(`^[\\d\\s.,;:()/-]{1,12}$`), and every one of those names is wrapped in
U+202D ... U+202C, so the anchors never matched. Normalisation therefore has to
happen BEFORE validation, which is the order this module enforces.

THE SEPARATE, WORSE PROBLEM: STATUS PROVENANCE
----------------------------------------------
2,139 of those rows came from `List-of-Products-Under-Simplified-Procedure.pdf`
and were stamped `mandatory_or_voluntary = "Mandatory"` and
`scheme_type = "ISI Mark (Scheme I)"` as hardcoded parser defaults.

The Simplified Procedure list enumerates products eligible for a *faster
certification route*. It establishes nothing whatsoever about whether a product
is under a mandatory Quality Control Order. So the system was asserting
mandatory legal status for ~2,139 products on the authority of a document that
confers no such status.

`status_basis()` below fixes that at the root: mandatory/voluntary is reported
ONLY when the citing document is genuinely status-establishing (a QCO, a CRS
order, or the compulsory-certification list). Otherwise the status is returned
as UNKNOWN and the answer layer must say so explicitly rather than guess.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

# ── Invisible characters that defeat naive validation ────────────
# U+202A-U+202E  bidi embedding/override  (the actual culprits: 202D/202C)
# U+2066-U+2069  bidi isolates
# U+200E,U+200F  LTR/RTL marks
# U+200B-U+200D  zero-width space/non-joiner/joiner
# U+FEFF         BOM / zero-width no-break space
# U+00AD         soft hyphen
_INVISIBLE_RE = re.compile(
    "[\u202a-\u202e\u2066-\u2069\u200e\u200f\u200b-\u200d\ufeff\u00ad]"
)

# PDF/HTML extraction artefacts that are never part of a product name.
_ARTEFACT_RE = re.compile(
    r"\[\s*(?:image|img|photo|picture|figure|table|logo)\s*\]", re.I
)

# U+FFFD REPLACEMENT CHARACTER — a mis-decoded byte. Seen in the shipped table
# as "Electric Domestic Appliances <?> Particular Requirements", where the
# original character was an en dash.
_REPLACEMENT_CHAR = "\ufffd"


def normalize(value: object) -> str:
    """
    Canonicalise a raw extracted field so validation can actually see it.

    Order matters: invisible characters are removed BEFORE anything else,
    because leaving them in is precisely what let 2,112 junk rows slip past the
    previous anchored-regex filter.
    """
    if value is None:
        return ""

    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE_RE.sub("", text)
    text = _ARTEFACT_RE.sub(" ", text)
    text = text.replace(_REPLACEMENT_CHAR, " ")
    # Collapse all whitespace (incl. non-breaking) into single spaces.
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n-–—|·•,;:")


# ── Junk product-name detection ──────────────────────────────────

# A bare table serial number, page marker, or punctuation-only cell.
_NUMERIC_ONLY_RE = re.compile(r"^[\d\s.,;:()/\[\]#*+-]*$")

# Table headers captured as if they were data. Header cells are frequently
# slash- or pipe-joined ("Product/Title", "Product | Standard No."), so the
# whole string is treated as a header when every segment is a header word.
_HEADER_WORD = (
    r"s\.?\s*no\.?|sr\.?\s*no\.?|serial(?:\s*no\.?)?|sl\.?\s*no\.?|"
    r"product(?:\s*name)?|standard(?:\s*(?:no\.?|number|title))?|title|"
    r"is\s*(?:no\.?|number)|scheme(?:\s*type)?|status|remarks?|"
    r"mandatory(?:\s*/?\s*voluntary)?|compulsory|category|item|"
    r"particulars?|description|sector|page|annexure?|contents?"
)
_HEADER_RE = re.compile(
    rf"^(?:{_HEADER_WORD})(?:\s*[/|,&+-]\s*(?:{_HEADER_WORD}))*$", re.I
)

# Pagination / UI chrome that the HTML extractor picked up as a table cell,
# e.g. "Next Prev".
_PAGINATION_RE = re.compile(
    r"^(?:next|prev(?:ious)?|first|last|more|back|top)"
    r"(?:\s+(?:next|prev(?:ious)?|first|last|more|back|top))*$",
    re.I,
)

# Notice / circular headlines, not products. The shipped table contained
# 'Grant of All India First Licence for "Cylinder Cartridge"' as a product.
_NOTICE_RE = re.compile(
    r"^(?:grant(?:ing)?|issue|issuance|withdrawal|cancellation|suspension|"
    r"amendment|revision|inclusion|deletion|extension|renewal|transfer)\s+"
    r"(?:of|to|for)\b",
    re.I,
)

# Wrapped-table-cell tail fragments. These are real English words, so they pass
# a generic "has letters" test — which is exactly how they became product names.
# They are only ever meaningful as the tail of a longer cell.
_FRAGMENT_RE = re.compile(
    r"^(?:purposes?|dimensions?|applications?|requirements?|specifications?|"
    r"general|others?|etc\.?|type|types|grade|grades|parts?|"
    r"radial\s+ply|and\s+.*|or\s+.*|for\s+.*|of\s+.*|with\s+.*|"
    r"thereof|above|below|contd\.?|continued)$",
    re.I,
)

# Scraped website navigation / UI chrome, English and Hindi.
_NAV_RE = re.compile(
    r"^(?:home|about|about\s+us|contact(?:\s+us)?|sitemap|search|login|"
    r"read\s+more|click\s+here|back|next|previous|menu|skip\s+to\s+content|"
    r"what'?s\s+new|downloads?|tenders?|careers?|feedback|disclaimer|"
    r"privacy\s+policy|terms|faqs?|"
    r"क्या\s*नया\s*है|होम|हमारे\s*बारे\s*में|संपर्क|खोज|मुख्य\s*पृष्ठ|"
    r"डाउनलोड|निविदा|प्रतिक्रिया)$",
    re.I,
)

# A product name that is nothing but an IS reference (the number leaked into
# the name column).
_IS_ONLY_RE = re.compile(
    r"^IS(?:\s*/\s*(?:IEC|ISO))?\s*\d{2,5}"
    r"(?:\s*\(\s*Part\s*[0-9IVXivx]+\s*\))?"
    r"(?:\s*[:\-]\s*\d{4})?$",
    re.I,
)

MIN_NAME_CHARS = 4


def product_name_issue(raw_name: object) -> str | None:
    """
    Return a short reason string if this product name is unusable, else None.

    Always operates on the NORMALISED name, so invisible characters cannot
    smuggle junk past the checks.
    """
    name = normalize(raw_name)

    if not name:
        return "empty_after_normalisation"

    # Count only real word characters — "( 10 )" has 4 chars but no content.
    letters = re.findall(r"[^\W\d_]", name, flags=re.UNICODE)
    if _NUMERIC_ONLY_RE.match(name):
        return "numeric_or_punctuation_only"
    if len(name) < MIN_NAME_CHARS:
        return "too_short"
    if not letters:
        return "no_alphabetic_content"
    if _HEADER_RE.match(name):
        return "table_header"
    if _NAV_RE.match(name):
        return "website_navigation"
    if _IS_ONLY_RE.match(name):
        return "is_number_in_name_column"
    if _FRAGMENT_RE.match(name):
        return "wrapped_cell_fragment"
    if _PAGINATION_RE.match(name):
        return "pagination_chrome"
    if _NOTICE_RE.match(name):
        return "notice_headline"
    # A product name is a noun phrase, not a sentence. This targets scraped
    # prose such as 'लाइसेंस प्रदान करना।' ("to grant licence").
    #
    # A trailing '.' is deliberately NOT treated as prose: real product names
    # end in technical abbreviations. Rejecting on '.' threw away
    # 'Propiconazole E.c.' (emulsifiable concentrate) and 'Electric Cables for
    # Photovoltaic Systems for Rated Voltage 1 500 V d.c.'.
    if name.endswith(("।", "?", "!")):
        return "sentence_not_product_name"
    # Needs at least one alphabetic run of 3+ chars to be a plausible name.
    if not re.search(r"[^\W\d_]{3,}", name, flags=re.UNICODE):
        return "no_word_of_three_letters"

    return None


# ── Status provenance policy ─────────────────────────────────────

StatusBasis = Literal["established", "unknown"]

# Documents that genuinely establish mandatory/voluntary status.
_STATUS_ESTABLISHING_RE = re.compile(
    r"quality\s*control\s*order|\bqco\b|"
    r"compulsory\s*registration(?:\s*scheme)?|\bcrs\b\s*order|"
    r"products?\s*under\s*compulsory\s*certification|"
    r"compulsory\s*certification|"
    # The Hallmarking Regulation is a statutory instrument that does establish
    # mandatory hallmarking, so it counts alongside the Hallmarking QCO.
    r"hallmarking\s*(?:quality\s*control\s*)?(?:order|regulation)",
    re.I,
)

# Documents that describe a PROCEDURE or a facility list, and therefore say
# nothing about legal status — however tempting their product tables look.
_NON_STATUS_RE = re.compile(
    r"simplified\s*procedure|"
    r"empanelled|empanelment|laborator(?:y|ies)|"
    r"recognized\s*labs?|test(?:ing)?\s*facilit",
    re.I,
)


def status_basis(row: dict) -> StatusBasis:
    """
    Decide whether this row's mandatory/voluntary value may be reported at all.

    A status claim is only as good as the document behind it. The Simplified
    Procedure list is the motivating case: it lists products eligible for a
    faster certification route and is silent on whether any of them is legally
    mandatory, yet it supplied 96.9% of the shipped table's "Mandatory" values.
    """
    provenance = " ".join(
        normalize(row.get(key))
        for key in ("qco_reference", "source_url", "standard_title")
    )

    # Checked first: an explicitly non-status source cannot be rescued by a
    # coincidental keyword elsewhere in its title or URL.
    if _NON_STATUS_RE.search(provenance):
        return "unknown"
    if _STATUS_ESTABLISHING_RE.search(provenance):
        return "established"
    return "unknown"


STATUS_UNKNOWN = "Not established by the cited source"


# ── Extraction trust tiers ───────────────────────────────────────
#
# Name validation alone is provably insufficient. The shipped table contained
#
#     product_name       = 'Conduits for electrical installations: Part 3'
#     is_standard_number = 'IS 694'
#
# which passes every name check above, yet the pairing is wrong: IS 694 is PVC
# insulated cables, while conduits are IS 9537. The row is junk in a way no
# amount of string validation can detect, because the NAME is fine and the
# NUMBER is fine -- only their association is false.
#
# That association was produced by `bis_supplement.parse_qco_rows`, which finds
# an IS number on a line and then scans up to 8 lines backwards for any line
# containing a 3-letter word, calling that the product. On a multi-column PDF
# table with wrapped cells, that heuristic mis-pairs routinely.
#
# So trust is assigned by HOW a row was extracted, not by how clean it looks:
#
#   curated       hand-written in bis_supplement.SEED_QCO_ROWS; a human made
#                 the product<->standard association. Trusted if it also
#                 passes name validation.
#   table_parse   produced by scripts/parse_qco_tables.py, which reads real
#                 table geometry so a row's cells genuinely belong together.
#                 Trusted.
#   text_proximity  produced by the line-scanning parser. QUARANTINED: never
#                 served, regardless of how plausible the row looks.
#
# Rows with no recorded method are treated as text_proximity, because every
# row in the pre-fix database came from that parser.

TRUSTED_METHODS = frozenset({"curated", "table_parse"})
QUARANTINED_METHODS = frozenset({"text_proximity", "unknown"})


def extraction_method(row: dict) -> str:
    """Recorded extraction method for a row, defaulting to the untrusted one."""
    method = normalize(row.get("extraction_method")).lower()
    return method if method else "unknown"


# ── Row verdict ──────────────────────────────────────────────────

@dataclass
class RowVerdict:
    """Outcome of validating one QCO row."""

    ok: bool
    row: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return ",".join(self.reasons) or "ok"


def validate_row(raw: dict) -> RowVerdict:
    """
    Normalise and vet a single QCO row.

    On success `verdict.row` is the cleaned row, with two fields added:

      status_basis  'established' | 'unknown'
      status_note   human-readable caveat when the status is not established

    When the status is not established, `mandatory_or_voluntary` is REPLACED
    with STATUS_UNKNOWN rather than passed through. The generation layer never
    sees the unsupported claim, so it cannot repeat it.
    """
    reasons: list[str] = []

    # Primary gate: an untrusted extraction method is disqualifying on its own.
    # Checked first because a mis-paired row can look perfectly clean.
    method = extraction_method(raw)
    if method not in TRUSTED_METHODS:
        reasons.append(f"untrusted_extraction:{method}")

    issue = product_name_issue(raw.get("product_name"))
    if issue:
        reasons.append(issue)

    is_number = normalize(raw.get("is_standard_number"))
    if not is_number:
        reasons.append("missing_is_number")
    elif not re.search(r"\d", is_number):
        # One digit is enough: 'IS 1' is The National Flag of India. Requiring
        # two digits here silently discarded it.
        reasons.append("is_number_has_no_digits")

    if reasons:
        return RowVerdict(ok=False, reasons=reasons)

    row = {key: normalize(value) for key, value in raw.items()}
    row["id"] = raw.get("id")
    row["extraction_method"] = method

    basis = status_basis(row)
    row["status_basis"] = basis
    if basis == "established":
        row["status_note"] = ""
    else:
        source = row.get("qco_reference") or "the cited document"
        row["mandatory_or_voluntary"] = STATUS_UNKNOWN
        row["scheme_type"] = ""
        row["status_note"] = (
            f"'{source}' does not establish whether this product is under a "
            f"mandatory Quality Control Order. Confirm on bis.gov.in."
        )

    return RowVerdict(ok=True, row=row)


def validate_rows(raw_rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """
    Validate a batch of rows.

    Returns (clean_rows, rejection_counts_by_reason). The counts are logged at
    startup and exposed on /health so silent corpus rot is visible rather than
    something you discover from a user complaint.
    """
    clean: list[dict] = []
    rejected: dict[str, int] = {}

    for raw in raw_rows:
        verdict = validate_row(raw)
        if verdict.ok:
            clean.append(verdict.row)
        else:
            for reason in verdict.reasons:
                rejected[reason] = rejected.get(reason, 0) + 1

    return clean, rejected
