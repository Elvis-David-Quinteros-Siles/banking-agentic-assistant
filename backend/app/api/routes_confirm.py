"""Endpoint POST /confirm/{thread_id}: el CLIENTE confirma o cancela una acción preparada (bloqueo de tarjeta).

Diferente de /approve (operador del back-office): aquí decide el dueño de la cuenta, y solo él.
Se verifica que el customer_id del pedido sea el de la conversación pausada; en producción esa
identidad vendría del token de sesión del canal, no del cuerpo del pedido.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.schemas import ChatResponse
from app.services import chat_service

router = APIRouter(tags=["acciones"])


class ConfirmRequest(BaseModel):
    customer_id: str = Field(pattern=r"^CLI-\d{3}$")
    confirm: bool


@router.post("/confirm/{thread_id}", response_model=ChatResponse)
async def confirm_action(thread_id: str, body: ConfirmRequest, request: Request) -> ChatResponse:
    graph = request.app.state.graph
    state = await chat_service.ensure_interrupted(graph, thread_id, node="action_confirmation")
    if state.get("customer_id") != body.customer_id:
        # Nadie puede confirmar una acción sobre las tarjetas de otro cliente
        raise HTTPException(status_code=403, detail="La acción pertenece a otra sesión de cliente")
    decision = {"confirm": body.confirm}
    return await chat_service.run_turn(
        graph, thread_id, chat_service.resume_input(decision), kind="confirm", customer_id=body.customer_id
    )
