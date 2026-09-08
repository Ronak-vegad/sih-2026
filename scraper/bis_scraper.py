"""
BIS Intelligent Assistant — Phase 1: Data Collection Pipeline
=============================================================
Scrapes HTML/FAQ pages and downloads/extracts BIS PDFs.
Outputs:
  data/raw_documents.json    — unified JSON per schema
  data/qco_structured.csv    — structured QCO product-standard table
  data/qco_structured.db    — SQLite for deterministic lookups
  data/failed_urls.log       — any URL that failed after retries
  data/scraper.log           — full run log

Fixes applied vs v1:
  - data/ dir created BEFORE logging.basicConfig
  - pymupdf import (no deprecation warning)
  - Console print uses sys.stdout with UTF-8 reconfiguration (Windows cp1252 fix)
  - Playwright timeout raised to 60 s + waits for DOM content
  - Playwright-based PDF download for 403-blocked URLs (browser session bypass)
  - QCO link discovery also tries Playwright (JS-rendered page)
  - Wider QCO keyword filter + no-filter fallback logs all PDF links found
"""

import json
import csv
import sqlite3
import re
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Ensure data/ exists BEFORE logging.basicConfig ──────────────
Path("data").mkdir(exist_ok=True)

# ── Reconfigure stdout to UTF-8 so print() works on Windows ─────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup
import pymupdf as fitz  # PyMuPDF (new canonical import)

# ─────────────────────────── Logging ────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bis_scraper")

# ─────────────────────────── Config ─────────────────────────────
DATA_DIR = Path("data")
(DATA_DIR / "pdfs").mkdir(exist_ok=True)

FAILED_LOG = DATA_DIR / "failed_urls.log"
RAW_JSON   = DATA_DIR / "raw_documents.json"
QCO_CSV    = DATA_DIR / "qco_structured.csv"
QCO_DB     = DATA_DIR / "qco_structured.db"

REQUEST_DELAY    = 1.5   # seconds between requests
MAX_RETRIES      = 3
TIMEOUT_HTTP     = 30
TIMEOUT_PLAYWRIGHT = 60_000   # ms

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.bis.gov.in/",
}

# ─────────────── Target HTML pages ───────────────────────────────
HTML_TARGETS = [
    {
        "url":      "https://www.bis.gov.in/hallmarking-overview/hallmarking-faqs/hallmarking-faq/?lang=en",
        "title":    "Hallmarking FAQ",
        "category": "hallmarking",
    },
    {
        "url":      "https://www.bis.gov.in/hallmarking-overview/hallmarking-faqs/common-consumer-faq/?lang=en",
        "title":    "Hallmarking Common Consumer FAQ",
        "category": "hallmarking",
    },
    {
        "url":      "https://www.bis.gov.in/index.php/consumer-overview/consumer-overviews/for-consumers-faq",
        "title":    "For Consumers FAQ",
        "category": "faq",
        "playwright_first": True,   # known JS-rendered page
    },
    {
        "url":      "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/?lang=en",
        "title":    "Products Under Compulsory Certification",
        "category": "certification",
    },
]

# ─────────────── Direct PDF downloads ────────────────────────────
PDF_TARGETS = [
    {
        "url":      "https://bis.gov.in/PDF/pdf/rti/operating_manual.pdf",
        "title":    "BIS Operating Manual (RTI)",
        "category": "certification",
        "try_playwright": True,   # 403 on bare requests; try browser session
    },
    {
        "url":      "https://www.bis.gov.in/wp-content/uploads/2021/07/Guidance-document-on-QCOs-Revised-1.pdf",
        "title":    "Guidance Document on QCOs (Revised)",
        "category": "qco",
    },
]

# QCO index page
QCO_INDEX_URL = "https://www.bis.gov.in/branch_whatsnew/guidance-document-on-quality-control-orders-qcos/?lang=en"

# Keyword filter — any PDF whose link text OR URL contains one of these is included
QCO_CATEGORIES_FILTER = {
    "electrical", "appliance", "footwear", "toy", "toys",
    "led", "cement", "lamp", "light", "lighting",
    "cable", "wire", "fan", "pump", "motor", "helmet",
    "pressure", "cooker", "switch", "plug", "socket",
    "transformer", "inverter", "battery", "charger",
}

