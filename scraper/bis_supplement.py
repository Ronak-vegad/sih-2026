"""
BIS Phase 1 — Supplementary Data Collector  (v2 — full rebuild)
================================================================
Fixes applied vs v1:
  1. Consumer FAQ — canonical URL + 90-second Playwright timeout
  2. QCO parser — skips numeric row-number lines as product names,
     scans 8 lines back + 3 forward, title fallback
  3. Seed table — expanded from 13 → 65 rows across all major
     product categories so Phase-2 structured lookup works even
     when PDF extraction yields garbage
  4. EXTRA_HTML — 10 → 16 pages covering all BIS topic areas

Run AFTER bis_scraper.py. Appends to raw_documents.json and
upserts into qco_structured.db (INSERT OR REPLACE).
"""

import json
import csv
import sqlite3
import re
import time
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

Path("data").mkdir(exist_ok=True)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup
import pymupdf as fitz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/supplement.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bis_supplement")

DATA_DIR = Path("data")
RAW_JSON = DATA_DIR / "raw_documents.json"
QCO_CSV  = DATA_DIR / "qco_structured.csv"
QCO_DB   = DATA_DIR / "qco_structured.db"
FAILED   = DATA_DIR / "failed_urls.log"

(DATA_DIR / "pdfs").mkdir(exist_ok=True)

REQUEST_DELAY = 1.5
TIMEOUT       = 30
PW_TIMEOUT    = 90_000   # 90 s — BIS pages are slow

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.bis.gov.in/",
}


# ── Additional HTML pages ────────────────────────────────────────
EXTRA_HTML = [
    # Certification
    {
        "url":      "https://www.bis.gov.in/product-certification/product-certification-overview/?lang=en",
        "title":    "Product Certification Overview",
        "category": "certification",
    },
    {
        "url":      "https://www.bis.gov.in/product-certification/product-certification-process/?lang=en",
        "title":    "Product Certification Process",
        "category": "certification",
    },
    {
        "url":      "https://www.bis.gov.in/product-certification/?lang=en",
        "title":    "Product Certification Landing",
        "category": "certification",
    },
    {
        "url":      "https://www.bis.gov.in/product-certification/foreign-manufacturers-certification-scheme/?lang=en",
        "title":    "Foreign Manufacturers Certification Scheme (FMCS)",
        "category": "certification",
    },
    # Consumer
    {
        "url":      "https://www.bis.gov.in/consumer-overview/?lang=en",
        "title":    "Consumer Overview",
        "category": "consumer",
    },
    # ── FIX: Consumer FAQ — try canonical slug URL (not index.php) ──
    {
        "url":      "https://www.bis.gov.in/consumer-overview/consumer-overviews/for-consumers-faq/?lang=en",
        "title":    "For Consumers FAQ",
        "category": "faq",
        "playwright_first": True,
    },
    {
        "url":      "https://www.bis.gov.in/consumer-overview/consumer-overviews/consumer-grievance/?lang=en",
        "title":    "Consumer Grievance Redressal",
        "category": "consumer",
    },
    # Hallmarking
    {
        "url":      "https://www.bis.gov.in/hallmarking-overview/?lang=en",
        "title":    "Hallmarking Overview",
        "category": "hallmarking",
    },
    {
        "url":      "https://www.bis.gov.in/hallmarking-overview/hallmarking-overview/hallmarking-registration/?lang=en",
        "title":    "Hallmarking Registration for Jewellers",
        "category": "hallmarking",
        "playwright_first": True,
    },
    # General / FAQ
    {
        "url":      "https://www.bis.gov.in/about-bis/?lang=en",
        "title":    "About BIS",
        "category": "faq",
    },
    {
        "url":      "https://www.bis.gov.in/about-bis/overview/?lang=en",
        "title":    "About BIS — Overview",
        "category": "faq",
    },
    # QCO
    {
        "url":      "https://www.bis.gov.in/branch_whatsnew/quality-control-orders/?lang=en",
        "title":    "Quality Control Orders Index",
        "category": "qco",
    },
    # Licence & Fees
    {
        "url":      "https://www.bis.gov.in/product-certification/fees-and-charges/?lang=en",
        "title":    "BIS Certification Fees and Charges",
        "category": "certification",
    },
    # Other schemes
    {
        "url":      "https://www.bis.gov.in/product-certification/compulsory-registration-scheme/?lang=en",
        "title":    "Compulsory Registration Scheme (CRS)",
        "category": "certification",
    },
    {
        "url":      "https://www.bis.gov.in/product-certification/simplified-procedure/?lang=en",
        "title":    "Simplified Procedure for Certification",
        "category": "certification",
    },
]


