// Cliente HTTP del backend. Todas las rutas pasan por el proxy de Vite (/api → FastAPI).

import { readSSE } from "./sse";
import type {
  ApiError,
  ApproveRequest,
  ChatRequest,
  ChatResponse,
  HealthResponse,
  MetricsSummary,
  QueueItem,
  StreamEvent,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiRequestError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function toError(response: Response): Promise<ApiRequestError> {
  try {
    const body = (await response.json()) as ApiError;
    return new ApiRequestError(response.status, body.error.code, body.error.message);
  } catch {
    return new ApiRequestError(response.status, `HTTP_${response.status}`, response.statusText);
  }
}

// Token del back-office (si el servidor define OPERATOR_TOKEN). Se guarda solo en este navegador.
export function operatorHeaders(): Record<string, string> {
  try {
    const token = window.localStorage.getItem("operatorToken");
    return token ? { "X-Operator-Token": token } : {};
  } catch {
    return {};
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: operatorHeaders() });
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...operatorHeaders() },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

export const getHealth = async (): Promise<HealthResponse> => {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) throw await toError(response);
  return (await response.json()) as HealthResponse;
};

export const getQueue = () => getJson<{ pending: number; items: QueueItem[] }>("/operator/queue");

export const getSummary = (hours: number) => getJson<MetricsSummary>(`/metrics/summary?hours=${hours}`);

export const sendChat = (request: ChatRequest) => postJson<ChatResponse>("/chat", request);

export const approve = (threadId: string, request: ApproveRequest) =>
  postJson<ChatResponse>(`/approve/${encodeURIComponent(threadId)}`, request);

export const confirmAction = (threadId: string, customerId: string, confirm: boolean) =>
  postJson<ChatResponse>(`/confirm/${encodeURIComponent(threadId)}`, { customer_id: customerId, confirm });

/** POST /chat/stream: llama a onEvent por cada evento SSE tipado. */
export async function streamChat(
  request: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(request),
    signal,
  });
  // Los errores de validación/estado (409 thread pendiente, 422) llegan ANTES de abrir el stream
  if (!response.ok) throw await toError(response);
  if (!response.body) throw new ApiRequestError(500, "NO_STREAM", "El navegador no soporta streaming");

  for await (const raw of readSSE(response.body)) {
    onEvent({ event: raw.event, data: JSON.parse(raw.data) } as StreamEvent);
  }
}
