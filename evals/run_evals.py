"""Evals de punta a punta del asistente bancario.

Ejecuta los casos de evals/cases.yaml contra el sistema COMPLETO (FastAPI + grafo LangGraph) en
proceso, con bases SQLite temporales para no ensuciar los datos de desarrollo. Simula al operador
HITL con /approve. Calcula métricas por caso de uso y las compara con evals/thresholds.yaml.

Uso (desde la raíz del repo):
    python evals/run_evals.py                               # rules + keyword + fake (igual que el CI)
    python evals/run_evals.py --knowledge neo4j             # Graph RAG (requiere Neo4j cargado)
    python evals/run_evals.py --router lora --knowledge neo4j
    python evals/run_evals.py --llm ollama --judge          # + LLM-as-judge de fidelidad
    python evals/run_evals.py --check                       # exit 1 si algún umbral no se cumple

Salida: evals/reports/<config>.json y .md (y latest.json / latest.md).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "backend"))

INTENTS = ("consulta_cuenta", "conocimiento_producto", "queja_disputa", "fuera_de_alcance", "ambiguo")


@dataclass
class CaseResult:
    id: str
    category: str
    message: str
    tags: list[str]
    passed: bool = True
    failures: list[str] = field(default_factory=list)
    intent: str | None = None
    expected_intent: str | None = None
    confidence: float | None = None
    status: str | None = None
    citations: list[str] = field(default_factory=list)
    pii_found: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    degraded: bool = False
    node_latency: dict[str, float] = field(default_factory=dict)
    hitl_ok: bool | None = None
    faithfulness: dict | None = None
    answer: str = ""

    def fail(self, reason: str) -> None:
        self.passed = False
        self.failures.append(reason)


def configure_env(args: argparse.Namespace, tmp: Path) -> None:
    """Fija la configuración ANTES de importar la app (la configuración se cachea)."""
    os.environ.update({
        "APP_ENV": "test",
        "LOG_LEVEL": "WARNING",
        "LLM_PROVIDER": args.llm,
        "ROUTER_BACKEND": args.router,
        "KNOWLEDGE_BACKEND": args.knowledge,
        "SQLITE_PATH": str(tmp / "bank.db"),
        "CHECKPOINTS_PATH": str(tmp / "checkpoints.db"),
    })
    if args.router == "lora":
        # El modelo LoRA ya está en caché local: evita consultas a Hugging Face en cada corrida
        os.environ.setdefault("HF_HUB_OFFLINE", "1")


# ---------------------------------------------------------------------------
# Ejecución de un caso
# ---------------------------------------------------------------------------
def run_case(client, case: dict, judge=None) -> CaseResult:
    expected = case.get("expected", {})
    result = CaseResult(
        id=case["id"], category=case["category"], message=case["message"],
        tags=case.get("tags", []), expected_intent=expected.get("intent"),
    )

    started = time.perf_counter()
    response = client.post("/chat", json={"customer_id": case["customer"], "message": case["message"]})
    result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
    if response.status_code != 200:
        result.fail(f"HTTP {response.status_code}: {response.text[:120]}")
        return result
    body = response.json()

    result.intent = body["intent"]
    result.confidence = body["intent_confidence"]
    result.status = body["status"]
    result.citations = body["citations"]
    result.pii_found = body["guardrail"]["pii_found"]
    result.degraded = body["degraded"]
    result.answer = body["answer"]
    result.tokens_in = body["metrics"]["tokens_in"]
    result.tokens_out = body["metrics"]["tokens_out"]
    result.cost_usd = body["metrics"]["cost_usd"]
    result.node_latency = {n["node"]: n["latency_ms"] for n in body["metrics"]["nodes"]}

    # Estado interno del grafo (contexto recuperado, borrador) para checks más profundos
    graph = client.app.state.graph
    snapshot = client.portal.call(graph.aget_state, {"configurable": {"thread_id": body["thread_id"]}})
    values = snapshot.values

    if "intent" in expected and result.intent != expected["intent"]:
        result.fail(f"intent {result.intent} (esperado {expected['intent']})")
    if "status" in expected and result.status != expected["status"]:
        result.fail(f"status {result.status} (esperado {expected['status']})")
    if "blocked_reason" in expected and body["guardrail"]["reason"] != expected["blocked_reason"]:
        result.fail(f"motivo de bloqueo {body['guardrail']['reason']} (esperado {expected['blocked_reason']})")
    for kind in expected.get("pii", []):
        if kind not in result.pii_found:
            result.fail(f"PII {kind} no detectada")
    if "citations_any" in expected and not set(expected["citations_any"]) & set(result.citations):
        result.fail(f"citas {result.citations} (esperada alguna de {expected['citations_any']})")
    for text in expected.get("answer_contains", []):
        if text not in result.answer:
            result.fail(f"falta en la respuesta: {text!r}")
    for text in expected.get("answer_not_contains", []):
        stored = " ".join(str(m.content) for m in values.get("messages", []))
        if text in result.answer or text in stored:
            result.fail(f"fuga en respuesta/historial: {text!r}")

    draft = (body.get("interrupt") or {}).get("payload") or {}
    for key, value in (expected.get("draft") or {}).items():
        if draft.get(key) != value:
            result.fail(f"borrador.{key}={draft.get(key)!r} (esperado {value!r})")
    for text in expected.get("payload_not_contains", []):
        if text in json.dumps(draft, ensure_ascii=False):
            result.fail(f"PII en el borrador HITL: {text!r}")

    # HITL: simular la decisión del operador y verificar la reanudación
    hitl = expected.get("hitl")
    if hitl and result.status == "interrupted":
        decision = {
            "decision": hitl["decision"], "reviewer_id": "EVAL-OP",
            "comment": hitl.get("comment"), "edited_draft": hitl.get("edited_draft"),
        }
        resumed = client.post(f"/approve/{body['thread_id']}", json=decision)
        final = resumed.json() if resumed.status_code == 200 else {}
        result.hitl_ok = resumed.status_code == 200 and final.get("status") == "completed" and all(
            text in final.get("answer", "") for text in hitl.get("final_contains", [])
        )
        if not result.hitl_ok:
            result.fail(f"reanudación HITL fallida: {resumed.status_code} {final.get('answer', '')[:80]!r}")
    elif hitl:
        result.hitl_ok = False

    if judge is not None and result.intent == "conocimiento_producto" and values.get("retrieved_context"):
        result.faithfulness = judge(case["message"], result.answer, values["retrieved_context"])

    return result


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------
def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))], 1)


def compute_metrics(results: list[CaseResult], cases: list[dict]) -> dict[str, Any]:
    by_id = {c["id"]: c for c in cases}
    exp = {r.id: by_id[r.id].get("expected", {}) for r in results}

    routed = [r for r in results if r.expected_intent is not None]
    attacks = [r for r in results if exp[r.id].get("status") == "blocked"]
    legit = [r for r in results if exp[r.id].get("status") != "blocked"]
    pii_cases = [r for r in results if exp[r.id].get("pii")]
    complaints = [r for r in results if r.expected_intent == "queja_disputa"]

    # Métricas de MECANISMO: se miden solo sobre casos que el router envió al agente correcto,
    # para no mezclar "¿funciona el HITL / el SQL / el RAG?" con "¿acertó el router?".
    def routed_ok(r: CaseResult) -> bool:
        return r.intent == r.expected_intent

    hitl_cases = [r for r in results if exp[r.id].get("hitl") and routed_ok(r)]
    hitl_triggered = [r for r in hitl_cases if r.status == "interrupted"]
    knowledge = [r for r in results if exp[r.id].get("citations_any") and routed_ok(r)]
    exact = [r for r in results if exp[r.id].get("answer_contains") and routed_ok(r)]

    per_intent = {}
    for label in INTENTS:
        tp = sum(r.intent == label and r.expected_intent == label for r in routed)
        fp = sum(r.intent == label and r.expected_intent != label for r in routed)
        fn = sum(r.intent != label and r.expected_intent == label for r in routed)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        per_intent[label] = {"precision": None if precision is None else round(precision, 3),
                             "recall": None if recall is None else round(recall, 3), "support": tp + fn}

    def pii_ok(r: CaseResult) -> bool:
        return not any(f.startswith(("PII", "fuga")) for f in r.failures)

    latencies = [r.latency_ms for r in results]
    node_names = sorted({n for r in results for n in r.node_latency})
    judged = [r.faithfulness for r in results if r.faithfulness]

    paraphrase = [r for r in routed if "parafrasis" in r.tags or "trampa" in r.tags]
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "end_to_end_success": ratio(sum(r.passed for r in results), len(results)),
        "routing_accuracy": ratio(sum(r.intent == r.expected_intent for r in routed), len(routed)),
        "routing_accuracy_paraphrase": ratio(sum(r.intent == r.expected_intent for r in paraphrase), len(paraphrase)),
        "per_intent": per_intent,
        "guardrail_interception_rate": ratio(sum(r.status == "blocked" for r in attacks), len(attacks)),
        "guardrail_reason_accuracy": ratio(
            sum(not any(f.startswith("motivo") for f in r.failures) and r.status == "blocked" for r in attacks), len(attacks)),
        "guardrail_false_positive_rate": ratio(sum(r.status == "blocked" for r in legit), len(legit)),
        "pii_redaction_recall": ratio(sum(pii_ok(r) for r in pii_cases), len(pii_cases)),
        "complaint_recall": ratio(sum(r.intent == "queja_disputa" for r in complaints), len(complaints)),
        "hitl_trigger_rate": ratio(len(hitl_triggered), len(hitl_cases)),
        "hitl_resume_success": ratio(sum(bool(r.hitl_ok) for r in hitl_triggered), len(hitl_triggered)),
        "citation_hit_rate": ratio(sum(not any(f.startswith("citas") for f in r.failures) for r in knowledge), len(knowledge)),
        "data_exactness": ratio(sum(not any(f.startswith("falta") for f in r.failures) for r in exact), len(exact)),
        "faithfulness": ratio(sum(j["faithful"] for j in judged), len(judged)) if judged else None,
        "degraded_rate": ratio(sum(r.degraded for r in results), len(results)),
        "latency_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95), "max": max(latencies)},
        "node_latency_p95_ms": {n: percentile([r.node_latency[n] for r in results if n in r.node_latency], 0.95) for n in node_names},
        "tokens": {"in": sum(r.tokens_in for r in results), "out": sum(r.tokens_out for r in results)},
        "cost_usd": {"total": round(sum(r.cost_usd for r in results), 6),
                     "per_turn": round(sum(r.cost_usd for r in results) / len(results), 6)},
        "confidence_correct_vs_wrong": {
            "correct_mean": _mean([r.confidence for r in routed if r.intent == r.expected_intent]),
            "wrong_mean": _mean([r.confidence for r in routed if r.intent != r.expected_intent]),
        },
    }


def _mean(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return round(statistics.mean(clean), 3) if clean else None


def check_thresholds(metrics: dict, thresholds: dict, router: str, knowledge: str) -> list[str]:
    rules = {**thresholds["common"], **thresholds["router"].get(router, {}), **thresholds["knowledge"].get(knowledge, {})}
    violations = []
    for name, limit in rules.items():
        value = metrics.get(name)
        if value is None:
            continue
        if "min" in limit and value < limit["min"]:
            violations.append(f"{name} = {value} < {limit['min']}")
        if "max" in limit and value > limit["max"]:
            violations.append(f"{name} = {value} > {limit['max']}")
    budget = thresholds["latency"].get(router, {}).get("p95_ms_max")
    if budget and metrics["latency_ms"]["p95"] > budget:
        violations.append(f"latency p95 = {metrics['latency_ms']['p95']} ms > {budget} ms")
    return violations


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------
def to_markdown(config: dict, metrics: dict, results: list[CaseResult], violations: list[str]) -> str:
    def pct(v):
        return "—" if v is None else f"{v * 100:.1f}%"

    m = metrics
    lines = [
        f"# Evals · router={config['router']} · knowledge={config['knowledge']} · llm={config['llm']}",
        "",
        f"**{m['passed']}/{m['cases']} casos OK** ({pct(m['end_to_end_success'])}) · "
        f"{'✅ todos los umbrales cumplidos' if not violations else '❌ ' + '; '.join(violations)}",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Precisión de enrutamiento | {pct(m['routing_accuracy'])} |",
        f"| … en paráfrasis y trampas | {pct(m['routing_accuracy_paraphrase'])} |",
        f"| Interceptación de guardrail (ataques + fuera de alcance) | {pct(m['guardrail_interception_rate'])} |",
        f"| Falsos positivos de guardrail | {pct(m['guardrail_false_positive_rate'])} |",
        f"| Recall de enmascarado de PII | {pct(m['pii_redaction_recall'])} |",
        f"| Recall de reclamos (router) | {pct(m['complaint_recall'])} |",
        f"| Disparo de HITL en reclamos enrutados ¹ | {pct(m['hitl_trigger_rate'])} |",
        f"| Éxito de reanudación HITL ¹ | {pct(m['hitl_resume_success'])} |",
        f"| Aciertos de cita (Graph RAG) ¹ | {pct(m['citation_hit_rate'])} |",
        f"| Exactitud de datos del core ¹ | {pct(m['data_exactness'])} |",
        f"| Fidelidad (LLM-as-judge) | {pct(m['faithfulness'])} |",
        f"| Latencia p50 / p95 | {m['latency_ms']['p50']} / {m['latency_ms']['p95']} ms |",
        f"| Tokens (in / out) | {m['tokens']['in']} / {m['tokens']['out']} |",
        f"| Costo total / por turno | US$ {m['cost_usd']['total']} / {m['cost_usd']['per_turn']} |",
        "",
        "Precision / recall por intención:",
        "",
        "| Intención | Precision | Recall | Casos |",
        "|---|---|---|---|",
    ]
    for label, v in m["per_intent"].items():
        lines.append(f"| {label} | {pct(v['precision'])} | {pct(v['recall'])} | {v['support']} |")
    lines += ["", "¹ Métrica de mecanismo: se calcula sobre los casos que el router envió al agente correcto.", ""]
    lines += ["Latencia p95 por nodo (ms): " + ", ".join(f"`{n}` {v}" for n, v in m["node_latency_p95_ms"].items()), ""]

    failed = [r for r in results if not r.passed]
    if failed:
        lines += ["## Casos fallidos", "", "| Caso | Mensaje | Fallas |", "|---|---|---|"]
        for r in failed:
            lines.append(f"| {r.id} | {r.message} | {'; '.join(r.failures)} |")
    return "\n".join(lines) + "\n"


def run(router: str = "rules", knowledge: str = "keyword", llm: str = "fake", judge: bool = False,
        cases_path: Path = ROOT / "cases.yaml", verbose: bool = True) -> dict:
    if "app.main" in sys.modules:
        raise RuntimeError("La app ya fue importada con otra configuración: ejecutar los Evals en un proceso aparte")
    args = argparse.Namespace(router=router, knowledge=knowledge, llm=llm)
    tmp = Path(tempfile.mkdtemp(prefix="bank-evals-"))
    configure_env(args, tmp)

    from fastapi.testclient import TestClient

    from app.main import app

    cases = yaml.safe_load(cases_path.read_text(encoding="utf-8"))["cases"]
    thresholds = yaml.safe_load((ROOT / "thresholds.yaml").read_text(encoding="utf-8"))

    judge_fn = None
    if judge:
        from judge import make_judge  # evals/judge.py

        judge_fn = make_judge()

    results: list[CaseResult] = []
    with TestClient(app) as client:
        for case in cases:
            result = run_case(client, case, judge_fn)
            results.append(result)
            if verbose:
                mark = "OK  " if result.passed else "FAIL"
                print(f"  {mark} {result.id:4} {result.intent or '-':22} {result.status or '-':13} "
                      f"{result.latency_ms:7.1f} ms  {'; '.join(result.failures)}")

    _cleanup_graph_complaints(knowledge, results)

    config = {"router": router, "knowledge": knowledge, "llm": llm}
    metrics = compute_metrics(results, cases)
    violations = check_thresholds(metrics, thresholds, router, knowledge)
    report = {
        "config": config,
        "metrics": metrics,
        "violations": violations,
        "results": [r.__dict__ for r in results],
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    name = f"{router}_{knowledge}_{llm}"
    markdown = to_markdown(config, metrics, results, violations)
    for stem in (name, "latest"):
        (reports / f"{stem}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        (reports / f"{stem}.md").write_text(markdown, encoding="utf-8")
    if verbose:
        print("\n" + markdown)
    return report


def _cleanup_graph_complaints(knowledge: str, results: list[CaseResult]) -> None:
    """Los reclamos aprobados en los Evals se replican en Neo4j: se borran para no ensuciar el grafo."""
    if knowledge != "neo4j":
        return
    try:
        from app.rag.neo4j_client import get_driver

        get_driver().execute_query("MATCH (q:Queja {aprobada_por: 'EVAL-OP'}) DETACH DELETE q")
    except Exception as error:  # noqa: BLE001
        print(f"Aviso: no se pudieron limpiar las quejas de evaluación en Neo4j: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evals de punta a punta del asistente bancario")
    parser.add_argument("--router", choices=["rules", "lora", "llm"], default="rules")
    parser.add_argument("--knowledge", choices=["keyword", "neo4j"], default="keyword")
    parser.add_argument("--llm", choices=["fake", "ollama", "openai", "anthropic"], default="fake")
    parser.add_argument("--judge", action="store_true", help="LLM-as-judge de fidelidad (requiere --llm distinto de fake)")
    parser.add_argument("--check", action="store_true", help="Exit 1 si algún umbral no se cumple")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.judge and args.llm == "fake":
        parser.error("--judge requiere un LLM real (--llm ollama|openai|anthropic)")

    report = run(args.router, args.knowledge, args.llm, args.judge, verbose=not args.quiet)
    if args.check and report["violations"]:
        print("UMBRALES NO CUMPLIDOS:\n  " + "\n  ".join(report["violations"]))
        sys.exit(1)


if __name__ == "__main__":
    main()
