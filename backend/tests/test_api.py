"""Tests de integración de la API (FastAPI TestClient ejecuta el lifespan)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["components"]["graph"] == "ok"


def test_chat_echo(client: TestClient) -> None:
    response = client.post("/chat", json={"customer_id": "CLI-001", "message": "¿Cuál es mi saldo?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["intent"] == "consulta_cuenta"
    assert "¿Cuál es mi saldo?" in body["answer"]
    assert [n["node"] for n in body["metrics"]["nodes"]] == ["router", "echo_agent"]


def test_chat_keeps_thread_and_trims_trace(client: TestClient) -> None:
    first = client.post("/chat", json={"customer_id": "CLI-001", "message": "hola"}).json()
    second = client.post(
        "/chat",
        json={"thread_id": first["thread_id"], "customer_id": "CLI-001", "message": "requisitos DPF"},
    ).json()
    assert second["thread_id"] == first["thread_id"]
    # Solo se reportan las métricas del turno actual, no las acumuladas del thread
    assert len(second["metrics"]["nodes"]) == 2
    assert second["intent"] == "conocimiento_producto"


def test_chat_validation_error_format(client: TestClient) -> None:
    response = client.post("/chat", json={"customer_id": "bad-id", "message": "hola"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
