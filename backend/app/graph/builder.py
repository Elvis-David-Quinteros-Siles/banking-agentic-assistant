"""Construcción del grafo LangGraph.

Fase 1:  START → router → echo_agent → END
Fase 2:  el router pasará a tener aristas condicionales hacia los subagentes.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes.echo_agent import echo_agent_node
from app.graph.nodes.router import router_node
from app.graph.state import AgentState


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Compila el grafo. El checkpointer persiste el estado por thread_id.

    En la Fase 1 se usa InMemorySaver; en la Fase 2 se cambia a AsyncSqliteSaver
    para que las interrupciones HITL sobrevivan entre requests y reinicios.
    """
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("echo_agent", echo_agent_node)

    builder.add_edge(START, "router")
    builder.add_edge("router", "echo_agent")
    builder.add_edge("echo_agent", END)

    return builder.compile(checkpointer=checkpointer or InMemorySaver())
