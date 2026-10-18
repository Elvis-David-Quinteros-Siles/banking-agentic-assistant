"""Punto de entrada FastAPI.

Arranque:  uvicorn app.main:app --reload   (desde la carpeta backend/)
"""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import routes_chat, routes_health, routes_hitl
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
    # 1. Core bancario simulado: crea el esquema y carga los datos semilla si está vacío
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
            from app.llm.provider import get_chat_model

            get_chat_model().invoke("Responde solo: ok")
        except Exception as error:  # noqa: BLE001
            logger.warning("No se pudo precalentar el LLM (%s): los nodos usarán su ruta determinista", error)
    if settings.router_backend == "lora":
        try:
            from app.llm.lora_router import get_lora_classifier

            get_lora_classifier().classify("hola")  # carga pesos + calienta kernels CUDA
        except Exception as error:  # noqa: BLE001
            logger.warning("Router LoRA no disponible (%s): se usará el router por reglas", error)
    # 3. Checkpointer persistente: los reclamos pendientes de HITL sobreviven a reinicios
    settings.checkpoints_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoints_path)) as checkpointer:
        app.state.graph = build_graph(checkpointer)
        logger.info(
            "Grafo compilado (llm=%s, router=%s, knowledge=%s)",
            settings.llm_provider, settings.router_backend, settings.knowledge_backend,
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
app.include_router(routes_chat.router)
app.include_router(routes_hitl.router)
app.include_router(routes_health.router)


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
