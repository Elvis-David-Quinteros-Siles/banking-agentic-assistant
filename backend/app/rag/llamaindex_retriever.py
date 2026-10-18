"""Cuarto backend de conocimiento: recuperación híbrida con LlamaIndex (KNOWLEDGE_BACKEND=llamaindex).

Misma tarea que Neo4j y pgvector, resuelta con las piezas estándar del framework:
- VectorStoreIndex en memoria con los MISMOS embeddings (fastembed, 384 dims), vía un adaptador
  BaseEmbedding: la comparación mide el algoritmo de recuperación, no el modelo de embeddings.
- BM25Retriever (bm25s, stemmer en español) para el canal léxico.
- Un tercer canal vectorial filtrado por la entidad de producto (MetadataFilters) si el router la detectó.
- QueryFusionRetriever en modo reciprocal_rerank (RRF) sin generación de consultas por LLM.

No tiene la expansión por relaciones producto → política del grafo: es justamente la diferencia
que miden los Evals (ver evals/README.md).
"""

import logging
from functools import lru_cache
from typing import Any

from llama_index.core import Settings as LlamaSettings
from llama_index.core import VectorStoreIndex
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.llms import MockLLM
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores import FilterOperator, MetadataFilter, MetadataFilters
from llama_index.retrievers.bm25 import BM25Retriever
from pydantic import PrivateAttr

from app.rag.embeddings import Embedder

logging.getLogger("bm25s").setLevel(logging.WARNING)  # sin el log DEBUG de cada indexado


class BankEmbedding(BaseEmbedding):
    """Adaptador: el Embedder del proyecto como embedding de LlamaIndex."""

    _embedder: Any = PrivateAttr()

    def __init__(self, embedder: Embedder, **kwargs: Any):
        super().__init__(model_name="bank-fastembed", **kwargs)
        self._embedder = embedder

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embedder.embed([query])[0]

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embedder.embed([text])[0]

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.embed(texts)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)


def _cosine01(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5) or 1.0
    return (1 + dot / norm) / 2  # misma escala que Neo4j y pgvector


class LlamaIndexHybridRetriever:
    prefers_full_context = True

    def __init__(self, documents: list[dict], embedder: Embedder, min_similarity: float, top_n: int = 8):
        LlamaSettings.llm = MockLLM()  # sin OpenAI implícito: el framework no debe llamar a ningún LLM
        self.embed_model = BankEmbedding(embedder)
        self.min_similarity = min_similarity
        self.top_n = top_n
        self.docs = {d["id"]: d for d in documents}
        nodes = [
            TextNode(
                id_=d["id"],
                text=f"{d['title']}. {d['text']}",
                metadata={"doc_id": d["id"], "type": d["type"], "product_tags": d["product_tags"]},
            )
            for d in documents
        ]
        self.index = VectorStoreIndex(nodes, embed_model=self.embed_model)
        self.vectors = {n.id_: self.embed_model.get_text_embedding(n.text) for n in nodes}
        self.bm25 = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=top_n, language="spanish")

    def _retrievers(self, product_hint: str | None) -> list:
        retrievers = [self.index.as_retriever(similarity_top_k=self.top_n), self.bm25]
        if product_hint:
            filters = MetadataFilters(
                filters=[MetadataFilter(key="product_tags", value=product_hint, operator=FilterOperator.CONTAINS)]
            )
            retrievers.append(self.index.as_retriever(similarity_top_k=4, filters=filters))
        return retrievers

    def search(self, query: str, k: int = 4, product_hint: str | None = None) -> list[dict]:
        fusion = QueryFusionRetriever(
            self._retrievers(product_hint),
            mode="reciprocal_rerank",  # type: ignore[arg-type]
            num_queries=1,  # sin reescritura de la consulta por LLM
            similarity_top_k=k * 2,
            use_async=False,
            llm=MockLLM(),
        )
        hits = fusion.retrieve(query)
        if not hits:
            return []
        query_vector = self.embed_model.get_query_embedding(query)
        similarity = {h.node.node_id: _cosine01(query_vector, self.vectors[h.node.node_id]) for h in hits}
        if max(similarity.values()) < self.min_similarity:
            return []  # abstención, mismo criterio que los otros backends
        results = []
        for hit in hits:
            doc_id = hit.node.node_id
            if similarity[doc_id] < self.min_similarity - 0.1:
                continue
            d = self.docs[doc_id]
            results.append(
                {
                    "id": doc_id,
                    "type": d["type"],
                    "title": d["title"],
                    "text": d["text"],
                    "score": round(similarity[doc_id], 3),
                    "rrf": round(float(hit.score or 0.0), 5),
                    "product_tags": d["product_tags"],
                }
            )
        return results[:k]


@lru_cache
def get_llamaindex_retriever() -> LlamaIndexHybridRetriever:
    from app.config import get_settings
    from app.rag.embeddings import get_embedder
    from app.tools.knowledge_base import load_documents

    settings = get_settings()
    documents = load_documents(settings.seed_dir / "knowledge.json")
    return LlamaIndexHybridRetriever(documents, get_embedder(), settings.neo4j_min_similarity)
