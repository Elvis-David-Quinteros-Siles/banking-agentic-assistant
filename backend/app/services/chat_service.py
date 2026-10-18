"""Orquestación de un turno del grafo, compartida por /chat, /chat/stream y /approve.

Responsabilidades:
- Validar el estado del thread (no existe / pendiente de aprobación).
- Ejecutar el grafo (invoke o stream) y construir la respuesta del contrato de API.
- Recortar el trace al turno actual y agregar métricas de latencia, tokens y costo.
"""

import json
import time
from collections.abc import AsyncIterator
from typing import Any, cast

from langchain_core.messages import AIMessageChunk, HumanMessage
from langgraph.types import Command

from app.api.schemas import ChatResponse, GuardrailInfo, InterruptInfo, Metrics, NodeMetric, ResponseStatus
from app.graph.state import turn_reset
from app.observability.langfuse_tracing import turn_trace

# Nodos cuyos tokens de LLM se reenvían al cliente por SSE (el router y el borrador de queja no)
STREAMABLE_NODES = {"query_agent", "knowledge_agent"}


class ThreadError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def _trace_len(graph, thread_id: str) -> int:
    snapshot = await graph.aget_state(_config(thread_id))
    return len(snapshot.values.get("trace", [])) if snapshot.values else 0


async def ensure_thread_owner(graph, thread_id: str, customer_id: str) -> None:
    """Una conversación existente solo la puede continuar su dueño: si no, el historial de otro
    cliente entraría al contexto del nuevo turno (IDOR por thread_id)."""
    snapshot = await graph.aget_state(_config(thread_id))
    owner = (snapshot.values or {}).get("customer_id")
    if owner and owner != customer_id:
        raise ThreadError(403, "THREAD_FORBIDDEN", "La conversación pertenece a otra sesión de cliente.")


async def ensure_not_pending(graph, thread_id: str) -> None:
    """Un thread pausado en HITL no acepta mensajes nuevos hasta que el operador decida."""
    snapshot = await graph.aget_state(_config(thread_id))
    if snapshot.next:
        raise ThreadError(409, "THREAD_PENDING_APPROVAL", "La conversación tiene un reclamo pendiente de aprobación.")


PENDING_MESSAGES = {
    "hitl_review": "La conversación no tiene un reclamo pendiente de aprobación.",
    "action_confirmation": "La conversación no tiene una acción pendiente de confirmación.",
}


async def ensure_interrupted(graph, thread_id: str, node: str = "hitl_review") -> dict:
    """Valida que la conversación exista y esté pausada en `node`. Devuelve su estado."""
    snapshot = await graph.aget_state(_config(thread_id))
    if not snapshot.values:
        raise ThreadError(404, "THREAD_NOT_FOUND", f"No existe la conversación {thread_id}.")
    if node not in snapshot.next:
        raise ThreadError(409, "THREAD_NOT_INTERRUPTED", PENDING_MESSAGES.get(node, "La conversación no está pausada."))
    return dict(snapshot.values)


def chat_input(customer_id: str, message: str) -> dict:
    return {**turn_reset(customer_id), "messages": [HumanMessage(content=message)]}


def resume_input(decision: dict) -> Command:
    return Command(resume=decision)


async def build_response(graph, thread_id: str, previous_len: int, started: float) -> ChatResponse:
    """Construye la respuesta del contrato a partir del checkpoint (fuente de verdad)."""
    snapshot = await graph.aget_state(_config(thread_id))
    values = snapshot.values
    interrupts = [i for task in snapshot.tasks for i in task.interrupts]

    nodes = [NodeMetric(**entry) for entry in values.get("trace", [])[previous_len:]]
    status = cast(ResponseStatus, "interrupted" if interrupts else (values.get("status") or "completed"))
    interrupt = None
    if interrupts:
        value: dict[str, Any] = interrupts[0].value
        interrupt = InterruptInfo(type=value["type"], payload=value["payload"])

    guardrail = values.get("guardrail") or {}
    return ChatResponse(
        thread_id=thread_id,
        status=status,
        answer=values.get("final_answer") or "",
        intent=values.get("intent"),
        intent_confidence=values.get("intent_confidence"),
        citations=values.get("citations") or [],
        interrupt=interrupt,
        guardrail=GuardrailInfo(
            blocked=guardrail.get("blocked", False),
            reason=guardrail.get("reason"),
            pii_found=guardrail.get("pii_found", []),
        ),
        degraded=values.get("degraded", False),
        metrics=Metrics(
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            tokens_in=sum(n.tokens_in for n in nodes),
            tokens_out=sum(n.tokens_out for n in nodes),
            cost_usd=round(sum(n.cost_usd for n in nodes), 6),
            nodes=nodes,
        ),
    )