MAX_QCO_PDFS = 20


# ═══════════════════════════════════════════════════════════════
#  HTTP helpers
# ═══════════════════════════════════════════════════════════════

def _log_failure(url: str, reason: str) -> None:
    with open(FAILED_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()}  FAILED  {url}  {reason}\n")
    log.error("FAILED: %s — %s", url, reason)


def fetch_html(url: str) -> Optional[str]:
    """Fetch raw HTML with retries. Returns None on failure."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("GET (attempt %d) %s", attempt, url)
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_HTTP)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            log.warning("Attempt %d failed: %s", attempt, exc)
            if attempt < MAX_RETRIES:
                time.sleep(REQUEST_DELAY * attempt)
    _log_failure(url, "Max retries exceeded (HTML)")
    return None


def fetch_pdf_bytes(url: str) -> Optional[bytes]:
    """Download a PDF with retries. Returns raw bytes or None."""
    pdf_headers = {**HEADERS, "Accept": "application/pdf,*/*;q=0.8"}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info("PDF download (attempt %d) %s", attempt, url)
            resp = requests.get(url, headers=pdf_headers, timeout=60, stream=True)
            resp.raise_for_status()
            data = b"".join(resp.iter_content(chunk_size=65536))
            if len(data) < 512:
                raise ValueError(f"Suspiciously small PDF ({len(data)} bytes)")
            return data
        except (requests.RequestException, ValueError) as exc:
            log.warning("Attempt %d failed: %s", attempt, exc)
            if attempt < MAX_RETRIES:
                time.sleep(REQUEST_DELAY * attempt)
    _log_failure(url, "Max retries exceeded (PDF)")
    return None


def playwright_fetch_html(url: str, wait_selector: Optional[str] = None) -> Optional[str]:
    """
    JS-rendered HTML fetch via Playwright.
    wait_selector: CSS selector to wait for before capturing content.
    Timeout is 60 s to handle slow BIS servers.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        log.info("Playwright HTML fetch for %s", url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(extra_http_headers={
                "User-Agent": HEADERS["User-Agent"],
                "Referer":    HEADERS["Referer"],
            })
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_PLAYWRIGHT)
                # Give JS a moment to render dynamic content
                page.wait_for_timeout(3000)
                if wait_selector:
                    try:
                        page.wait_for_selector(wait_selector, timeout=10000)
                    except PWTimeout:
                        log.warning("wait_selector '%s' not found — capturing anyway", wait_selector)
            except PWTimeout:
                log.warning("Page load timed out — capturing partial content")
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        _log_failure(url, f"Playwright HTML error: {exc}")
        return None


def playwright_fetch_pdf(url: str) -> Optional[bytes]:
    """
    Download a PDF via Playwright (bypasses cookie/Referer checks that block requests).
    Navigates to the URL and captures the response body bytes.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        log.info("Playwright PDF fetch for %s", url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(extra_http_headers={
                "User-Agent": HEADERS["User-Agent"],
                "Referer":    HEADERS["Referer"],
            })
            page = ctx.new_page()
            pdf_bytes: Optional[bytes] = None

            def handle_response(response):
                nonlocal pdf_bytes
                if response.url == url and response.status == 200:
                    ct = response.headers.get("content-type", "")
                    if "pdf" in ct or url.endswith(".pdf"):
                        try:
                            pdf_bytes = response.body()
                        except Exception:
                            pass

            page.on("response", handle_response)
            try:
                page.goto(url, wait_until="networkidle", timeout=TIMEOUT_PLAYWRIGHT)
            except PWTimeout:
                pass
            browser.close()

            if pdf_bytes and len(pdf_bytes) > 512:
                log.info("  Playwright PDF captured: %d bytes", len(pdf_bytes))
                return pdf_bytes
            log.warning("  Playwright PDF response not captured for %s", url)
            return None
    except Exception as exc:
        _log_failure(url, f"Playwright PDF error: {exc}")
        return None


# ═══════════════════════════════════════════════════════════════
#  Text extraction helpers
# ═══════════════════════════════════════════════════════════════

def extract_text_from_html(html: str) -> str:
    """
    Parse HTML and extract meaningful text, skipping nav/footer boilerplate.
    Returns cleaned plain text.
    """
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "header", "footer",
                     "nav", "aside", "form", "iframe", "svg"]):
        tag.decompose()

    # Prefer semantic main content containers
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", class_=re.compile(r"entry-content|post-content|page-content|faq|content-area", re.I))
        or soup.find("div", id=re.compile(r"content|main|faq|primary", re.I))
        or soup.body
    )

    if not main:
        return soup.get_text(separator="\n", strip=True)

    lines = []
    for elem in main.descendants:
        if not hasattr(elem, "name"):
            continue
        if elem.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = elem.get_text(strip=True)
            if text:
                lines.append(f"\n## {text}\n")
        elif elem.name in ("p", "li", "td", "th", "dt", "dd", "span"):
            text = elem.get_text(separator=" ", strip=True)
            if text and len(text) > 10:
                lines.append(text)

    raw = "\n".join(lines)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using PyMuPDF."""
    text_pages = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        log.info("  PDF pages: %d", doc.page_count)
        for page_num, page in enumerate(doc, start=1):
            text = page.get_text("text")
            if text.strip():
                text_pages.append(f"[Page {page_num}]\n{text.strip()}")
    return "\n\n".join(text_pages)


