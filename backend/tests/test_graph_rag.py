"""Tests del Graph RAG.

- Unitarios (siempre): RRF, query Lucene segura, embedder hash, fallback del knowledge_agent.
- Integración (requieren `docker compose up -d neo4j` + `python scripts/seed_neo4j.py`):
  se marcan con @pytest.mark.neo4j y se SALTAN si Neo4j no responde.
"""

import pytest
from langchain_core.messages import HumanMessage

from app.graph.nodes import knowledge_agent
from app.rag.embeddings import HashEmbedder
from app.rag.neo4j_retriever import lucene_query, rrf


# ---------------------------------------------------------------------------
# Unitarios
# ---------------------------------------------------------------------------
def test_rrf_rewards_agreement_between_lists():
    scores = rrf({"vector": ["A", "B", "C"], "fulltext": ["B", "D"]}, k=60)
    # B aparece en ambas listas: supera a A aunque A sea primero en una
    assert scores["B"] > scores["A"] > scores["C"]
    assert set(scores) == {"A", "B", "C", "D"}


def test_lucene_query_strips_operators():
    assert lucene_query('¿límite "AND" OR (cajero)* -x?') == "límite and cajero"


def test_hash_embedder_is_normalized_and_deterministic():
    embedder = HashEmbedder()
    a, b = embedder.embed(["tarjeta de débito", "tarjeta de débito"])
    assert a == b
    assert len(a) == 384
    assert abs(sum(v * v for v in a) - 1.0) < 1e-6


def test_knowledge_agent_falls_back_when_retriever_fails(monkeypatch):
    class BrokenRetriever:
        def search(self, *args, **kwargs):
            raise ConnectionError("neo4j caído")

    monkeypatch.setattr(knowledge_agent, "get_retriever", lambda: BrokenRetriever())
    state = {"messages": [HumanMessage(content="requisitos para abrir una caja de ahorro")], "entities": {}}
    update = knowledge_agent.knowledge_agent_node(state)
    assert update["degraded"] is True
    assert "POL-001" in [d["id"] for d in update["retrieved_context"]]


# ---------------------------------------------------------------------------
# Integración con Neo4j
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def neo4j_retriever(settings):
    from app.rag.embeddings import FastEmbedEmbedder
    from app.rag.neo4j_client import get_driver, neo4j_available
    from app.rag.neo4j_retriever import Neo4jHybridRetriever

    if not neo4j_available():
        pytest.skip("Neo4j no disponible o sin datos (docker compose up -d neo4j + seed_neo4j.py)")
    return Neo4jHybridRetriever(
        get_driver(), FastEmbedEmbedder(settings.embeddings_model), settings.neo4j_min_similarity, settings.rrf_k
    )


@pytest.mark.neo4j
@pytest.mark.parametrize(
    ("question", "hint", "expected"),
    [
        ("límite de retiro en cajero con tarjeta de débito", "tarjeta_debito", "POL-003"),
        # Sin palabras en común con la política: lo resuelve el vector ("sacar del ATM" ≈ "retiro en cajeros")
        ("¿cuánto puedo sacar del ATM por día?", None, "POL-003"),
        ("¿cuánto cobran por usar la tarjeta en el extranjero?", "tarjeta", "POL-005"),
        ("perdí mi tarjeta, ¿qué hago?", "tarjeta", "POL-004"),
        ("quiero comprar una casa, ¿me pueden prestar?", None, "POL-009"),
    ],
)
def test_hybrid_retrieval_top1(neo4j_retriever, question, hint, expected):
    results = neo4j_retriever.search(question, k=4, product_hint=hint)
    assert results[0]["id"] == expected


@pytest.mark.neo4j
def test_graph_expansion_and_related_products(neo4j_retriever):
    results = neo4j_retriever.search("límite de retiro en cajero con tarjeta de débito", product_hint="tarjeta_debito")
    top = results[0]
    assert {"vector_politica", "fulltext", "grafo"} <= set(top["channels"])
    assert "Tarjeta de Débito" in top["related"]  # vecino del grafo: a qué producto aplica


@pytest.mark.neo4j
@pytest.mark.parametrize("question", ["receta de majadito", "¿quién ganó el mundial?"])
def test_abstains_on_uncovered_questions(neo4j_retriever, question):
    assert neo4j_retriever.search(question) == []


@pytest.mark.neo4j
def test_complaint_is_mirrored_in_graph(neo4j_retriever):
    from app.rag.graph_seed import upsert_complaint
    from app.rag.neo4j_client import get_driver

    driver = get_driver()
    upsert_complaint(
        driver,
        {
            "queja_id": "QJ-TEST01",
            "customer_id": "CLI-001",
            "producto_id": "PRD-TDB",
            "categoria": "cobro_indebido",
            "descripcion": "test",
            "monto": 350.0,
            "moneda": "BOB",
            "prioridad": "media",
            "estado": "registrada",
            "fecha": "2026-10-17",
            "aprobada_por": "OP-TEST",
        },
    )
    try:
        records, _, _ = driver.execute_query(
            """MATCH (c:Cliente)-[:PRESENTO]->(q:Queja {queja_id: 'QJ-TEST01'})-[:APLICA_A]->(p:Producto),
                     (q)-[:REGULADA_POR]->(pol:Politica)
               RETURN c.cliente_id AS c, p.producto_id AS p, pol.politica_id AS pol"""
        )
        assert records[0].data() == {"c": "CLI-001", "p": "PRD-TDB", "pol": "POL-011"}
    finally:
        driver.execute_query("MATCH (q:Queja {queja_id: 'QJ-TEST01'}) DETACH DELETE q")


def test_pii_placeholders_are_removed_from_retrieval_query():
    # Regresión detectada en Evals (P01): el marcador de PII desviaba la búsqueda
    query = knowledge_agent.retrieval_query("Mi tarjeta es [TARJETA ****1111], ¿cuál es el límite de retiro en cajero?")
    assert "TARJETA" not in query
    assert "límite de retiro en cajero" in query


def test_rrf_counts_each_document_once_per_list():
    # Regresión detectada en Evals (P01): duplicados en una lista inflaban el score
    scores = rrf({"entidad": ["POL-004", "POL-003", "POL-004"], "vector": ["POL-003", "POL-004"]}, k=60)
    assert scores["POL-003"] == scores["POL-004"]
