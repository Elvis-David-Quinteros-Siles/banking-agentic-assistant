"""Tests unitarios del router por reglas."""

import pytest

from app.graph.nodes.router import classify


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿Cuál es mi saldo?", "consulta_cuenta"),
        ("Muéstrame mis últimos movimientos", "consulta_cuenta"),
        ("¿Qué comisión cobra la tarjeta de crédito?", "conocimiento_producto"),
        ("Requisitos para un DPF", "conocimiento_producto"),
        ("Quiero presentar un reclamo por doble cobro", "queja_disputa"),
        ("¿Quién ganó el partido ayer?", "fuera_de_alcance"),
    ],
)
def test_classify(text: str, expected: str) -> None:
    intent, confidence = classify(text)
    assert intent == expected
    assert 0.0 <= confidence <= 1.0
