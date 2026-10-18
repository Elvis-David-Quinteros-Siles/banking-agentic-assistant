"""Nodo input_guardrail: enmascara PII y bloquea prompt injection antes de cualquier LLM."""

from langchain_core.messages import HumanMessage

from app.graph.nodes.common import last_user_message
from app.graph.state import AgentState
from app.guardrails.injection import check_injection
from app.guardrails.pii import mask_pii
from app.observability.tracing import traced


@traced("input_guardrail")
def input_guardrail_node(state: AgentState) -> dict:
    message = last_user_message(state.get("messages", []))
    text = str(message.content) if message else ""

    pii = mask_pii(text)
    reason = check_injection(pii.masked_text, state.get("customer_id", ""))
    update: dict = {"guardrail": {"blocked": reason is not None, "reason": reason, "pii_found": pii.found}}

    if pii.found and message is not None:
        # Mismo id => add_messages REEMPLAZA el mensaje original: la PII no queda en el checkpoint
        update["messages"] = [HumanMessage(content=pii.masked_text, id=message.id)]
    return update


def route_after_input_guardrail(state: AgentState) -> str:
    return "blocked_response" if state["guardrail"]["blocked"] else "router"
