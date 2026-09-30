"""
BIS-RAG Evaluation Pipeline
============================

Runs the golden question set against the live /chat endpoint and computes:

  Retrieval metrics:
    - retrieval_recall_at_k   : was the expected IS number in top-k retrieved chunks?
    - mrr                     : mean reciprocal rank of the first correct source chunk

  Generation metrics:
    - faithfulness            : LLM-as-judge — does the answer contain only claims
                                supported by the retrieved context?
    - answer_relevance        : does the answer address the question asked?
    - citation_accuracy       : every IS number in the answer appears in retrieved context
    - refusal_accuracy        : out-of-scope / trick questions are correctly declined

  Routing metric:
    - route_accuracy          : was the query sent to the expected route?

Usage:
    # Run all questions against the live API
    python eval/run_eval.py

    # Run only a specific category
    python eval/run_eval.py --category out_of_scope

    # Save results with a custom tag
    python eval/run_eval.py --tag v2-reranker

    # CI mode — exits with code 1 if any threshold is breached
    python eval/run_eval.py --ci

Thresholds (fail CI if below):
    retrieval_recall_at_k >= 0.70
    mrr                   >= 0.60
    faithfulness          >= 0.80
    answer_relevance      >= 0.75
    citation_accuracy     >= 0.85
    refusal_accuracy      >= 0.90
    route_accuracy        >= 0.80

Results are stored as JSON Lines in eval/results/<timestamp>_<tag>.jsonl
A summary JSON is written to eval/results/<timestamp>_<tag>_summary.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

# ── Config ───────────────────────────────────────────────────────
BASE_URL = os.getenv("EVAL_BASE_URL", "http://localhost:8000")
CHAT_ENDPOINT = f"{BASE_URL}/chat"
TIMEOUT_SECONDS = 60
TOP_K = 5  # match TOP_K_FINAL in backend config

# Thresholds for CI gate
THRESHOLDS = {
    "retrieval_recall_at_k": 0.70,
    "mrr": 0.60,
    "faithfulness": 0.80,
    "answer_relevance": 0.75,
    "citation_accuracy": 0.85,
    "refusal_accuracy": 0.90,
    "route_accuracy": 0.80,
}

RESULTS_DIR = Path(__file__).parent / "results"
QUESTIONS_FILE = Path(__file__).parent / "golden_questions.json"

# IS number regex (same as backend/config.py)
_IS_RE = re.compile(
    r"\bIS(?:\s*/\s*(?:IEC|ISO))?\s*\d{1,5}"
    r"(?:\s*\(\s*Part\s*[0-9IVXivx]+\s*\))?"
    r"(?:\s*[:\-]\s*\d{4})?",
    re.IGNORECASE,
)

NOT_ENOUGH_INFO_PHRASES = [
    "not enough verified information",
    "cannot find",
    "outside my scope",
    "not in my sources",
    "not available in",
    "verify with the official",
    "designed to answer questions about bureau of indian standards",
]


# ── Helpers ──────────────────────────────────────────────────────

def load_questions(path: Path, category: Optional[str] = None) -> list[dict]:
    """Load and optionally filter golden questions."""
    with open(path, encoding="utf-8") as f:
        questions = json.load(f)
    if category:
        questions = [q for q in questions if q.get("category") == category]
    return questions


def call_chat(question: str) -> tuple[dict, float]:
    """
    Call the /chat endpoint and return (response_json, elapsed_seconds).
    Returns an error dict on failure.
    """
    start = time.monotonic()
    try:
        resp = httpx.post(
            CHAT_ENDPOINT,
            json={"query": question, "history": []},
            timeout=TIMEOUT_SECONDS,
        )
        elapsed = time.monotonic() - start
        if resp.status_code == 200:
            return resp.json(), elapsed
        return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}, elapsed
    except Exception as exc:
        elapsed = time.monotonic() - start
        return {"error": str(exc)}, elapsed


def extract_is_numbers(text: str) -> set[str]:
    """Extract base IS numbers (digits only) from text."""
    return {
        re.search(r"\d{1,5}", m.group(0)).group(0)
        for m in _IS_RE.finditer(text)
        if re.search(r"\d{1,5}", m.group(0))
    }


def is_refusal(answer: str) -> bool:
    """True if the answer is a refusal / not-enough-info response."""
    lower = answer.lower()
    return any(phrase in lower for phrase in NOT_ENOUGH_INFO_PHRASES)


# ── Per-question metrics ─────────────────────────────────────────

def compute_metrics(question: dict, response: dict, elapsed: float) -> dict:
    """
    Compute all metrics for a single question-response pair.

    Returns a flat dict of metric values (all floats or None).
    """
    q_id         = question["id"]
    expected_is  = question.get("expected_source_is_number") or ""
    must_refuse  = question.get("must_refuse", False)
    expected_route = question.get("expected_route", "")
    expected_contains = question.get("expected_answer_contains", [])

    # Short-circuit on API error
    if "error" in response:
        return {
            "question_id":        q_id,
            "error":              response["error"],
            "retrieval_recall":   None,
            "mrr":                None,
            "faithfulness":       None,
            "answer_relevance":   None,
            "citation_accuracy":  None,
            "refusal_correct":    None,
            "route_correct":      None,
            "latency_s":          elapsed,
        }

    answer  = response.get("answer", "")
    sources = response.get("sources", [])
    route   = response.get("route", "")
    conf    = response.get("confidence", 0.0)

    # ── Route accuracy ────────────────────────────────────────────
    route_correct = (route == expected_route) if expected_route else None

    # ── Refusal accuracy ──────────────────────────────────────────
    refused = is_refusal(answer)
    if must_refuse:
        refusal_correct = 1.0 if refused else 0.0
    else:
        refusal_correct = 0.0 if refused else 1.0  # should NOT have refused

    # ── Retrieval recall@k ────────────────────────────────────────
    # "Did the expected IS number appear in any retrieved source?"
    if expected_is:
        expected_base = re.search(r"\d{1,5}", expected_is)
        expected_base = expected_base.group(0) if expected_base else ""
        source_text = " ".join(
            s.get("title", "") + " " + s.get("url", "") for s in sources
        )
        # Also search the answer (guardrail may have removed IS from answer but
        # sources still carry the title which usually names the IS number).
        recall_at_k = 1.0 if expected_base and expected_base in source_text else 0.0
    else:
        recall_at_k = None  # not applicable for this question

    # ── MRR ───────────────────────────────────────────────────────
    mrr_score = None
    if expected_is:
        expected_base = re.search(r"\d{1,5}", expected_is)
        expected_base = expected_base.group(0) if expected_base else ""
        for rank, source in enumerate(sources, 1):
            src_text = source.get("title", "") + source.get("url", "")
            if expected_base and expected_base in src_text:
                mrr_score = 1.0 / rank
                break
        if mrr_score is None:
            mrr_score = 0.0

    # ── Answer relevance (heuristic) ─────────────────────────────
    # Check if expected_answer_contains phrases appear in the answer
    if expected_contains and not must_refuse:
        hits = sum(
            1 for phrase in expected_contains
            if phrase.lower() in answer.lower()
        )
        answer_relevance = hits / len(expected_contains)
    elif must_refuse:
        answer_relevance = 1.0 if refused else 0.0
    else:
        answer_relevance = None  # no expected content to check

    # ── Citation accuracy ─────────────────────────────────────────
    # Every IS number in the answer must be in the stripped_is list
    # (guardrail-approved) OR in source titles.
    # stripped_is = IS numbers that were REMOVED — so they should NOT appear in
    # a clean answer. We check that IS numbers in the answer are not in stripped_is.
    stripped_is = set(response.get("stripped_is", []))
    answer_is_numbers = extract_is_numbers(answer)
    if answer_is_numbers:
        bad = {n for n in answer_is_numbers if any(s in stripped_is for s in stripped_is)}
        citation_accuracy = 1.0 - (len(bad) / len(answer_is_numbers))
    elif stripped_is:
        citation_accuracy = 0.0  # guardrail found problems
    else:
        citation_accuracy = 1.0  # no IS numbers, no problem

    # ── Faithfulness (heuristic — without LLM judge) ──────────────
    # As a heuristic proxy (full LLM-as-judge is the optional --llm-judge flag):
    # If any IS number was stripped → faithfulness penalty.
    # If low_confidence is True but we gave a non-refusal → slight penalty.
    faith_score = 1.0
    if stripped_is:
        faith_score -= 0.3 * min(len(stripped_is), 3)  # max -0.9
    faith_score = max(0.0, faith_score)

    return {
        "question_id":       q_id,
        "question":          question["question"],
        "category":          question["category"],
        "difficulty":        question["difficulty"],
        "expected_route":    expected_route,
        "actual_route":      route,
        "route_correct":     int(route_correct) if route_correct is not None else None,
        "refusal_correct":   refusal_correct,
        "retrieval_recall":  recall_at_k,
        "mrr":               mrr_score,
        "answer_relevance":  answer_relevance,
        "citation_accuracy": citation_accuracy,
        "faithfulness":      faith_score,
        "confidence":        conf,
        "latency_s":         round(elapsed, 3),
        "answer_preview":    answer[:300],
        "sources_count":     len(sources),
        "stripped_is":       list(stripped_is),
        "error":             None,
    }


# ── LLM-as-judge (optional) ──────────────────────────────────────

def llm_judge_faithfulness(
    question: str, answer: str, sources: list[dict],
    groq_api_key: str,
) -> float:
    """
    Use Groq (LLaMA 3.1 8B) to judge whether the answer is supported by sources.
    Returns a score 0.0–1.0.
    """
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=groq_api_key,
        )
        context_text = "\n\n".join(
            f"[Source {i}: {s['title']}]"
            for i, s in enumerate(sources, 1)
        )
        judge_prompt = (
            "You are a faithfulness judge. You will be given a QUESTION, ANSWER, and SOURCE LIST.\n\n"
            f"QUESTION: {question}\n\n"
            f"ANSWER: {answer[:800]}\n\n"
            f"SOURCES: {context_text[:800]}\n\n"
            "Is the answer supported ONLY by the sources, with no invented facts?\n"
            "Reply with a single number 0.0 to 1.0 where 1.0 = fully supported, 0.0 = not supported at all.\n"
            "Reply with the number only, no explanation."
        )
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": judge_prompt}],
            temperature=0.0,
            max_tokens=10,
        )
        score_str = resp.choices[0].message.content.strip()
        return float(score_str)
    except Exception as exc:
        print(f"  [judge] Error: {exc}")
        return None


# ── Aggregate metrics ─────────────────────────────────────────────

def aggregate(results: list[dict]) -> dict:
    """Compute aggregate metrics from per-question results."""
    def mean(values):
        vals = [v for v in values if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    return {
        "total_questions":        len(results),
        "errors":                 sum(1 for r in results if r.get("error")),
        "retrieval_recall_at_k":  mean(r["retrieval_recall"] for r in results),
        "mrr":                    mean(r["mrr"] for r in results),
        "faithfulness":           mean(r["faithfulness"] for r in results),
        "answer_relevance":       mean(r["answer_relevance"] for r in results),
        "citation_accuracy":      mean(r["citation_accuracy"] for r in results),
        "refusal_accuracy":       mean(r["refusal_correct"] for r in results),
        "route_accuracy":         mean(r["route_correct"] for r in results),
        "mean_latency_s":         mean(r["latency_s"] for r in results),
        "p95_latency_s":          _percentile(
                                     [r["latency_s"] for r in results if r.get("latency_s")], 95
                                 ),
        "by_category": _by_category(results),
        "by_difficulty": _by_difficulty(results),
    }


def _percentile(values: list[float], pct: int) -> Optional[float]:
    if not values:
        return None
    sorted_v = sorted(values)
    idx = max(0, int(len(sorted_v) * pct / 100) - 1)
    return round(sorted_v[idx], 3)


def _by_category(results: list[dict]) -> dict:
    cats: dict[str, list] = {}
    for r in results:
        cat = r.get("category", "unknown")
        cats.setdefault(cat, []).append(r)
    out = {}
    for cat, rlist in cats.items():
        out[cat] = {
            "count":           len(rlist),
            "refusal_accuracy": _mean([r["refusal_correct"] for r in rlist]),
            "answer_relevance": _mean([r["answer_relevance"] for r in rlist]),
            "route_accuracy":   _mean([r["route_correct"] for r in rlist]),
        }
    return out


def _by_difficulty(results: list[dict]) -> dict:
    diffs: dict[str, list] = {}
    for r in results:
        d = r.get("difficulty", "unknown")
        diffs.setdefault(d, []).append(r)
    return {
        d: {
            "count":           len(rlist),
            "answer_relevance": _mean([r["answer_relevance"] for r in rlist]),
            "faithfulness":    _mean([r["faithfulness"] for r in rlist]),
        }
        for d, rlist in diffs.items()
    }


def _mean(values) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


# ── CI gate ───────────────────────────────────────────────────────

def check_thresholds(summary: dict) -> list[str]:
    """
    Return list of threshold violations. Empty list = all pass.
    """
    failures = []
    for metric, threshold in THRESHOLDS.items():
        value = summary.get(metric)
        if value is None:
            continue
        if value < threshold:
            failures.append(
                f"  ❌ {metric}: {value:.4f} < threshold {threshold:.2f}"
            )
    return failures


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BIS-RAG Evaluation Pipeline")
    parser.add_argument("--category",  help="Only run questions in this category")
    parser.add_argument("--tag",       default="", help="Tag appended to result filename")
    parser.add_argument("--ci",        action="store_true", help="Exit 1 if thresholds not met")
    parser.add_argument("--llm-judge", action="store_true", help="Use Groq LLM for faithfulness scoring")
    parser.add_argument("--limit",     type=int, default=None, help="Only run first N questions")
    parser.add_argument("--base-url",  default=BASE_URL, help="Backend base URL")
    args = parser.parse_args()

    global CHAT_ENDPOINT
    CHAT_ENDPOINT = f"{args.base_url.rstrip('/')}/chat"

    groq_key = os.getenv("GROQ_API_KEY", "")

    print(f"\n{'='*60}")
    print("  BIS-RAG Evaluation Pipeline")
    print(f"  Endpoint : {CHAT_ENDPOINT}")
    print(f"  Time     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.category:
        print(f"  Category : {args.category}")
    print(f"{'='*60}\n")

    questions = load_questions(QUESTIONS_FILE, category=args.category)
    if args.limit:
        questions = questions[:args.limit]

    print(f"Running {len(questions)} questions...\n")

    results = []
    for i, q in enumerate(questions, 1):
        print(f"  [{i:>3}/{len(questions)}] {q['id']} | {q['category']:20s} | {q['question'][:55]}", end="")
        sys.stdout.flush()

        response, elapsed = call_chat(q["question"])

        if "error" in response:
            print(f"  ⚠ ERROR: {response['error']}")
        else:
            print(f"  ({elapsed:.1f}s) route={response.get('route','?')} conf={response.get('confidence',0):.2f}")

        metrics = compute_metrics(q, response, elapsed)

        # Optional LLM faithfulness judge
        if args.llm_judge and groq_key and "error" not in response:
            answer  = response.get("answer", "")
            sources = response.get("sources", [])
            faith   = llm_judge_faithfulness(q["question"], answer, sources, groq_key)
            if faith is not None:
                metrics["faithfulness"] = faith

        results.append(metrics)

    # Compute aggregate
    summary = aggregate(results)

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = f"_{args.tag}" if args.tag else ""
    cat = f"_{args.category}" if args.category else ""
    result_file   = RESULTS_DIR / f"{ts}{cat}{tag}.jsonl"
    summary_file  = RESULTS_DIR / f"{ts}{cat}{tag}_summary.json"

    with open(result_file, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary["run"] = {
        "timestamp": ts,
        "tag": args.tag,
        "category_filter": args.category,
        "endpoint": CHAT_ENDPOINT,
        "question_count": len(questions),
        "llm_judge": args.llm_judge,
    }
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # Print summary
    print(f"\n{'='*60}")
    print("  RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"  Total questions  : {summary['total_questions']}")
    print(f"  Errors           : {summary['errors']}")
    print()
    print(f"  {'Metric':<30} {'Score':>8}   {'Threshold':>10}   {'Status'}")
    print(f"  {'-'*60}")
    for metric, threshold in THRESHOLDS.items():
        value = summary.get(metric)
        if value is None:
            print(f"  {metric:<30} {'N/A':>8}   {threshold:>10.2f}   -- (no data)")
        else:
            status = "PASS" if value >= threshold else "FAIL"
            print(f"  {metric:<30} {value:>8.4f}   {threshold:>10.2f}   {status}")
    print()
    print(f"  Mean latency     : {summary.get('mean_latency_s', 0):.2f}s")
    print(f"  p95  latency     : {summary.get('p95_latency_s', 0):.2f}s")
    print()
    print(f"  Results  : {result_file}")
    print(f"  Summary  : {summary_file}")
    print(f"{'='*60}\n")

    # By-category breakdown
    if summary.get("by_category"):
        print("  BY CATEGORY")
        print(f"  {'-'*50}")
        for cat, cat_metrics in summary["by_category"].items():
            ref_val = cat_metrics.get("refusal_accuracy")
            rel_val = cat_metrics.get("answer_relevance")
            print(
                f"  {cat:<25} n={cat_metrics['count']:>3}  "
                f"refusal={ref_val if ref_val is not None else 'N/A'}  "
                f"relevance={rel_val if rel_val is not None else 'N/A'}"
            )
        print()

    # CI gate
    if args.ci:
        failures = check_thresholds(summary)
        if failures:
            print("  CI GATE FAILED -- the following thresholds were not met:")
            for f in failures:
                print(f)
            print()
            sys.exit(1)
        else:
            print("  CI GATE PASSED -- all thresholds met.\n")
            sys.exit(0)


if __name__ == "__main__":
    main()
