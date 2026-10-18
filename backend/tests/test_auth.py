"""Autenticación JWT: la identidad sale del token y cada vía de suplantación devuelve 401/403."""

import time

import jwt
import pytest

from app.api.auth import AUDIENCE, DEMO_ISSUER, _demo_secret, issue_token
from app.config import get_settings


@pytest.fixture
def jwt_mode(monkeypatch, client):
    monkeypatch.setattr(get_settings(), "auth_mode", "jwt")
    return client


def bearer(subject: str, role: str = "customer") -> dict:
    return {"Authorization": f"Bearer {issue_token(subject, role)}"}


def test_demo_token_and_chat_uses_token_identity(jwt_mode):
    token = jwt_mode.post("/auth/demo-token", json={"customer_id": "CLI-001"}).json()
    assert token["token_type"] == "bearer" and token["subject"] == "CLI-001"
    body = jwt_mode.post(
        "/chat", json={"message": "¿Cuál es mi saldo?"}, headers={"Authorization": f"Bearer {token['access_token']}"}
    ).json()
    assert "Bs 12.450,30" in body["answer"]  # saldos de CLI-001: el cliente salió del token


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),  # sin token
        ({"Authorization": "Bearer no-es-un-jwt"}, 401),
        ({"Authorization": "Basic abc"}, 401),
    ],
)
def test_missing_or_malformed_token(jwt_mode, headers, status):
    assert jwt_mode.post("/chat", json={"message": "hola"}, headers=headers).status_code == status


def test_expired_and_forged_tokens_are_rejected(jwt_mode):
    now = int(time.time())
    claims = {"sub": "CLI-001", "role": "customer", "iss": DEMO_ISSUER, "aud": AUDIENCE}
    expired = jwt.encode({**claims, "iat": now - 7200, "exp": now - 3600}, _demo_secret(), algorithm="HS256")
    forged = jwt.encode(
        {**claims, "iat": now, "exp": now + 600}, "otro-secreto-de-un-atacante-123456", algorithm="HS256"
    )
    none_alg = jwt.encode({**claims, "iat": now, "exp": now + 600}, None, algorithm="none")
    for token in (expired, forged, none_alg):
        r = jwt_mode.post("/chat", json={"message": "hola"}, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, token


def test_body_customer_id_cannot_override_token(jwt_mode):
    r = jwt_mode.post(
        "/chat", json={"customer_id": "CLI-002", "message": "¿Cuál es mi saldo?"}, headers=bearer("CLI-001")
    )
    assert r.status_code == 403


def test_thread_of_another_customer_cannot_be_continued(jwt_mode):
    first = jwt_mode.post("/chat", json={"message": "Muéstrame mis movimientos"}, headers=bearer("CLI-001")).json()
    hijack = jwt_mode.post(
        "/chat", json={"message": "¿y en dólares?", "thread_id": first["thread_id"]}, headers=bearer("CLI-002")
    )
    assert hijack.status_code == 403
    assert hijack.json()["error"]["code"] == "THREAD_FORBIDDEN"


def test_confirm_requires_owner_token(jwt_mode):
    body = jwt_mode.post("/chat", json={"message": "Bloquea mi tarjeta de débito"}, headers=bearer("CLI-004")).json()
    assert body["status"] == "interrupted"
    other = jwt_mode.post(f"/confirm/{body['thread_id']}", json={"confirm": True}, headers=bearer("CLI-002"))
    assert other.status_code == 403
    owner = jwt_mode.post(f"/confirm/{body['thread_id']}", json={"confirm": False}, headers=bearer("CLI-004"))
    assert owner.status_code == 200


def test_operator_endpoints_require_operator_role(jwt_mode):
    assert jwt_mode.get("/operator/queue", headers=bearer("CLI-001")).status_code == 403
    assert jwt_mode.get("/metrics/summary", headers=bearer("CLI-001")).status_code == 403
    assert jwt_mode.get("/operator/queue", headers=bearer("OP-DEMO", "operator")).status_code == 200
    complaint = jwt_mode.post(
        "/chat", json={"message": "Me cobraron Bs 350 dos veces en la tarjeta de débito"}, headers=bearer("CLI-001")
    ).json()
    decision = {"decision": "reject", "reviewer_id": "OP-1", "comment": "test"}
    assert (
        jwt_mode.post(f"/approve/{complaint['thread_id']}", json=decision, headers=bearer("CLI-001")).status_code == 403
    )
    ok = jwt_mode.post(f"/approve/{complaint['thread_id']}", json=decision, headers=bearer("OP-DEMO", "operator"))
    assert ok.status_code == 200
    # Un operador no puede chatear como cliente
    assert jwt_mode.post("/chat", json={"message": "hola"}, headers=bearer("OP-DEMO", "operator")).status_code == 403


def test_demo_issuer_disabled_with_oidc(jwt_mode, monkeypatch):
    monkeypatch.setattr(get_settings(), "oidc_jwks_url", "https://idp.banco.example/jwks")
    assert jwt_mode.post("/auth/demo-token", json={"customer_id": "CLI-001"}).status_code == 404


def test_oidc_rs256_tokens_are_verified_against_jwks(jwt_mode, monkeypatch):
    from types import SimpleNamespace

    from cryptography.hazmat.primitives.asymmetric import rsa

    import app.api.auth as auth

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = get_settings()
    monkeypatch.setattr(settings, "oidc_jwks_url", "https://idp.banco.example/jwks")
    monkeypatch.setattr(settings, "oidc_issuer", "https://idp.banco.example")
    monkeypatch.setattr(settings, "oidc_audience", "asistente")
    fake_jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=key.public_key()))
    monkeypatch.setattr(auth, "_jwks_client", lambda _url: fake_jwks)

    now = int(time.time())
    claims = {"sub": "CLI-003", "role": "customer", "iss": "https://idp.banco.example", "aud": "asistente",
              "iat": now, "exp": now + 600}  # fmt: skip
    good = jwt.encode(claims, key, algorithm="RS256")
    r = jwt_mode.post("/chat", json={"message": "¿Cuál es mi saldo?"}, headers={"Authorization": f"Bearer {good}"})
    assert r.status_code == 200 and "USD 25.300,40" in r.json()["answer"]
    # Un token de la demo (HS256) ya no sirve cuando hay IdP configurado
    assert jwt_mode.post("/chat", json={"message": "hola"}, headers=bearer("CLI-003")).status_code == 401
    wrong_aud = jwt.encode({**claims, "aud": "otra-app"}, key, algorithm="RS256")
    assert (
        jwt_mode.post("/chat", json={"message": "hola"}, headers={"Authorization": f"Bearer {wrong_aud}"}).status_code
        == 401
    )
