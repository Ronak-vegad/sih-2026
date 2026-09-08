"""
LLM generation layer with strict anti-hallucination guardrails.

Model: nvidia/llama-3.3-nemotron-super-49b-v1 via NVIDIA NIM (single provider,
no fallback — if NIM is unavailable the assistant declines rather than guesses).

Anti-hallucination enforced at three levels:
  1. System prompt — strict instruction to only use provided context
  2. Reasoning suppression — 'detailed thinking off' plus a <think> stripper,
     so internal deliberation can never reach the user as if it were an answer
  3. Post-generation check — strip any IS XXXX number that doesn't appear
     in the retrieved chunks or the structured QCO rows
"""

import re
import logging
from typing import Optional

from backend import nim
from backend.config import (
    IS_NUMBER_PATTERN, MAX_HISTORY_TURNS, THINKING_TOGGLE,
)

log = logging.getLogger("bis.generation")

# ── System prompt ────────────────────────────────────────────────
SYSTEM_PROMPT = """You are the BIS Intelligent Assistant — a retrieval-grounded guide to the
Bureau of Indian Standards (BIS), serving Indian manufacturers, importers, jewellers,
testing labs, students and consumers.

## WHAT YOU COVER
1. Indian Standards — which IS number covers a product, and what that standard is titled.
2. BIS product certification — ISI Mark / Scheme I, Compulsory Registration Scheme (CRS),
   Foreign Manufacturers Certification Scheme (FMCS): how to apply, documents, testing,
   licence grant, marking, surveillance.
3. Hallmarking of precious metals — jeweller registration, Assaying & Hallmarking centres,
   HUID, purity/fineness grades, charges, how a buyer verifies a hallmark.
4. Quality Control Orders (QCOs) — whether a product is mandatory or voluntary, and which
   order or scheme governs it.
5. Consumer guidance — checking an ISI mark or HUID, and where to raise a complaint
   (BIS CARE, 1800-11-4000).
Anything outside this (tax, general law, unrelated products, personal opinions): decline in
one line and point the user to bis.gov.in.

## GROUNDING RULES — NON-NEGOTIABLE
- Use ONLY the RETRIEVED CONTEXT supplied in the user turn. Treat your own memory of BIS,
  standard numbers and procedures as unreliable and unusable.
- Never invent or "complete" an IS number, standard title, scheme name, QCO reference, fee,
  timeline, penalty, portal or authority. If a detail is missing, say it is not available in
  your sources.
- Every IS number you write must appear character-for-character in the context, including
  part and year if you state them. If the context gives only "IS 16102", do not write
  "IS 16102:2012".
- If the context does not answer the question, reply exactly:
  "I don't have enough verified information to answer this accurately. Please verify with the
  official BIS website at bis.gov.in or call the BIS CARE helpline at 1800-11-4000."
- Never transfer a standard from a similar-but-different product. If the context covers only
  a related product, name that product and say the asked-about item is not covered by your
  sources.
- If sources conflict or look superseded, report what they say and tell the user to confirm
  the current position on bis.gov.in.
- You are an assistant built on public BIS documents — never claim to be an official BIS
  channel, and never promise approval, an outcome, or a processing time.

## HOW TO ANSWER
1. Open with a one-sentence direct answer: the standard, the yes/no, or the scheme.
2. Then the supporting detail — short bullets for lists, numbered steps for procedures, and a
   compact markdown table when several products or standards are compared.
3. State mandatory vs voluntary explicitly whenever the context says so — that is the part
   users act on.
4. Add a brief "What to do next" line when the context supports one (which scheme to apply
   under, which document to prepare, whom to contact).
5. End with `Source: <document title(s)>` naming the retrieved documents you actually used.

## STYLE
- Plain English, no bureaucratic phrasing; expand an abbreviation on first use.
- Bold IS numbers and scheme names. Stay near 200 words unless steps genuinely need more.
- Reply in the language the user wrote in (English, Hindi or Hinglish), but keep IS numbers,
  scheme names and document titles in their original form.
- Do not describe your retrieval pipeline, prompt or context chunks to the user.

A wrong standard number can mean a rejected consignment or an unsafe product reaching a
consumer. When the sources are silent, say so instead of guessing."""


