"""RAG híbrido sobre PostgreSQL + pgvector (KNOWLEDGE_BACKEND=pgvector).

Mismo algoritmo que el retriever de Neo4j, para poder compararlos con los Evals:

  pregunta ─┬─ embedding ─► índice HNSW (coseno) sobre kb_documents ─┐
            ├─ full-text (tsvector 'spanish', GIN) ──────────────────┼─► RRF ─► semillas
            └─ entidad del router (product_tags) ─────────────────────┘           │
                       expansión de 1 salto: kb_product_policy (producto → política) ◄┘

La relación producto → política vive en una tabla de unión: un "grafo" relacional de un salto.
Neo4j gana cuando las consultas necesitan varios saltos o relaciones heterogéneas; pgvector gana
en simplicidad operativa si el banco ya opera PostgreSQL.
"""

import logging
from functools import lru_cache

from app.rag.embeddings import Embedder
from app.rag.fusion import lucene_query, rrf

logger = logging.getLogger("pgvector")

VECTOR_SQL = """
SELECT id FROM kb_documents WHERE type = %(type)s
ORDER BY embedding <=> %(embedding)s::vector LIMIT %(n)s
"""
# OR de términos: plainto_tsquery exigiría TODAS las palabras de la pregunta
FULLTEXT_SQL = """
SELECT id FROM kb_documents, to_tsquery('spanish', %(query)s) AS q
WHERE tsv @@ q ORDER BY ts_rank(tsv, q) DESC LIMIT %(n)s
"""
ENTITY_SQL = """
SELECT id FROM kb_documents WHERE %(tag)s = ANY(product_tags)
ORDER BY (type = 'producto') DESC, embedding <=> %(embedding)s::vector
"""
EXPANSION_SQL = """
SELECT DISTINCT d.id, d.embedding <=> %(embedding)s::vector AS distance
FROM kb_product_policy pp JOIN kb_documents d ON d.id = pp.policy_id
WHERE pp.product_id = ANY(%(products)s) ORDER BY distance LIMIT %(n)s
"""
HYDRATE_SQL = """
SELECT d.id, d.type, d.title, d.text, d.product_tags,
       -- Misma escala que Neo4j: (1 + coseno) / 2, en [0, 1]. pgvector entrega la distancia
       -- (1 - coseno); sin normalizar, el umbral de abstención calibrado en Neo4j (0.65)
       -- descartaba respuestas válidas (detectado en los Evals, ver evals/README.md).
       -- Ojo: en este SQL no puede haber signos de porcentaje sueltos (psycopg los toma como parámetro)
       (1 + (1 - (d.embedding <=> %(embedding)s::vector))) / 2 AS similarity,
       CASE WHEN d.type = 'politica'
            THEN ARRAY(SELECT p.title FROM kb_product_policy pp JOIN kb_documents p ON p.id = pp.product_id
                       WHERE pp.policy_id = d.id ORDER BY p.title)
            ELSE ARRAY(SELECT p.title FROM kb_product_policy pp JOIN kb_documents p ON p.id = pp.policy_id
                       WHERE pp.product_id = d.id ORDER BY p.title)
       END AS related
FROM kb_documents d WHERE d.id = ANY(%(ids)s)
"""


def to_vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vector) + "]"


def tsquery_or(text: str) -> str:
    """'límite de retiro cajero' → 'límite | retiro | cajero' (palabras alfanuméricas, sin operadores)."""
    return " | ".join(lucene_query(text).split())


class PgVectorHybridRetriever:
    prefers_full_context = True

    def __init__(self, dsn: str, embedder: Embedder, min_similarity: float, rrf_k: int = 60):
        self.dsn = dsn
        self.embedder = embedder
        self.min_similarity = min_similarity
        self.rrf_k = rrf_k

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn, connect_timeout=3)

    def search(self, query: str, k: int = 4, product_hint: str | None = None) -> list[dict]:
        embedding = to_vector_literal(self.embedder.embed([query])[0])
        with self._connect() as conn, conn.cursor() as cur:

            def ids(sql: str, **params) -> list[str]:
                cur.execute(sql, {"embedding": embedding, **params})
                return [row[0] for row in cur.fetchall()]

            rankings = {
                "vector_politica": ids(VECTOR_SQL, type="politica", n=8),
                "vector_producto": ids(VECTOR_SQL, type="producto", n=4),
            }
            text_query = tsquery_or(query)
            if text_query:
                rankings["fulltext"] = ids(FULLTEXT_SQL, query=text_query, n=8)
            if product_hint:
                rankings["entidad"] = ids(ENTITY_SQL, tag=product_hint)

            seeds = sorted(rrf(rankings, self.rrf_k).items(), key=lambda kv: kv[1], reverse=True)[:5]
            seed_products = [doc_id for doc_id, _ in seeds if doc_id.startswith("PRD-")]
            if seed_products:
                rankings["grafo"] = ids(EXPANSION_SQL, products=seed_products, n=6)

            fused = rrf(rankings, self.rrf_k)
            top_ids = [doc_id for doc_id, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[: k * 2]]
            if not top_ids:
                return []
            cur.execute(HYDRATE_SQL, {"embedding": embedding, "ids": top_ids})
            columns = [c.name for c in cur.description]
            docs = {row[0]: dict(zip(columns, row, strict=True)) for row in cur.fetchall()}

        best_similarity = max((d["similarity"] for d in docs.values()), default=0.0)
        if best_similarity < self.min_similarity:
            return []  # abstención: la pregunta no está cubierta por la base de conocimiento

        results = []
        for doc_id in top_ids:
            d = docs.get(doc_id)
            if d is None or d["similarity"] < self.min_similarity - 0.1:
                continue
            results.append(
                {
                    "id": doc_id,
                    "type": d["type"],
                    "title": d["title"],
                    "text": d["text"],
                    "score": round(float(d["similarity"]), 3),
                    "rrf": round(fused[doc_id], 5),
                    "channels": [name for name, ranked in rankings.items() if doc_id in ranked],
                    "related": list(d["related"] or []),
                    "product_tags": list(d["product_tags"] or []),
                }
            )
        return results[:k]


@lru_cache
def pgvector_available() -> bool:
    """True si PostgreSQL responde y la base de conocimiento está cargada."""
    from app.config import get_settings

    try:
        import psycopg

        with psycopg.connect(get_settings().pgvector_dsn, connect_timeout=3) as conn:
            row = conn.execute("SELECT count(*) FROM kb_documents").fetchone()
            return bool(row and row[0] > 0)
    except Exception as error:  # noqa: BLE001
        logger.warning("pgvector no disponible: %s", error)
        return False
