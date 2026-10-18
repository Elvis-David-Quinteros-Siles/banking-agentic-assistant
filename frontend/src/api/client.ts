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

// Token del back-office con AUTH_MODE=off y OPERATOR_TOKEN definido. Se guarda solo en este navegador.
export function operatorHeaders(): Record<string, string> {
  try {
    const token = window.localStorage.getItem("operatorToken");
    return token ? { "X-Operator-Token": token } : {};
  } catch {
    return {};
  }
}

// Sesión JWT: el backend toma la identidad del token, no del cuerpo. En la demo el token lo emite
// POST /auth/demo-token (clientes ficticios); en producción vendría del login del banco (OIDC).
export type Principal = { role: "customer"; customerId: string } | { role: "operator" };

const tokens = new Map<string, { token: string; expiresAt: number } | null>();
const keyOf = (p: Principal) => (p.role === "customer" ? `customer:${p.customerId}` : "operator");

async function sessionToken(principal: Principal): Promise<string | null> {
  const key = keyOf(principal);
  const cached = tokens.get(key);
  if (cached === null) return null; // el servidor no usa JWT (AUTH_MODE=off)
  if (cached && cached.expiresAt > Date.now() + 30_000) return cached.token;
  const response = await fetch(`${API_BASE}/auth/demo-token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(principal.role === "customer" ? { customer_id: principal.customerId } : { role: "operator" }),
  });
  if (response.status === 404) {
    tokens.set(key, null);
    return null;
  }
  if (!response.ok) throw await toError(response);
  const body = (await response.json()) as { access_token: string; expires_in: number };
  tokens.set(key, { token: body.access_token, expiresAt: Date.now() + body.expires_in * 1000 });
  return body.access_token;
}

async function authHeaders(principal?: Principal): Promise<Record<string, string>> {
  const headers: Record<string, string> = { ...operatorHeaders() };
  if (principal) {
    const token = await sessionToken(principal);
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

/** fetch con el token de la sesión; si vence (401), se pide uno nuevo y se reintenta una vez. */
async function authFetch(path: string, init: RequestInit, principal?: Principal): Promise<Response> {
  const run = async () =>
    fetch(`${API_BASE}${path}`, { ...init, headers: { ...(init.headers ?? {}), ...(await authHeaders(principal)) } });
  let response = await run();
  if (response.status === 401 && principal) {
    tokens.delete(keyOf(principal));
    response = await run();
  }
  return response;
}

async function getJson<T>(path: string, principal?: Principal): Promise<T> {
  const response = await authFetch(path, {}, principal);
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body: unknown, principal?: Principal): Promise<T> {
  const response = await authFetch(
    path,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    principal,
  );
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

const OPERATOR: Principal = { role: "operator" };
const customer = (customerId: string): Principal => ({ role: "customer", customerId });

export const getHealth = async (): Promise<HealthResponse> => {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) throw await toError(response);
  return (await response.json()) as HealthResponse;
};

export const getQueue = () => getJson<{ pending: number; items: QueueItem[] }>("/operator/queue", OPERATOR);

export const getSummary = (hours: number) => getJson<MetricsSummary>(`/metrics/summary?hours=${hours}`, OPERATOR);

export const sendChat = (request: ChatRequest) =>
  postJson<ChatResponse>("/chat", request, customer(request.customer_id));

export const approve = (threadId: string, request: ApproveRequest) =>
  postJson<ChatResponse>(`/approve/${encodeURIComponent(threadId)}`, request, OPERATOR);

export const confirmAction = (threadId: string, customerId: string, confirm: boolean) =>
  postJson<ChatResponse>(
    `/confirm/${encodeURIComponent(threadId)}`,
    { customer_id: customerId, confirm },
    customer(customerId),
  );

/** POST /chat/stream: llama a onEvent por cada evento SSE tipado. */
export async function streamChat(
  request: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await authFetch(
    "/chat/stream",
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(request),
      signal,
    },
    customer(request.customer_id),
  );
  // Los errores de validación/estado (409 thread pendiente, 422) llegan ANTES de abrir el stream
  if (!response.ok) throw await toError(response);
  if (!response.body) throw new ApiRequestError(500, "NO_STREAM", "El navegador no soporta streaming");

  for await (const raw of readSSE(response.body)) {
    onEvent({ event: raw.event, data: JSON.parse(raw.data) } as StreamEvent);
  }
}
