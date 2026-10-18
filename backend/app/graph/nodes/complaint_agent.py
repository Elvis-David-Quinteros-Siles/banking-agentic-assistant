"""Subagente de quejas: arma un borrador estructurado del reclamo para revisión humana.

El LLM propone; un operador decide (nodo hitl_review). Ningún reclamo se registra sin
aprobación: es un caso con riesgo regulatorio y reputacional.
"""

from typing import Literal

from langchain_core.messages import AIMessage, SystemMessage
from pydantic import BaseModel, Field

from app.config import get_settings
from app.graph.nodes.common import last_user_text, log_fallback, recent_history
from app.graph.state import AgentState
from app.llm.pricing import usage_from_message
from app.llm.provider import get_chat_model, model_name
from app.observability.tracing import traced
from app.text import normalize

Category = Literal["cobro_indebido", "fraude", "atencion", "transferencia", "producto", "otro"]

PENDING_MESSAGE = (
    "Registré los datos de su reclamo y lo derivé a un ejecutivo para su revisión. "
    "Le confirmaremos el número de seguimiento en cuanto sea aprobado."
)


class ComplaintDraft(BaseModel):
    categoria: Category
    producto: str | None = None
    monto: float | None = None
    moneda: Literal["BOB", "USD"] | None = None
    prioridad: Literal["alta", "media", "baja"] = "media"
    resumen: str = Field(description="Resumen objetivo del reclamo en 1-2 oraciones, en tercera persona")


SYSTEM_PROMPT = """Eres el agente de reclamos de un banco. A partir de la conversación, completa el borrador del reclamo.
Categorías: cobro_indebido, fraude, atencion, transferencia, producto, otro.
El resumen debe ser objetivo, en tercera persona ("El cliente reporta..."), sin opiniones ni promesas."""

CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("fraude", ("fraude", "no autorice", "estafa", "robaron", "clonaron", "no reconozco")),
    ("cobro_indebido", ("doble cobro", "cobraron dos veces", "cobro indebido", "me cobraron", "cargo", "comision")),
    ("transferencia", ("transferencia", "no llego", "no se acredito")),
    ("atencion", ("mal atendido", "atencion", "ejecutivo", "agencia", "fila", "grosero")),
    ("producto", ("tarjeta", "app", "cajero", "atm", "bloqueada")),
]


def rules_category(text: str) -> str:
    normalized = normalize(text)
    for category, keywords in CATEGORY_KEYWORDS:
        if any(k in normalized for k in keywords):
            return category
    return "otro"


def compute_priority(category: str, amount: float | None, currency: str | None) -> str:
    """Prioridad determinista: no se delega al LLM porque define SLA y escalamiento."""
    threshold = get_settings().complaint_high_priority_amount
    amount_bob = (amount or 0) * (6.96 if currency == "USD" else 1)  # tipo de cambio oficial referencial
    if category == "fraude" or amount_bob >= threshold:
        return "alta"
    if category in ("cobro_indebido", "transferencia"):
        return "media"
    return "baja"


def rules_draft(text: str, entities: dict) -> dict:
    category = rules_category(text)
    return ComplaintDraft(
        categoria=category,
        producto=entities.get("producto"),
        monto=entities.get("monto"),
        moneda=entities.get("moneda"),
        resumen=f"El cliente reporta: {text.strip()[:300]}",
    ).model_dump()


@traced("complaint_agent")
def complaint_agent_node(state: AgentState) -> dict:
    text = last_user_text(state)  # ya viene con PII enmascarada por el input_guardrail
    entities = state.get("entities", {})
    update: dict = {}

    draft: dict
    if get_chat_model() is not None:
        try:
            structured = get_chat_model().with_structured_output(ComplaintDraft, include_raw=True)
            result = structured.invoke([SystemMessage(content=SYSTEM_PROMPT), *recent_history(state["messages"])])
            if result.get("parsed") is None:
                raise ValueError(f"salida no parseable: {result.get('parsing_error')}")
            draft = result["parsed"].model_dump()
            update["_usage"] = usage_from_message(result["raw"], model_name())
            # Las entidades deterministas (monto/moneda) prevalecen sobre las del LLM
            draft.update({k: v for k, v in entities.items() if k in ("producto", "monto", "moneda") and v})
        except Exception as error:  # noqa: BLE001
            log_fallback("complaint_agent", error)
            draft = rules_draft(text, entities)
            update["degraded"] = True
    else:
        draft = rules_draft(text, entities)

    draft["prioridad"] = compute_priority(draft["categoria"], draft.get("monto"), draft.get("moneda"))
    draft["customer_id"] = state["customer_id"]

    # final_answer = mensaje que ve el cliente mientras el reclamo espera revisión
    update.update({"complaint_draft": draft, "final_answer": PENDING_MESSAGE})
    return update
