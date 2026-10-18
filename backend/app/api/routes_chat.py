"""Endpoints POST /chat (respuesta completa) y POST /chat/stream (SSE)."""

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.auth import Identity, get_identity, resolve_customer
from app.api.schemas import ChatRequest, ChatResponse
from app.services import chat_service

router = APIRouter(tags=["chat"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


async def _prepare(body: ChatRequest, request: Request, identity: Identity | None) -> tuple[str, str]:
    customer_id = resolve_customer(identity, body.customer_id)
    thread_id = body.thread_id or str(uuid.uuid4())
    graph = request.app.state.graph
    # Se valida ANTES de ejecutar (o de abrir el stream) para responder con un 403/409 normal
    await chat_service.ensure_thread_owner(graph, thread_id, customer_id)
    await chat_service.ensure_not_pending(graph, thread_id)
    return customer_id, thread_id


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request, identity: Identity | None = Depends(get_identity)) -> ChatResponse:
    customer_id, thread_id = await _prepare(body, request, identity)
    return await chat_service.run_turn(
        request.app.state.graph, thread_id, chat_service.chat_input(customer_id, body.message), customer_id=customer_id
    )


@router.post("/chat/stream")
async def chat_stream(
    body: ChatRequest, request: Request, identity: Identity | None = Depends(get_identity)
) -> StreamingResponse:
    customer_id, thread_id = await _prepare(body, request, identity)
    events = chat_service.stream_turn(
        request.app.state.graph, thread_id, chat_service.chat_input(customer_id, body.message), customer_id=customer_id
    )
    return StreamingResponse(events, media_type="text/event-stream; charset=utf-8", headers=SSE_HEADERS)
