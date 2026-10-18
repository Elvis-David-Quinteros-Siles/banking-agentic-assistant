"""Endpoint POST /chat (síncrono). /chat/stream se agrega en la Fase 2."""

import time
import uuid

from fastapi import APIRouter, Request
from langchain_core.messages import HumanMessage

from app.api.schemas import ChatRequest, ChatResponse, Metrics, NodeMetric

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    graph = request.app.state.graph
    thread_id = body.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # El trace es acumulativo por thread: guardamos su largo para quedarnos solo con este turno
    previous = await graph.aget_state(config)
    previous_len = len(previous.values.get("trace", [])) if previous.values else 0

    started = time.perf_counter()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=body.message)], "customer_id": body.customer_id},
        config=config,
    )
    total_ms = round((time.perf_counter() - started) * 1000, 2)

    nodes = [NodeMetric(**entry) for entry in result.get("trace", [])[previous_len:]]
    return ChatResponse(
        thread_id=thread_id,
        status="completed",
        answer=result.get("final_answer") or "",
        intent=result.get("intent"),
        intent_confidence=result.get("intent_confidence"),
        metrics=Metrics(
            latency_ms=total_ms,
            tokens_in=sum(n.tokens_in for n in nodes),
            tokens_out=sum(n.tokens_out for n in nodes),
            cost_usd=round(sum(n.cost_usd for n in nodes), 6),
            nodes=nodes,
        ),
    )
