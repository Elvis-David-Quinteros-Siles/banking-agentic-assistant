"""Tests de las tools SQL y del retriever de conocimiento."""

from app.db.sqlite import SQLiteRepository
from app.tools.knowledge_base import get_keyword_retriever
from app.tools.sql_tools import BankingTools


def test_accounts_are_masked_and_scoped(seeded_db):
    accounts = BankingTools(SQLiteRepository(seeded_db), "CLI-001").get_accounts()
    assert {a["cuenta"] for a in accounts} == {"****2101", "****2102"}
    assert all(not a["cuenta"][4:].startswith("1000") for a in accounts)


def test_cannot_read_other_customer_transactions(seeded_db):
    # La cuenta 2000781201 pertenece a CLI-002: CLI-001 no debe poder leerla
    assert BankingTools(SQLiteRepository(seeded_db), "CLI-001").get_transactions("2000781201") == []
    assert BankingTools(SQLiteRepository(seeded_db), "CLI-002").get_transactions("****1201") != []


def test_transactions_limit(seeded_db):
    assert len(BankingTools(SQLiteRepository(seeded_db), "CLI-001").get_transactions("****2101", limit=3)) == 3


def test_retriever_finds_relevant_policy(settings):
    retriever = get_keyword_retriever()
    results = retriever.search("límite de retiro en cajero con tarjeta de débito")
    # Top-2 porque la respuesta se arma con los 2 primeros; la ficha corta del producto
    # compite con la política (sesgo de BM25 por longitud). La Fase 3 lo mejora con vectores.
    assert "POL-003" in [r["id"] for r in results[:2]]


def test_retriever_returns_low_score_for_unrelated(settings):
    retriever = get_keyword_retriever()
    results = retriever.search("receta de majadito")
    assert results == []  # el retriever filtra internamente por KNOWLEDGE_MIN_SCORE