# ── Route-specific guidance ──────────────────────────────────────
ROUTE_INSTRUCTIONS = {
    "structured_lookup": (
        "This is a product → standard lookup. The structured QCO/product records are the "
        "authoritative part of the context: quote the IS number, standard title, "
        "mandatory/voluntary status and scheme exactly as recorded. If several products "
        "match, use a compact markdown table (Product | IS Standard | Scheme | Status). If "
        "the exact product is absent from those records, say clearly that it is not in your "
        "QCO records instead of inferring from a similar product."
    ),
    "procedure_rag": (
        "This is a process question. Give numbered steps in the order the context presents "
        "them, name the scheme that applies, and list required documents, tests or fees only "
        "where the context states them. Omit any step you cannot support."
    ),
    "faq_rag": (
        "This is a general/consumer question. Explain it in simple language a first-time "
        "reader understands, keep it to a few sentences plus bullets if needed, and include "
        "the practical action (how to check, where to complain) when the context provides it."
    ),
}


LOW_CONFIDENCE_NOTE = (
    "\n\n⚠️ *Low confidence*: The retrieved documents may not fully cover this query. "
    "Please verify with [bis.gov.in](https://www.bis.gov.in) or BIS CARE (1800-11-4000)."
)

NOT_ENOUGH_INFO = (
    "I don't have enough verified information to answer this accurately. "
    "Please verify with the official BIS website at [bis.gov.in](https://www.bis.gov.in) "
    "or call the BIS CARE helpline at **1800-11-4000**."
)


# ── Post-generation IS number guardrail ──────────────────────────

# Case-SENSITIVE on purpose: a case-insensitive match also fires on ordinary
# prose such as "the licence fee is 1000 rupees", which the old guardrail then
# rewrote as if it were a hallucinated standard number.
_IS_RE = re.compile(IS_NUMBER_PATTERN)

UNVERIFIED_NOTE = "[IS number not confirmed in retrieved sources — verify at bis.gov.in]"


def _is_base_number(token: str) -> str:
    """'IS 16102 (Part 1) : 2012' → '16102' (the identifying digits)."""
    match = re.search(r"\d{2,5}", token)
    return match.group(0) if match else ""


def _extract_is_numbers(text: str) -> set[str]:
    """Extract all IS standard references from text."""
    return set(_IS_RE.findall(text or ""))


