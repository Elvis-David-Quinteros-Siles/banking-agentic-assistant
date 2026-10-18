"""Endpoint POST /approve/{thread_id}: decisión del operador sobre un reclamo pendiente.

En producción este endpoint estaría detrás de autenticación de back-office (rol operador),
separada del canal del cliente.
"""

from fastapi import APIRouter, Header, Request

from app.api.routes_operator import require_operator
from app.api.schemas import ApproveRequest, ChatResponse
from app.services import chat_service

router = APIRouter(tags=["hitl"])


@router.post("/approve/{thread_id}", response_model=ChatResponse)
async def approve(
    thread_id: str, body: ApproveRequest, request: Request, x_operator_token: str | None = Header(default=None)
) -> ChatResponse:
    require_operator(x_operator_token)
    graph = request.app.state.graph
    await chat_service.ensure_interrupted(graph, thread_id)
    decision = body.model_dump()
    return await chat_service.run_turn(
        graph, thread_id, chat_service.resume_input(decision), kind="approve", decision=decision
    )
