"""Límite de peticiones por cliente (ventana deslizante en memoria) para los endpoints caros.

Protege la demo pública de abuso: cada turno ejecuta el grafo completo (y con LLM, una
inferencia). En memoria alcanza para un solo proceso; con varias réplicas iría en Redis o en el
proxy (limit_req de Nginx). RATE_LIMIT_PER_MINUTE=0 lo desactiva (desarrollo, tests, Evals).
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

LIMITED_PATHS = ("/chat", "/api/chat", "/approve", "/api/approve")


def client_key(request: Request) -> str:
    # Detrás de Nginx la IP real llega en X-Real-IP (la pone el proxy, no el cliente)
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "desconocido")


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """None si se permite; si no, los segundos a esperar."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return round(self.window - (now - hits[0]), 1)
            hits.append(now)
            return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter: SlidingWindowLimiter):
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and request.url.path.startswith(LIMITED_PATHS):
            retry_after = self.limiter.check(client_key(request))
            if retry_after is not None:
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(int(retry_after) + 1)},
                    content={
                        "error": {
                            "code": "RATE_LIMITED",
                            "message": f"Demasiadas solicitudes. Intente de nuevo en {int(retry_after) + 1} s.",
                            "request_id": "",
                        }
                    },
                )
        return await call_next(request)
