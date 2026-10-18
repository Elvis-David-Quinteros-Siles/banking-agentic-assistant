"""Estado compartido del grafo LangGraph (ver docs/ARCHITECTURE.md §2.4)."""

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

# Intenciones soportadas por el router
INTENTS = ("consulta_cuenta", "conocimiento_producto", "queja_disputa", "accion_tarjeta", "fuera_de_alcance")

TurnStatus = Literal["completed", "blocked", "clarification"]


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # historial (con PII ya enmascarada)
    customer_id: str  # cliente autenticado (lo fija la API, nunca el LLM)
    intent: str | None
    intent_confidence: float | None
    router_stage: str | None  # quién clasificó: rules | lora | llm (la cascada usa reglas o LoRA)
    entities: dict[str, Any]
    guardrail: dict[str, Any]  # {"blocked", "reason", "pii_found"}
    retrieved_context: list[dict[str, Any]]
    citations: list[str]
    complaint_draft: dict[str, Any] | None
    hitl_decision: dict[str, Any] | None
    pending_action: dict[str, Any] | None  # acción preparada que espera la confirmación del cliente
    action_decision: dict[str, Any] | None  # {"confirm": bool}
    final_answer: str | None
    status: TurnStatus | None  # estado del turno; "interrupted" lo deduce la API del checkpoint
    degraded: bool  # True si algún nodo cayó a su ruta determinista por fallo del LLM
    # Multi-turno: intención del último turno con intención clara (NO se reinicia entre turnos)
    last_intent: str | None
    # Pregunta efectiva del turno: si el mensaje es un seguimiento ("¿y en dólares?"), la pregunta
    # anterior + la actual; si no, None y los nodos usan el último mensaje
    effective_query: str | None
    # Acumulativo: cada nodo añade su métrica; la API recorta el tramo del turno actual
    trace: Annotated[list[dict[str, Any]], operator.add]


def turn_reset(customer_id: str) -> dict[str, Any]:
    """Campos que se limpian al inicio de cada turno (el checkpoint conserva el turno anterior)."""
    return {
        "customer_id": customer_id,
        "intent": None,
        "intent_confidence": None,
        "router_stage": None,
        "entities": {},
        "guardrail": {"blocked": False, "reason": None, "pii_found": []},
        "retrieved_context": [],
        "citations": [],
        "complaint_draft": None,
        "hitl_decision": None,
        "pending_action": None,
        "action_decision": None,
        "final_answer": None,
        "status": None,
        "degraded": False,
        "effective_query": None,
    }
