"""Endpoint GET /health: estado de cada componente."""

from typing import Literal

import httpx
from fastapi import APIRouter, Request

from app.api.schemas import HealthResponse
from app.config import get_settings

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


def _check_core() -> str:
    """Core bancario (SQLite o SQL Server) a través del repositorio."""
    from app.db.repository import get_repository

    return "ok" if get_repository().ping() else "down"


def _check_knowledge_store() -> str:
    """Base de conocimiento externa (Neo4j o pgvector); "down" = el agente usa BM25 en memoria."""
    backend = get_settings().knowledge_backend
    if backend == "llamaindex":
        import importlib.util

        return "ok" if importlib.util.find_spec("llama_index") else "down"
    if backend == "pgvector":
        from app.rag.pgvector_retriever import pgvector_available

        return "ok" if pgvector_available() else "down"
    if backend != "neo4j":
        return "disabled"
    from app.rag.neo4j_client import neo4j_available

    # "down" => el knowledge_agent está usando el fallback BM25 en memoria
    return "ok" if neo4j_available() else "down"


def _check_lora() -> str:
    """Solo informa si el modelo ya está en memoria (no lo carga: /health debe ser instantáneo)."""
    if get_settings().router_backend not in ("lora", "cascade"):
        return "disabled"
    from app.llm import lora_router

    return "loaded" if lora_router.get_lora_classifier.cache_info().currsize else "not_loaded"


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = get_settings()
    components = {
        "graph": "ok" if getattr(request.app.state, "graph", None) is not None else "down",
        "core": _check_core(),
        "core_backend": settings.core_backend,
        "llm": await _check_llm(),
        "llm_provider": settings.llm_provider,
        "router": settings.router_backend,
        "knowledge": settings.knowledge_backend,
        "knowledge_store": _check_knowledge_store(),
        "lora_router": _check_lora(),
    }
    status: Literal["ok", "degraded", "down"]
    if components["graph"] != "ok" or components["core"] != "ok":
        status = "down"
    elif components["llm"] not in ("ok", "disabled") or components["knowledge_store"] not in ("ok", "disabled"):
        status = "degraded"  # el sistema responde con rutas deterministas
    else:
        status = "ok"
    return HealthResponse(status=status, version=settings.app_version, components=components)
