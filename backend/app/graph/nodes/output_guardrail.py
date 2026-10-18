"""Nodo output_guardrail: última verificación antes de responder al cliente.

1. Grounding (solo knowledge_agent): si la respuesta trae cifras que no están en el
   contexto o casi no se apoya en él, se reemplaza por una respuesta extractiva.
   En la Fase 6 se mide además con un LLM-judge (faithfulness) en los Evals.
2. Enmascara números largos (cuentas/tarjetas) que pudieran filtrarse: solo últimos 4 dígitos.
3. Si el cliente envió PII, se añade un aviso de seguridad.
"""

from langchain_core.messages import AIMessage

from app.graph.nodes.knowledge_agent import extractive_answer
from app.graph.state import AgentState
from app.guardrails.grounding import check_grounding
from app.guardrails.pii import mask_long_numbers
from app.observability.tracing import traced

MIN_LEXICAL_SUPPORT = 0.3
PII_NOTICE = (
    "\n\nPor su seguridad ocultamos datos sensibles de su mensaje. "
    "Nunca comparta números completos de tarjeta ni su cédula por este canal."
)


@traced("output_guardrail")
def output_guardrail_node(state: AgentState) -> dict:
    original = state.get("final_answer") or ""
    answer = original
    guardrail = dict(state.get("guardrail", {}))
    update: dict = {}

    if state.get("intent") == "conocimiento_producto" and state.get("retrieved_context"):
        result = check_grounding(answer, state["retrieved_context"])
        guardrail["grounding_support"] = result.support
        if result.unsupported_numbers or result.support < MIN_LEXICAL_SUPPORT:
            guardrail["grounding_fallback"] = True
            guardrail["unsupported_numbers"] = result.unsupported_numbers
            answer = extractive_answer(state["retrieved_context"])
            update["citations"] = [d["id"] for d in state["retrieved_context"][:2]]

    answer = mask_long_numbers(answer)
    if guardrail.get("pii_found"):
        answer += PII_NOTICE

    update.update({"final_answer": answer, "guardrail": guardrail})

    # Si cambió la respuesta, se reemplaza también el último AIMessage del historial (mismo id)
    if answer != original:
        last_ai = next((m for m in reversed(state.get("messages", [])) if isinstance(m, AIMessage)), None)
        if last_ai is not None:
            update["messages"] = [AIMessage(content=answer, id=last_ai.id)]
    return update