# ═══════════════════════════════════════════════════════════════
#  QCO link discovery
# ═══════════════════════════════════════════════════════════════

def discover_qco_pdf_links(index_url: str) -> list[dict]:
    """
    Scrape the QCO index page (tries both requests + Playwright) and return
    a list of {url, title, category} dicts for matching PDFs.
    Also logs ALL PDF links found (so you can review what was skipped).
    """
    # Try requests first, then Playwright for JS-rendered content
    html = fetch_html(index_url)
    text_len = len(extract_text_from_html(html)) if html else 0
    if not html or text_len < 200:
        log.info("Short content (%d chars) — trying Playwright for QCO index page", text_len)
        html_pw = playwright_fetch_html(index_url)
        if html_pw and len(extract_text_from_html(html_pw)) > text_len:
            html = html_pw

    if not html:
        log.error("Could not fetch QCO index page at all.")
        return []

    soup = BeautifulSoup(html, "lxml")
    all_pdf_links = []
    filtered_links = []
    seen_urls: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = a.get_text(strip=True)

        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://www.bis.gov.in" + href

        if not href.lower().endswith(".pdf"):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        entry = {
            "url":      href,
            "title":    text or Path(href).stem.replace("-", " ").replace("_", " ").title(),
            "category": "qco",
        }
        all_pdf_links.append(entry)

        combined = (text + " " + href).lower()
        if any(kw in combined for kw in QCO_CATEGORIES_FILTER):
            filtered_links.append(entry)

    log.info("QCO index — total PDFs found: %d, filtered to target categories: %d",
             len(all_pdf_links), len(filtered_links))

    if all_pdf_links:
        log.info("All PDF links on QCO index page:")
        for e in all_pdf_links:
            marker = "[✓]" if e in filtered_links else "[ ]"
            log.info("  %s %s  |  %s", marker, e["title"][:60], e["url"][:80])

    return filtered_links[:MAX_QCO_PDFS]


# ═══════════════════════════════════════════════════════════════
#  QCO structured data extractor
# ═══════════════════════════════════════════════════════════════

_IS_NUM_RE    = re.compile(r"\bIS\s*[\d]+(?::\d{4})?(?:\s*\(Part\s*[\d]+\))?(?:\s*/\s*IEC\s*[\d:]+)?", re.I)
_PENALTY_RE   = re.compile(r"(?:penalty|fine|imprisonment|punish[^.]{0,100})", re.I)
_SCHEME_RE    = re.compile(r"(?:Scheme\s+[IVXLCDM]+|ISI\s*Mark|Self[\s-]Declaration|CRS|Compulsory\s+Registration)", re.I)
_MANDATORY_RE = re.compile(r"\b(mandatory|compulsory|voluntary|optional)\b", re.I)
# Detects lines that are just a number / serial (e.g. "984", "S.No", "1.")
_NUMERIC_LINE_RE = re.compile(r"^[\d\s.,;:()/-]{1,10}$")
_HEADER_SKIP_RE  = re.compile(
    r"^(?:s\.?\s*no\.?|sr\.?|serial|sl\.?|product|standard|title|"
    r"is\s*no\.?|is\s*number|scheme|status|mandatory|compulsory|"
    r"category|item|particulars|description)\s*$",
    re.I,
)


