"""
Follow-up resolution: make context-dependent questions retrievable.

"Is it mandatory?" carries no product name, so embedding it alone retrieves
whatever is generically similar — usually the wrong document. The topic lives in
the previous turn, so it has to be folded into the text used for retrieval.

This is deliberately NOT an LLM rewrite. Retrieval (BM25 + cosine) is a
bag-of-terms match: it does not need a grammatical sentence, only the right
terms. Carrying terms forward is instant, deterministic, and cannot invent a
product or standard that was never discussed — whereas an LLM rewrite measured
6.8-9.1s per call on this stack and would double perceived latency on every
follow-up.

The original question is always what gets sent to the LLM; only the retrieval
query is augmented.
"""

import re
import logging

from backend.config import (
    IS_NUMBER_PATTERN, QCO_STOPWORDS,
    FOLLOWUP_MAX_TERMS, FOLLOWUP_LOOKBACK,
)

log = logging.getLogger("bis.followup")

_IS_RE = re.compile(IS_NUMBER_PATTERN)

# Words that only make sense relative to something said earlier.
_REFERENTIAL = re.compile(
    r"\b(it|its|it's|they|them|their|theirs|this|that|these|those|"
    r"same|there|above|previous|earlier|former|latter|one|ones|both)\b",
    re.I,
)

# Openings that continue a previous thought rather than starting a new one.
_ELLIPTIC_START = re.compile(
    r"^\s*(and|also|but|so|then|ok|okay|what about|how about|whats about|"
    r"what if|why|why not|how come|any|anything else)\b",
    re.I,
)

# Very short questions are almost always continuations ("how much?", "and silver?")
_SHORT_QUERY_WORDS = 4

# Conversational filler that carries no retrieval signal but survives
# QCO_STOPWORDS (which was tuned for product lookups, not dialogue).
_CARRY_NOISE = {
    "the", "and", "for", "are", "its", "covers", "cover", "covered",
    "specifies", "specified", "specify", "includes", "included", "including",
    "falls", "fall", "carry", "carries", "must", "should", "per", "also",
    "under", "used", "use", "using", "general", "regarding", "related",
    "tell", "explain", "know", "want", "need", "please", "thanks",
}


def needs_context(query: str, history: list[dict]) -> bool:
    """
    True when the question cannot stand on its own.

    Conservative by design: a self-contained question must never be polluted
    with terms from an unrelated earlier topic.
    """
    if not history:
        return False

    q = query.strip()
    if _REFERENTIAL.search(q):
        return True
    if _ELLIPTIC_START.match(q):
        return True
    if len(re.findall(r"\b\w+\b", q)) <= _SHORT_QUERY_WORDS:
        return True
    return False


def _content_terms(text: str) -> list[str]:
    """Product-ish words from a message, in order, deduplicated."""
    words = re.findall(r"\b[\w/]{3,}\b", (text or "").lower())
    out: list[str] = []
    for w in dict.fromkeys(words):
        if w in QCO_STOPWORDS or w in _CARRY_NOISE:
            continue
        if w.isdigit():          # bare years/numbers are handled via IS numbers
            continue
        out.append(w)
    return out


def _is_numbers(text: str) -> list[str]:
    """
    IS references in a message, each expanded into the forms retrieval uses.

    'IS 16102:2012' contributes both the full token (so the QCO lookup's
    `IS \\d+` rule fires) and the bare digits (so BM25 matches however the
    corpus happens to punctuate it).
    """
    out: list[str] = []
    for match in _IS_RE.findall(text or ""):
        token = " ".join(match.split())
        if token not in out:
            out.append(token)
        digits = re.search(r"\d{2,5}", match)
        if digits and digits.group(0) not in out:
            out.append(digits.group(0))
    return out


def topic_terms(history: list[dict], already_have: str = "") -> list[str]:
    """
    Terms that identify what the conversation is currently about.

    Priority order:
      1. IS numbers from recent turns — the most specific handle available,
         and they normally appear in the assistant's answer rather than the
         user's question.
      2. Content words from the most recent user message that yields any,
         since that is where the user established the topic.
      """
    recent = [m for m in history if m.get("content")][-FOLLOWUP_LOOKBACK:]
    if not recent:
        return []

    present = set(re.findall(r"\b[\w/]{3,}\b", already_have.lower()))
    terms: list[str] = []

    def add(candidates: list[str]) -> None:
        for term in candidates:
            if len(terms) >= FOLLOWUP_MAX_TERMS:
                return
            if term.lower() in present:
                continue
            if term not in terms:
                terms.append(term)

    # 1. IS numbers, newest turn first
    for msg in reversed(recent):
        add(_is_numbers(msg["content"]))

    # 2. Topic words from the latest user turn that has any
    for msg in reversed(recent):
        if msg.get("role") != "user":
            continue
        candidates = _content_terms(msg["content"])
        if candidates:
            add(candidates)
            break

    return terms[:FOLLOWUP_MAX_TERMS]


def resolve_query(query: str, history: list[dict]) -> tuple[str, list[str]]:
    """
    Return (query_to_retrieve_with, terms_carried_forward).

    When nothing needs carrying, the original query is returned untouched and
    the term list is empty — callers can use that to tell whether resolution
    actually happened.
    """
    query = query.strip()
    if not needs_context(query, history):
        return query, []

    terms = topic_terms(history, already_have=query)
    if not terms:
        log.info("Follow-up detected but no topic terms found: %r", query[:60])
        return query, []

    resolved = f"{query} {' '.join(terms)}"
    log.info("Follow-up resolved: %r + %s", query[:60], terms)
    return resolved, terms
