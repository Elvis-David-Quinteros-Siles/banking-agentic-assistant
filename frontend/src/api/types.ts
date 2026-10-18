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

export interface InterruptInfo {
  type: "complaint_review" | string;
  payload: ComplaintDraft;
}

export interface ChatResponse {
  thread_id: string;
  status: TurnStatus;
  answer: string;
  intent: string | null;
  intent_confidence: number | null;
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
