"""Nodo router — backend "rules" (baseline por palabras clave).

En la Fase 2 se suma el backend "llm" y en la Fase 4 el backend "lora". Este baseline
se mantiene como fallback y como piso de comparación en los Evals.
"""

import re
import unicodedata

from langchain_core.messages import HumanMessage

from app.graph.state import AgentState
from app.observability.tracing import traced

# Palabras clave por intención (sin tildes; el texto se normaliza antes de comparar)
KEYWORDS: dict[str, tuple[str, ...]] = {
    "consulta_cuenta": (
        "saldo", "movimiento", "movimientos", "extracto", "cuanto tengo", "mi cuenta",
        "transferencia", "deposito", "retiro", "ultimas transacciones",
    ),
    "conocimiento_producto": (
        "requisito", "requisitos", "comision", "comisiones", "tasa", "interes", "tarjeta",
        "credito", "prestamo", "dpf", "plazo fijo", "como abro", "abrir una cuenta", "limite",
        "politica", "producto",
    ),
    "queja_disputa": (
        "queja", "reclamo", "reclamar", "disputa", "no reconozco", "cobro indebido",
        "doble cobro", "me cobraron", "fraude", "mal atendido", "molesto",
    ),
}


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para que 'Comisión' y 'comision' coincidan."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def classify(text: str) -> tuple[str, float]:
    """Devuelve (intención, confianza). Confianza = proporción de coincidencias de la ganadora."""
    normalized = normalize(text)
    scores = {
        intent: sum(1 for kw in kws if re.search(rf"\b{re.escape(kw)}\b", normalized))
        for intent, kws in KEYWORDS.items()
    }
    total = sum(scores.values())
    if total == 0:
        return "fuera_de_alcance", 0.5
    best = max(scores, key=scores.get)
    return best, round(scores[best] / total, 2)


def last_user_text(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


@traced("router")
def router_node(state: AgentState) -> dict:
    intent, confidence = classify(last_user_text(state))
    return {"intent": intent, "intent_confidence": confidence, "entities": {}}
