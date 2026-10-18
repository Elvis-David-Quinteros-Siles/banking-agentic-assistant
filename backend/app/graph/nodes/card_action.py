"""Acción con confirmación del cliente: bloqueo de tarjeta.

Flujo: card_action_agent (prepara) → action_confirmation (interrupt: el CLIENTE confirma) →
execute_card_action (ejecuta en el core, con auditoría) | action_cancelled.

Principio: las acciones con efecto real NO pasan por el LLM. La identificación de la tarjeta y
del motivo es determinista, la ejecución valida la pertenencia en la base (procedimiento
almacenado en SQL Server) y nada se ejecuta sin un "sí" explícito del cliente.
"""

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from app.db.repository import get_repository
from app.graph.nodes.common import effective_user_text
from app.graph.state import AgentState
from app.observability.tracing import traced
from app.text import normalize

CARD_TYPE_LABEL = {"debito": "débito", "credito": "crédito"}
REASONS = (
    ("robo", ("robaron", "robo", "asaltaron")),
    ("extravío", ("perdi", "extravie", "perdida")),
    ("fraude", ("fraude", "clonaron", "no reconozco", "no autorice")),
)
REPOSITION_NOTE = "La reposición cuesta Bs 30 y se entrega en 5 días hábiles [POL-004]."


def detect_reason(text: str) -> str:
    normalized = normalize(text)
    for reason, words in REASONS:
        if any(w in normalized for w in words):
            return reason
    return "solicitud del cliente"


def pick_card(cards: list[dict], text: str) -> dict | None | str:
    """La tarjeta mencionada; None si no hay ninguna; "ambigua" si hay varias y no se puede elegir."""
    if not cards:
        return None
    normalized = normalize(text)
    for card in cards:
        if card["last4"] in normalized:
            return card
    by_type = [c for c in cards if c["card_type"] in normalized or CARD_TYPE_LABEL[c["card_type"]] in text.lower()]
    if len(by_type) == 1:
        return by_type[0]
    return cards[0] if len(cards) == 1 else "ambigua"


def _answer(text: str, status: str = "completed", **extra) -> dict:
    return {"final_answer": text, "status": status, "messages": [AIMessage(content=text)], **extra}


@traced("card_action_agent")
def card_action_agent_node(state: AgentState) -> dict:
    text = effective_user_text(state)
    cards = get_repository().list_cards(state["customer_id"])
    card = pick_card(cards, text)

    if card is None:
        return _answer(
            "No encontré tarjetas asociadas a su usuario. Si cree que es un error, comuníquese con la línea 24/7."
        )
    if card == "ambigua":
        options = ", ".join(f"{CARD_TYPE_LABEL[c['card_type']]} ****{c['last4']}" for c in cards)
        return _answer(f"Tiene varias tarjetas ({options}). ¿Cuál desea bloquear?", status="clarification")
    assert isinstance(card, dict)
    label = f"tarjeta de {CARD_TYPE_LABEL[card['card_type']]} ****{card['last4']}"
    if card["status"] == "bloqueada":
        return _answer(f"Su {label} ya está bloqueada. {REPOSITION_NOTE}", citations=["POL-004"])

    action = {"accion": "bloquear_tarjeta", "tarjeta": label, "last4": card["last4"], "motivo": detect_reason(text)}
    question = f"¿Confirma que desea bloquear su {label}? Una vez bloqueada no podrá usarla hasta reponerla."
    return {"pending_action": action, "final_answer": question}


def route_after_card_action(state: AgentState) -> str:
    return "action_confirmation" if state.get("pending_action") else "end"


@traced("action_confirmation")
def action_confirmation_node(state: AgentState) -> dict:
    # Al reanudar, LangGraph re-ejecuta este nodo y interrupt() devuelve {"confirm": bool}
    decision = interrupt({"type": "action_confirmation", "payload": state["pending_action"]})
    return {"action_decision": decision}


def route_after_confirmation(state: AgentState) -> str:
    return "execute_card_action" if (state.get("action_decision") or {}).get("confirm") else "action_cancelled"


@traced("execute_card_action")
def execute_card_action_node(state: AgentState, config) -> dict:
    action = state.get("pending_action") or {}
    result = get_repository().block_card(
        state["customer_id"], action.get("last4", ""), action.get("motivo", ""), config["configurable"]["thread_id"]
    )
    if result == "bloqueada":
        text = f"Listo: su {action['tarjeta']} quedó bloqueada. {REPOSITION_NOTE}"
    elif result == "ya_bloqueada":
        text = f"Su {action['tarjeta']} ya estaba bloqueada. {REPOSITION_NOTE}"
    else:
        text = "No pude bloquear la tarjeta. Por seguridad, comuníquese de inmediato con la línea gratuita 24/7."
    return _answer(text, citations=["POL-004"])


@traced("action_cancelled")
def action_cancelled_node(state: AgentState) -> dict:
    return _answer("Entendido, no realicé ningún cambio. Su tarjeta sigue activa.")
