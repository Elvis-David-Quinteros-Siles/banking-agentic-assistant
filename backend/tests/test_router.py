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
        (
            "me cobraron Bs 350 dos veces en la tarjeta de débito",
            {"producto": "tarjeta_debito", "monto": 350.0, "moneda": "BOB"},
        ),
        ("un cargo de 1.250,50 bolivianos", {"monto": 1250.5, "moneda": "BOB"}),
        ("compra de $us 64 que no hice", {"monto": 64.0, "moneda": "USD"}),
        ("requisitos del crédito de vivienda", {"producto": "credito_vivienda"}),
    ],
)
def test_extract_entities(text, expected):
    entities = extract_entities(text)
    for key, value in expected.items():
        assert entities.get(key) == value


@pytest.mark.parametrize(
    ("text", "previous", "expected"),
    [
        ("¿y los de la cuenta en dólares?", "Muéstrame mis movimientos", True),
        ("¿y cuánto cuesta reponerla?", "límite de la tarjeta de débito", True),
        ("también quiero saber el pago mínimo", "tasa de la tarjeta de crédito", True),
        # Cambio de tema claro con sus propias palabras clave: no es seguimiento
        ("¿Qué requisitos piden para la tarjeta de crédito?", "¿Cuál es mi saldo?", False),
        # Primer turno: no hay contexto que heredar
        ("¿y en dólares?", "", False),
        # Mensaje largo: se entiende solo
        ("y quiero presentar un reclamo formal porque me cobraron dos veces en el supermercado ayer", "hola", False),
    ],
)
def test_is_followup(text, previous, expected):
    from app.graph.nodes.router import is_followup

    assert is_followup(text, previous) is expected


def test_followup_inherits_intent_and_builds_effective_query():
    from langchain_core.messages import AIMessage, HumanMessage

    from app.graph.nodes.router import router_node

    state = {
        "messages": [
            HumanMessage(content="¿Qué requisitos piden para abrir una caja de ahorro?"),
            AIMessage(content="..."),
            HumanMessage(content="¿y cobran mantenimiento?"),
        ],
        "last_intent": "conocimiento_producto",
    }
    update = router_node(state)
    assert update["intent"] == "conocimiento_producto"
    assert update["effective_query"].startswith("¿Qué requisitos piden")
    assert update["entities"]["producto"] == "caja_ahorro"  # el producto viene del turno anterior
