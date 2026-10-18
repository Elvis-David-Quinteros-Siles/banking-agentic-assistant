// Tipos del contrato de API (espejo de backend/app/api/schemas.py)

export type TurnStatus = "completed" | "interrupted" | "blocked" | "clarification";

export interface NodeMetric {
  node: string;
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
}

export interface ComplaintDraft {
  categoria: string;
  producto: string | null;
  monto: number | null;
  moneda: "BOB" | "USD" | null;
  prioridad: "alta" | "media" | "baja";
  resumen: string;
  customer_id: string;
}

export interface CardAction {
  accion: "bloquear_tarjeta";
  tarjeta: string;
  last4: string;
  motivo: string;
}

// complaint_review: decide un operador (/approve) · action_confirmation: decide el cliente (/confirm)
export type InterruptInfo =
  | { type: "complaint_review"; payload: ComplaintDraft }
  | { type: "action_confirmation"; payload: CardAction };

export interface ChatResponse {
  thread_id: string;
  status: TurnStatus;
  answer: string;
  intent: string | null;
  intent_confidence: number | null;
  router_stage?: "rules" | "lora" | "llm" | null;
  citations: string[];
  interrupt: InterruptInfo | null;
  guardrail: { blocked: boolean; reason: string | null; pii_found: string[] };
  degraded: boolean;
  metrics: {
    latency_ms: number;
    tokens_in: number;
    tokens_out: number;
    cost_usd: number;
    nodes: NodeMetric[];
  };
}

export interface ChatRequest {
  customer_id: string;
  message: string;
  thread_id?: string | null;
}

export interface ApproveRequest {
  decision: "approve" | "edit" | "reject";
  reviewer_id: string;
  comment?: string | null;
  edited_draft?: Partial<ComplaintDraft> | null;
}

export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  version: string;
  components: Record<string, string>;
}

export interface ApiError {
  error: { code: string; message: string; request_id: string };
}

// Eventos SSE de /chat/stream
export type StreamEvent =
  | { event: "meta"; data: { thread_id: string } }
  | { event: "node"; data: { node: string; status: "start" | "end"; latency_ms?: number | null } }
  | { event: "token"; data: { delta: string } }
  | { event: "interrupt"; data: InterruptInfo }
  | { event: "final"; data: ChatResponse }
  | { event: "error"; data: { code: string; message: string } };

// Back-office: cola del operador y KPIs (GET /operator/queue, GET /metrics/summary)
export interface QueueItem {
  thread_id: string;
  customer_id: string;
  created_at: string;
  age_minutes: number;
  priority: "alta" | "media" | "baja";
  draft: ComplaintDraft;
}

export interface IntentKpi {
  intent: string;
  turns: number;
  completed_rate: number | null;
  hitl_rate: number | null;
  clarification_rate: number | null;
  blocked_rate: number | null;
  degraded_rate: number | null;
  latency_p95_ms: number | null;
  avg_cost_usd: number;
}

export interface MetricsSummary {
  window_hours: number;
  generated_at: string;
  kpis: {
    turns: number;
    containment_rate: number | null;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
    degraded_rate: number | null;
    blocked_rate: number | null;
    clarification_rate: number | null;
    cost_usd: number;
    tokens: number;
  };
  hitl: {
    created: number;
    pending: number;
    approved: number;
    edited: number;
    rejected: number;
    approved_without_edit_rate: number | null;
    avg_decision_minutes: number | null;
    oldest_pending_minutes: number | null;
  };
  by_intent: IntentKpi[];
  hourly: { hour: string; turns: number; latency_p95_ms: number | null }[];
  safety: { blocked_reasons: Record<string, number>; pii_detected: Record<string, number> };
}
