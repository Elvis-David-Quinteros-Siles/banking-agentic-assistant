"""Calibración de la confianza del router LoRA y diseño de la cascada reglas → LoRA.

Dos etapas:
  collect  → corre reglas y LoRA sobre val y test y guarda las puntuaciones crudas
             (reports/router_scores.jsonl). Necesita el adaptador (GPU recomendada, ~4 min).
  analyze  → con esas puntuaciones: calibración (ECE, Brier, AUROC de errores, riesgo-cobertura),
             temperatura, intenciones que resuelven las reglas y umbral de aclaración del LoRA.
             Solo Python: segundos (reports/calibration.{json,md}).

Decisiones de método:
- Se comparan dos confianzas: la greedy (producción: probabilidad de los tokens de la intención
  generada) y una distribución sobre las 6 etiquetas puntuadas una por una, calibrable con
  temperatura. La temperatura NO cambia el argmax: la accuracy es la misma; cambia qué tan creíble
  es la confianza.
- val comparte plantillas con train: el modelo acierta casi todo y una temperatura ajustada ahí
  empuja a más confianza. El test tiene plantillas nunca vistas (el caso real). Por eso T se elige
  con validación cruzada AGRUPADA por plantilla sobre test (ninguna plantilla en ambos lados) y se
  reporta el resultado fuera de muestra.
- Qué intenciones resuelven las reglas se decide sobre train+val (solo reglas: no necesita el
  modelo) y se mide sobre test.
- Resultado (finetune/README.md): la confianza greedy ya está bien calibrada en v3 y la
  temperatura no aporta; lo que sí aporta es la cascada y un umbral de aclaración propio del LoRA.

Uso:  python finetune/calibrate.py collect
      python finetune/calibrate.py analyze
"""

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.graph.nodes.router import classify_rules  # noqa: E402
from app.llm.intent_format import INTENT_LABELS, build_messages  # noqa: E402

REPORTS = ROOT / "reports"
SCORES = REPORTS / "router_scores.jsonl"
DEFAULT_ADAPTER = ROOT / "adapters" / "qwen2.5-0.5b-intent-lora"
TEMPERATURES = [round(0.5 + 0.1 * i, 1) for i in range(96)]  # 0.5 … 10.0
# La cascada acepta una intención de las reglas solo si, en train+val, su precisión llega a esto
# con un soporte mínimo (con pocos casos, un 100% no dice nada)
CASCADE_MIN_PRECISION = 0.99
CASCADE_MIN_SUPPORT = 30
RULES_MIN_CONFIDENCE = 0.6  # el umbral de aclaración del router por reglas
CLARIFY_THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]


def load(split: str) -> list[dict]:
    path = ROOT / "data" / f"{split}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------
def label_logprobs(model, text: str) -> dict[str, float]:
    """Log-probabilidad de CADA intención como continuación de '{"intent":"' (una pasada en lote).

    La confianza greedy solo mira el camino elegido; esto puntúa el conjunto cerrado de etiquetas
    y, con softmax(logp / T), da una distribución que se puede calibrar con temperatura.
    """
    torch, tokenizer = model.torch, model.tokenizer
    prompt = tokenizer.apply_chat_template(build_messages(text), tokenize=False, add_generation_prompt=True)
    sequences = [tokenizer(f'{prompt}{{"intent":"{label}","')["input_ids"] for label in INTENT_LABELS]
    # Tokens comunes a las 6 secuencias = el prompt; lo que sigue es la etiqueta + el cierre '","'
    shared = 0
    while all(len(seq) > shared and seq[shared] == sequences[0][shared] for seq in sequences):
        shared += 1
    width = max(len(seq) for seq in sequences)
    pad = tokenizer.pad_token_id or tokenizer.eos_token_id
    input_ids = torch.tensor([seq + [pad] * (width - len(seq)) for seq in sequences], device=model.device)
    attention = torch.tensor([[1] * len(seq) + [0] * (width - len(seq)) for seq in sequences], device=model.device)
    with torch.inference_mode():
        logits = model.model(input_ids=input_ids, attention_mask=attention).logits.float()
    logprobs = torch.log_softmax(logits, -1)
    return {
        label: float(sum(logprobs[row, pos - 1, seq[pos]].item() for pos in range(shared, len(seq))))
        for row, (label, seq) in enumerate(zip(INTENT_LABELS, sequences, strict=True))
    }


