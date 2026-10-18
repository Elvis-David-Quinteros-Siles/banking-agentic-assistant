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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Muéstrame mis últimos movimientos ¿y los de la cuenta en dólares?", None),  # sin cantidad: default
        ("dame los últimos 3 movimientos", 3),
        ("quiero ver diez movimientos de mi caja de ahorro", 10),
        ("mis ultimas dos transacciones", 2),
        ("¿cuál es mi saldo? tengo 2 cuentas", None),
    ],
)
def test_requested_limit_comes_from_the_customer(text, expected):
    from app.graph.nodes.query_agent import requested_limit

    assert requested_limit(text) == expected


def test_invented_amounts_in_query_answers_are_detected():
    from app.graph.nodes.query_agent import ungrounded_amounts

    accounts = [{"saldo_texto": "Bs 12.450,30"}, {"saldo_texto": "USD 860,00"}]
    # Caso real (Evals con LLM real, M01): movimiento inventado imitando el formato del extracto
    invented = "Su caja de ahorro ****2102 tiene un saldo de USD 860,00.\n- Retiro ATM: -USD 100,00"
    assert ungrounded_amounts(invented, accounts) == ["USD 100,00"]
    assert ungrounded_amounts("Sus saldos: Bs 12.450,30 y USD 860,00", accounts) == []