def record_analytics(
    response: ChatResponse, kind: str, customer_id: str | None, decision: dict | None = None, source: str = "live"
) -> None:
    """KPIs por turno y cola del back-office. Nunca interrumpe la conversación si falla."""
    from app.analytics.store import get_analytics, safe

    store = get_analytics()
    if kind == "approve" and decision:
        safe(store.decide_complaint, response.thread_id, decision)
        customer_id = customer_id or store.customer_for(response.thread_id)
    safe(store.record_turn, response.model_dump(), kind, customer_id, source)
    # Solo los reclamos van a la cola del operador; las confirmaciones de acciones las decide el cliente
    if (
        response.status == "interrupted"
        and response.interrupt
        and response.interrupt.type == "complaint_review"
        and customer_id
    ):
        safe(store.enqueue_complaint, response.thread_id, customer_id, response.interrupt.payload, source)


async def run_turn(
    graph,
    thread_id: str,
    graph_input: Any,
    *,
    kind: str = "chat",
    customer_id: str | None = None,
    decision: dict | None = None,
    source: str = "live",
) -> ChatResponse:
    previous_len = await _trace_len(graph, thread_id)
    started = time.perf_counter()
    with turn_trace(thread_id, customer_id, kind, graph_input) as turn:
        await graph.ainvoke(graph_input, config=turn.config(_config(thread_id)))
        response = await build_response(graph, thread_id, previous_len, started)
        turn.record(response)
    record_analytics(response, kind, customer_id, decision, source)
    return response


def sse(event: str, data: Any) -> str:
    """Serializa un evento Server-Sent Events."""
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def stream_turn(graph, thread_id: str, graph_input: Any, customer_id: str | None = None) -> AsyncIterator[str]:
    """Ejecuta el turno emitiendo eventos SSE: meta, node, token, interrupt, final, error."""
    previous_len = await _trace_len(graph, thread_id)
    started = time.perf_counter()
    streamed_tokens = False
    yield sse("meta", {"thread_id": thread_id})

    try:
        with turn_trace(thread_id, customer_id, "chat", graph_input) as turn:
            async for mode, chunk in graph.astream(
                graph_input, config=turn.config(_config(thread_id)), stream_mode=["tasks", "messages"]
            ):
                if mode == "tasks":
                    if "result" not in chunk:  # inicio de nodo
                        yield sse("node", {"node": chunk["name"], "status": "start"})
                    else:  # fin de nodo (el resultado trae el trace con la latencia)
                        trace = (chunk.get("result") or {}).get("trace") or [{}]
                        yield sse(
                            "node", {"node": chunk["name"], "status": "end", "latency_ms": trace[0].get("latency_ms")}
                        )
                elif mode == "messages":
                    message, metadata = chunk
                    if (
                        metadata.get("langgraph_node") in STREAMABLE_NODES
                        and isinstance(message, AIMessageChunk)
                        and message.content
                        and not message.tool_call_chunks
                    ):
                        streamed_tokens = True
                        yield sse("token", {"delta": message.content})

            response = await build_response(graph, thread_id, previous_len, started)
            turn.record(response)
        record_analytics(response, "chat", customer_id)
        if not streamed_tokens:
            # Sin LLM (modo fake o plantillas): se emite la respuesta en trozos para una UX uniforme
            for word in response.answer.split(" "):
                yield sse("token", {"delta": word + " "})
        if response.interrupt:
            yield sse("interrupt", response.interrupt.model_dump())
        # El frontend debe reemplazar el texto acumulado por final.answer: el output_guardrail
        # puede haber corregido o enmascarado lo que se emitió como tokens
        yield sse("final", response.model_dump())
    except Exception as error:  # noqa: BLE001 - se informa al cliente sin cortar la conexión en seco
        yield sse("error", {"code": "INTERNAL_ERROR", "message": str(error)})
