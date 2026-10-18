"""Estimación de tokens y costo por turno si los agentes usaran un LLM (Cost & Latency Awareness).

Construye los prompts REALES que enviaría cada nodo (system prompt + contexto recuperado + historial
+ resultados de tools) para los casos de evals/cases.yaml, y los cuenta con el tokenizer de Qwen2.5.
Otros tokenizers (OpenAI, Anthropic) difieren aproximadamente ±20% para texto en español.

Las salidas (tokens generados) se estiman con supuestos explícitos, porque dependen del modelo.

Uso:  python evals/estimate_cost.py [--knowledge neo4j]
Salida: evals/reports/cost_estimate.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

# Supuestos de tokens de SALIDA por nodo (no se pueden medir sin el LLM real)
OUTPUT_TOKENS = {"router": 25, "query_agent": 60, "knowledge_agent": 120, "complaint_agent": 80}
# Precios referenciales (USD / 1M tokens). Verificar en la página del proveedor antes de usarlos.
PRICES = {"gpt-4o-mini": (0.15, 0.60)}
# Ida y vuelta de las tool calls del loop ReAct: esquemas de tools + 1 llamada de tool + respuesta
REACT_CALLS = 2


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge", choices=["keyword", "neo4j"], default="neo4j")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    tmp = Path(tempfile.mkdtemp(prefix="bank-cost-"))
    os.environ.update({"KNOWLEDGE_BACKEND": args.knowledge, "SQLITE_PATH": str(tmp / "bank.db"),
                       "HF_HUB_OFFLINE": "1", "LOG_LEVEL": "WARNING"})

    from transformers import AutoTokenizer

    from app.config import get_settings
    from app.db.sqlite import init_db
    from app.graph.nodes import complaint_agent, knowledge_agent, query_agent, router
    from app.tools.knowledge_base import get_retriever
    from app.db.sqlite import SQLiteRepository
    from app.tools.sql_tools import BankingTools

    settings = get_settings()
    init_db(settings.sqlite_path, settings.seed_dir / "customers.json")
    tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")

    def count(text: str) -> int:
        return len(tokenizer(text, add_special_tokens=False)["input_ids"])

    cases = yaml.safe_load((ROOT / "cases.yaml").read_text(encoding="utf-8"))["cases"]
    tool_schema = json.dumps([t.args_schema.model_json_schema() for t in BankingTools(SQLiteRepository(settings.sqlite_path), "CLI-001").as_langchain_tools()])
    per_node: dict[str, list[int]] = {k: [] for k in OUTPUT_TOKENS}
    per_turn: list[dict] = []

    for case in cases:
        intent = case["expected"].get("intent")
        msg = case["message"]
        turn = {"id": case["id"], "intent": intent, "router": count(router.ROUTER_PROMPT) + count(msg)}
        per_node["router"].append(turn["router"])

        if intent == "conocimiento_producto":
            docs = get_retriever().search(knowledge_agent.retrieval_query(msg), k=settings.knowledge_top_k)
            prompt = knowledge_agent.SYSTEM_PROMPT + f"CONTEXTO:\n{knowledge_agent.format_context(docs)}\n\nPREGUNTA: {msg}"
            turn["knowledge_agent"] = count(prompt)
            per_node["knowledge_agent"].append(turn["knowledge_agent"])
        elif intent == "consulta_cuenta":
            tools = BankingTools(SQLiteRepository(settings.sqlite_path), case["customer"])
            observation = json.dumps(tools.get_accounts(), ensure_ascii=False)
            base = count(query_agent.SYSTEM_PROMPT) + count(tool_schema) + count(msg)
            # 1ª llamada: decide la tool; 2ª: ve el resultado y redacta
            turn["query_agent"] = base * REACT_CALLS + count(observation) + 30
            per_node["query_agent"].append(turn["query_agent"])
        elif intent == "queja_disputa":
            schema = json.dumps(complaint_agent.ComplaintDraft.model_json_schema())
            turn["complaint_agent"] = count(complaint_agent.SYSTEM_PROMPT) + count(schema) + count(msg)
            per_node["complaint_agent"].append(turn["complaint_agent"])
        # Bloqueos y aclaraciones: plantilla, 0 tokens después del router (o antes, si bloquea el guardrail)
        if case["expected"].get("status") == "blocked" and intent is None:
            turn["router"] = 0
        per_turn.append(turn)

    def turn_tokens(t: dict) -> tuple[int, int]:
        nodes = [k for k in OUTPUT_TOKENS if t.get(k)]
        return sum(t[k] for k in nodes), sum(OUTPUT_TOKENS[k] for k in nodes)

    totals = [turn_tokens(t) for t in per_turn]
    mean_in = statistics.mean(i for i, _ in totals)
    mean_out = statistics.mean(o for _, o in totals)
    price_in, price_out = PRICES["gpt-4o-mini"]
    cost_turn = (mean_in * price_in + mean_out * price_out) / 1e6

    # Variante: router LoRA local (sus tokens no se pagan)
    totals_lora = [(i - t["router"], o - (OUTPUT_TOKENS["router"] if t["router"] else 0)) for t, (i, o) in zip(per_turn, totals)]
    cost_turn_lora = (statistics.mean(i for i, _ in totals_lora) * price_in + statistics.mean(o for _, o in totals_lora) * price_out) / 1e6

    report = {
        "tokenizer": "Qwen2.5 (±20% frente a otros tokenizers)",
        "knowledge_backend": args.knowledge,
        "input_tokens_per_node": {k: {"mean": round(statistics.mean(v)), "max": max(v)} for k, v in per_node.items() if v},
        "output_tokens_assumed": OUTPUT_TOKENS,
        "mean_tokens_per_turn": {"in": round(mean_in), "out": round(mean_out)},
        "price_reference": {"model": "gpt-4o-mini", "usd_per_mtok_in": price_in, "usd_per_mtok_out": price_out},
        "cost_per_turn_usd": round(cost_turn, 6),
        "cost_per_turn_usd_lora_router": round(cost_turn_lora, 6),
        "cost_per_100k_turns_usd": round(cost_turn * 100_000, 2),
        "cost_per_100k_turns_usd_lora_router": round(cost_turn_lora * 100_000, 2),
        "router_share_of_input_tokens": round(statistics.mean(t["router"] for t in per_turn) / mean_in, 3),
    }
    (ROOT / "reports" / "cost_estimate.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
