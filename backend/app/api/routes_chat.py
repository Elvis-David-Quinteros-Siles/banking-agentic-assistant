"""Endpoints POST /chat (respuesta completa) y POST /chat/stream (SSE)."""

import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.schemas import ChatRequest, ChatResponse
from app.services import chat_service

router = APIRouter(tags=["chat"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    graph = request.app.state.graph
    thread_id = body.thread_id or str(uuid.uuid4())
    await chat_service.ensure_not_pending(graph, thread_id)
    return await chat_service.run_turn(graph, thread_id, chat_service.chat_input(body.customer_id, body.message))


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    graph = request.app.state.graph
    thread_id = body.thread_id or str(uuid.uuid4())
    # Se valida ANTES de abrir el stream para poder responder con un 409 normal
    await chat_service.ensure_not_pending(graph, thread_id)
    events = chat_service.stream_turn(graph, thread_id, chat_service.chat_input(body.customer_id, body.message))
    return StreamingResponse(events, media_type="text/event-stream; charset=utf-8", headers=SSE_HEADERS)
