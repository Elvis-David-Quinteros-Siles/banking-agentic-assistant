"""Back-office: cola de reclamos para el operador y KPIs por caso de uso.

Separado del canal del cliente: con AUTH_MODE=jwt exige un token con rol "operator" (en un banco,
el SSO del back-office). Con AUTH_MODE=off, el token compartido opcional OPERATOR_TOKEN.
"""

from fastapi import APIRouter, Depends, Header, Query

from app.analytics.store import get_analytics
from app.api.auth import Identity, get_identity, require_operator

router = APIRouter(tags=["back-office"])


@router.get("/operator/queue")
async def operator_queue(
    identity: Identity | None = Depends(get_identity), x_operator_token: str | None = Header(default=None)
) -> dict:
    require_operator(identity, x_operator_token)
    items = get_analytics().pending_queue()
    return {"pending": len(items), "items": items}


@router.get("/metrics/summary")
async def metrics_summary(
    hours: int = Query(default=24, ge=1, le=24 * 30),
    identity: Identity | None = Depends(get_identity),
    x_operator_token: str | None = Header(default=None),
) -> dict:
    require_operator(identity, x_operator_token)
    return get_analytics().summary(hours)
