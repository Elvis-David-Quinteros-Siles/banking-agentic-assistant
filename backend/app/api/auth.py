"""Autenticación del canal con JWT: la identidad sale del token, nunca del cuerpo del pedido.

Dos emisores posibles, misma verificación para el resto del sistema:
- Demo (por defecto): HS256 firmado por esta API en POST /auth/demo-token, para los clientes
  ficticios. Sin JWT_SECRET configurado, el secreto se genera al arrancar (los tokens vencen al
  reiniciar).
- Producción: el proveedor de identidad del banco (OIDC). Con OIDC_JWKS_URL se verifican tokens
  RS256 contra sus claves públicas, con issuer y audience; /auth/demo-token se desactiva.

Roles: "customer" (sub = customer_id) para el chat y la confirmación de acciones; "operator" para
/approve, la cola y los KPIs. AUTH_MODE=off conserva el contrato anterior (customer_id en el
cuerpo) solo para desarrollo y tests.
"""

import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import jwt
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from app.config import get_settings

Role = Literal["customer", "operator"]
DEMO_ISSUER = "banking-assistant-demo"
AUDIENCE = "banking-assistant"

router = APIRouter(tags=["auth"])


@dataclass(frozen=True)
class Identity:
    subject: str
    role: Role


@lru_cache
def _demo_secret() -> str:
    return get_settings().jwt_secret or secrets.token_urlsafe(32)


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True)


def issue_token(subject: str, role: Role, ttl_minutes: int | None = None) -> str:
    now = int(time.time())
    ttl = (ttl_minutes or get_settings().jwt_ttl_minutes) * 60
    claims = {"sub": subject, "role": role, "iss": DEMO_ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, _demo_secret(), algorithm="HS256")


def decode_token(token: str) -> Identity:
    settings = get_settings()
    try:
        if settings.oidc_jwks_url:
            key = _jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=settings.oidc_audience, issuer=settings.oidc_issuer
            )
        else:
            claims = jwt.decode(token, _demo_secret(), algorithms=["HS256"], audience=AUDIENCE, issuer=DEMO_ISSUER)
    except jwt.ExpiredSignatureError as error:
        raise HTTPException(status_code=401, detail="El token venció; vuelva a iniciar sesión") from error
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=401, detail="Token inválido") from error
    role = claims.get("role")
    if role not in ("customer", "operator") or not claims.get("sub"):
        raise HTTPException(status_code=401, detail="El token no tiene un rol válido")
    return Identity(subject=str(claims["sub"]), role=role)


def get_identity(authorization: str | None = Header(default=None)) -> Identity | None:
    """Dependencia FastAPI. None solo con AUTH_MODE=off."""
    if get_settings().auth_mode == "off":
        return None
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Falta el token (Authorization: Bearer ...)")
    return decode_token(token)


def resolve_customer(identity: Identity | None, body_customer_id: str | None) -> str:
    """El cliente del pedido: el del token. Un customer_id en el cuerpo distinto del token es un 403."""
    if identity is None:  # AUTH_MODE=off: contrato anterior
        if not body_customer_id:
            raise HTTPException(status_code=422, detail="customer_id es obligatorio con AUTH_MODE=off")
        return body_customer_id
    if identity.role != "customer":
        raise HTTPException(status_code=403, detail="Este endpoint es del canal del cliente")
    if body_customer_id and body_customer_id != identity.subject:
        raise HTTPException(status_code=403, detail="El customer_id no coincide con la sesión")
    return identity.subject


def require_operator(identity: Identity | None, x_operator_token: str | None = None) -> None:
    if identity is None:  # AUTH_MODE=off: token compartido opcional (contrato anterior)
        expected = get_settings().operator_token
        if expected and not (x_operator_token and secrets.compare_digest(x_operator_token, expected)):
            raise HTTPException(status_code=401, detail="Se requiere el token de operador (X-Operator-Token)")
        return
    if identity.role != "operator":
        raise HTTPException(status_code=403, detail="Se requiere el rol de operador")


class DemoTokenRequest(BaseModel):
    role: Role = "customer"
    customer_id: str | None = Field(default=None, pattern=r"^CLI-\d{3}$")

    @model_validator(mode="after")
    def customer_needs_id(self):
        if self.role == "customer" and not self.customer_id:
            raise ValueError("role 'customer' requiere customer_id")
        return self


@router.post("/auth/demo-token")
async def demo_token(body: DemoTokenRequest, request: Request) -> dict:
    """Emisor de la DEMO (clientes ficticios). En producción el token lo emite el IdP del banco."""
    settings = get_settings()
    if settings.auth_mode == "off" or settings.oidc_jwks_url or not settings.demo_auth_enabled:
        raise HTTPException(status_code=404, detail="El emisor de demo está desactivado")
    subject = body.customer_id if body.role == "customer" else "OP-DEMO"
    assert subject is not None
    return {
        "access_token": issue_token(subject, body.role),
        "token_type": "bearer",
        "expires_in": settings.jwt_ttl_minutes * 60,
        "role": body.role,
        "subject": subject,
    }
