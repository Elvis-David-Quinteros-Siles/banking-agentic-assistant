"""Evaluación comparativa del router sobre el split de test (plantillas nunca vistas al entrenar).

Compara tres backends con el mismo conjunto:
  rules       → baseline por palabras clave (app/graph/nodes/router.py)
  base        → Qwen2.5-0.5B-Instruct SIN fine-tuning (zero-shot, mismo prompt)
  lora        → Qwen2.5-0.5B-Instruct + adaptador LoRA

Métricas: accuracy y macro-F1 de intención, precision/recall por clase, validez del JSON,
exact-match de entidades (producto, monto, moneda) y latencia p50/p95.

Uso:  python finetune/eval_classifier.py [--device cpu] [--skip-base]
Salida: finetune/reports/router_eval.json y router_eval.md
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.graph.nodes.router import classify_rules, extract_entities  # noqa: E402
from app.llm.intent_format import INTENT_LABELS  # noqa: E402

REPORTS = ROOT / "reports"
DEFAULT_ADAPTER = ROOT / "adapters" / "qwen2.5-0.5b-intent-lora"


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 1)


def classification_report(gold: list[str], pred: list[str]) -> dict:
    per_class = {}
    for label in INTENT_LABELS:
        tp = sum(g == label and p == label for g, p in zip(gold, pred))
        fp = sum(g != label and p == label for g, p in zip(gold, pred))
        fn = sum(g == label and p != label for g, p in zip(gold, pred))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}
    return {
        "accuracy": round(sum(g == p for g, p in zip(gold, pred)) / len(gold), 3),
        "macro_f1": round(statistics.mean(c["f1"] for c in per_class.values()), 3),
        "per_class": per_class,
    }


def entity_match(gold: dict, pred: dict) -> dict[str, bool]:
    def same_amount(a, b):
        return (a is None and b is None) or (a is not None and b is not None and abs(float(a) - float(b)) < 0.01)

    return {
        "producto": gold.get("producto") == pred.get("producto"),
        "monto": same_amount(gold.get("monto"), pred.get("monto")),
        "moneda": gold.get("moneda") == pred.get("moneda"),
    }


def evaluate(name: str, rows: list[dict], predict) -> dict:
    gold, pred, latencies, matches, valid, errors = [], [], [], [], 0, []
    for row in rows:
        started = time.perf_counter()
        try:
            output = predict(row["text"])
            valid += 1
        except ValueError:
            output = {"intent": "invalido"}
        latencies.append((time.perf_counter() - started) * 1000)
        gold.append(row["label"]["intent"])
        pred.append(output["intent"])
        matches.append(entity_match(row["label"], output))
        if output["intent"] != row["label"]["intent"]:
            errors.append({"text": row["text"], "gold": row["label"]["intent"], "pred": output["intent"]})

    report = classification_report(gold, pred)
    report.update({
        "backend": name,
        "n": len(rows),
        "json_valid_rate": round(valid / len(rows), 3),
        "entity_exact_match": {k: round(sum(m[k] for m in matches) / len(matches), 3) for k in ("producto", "monto", "moneda")},
        "latency_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95)},
        "errors_sample": errors[:10],
    })
    print(f"{name:6} acc={report['accuracy']:.3f} macroF1={report['macro_f1']:.3f} "
          f"json={report['json_valid_rate']:.2f} p95={report['latency_ms']['p95']}ms")
    return report


def to_markdown(results: list[dict]) -> str:
    lines = [
        "| Backend | Accuracy | Macro-F1 | JSON válido | Producto EM | Monto EM | Moneda EM | p50 (ms) | p95 (ms) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        e = r["entity_exact_match"]
        lines.append(
            f"| {r['backend']} | {r['accuracy']:.3f} | {r['macro_f1']:.3f} | {r['json_valid_rate']:.2f} | "
            f"{e['producto']:.2f} | {e['monto']:.2f} | {e['moneda']:.2f} | {r['latency_ms']['p50']} | {r['latency_ms']['p95']} |"
        )
    lines += ["", "Recall por intención:", "", "| Backend | " + " | ".join(INTENT_LABELS) + " |",
              "|---|" + "---|" * len(INTENT_LABELS)]
    for r in results:
        lines.append(f"| {r['backend']} | " + " | ".join(f"{r['per_class'][l]['recall']:.2f}" for l in INTENT_LABELS) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="test")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--skip-base", action="store_true", help="No evaluar el modelo base zero-shot")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    rows = [json.loads(l) for l in (ROOT / "data" / f"{args.split}.jsonl").read_text(encoding="utf-8").splitlines() if l]

    def rules_predict(text: str) -> dict:
        intent, _ = classify_rules(text)
        return {"intent": intent, "producto": None, "monto": None, "moneda": None, **extract_entities(text)}

    results = [evaluate("rules", rows, rules_predict)]

    from app.llm.lora_router import LoraIntentClassifier

    if not args.skip_base:
        base = LoraIntentClassifier(args.base_model, None, args.device)
        base.classify("hola")  # calentamiento: la primera llamada compila kernels
        results.append(evaluate("base", rows, lambda t: base.classify(t).output))
        del base

    lora = LoraIntentClassifier(args.base_model, args.adapter, args.device)
    lora.classify("hola")
    results.append(evaluate("lora", rows, lambda t: lora.classify(t).output))

    REPORTS.mkdir(exist_ok=True)
    suffix = "" if args.device in ("auto", "cuda") else f"_{args.device}"
    (REPORTS / f"router_eval{suffix}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    (REPORTS / f"router_eval{suffix}.md").write_text(to_markdown(results), encoding="utf-8")
    print(to_markdown(results))


if __name__ == "__main__":
    main()
