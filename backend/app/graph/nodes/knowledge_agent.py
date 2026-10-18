"""Subagente de conocimiento: RAG sobre productos y políticas con citas obligatorias.

Reglas anti-alucinación:
1. Si ningún documento supera KNOWLEDGE_MIN_SCORE, se ABSTIENE (no llama al LLM).
2. El prompt exige responder solo con el contexto y citar [ID].
3. El output_guardrail verifica que la respuesta esté soportada por el contexto.
"""

import re

from langchain_core.messages import AIMessage, SystemMessage

from app.config import get_settings
from app.graph.nodes.common import last_user_text, log_fallback
from app.graph.state import AgentState
from app.llm.pricing import usage_from_message
from app.llm.provider import get_chat_model, model_name
from app.observability.tracing import traced
from app.tools.knowledge_base import get_retriever

ABSTAIN_MESSAGE = (
    "No encontré información sobre eso en nuestras políticas y productos. "
    "Le sugiero consultar con un ejecutivo en agencia o en la línea gratuita."
)

SYSTEM_PROMPT = """Eres el agente de conocimiento de un banco. Responde en español, breve y claro.
Usa ÚNICAMENTE la información del CONTEXTO. Si el contexto no responde la pregunta, di que no tienes esa información.
Cita cada dato con el id del documento entre corchetes, por ejemplo [POL-003]. No inventes montos, tasas ni plazos."""

CITATION_RE = re.compile(r"\[((?:POL|PRD)-[A-Z0-9-]+)\]")


def format_context(docs: list[dict]) -> str:
    return "\n\n".join(f"[{d['id']}] {d['title']}: {d['text']}" for d in docs)


def extractive_answer(docs: list[dict]) -> str:
    """Respuesta sin LLM: cita textual de los documentos más relevantes."""
    top = docs[:2]
    return "\n".join(f"Según «{d['title']}» [{d['id']}]: {d['text']}" for d in top)


@traced("knowledge_agent")
def knowledge_agent_node(state: AgentState) -> dict:
    settings = get_settings()
    question = last_user_text(state)
    retriever = get_retriever(settings.seed_dir / "knowledge.json")
    docs = retriever.search(question, k=settings.knowledge_top_k, product_hint=state.get("entities", {}).get("producto"))
    docs = [d for d in docs if d["score"] >= settings.knowledge_min_score]

    update: dict = {"status": "completed", "retrieved_context": docs}
    if not docs:
        update.update({"final_answer": ABSTAIN_MESSAGE, "citations": [], "messages": [AIMessage(content=ABSTAIN_MESSAGE)]})
        return update

    answer: str
    if get_chat_model() is not None:
        try:
            response = get_chat_model().invoke([
                SystemMessage(content=SYSTEM_PROMPT),
                ("human", f"CONTEXTO:\n{format_context(docs)}\n\nPREGUNTA: {question}"),
            ])
            answer = str(response.content)
            update["_usage"] = usage_from_message(response, model_name())
        except Exception as error:  # noqa: BLE001
            log_fallback("knowledge_agent", error)
            answer = extractive_answer(docs)
            update["degraded"] = True
    else:
        answer = extractive_answer(docs)

    retrieved_ids = {d["id"] for d in docs}
    cited = [c for c in dict.fromkeys(CITATION_RE.findall(answer)) if c in retrieved_ids]
    update.update({"final_answer": answer, "citations": cited, "messages": [AIMessage(content=answer)]})
    return update
