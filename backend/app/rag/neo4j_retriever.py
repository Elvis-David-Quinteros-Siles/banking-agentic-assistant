"""Graph RAG: recuperación híbrida sobre Neo4j.

Pipeline (ver docs/ARCHITECTURE.md §3.2):

  pregunta ─┬─ embedding ─► vector index Politica (top 8) ─┐
            │             └► vector index Producto (top 4) ─┤
            ├─ full-text Lucene (analizador español) ────────┼─► RRF ─► semillas
            └─ entidad del router ("tarjeta_debito") ────────┘           │
                                                                         ▼
                          expansión de grafo: (Producto)-[:TIENE_POLITICA]->(Politica)
                                                                         │
                                         RRF final ─► top-k + productos relacionados

Por qué híbrido:
- El vector entiende sinónimos ("cajero" ≈ "ATM", "sacar plata" ≈ "retiro").
- El full-text acierta términos exactos que el embedding diluye ("DPF", "QR", montos).
- El grafo aporta lo que ningún índice ve: si la pregunta habla del PRODUCTO, trae
  las POLÍTICAS que lo gobiernan aunque no compartan palabras con la pregunta.
"""

from neo4j import Driver

from app.rag.embeddings import Embedder
from app.rag.fusion import lucene_query, rrf  # noqa: F401 - re-exportadas para los tests

VECTOR_QUERY = """
CALL db.index.vector.queryNodes($index, $n, $embedding) YIELD node, score
RETURN coalesce(node.politica_id, node.producto_id) AS id, score
"""

FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes('conocimiento_texto', $text, {limit: $n}) YIELD node, score
RETURN coalesce(node.politica_id, node.producto_id) AS id, score
"""

ENTITY_QUERY = """
MATCH (p:Producto) WHERE $tag IN p.tags
OPTIONAL MATCH (p)-[:TIENE_POLITICA]->(pol:Politica)
WITH p, pol ORDER BY vector.similarity.cosine(pol.embedding, $embedding) DESC
RETURN p.producto_id AS product_id, collect(pol.politica_id) AS policy_ids
"""

# Expansión de 1 salto: políticas de los productos semilla, ordenadas por similitud con la pregunta
EXPANSION_QUERY = """
MATCH (p:Producto)-[:TIENE_POLITICA]->(pol:Politica)
WHERE p.producto_id IN $product_ids
WITH DISTINCT pol
RETURN pol.politica_id AS id, vector.similarity.cosine(pol.embedding, $embedding) AS score
ORDER BY score DESC LIMIT $n
"""

# Hidratación: propiedades + similitud + vecinos para enriquecer el contexto del LLM
HYDRATE_QUERY = """
MATCH (n) WHERE (n:Politica OR n:Producto) AND coalesce(n.politica_id, n.producto_id) IN $ids
OPTIONAL MATCH (n)<-[:TIENE_POLITICA]-(owner:Producto)
OPTIONAL MATCH (n)-[:TIENE_POLITICA]->(child:Politica)
RETURN coalesce(n.politica_id, n.producto_id) AS id,
       CASE WHEN n:Politica THEN 'politica' ELSE 'producto' END AS type,
       coalesce(n.titulo, n.nombre) AS title,
       coalesce(n.texto, n.descripcion) AS text,
       coalesce(n.tags, []) AS own_tags,
       vector.similarity.cosine(n.embedding, $embedding) AS similarity,
       collect(DISTINCT owner.nombre) AS applies_to,
       collect(DISTINCT owner.tags) AS owner_tags,
       collect(DISTINCT child.titulo) AS policies
"""


class Neo4jHybridRetriever:
    def __init__(self, driver: Driver, embedder: Embedder, min_similarity: float, rrf_k: int = 60):
        self.driver = driver
        self.embedder = embedder
        self.min_similarity = min_similarity
        self.rrf_k = rrf_k

    def _ids(self, query: str, **params) -> list[str]:
        records, _, _ = self.driver.execute_query(query, **params)
        return [r["id"] for r in records if r["id"]]

    def search(self, query: str, k: int = 4, product_hint: str | None = None) -> list[dict]:
        embedding = self.embedder.embed([query])[0]
        rankings: dict[str, list[str]] = {
            "vector_politica": self._ids(VECTOR_QUERY, index="politica_embedding", n=8, embedding=embedding),
            "vector_producto": self._ids(VECTOR_QUERY, index="producto_embedding", n=4, embedding=embedding),
        }
        text = lucene_query(query)
        if text:
            rankings["fulltext"] = self._ids(FULLTEXT_QUERY, text=text, n=8)
        if product_hint:
            records, _, _ = self.driver.execute_query(ENTITY_QUERY, tag=product_hint, embedding=embedding)
            rankings["entidad"] = [r["product_id"] for r in records] + [
                pid for r in records for pid in r["policy_ids"]
            ]

        # Expansión: productos entre las 5 primeras semillas → sus políticas
        seeds = sorted(rrf(rankings, self.rrf_k).items(), key=lambda kv: kv[1], reverse=True)[:5]
        seed_products = [doc_id for doc_id, _ in seeds if doc_id.startswith("PRD-")]
        if seed_products:
            rankings["grafo"] = self._ids(EXPANSION_QUERY, product_ids=seed_products, embedding=embedding, n=6)

        fused = rrf(rankings, self.rrf_k)
        top_ids = [doc_id for doc_id, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[: k * 2]]
        if not top_ids:
            return []

        records, _, _ = self.driver.execute_query(HYDRATE_QUERY, ids=top_ids, embedding=embedding)
        docs = {r["id"]: r for r in records}

        # Abstención: si ni el mejor documento se parece a la pregunta, no está cubierta por la base.
        # Calibración (scripts de la Fase 3): preguntas válidas ≥ 0.70, fuera de alcance 0.52–0.58.
        best_similarity = max((r["similarity"] for r in records), default=0.0)
        if best_similarity < self.min_similarity:
            return []

        results = []
        for doc_id in top_ids:
            r = docs.get(doc_id)
            # Se descartan documentos poco similares que entraron solo por la expansión del grafo
            if r is None or r["similarity"] < self.min_similarity - 0.1:
                continue
            owner_tags = sorted({tag for tags in r["owner_tags"] for tag in tags})
            results.append({
                "id": doc_id,
                "type": r["type"],
                "title": r["title"],
                "text": r["text"],
                "score": round(r["similarity"], 3),
                "rrf": round(fused[doc_id], 5),
                "channels": [name for name, ids in rankings.items() if doc_id in ids],
                "related": r["applies_to"] if r["type"] == "politica" else r["policies"],
                "product_tags": r["own_tags"] or owner_tags,
            })
        return results[:k]