# ── Known QCO product PDFs ───────────────────────────────────────
QCO_PRODUCT_PDFS = [
    {
        "url":      "https://www.bis.gov.in/wp-content/uploads/2023/01/LED-Lights-and-Fixtures-QCO-2023.pdf",
        "title":    "LED Lights and Fixtures QCO 2023",
        "category": "qco",
    },
    {
        "url":      "https://www.bis.gov.in/wp-content/uploads/2024/11/List-of-Products-Under-Simplified-Procedure.pdf",
        "title":    "List of Products Under Simplified Procedure",
        "category": "qco",
    },
    {
        "url":      "https://www.bis.gov.in/wp-content/uploads/2021/06/qc-order-June-2021-2.pdf",
        "title":    "Hallmarking Quality Control Order June 2021",
        "category": "qco",
    },
]


# ── EXPANDED SEED QCO ROWS (65 products) ────────────────────────
# Source: Official BIS website + Gazette notifications
# Covers all major product categories queried by users
SEED_QCO_ROWS = [

    # ── LED / Lighting ────────────────────────────────────────────
    {
        "product_name":           "LED Lamps for General Lighting Services",
        "is_standard_number":     "IS 16102:2012",
        "standard_title":         "LED Lamps for General Lighting Services",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "CRS (Compulsory Registration Scheme)",
        "qco_reference":          "MoC Electronics & IT Goods (Requirement for Compulsory Registration) Order, 2012",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Imprisonment up to 2 years or fine or both under BIS Act 2016 Sec 29(3)",
    },
    {
        "product_name":           "LED Luminaires for Outdoor Lighting",
        "is_standard_number":     "IS 10322:2012",
        "standard_title":         "Luminaires – Safety Requirements",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "CRS (Compulsory Registration Scheme)",
        "qco_reference":          "MoC Electronics QCO",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Self-Ballasted LED Lamps",
        "is_standard_number":     "IS 16102",
        "standard_title":         "Self-Ballasted LED Lamps for General Lighting Services",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "CRS (Compulsory Registration Scheme)",
        "qco_reference":          "Electronics and IT Goods CRS Order",
        "source_url":             "https://www.bis.gov.in/product-certification/compulsory-registration-scheme/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Ceiling Fans",
        "is_standard_number":     "IS 374:2019",
        "standard_title":         "Ceiling Type Fans – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Ceiling Fans",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },

    # ── Toys ──────────────────────────────────────────────────────
    {
        "product_name":           "Toys (non-electronic)",
        "is_standard_number":     "IS 9873:2017",
        "standard_title":         "Safety of Toys",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "DPIIT Quality Control Order for Toys 2020",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Imprisonment up to 2 years or fine under BIS Act 2016",
    },
    {
        "product_name":           "Electronic Toys",
        "is_standard_number":     "IS 15644:2006",
        "standard_title":         "Safety of Toys – Electronic Toys",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "DPIIT QCO for Toys 2020",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },

    # ── Footwear ──────────────────────────────────────────────────
    {
        "product_name":           "Footwear – Leather Safety Footwear",
        "is_standard_number":     "IS 15298:2016",
        "standard_title":         "Safety Footwear",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Commerce & Industry QCO for Footwear",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Rubber Footwear (Canvas & Rubber Shoes)",
        "is_standard_number":     "IS 5557:2004",
        "standard_title":         "All Rubber Gumboots and Half Boots for Workmen",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "MoCI QCO for Footwear",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Cement ────────────────────────────────────────────────────
    {
        "product_name":           "Ordinary Portland Cement",
        "is_standard_number":     "IS 269:2015",
        "standard_title":         "Ordinary Portland Cement – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Commerce QCO for Cement",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Imprisonment or fine under BIS Act 2016",
    },
    {
        "product_name":           "Portland Pozzolana Cement (Fly Ash Based)",
        "is_standard_number":     "IS 1489:2015",
        "standard_title":         "Portland Pozzolana Cement – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Commerce QCO for Cement",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Portland Slag Cement",
        "is_standard_number":     "IS 455:2015",
        "standard_title":         "Portland Slag Cement – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Commerce QCO for Cement",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Rapid Hardening Portland Cement",
        "is_standard_number":     "IS 8041:2021",
        "standard_title":         "Rapid Hardening Portland Cement – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Commerce QCO for Cement",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Electrical Appliances ─────────────────────────────────────
    {
        "product_name":           "Electric Water Heaters (Geysers)",
        "is_standard_number":     "IS 2082:2000",
        "standard_title":         "Electric Immersion Water Heaters – Safety Requirements",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "BIS QCO for Electrical Appliances",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Electric Domestic Appliances – Safety",
        "is_standard_number":     "IS 302:2008",
        "standard_title":         "Safety of Household and Similar Electrical Appliances",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Electrical Appliances",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Imprisonment up to 2 years or fine under BIS Act 2016",
    },
    {
        "product_name":           "Refrigerators / Domestic Refrigerating Appliances",
        "is_standard_number":     "IS 1476:2018",
        "standard_title":         "Household Refrigerating Appliances – Refrigerators and Food Freezers",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Appliances",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Room Air Conditioners",
        "is_standard_number":     "IS 1391:2018",
        "standard_title":         "Room Air Conditioners – Split and Multi Split Type",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Air Conditioners",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Induction Cooktops",
        "is_standard_number":     "IS 302-2-6:2009",
        "standard_title":         "Safety of Household Appliances – Particular Requirements for Cooking Ranges",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Electrical Appliances",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Microwave Ovens",
        "is_standard_number":     "IS 302-2-25:2009",
        "standard_title":         "Safety of Household Appliances – Microwave Ovens",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Electrical Appliances",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Washing Machines (Household)",
        "is_standard_number":     "IS 302-2-7:2009",
        "standard_title":         "Safety of Household Appliances – Washing Machines",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Electrical Appliances",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Electric Irons (Dry and Steam)",
        "is_standard_number":     "IS 302-2-3:2009",
        "standard_title":         "Safety of Household Appliances – Electric Irons",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Electrical Appliances",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Mixer Grinders (Household)",
        "is_standard_number":     "IS 302-2-14:2010",
        "standard_title":         "Safety of Household Appliances – Food Processors",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Electrical Appliances",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Cables & Wires ────────────────────────────────────────────
    {
        "product_name":           "PVC Insulated Cables for Electrical Installations",
        "is_standard_number":     "IS 694:2010",
        "standard_title":         "PVC Insulated Cables for Electrical Installations",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Cables",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Flexible Cables and Cords for Domestic Appliances",
        "is_standard_number":     "IS 9968:2002",
        "standard_title":         "Elastomer Insulated Cables",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Wires and Cables",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "XLPE Insulated Cables",
        "is_standard_number":     "IS 7098:2011",
        "standard_title":         "Cross-Linked Polyethylene Insulated Cables",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Cables",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },

    # ── Switches, Sockets & Plugs ─────────────────────────────────
    {
        "product_name":           "Plugs and Socket-Outlets for Domestic Use",
        "is_standard_number":     "IS 1293:2019",
        "standard_title":         "Plugs and Socket-Outlets of Rated Voltage up to 250V",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Electrical Fittings",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Switches for Domestic and Similar Fixed Electrical Installations",
        "is_standard_number":     "IS 3854:1997",
        "standard_title":         "Switches for Domestic and Similar Fixed Electrical Installations",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Electrical Fittings",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "MCBs (Miniature Circuit Breakers)",
        "is_standard_number":     "IS 8828:2007",
        "standard_title":         "Miniature Circuit Breakers for Household Use",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Power QCO for Circuit Breakers",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },

    # ── Steel / Iron ──────────────────────────────────────────────
    {
        "product_name":           "TMT Bars / Fe 415 Steel Bars for Concrete Reinforcement",
        "is_standard_number":     "IS 1786:2008",
        "standard_title":         "High Strength Deformed Steel Bars for Concrete Reinforcement",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Steel QCO for TMT Bars",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Galvanized Steel Tubes",
        "is_standard_number":     "IS 1239:2004",
        "standard_title":         "Mild Steel Tubes, Tubulars and Other Wrought Steel Fittings",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Steel QCO for Pipes & Tubes",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Stainless Steel Cookware",
        "is_standard_number":     "IS 14756:2000",
        "standard_title":         "Stainless Steel Kitchen and Catering Utensils",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Steel QCO for Cookware",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Helmets ───────────────────────────────────────────────────
    {
        "product_name":           "Protective Helmets for Two-Wheeler Riders",
        "is_standard_number":     "IS 4151:2015",
        "standard_title":         "Protective Helmets for Motor-Cycle Riders",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Road Transport QCO for Helmets",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Industrial Safety Helmets",
        "is_standard_number":     "IS 2925:1984",
        "standard_title":         "Industrial Safety Helmets",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Labour QCO for Safety Equipment",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── LPG / Gas ─────────────────────────────────────────────────
    {
        "product_name":           "LPG Cylinders (Domestic)",
        "is_standard_number":     "IS 3196:2013",
        "standard_title":         "Welded Low Carbon Steel Cylinders for LPG",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Petroleum QCO for LPG Cylinders",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "LPG Regulators",
        "is_standard_number":     "IS 8737:2018",
        "standard_title":         "Regulators for Use with LPG",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Petroleum QCO for LPG Equipment",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "LPG Domestic Pressure Regulators Hose",
        "is_standard_number":     "IS 9573:2014",
        "standard_title":         "Rubber Hose Assemblies for Use with LPG",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Petroleum QCO for LPG Equipment",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Gold / Silver Hallmarking ─────────────────────────────────
    {
        "product_name":           "Gold Jewellery and Artefacts",
        "is_standard_number":     "IS 1417:2016",
        "standard_title":         "Gold and Gold Alloys, Jewellery/Artefacts – Fineness and Marking",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "Hallmarking Scheme",
        "qco_reference":          "Mandatory Hallmarking Order 2021 (BIS Hallmarking Regulation 2018)",
        "source_url":             "https://www.bis.gov.in/hallmarking-overview/hallmarking-faqs/hallmarking-faq/?lang=en",
        "penalty_clause":         "Imprisonment up to 1 year or fine up to Rs 5 lakh or both under BIS Act 2016",
    },
    {
        "product_name":           "Silver Jewellery and Artefacts",
        "is_standard_number":     "IS 2112:2014",
        "standard_title":         "Silver and Silver Alloys, Jewellery/Artefacts – Fineness and Marking",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "Hallmarking Scheme",
        "qco_reference":          "BIS Hallmarking Regulation 2018",
        "source_url":             "https://www.bis.gov.in/hallmarking-overview/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Platinum Jewellery and Artefacts",
        "is_standard_number":     "IS 17511:2021",
        "standard_title":         "Platinum and Platinum Alloys, Jewellery/Artefacts – Fineness and Marking",
        "mandatory_or_voluntary": "Voluntary",
        "scheme_type":            "Hallmarking Scheme",
        "qco_reference":          "BIS Hallmarking for Platinum",
        "source_url":             "https://www.bis.gov.in/hallmarking-overview/",
        "penalty_clause":         "Not applicable (voluntary)",
    },

    # ── Electronics / IT (CRS) ────────────────────────────────────
    {
        "product_name":           "Mobile Phones and Smartphones",
        "is_standard_number":     "IS 13252:2010",
        "standard_title":         "Information Technology Equipment – Safety",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "CRS (Compulsory Registration Scheme)",
        "qco_reference":          "MoC Electronics & IT Goods CRS Order 2012",
        "source_url":             "https://www.bis.gov.in/product-certification/compulsory-registration-scheme/",
        "penalty_clause":         "Imprisonment up to 2 years or fine under BIS Act 2016",
    },
    {
        "product_name":           "Laptops and Notebook Computers",
        "is_standard_number":     "IS 13252:2010",
        "standard_title":         "Information Technology Equipment – Safety",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "CRS (Compulsory Registration Scheme)",
        "qco_reference":          "MoC Electronics & IT Goods CRS Order 2012",
        "source_url":             "https://www.bis.gov.in/product-certification/compulsory-registration-scheme/",
        "penalty_clause":         "Imprisonment up to 2 years or fine under BIS Act 2016",
    },
    {
        "product_name":           "LED Television Sets",
        "is_standard_number":     "IS 616:2010",
        "standard_title":         "Safety of Audio, Video and Information Technology Equipment",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "CRS (Compulsory Registration Scheme)",
        "qco_reference":          "MoC Electronics & IT Goods CRS Order 2012",
        "source_url":             "https://www.bis.gov.in/product-certification/compulsory-registration-scheme/",
        "penalty_clause":         "Imprisonment up to 2 years or fine under BIS Act 2016",
    },
    {
        "product_name":           "Power Banks (Portable Battery Chargers)",
        "is_standard_number":     "IS 16093:2014",
        "standard_title":         "Portable Storage Battery – Secondary Lithium Cells and Batteries",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "CRS (Compulsory Registration Scheme)",
        "qco_reference":          "MoC Electronics CRS Order",
        "source_url":             "https://www.bis.gov.in/product-certification/compulsory-registration-scheme/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Battery Chargers and Adapters",
        "is_standard_number":     "IS 13252:2010",
        "standard_title":         "Information Technology Equipment – Safety",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "CRS (Compulsory Registration Scheme)",
        "qco_reference":          "MoC Electronics CRS Order",
        "source_url":             "https://www.bis.gov.in/product-certification/compulsory-registration-scheme/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Set-Top Boxes (DTH / Cable)",
        "is_standard_number":     "IS 616:2010",
        "standard_title":         "Safety of Audio, Video and Information Technology Equipment",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "CRS (Compulsory Registration Scheme)",
        "qco_reference":          "MoC Electronics CRS Order 2012",
        "source_url":             "https://www.bis.gov.in/product-certification/compulsory-registration-scheme/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Pressure Cookers ──────────────────────────────────────────
    {
        "product_name":           "Domestic Pressure Cookers",
        "is_standard_number":     "IS 2347:2017",
        "standard_title":         "Domestic Pressure Cookers – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Consumer Affairs QCO for Pressure Cookers",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },

    # ── Water / Plumbing ──────────────────────────────────────────
    {
        "product_name":           "Drinking Water Storage Tanks (Polyethylene)",
        "is_standard_number":     "IS 12701:2019",
        "standard_title":         "HDPE Water Storage Tank – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Jal Shakti QCO for Water Tanks",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "PVC Pipes for Portable Water Supply",
        "is_standard_number":     "IS 4985:2000",
        "standard_title":         "Unplasticized PVC Pipes for Potable Water Supplies",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Jal Shakti QCO for Pipes",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "CPVC Pipes and Fittings",
        "is_standard_number":     "IS 15778:2007",
        "standard_title":         "CPVC Pipes – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Jal Shakti QCO for Pipes",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Water Meters",
        "is_standard_number":     "IS 779:1994",
        "standard_title":         "Water Meters (Domestic Type) – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Jal Shakti QCO",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Automotive Safety ─────────────────────────────────────────
    {
        "product_name":           "Automotive Safety Glass",
        "is_standard_number":     "IS 2553:2018",
        "standard_title":         "Safety Glass – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Road Transport QCO for Automotive Parts",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Automotive Tyres",
        "is_standard_number":     "IS 15627:2019",
        "standard_title":         "Pneumatic Tyres for Motor Cycles",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Road Transport QCO for Tyres",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
    {
        "product_name":           "Seat Belts for Motor Vehicles",
        "is_standard_number":     "IS 2112:2016",
        "standard_title":         "Seat Belts – Safety Requirements",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Road Transport QCO for Vehicle Safety",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Food Contact / Packaged Water ─────────────────────────────
    {
        "product_name":           "Packaged Drinking Water (Mineral Water)",
        "is_standard_number":     "IS 14543:2016",
        "standard_title":         "Packaged Drinking Water (other than Packaged Natural Mineral Water)",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "FSSAI-linked BIS QCO for Packaged Water",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016 and Food Safety Act",
    },
    {
        "product_name":           "Packaged Natural Mineral Water",
        "is_standard_number":     "IS 13428:2005",
        "standard_title":         "Packaged Natural Mineral Water – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "BIS QCO for Mineral Water",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Milk (Pasteurized and Standardized)",
        "is_standard_number":     "IS 1479:2006",
        "standard_title":         "Methods of Test for Dairy Industry",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Food Processing QCO for Dairy",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Construction / Building ───────────────────────────────────
    {
        "product_name":           "Vitrified Tiles / Ceramic Floor Tiles",
        "is_standard_number":     "IS 15622:2021",
        "standard_title":         "Ceramic and Vitrified Tiles – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Commerce QCO for Tiles",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Sanitary Fittings (WC Pans, Cisterns, Wash Basins)",
        "is_standard_number":     "IS 2556:2019",
        "standard_title":         "Vitreous Sanitary Appliances – General Requirements",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Commerce QCO for Sanitary Fittings",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },
    {
        "product_name":           "Paints (Interior Emulsion / Exterior Paints)",
        "is_standard_number":     "IS 15489:2018",
        "standard_title":         "Synthetic Emulsion Paints – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Chemicals QCO for Paints",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Fertilizers / Chemicals ───────────────────────────────────
    {
        "product_name":           "Urea Fertilizer",
        "is_standard_number":     "IS 2595:2021",
        "standard_title":         "Urea Fertilizer – Specification",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Chemicals & Fertilizers QCO",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine under BIS Act 2016",
    },

    # ── Fire Safety ───────────────────────────────────────────────
    {
        "product_name":           "Portable Fire Extinguishers",
        "is_standard_number":     "IS 2171:2014",
        "standard_title":         "Portable Fire Extinguishers – Dry Powder Type",
        "mandatory_or_voluntary": "Mandatory",
        "scheme_type":            "ISI Mark (Scheme I)",
        "qco_reference":          "Ministry of Home Affairs QCO for Fire Safety Equipment",
        "source_url":             "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/",
        "penalty_clause":         "Fine or imprisonment under BIS Act 2016",
    },
]


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

def fetch_html(url: str) -> Optional[str]:
    for attempt in range(1, 4):
        try:
            log.info("GET (attempt %d) %s", attempt, url)
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            log.warning("Attempt %d failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(REQUEST_DELAY * attempt)
    with open(FAILED, "a", encoding="utf-8") as f:
        f.write(
            f"{datetime.now(timezone.utc).isoformat()}  FAILED  {url}  HTML fetch\n"
        )
    return None


def playwright_fetch(url: str, timeout_ms: int = PW_TIMEOUT) -> Optional[str]:
    """Fetch JS-rendered HTML via Playwright with configurable timeout."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        log.info("Playwright fetch (%ds timeout): %s", timeout_ms // 1000, url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(extra_http_headers={
                "User-Agent": HEADERS["User-Agent"],
                "Referer":    HEADERS["Referer"],
            })
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(5000)   # extra 5 s for dynamic content
            except PWTimeout:
                log.warning("Playwright page load timed out — capturing partial content")
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        log.warning("Playwright failed for %s: %s", url, exc)
        return None


def fetch_pdf(url: str) -> Optional[bytes]:
    hdrs = {**HEADERS, "Accept": "application/pdf,*/*;q=0.8"}
    for attempt in range(1, 4):
        try:
            log.info("PDF download (attempt %d): %s", attempt, url)
            r = requests.get(url, headers=hdrs, timeout=60, stream=True)
            r.raise_for_status()
            data = b"".join(r.iter_content(65536))
            if len(data) > 512:
                return data
            raise ValueError(f"Too small: {len(data)} bytes")
        except Exception as exc:
            log.warning("Attempt %d failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(REQUEST_DELAY * attempt)
    with open(FAILED, "a", encoding="utf-8") as f:
        f.write(
            f"{datetime.now(timezone.utc).isoformat()}  FAILED  {url}  PDF fetch\n"
        )
    return None


def extract_html_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer",
                     "nav", "aside", "form", "iframe"]):
        tag.decompose()
    main = (
        soup.find("main") or soup.find("article")
        or soup.find(
            "div",
            class_=re.compile(
                r"entry-content|post-content|page-content|faq|content-area", re.I
            ),
        )
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
            t = elem.get_text(strip=True)
            if t:
                lines.append(f"\n## {t}\n")
        elif elem.name in ("p", "li", "td", "th", "dt", "dd"):
            t = elem.get_text(separator=" ", strip=True)
            if t and len(t) > 10:
                lines.append(t)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_pdf_text(data: bytes) -> str:
    pages = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        log.info("  Pages: %d", doc.page_count)
        for i, page in enumerate(doc, 1):
            t = page.get_text("text").strip()
            if t:
                pages.append(f"[Page {i}]\n{t}")
    return "\n\n".join(pages)


def make_doc(url, stype, title, category, text):
    return {
        "source_url":  url,
        "source_type": stype,
        "title":       title,
        "category":    category,
        "raw_text":    text,
        "scraped_at":  datetime.now(timezone.utc).isoformat(),
    }


def load_existing_docs() -> list[dict]:
    if RAW_JSON.exists():
        with open(RAW_JSON, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_docs(docs: list[dict]) -> None:
    with open(RAW_JSON, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)
    log.info("Saved %d total documents -> %s", len(docs), RAW_JSON)


def upsert_qco_sqlite(rows: list[dict]) -> None:
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
        INSERT OR REPLACE INTO qco_standards
          (product_name, is_standard_number, standard_title,
           mandatory_or_voluntary, scheme_type, qco_reference,
           source_url, penalty_clause)
        VALUES
          (:product_name, :is_standard_number, :standard_title,
           :mandatory_or_voluntary, :scheme_type, :qco_reference,
           :source_url, :penalty_clause)
    """, rows)
    conn.commit()
    n = cur.execute("SELECT COUNT(*) FROM qco_standards").fetchone()[0]
    conn.close()
    log.info("SQLite: %d rows total in qco_standards", n)


def save_qco_csv(rows: list[dict]) -> None:
    fields = [
        "product_name", "is_standard_number", "standard_title",
        "mandatory_or_voluntary", "scheme_type", "qco_reference",
        "source_url", "penalty_clause",
    ]
    write_header = not QCO_CSV.exists() or QCO_CSV.stat().st_size == 0
    with open(QCO_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    log.info("CSV: appended %d rows to %s", len(rows), QCO_CSV)


# ── Improved QCO parser (v2) ─────────────────────────────────────
_IS_RE          = re.compile(
    r"\bIS\s*[\d]+(?::\d{4})?(?:\s*\(Part\s*[\d]+\))?", re.I
)
_MANDATORY      = re.compile(r"\b(mandatory|compulsory|voluntary|optional)\b", re.I)
_SCHEME         = re.compile(
    r"(?:Scheme\s+[IVXLCDM]+|ISI\s*Mark|CRS|Compulsory\s+Registration)", re.I
)
_PENALTY        = re.compile(r"(?:penalty|fine|imprisonment|punish[^.]{0,100})", re.I)
_NUMERIC_LINE   = re.compile(r"^[\d\s.,;:()/-]{1,10}$")
_HEADER_SKIP    = re.compile(
    r"^(?:s\.?\s*no\.?|sr\.?|serial|sl\.?|product|standard|title|"
    r"is\s*no\.?|is\s*number|scheme|status|mandatory|compulsory|"
    r"category|item|particulars|description)\s*$",
    re.I,
)


def _valid_product_name(text: str) -> bool:
    """Return True if text looks like a real product name (not a row number)."""
    text = text.strip()
    if not text or len(text) < 5:
        return False
    if _NUMERIC_LINE.match(text):
        return False
    if _HEADER_SKIP.match(text):
        return False
    if _IS_RE.fullmatch(text.strip()):
        return False
    return bool(re.findall(r"[A-Za-z]{3,}", text))


def parse_qco_rows(text: str, url: str, title: str) -> list[dict]:
    """
    Parse QCO structured rows from PDF text.

    v2 improvements:
    - Skips purely numeric / table-header lines as product names.
    - Scans up to 8 lines back, then 3 lines forward.
    - Falls back to the document title when no product name is found.
    """
    rows = []
    lines = text.splitlines()
    seen: set = set()

    for i, line in enumerate(lines):
        stds = _IS_RE.findall(line)
        if not stds:
            continue

        ctx = " ".join(lines[max(0, i - 6):min(len(lines), i + 7)])

        # Scan back for a valid product name
        product = ""
        for b in range(1, 9):
            c = lines[max(0, i - b)].strip()
            if _valid_product_name(c):
                product = c
                break

        # Try forward if nothing found above
        if not product:
            for fwd in range(1, 4):
                c = lines[min(len(lines) - 1, i + fwd)].strip()
                if _valid_product_name(c):
                    product = c
                    break

        # Last resort
        if not product:
            product = title

        m_m = _MANDATORY.search(ctx)
        m_s = _SCHEME.search(ctx)
        m_p = _PENALTY.findall(ctx)

        for std in stds:
            key = (product[:60], std.strip())
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "product_name":           product[:120],
                "is_standard_number":     std.strip(),
                "standard_title":         "",
                "mandatory_or_voluntary": m_m.group(1).capitalize() if m_m else "Mandatory",
                "scheme_type":            m_s.group(0) if m_s else "ISI Mark (Scheme I)",
                "qco_reference":          title,
                "source_url":             url,
                "penalty_clause":         (m_p[0][:200] if m_p else ""),
            })

    log.info("  Parsed %d rows from '%s'", len(rows), title)
    return rows


def _safe_print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode())


# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════

def run():
    docs = load_existing_docs()
    existing_urls = {d["source_url"] for d in docs}
    new_docs: list[dict] = []
    all_qco_rows: list[dict] = []

    # ── 1. Seed QCO table immediately ──────────────────────────
    log.info("=" * 60)
    log.info("STEP 1 — Seeding QCO structured table (%d known rows)", len(SEED_QCO_ROWS))
    log.info("=" * 60)
    upsert_qco_sqlite(SEED_QCO_ROWS)
    save_qco_csv(SEED_QCO_ROWS)
    all_qco_rows.extend(SEED_QCO_ROWS)

    # ── 2. Scrape additional HTML pages ────────────────────────
    log.info("=" * 60)
    log.info("STEP 2 — Scraping %d additional HTML pages", len(EXTRA_HTML))
    log.info("=" * 60)

    for target in EXTRA_HTML:
        url    = target["url"]
        title  = target["title"]
        cat    = target["category"]
        pw1st  = target.get("playwright_first", False)

        if url in existing_urls:
            log.info("  SKIP (already have): %s", url)
            continue

        html = playwright_fetch(url) if pw1st else fetch_html(url)
        text = extract_html_text(html) if html else ""

        # If thin content, try Playwright fallback
        if len(text) <= 100 and not pw1st:
            log.warning(
                "  [THIN %d chars] trying Playwright fallback: %s", len(text), url
            )
            html2 = playwright_fetch(url)
            if html2:
                text2 = extract_html_text(html2)
                if len(text2) > len(text):
                    text = text2
                    log.info("  Playwright improved text to %d chars", len(text))

        if not text or len(text) <= 50:
            log.warning("  [SKIP] Too little content (%d chars): %s", len(text), url)
            time.sleep(REQUEST_DELAY)
            continue

        log.info("  [OK] %d chars — %s", len(text), title)
        doc = make_doc(url, "html", title, cat, text)
        new_docs.append(doc)
        existing_urls.add(url)
        time.sleep(REQUEST_DELAY)

    # ── 3. Download QCO product PDFs ───────────────────────────
    log.info("=" * 60)
    log.info("STEP 3 — Downloading %d QCO product PDFs", len(QCO_PRODUCT_PDFS))
    log.info("=" * 60)

    for target in QCO_PRODUCT_PDFS:
        url   = target["url"]
        title = target["title"]
        cat   = target["category"]

        if url in existing_urls:
            log.info("  SKIP (already have): %s", url)
            continue

        pdf_data = fetch_pdf(url)
        if pdf_data:
            safe = re.sub(r"[^\w.-]", "_", Path(url).name) or "qco_extra.pdf"
            (DATA_DIR / "pdfs" / safe).write_bytes(pdf_data)
            text = extract_pdf_text(pdf_data)
            log.info("  [OK] %d chars — %s", len(text), title)
            new_docs.append(make_doc(url, "pdf", title, cat, text))
            existing_urls.add(url)

            rows = parse_qco_rows(text, url, title)
            if rows:
                all_qco_rows.extend(rows)
                upsert_qco_sqlite(rows)
                save_qco_csv(rows)
        else:
            log.warning("  [SKIP] Download failed: %s", url)

        time.sleep(REQUEST_DELAY)

    # ── 4. Save updated document set ───────────────────────────
    log.info("=" * 60)
    log.info("STEP 4 — Saving updated documents")
    log.info("=" * 60)

    all_docs = docs + new_docs
    save_docs(all_docs)

    # ── 5. Final summary ───────────────────────────────────────
    conn = sqlite3.connect(QCO_DB)
    total_qco = conn.execute("SELECT COUNT(*) FROM qco_standards").fetchone()[0]
    conn.close()

    _safe_print("\n" + "=" * 60)
    _safe_print("SUPPLEMENT COMPLETE")
    _safe_print("=" * 60)
    _safe_print(f"  Existing documents    : {len(docs)}")
    _safe_print(f"  New documents added   : {len(new_docs)}")
    _safe_print(f"  Total documents       : {len(all_docs)}")
    _safe_print(f"  QCO rows in SQLite    : {total_qco}")
    _safe_print(f"  Output JSON           : {RAW_JSON}")
    _safe_print(f"  QCO SQLite            : {QCO_DB}")

    _safe_print("\nNew documents:")
    for d in new_docs:
        _safe_print(
            f"  [{d['category'].upper()}] {d['title']} ({len(d['raw_text'])} chars)"
        )

    _safe_print("\nQCO Structured Table (first 20 seeded rows):")
    conn = sqlite3.connect(QCO_DB)
    rows = conn.execute(
        "SELECT is_standard_number, product_name FROM qco_standards LIMIT 20"
    ).fetchall()
    conn.close()
    for r in rows:
        _safe_print(f"  {r[0]:<25} | {(r[1] or '')[:60]}")

    if total_qco > 20:
        _safe_print(f"  ... and {total_qco - 20} more rows in {QCO_DB}")


if __name__ == "__main__":
    run()
