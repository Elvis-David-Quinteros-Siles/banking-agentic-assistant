"""Trazas en Langfuse self-hosted (opcional): cada turno es una traza con los nodos del grafo, las
llamadas al LLM (tokens, costo, latencia) y scores del resultado (intención, degradación, bloqueo).

- Se activa con LANGFUSE_ENABLED=true + claves; sin eso no hace nada (ni importa el SDK).
- **Sin PII hacia el servidor de trazas**: la entrada del turno llega SIN enmascarar (el
  input_guardrail corre dentro del grafo), así que todo lo que se exporta pasa por mask_pii, y el
  cliente se identifica con el mismo seudónimo que la analítica.
- Un fallo de Langfuse nunca afecta al cliente: se registra y el turno sigue.
"""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.analytics.store import pseudonymize
from app.config import get_settings
from app.guardrails.pii import mask_long_numbers, mask_pii

logger = logging.getLogger(__name__)
CUSTOMER_ID_RE = re.compile(r"\bCLI-\d{3}\b")


def mask_data(*, data: Any, **_: Any) -> Any:
    """Enmascara PII en cualquier estructura que el SDK vaya a exportar (strings, dicts, listas)."""
    if isinstance(data, str):
        masked = mask_long_numbers(mask_pii(data).masked_text)
        # El estado del grafo lleva el customer_id: se reemplaza por el seudónimo (el mismo del userId)
        return CUSTOMER_ID_RE.sub(lambda m: f"[CLIENTE {pseudonymize(m.group())}]", masked)
    if isinstance(data, dict):
        return {k: mask_data(data=v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [mask_data(data=v) for v in data]
    return data


def mask_spans(*, params: Any) -> Any:
    """Red de seguridad al exportar: enmascara TODO atributo de texto de cada span.

    `mask` no cubre todos los campos: al pausarse el grafo (HITL), LangGraph registra la
    interrupción como mensaje de estado con el borrador del reclamo y el customer_id adentro
    (detectado consultando las trazas exportadas).
    """
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        changed: dict[str, Any] = {}
        for key, value in span.attributes.items():
            if isinstance(value, str):
                masked = mask_data(data=value)
                if masked != value:
                    changed[key] = masked
            elif isinstance(value, list | tuple) and value and all(isinstance(v, str) for v in value):
                masked_seq = [mask_data(data=v) for v in value]
                if masked_seq != list(value):
                    changed[key] = masked_seq
        if changed:
            patches[identifier] = OtelSpanPatch(set_attributes=changed)
    return MaskOtelSpansResult(span_patches=patches)


@lru_cache
def get_langfuse() -> Any | None:
    settings = get_settings()
    if not (settings.langfuse_enabled and settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            environment=settings.app_env,
            mask=mask_data,
            mask_otel_spans=mask_spans,
        )
    except Exception as error:  # noqa: BLE001 - la observabilidad no puede tumbar la app
        logger.warning("Langfuse no disponible: %s", error)
        return None


@dataclass
class TurnTrace:
    callbacks: list = field(default_factory=list)
    span: Any = None

    def config(self, base: dict) -> dict:
        return {**base, "callbacks": self.callbacks} if self.callbacks else base

    def record(self, response: Any) -> None:
        """Resultado del turno como output y scores de la traza (filtrables en la UI)."""
        if self.span is None:
            return
        try:
            self.span.update(
                output={"answer": response.answer, "status": response.status, "citations": response.citations},
                metadata={"intent": response.intent, "intent_confidence": response.intent_confidence},
            )
            self.span.score_trace(name="intent", value=response.intent or "ninguna", data_type="CATEGORICAL")
            self.span.score_trace(name="status", value=response.status, data_type="CATEGORICAL")
            self.span.score_trace(name="degraded", value=float(response.degraded), data_type="BOOLEAN")
            self.span.score_trace(
                name="guardrail_blocked", value=float(response.guardrail.blocked), data_type="BOOLEAN"
            )
            self.span.score_trace(name="latency_ms", value=float(response.metrics.latency_ms))
        except Exception as error:  # noqa: BLE001
            logger.warning("No se pudo registrar el turno en Langfuse: %s", error)


@contextmanager
def turn_trace(thread_id: str, customer_id: str | None, kind: str, graph_input: Any = None) -> Iterator[TurnTrace]:
    """Traza raíz del turno. Sin Langfuse configurado devuelve un TurnTrace vacío (no-op)."""
    client = get_langfuse()
    if client is None:
        yield TurnTrace()
        return
    from langfuse import propagate_attributes
    from langfuse.langchain import CallbackHandler

    user = pseudonymize(customer_id) if customer_id else None
    with (
        propagate_attributes(user_id=user, session_id=thread_id, tags=[kind], trace_name=f"turno:{kind}"),
        client.start_as_current_observation(
            name=f"turno:{kind}", as_type="agent", input=_input_summary(graph_input)
        ) as span,
    ):
        yield TurnTrace(callbacks=[CallbackHandler()], span=span)


def _input_summary(graph_input: Any) -> Any:
    """Solo el último mensaje del cliente (enmascarado luego por mask_data), no el estado completo."""
    if isinstance(graph_input, dict) and graph_input.get("messages"):
        return {"message": str(graph_input["messages"][-1].content)}
    return {"resume": str(getattr(graph_input, "resume", ""))}


def flush() -> None:
    client = get_langfuse()
    if client is not None:
        client.flush()
