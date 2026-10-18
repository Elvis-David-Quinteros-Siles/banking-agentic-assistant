"""Tests de integración: core bancario en SQL Server y base de conocimiento en pgvector.

Requieren los servicios levantados e inicializados (se SALTAN solos si no responden):
    docker compose up -d sqlserver pgvector
    python scripts/init_sqlserver.py ; python scripts/seed_pgvector.py
"""

import uuid

import pytest

from app.rag.pgvector_retriever import tsquery_or


# ---------------------------------------------------------------------------
# Unitarios (siempre)
# ---------------------------------------------------------------------------
def test_tsquery_or_is_safe_and_uses_or():
    assert tsquery_or("¿límite de retiro? & DROP TABLE") == "límite | retiro | drop | table"
    assert tsquery_or("?? !!") == ""


# ---------------------------------------------------------------------------
# SQL Server
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def sqlserver_repo(settings):
    from app.db.sqlserver import SqlServerRepository

    repo = SqlServerRepository(settings.sqlserver_host, settings.sqlserver_port, settings.sqlserver_database,
                               settings.sqlserver_user, settings.sqlserver_password)
    if not repo.ping():
        pytest.skip("SQL Server no disponible (docker compose up -d sqlserver + init_sqlserver.py)")
    return repo


@pytest.mark.sqlserver
def test_sqlserver_tools_return_exact_data(sqlserver_repo):
    from app.tools.sql_tools import BankingTools

    tools = BankingTools(sqlserver_repo, "CLI-001")
    assert {a["saldo_texto"] for a in tools.get_accounts()} == {"Bs 12.450,30", "USD 860,00"}
    movements = tools.get_transactions("****2101", limit=3)
    assert len(movements) == 3
    assert movements[0]["comercio"] == "Cooperativa Eléctrica"
    assert movements[0]["fecha"] == "2026-10-12 15:22"


@pytest.mark.sqlserver
def test_sqlserver_foreign_account_is_filtered_inside_the_database(sqlserver_repo):
    # ****1201 es de CLI-002: el procedimiento almacenado no la devuelve a CLI-001
    assert sqlserver_repo.recent_transactions("CLI-001", "1201", 5) == []
    assert sqlserver_repo.recent_transactions("CLI-002", "1201", 5) != []


@pytest.mark.sqlserver
def test_sqlserver_app_login_has_no_direct_table_access(settings, sqlserver_repo):
    import pymssql

    conn = pymssql.connect(server=settings.sqlserver_host, port=settings.sqlserver_port, database=settings.sqlserver_database,
                           user=settings.sqlserver_user, password=settings.sqlserver_password)
    try:
        cursor = conn.cursor()
        for sql in ("SELECT TOP 1 * FROM dbo.accounts", "DELETE FROM dbo.transactions", "UPDATE dbo.accounts SET balance = 0"):
            with pytest.raises(pymssql.Error, match="permission was denied"):
                cursor.execute(sql)
    finally:
        conn.close()


@pytest.mark.sqlserver
def test_sqlserver_registers_complaint_through_procedure(sqlserver_repo):
    sqlserver_repo.insert_complaint({
        "complaint_id": f"QJ-T{uuid.uuid4().hex[:5].upper()}", "customer_id": "CLI-001", "thread_id": "test",
        "category": "cobro_indebido", "product": "tarjeta_debito", "amount": 350.0, "currency": "BOB",
        "priority": "media", "summary": "test de integración", "status": "registrada", "reviewer_id": "TEST",
    })


# ---------------------------------------------------------------------------
# pgvector
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def pg_retriever(settings):
    from app.rag.embeddings import FastEmbedEmbedder
    from app.rag.pgvector_retriever import PgVectorHybridRetriever, pgvector_available

    if not pgvector_available():
        pytest.skip("pgvector no disponible (docker compose up -d pgvector + seed_pgvector.py)")
    return PgVectorHybridRetriever(settings.pgvector_dsn, FastEmbedEmbedder(settings.embeddings_model),
                                   settings.neo4j_min_similarity, settings.rrf_k)


@pytest.mark.pgvector
@pytest.mark.parametrize(
    ("question", "hint", "expected"),
    [
        ("límite de retiro en cajero con tarjeta de débito", "tarjeta_debito", "POL-003"),
        ("¿cuánto puedo sacar del ATM por día?", None, "POL-003"),
        ("perdí mi tarjeta, ¿qué hago?", "tarjeta", "POL-004"),
        ("¿qué tasa de interés paga el DPF?", "dpf", "POL-008"),
    ],
)
def test_pgvector_hybrid_retrieval_top1(pg_retriever, question, hint, expected):
    assert pg_retriever.search(question, k=4, product_hint=hint)[0]["id"] == expected


@pytest.mark.pgvector
def test_pgvector_similarity_uses_same_scale_as_neo4j(pg_retriever):
    # Regresión (Evals): sin normalizar a (1 + coseno) / 2, el umbral 0.65 descartaba respuestas válidas
    top = pg_retriever.search("perdí mi tarjeta, ¿qué hago?", product_hint="tarjeta")[0]
    assert 0.65 <= top["score"] <= 1.0
    assert "Tarjeta de Débito" in top["related"]  # expansión producto ↔ política vía tabla de unión


@pytest.mark.pgvector
@pytest.mark.parametrize("question", ["receta de majadito", "¿quién ganó el mundial?"])
def test_pgvector_abstains_on_uncovered_questions(pg_retriever, question):
    assert pg_retriever.search(question) == []
