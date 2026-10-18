"""Carga del grafo de conocimiento en Neo4j (idempotente: usa MERGE).

Modelo (ver docs/ARCHITECTURE.md §3.1):
  (:Cliente)-[:PRESENTO]->(:Queja)-[:APLICA_A]->(:Producto)-[:TIENE_POLITICA]->(:Politica)
  (:Cliente)-[:CONTRATO]->(:Producto)          (:Queja)-[:REGULADA_POR]->(:Politica)

Índices: vectorial (coseno, 384 dims) en Producto y Politica, full-text con analizador
español sobre ambos, y constraints de unicidad por id.
"""

import json
import logging
from pathlib import Path

from neo4j import Driver

from app.rag.embeddings import Embedder
from app.tools.knowledge_base import PRODUCT_TAGS

logger = logging.getLogger("graph_seed")

COMPLAINTS_POLICY_ID = "POL-011"  # política de plazos de reclamos

SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT cliente_id IF NOT EXISTS FOR (c:Cliente) REQUIRE c.cliente_id IS UNIQUE",
    "CREATE CONSTRAINT producto_id IF NOT EXISTS FOR (p:Producto) REQUIRE p.producto_id IS UNIQUE",
    "CREATE CONSTRAINT politica_id IF NOT EXISTS FOR (p:Politica) REQUIRE p.politica_id IS UNIQUE",
    "CREATE CONSTRAINT queja_id IF NOT EXISTS FOR (q:Queja) REQUIRE q.queja_id IS UNIQUE",
    """CREATE VECTOR INDEX producto_embedding IF NOT EXISTS FOR (p:Producto) ON p.embedding
       OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: 'cosine'}}""",
    """CREATE VECTOR INDEX politica_embedding IF NOT EXISTS FOR (p:Politica) ON p.embedding
       OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: 'cosine'}}""",
    """CREATE FULLTEXT INDEX conocimiento_texto IF NOT EXISTS FOR (n:Producto|Politica)
       ON EACH [n.nombre, n.descripcion, n.titulo, n.texto]
       OPTIONS {indexConfig: {`fulltext.analyzer`: 'spanish'}}""",
]


def seed_graph(driver: Driver, embedder: Embedder, seed_dir: Path, reset: bool = False) -> dict:
    knowledge = json.loads((seed_dir / "knowledge.json").read_text(encoding="utf-8"))
    customers = json.loads((seed_dir / "customers.json").read_text(encoding="utf-8"))
    graph = json.loads((seed_dir / "graph.json").read_text(encoding="utf-8"))

    if reset:
        # Al cambiar de modelo de embeddings cambia la dimensión: hay que borrar índices también
        driver.execute_query("MATCH (n) DETACH DELETE n")
        for index in ("producto_embedding", "politica_embedding", "conocimiento_texto"):
            driver.execute_query(f"DROP INDEX {index} IF EXISTS")

    for statement in SCHEMA_STATEMENTS:
        driver.execute_query(statement, dim=embedder.dim)

    # Se embebe "título + texto": el título aporta la señal de tema, el texto el detalle
    products = knowledge["products"]
    product_vectors = embedder.embed([f"{p['nombre']}. {p['descripcion']}" for p in products])
    driver.execute_query(
        """UNWIND $rows AS row
           MERGE (p:Producto {producto_id: row.producto_id})
           SET p.nombre = row.nombre, p.tipo = row.tipo, p.moneda = row.moneda,
               p.tasa_referencial = row.tasa_referencial, p.descripcion = row.descripcion,
               p.tags = row.tags, p.embedding = row.embedding""",
        rows=[
            {**p, "tags": PRODUCT_TAGS.get(p["producto_id"], []), "embedding": v}
            for p, v in zip(products, product_vectors, strict=True)
        ],
    )

    policies = knowledge["policies"]
    policy_vectors = embedder.embed([f"{p['titulo']}. {p['texto']}" for p in policies])
    driver.execute_query(
        """UNWIND $rows AS row
           MERGE (pol:Politica {politica_id: row.politica_id})
           SET pol.titulo = row.titulo, pol.texto = row.texto, pol.categoria = row.categoria,
               pol.vigente_desde = date(row.vigente_desde), pol.embedding = row.embedding
           WITH pol, row
           UNWIND row.productos AS producto_id
           MATCH (p:Producto {producto_id: producto_id})
           MERGE (p)-[:TIENE_POLITICA]->(pol)""",
        rows=[{**p, "embedding": v} for p, v in zip(policies, policy_vectors, strict=True)],
    )

    driver.execute_query(
        """UNWIND $rows AS row
           MERGE (c:Cliente {cliente_id: row.customer_id})
           SET c.nombre = row.full_name, c.segmento = row.segment""",
        rows=customers["customers"],
    )
    driver.execute_query(
        """UNWIND $rows AS row
           MATCH (c:Cliente {cliente_id: row.customer_id})
           MATCH (p:Producto {producto_id: row.producto_id})
           MERGE (c)-[r:CONTRATO]->(p) SET r.desde = date(row.desde)""",
        rows=graph["contracts"],
    )
    for complaint in graph["complaints"]:
        upsert_complaint(driver, complaint)

    counts, _, _ = driver.execute_query("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label")
    summary = {r["label"]: r["n"] for r in counts}
    logger.info("Grafo cargado: %s", summary)
    return summary


def upsert_complaint(driver: Driver, complaint: dict) -> None:
    """Crea/actualiza una Queja y sus relaciones. Lo usa el seed y el nodo register_complaint."""
    driver.execute_query(
        """MATCH (c:Cliente {cliente_id: $customer_id})
           MERGE (q:Queja {queja_id: $queja_id})
           SET q.categoria = $categoria, q.descripcion = $descripcion, q.monto = $monto,
               q.moneda = $moneda, q.prioridad = $prioridad, q.estado = $estado,
               q.fecha = date($fecha), q.aprobada_por = $aprobada_por
           MERGE (c)-[r:PRESENTO]->(q) SET r.fecha = date($fecha)
           WITH q
           OPTIONAL MATCH (p:Producto {producto_id: $producto_id})
           FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (q)-[:APLICA_A]->(p))
           WITH q
           MATCH (pol:Politica {politica_id: $politica_id})
           MERGE (q)-[:REGULADA_POR]->(pol)""",
        customer_id=complaint["customer_id"],
        queja_id=complaint["queja_id"],
        categoria=complaint["categoria"],
        descripcion=complaint["descripcion"],
        monto=complaint.get("monto"),
        moneda=complaint.get("moneda"),
        prioridad=complaint["prioridad"],
        estado=complaint["estado"],
        fecha=complaint["fecha"],
        aprobada_por=complaint.get("aprobada_por"),
        producto_id=complaint.get("producto_id"),
        politica_id=COMPLAINTS_POLICY_ID,
    )
