"""Agente eco (solo Fase 1): devuelve el mensaje del usuario junto a la intención detectada.

Sirve para validar el cableado API → grafo → checkpointer antes de introducir LLMs.
Se reemplaza por los subagentes especializados en la Fase 2.
"""

from langchain_core.messages import AIMessage

from app.graph.nodes.router import last_user_text
from app.graph.state import AgentState
from app.observability.tracing import traced


@traced("echo_agent")
def echo_agent_node(state: AgentState) -> dict:
    answer = f"[eco · intención: {state.get('intent')}] {last_user_text(state)}"
    return {"final_answer": answer, "messages": [AIMessage(content=answer)]}