def _valid_product_name(text: str) -> bool:
    """Return True if text looks like a real product name."""
    text = text.strip()
    if not text or len(text) < 5:
        return False
    # Purely numeric / row-number line
    if _NUMERIC_LINE_RE.match(text):
        return False
    # Table-header keywords
    if _HEADER_SKIP_RE.match(text):
        return False
    # The line itself is just an IS reference
    if _IS_NUM_RE.fullmatch(text.strip()):
        return False
    # Must contain at least one alphabetic word of length ≥ 3
    words = re.findall(r"[A-Za-z]{3,}", text)
    return bool(words)


def parse_qco_structured(text: str, source_url: str, title: str) -> list[dict]:
    """
    Extract structured QCO rows from PDF text.

    Improvements over v1:
    - Skips purely numeric / header lines as product names.
    - Scans up to 8 lines back and 3 lines forward for a usable product name.
    - Falls back to the document title when no name is found in context.
    """
    rows = []
    lines = text.splitlines()

    for i, line in enumerate(lines):
        standards = _IS_NUM_RE.findall(line)
        if not standards:
            continue

        ctx_start = max(0, i - 6)
        ctx_end   = min(len(lines), i + 7)
        context   = " ".join(lines[ctx_start:ctx_end])

        # ── Product name: scan up to 8 lines back ─────────────
        product_line = ""
        for back in range(1, 9):
            candidate = lines[max(0, i - back)].strip()
            if _valid_product_name(candidate):
                product_line = candidate
                break

        # ── If nothing usable above, try lines forward ─────────
        if not product_line:
            for fwd in range(1, 4):
                candidate = lines[min(len(lines) - 1, i + fwd)].strip()
                if _valid_product_name(candidate):
                    product_line = candidate
                    break

        # ── Last resort: use the document title ────────────────
        if not product_line:
            product_line = title

        mandatory_match = _MANDATORY_RE.search(context)
        mandatory_val   = mandatory_match.group(1).capitalize() if mandatory_match else "Mandatory"

        scheme_match = _SCHEME_RE.search(context)
        scheme_val   = scheme_match.group(0) if scheme_match else "ISI Mark (Scheme I)"

        penalty_matches = _PENALTY_RE.findall(context)
        penalty_val = penalty_matches[0][:200] if penalty_matches else ""

        for std in standards:
            rows.append({
                "product_name":           product_line[:120],
                "is_standard_number":     std.strip(),
                "standard_title":         "",
                "mandatory_or_voluntary": mandatory_val,
                "scheme_type":            scheme_val,
                "qco_reference":          title,
                "source_url":             source_url,
                "penalty_clause":         penalty_val,
            })

    # Deduplicate by (product_name prefix, is_standard_number)
    seen: set[tuple] = set()
    unique_rows = []
    for r in rows:
        key = (r["product_name"][:60], r["is_standard_number"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)

    log.info("  Extracted %d structured QCO rows from '%s'", len(unique_rows), title)
    return unique_rows


# ═══════════════════════════════════════════════════════════════
#  Persistence helpers
# ═══════════════════════════════════════════════════════════════

def make_document(source_url: str, source_type: str, title: str,
                  category: str, raw_text: str) -> dict:
    return {
        "source_url":  source_url,
        "source_type": source_type,
        "title":       title,
        "category":    category,
        "raw_text":    raw_text,
        "scraped_at":  datetime.now(timezone.utc).isoformat(),
    }


def save_qco_csv(rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = [
        "product_name", "is_standard_number", "standard_title",
        "mandatory_or_voluntary", "scheme_type", "qco_reference",
        "source_url", "penalty_clause",
    ]
    write_header = not QCO_CSV.exists() or QCO_CSV.stat().st_size == 0
    with open(QCO_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})
    log.info("Saved %d rows to %s", len(rows), QCO_CSV)


def save_qco_sqlite(rows: list[dict]) -> None:
    if not rows:
        return
    conn = sqlite3.connect(QCO_DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS qco_standards (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name           TEXT,
            is_standard_number     TEXT,
            standard_title         TEXT,
            mandatory_or_voluntary TEXT,
            scheme_type            TEXT,
            qco_reference          TEXT,
            source_url             TEXT,
            penalty_clause         TEXT,
            UNIQUE(product_name, is_standard_number)
        )
    """)
    cur.executemany("""
        INSERT OR IGNORE INTO qco_standards
          (product_name, is_standard_number, standard_title,
           mandatory_or_voluntary, scheme_type, qco_reference,
           source_url, penalty_clause)
        VALUES
          (:product_name, :is_standard_number, :standard_title,
           :mandatory_or_voluntary, :scheme_type, :qco_reference,
           :source_url, :penalty_clause)
    """, rows)
    conn.commit()
    conn.close()
    log.info("SQLite: upserted %d rows into %s", len(rows), QCO_DB)


# ═══════════════════════════════════════════════════════════════
#  Main pipeline
# ═══════════════════════════════════════════════════════════════

def _safe_print(msg: str) -> None:
    """Print with unicode error replacement (safe for Windows cp1252 consoles)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def run_pipeline() -> None:
    documents:    list[dict] = []
    all_qco_rows: list[dict] = []

    # ── 1. HTML pages ──────────────────────────────────────────
    log.info("=" * 60)
    log.info("PHASE 1A — Scraping HTML pages (%d targets)", len(HTML_TARGETS))
    log.info("=" * 60)

    for target in HTML_TARGETS:
        url      = target["url"]
        title    = target["title"]
        cat      = target["category"]
        pw_first = target.get("playwright_first", False)

        if pw_first:
            html = playwright_fetch_html(url)
        else:
            html = fetch_html(url)

        if html:
            text = extract_text_from_html(html)
            if len(text) < 150 and not pw_first:
                log.warning("Short text (%d chars) for '%s' — trying Playwright fallback", len(text), title)
                html2 = playwright_fetch_html(url)
                if html2:
                    text2 = extract_text_from_html(html2)
                    if len(text2) > len(text):
                        text = text2
                        log.info("  Playwright improved text to %d chars", len(text))
        else:
            if not pw_first:
                log.warning("requests failed for '%s' — trying Playwright...", title)
                html = playwright_fetch_html(url)
                if html:
                    text = extract_text_from_html(html)
                else:
                    text = ""
            else:
                text = ""

        if text:
            log.info("  Extracted %d chars from '%s'", len(text), title)
            documents.append(make_document(url, "html", title, cat, text))
        else:
            log.error("  SKIPPED (no content): %s", url)
            _log_failure(url, "No text extracted after all attempts")

        time.sleep(REQUEST_DELAY)

    # ── 2. Direct PDFs ─────────────────────────────────────────
    log.info("=" * 60)
    log.info("PHASE 1B — Downloading direct PDFs (%d targets)", len(PDF_TARGETS))
    log.info("=" * 60)

    for target in PDF_TARGETS:
        url          = target["url"]
        title        = target["title"]
        cat          = target["category"]
        try_pw       = target.get("try_playwright", False)

        pdf_bytes = fetch_pdf_bytes(url)

        if not pdf_bytes and try_pw:
            log.info("  requests failed (403?) — trying Playwright for PDF: %s", url)
            pdf_bytes = playwright_fetch_pdf(url)

        if pdf_bytes:
            safe_name = re.sub(r"[^\w.-]", "_", Path(url).name) or "document.pdf"
            pdf_path  = DATA_DIR / "pdfs" / safe_name
            pdf_path.write_bytes(pdf_bytes)
            log.info("  Saved PDF (%d bytes) -> %s", len(pdf_bytes), pdf_path)

            text = extract_text_from_pdf(pdf_bytes)
            log.info("  Extracted %d chars from PDF '%s'", len(text), title)
            documents.append(make_document(url, "pdf", title, cat, text))

            if cat == "qco":
                qco_rows = parse_qco_structured(text, url, title)
                all_qco_rows.extend(qco_rows)
        else:
            log.error("  SKIPPED (all download methods failed): %s", url)
            _log_failure(url, "All download methods failed")

        time.sleep(REQUEST_DELAY)

    # ── 3. QCO index → linked PDFs ─────────────────────────────
    log.info("=" * 60)
    log.info("PHASE 1C — Discovering QCO PDFs from index page")
    log.info("=" * 60)

    qco_links = discover_qco_pdf_links(QCO_INDEX_URL)
    time.sleep(REQUEST_DELAY)

    if not qco_links:
        log.warning("No matching QCO PDF links found after filtering. Check failed_urls.log.")
    else:
        log.info("Will download %d QCO PDFs.", len(qco_links))

    for i, target in enumerate(qco_links, start=1):
        url   = target["url"]
        title = target["title"]
        cat   = target["category"]
        log.info("[QCO %d/%d] %s", i, len(qco_links), url)

        pdf_bytes = fetch_pdf_bytes(url)
        if pdf_bytes:
            safe_name = re.sub(r"[^\w.-]", "_", Path(url).name) or f"qco_{i}.pdf"
            pdf_path  = DATA_DIR / "pdfs" / safe_name
            pdf_path.write_bytes(pdf_bytes)

            text = extract_text_from_pdf(pdf_bytes)
            log.info("  Extracted %d chars from '%s'", len(text), title)
            documents.append(make_document(url, "pdf", title, cat, text))

            qco_rows = parse_qco_structured(text, url, title)
            all_qco_rows.extend(qco_rows)
        else:
            log.error("  SKIPPED: %s", url)

        time.sleep(REQUEST_DELAY)

    # ── 4. Persist ──────────────────────────────────────────────
    log.info("=" * 60)
    log.info("PHASE 1D — Saving output files")
    log.info("=" * 60)

    with open(RAW_JSON, "w", encoding="utf-8") as f:
        json.dump(documents, f, ensure_ascii=False, indent=2)
    log.info("Saved %d documents -> %s", len(documents), RAW_JSON)

    save_qco_csv(all_qco_rows)
    save_qco_sqlite(all_qco_rows)

    # ── 5. Console summary ──────────────────────────────────────
    log.info("=" * 60)
    log.info("PIPELINE COMPLETE")
    log.info("  Documents scraped  : %d", len(documents))
    log.info("  QCO structured rows: %d", len(all_qco_rows))
    log.info("  Failed URLs logged : %s", FAILED_LOG)
    log.info("  Output JSON        : %s", RAW_JSON)
    log.info("  QCO CSV            : %s", QCO_CSV)
    log.info("  QCO SQLite         : %s", QCO_DB)
    log.info("=" * 60)

    _safe_print("\n" + "=" * 60)
    _safe_print("SCRAPE RESULTS SUMMARY")
    _safe_print("=" * 60)
    for doc in documents:
        preview = doc["raw_text"][:200].replace("\n", " ")
        _safe_print(f"\n[{doc['category'].upper()}] {doc['title']}")
        _safe_print(f"  Source : {doc['source_url']}")
        _safe_print(f"  Type   : {doc['source_type']}")
        _safe_print(f"  Length : {len(doc['raw_text'])} chars")
        _safe_print(f"  Preview: {preview[:180]}...")

    if all_qco_rows:
        _safe_print(f"\n{'='*60}")
        _safe_print(f"QCO STRUCTURED TABLE -- {len(all_qco_rows)} rows")
        _safe_print(f"{'='*60}")
        for row in all_qco_rows[:15]:
            _safe_print(f"  {row['is_standard_number']:<25} | {(row.get('product_name') or '')[:60]}")
        if len(all_qco_rows) > 15:
            _safe_print(f"  ... and {len(all_qco_rows) - 15} more rows (see {QCO_CSV})")
    else:
        _safe_print("\n[!] No QCO structured rows extracted.")
        _safe_print("    If QCO PDFs were downloaded, check data/scraper.log for IS number pattern matches.")

    if FAILED_LOG.exists() and FAILED_LOG.stat().st_size > 0:
        _safe_print(f"\n[!] Some URLs failed -- review: {FAILED_LOG}")
        with open(FAILED_LOG, encoding="utf-8") as f:
            for line in f:
                _safe_print(f"    {line.rstrip()}")
    else:
        _safe_print("\n[OK] No failures logged.")


if __name__ == "__main__":
    run_pipeline()
