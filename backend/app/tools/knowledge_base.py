"""Recuperación de conocimiento (productos y políticas).

- KeywordRetriever: BM25 en memoria sobre data/seed/knowledge.json. Sin dependencias;
  es el backend "keyword" y el fallback si Neo4j no está disponible.
- Neo4jHybridRetriever (app/rag/neo4j_retriever.py): vector + full-text + expansión de grafo.

Ambos cumplen el protocolo Retriever y filtran internamente lo irrelevante, de modo que el
knowledge_agent no sabe ni le importa qué backend está activo.
"""

import json
import logging
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings
from app.text import normalize

logger = logging.getLogger("knowledge")

STOPWORDS = set(
    [
        "de",
        "la",
        "el",
        "en",
        "y",
        "a",
        "los",
        "las",
        "del",
        "un",
        "una",
        "que",
        "por",
        "para",
        "con",
        "se",
        "su",
        "sus",
        "al",
        "lo",
        "como",
        "mas",
        "o",
        "es",
        "cual",
        "cuales",
        "mi",
        "me",
        "tengo",
        "hay",
        "son",
        "esta",
        "este",
        "cuanto",
        "cuanta",
        "puedo",
        "quiero",
        "saber",
        "sobre",
        "tiene",
    ]
)


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", normalize(text))
    # Stemming mínimo: plural simple ("requisitos" -> "requisito")
    return [t[:-1] if len(t) > 4 and t.endswith("s") else t for t in tokens if t not in STOPWORDS]


class Retriever(Protocol):
    # Seguimientos multi-turno: los retrievers densos (embeddings) rinden mejor con la pregunta anterior
    # completa; el léxico (BM25), con el mensaje actual + el producto (medido en Evals M02/M03)
    prefers_full_context: bool

    def search(self, query: str, k: int = 4, product_hint: str | None = None) -> list[dict]: ...


PRODUCT_DOC_WEIGHT = 0.75


class KeywordRetriever:
    prefers_full_context = False
    """BM25 sobre documentos de política y producto. Score normalizado a [0, 1]."""

    def __init__(self, documents: list[dict], min_score: float = 0.0, k1: float = 1.5, b: float = 0.75):
        self.documents = documents
        self.min_score = min_score
        self.k1, self.b = k1, b
        self.doc_tokens = [tokenize(f"{d['title']} {d['title']} {d['text']}") for d in documents]
        self.avg_len = sum(map(len, self.doc_tokens)) / max(len(self.doc_tokens), 1)
        df = Counter(t for tokens in self.doc_tokens for t in set(tokens))
        n = len(documents)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def _bm25(self, query_tokens: list[str], index: int) -> float:
        tokens = self.doc_tokens[index]
        freqs = Counter(tokens)
        score = 0.0
        for t in query_tokens:
            if t not in freqs:
                continue
            tf = freqs[t]
            score += (
                self.idf[t] * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * len(tokens) / self.avg_len))
            )
        return score

    def search(self, query: str, k: int = 4, product_hint: str | None = None) -> list[dict]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        # Score máximo teórico aproximado para normalizar (todas las palabras con tf=1)
        max_score = sum(self.idf.get(t, 0.0) for t in query_tokens) * (self.k1 + 1) / (1 + self.k1) or 1.0
        scored = []
        for i, doc in enumerate(self.documents):
            score = self._bm25(query_tokens, i) / max_score
            if doc["type"] == "producto":
                # Las fichas de producto son cortas y repiten su nombre: BM25 las favorece por longitud,
                # pero las respuestas (montos, plazos, requisitos) están en las políticas (Evals K03/K05/M03)
                score *= PRODUCT_DOC_WEIGHT
            if product_hint and product_hint in doc.get("product_tags", []):
                score += 0.1  # pequeño boost por la entidad que extrajo el router
            if score > 0 and min(score, 1.0) >= self.min_score:
                scored.append({**doc, "score": round(min(score, 1.0), 3)})
        return sorted(scored, key=lambda d: d["score"], reverse=True)[:k]


# Mapeo de entidades del router a productos del catálogo
PRODUCT_TAGS = {
    "PRD-AHO-BOB": ["caja_ahorro"],
    "PRD-AHO-USD": ["caja_ahorro"],
    "PRD-CTE": ["cuenta_corriente"],
    "PRD-TDB": ["tarjeta", "tarjeta_debito"],
    "PRD-TCR": ["tarjeta", "tarjeta_credito"],
    "PRD-DPF": ["dpf"],
    "PRD-VIV": ["credito_vivienda"],
    "PRD-PYME": ["credito_pyme"],
    "PRD-BXI": ["banca_internet", "qr", "transferencia"],
}


def load_documents(seed_file: Path) -> list[dict]:
    """Convierte productos y políticas del seed en documentos recuperables con id citable."""
    data = json.loads(seed_file.read_text(encoding="utf-8"))
    documents = []
    for p in data["products"]:
        documents.append(
            {
                "id": p["producto_id"],
                "type": "producto",
                "title": p["nombre"],
                "text": p["descripcion"],
                "product_tags": PRODUCT_TAGS.get(p["producto_id"], []),
            }
        )
    for pol in data["policies"]:
        tags = sorted({tag for pid in pol["productos"] for tag in PRODUCT_TAGS.get(pid, [])})
        documents.append(
            {
                "id": pol["politica_id"],
                "type": "politica",
                "title": pol["titulo"],
                "text": pol["texto"],
                "product_tags": tags,
            }
        )
    return documents


@lru_cache
def get_keyword_retriever() -> KeywordRetriever:
    settings = get_settings()
    return KeywordRetriever(
        load_documents(settings.seed_dir / "knowledge.json"), min_score=settings.knowledge_min_score
    )


def product_id_for_tag(tag: str | None) -> str | None:
    """Entidad del router ("tarjeta_debito") -> producto del catálogo ("PRD-TDB")."""
    for product_id, tags in PRODUCT_TAGS.items():
        if tag in tags:
            return product_id
    return None


@lru_cache
def get_retriever() -> Retriever:
    """Backend configurado en KNOWLEDGE_BACKEND; si es neo4j y no responde, cae a keyword."""
    settings = get_settings()
    if settings.knowledge_backend == "pgvector":
        from app.rag.embeddings import get_embedder
        from app.rag.pgvector_retriever import PgVectorHybridRetriever, pgvector_available

        if pgvector_available():
            return PgVectorHybridRetriever(
                settings.pgvector_dsn, get_embedder(), settings.neo4j_min_similarity, settings.rrf_k
            )
        logger.warning("KNOWLEDGE_BACKEND=pgvector pero PostgreSQL no responde o está vacío: se usa BM25 en memoria")
        return get_keyword_retriever()
    if settings.knowledge_backend == "neo4j":
        from app.rag.embeddings import get_embedder
        from app.rag.neo4j_client import get_driver, neo4j_available
        from app.rag.neo4j_retriever import Neo4jHybridRetriever

        if neo4j_available():
            return Neo4jHybridRetriever(get_driver(), get_embedder(), settings.neo4j_min_similarity, settings.rrf_k)
        logger.warning("KNOWLEDGE_BACKEND=neo4j pero Neo4j no responde o está vacío: se usa BM25 en memoria")
    return get_keyword_retriever()
