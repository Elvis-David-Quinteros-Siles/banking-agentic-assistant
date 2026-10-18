"""Tests del backend "lora" del router y del formato compartido (sin torch: clasificador simulado)."""

import pytest
from langchain_core.messages import HumanMessage

from app.graph.nodes import router
from app.llm.intent_format import SYSTEM_PROMPT, build_messages, parse_output, serialize_label
from app.llm.lora_router import LoraPrediction


def test_serialize_label_is_canonical():
    label = {"intent": "queja_disputa", "monto": 350.0, "moneda": "BOB", "producto": "tarjeta_debito"}
    assert (
        serialize_label(label) == '{"intent":"queja_disputa","producto":"tarjeta_debito","monto":350.0,"moneda":"BOB"}'
    )
    assert serialize_label({"intent": "ambiguo"}).endswith('"monto":null,"moneda":null}')


def test_parse_output_roundtrip_and_validation():
    label = {"intent": "consulta_cuenta", "producto": "caja_ahorro", "monto": None, "moneda": None}
    assert parse_output(serialize_label(label)) == label
    # Producto desconocido se descarta; texto alrededor del JSON se ignora
    parsed = parse_output(
        'Respuesta: {"intent":"conocimiento_producto","producto":"cohete","monto":"12","moneda":"EUR"}'
    )
    assert parsed == {"intent": "conocimiento_producto", "producto": None, "monto": 12.0, "moneda": None}
    with pytest.raises(ValueError):
        parse_output('{"intent":"saludo"}')
    with pytest.raises(ValueError):
        parse_output("no sé")


def test_prompt_is_short():
    # Se ejecuta en cada turno: el presupuesto de contexto del router es deliberadamente chico
    assert len(SYSTEM_PROMPT) < 500  # ~410 caracteres ≈ 120 tokens
    assert build_messages("hola")[1] == {"role": "user", "content": "hola"}


class FakeClassifier:
    def __init__(self, output=None, error=None):
        self.output, self.error = output, error

    def classify(self, text):
        if self.error:
            raise self.error
        return LoraPrediction(output=self.output, confidence=0.97, latency_ms=12.0, raw="", tokens_in=95, tokens_out=24)


def _state(text):
    return {"messages": [HumanMessage(content=text)]}


def test_router_uses_lora_and_keeps_regex_amount(monkeypatch):
    monkeypatch.setattr(router.get_settings(), "router_backend", "lora")
    fake = FakeClassifier({"intent": "queja_disputa", "producto": "tarjeta_debito", "monto": 35.0, "moneda": "BOB"})
    monkeypatch.setattr("app.llm.lora_router.get_lora_classifier", lambda: fake)

    update = router.router_node(_state("me cobraron Bs 350 dos veces en la tarjeta de débito"))
    assert update["intent"] == "queja_disputa"
    assert update["intent_confidence"] == 0.97
    assert update["entities"]["monto"] == 350.0  # la regex corrige el monto mal extraído por el modelo
    assert update["trace"][0]["tokens_in"] == 95
    assert update["trace"][0]["cost_usd"] == 0.0


def test_router_lora_failure_degrades_to_rules(monkeypatch):
    monkeypatch.setattr(router.get_settings(), "router_backend", "lora")
    monkeypatch.setattr(
        "app.llm.lora_router.get_lora_classifier", lambda: FakeClassifier(error=ValueError("JSON roto"))
    )

    update = router.router_node(_state("¿cuál es mi saldo?"))
    assert update["degraded"] is True
    assert update["intent"] == "consulta_cuenta"
