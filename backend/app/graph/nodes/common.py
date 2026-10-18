"""Helpers compartidos por los nodos."""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

logger = logging.getLogger("agents")


def last_user_message(messages: Sequence[BaseMessage]) -> HumanMessage | None:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message
    return None


def previous_user_text(state: Mapping[str, Any]) -> str:
    """Penúltimo mensaje del cliente (el turno anterior), o "" si es el primero."""
    humans = [m for m in state.get("messages", []) if isinstance(m, HumanMessage)]
    return str(humans[-2].content) if len(humans) >= 2 else ""


def effective_user_text(state: Mapping[str, Any]) -> str:
    """Lo que el cliente está preguntando en realidad: con el contexto del turno anterior si es un seguimiento."""
    return state.get("effective_query") or last_user_text(state)


def last_user_text(state: Mapping[str, Any]) -> str:
    message = last_user_message(state.get("messages", []))
    return str(message.content) if message else ""


def recent_history(messages: Sequence[BaseMessage], max_messages: int = 6) -> list[BaseMessage]:
    """Recorta el historial que se envía al LLM (presupuesto de contexto = costo y latencia)."""
    return list(messages[-max_messages:])


def log_fallback(node: str, error: Exception) -> None:
    logger.warning("Nodo %s: fallo del LLM (%s). Se usa la ruta determinista.", node, error)
