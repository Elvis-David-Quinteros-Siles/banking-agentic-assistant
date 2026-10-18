"""Chequeo de grounding léxico para respuestas RAG.

Dos señales, de costo cero:
1. Números no soportados: cualquier cifra de la respuesta (monto, tasa, plazo) que no
   esté en el contexto. Es la alucinación más peligrosa en banca → falla dura.
2. Soporte léxico: fracción de palabras de contenido de la respuesta presentes en el contexto.
"""

import re
from dataclasses import dataclass

from app.tools.knowledge_base import tokenize

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
LIST_MARKER_RE = re.compile(r"(?m)^\s*\d+[.)]\s")  # "1. ", "2) " no son datos
CITATION_RE = re.compile(r"\[(?:POL|PRD)-[A-Z0-9-]+\]")


def _canonical_number(raw: str) -> str:
    return raw.replace(".", "").replace(",", "")


@dataclass
class GroundingResult:
    support: float  # 0..1
    unsupported_numbers: list[str]


def check_grounding(answer: str, context_docs: list[dict]) -> GroundingResult:
    context = " ".join(f"{d['title']} {d['text']}" for d in context_docs)
    context_tokens = set(tokenize(context))
    context_numbers = {_canonical_number(n) for n in NUMBER_RE.findall(context)}

    clean = LIST_MARKER_RE.sub(" ", CITATION_RE.sub(" ", answer))
    numbers = [n for n in NUMBER_RE.findall(clean) if _canonical_number(n) not in context_numbers]
    words = [t for t in tokenize(clean) if len(t) > 3 and not t.isdigit()]
    support = 1.0 if not words else round(sum(w in context_tokens for w in words) / len(words), 2)
    return GroundingResult(support=support, unsupported_numbers=numbers)
