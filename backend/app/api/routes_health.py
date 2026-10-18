"""Endpoint GET /health. En fases posteriores se agregan chequeos de SQLite, Neo4j y LoRA."""

from fastapi import APIRouter, Request

from app.api.schemas import HealthResponse
from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = get_settings()
    components = {
        "graph": "ok" if getattr(request.app.state, "graph", None) is not None else "down",
        "llm": "disabled",  # Fase 1: todavía no hay LLM
        "router": settings.router_backend,
    }
    status = "ok" if components["graph"] == "ok" else "down"
    return HealthResponse(status=status, version=settings.app_version, components=components)
