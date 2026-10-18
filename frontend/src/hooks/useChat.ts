// Estado y flujo de una conversación: streaming SSE, progreso por nodo, aprobación HITL y
// confirmación de acciones por el cliente.

import { useCallback, useReducer, useRef } from "react";
import { approve, ApiRequestError, confirmAction, streamChat } from "../api/client";
import type { ApproveRequest, ChatResponse, InterruptInfo } from "../api/types";

export interface NodeProgress {
  node: string;
  status: "running" | "done";
  latency_ms?: number | null;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  streaming?: boolean;
  response?: ChatResponse; // metadatos del turno (intención, citas, métricas)
}

interface State {
  customerId: string;
  threadId: string | null;
  messages: Message[];
  nodes: NodeProgress[];
  pending: InterruptInfo | null; // reclamo del operador o acción que espera la confirmación del cliente
  busy: boolean;
  error: string | null;
  lastResponse: ChatResponse | null;
}

type Action =
  | { type: "reset"; customerId: string }
  | { type: "send"; text: string; assistantId: string }
  | { type: "meta"; threadId: string }
  | { type: "node"; node: string; status: "start" | "end"; latency_ms?: number | null }
  | { type: "token"; assistantId: string; delta: string }
  | { type: "interrupt"; interrupt: InterruptInfo }
  | { type: "final"; assistantId: string; response: ChatResponse }
  | { type: "approving" }
  | { type: "approved"; response: ChatResponse; note: string }
  | { type: "error"; message: string; assistantId?: string };

const initialState = (customerId: string): State => ({
  customerId,
  threadId: null,
  messages: [],
  nodes: [],
  pending: null,
  busy: false,
  error: null,
  lastResponse: null,
});

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "reset":
      return initialState(action.customerId);
    case "send":
      return {
        ...state,
        busy: true,
        error: null,
        nodes: [],
        messages: [
          ...state.messages,
          { id: crypto.randomUUID(), role: "user", text: action.text },
          { id: action.assistantId, role: "assistant", text: "", streaming: true },
        ],
      };
    case "meta":
      return { ...state, threadId: action.threadId };
    case "node":
      if (action.status === "start") {
        return { ...state, nodes: [...state.nodes, { node: action.node, status: "running" }] };
      }
      return {
        ...state,
        nodes: state.nodes.map((n) =>
          n.node === action.node && n.status === "running" ? { ...n, status: "done", latency_ms: action.latency_ms } : n,
        ),
      };
    case "token":
      return {
        ...state,
        messages: state.messages.map((m) => (m.id === action.assistantId ? { ...m, text: m.text + action.delta } : m)),
      };
    case "interrupt":
      return { ...state, pending: action.interrupt };
    case "final":
      // Se REEMPLAZA el texto acumulado por final.answer: el output_guardrail pudo
      // corregir o enmascarar lo que se emitió como tokens
      return {
        ...state,
        busy: false,
        lastResponse: action.response,
        messages: state.messages.map((m) =>
          m.id === action.assistantId ? { ...m, text: action.response.answer, streaming: false, response: action.response } : m,
        ),
      };
    case "approving":
      return { ...state, busy: true, error: null };
    case "approved":
      return {
        ...state,
        busy: false,
        pending: null,
        lastResponse: action.response,
        nodes: action.response.metrics.nodes.map((n) => ({ node: n.node, status: "done", latency_ms: n.latency_ms })),
        messages: [
          ...state.messages,
          { id: crypto.randomUUID(), role: "system", text: action.note },
          { id: crypto.randomUUID(), role: "assistant", text: action.response.answer, response: action.response },
        ],
      };
    case "error":
      return {
        ...state,
        busy: false,
        error: action.message,
        // Se quita la burbuja vacía del asistente si el turno falló antes de responder
        messages: state.messages.filter((m) => !(m.id === action.assistantId && m.text === "")),
      };
  }
}

const DECISION_NOTES: Record<ApproveRequest["decision"], string> = {
  approve: "Un ejecutivo aprobó el reclamo",
  edit: "Un ejecutivo editó y aprobó el reclamo",
  reject: "Un ejecutivo rechazó el reclamo",
};

export function useChat(initialCustomer: string) {
  const [state, dispatch] = useReducer(reducer, initialCustomer, initialState);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (text: string) => {
      const assistantId = crypto.randomUUID();
      dispatch({ type: "send", text, assistantId });
      abortRef.current = new AbortController();
      try {
        await streamChat(
          { customer_id: state.customerId, message: text, thread_id: state.threadId },
          (event) => {
            switch (event.event) {
              case "meta":
                return dispatch({ type: "meta", threadId: event.data.thread_id });
              case "node":
                return dispatch({ type: "node", ...event.data });
              case "token":
                return dispatch({ type: "token", assistantId, delta: event.data.delta });
              case "interrupt":
                return dispatch({ type: "interrupt", interrupt: event.data });
              case "final":
                return dispatch({ type: "final", assistantId, response: event.data });
              case "error":
                return dispatch({ type: "error", assistantId, message: event.data.message });
            }
          },
          abortRef.current.signal,
        );
      } catch (err) {
        const message =
          err instanceof ApiRequestError
            ? `${err.message} (${err.code})`
            : "No se pudo conectar con el backend. ¿Está corriendo uvicorn en el puerto 8000?";
        dispatch({ type: "error", assistantId, message });
      }
    },
    [state.customerId, state.threadId],
  );

  const decide = useCallback(
    async (request: ApproveRequest) => {
      if (!state.threadId) return;
      dispatch({ type: "approving" });
      try {
        const response = await approve(state.threadId, request);
        dispatch({ type: "approved", response, note: `${DECISION_NOTES[request.decision]} (${request.reviewer_id}).` });
      } catch (err) {
        dispatch({ type: "error", message: err instanceof ApiRequestError ? err.message : String(err) });
      }
    },
    [state.threadId],
  );

  const confirm = useCallback(
    async (yes: boolean) => {
      if (!state.threadId) return;
      dispatch({ type: "approving" });
      try {
        const response = await confirmAction(state.threadId, state.customerId, yes);
        dispatch({ type: "approved", response, note: yes ? "Usted confirmó la acción." : "Usted canceló la acción." });
      } catch (err) {
        dispatch({ type: "error", message: err instanceof ApiRequestError ? err.message : String(err) });
      }
    },
    [state.threadId, state.customerId],
  );

  const reset = useCallback((customerId: string) => {
    abortRef.current?.abort();
    dispatch({ type: "reset", customerId });
  }, []);

  return { state, send, decide, confirm, reset };
}
