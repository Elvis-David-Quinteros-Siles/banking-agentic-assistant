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


def test_react_loop_calls_tool_then_answers(monkeypatch, seeded_db):
    model = ScriptedModel(script=[
        AIMessage(content="", tool_calls=[{"name": "get_accounts", "args": {}, "id": "c1"}], usage_metadata=usage(300, 20)),
        AIMessage(content="Su caja de ahorro ****2101 tiene Bs 12.450,30.", usage_metadata=usage(450, 30)),
    ])
    use_model(monkeypatch, model, query_agent)
    update = query_agent.query_agent_node(state("¿cuál es mi saldo?"))
    assert model.calls == 2
    assert "12.450,30" in update["final_answer"]
    assert update["trace"][0]["tokens_in"] == 750
    assert not update.get("degraded")


def test_react_loop_falls_back_when_llm_fails(monkeypatch, seeded_db):
    use_model(monkeypatch, ScriptedModel(script=[ConnectionError("ollama apagado")]), query_agent)
    update = query_agent.query_agent_node(state("¿cuál es mi saldo?"))
    assert update["degraded"] is True
    assert "Bs 12.450,30" in update["final_answer"]


def test_router_llm_structured_output(monkeypatch):
    monkeypatch.setattr(router.get_settings(), "router_backend", "llm")
    model = ScriptedModel(script=[AIMessage(
        content="",
        tool_calls=[{"name": "RouterOutput", "args": {"intent": "queja_disputa", "confidence": 0.91}, "id": "r1"}],
        usage_metadata=usage(200, 15),
    )])
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
    model = ScriptedModel(script=[AIMessage(
        content="",
        tool_calls=[{"name": "ComplaintDraft", "args": {
            "categoria": "fraude", "prioridad": "baja", "resumen": "El cliente no reconoce una compra.",
        }, "id": "q1"}],
    )])
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
    model = ScriptedModel(script=[AIMessage(content="El DPF tiene una tasa referencial de 5,5% anual a 360 días [POL-008].")])
    use_model(monkeypatch, model, knowledge_agent)
    s = state(question)
    s.update(knowledge_agent.knowledge_agent_node(s))
    s["intent"] = "conocimiento_producto"
    s["guardrail"] = {"pii_found": []}

    result = output_guardrail_node(s)
    assert not result["guardrail"].get("grounding_fallback")
    assert s["citations"] == ["POL-008"]
