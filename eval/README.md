# BIS-RAG Evaluation System

This directory contains the golden question set and evaluation pipeline for the BIS Intelligent Assistant.

---

## Files

```
eval/
├── golden_questions.json   # 110 golden questions across 8 categories
├── run_eval.py             # Eval runner + CI gate
└── results/                # Per-run JSONL results + summary JSON (auto-created)
```

---

## Question Categories

| Category | Count | Description |
|---|---|---|
| `product_standard` | ~25 | "Which IS standard covers X?" — tests structured QCO lookup |
| `mandatory_check` | ~10 | "Is certification mandatory for X?" — tests mandatory/voluntary accuracy |
| `factual_faq` | ~20 | Factual questions about BIS, hallmarking, schemes |
| `certification_procedure` | ~12 | "How do I apply for..." — tests procedure RAG |
| `consumer_guidance` | ~8 | Consumer-facing questions |
| `cross_standard` | ~6 | Comparisons between standards or schemes |
| `clause_lookup` | ~5 | Specific clause or section lookups |
| `hinglish` | ~5 | Hindi/Hinglish queries |
| `out_of_scope` | ~10 | Questions that must be refused |
| `trick` | ~10 | Prompt injection, false claims, jailbreak attempts |

---

## Metrics

| Metric | Description | Threshold |
|---|---|---|
| `retrieval_recall_at_k` | Was the expected IS number in any retrieved source? | ≥ 0.70 |
| `mrr` | Mean Reciprocal Rank of correct source | ≥ 0.60 |
| `faithfulness` | No hallucinated IS numbers in answer (guardrail-based) | ≥ 0.80 |
| `answer_relevance` | Expected phrases present in answer | ≥ 0.75 |
| `citation_accuracy` | IS numbers in answer not stripped by guardrail | ≥ 0.85 |
| `refusal_accuracy` | Out-of-scope / trick questions correctly refused | ≥ 0.90 |
| `route_accuracy` | Query routed to expected pipeline | ≥ 0.80 |

---

## Usage

### Prerequisites

Start the backend first:
```bash
# Activate your venv
venv\Scripts\activate   # Windows

# Start the server
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### Run full eval
```bash
python eval/run_eval.py
```

### Run one category only
```bash
python eval/run_eval.py --category out_of_scope
python eval/run_eval.py --category product_standard
```

### Run with a version tag
```bash
python eval/run_eval.py --tag v2-reranker
```

### CI mode (exits 1 if thresholds not met)
```bash
python eval/run_eval.py --ci
```

### Use LLM-as-judge for faithfulness (more accurate but slower)
```bash
python eval/run_eval.py --llm-judge
```

### Limit to first N questions (for quick smoke tests)
```bash
python eval/run_eval.py --limit 20
```

---

## Results Format

### Per-run JSONL (`results/<timestamp>.jsonl`)
One line per question with all metric values:
```json
{
  "question_id": "Q001",
  "question": "Which IS standard applies to LED bulbs?",
  "category": "product_standard",
  "difficulty": "easy",
  "expected_route": "structured_lookup",
  "actual_route": "structured_lookup",
  "route_correct": 1,
  "refusal_correct": 1.0,
  "retrieval_recall": 1.0,
  "mrr": 1.0,
  "answer_relevance": 1.0,
  "citation_accuracy": 1.0,
  "faithfulness": 1.0,
  "confidence": 0.95,
  "latency_s": 1.23,
  "answer_preview": "The standard for LED bulbs...",
  "sources_count": 3,
  "stripped_is": []
}
```

### Summary JSON (`results/<timestamp>_summary.json`)
Aggregate metrics + per-category and per-difficulty breakdowns.

---

## Comparing Versions

```bash
# Run eval before a change
python eval/run_eval.py --tag v1-baseline

# Make your change (e.g. add reranker)

# Run eval after
python eval/run_eval.py --tag v2-reranker

# Compare
python - <<'EOF'
import json
from pathlib import Path

results = sorted(Path("eval/results").glob("*_summary.json"))
for f in results[-2:]:
    s = json.loads(f.read_text())
    print(f"\n{f.stem}")
    for k in ["retrieval_recall_at_k","mrr","faithfulness","answer_relevance"]:
        print(f"  {k}: {s.get(k)}")
EOF
```

---

## Adding New Questions

Edit `eval/golden_questions.json`. Each question must have:
```json
{
  "id": "Q111",                         // unique ID, incrementing
  "category": "product_standard",       // one of the categories above
  "difficulty": "easy|medium|hard",
  "question": "...",
  "expected_answer_contains": ["IS 16102"],  // substrings that must appear in answer
  "expected_route": "structured_lookup",    // expected routing pipeline
  "expected_source_is_number": "IS 16102",  // for retrieval recall; null if N/A
  "expected_source_clause": null,           // clause number if known
  "must_refuse": false,                     // true for out-of-scope/trick
  "notes": "Explanation for auditors"
}
```

---

## CI Integration

The eval gate runs automatically on every push to `main` via `.github/workflows/ci.yml`.
Results are uploaded as GitHub Actions artifacts and retained for 30 days.

To add secrets, go to **GitHub → Settings → Secrets → Actions** and add:
- `GROQ_API_KEY`
- `GOOGLE_API_KEY`
