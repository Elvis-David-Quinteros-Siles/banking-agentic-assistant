"""Nodos de respuesta por plantilla: blocked_response y clarify.

No usan LLM: respuesta inmediata, costo 0 y comportamiento 100% predecible, que es lo que
se espera ante un bloqueo de seguridad o una consulta fuera de alcance.
"""

from langchain_core.messages import AIMessage

from app.graph.nodes.common import last_user_text
from app.graph.state import AgentState
from app.observability.tracing import traced
from app.text import normalize

BLOCKED_MESSAGES = {
    "prompt_injection": (
        "No puedo procesar esa solicitud. Estoy aquí para ayudarle con sus cuentas, "
        "productos del banco o reclamos."
    ),
    "acceso_no_autorizado": (
        "Por seguridad solo puedo brindar información de las cuentas de la sesión actual. "
        "Si necesita información de otra persona, ella debe consultarla directamente."
    ),
    "fuera_de_alcance": (
        "Esa consulta está fuera de lo que puedo atender. Puedo ayudarle con saldos y movimientos, "
        "información de productos y políticas del banco, o registrar un reclamo."
    ),
}

CLARIFY_MESSAGE = (
    "¿En qué puedo ayudarle? Puedo:\n"
    "1. Consultar saldos y movimientos de sus cuentas.\n"
    "2. Informarle sobre productos, requisitos y comisiones.\n"
    "3. Registrar un reclamo o una disputa."
)


@traced("blocked_response")
def blocked_response_node(state: AgentState) -> dict:
    reason = state.get("guardrail", {}).get("reason") or "fuera_de_alcance"
    answer = BLOCKED_MESSAGES.get(reason, BLOCKED_MESSAGES["fuera_de_alcance"])
    guardrail = {**state.get("guardrail", {}), "blocked": True, "reason": reason}
    return {"final_answer": answer, "status": "blocked", "guardrail": guardrail, "messages": [AIMessage(content=answer)]}


@traced("clarify")
def clarify_node(state: AgentState) -> dict:
    greeting = normalize(last_user_text(state)).startswith(("hola", "buen", "hey", "que tal"))
    answer = ("¡Hola! " if greeting else "No estoy seguro de haber entendido. ") + CLARIFY_MESSAGE
    return {"final_answer": answer, "status": "clarification", "messages": [AIMessage(content=answer)]}
