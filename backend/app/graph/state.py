"""Estado compartido del grafo LangGraph (ver docs/ARCHITECTURE.md §2.4)."""

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

# Intenciones soportadas por el router
INTENTS = ("consulta_cuenta", "conocimiento_producto", "queja_disputa", "fuera_de_alcance")

TurnStatus = Literal["completed", "blocked", "clarification"]


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # historial (con PII ya enmascarada)
    customer_id: str  # cliente autenticado (lo fija la API, nunca el LLM)
    intent: str | None
    intent_confidence: float | None
    entities: dict[str, Any]
    guardrail: dict[str, Any]  # {"blocked", "reason", "pii_found"}
    retrieved_context: list[dict[str, Any]]
    citations: list[str]
    complaint_draft: dict[str, Any] | None
    hitl_decision: dict[str, Any] | None
    final_answer: str | None
    status: TurnStatus | None  # estado del turno; "interrupted" lo deduce la API del checkpoint
    degraded: bool  # True si algún nodo cayó a su ruta determinista por fallo del LLM
    # Acumulativo: cada nodo añade su métrica; la API recorta el tramo del turno actual
    trace: Annotated[list[dict[str, Any]], operator.add]


def turn_reset(customer_id: str) -> dict[str, Any]:
    """Campos que se limpian al inicio de cada turno (el checkpoint conserva el turno anterior)."""
    return {
        "customer_id": customer_id,
        "intent": None,
        "intent_confidence": None,
        "entities": {},
        "guardrail": {"blocked": False, "reason": None, "pii_found": []},
        "retrieved_context": [],
        "citations": [],
        "complaint_draft": None,
        "hitl_decision": None,
        "final_answer": None,
        "status": None,
        "degraded": False,
    }
