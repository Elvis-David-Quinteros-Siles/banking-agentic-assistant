"""Tests de las rutas CON LLM usando un modelo guionado (sin red ni GPU).

Verifican el cableado (loop ReAct, salida estructurada, conteo de tokens) y la
degradación: si el LLM falla o alucina, el nodo responde con su ruta determinista.
"""

from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.graph.nodes import complaint_agent, knowledge_agent, query_agent, router
from app.graph.nodes.output_guardrail import output_guardrail_node


class ScriptedModel(BaseChatModel):
    """Devuelve respuestas predefinidas en orden; si una es Exception, la lanza."""

    script: list[Any]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        item = self.script[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])


def usage(i: int, o: int) -> dict:
    return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}


def use_model(monkeypatch, model, *modules):
    for module in modules:
        monkeypatch.setattr(module, "get_chat_model", lambda: model)


def state(text: str, **extra) -> dict:
    return {"messages": [HumanMessage(content=text, id="m1")], "customer_id": "CLI-001", "entities": {}, **extra}


def test_llm_chooses_tool_args_and_code_renders_statement(monkeypatch, seeded_db):
    # El LLM decide cuenta y cantidad; el extracto (comercios y montos) lo redacta el código
    model = ScriptedModel(
        script=[
            AIMessage(
                content="",
                tool_calls=[{"name": "get_transactions", "args": {"account": "****2101", "limit": 3}, "id": "c1"}],
                usage_metadata=usage(300, 20),
            ),
        ]
    )
    use_model(monkeypatch, model, query_agent)
    update = query_agent.query_agent_node(state("muéstrame mis movimientos"))
    assert model.calls == 1  # sin segunda llamada para redactar
    assert "Cooperativa Eléctrica" in update["final_answer"]
    assert "-Bs 230,40" in update["final_answer"]
    assert update["final_answer"].count("\n- ") == 3
    assert update["trace"][0]["tokens_in"] == 300
    assert not update.get("degraded")


def test_tool_call_for_foreign_account_is_rejected(monkeypatch, seeded_db):
    # Si el LLM inventa una cuenta ajena, no se consulta; tras agotar vueltas, ruta determinista
    call = {"name": "get_transactions", "args": {"account": "****1201"}, "id": "c1"}  # cuenta de CLI-002
    model = ScriptedModel(script=[AIMessage(content="", tool_calls=[call])] * 4)
    use_model(monkeypatch, model, query_agent)
    update = query_agent.query_agent_node(state("muéstrame mis movimientos"))
    assert update["degraded"] is True
    assert "Planilla" not in update["final_answer"]  # movimiento de CLI-002
    assert "Tienda Online XYZ" in update["final_answer"]


def test_balance_answered_from_prefetched_accounts(monkeypatch, seeded_db):
    # Las cuentas van precargadas en el contexto: el saldo se responde en UNA llamada, sin tools
    seen = {}

    class CapturingModel(ScriptedModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen["system"] = messages[0].content
            return super()._generate(messages, stop, run_manager, **kwargs)

    model = CapturingModel(script=[AIMessage(content="Su caja de ahorro ****2101 tiene Bs 12.450,30.")])
    use_model(monkeypatch, model, query_agent)
    query_agent.query_agent_node(state("¿cuál es mi saldo?"))
    assert model.calls == 1
    assert '"saldo_texto": "Bs 12.450,30"' in seen["system"]
    assert "1000452101" not in seen["system"]  # el LLM nunca ve el número completo


def test_evasive_answer_falls_back_to_deterministic(monkeypatch, seeded_db):
    # Falla real observada con qwen2.5:3b: pedía al cliente el número de cuenta en vez de responder
    model = ScriptedModel(
        script=[AIMessage(content="Para darle su saldo necesito el número de cuenta. ¿Podría proporcionarlo?")]
    )
    use_model(monkeypatch, model, query_agent)
    update = query_agent.query_agent_node(state("¿cuál es mi saldo?"))
    assert update["degraded"] is True
    assert "Bs 12.450,30" in update["final_answer"]


def test_react_loop_falls_back_when_llm_fails(monkeypatch, seeded_db):
    use_model(monkeypatch, ScriptedModel(script=[ConnectionError("ollama apagado")]), query_agent)
    update = query_agent.query_agent_node(state("¿cuál es mi saldo?"))
    assert update["degraded"] is True
    assert "Bs 12.450,30" in update["final_answer"]


def test_router_llm_structured_output(monkeypatch):
    monkeypatch.setattr(router.get_settings(), "router_backend", "llm")
    model = ScriptedModel(
        script=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "RouterOutput", "args": {"intent": "queja_disputa", "confidence": 0.91}, "id": "r1"}
                ],
                usage_metadata=usage(200, 15),
            )
        ]
    )
    use_model(monkeypatch, model, router)
    update = router.router_node(state("me cobraron Bs 350 dos veces"))
    assert update["intent"] == "queja_disputa"
    assert update["entities"]["monto"] == 350.0  # la entidad determinista se conserva
    assert update["trace"][0]["tokens_out"] == 15


def test_router_llm_invalid_output_degrades_to_rules(monkeypatch):
    monkeypatch.setattr(router.get_settings(), "router_backend", "llm")
    use_model(monkeypatch, ScriptedModel(script=[AIMessage(content="no sé, quizás una queja")]), router)
    update = router.router_node(state("quiero ver mi saldo"))
    assert update["degraded"] is True
    assert update["intent"] == "consulta_cuenta"


