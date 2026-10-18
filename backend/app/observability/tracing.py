"""Instrumentación por nodo: mide latencia y la agrega al campo `trace` del estado.

Cada nodo del grafo se decora con @traced("nombre"). El decorador añade una entrada
{node, latency_ms, tokens_in, tokens_out, cost_usd} a la actualización que devuelve el nodo.
Los tokens/costo quedan en 0 hasta que existan nodos con LLM (Fase 2).
"""

import functools
import inspect
import time
from collections.abc import Callable
from typing import Any


def _trace_entry(node: str, started: float, update: dict[str, Any]) -> dict[str, Any]:
    # El nodo puede reportar su propio consumo en la clave privada "_usage"
    usage = update.pop("_usage", {}) if isinstance(update, dict) else {}
    return {
        "node": node,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "tokens_in": usage.get("tokens_in", 0),
        "tokens_out": usage.get("tokens_out", 0),
        "cost_usd": usage.get("cost_usd", 0.0),
    }


def traced(node: str) -> Callable:
    """Decorador para nodos síncronos o asíncronos de LangGraph."""

    def decorator(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(state, *args, **kwargs):
                started = time.perf_counter()
                update = dict(await fn(state, *args, **kwargs) or {})
                update["trace"] = [_trace_entry(node, started, update)]
                return update

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(state, *args, **kwargs):
            started = time.perf_counter()
            update = dict(fn(state, *args, **kwargs) or {})
            update["trace"] = [_trace_entry(node, started, update)]
            return update

        return sync_wrapper

    return decorator
