"""Endpoint GET /health: estado de cada componente."""

import httpx
from fastapi import APIRouter, Request

from app.api.schemas import HealthResponse
from app.config import get_settings
from app.db.sqlite import connect

router = APIRouter(tags=["health"])


async def _check_llm() -> str:
    settings = get_settings()
    if settings.llm_provider == "fake":
        return "disabled"
    if settings.llm_provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{settings.ollama_base_url}/api/tags")
            models = [m["name"] for m in response.json().get("models", [])]
            return "ok" if settings.ollama_model in models else f"model_missing:{settings.ollama_model}"
        except Exception:  # noqa: BLE001
            return "down"
    # Proveedores cloud: solo se verifica que haya API key (no se gasta una llamada)
    key = settings.openai_api_key if settings.llm_provider == "openai" else settings.anthropic_api_key
    return "ok" if key else "missing_api_key"


def _check_sqlite() -> str:
    try:
        with connect(get_settings().sqlite_path) as conn:
            conn.execute("SELECT 1 FROM accounts LIMIT 1")
        return "ok"
    except Exception:  # noqa: BLE001
        return "down"


def _check_neo4j() -> str:
    if get_settings().knowledge_backend != "neo4j":
        return "disabled"
    from app.rag.neo4j_client import neo4j_available

    # "down" => el knowledge_agent está usando el fallback BM25 en memoria
    return "ok" if neo4j_available() else "down"


def _check_lora() -> str:
    """Solo informa si el modelo ya está en memoria (no lo carga: /health debe ser instantáneo)."""
    if get_settings().router_backend != "lora":
        return "disabled"
    from app.llm import lora_router

    return "loaded" if lora_router.get_lora_classifier.cache_info().currsize else "not_loaded"


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = get_settings()
    components = {
        "graph": "ok" if getattr(request.app.state, "graph", None) is not None else "down",
        "sqlite": _check_sqlite(),
        "llm": await _check_llm(),
        "llm_provider": settings.llm_provider,
        "router": settings.router_backend,
        "knowledge": settings.knowledge_backend,
        "neo4j": _check_neo4j(),
        "lora_router": _check_lora(),
    }
    if components["graph"] != "ok" or components["sqlite"] != "ok":
        status = "down"
    elif components["llm"] not in ("ok", "disabled") or components["neo4j"] not in ("ok", "disabled"):
        status = "degraded"  # el sistema responde con rutas deterministas
    else:
        status = "ok"
    return HealthResponse(status=status, version=settings.app_version, components=components)