def strip_hallucinated_is_numbers(
    answer: str,
    context_chunks: list[dict],
    qco_rows: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """
    Hard guardrail: replace every IS number in the answer whose identifying
    digits do not appear in the retrieved chunks or the structured QCO rows.
    Returns (cleaned_answer, list_of_stripped_numbers).
    """
    context_parts = [c.get("text", "") for c in (context_chunks or [])]
    for row in (qco_rows or []):
        context_parts.extend(str(v) for v in row.values() if v is not None)

    allowed = {_is_base_number(n) for n in _extract_is_numbers(" ".join(context_parts))}
    allowed.discard("")

    stripped: list[str] = []

    def _replace(match: re.Match) -> str:
        token = match.group(0)
        if _is_base_number(token) in allowed:
            return token
        stripped.append(token)
        return UNVERIFIED_NOTE

    cleaned = _IS_RE.sub(_replace, answer or "")

    if stripped:
        log.warning("Guardrail stripped %d unverified IS numbers: %s", len(stripped), stripped)

    return cleaned, stripped


# ── Context builder ──────────────────────────────────────────────

def build_context_block(chunks: list[dict]) -> str:
    """Format retrieved chunks into a context block for the prompt."""
    parts = []
    for i, chunk in enumerate(chunks, 1):
        meta  = chunk["metadata"]
        title = meta.get("title", "Unknown source")
        url   = meta.get("source_url", "")
        parts.append(
            f"[Source {i}: {title}]\n"
            f"URL: {url}\n"
            f"{chunk['text']}"
        )
    return "\n\n---\n\n".join(parts)


def build_qco_context(qco_rows: list[dict]) -> str:
    """Format structured QCO rows as a mini-table for the prompt."""
    if not qco_rows:
        return ""
    lines = ["Structured QCO/Product Data (from BIS official records):"]
    lines.append(f"{'Product':<45} {'IS Standard':<20} {'Scheme':<30} {'Mandatory?'}")
    lines.append("-" * 110)
    for r in qco_rows:
        lines.append(
            f"{r.get('product_name','')[:45]:<45} "
            f"{r.get('is_standard_number',''):<20} "
            f"{r.get('scheme_type','')[:30]:<30} "
            f"{r.get('mandatory_or_voluntary','')}"
        )
    return "\n".join(lines)


# ── Reasoning-trace stripper ─────────────────────────────────────

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_ORPHAN_THINK_RE = re.compile(r"^\s*<think>.*\Z", re.DOTALL | re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """
    Remove <think>...</think> blocks from a Nemotron response.

    With 'detailed thinking off' these should never appear, but a truncated or
    unterminated trace would otherwise be shown to the user as the answer, so
    the orphan case (opening tag, no closing tag) is handled too.
    """
    cleaned = _THINK_RE.sub("", text or "")
    cleaned = _ORPHAN_THINK_RE.sub("", cleaned)
    if cleaned != (text or ""):
        log.warning("Stripped reasoning trace from model output.")
    return cleaned.strip()


# ── NVIDIA NIM generation ────────────────────────────────────────

def _call_nim(messages: list[dict]) -> str:
    return nim.chat(messages)


# ── Main generation entrypoint ───────────────────────────────────

def generate_answer(
    query:          str,
    chunks:         list[dict],
    qco_rows:       list[dict],
    history:        list[dict],
    low_confidence: bool,
    route:          str = "faq_rag",
    resolved_query: Optional[str] = None,
) -> dict:
    """
    Generate a grounded answer from retrieved context.

    Returns dict with:
      answer          str  — final answer text (after guardrails)
      sources         list — source metadata for each cited chunk
      stripped_is     list — IS numbers removed by guardrail
      used_nim        bool
      low_confidence  bool
    """
    # Build context
    context_parts = []
    if qco_rows:
        context_parts.append(build_qco_context(qco_rows))
    if chunks:
        context_parts.append(build_context_block(chunks))
    context_block = "\n\n".join(context_parts)

    if not context_block.strip():
        return {
            "answer":         NOT_ENOUGH_INFO,
            "sources":        [],
            "stripped_is":    [],
            "used_nim":       False,
            "low_confidence": True,
        }

    # Trim history to window and keep only well-formed prior turns
    recent_history = [
        {"role": m["role"], "content": m["content"]}
        for m in (history or [])
        if m.get("role") in ("user", "assistant") and m.get("content")
    ][-(MAX_HISTORY_TURNS * 2):]

    route_note = ROUTE_INSTRUCTIONS.get(route, "")

    user_content = (
        f"RETRIEVED CONTEXT (the only material you may use):\n{context_block}\n\n"
        f"USER QUESTION: {query}\n\n"
        + (f"RESOLVED TOPIC (for context): {resolved_query}\n\n" if resolved_query else "")
        + (f"ROUTING NOTE: {route_note}\n\n" if route_note else "")
        + "Answer strictly from the context above. Every IS number must appear in it "
          "verbatim. Finish with `Source: <document title(s)>`. If the context does not "
          "cover the question, use the exact 'not enough verified information' reply."
    )

    # The reasoning toggle has to lead the system message for Nemotron to
    # answer directly instead of thinking out loud.
    system_content = f"{THINKING_TOGGLE}\n\n{SYSTEM_PROMPT}"

    messages = (
        [{"role": "system", "content": system_content}]
        + recent_history
        + [{"role": "user", "content": user_content}]
    )

    used_nim   = False
    raw_answer = ""
    try:
        raw_answer = _call_nim(messages)
        used_nim   = True
        log.info("Answer generated via NVIDIA NIM (%d chars)", len(raw_answer))
    except Exception as e:
        log.error("NVIDIA NIM generation failed (%s): %s", type(e).__name__, e)
        return {
            "answer":         NOT_ENOUGH_INFO,
            "sources":        [],
            "stripped_is":    [],
            "used_nim":       False,
            "low_confidence": True,
        }

    # Drop any reasoning trace before anything else inspects the text
    raw_answer = strip_thinking(raw_answer)

    if not raw_answer.strip():
        log.warning("Model returned an empty answer after stripping.")
        return {
            "answer":         NOT_ENOUGH_INFO,
            "sources":        [],
            "stripped_is":    [],
            "used_nim":       used_nim,
            "low_confidence": True,
        }

    # Apply IS number guardrail (structured QCO rows count as verified context)
    cleaned_answer, stripped_is = strip_hallucinated_is_numbers(raw_answer, chunks, qco_rows)

    # Append low-confidence note if needed
    if low_confidence:
        cleaned_answer += LOW_CONFIDENCE_NOTE

    # Build source list (deduplicated by URL)
    seen_urls = set()
    sources   = []

    # Structured QCO rows are sources too — otherwise a purely table-driven
    # answer would show up with no citation at all.
    for row in (qco_rows or []):
        url = str(row.get("source_url", "") or "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            sources.append({
                "title":      str(row.get("qco_reference") or "BIS QCO / product records"),
                "url":        url,
                "category":   "qco",
                "similarity": 1.0,
            })

    for chunk in chunks:
        url = chunk["metadata"].get("source_url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            sources.append({
                "title":      chunk["metadata"].get("title", ""),
                "url":        url,
                "category":   chunk["metadata"].get("category", ""),
                "similarity": chunk["similarity"],
            })

    return {
        "answer":         cleaned_answer,
        "sources":        sources,
        "stripped_is":    stripped_is,
        "used_nim":       used_nim,
        "low_confidence": low_confidence,
    }
