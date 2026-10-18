"""Punto de entrada FastAPI.

Arranque:  uvicorn app.main:app --reload   (desde la carpeta backend/)
"""

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth, routes_chat, routes_confirm, routes_health, routes_hitl, routes_operator
from app.api.rate_limit import RateLimitMiddleware, SlidingWindowLimiter
from app.config import get_settings
from app.db.sqlite import init_db
from app.graph.builder import build_graph
from app.services.chat_service import ThreadError
from app.tools.knowledge_base import get_retriever

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(settings.app_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Core bancario: en SQLite se crea y carga solo; SQL Server se inicializa aparte con
    #    scripts/init_sqlserver.py (un administrador crea la base; la app solo ejecuta procedimientos)
    if settings.core_backend == "sqlite":
        init_db(settings.sqlite_path, settings.seed_dir / "customers.json")
    # 2. Precarga del retriever (y del modelo de embeddings): sin esto, la primera pregunta
    #    del día paga ~3 s de arranque en frío
    try:
        get_retriever().search("precarga")
    except Exception as error:  # noqa: BLE001
        logger.warning("No se pudo precargar el retriever: %s", error)
    if settings.llm_provider != "fake":
        # Con Ollama, la primera llamada carga el modelo en la GPU (medido: ~90 s desde el volumen
        # de Docker). Se paga al arrancar, no en la primera consulta de un cliente.
        try:
            from app.llm.provider import require_llm

            require_llm().invoke("Responde solo: ok")
        except Exception as error:  # noqa: BLE001
            logger.warning("No se pudo precalentar el LLM (%s): los nodos usarán su ruta determinista", error)
    if settings.router_backend in ("lora", "cascade"):
        try:
            from app.llm.lora_router import get_lora_classifier

            get_lora_classifier().classify("hola")  # carga pesos + calienta kernels CUDA
        except Exception as error:  # noqa: BLE001
            logger.warning("Router LoRA no disponible (%s): se usará el router por reglas", error)
    # 3. Checkpointer persistente: los reclamos pendientes de HITL sobreviven a reinicios
    settings.checkpoints_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoints_path)) as checkpointer:
        app.state.graph = build_graph(checkpointer)
        if settings.demo_seed_traffic:
            # En segundo plano: el servidor acepta tráfico mientras se genera la demo
            from app.analytics.demo_seed import seed_demo_traffic

            app.state.seed_task = asyncio.create_task(seed_demo_traffic(app.state.graph))
        logger.info(
            "Grafo compilado (llm=%s, router=%s, knowledge=%s, core=%s)",
            settings.llm_provider,
            settings.router_backend,
            settings.knowledge_backend,
            settings.core_backend,
        )
        yield


class UTF8JSONResponse(JSONResponse):
    # Declarar el charset evita que clientes como PowerShell 5.1 decodifiquen como ISO-8859-1
    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    default_response_class=UTF8JSONResponse,
)
if settings.rate_limit_per_minute > 0:
    app.add_middleware(RateLimitMiddleware, limiter=SlidingWindowLimiter(settings.rate_limit_per_minute))

for api_router in (
    auth.router,
    routes_chat.router,
    routes_hitl.router,
    routes_confirm.router,
    routes_health.router,
    routes_operator.router,
):
    app.include_router(api_router)
    # Mismo prefijo que usa el frontend (/api): permite servir todo desde un solo puerto
    app.include_router(api_router, prefix="/api", include_in_schema=False)

# Demo pública (imagen Docker): si existe el build del frontend, se sirve en "/".
# Se monta al final para que las rutas de la API tengan prioridad.
if settings.frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    """Formato de error común del contrato: {"error": {code, message, request_id}}."""
    body = {"error": {"code": code, "message": message, "request_id": str(uuid.uuid4())}}
    return UTF8JSONResponse(status_code=status_code, content=body)


@app.exception_handler(ThreadError)
async def thread_error_handler(_: Request, exc: ThreadError) -> JSONResponse:
    return _error(exc.status_code, exc.code, exc.message)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:])
    return _error(422, "VALIDATION_ERROR", f"{field}: {first.get('msg', 'entrada inválida')}")