def collect(adapter: Path, device: str) -> None:
    from app.llm.lora_router import LoraIntentClassifier

    model = LoraIntentClassifier("Qwen/Qwen2.5-0.5B-Instruct", adapter, device)
    model.classify("hola")
    label_logprobs(model, "hola")
    REPORTS.mkdir(exist_ok=True)
    with SCORES.open("w", encoding="utf-8") as out:
        for split in ("val", "test"):
            for row in load(split):
                prediction = model.classify(row["text"])
                started = time.perf_counter()
                logprobs = label_logprobs(model, row["text"])
                scoring_ms = (time.perf_counter() - started) * 1000
                record = {
                    "split": split,
                    "template_id": row["template_id"],
                    "text": row["text"],
                    "gold": row["label"]["intent"],
                    "greedy_intent": prediction.output["intent"],
                    "greedy_confidence": prediction.confidence,
                    "label_logprobs": logprobs,
                    "generate_ms": prediction.latency_ms,
                    "scoring_ms": round(scoring_ms, 2),
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"Puntuaciones en {SCORES}")


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------
def softmax(logprobs: dict[str, float], temperature: float) -> dict[str, float]:
    scaled = {k: v / temperature for k, v in logprobs.items()}
    top = max(scaled.values())
    exp = {k: math.exp(v - top) for k, v in scaled.items()}
    total = sum(exp.values())
    return {k: v / total for k, v in exp.items()}


def predictions(rows: list[dict], temperature: float) -> list[tuple[str, float, str]]:
    """(intención predicha, confianza, oro) con la distribución sobre las 6 etiquetas."""
    result = []
    for r in rows:
        probs = softmax(r["label_logprobs"], temperature)
        best = max(probs, key=lambda k: probs[k])
        result.append((best, probs[best], r["gold"]))
    return result


def nll(rows: list[dict], temperature: float) -> float:
    return -statistics.mean(math.log(max(softmax(r["label_logprobs"], temperature)[r["gold"]], 1e-12)) for r in rows)


def fit_temperature(rows: list[dict]) -> float:
    return min(TEMPERATURES, key=lambda t: nll(rows, t))


def calibration_metrics(preds: list[tuple[str, float, str]], bins: int = 10) -> dict:
    n = len(preds)
    correct = [p == g for p, _, g in preds]
    conf = [c for _, c, _ in preds]
    ece, reliability = 0.0, []
    for b in range(bins):
        low, high = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(conf) if low < c <= high or (b == 0 and c == 0)]
        if not idx:
            continue
        acc = sum(correct[i] for i in idx) / len(idx)
        mean_conf = sum(conf[i] for i in idx) / len(idx)
        ece += len(idx) / n * abs(acc - mean_conf)
        reliability.append({"bin": f"{low:.1f}-{high:.1f}", "n": len(idx), "accuracy": acc, "confidence": mean_conf})
    return {
        "accuracy": round(sum(correct) / n, 3),
        "mean_confidence": round(statistics.mean(conf), 3),
        "ece": round(ece, 3),
        "brier": round(statistics.mean((c - k) ** 2 for c, k in zip(conf, correct, strict=True)), 4),
        "error_auroc": error_auroc(conf, correct),
        "reliability": reliability,
    }


def error_auroc(conf: list[float], correct: list[bool]) -> float | None:
    """P(un acierto tiene más confianza que un error): ¿la confianza separa errores de aciertos?"""
    hits = [c for c, k in zip(conf, correct, strict=True) if k]
    misses = [c for c, k in zip(conf, correct, strict=True) if not k]
    if not hits or not misses:
        return None
    wins = sum((h > m) + 0.5 * (h == m) for h in hits for m in misses)
    return round(wins / (len(hits) * len(misses)), 3)


def risk_coverage(preds: list[tuple[str, float, str]], thresholds: list[float]) -> list[dict]:
    """Con umbral τ: qué fracción se responde directo (cobertura) y con qué accuracy; el resto se aclara."""
    rows = []
    for tau in thresholds:
        kept = [(p, g) for p, c, g in preds if c >= tau]
        rows.append(
            {
                "threshold": tau,
                "coverage": round(len(kept) / len(preds), 3),
                "accuracy_answered": round(sum(p == g for p, g in kept) / len(kept), 3) if kept else None,
                "errors_answered": sum(p != g for p, g in kept),
            }
        )
    return rows


