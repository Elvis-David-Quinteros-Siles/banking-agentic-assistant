"""LLM-as-judge de fidelidad (faithfulness) para las respuestas del agente de conocimiento.

Pregunta al LLM configurado si CADA afirmación de la respuesta está respaldada por el contexto
recuperado. Complementa al grounding léxico del output_guardrail (barato, en línea) con una
medición más semántica (cara, solo offline en los Evals).

Limitación conocida: si el juez es el mismo modelo que generó la respuesta, tiende a ser
indulgente consigo mismo. En producción se usaría un modelo juez distinto y más capaz, y se
validaría el juez contra una muestra etiquetada por humanos.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field

JUDGE_PROMPT = """Eres un auditor de calidad de un banco. Recibes un CONTEXTO (políticas oficiales) y una RESPUESTA
dada a un cliente. Decide si TODAS las afirmaciones factuales de la respuesta (montos, tasas, plazos,
requisitos, condiciones) están respaldadas por el contexto. Parafrasear está bien; agregar datos que no
están en el contexto NO. Lista las afirmaciones no respaldadas."""


class JudgeVerdict(BaseModel):
    faithful: bool = Field(description="True si todas las afirmaciones están respaldadas por el contexto")
    unsupported_claims: list[str] = Field(default_factory=list)


def make_judge(model: BaseChatModel | None = None) -> Callable[[str, str, list[dict]], dict]:
    if model is None:
        from app.llm.provider import get_chat_model

        model = get_chat_model()
        if model is None:
            raise RuntimeError("El juez necesita un LLM real (LLM_PROVIDER distinto de fake)")
    structured = model.with_structured_output(JudgeVerdict)

    def judge(question: str, answer: str, context: list[dict]) -> dict:
        context_text = "\n".join(f"[{d['id']}] {d['title']}: {d['text']}" for d in context)
        try:
            verdict = structured.invoke([
                SystemMessage(content=JUDGE_PROMPT),
                ("human", f"CONTEXTO:\n{context_text}\n\nPREGUNTA: {question}\n\nRESPUESTA: {answer}"),
            ])
            return verdict.model_dump()
        except Exception as error:  # noqa: BLE001 - un fallo del juez no debe tumbar los Evals
            return {"faithful": False, "unsupported_claims": [f"error del juez: {error}"]}

    return judge
