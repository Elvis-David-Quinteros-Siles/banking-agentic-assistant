"""Carga la base de conocimiento en PostgreSQL + pgvector (idempotente).

Esquema:
  kb_documents       productos y políticas con embedding (vector 384, índice HNSW coseno) y
                     tsvector en español (columna generada, índice GIN)
  kb_product_policy  relación producto → política (la expansión de un salto del retriever)
"""

import json
import logging
from pathlib import Path

from app.rag.embeddings import Embedder
from app.rag.pgvector_retriever import to_vector_literal
from app.tools.knowledge_base import PRODUCT_TAGS

logger = logging.getLogger("pgvector_seed")


def schema_sql(dim: int) -> str:
    return f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS kb_documents (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL CHECK (type IN ('producto', 'politica')),
    title        TEXT NOT NULL,
    text         TEXT NOT NULL,
    product_tags TEXT[] NOT NULL DEFAULT '{{}}',
    embedding    vector({dim}) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('spanish', title || ' ' || text)) STORED
);
CREATE TABLE IF NOT EXISTS kb_product_policy (
    product_id TEXT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    policy_id  TEXT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    PRIMARY KEY (product_id, policy_id)
);
CREATE INDEX IF NOT EXISTS kb_documents_embedding_hnsw ON kb_documents USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_documents_tsv_gin ON kb_documents USING gin (tsv);
"""


def seed_pgvector(dsn: str, embedder: Embedder, seed_dir: Path, reset: bool = False) -> dict:
    import psycopg

    knowledge = json.loads((seed_dir / "knowledge.json").read_text(encoding="utf-8"))
    products, policies = knowledge["products"], knowledge["policies"]
    product_vectors = embedder.embed([f"{p['nombre']}. {p['descripcion']}" for p in products])
    policy_vectors = embedder.embed([f"{p['titulo']}. {p['texto']}" for p in policies])

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if reset:
            cur.execute("DROP TABLE IF EXISTS kb_product_policy, kb_documents")
        cur.execute(schema_sql(embedder.dim))
        upsert = """
            INSERT INTO kb_documents (id, type, title, text, product_tags, embedding)
            VALUES (%s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT (id) DO UPDATE SET type = EXCLUDED.type, title = EXCLUDED.title, text = EXCLUDED.text,
                product_tags = EXCLUDED.product_tags, embedding = EXCLUDED.embedding
        """
        for p, v in zip(products, product_vectors):
            cur.execute(upsert, (p["producto_id"], "producto", p["nombre"], p["descripcion"],
                                 PRODUCT_TAGS.get(p["producto_id"], []), to_vector_literal(v)))
        for pol, v in zip(policies, policy_vectors):
            tags = sorted({t for pid in pol["productos"] for t in PRODUCT_TAGS.get(pid, [])})
            cur.execute(upsert, (pol["politica_id"], "politica", pol["titulo"], pol["texto"], tags, to_vector_literal(v)))
            for product_id in pol["productos"]:
                cur.execute("INSERT INTO kb_product_policy VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (product_id, pol["politica_id"]))
        cur.execute("SELECT type, count(*) FROM kb_documents GROUP BY type ORDER BY type")
        summary = dict(cur.fetchall())
        cur.execute("SELECT count(*) FROM kb_product_policy")
        summary["relaciones"] = cur.fetchone()[0]
    logger.info("pgvector cargado: %s", summary)
    return summary
