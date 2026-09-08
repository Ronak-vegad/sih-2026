"""
Query classifier: routes queries to the right pipeline.

Routes:
  structured_lookup  → product→standard deterministic QCO table query
  procedure_rag      → vector search on certification/procedure docs
  faq_rag            → vector search on FAQ/consumer docs
  out_of_scope       → politely decline
"""

import re
import logging

log = logging.getLogger("bis.classifier")

# ── Rule-based classifier ────────────────────────────────────────
# Ordered: more specific rules first

_STRUCTURED_PATTERNS = [
    r"\bIS\s*\d+",                               # "IS 302", "IS 1417:2016"
    r"\b(which|what|whats|what's)\s+(is\s+)?(indian\s+)?standard\b",
    r"\bstandard\b.{0,40}\b(for|of|applicable|applies|apply|cover|covers|governs|governing)\b",
    r"\b(applies|applicable)\b.{0,40}\b(standard|product|certification)\b",
    r"\bwhat (is|are) the (is|indian) standard",
    r"\bcertif(y|ied|ication).{0,20}\b(standard|is \d+)\b",
    r"\b(compulsory|mandatory|voluntary)\b.{0,40}\b(standard|certification|registration|is \d+|for|to sell|selling|import)\b",
    r"\bqco\b",
    r"\bquality control order\b",
    r"\b(covered|listed)\b.{0,20}\bunder\b.{0,25}\b(qco|crs|scheme|compulsory)\b",
    r"\b(product|item).{0,30}\b(standard|mark|is \d+)\b",
    r"\blist of products\b",
    r"\bwhich products\b.{0,30}\b(require|need|mandatory|covered)\b",
]

_PROCEDURE_PATTERNS = [
    r"\bhow (do|to|can)\b.{0,30}\b(apply|get|obtain|certif|register|licen)\b",
    r"\b(apply|application)\b.{0,30}\b(bis|certification|license|licence|hallmark)\b",
    r"\b(steps?|process|procedure)\b.{0,30}\b(certif|hallmark|register|licen)\b",
    r"\b(scheme [ivxlcdm]+|scheme i|scheme ii|fmcs|crs)\b",
    r"\bforeign manufacturer\b",
    r"\blicen[sc]e.{0,20}\bfee\b",
    r"\bhow (long|many days|much time)\b.{0,30}\b(certif|hallmark|licen)\b",
    r"\bdocument.{0,20}\b(required|needed)\b",
    r"\bsurveillance (audit|visit)\b",
]

_FAQ_PATTERNS = [
    r"\bwhat is\b.{0,30}\b(bis|hallmark|isi mark|certification|qco|crs)\b",
    r"\bhallmark\b",
    r"\bgold\b.{0,30}\b(purity|caratage|fineness|huid)\b",
    r"\bconsumer\b.{0,20}\b(complaint|grievance|care|bis care)\b",
    r"\bisi mark\b",
    r"\bhuid\b",
    r"\bbenef(it|its)\b.{0,30}\b(hallmark|certif|bis)\b",
    r"\bverif(y|ication)\b.{0,30}\b(hallmark|huid|isi)\b",
    r"\bcharges?\b.{0,30}\b(hallmark|certif)\b",
    r"\bfaq\b",
]

# Kept deliberately narrow: broad words like "film" or "sport" appear in real
# BIS queries ("plastic film standard", "sports helmet"), so they must not be
# treated as off-topic. Any pattern here is checked BEFORE the in-scope ones.
_OUT_OF_SCOPE_PATTERNS = [
    r"\b(gst rate|income tax|customs duty|tax slab|tds)\b",
    r"\b(legal advice|lawsuit|sue\s+(?:them|him|her|someone)|court case)\b",
    r"\b(stock market|share price|mutual fund|crypto|bitcoin)\b",
    r"\b(recipe|how to cook|restaurant near)\b",
    r"\b(weather (?:today|tomorrow|forecast)|cricket (?:score|match)|movie (?:ticket|review)|song|joke)\b",
    r"\b(who are you|your prompt|system prompt|ignore (?:all|previous) instructions)\b",
]


def classify_query(query: str) -> str:
    """
    Returns one of: 'structured_lookup', 'procedure_rag', 'faq_rag', 'out_of_scope'
    """
    q = query.lower().strip()

    for pat in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pat, q, re.I):
            log.info("Classifier → out_of_scope (pattern: %s)", pat)
            return "out_of_scope"

    for pat in _STRUCTURED_PATTERNS:
        if re.search(pat, q, re.I):
            log.info("Classifier → structured_lookup (pattern: %s)", pat)
            return "structured_lookup"

    for pat in _PROCEDURE_PATTERNS:
        if re.search(pat, q, re.I):
            log.info("Classifier → procedure_rag (pattern: %s)", pat)
            return "procedure_rag"

    for pat in _FAQ_PATTERNS:
        if re.search(pat, q, re.I):
            log.info("Classifier → faq_rag (pattern: %s)", pat)
            return "faq_rag"

    # Default: try faq_rag (most general)
    log.info("Classifier → faq_rag (default)")
    return "faq_rag"
