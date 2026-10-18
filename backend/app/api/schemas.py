"""Modelos Pydantic del contrato de API (ver docs/ARCHITECTURE.md §4)."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ChatRequest(BaseModel):
    thread_id: str | None = Field(default=None, description="Si se omite, se crea una conversación nueva")
    # Con AUTH_MODE=jwt sobra (el cliente sale del token); si se envía, debe coincidir con el token
    customer_id: str | None = Field(default=None, pattern=r"^CLI-\d{3}$", examples=["CLI-001"])
    message: str = Field(min_length=1, max_length=2000)


class ApproveRequest(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    reviewer_id: str = Field(min_length=1, examples=["OP-17"])
    comment: str | None = None
    edited_draft: dict[str, Any] | None = None

    @model_validator(mode="after")
    def edit_requires_draft(self):
        if self.decision == "edit" and not self.edited_draft:
            raise ValueError("decision 'edit' requiere edited_draft")
        return self


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


ResponseStatus = Literal["completed", "interrupted", "blocked", "clarification"]


class ChatResponse(BaseModel):
    thread_id: str
    status: ResponseStatus
    answer: str
    intent: str | None = None
    intent_confidence: float | None = None
    router_stage: str | None = None  # rules | lora | llm: quién clasificó el turno
    citations: list[str] = []
    interrupt: InterruptInfo | None = None
    guardrail: GuardrailInfo = GuardrailInfo()
    degraded: bool = False  # True si algún nodo usó su ruta determinista por fallo del LLM
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