def grouped_folds(rows: list[dict], k: int = 2) -> list[tuple[list[dict], list[dict]]]:
    """k folds sin compartir plantillas (template_id) entre ajuste y evaluación."""
    templates = sorted({r["template_id"] for r in rows})
    folds = []
    for i in range(k):
        held = {t for j, t in enumerate(templates) if j % k == i}
        folds.append(([r for r in rows if r["template_id"] not in held], [r for r in rows if r["template_id"] in held]))
    return folds


def rules_precision_by_intent(rows: list[dict]) -> dict[str, dict]:
    """Precisión de las reglas por intención predicha (con confianza >= su umbral de aclaración)."""
    table: dict[str, dict] = {}
    for r in rows:
        intent, conf = classify_rules(r["text"])
        if conf < RULES_MIN_CONFIDENCE:
            continue
        entry = table.setdefault(intent, {"support": 0, "correct": 0, "errors": []})
        entry["support"] += 1
        if intent == r["label"]["intent"]:
            entry["correct"] += 1
        elif len(entry["errors"]) < 3:
            entry["errors"].append({"text": r["text"], "gold": r["label"]["intent"]})
    for entry in table.values():
        entry["precision"] = round(entry["correct"] / entry["support"], 3)
    return dict(sorted(table.items()))


def trusted_rule_intents(table: dict[str, dict]) -> list[str]:
    return [
        intent
        for intent, e in table.items()
        if e["precision"] >= CASCADE_MIN_PRECISION and e["support"] >= CASCADE_MIN_SUPPORT
    ]


def cascade_on_test(test: list[dict], trusted: list[str], clarify_tau: float) -> dict:
    """Cascada sobre test: las reglas resuelven solas sus intenciones confiables; el resto, LoRA.

    Un turno de LoRA con confianza < clarify_tau pide aclaración (no responde con una intención dudosa).
    """
    stages = {"rules": 0, "lora": 0}
    correct, clarified, latencies, errors = 0, 0, [], []
    for r in test:
        intent, conf = classify_rules(r["text"])
        if intent in trusted and conf >= RULES_MIN_CONFIDENCE:
            stage, latency = "rules", 0.2
        else:
            stage, intent, conf = "lora", r["greedy_intent"], r["greedy_confidence"]
            latency = r["generate_ms"]
        stages[stage] += 1
        latencies.append(latency)
        correct += intent == r["gold"]
        if stage == "lora" and conf < clarify_tau:
            clarified += 1
        elif intent != r["gold"]:
            errors.append({"text": r["text"], "gold": r["gold"], "pred": intent, "stage": stage})
    n = len(test)
    return {
        "clarify_threshold": clarify_tau,
        "rules_share": round(stages["rules"] / n, 3),
        "accuracy": round(correct / n, 3),
        "clarified": clarified,
        "errors_answered": len(errors),
        "latency_p50_ms": round(statistics.median(latencies), 1),
        "latency_mean_ms": round(statistics.mean(latencies), 1),
        "errors": errors,
    }


def analyze() -> dict:
    scores = [json.loads(line) for line in SCORES.read_text(encoding="utf-8").splitlines() if line]
    val = [r for r in scores if r["split"] == "val"]
    test = [r for r in scores if r["split"] == "test"]
    report: dict = {"n_val": len(val), "n_test": len(test)}

    # 1. Greedy (la confianza actual) frente a la distribución sobre etiquetas sin calibrar
    greedy = [(r["greedy_intent"], r["greedy_confidence"], r["gold"]) for r in test]
    report["greedy"] = calibration_metrics(greedy)
    report["labels_T1"] = calibration_metrics(predictions(test, 1.0))
    report["argmax_agrees_with_greedy"] = round(
        sum(p == r["greedy_intent"] for (p, _, _), r in zip(predictions(test, 1.0), test, strict=True)) / len(test), 3
    )

    # 2. Temperatura: ajustada en val (referencia) y por CV agrupada en test (la que se usa)
    report["T_val"] = fit_temperature(val)
    report["labels_Tval_on_test"] = calibration_metrics(predictions(test, report["T_val"]))
    out_of_fold, fold_t = [], []
    for fit_rows, eval_rows in grouped_folds(test):
        t = fit_temperature(fit_rows)
        fold_t.append(t)
        out_of_fold += predictions(eval_rows, t)
    report["T_cv_folds"] = fold_t
    report["labels_Tcv_out_of_fold"] = calibration_metrics(out_of_fold)
    report["T_final"] = fit_temperature(test)

    thresholds = CLARIFY_THRESHOLDS
    report["risk_coverage"] = {
        "greedy": risk_coverage(greedy, thresholds),
        "calibrated_out_of_fold": risk_coverage(out_of_fold, thresholds),
    }

    # 3. Cascada: intenciones confiables de las reglas elegidas en train+val, medida en test
    table = rules_precision_by_intent(load("train") + load("val"))
    report["rules_precision_train_val"] = table
    trusted = trusted_rule_intents(table)
    report["trusted_rule_intents"] = trusted
    report["rules_precision_test"] = rules_precision_by_intent(load("test"))
    report["cascade_test"] = [cascade_on_test(test, trusted, tau) for tau in CLARIFY_THRESHOLDS]
    report["lora_only_test"] = [cascade_on_test(test, [], tau) for tau in CLARIFY_THRESHOLDS]
    report["latency_lora_ms"] = {
        "generate_p50": round(statistics.median(r["generate_ms"] for r in test), 1),
        "scoring_p50": round(statistics.median(r["scoring_ms"] for r in test), 1),
    }
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "calibration.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (REPORTS / "calibration.md").write_text(to_markdown(report), encoding="utf-8")
    return report


