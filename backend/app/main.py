"""Punto de entrada FastAPI.

Arranque:  uvicorn app.main:app --reload   (desde la carpeta backend/)
"""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import routes_chat, routes_health
from app.config import get_settings
from app.graph.builder import build_graph

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(settings.app_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Se compila el grafo una sola vez al iniciar y se comparte entre requests
    app.state.graph = build_graph()
    logger.info("Grafo compilado (router_backend=%s)", settings.router_backend)
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
app.include_router(routes_health.router)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    """Formato de error común del contrato: {"error": {code, message, request_id}}."""
    body = {"error": {"code": code, "message": message, "request_id": str(uuid.uuid4())}}
    return UTF8JSONResponse(status_code=status_code, content=body)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:])
    return _error(422, "VALIDATION_ERROR", f"{field}: {first.get('msg', 'entrada inválida')}")
