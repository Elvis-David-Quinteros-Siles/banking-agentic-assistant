"""Helpers compartidos por los nodos."""

import logging

from langchain_core.messages import BaseMessage, HumanMessage

logger = logging.getLogger("agents")


def last_user_message(messages: list[BaseMessage]) -> HumanMessage | None:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message
    return None


def last_user_text(state: dict) -> str:
    message = last_user_message(state.get("messages", []))
    return str(message.content) if message else ""


def recent_history(messages: list[BaseMessage], max_messages: int = 6) -> list[BaseMessage]:
    """Recorta el historial que se envía al LLM (presupuesto de contexto = costo y latencia)."""
    return messages[-max_messages:]


def log_fallback(node: str, error: Exception) -> None:
    logger.warning("Nodo %s: fallo del LLM (%s). Se usa la ruta determinista.", node, error)
