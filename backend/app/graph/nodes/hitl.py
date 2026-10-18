"""Human-in-the-Loop para reclamos: hitl_review (interrupt), register_complaint, complaint_rejected.

Flujo:
1. hitl_review llama a interrupt(): el grafo se pausa y el checkpointer guarda el estado.
2. La API responde status="interrupted" con el borrador.
3. Un operador llama a POST /approve/{thread_id}; la API reanuda con Command(resume=decisión).
4. interrupt() devuelve esa decisión y el grafo continúa hacia registrar o rechazar.
"""

import uuid
from datetime import datetime

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from app.config import get_settings
from app.db.sqlite import connect
from app.graph.state import AgentState
from app.observability.tracing import traced


@traced("hitl_review")
def hitl_review_node(state: AgentState) -> dict:
    # Importante: en la reanudación, LangGraph vuelve a ejecutar este nodo desde el inicio
    # y interrupt() devuelve el valor de resume. Por eso no hay efectos secundarios antes.
    decision = interrupt({"type": "complaint_review", "payload": state["complaint_draft"]})
    return {"hitl_decision": decision}


def route_after_hitl(state: AgentState) -> str:
    return "complaint_rejected" if state["hitl_decision"]["decision"] == "reject" else "register_complaint"


@traced("register_complaint")
def register_complaint_node(state: AgentState, config) -> dict:
    decision = state["hitl_decision"]
    draft = {**state["complaint_draft"], **(decision.get("edited_draft") or {})}
    complaint_id = f"QJ-{uuid.uuid4().hex[:6].upper()}"

    with connect(get_settings().sqlite_path) as conn:
        conn.execute(
            "INSERT INTO complaints (complaint_id, customer_id, thread_id, category, product, amount, currency, "
            "priority, summary, status, reviewer_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                complaint_id, state["customer_id"], config["configurable"]["thread_id"], draft["categoria"],
                draft.get("producto"), draft.get("monto"), draft.get("moneda"), draft["prioridad"],
                draft["resumen"], "registrada", decision["reviewer_id"], datetime.now().isoformat(timespec="seconds"),
            ),
        )

    days = 15 if draft["categoria"] in ("fraude", "cobro_indebido") else 5
    answer = (
        f"Su reclamo fue registrado con el número {complaint_id} (prioridad {draft['prioridad']}). "
        f"El plazo referencial de respuesta es de {days} días hábiles."
    )
    return {
        "complaint_draft": {**draft, "complaint_id": complaint_id},
        "final_answer": answer,
        "status": "completed",
        "messages": [AIMessage(content=answer)],
    }


@traced("complaint_rejected")
def complaint_rejected_node(state: AgentState) -> dict:
    comment = (state["hitl_decision"].get("comment") or "").strip()
    answer = "Un ejecutivo revisó su solicitud y no corresponde registrarla como reclamo."
    if comment:
        answer += f" Observación: {comment}"
    answer += " Si lo desea, puede acercarse a una agencia para más detalles."
    return {"final_answer": answer, "status": "completed", "messages": [AIMessage(content=answer)]}
