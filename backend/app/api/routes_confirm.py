"""Endpoint POST /confirm/{thread_id}: el CLIENTE confirma o cancela una acción preparada (bloqueo de tarjeta).

Diferente de /approve (operador del back-office): aquí decide el dueño de la cuenta, y solo él.
Con AUTH_MODE=jwt el cliente sale del token; debe ser el de la conversación pausada.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import Identity, get_identity, resolve_customer
from app.api.schemas import ChatResponse
from app.services import chat_service

router = APIRouter(tags=["acciones"])


class ConfirmRequest(BaseModel):
    customer_id: str | None = Field(default=None, pattern=r"^CLI-\d{3}$")
    confirm: bool


@router.post("/confirm/{thread_id}", response_model=ChatResponse)
async def confirm_action(
    thread_id: str, body: ConfirmRequest, request: Request, identity: Identity | None = Depends(get_identity)
) -> ChatResponse:
    customer_id = resolve_customer(identity, body.customer_id)
    graph = request.app.state.graph
    state = await chat_service.ensure_interrupted(graph, thread_id, node="action_confirmation")
    if state.get("customer_id") != customer_id:
        # Nadie puede confirmar una acción sobre las tarjetas de otro cliente
        raise HTTPException(status_code=403, detail="La acción pertenece a otra sesión de cliente")
    decision = {"confirm": body.confirm}
    return await chat_service.run_turn(
        graph, thread_id, chat_service.resume_input(decision), kind="confirm", customer_id=customer_id
    )
