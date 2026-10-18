"""Construcción del grafo multi-agente (ver docs/ARCHITECTURE.md §2).

START → input_guardrail ─┬─(bloqueado)──────────────────────────► blocked_response → END
                         └─► router ─┬─ consulta_cuenta ─────────► query_agent ─────┐
                                     ├─ conocimiento_producto ───► knowledge_agent ─┤
                                     ├─ queja_disputa ─► complaint_agent            │
                                     │                    ▼                         │
                                     │                hitl_review (interrupt)       │
                                     │                 ├─ approve/edit ─► register ─┤
                                     │                 └─ reject ─► rejected ───────┤
                                     ├─ fuera_de_alcance ────────► blocked_response │
                                     └─ ambiguo / baja confianza ► clarify → END    ▼
                                                                         output_guardrail → END
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes.card_action import (
    action_cancelled_node,
    action_confirmation_node,
    card_action_agent_node,
    execute_card_action_node,
    route_after_card_action,
    route_after_confirmation,
)
from app.graph.nodes.complaint_agent import complaint_agent_node
from app.graph.nodes.hitl import (
    complaint_rejected_node,
    hitl_review_node,
    register_complaint_node,
    route_after_hitl,
)
from app.graph.nodes.input_guardrail import input_guardrail_node, route_after_input_guardrail
from app.graph.nodes.knowledge_agent import knowledge_agent_node
from app.graph.nodes.output_guardrail import output_guardrail_node
from app.graph.nodes.query_agent import query_agent_node
from app.graph.nodes.responses import blocked_response_node, clarify_node
from app.graph.nodes.router import route_after_router, router_node
from app.graph.state import AgentState


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Compila el grafo. El checkpointer persiste el estado por thread_id (necesario para HITL)."""
    builder = StateGraph(AgentState)

    builder.add_node("input_guardrail", input_guardrail_node)
    builder.add_node("router", router_node)
    builder.add_node("query_agent", query_agent_node)
    builder.add_node("knowledge_agent", knowledge_agent_node)
    builder.add_node("complaint_agent", complaint_agent_node)
    builder.add_node("hitl_review", hitl_review_node)
    builder.add_node("register_complaint", register_complaint_node)
    builder.add_node("complaint_rejected", complaint_rejected_node)
    builder.add_node("card_action_agent", card_action_agent_node)
    builder.add_node("action_confirmation", action_confirmation_node)
    builder.add_node("execute_card_action", execute_card_action_node)
    builder.add_node("action_cancelled", action_cancelled_node)
    builder.add_node("blocked_response", blocked_response_node)
    builder.add_node("clarify", clarify_node)
    builder.add_node("output_guardrail", output_guardrail_node)

    builder.add_edge(START, "input_guardrail")
    builder.add_conditional_edges("input_guardrail", route_after_input_guardrail, ["router", "blocked_response"])
    builder.add_conditional_edges(
        "router",
        route_after_router,
        ["query_agent", "knowledge_agent", "complaint_agent", "card_action_agent", "blocked_response", "clarify"],
    )
    builder.add_edge("complaint_agent", "hitl_review")
    builder.add_conditional_edges("hitl_review", route_after_hitl, ["register_complaint", "complaint_rejected"])
    builder.add_conditional_edges(
        "card_action_agent",
        route_after_card_action,
        {"action_confirmation": "action_confirmation", "end": "output_guardrail"},
    )
    builder.add_conditional_edges(
        "action_confirmation", route_after_confirmation, ["execute_card_action", "action_cancelled"]
    )

    for node in (
        "query_agent",
        "knowledge_agent",
        "register_complaint",
        "complaint_rejected",
        "execute_card_action",
        "action_cancelled",
    ):
        builder.add_edge(node, "output_guardrail")
    for node in ("output_guardrail", "blocked_response", "clarify"):
        builder.add_edge(node, END)

    return builder.compile(checkpointer=checkpointer or InMemorySaver())
