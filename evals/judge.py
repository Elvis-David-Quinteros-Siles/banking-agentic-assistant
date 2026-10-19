"""LLM-as-judge de fidelidad (faithfulness) para las respuestas del agente de conocimiento.

Pregunta al LLM configurado si CADA afirmación de la respuesta está respaldada por el contexto
recuperado. Complementa al grounding léxico del output_guardrail (barato, en línea) con una
medición más semántica (cara, solo offline en los Evals).

Limitación conocida: si el juez es el mismo modelo que generó la respuesta, tiende a ser
indulgente consigo mismo. En producción se usaría un modelo juez distinto y más capaz, y se
validaría el juez contra una muestra etiquetada por humanos.

Validación determinista del veredicto: con qwen2.5:3b el juez a veces "encuentra" afirmaciones que
la respuesta no contiene (caso M02: marcó "el pago mínimo es el 5% del saldo", una frase de OTRA
política del contexto). Una afirmación no respaldada solo cuenta si aparece en la respuesta; las
descartadas quedan en el reporte (discarded_claims).

El error inverso también ocurre (M02 en el quinto ciclo, 3 de 3 corridas): marcó como no
respaldada "El límite diario de retiro en cajeros automáticos es de Bs 3.000", que es texto LITERAL
de POL-003. Una afirmación cuyas cifras y casi todas sus palabras están en UN documento del
contexto también se descarta. El criterio es estricto (80% de palabras en el mismo documento)
para no esconder paráfrasis que cambian el sentido.
"""

from __future__ import annotations

import re
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


WORD_RE = re.compile(r"[a-záéíóúñü]{4,}")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def claim_in_answer(claim: str, answer: str) -> bool:
    """True si la afirmación está realmente en la respuesta: sus cifras aparecen y comparte al menos
    la mitad de sus palabras de contenido."""
    answer_low = answer.lower()
    numbers = NUM_RE.findall(claim)
    if any(n not in answer for n in numbers):
        return False
    words = set(WORD_RE.findall(claim.lower()))
    if not words:
        return bool(numbers)
    return len({w for w in words if w in answer_low}) / len(words) >= 0.5


def claim_in_context(claim: str, context: list[dict]) -> bool:
    """True si la afirmación está casi literal en un documento: todas sus cifras y >= 80% de sus
    palabras de contenido."""
    numbers = NUM_RE.findall(claim)
    words = set(WORD_RE.findall(claim.lower()))
    for doc in context:
        text = f"{doc.get('title', '')} {doc['text']}".lower()
        if all(n in text for n in numbers) and words and len({w for w in words if w in text}) / len(words) >= 0.8:
            return True
    return False


def validate_verdict(verdict: dict, answer: str, context: list[dict] | None = None) -> dict:
    kept = [
        c
        for c in verdict.get("unsupported_claims", [])
        if claim_in_answer(c, answer) and not claim_in_context(c, context or [])
    ]
    discarded = [c for c in verdict.get("unsupported_claims", []) if c not in kept]
    faithful = verdict.get("faithful", False) or (not kept and bool(discarded))
    return {"faithful": faithful and not kept, "unsupported_claims": kept, "discarded_claims": discarded}


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
            verdict = structured.invoke(
                [
                    SystemMessage(content=JUDGE_PROMPT),
                    ("human", f"CONTEXTO:\n{context_text}\n\nPREGUNTA: {question}\n\nRESPUESTA: {answer}"),
                ]
            )
            return validate_verdict(verdict.model_dump(), answer, context)
        except Exception as error:  # noqa: BLE001 - un fallo del juez no debe tumbar los Evals
            return {"faithful": False, "unsupported_claims": [f"error del juez: {error}"]}

    return judge
