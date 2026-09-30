import json
from pathlib import Path

results_dir = Path("eval/results")
summary_files = sorted(results_dir.glob("*_summary.json"))
if not summary_files:
    print("No summary files found")
else:
    latest = summary_files[-1]
    print("Summary file:", latest.name)
    s = json.loads(latest.read_text(encoding="utf-8"))

    thresholds = {
        "retrieval_recall_at_k": 0.70,
        "mrr": 0.60,
        "faithfulness": 0.80,
        "answer_relevance": 0.75,
        "citation_accuracy": 0.85,
        "refusal_accuracy": 0.90,
        "route_accuracy": 0.80,
    }

    print("\n=== AGGREGATE METRICS ===")
    for k, t in thresholds.items():
        v = s.get(k)
        if v is None:
            status = "N/A"
        else:
            status = "PASS" if v >= t else "FAIL"
        vstr = f"{v:.4f}" if isinstance(v, float) else str(v)
        print(f"  {k:<30} {vstr:>8}   thresh={t:.2f}   {status}")

    ml = s.get("mean_latency_s")
    p95 = s.get("p95_latency_s")
    print(f"\n  mean_latency_s : {ml}")
    print(f"  p95_latency_s  : {p95}")
    print(f"  total_questions: {s.get('total_questions')}")
    print(f"  errors         : {s.get('errors')}")

    print("\n=== BY CATEGORY ===")
    for cat, cm in s.get("by_category", {}).items():
        ref = cm.get("refusal_accuracy")
        rel = cm.get("answer_relevance")
        rte = cm.get("route_accuracy")
        print(f"  {cat:<25} n={cm['count']:>3}  refusal={ref}  relevance={rel}  route={rte}")

    print("\n=== BY DIFFICULTY ===")
    for diff, dm in s.get("by_difficulty", {}).items():
        print(f"  {diff:<8} n={dm['count']:>3}  relevance={dm.get('answer_relevance')}  faithfulness={dm.get('faithfulness')}")
