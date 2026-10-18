"""Modelos Pydantic del contrato de API (ver docs/ARCHITECTURE.md §4)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    thread_id: str | None = Field(default=None, description="Si se omite, se crea una conversación nueva")
    customer_id: str = Field(pattern=r"^CLI-\d{3}$", examples=["CLI-001"])
    message: str = Field(min_length=1, max_length=2000)


class GuardrailInfo(BaseModel):
    blocked: bool = False
    reason: str | None = None
    pii_found: list[str] = []


class NodeMetric(BaseModel):
    node: str
    latency_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class Metrics(BaseModel):
    latency_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    nodes: list[NodeMetric] = []


class InterruptInfo(BaseModel):
    type: str
    payload: dict[str, Any]


class ChatResponse(BaseModel):
    thread_id: str
    status: Literal["completed", "interrupted", "blocked", "clarification"]
    answer: str
    intent: str | None = None
    intent_confidence: float | None = None
    citations: list[str] = []
    interrupt: InterruptInfo | None = None
    guardrail: GuardrailInfo = GuardrailInfo()
    metrics: Metrics


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    components: dict[str, str]


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
