"""Estado compartido del grafo LangGraph (ver docs/ARCHITECTURE.md §2.4).

En la Fase 1 solo se usan messages, customer_id, intent, intent_confidence, entities,
final_answer y trace. El resto de campos se declara desde ya para que el contrato del
estado no cambie entre fases.
"""

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

# Intenciones soportadas por el router
INTENTS = ("consulta_cuenta", "conocimiento_producto", "queja_disputa", "fuera_de_alcance")


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # historial de la conversación
    customer_id: str  # cliente autenticado (lo fija la API, nunca el LLM)
    intent: str | None
    intent_confidence: float | None
    entities: dict[str, Any]
    guardrail: dict[str, Any]
    retrieved_context: list[dict[str, Any]]
    citations: list[str]
    complaint_draft: dict[str, Any] | None
    hitl_decision: dict[str, Any] | None
    final_answer: str | None
    # Acumulativo: cada nodo añade su métrica; la API recorta el tramo del turno actual
    trace: Annotated[list[dict[str, Any]], operator.add]
