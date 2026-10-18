"""Back-office: cola de reclamos para el operador y KPIs por caso de uso.

Separado del canal del cliente. Si OPERATOR_TOKEN está configurado, estas rutas (y /approve)
exigen la cabecera X-Operator-Token. En un banco sería SSO del back-office con rol "operador".
"""

import secrets

from fastapi import APIRouter, Header, HTTPException, Query

from app.analytics.store import get_analytics
from app.config import get_settings

router = APIRouter(tags=["back-office"])


def require_operator(x_operator_token: str | None = Header(default=None)) -> None:
    """Dependencia de autorización del back-office (no-op si OPERATOR_TOKEN no está definido)."""
    expected = get_settings().operator_token
    if expected and not (x_operator_token and secrets.compare_digest(x_operator_token, expected)):
        raise HTTPException(status_code=401, detail="Se requiere el token de operador (X-Operator-Token)")


@router.get("/operator/queue")
async def operator_queue(x_operator_token: str | None = Header(default=None)) -> dict:
    require_operator(x_operator_token)
    items = get_analytics().pending_queue()
    return {"pending": len(items), "items": items}


@router.get("/metrics/summary")
async def metrics_summary(
    hours: int = Query(default=24, ge=1, le=24 * 30),
    x_operator_token: str | None = Header(default=None),
) -> dict:
    require_operator(x_operator_token)
    return get_analytics().summary(hours)
