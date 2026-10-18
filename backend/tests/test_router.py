"""Tests unitarios del router por reglas y de la extracción de entidades."""

import pytest

from app.graph.nodes.router import classify_rules, extract_entities


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿Cuál es mi saldo?", "consulta_cuenta"),
        ("Muéstrame mis últimos movimientos", "consulta_cuenta"),
        ("¿Qué comisión cobra la tarjeta de crédito?", "conocimiento_producto"),
        ("Requisitos para un DPF", "conocimiento_producto"),
        ("Quiero presentar un reclamo por doble cobro", "queja_disputa"),
        ("Me cobraron una comisión de la tarjeta que no reconozco", "queja_disputa"),
        ("¿Quién ganó el partido ayer?", "fuera_de_alcance"),
        ("hola", "ambiguo"),
    ],
)
def test_classify_rules(text: str, expected: str) -> None:
    intent, confidence = classify_rules(text)
    assert intent == expected
    assert 0.0 <= confidence <= 1.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("me cobraron Bs 350 dos veces en la tarjeta de débito", {"producto": "tarjeta_debito", "monto": 350.0, "moneda": "BOB"}),
        ("un cargo de 1.250,50 bolivianos", {"monto": 1250.5, "moneda": "BOB"}),
        ("compra de $us 64 que no hice", {"monto": 64.0, "moneda": "USD"}),
        ("requisitos del crédito de vivienda", {"producto": "credito_vivienda"}),
    ],
)
def test_extract_entities(text, expected):
    entities = extract_entities(text)
    for key, value in expected.items():
        assert entities.get(key) == value