def to_markdown(report: dict) -> str:
    lines = [
        f"Test: {report['n_test']} ejemplos (plantillas nunca vistas). Temperatura por CV agrupada: "
        f"{report['T_cv_folds']} → final {report['T_final']} (en val: {report['T_val']}).",
        "",
        "| Confianza | Accuracy | Confianza media | ECE | Brier | AUROC aciertos vs errores |",
        "|---|---|---|---|---|---|",
    ]
    names = {
        "greedy": "Greedy (la de producción)",
        "labels_T1": "6 etiquetas, T=1",
        "labels_Tcv_out_of_fold": "6 etiquetas, T por CV (fuera de muestra)",
    }
    for key, name in names.items():
        m = report[key]
        lines.append(
            f"| {name} | {m['accuracy']:.3f} | {m['mean_confidence']:.3f} | {m['ece']:.3f} | "
            f"{m['brier']:.4f} | {m['error_auroc']} |"
        )
    lines += [
        "",
        "Diagrama de fiabilidad (greedy):",
        "",
        "| Rango | n | Accuracy | Confianza media |",
        "|---|---|---|---|",
    ]
    for b in report["greedy"]["reliability"]:
        lines.append(f"| {b['bin']} | {b['n']} | {b['accuracy']:.2f} | {b['confidence']:.2f} |")
    lines += [
        "",
        "Precisión de las reglas por intención (train+val, confianza >= 0.6):",
        "",
        "| Intención | Soporte | Precisión | ¿La resuelven las reglas? |",
        "|---|---|---|---|",
    ]
    for intent, e in report["rules_precision_train_val"].items():
        trusted = "sí" if intent in report["trusted_rule_intents"] else "no → LoRA"
        lines.append(f"| {intent} | {e['support']} | {e['precision']:.3f} | {trusted} |")
    lines += [
        "",
        "Umbral de aclaración del LoRA (test):",
        "",
        "| Umbral | LoRA solo: errores respondidos / aclaraciones | Cascada: errores respondidos / aclaraciones |",
        "|---|---|---|",
    ]
    for solo, cascade in zip(report["lora_only_test"], report["cascade_test"], strict=True):
        lines.append(
            f"| {solo['clarify_threshold']} | {solo['errors_answered']} / {solo['clarified']} | "
            f"{cascade['errors_answered']} / {cascade['clarified']} |"
        )
    solo, cascade = report["lora_only_test"][0], report["cascade_test"][0]
    lines += [
        "",
        f"Cascada: {cascade['rules_share']:.0%} de los turnos por reglas · accuracy {solo['accuracy']:.3f} → "
        f"{cascade['accuracy']:.3f} · latencia media del router {solo['latency_mean_ms']} → "
        f"{cascade['latency_mean_ms']} ms.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["collect", "analyze"])
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.stage == "collect":
        collect(args.adapter, args.device)
    else:
        report = analyze()
        summary = {k: v for k, v in report.items() if k not in ("risk_coverage", "cascade_test", "lora_only_test")}
        for key in ("greedy", "labels_T1", "labels_Tval_on_test", "labels_Tcv_out_of_fold"):
            summary[key] = {k: v for k, v in report[key].items() if k != "reliability"}
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        for name in ("lora_only_test", "cascade_test"):
            print(name)
            for row in report[name]:
                print("  ", {k: v for k, v in row.items() if k != "errors"})


if __name__ == "__main__":
    main()
