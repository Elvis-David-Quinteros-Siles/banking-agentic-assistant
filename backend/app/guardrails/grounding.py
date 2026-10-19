"""Chequeo de grounding léxico para respuestas RAG.

Tres señales, de costo cero:
1. Números no soportados: cualquier cifra de la respuesta (monto, tasa, plazo) que no
   esté en el contexto. Es la alucinación más peligrosa en banca → falla dura.
2. Soporte léxico: fracción de palabras de contenido de la respuesta presentes en el contexto.
3. "Es gratis" contra un precio: la respuesta dice que algo no tiene costo y el contexto le
   pone precio. No trae cifras inventadas ni palabras ajenas, así que 1 y 2 no lo ven (lo
   encontró el red teaming: "la reposición es gratuita", cuando cuesta Bs 30).
"""

import re
from dataclasses import dataclass, field

from app.text import normalize
from app.tools.knowledge_base import tokenize

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
LIST_MARKER_RE = re.compile(r"(?m)^\s*\d+[.)]\s")  # "1. ", "2) " no son datos
CITATION_RE = re.compile(r"\[(?:POL|PRD)-[A-Z0-9-]+\]")
# Dígitos enmascarados ("****1111") y marcadores de PII: no son datos del contexto
# (falso positivo observado con LLM real: repetía "[TARJETA ****1111]" de la pregunta)
MASKED_RE = re.compile(r"\[(?:TARJETA[^\]]*|CI|CELULAR|EMAIL)\]|\*{2,}\d{1,4}")


def _canonical_number(raw: str) -> str:
    return raw.replace(".", "").replace(",", "")


def numbers_in(text: str) -> set[str]:
    """Cifras canónicas de un texto (sin separadores), excluyendo citas, enmascarados y viñetas."""
    clean = LIST_MARKER_RE.sub(" ", MASKED_RE.sub(" ", CITATION_RE.sub(" ", text)))
    return {_canonical_number(n) for n in NUMBER_RE.findall(clean)}


FREE_RE = re.compile(
    r"\b(gratis|gratuit[oa]s?|sin (ningun )?(costo|cargo|comision)\w*|"
    r"no (tiene|tienen|incluye|incluyen|genera|generan|cobra|cobran|cuesta|cuestan) (ningun |ninguna )?"
    r"(costo|cargo|comision|nada)\w*|free of charge|no cost)\b"
)
PRICE_RE = re.compile(r"\b(bs|usd|\$us)\.? ?\d|\d+(,\d+)? ?%|\b(cuesta|cuestan|comision|costo|tarifa)\b")
SENTENCE_RE = re.compile(r"(?<=[.!?;\n])\s+")
# Palabras demasiado generales para atribuir un precio a algo concreto
GENERIC_WORDS = {"tarjeta", "cuenta", "cliente", "banco", "credito", "debito", "servicio", "costo", "adicional"}


def cost_contradictions(answer: str, context_docs: list[dict]) -> list[str]:
    """Frases de la respuesta que dicen "gratis" sobre algo que el contexto cobra."""
    priced = [
        set(tokenize(s))
        for d in context_docs
        for s in SENTENCE_RE.split(d["text"])
        if PRICE_RE.search(normalize(s)) and not FREE_RE.search(normalize(s))
    ]
    contradictions = []
    for sentence in SENTENCE_RE.split(answer):
        if not FREE_RE.search(normalize(sentence)):
            continue
        subject = {t for t in tokenize(sentence) if len(t) > 4 and t not in GENERIC_WORDS}
        if any(subject & words for words in priced):
            contradictions.append(sentence.strip())
    return contradictions


@dataclass
class GroundingResult:
    support: float  # 0..1
    unsupported_numbers: list[str]
    cost_contradictions: list[str] = field(default_factory=list)


def check_grounding(answer: str, context_docs: list[dict]) -> GroundingResult:
    context = " ".join(f"{d['title']} {d['text']}" for d in context_docs)
    context_tokens = set(tokenize(context))
    context_numbers = {_canonical_number(n) for n in NUMBER_RE.findall(context)}

    clean = LIST_MARKER_RE.sub(" ", MASKED_RE.sub(" ", CITATION_RE.sub(" ", answer)))
    numbers = [n for n in NUMBER_RE.findall(clean) if _canonical_number(n) not in context_numbers]
    words = [t for t in tokenize(clean) if len(t) > 3 and not t.isdigit()]
    support = 1.0 if not words else round(sum(w in context_tokens for w in words) / len(words), 2)
    return GroundingResult(
        support=support, unsupported_numbers=numbers, cost_contradictions=cost_contradictions(answer, context_docs)
    )