def test_complaint_priority_is_deterministic(monkeypatch):
    # Aunque el LLM diga "baja", un fraude siempre es prioridad alta
    model = ScriptedModel(
        script=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ComplaintDraft",
                        "args": {
                            "categoria": "fraude",
                            "prioridad": "baja",
                            "resumen": "El cliente no reconoce una compra.",
                        },
                        "id": "q1",
                    }
                ],
            )
        ]
    )
    use_model(monkeypatch, model, complaint_agent)
    update = complaint_agent.complaint_agent_node(state("no reconozco una compra"))
    assert update["complaint_draft"]["prioridad"] == "alta"


def test_hallucinated_number_is_replaced_by_extractive_answer(monkeypatch):
    # El LLM inventa un límite de Bs 5.000 que no está en la política
    model = ScriptedModel(script=[AIMessage(content="El límite diario de retiro es de Bs 5.000 [POL-003].")])
    use_model(monkeypatch, model, knowledge_agent)
    s = state("¿cuál es el límite de retiro en cajero de la tarjeta de débito?")
    s.update(knowledge_agent.knowledge_agent_node(s))
    s["intent"] = "conocimiento_producto"
    s["guardrail"] = {"pii_found": []}

    result = output_guardrail_node(s)
    assert result["guardrail"]["grounding_fallback"] is True
    assert "5.000" not in result["final_answer"]
    assert "3.000" in result["final_answer"]


@pytest.mark.parametrize("question", ["¿Qué tasa tiene el DPF a 360 días?"])
def test_grounded_llm_answer_is_kept(monkeypatch, question):
    model = ScriptedModel(
        script=[AIMessage(content="El DPF tiene una tasa referencial de 5,5% anual a 360 días [POL-008].")]
    )
    use_model(monkeypatch, model, knowledge_agent)
    s = state(question)
    s.update(knowledge_agent.knowledge_agent_node(s))
    s["intent"] = "conocimiento_producto"
    s["guardrail"] = {"pii_found": []}

    result = output_guardrail_node(s)
    assert not result["guardrail"].get("grounding_fallback")
    assert s["citations"] == ["POL-008"]


def test_empty_tool_response_retries_without_tools(monkeypatch, seeded_db):
    # Falla real con qwen2.5:3b + Ollama: tool call malformada descartada -> contenido vacío
    model = ScriptedModel(
        script=[
            AIMessage(content=""),
            AIMessage(content="Caja de ahorro ****2101: Bs 12.450,30\nCaja de ahorro ****2102: USD 860,00"),
        ]
    )
    use_model(monkeypatch, model, query_agent)
    update = query_agent.query_agent_node(state("¿cuál es mi saldo?"))
    assert model.calls == 2
    assert not update.get("degraded")
    assert "USD 860,00" in update["final_answer"]


def test_missing_account_is_appended_when_no_specific_account_requested(monkeypatch, seeded_db):
    # Observado con qwen2.5:3b: mostraba la cuenta en Bs y omitía la de USD
    model = ScriptedModel(script=[AIMessage(content="Su caja de ahorro ****2101 tiene Bs 12.450,30.")])
    use_model(monkeypatch, model, query_agent)
    update = query_agent.query_agent_node(state("¿cuál es mi saldo?"))
    assert "Bs 12.450,30" in update["final_answer"]
    assert "USD 860,00" in update["final_answer"]


def test_account_mention_is_not_evasive():
    assert not query_agent.EVASIVE_RE.search("Su caja de ahorro con número de cuenta ****2101 tiene Bs 12.450,30.")
    assert query_agent.EVASIVE_RE.search("Para darle su saldo necesito el número de cuenta.")
    assert query_agent.EVASIVE_RE.search("¿Podría proporcionarme su cuenta?")


def test_citations_attributed_by_evidence():
    docs = [
        {"id": "PRD-DPF", "title": "Depósito a Plazo Fijo", "text": "Inversión a plazo fijo de 30 a 1080 días."},
        {
            "id": "POL-008",
            "title": "Plazos y tasas del DPF",
            "text": "Plazos de 30 a 1080 días. Tasa 5,5% anual a 360 días.",
        },
    ]
    # Respuesta real de qwen2.5:3b: datos de POL-008 pero citando la ficha del producto
    answer = "[PRD-DPF] indica plazos de 30 a 1080 días con una tasa del 5,5% a 360 días."
    assert "POL-008" in knowledge_agent.attribute_citations(answer, docs)
    # ID inventado que contiene uno real
    assert knowledge_agent.attribute_citations("[SOLICITUD-PRD-DPF] Sí, puede invertir.", docs) == ["PRD-DPF"]


def test_internal_context_leak_falls_back(monkeypatch, seeded_db):
    # Observado con qwen2.5:3b: devolvió literalmente su contexto ("CUENTAS: [{...}]")
    leak = 'CUENTAS: [{"cuenta": "****2101", "saldo_texto": "Bs 12.450,30"}]'
    use_model(monkeypatch, ScriptedModel(script=[AIMessage(content=leak)]), query_agent)
    update = query_agent.query_agent_node(state("¿cuál es mi saldo?"))
    assert update["degraded"] is True
    assert "CUENTAS:" not in update["final_answer"]
    assert "Bs 12.450,30" in update["final_answer"]


def test_statement_header_includes_balance(monkeypatch, seeded_db):
    # Observado: ante "saldo de mi cuenta corriente" el LLM eligió la tool de movimientos
    call = {"name": "get_transactions", "args": {"account": "****1201"}, "id": "c1"}
    use_model(monkeypatch, ScriptedModel(script=[AIMessage(content="", tool_calls=[call])]), query_agent)
    s = state("quiero ver el saldo de mi cuenta corriente")
    s["customer_id"] = "CLI-002"
    update = query_agent.query_agent_node(s)
    assert "Bs 48.210,75" in update["final_answer"]
