// Explicación en lenguaje simple de un turno: qué hizo cada capa y por qué.
// Se arma solo con la respuesta de la API (intención, guardrail, nodos ejecutados, citas, interrupt).

import type { ChatResponse } from "./api/types";
import { INTENT_LABELS } from "./labels";

// Qué hace cada nodo del grafo (backend/app/graph/builder.py)
export const NODE_LABELS: Record<string, string> = {
  input_guardrail: "Enmascara PII y busca prompt injection",
  router: "Clasifica la intención",
  query_agent: "Consulta el core bancario",
  knowledge_agent: "Busca en la base de conocimiento",
  complaint_agent: "Arma el borrador del reclamo",
  hitl_review: "Espera la decisión del operador",
  register_complaint: "Registra el reclamo aprobado",
  complaint_rejected: "Informa el rechazo del reclamo",
  card_action_agent: "Busca la tarjeta a bloquear",
  action_confirmation: "Espera la confirmación del cliente",
  execute_card_action: "Ejecuta el bloqueo y lo audita",
  action_cancelled: "Informa que no hubo cambios",
  blocked_response: "Responde con una plantilla segura",
  clarify: "Pide una aclaración",
  output_guardrail: "Verifica las cifras de la respuesta",
};

const STAGE_TEXT: Record<string, string> = {
  rules: "por reglas, sin modelo",
  lora: "con el modelo LoRA",
  llm: "con el LLM",
};

const BLOCK_REASONS: Record<string, string> = {
  prompt_injection: "parece un intento de cambiar las instrucciones del asistente (prompt injection)",
  acceso_no_autorizado: "pide datos de otra persona",
};

export type StepTone = "ok" | "warn" | "info";

export interface ExplainStep {
  layer: "Entrada" | "Router" | "Agente" | "Salida";
  text: string;
  tone: StepTone;
}

const pct = (value: number) => `${Math.round(value * 100)}%`;

export function explainTurn(r: ChatResponse): ExplainStep[] {
  const ran = new Set(r.metrics.nodes.map((n) => n.node));
  const steps: ExplainStep[] = [];
  const { reason, pii_found } = r.guardrail;

  // 1. Guardrail de entrada (en un turno reanudado con /confirm o /approve no vuelve a correr)
  if (ran.has("input_guardrail")) {
    if (reason && BLOCK_REASONS[reason]) {
      steps.push({
        layer: "Entrada",
        tone: "warn",
        text: `Bloqueó el mensaje porque ${BLOCK_REASONS[reason]}. Ningún agente ni modelo llegó a verlo.`,
      });
    } else if (pii_found.length > 0) {
      steps.push({
        layer: "Entrada",
        tone: "warn",
        text: `Detectó datos sensibles (${pii_found.join(", ")}) y los enmascaró antes de seguir: no quedan en el historial ni llegan a un modelo.`,
      });
    } else {
      steps.push({ layer: "Entrada", tone: "ok", text: "Revisó datos sensibles e intentos de inyección: sin hallazgos." });
    }
  }

  // 2. Router
  if (ran.has("router")) {
    const how = STAGE_TEXT[r.router_stage ?? ""] ?? "";
    const label = INTENT_LABELS[r.intent ?? ""] ?? r.intent ?? "";
    const confidence = r.intent_confidence != null ? ` (${pct(r.intent_confidence)})` : "";
    if (reason === "fuera_de_alcance") {
      steps.push({ layer: "Router", tone: "info", text: `Lo clasificó fuera de alcance${how ? `, ${how}` : ""}. Responde con una plantilla, sin gastar un modelo.` });
    } else if (r.status === "clarification") {
      steps.push({
        layer: "Router",
        tone: "info",
        text: `No tuvo confianza suficiente${confidence}${how ? `, ${how}` : ""}. En lugar de adivinar, pregunta.`,
      });
    } else if (label) {
      steps.push({ layer: "Router", tone: "ok", text: `Clasificó la intención como «${label}»${confidence}, ${how || "con el router"}.` });
    }
  }

  // 3. Agente: qué datos tocó y si el grafo quedó en pausa
  if (ran.has("query_agent")) {
    steps.push({ layer: "Agente", tone: "ok", text: "Consultó el core bancario solo con las cuentas del cliente del token de sesión." });
  }
  if (ran.has("knowledge_agent")) {
    const sources = r.citations.length > 0 ? ` y citó ${r.citations.join(", ")}` : "";
    steps.push({ layer: "Agente", tone: "ok", text: `Buscó en la base de conocimiento${sources}.` });
  }
  if (r.interrupt?.type === "complaint_review") {
    steps.push({ layer: "Agente", tone: "info", text: "Armó el borrador del reclamo y pausó el grafo: un ejecutivo debe aprobarlo en la pestaña Operador." });
  }
  if (r.interrupt?.type === "action_confirmation") {
    steps.push({ layer: "Agente", tone: "info", text: "Encontró la tarjeta y pausó el grafo: nada cambia hasta que usted confirme." });
  }
  if (ran.has("execute_card_action")) {
    steps.push({ layer: "Agente", tone: "ok", text: "Con su confirmación, bloqueó la tarjeta y dejó el registro en la auditoría." });
  }
  if (ran.has("action_cancelled")) {
    steps.push({ layer: "Agente", tone: "ok", text: "Usted canceló: no se ejecutó ningún cambio." });
  }
  if (ran.has("register_complaint")) {
    steps.push({ layer: "Agente", tone: "ok", text: "El ejecutivo aprobó el reclamo y quedó registrado con número de seguimiento." });
  }
  if (ran.has("complaint_rejected")) {
    steps.push({ layer: "Agente", tone: "info", text: "El ejecutivo rechazó el reclamo; no se registró." });
  }

  // 4. Guardrail de salida
  if (ran.has("output_guardrail")) {
    steps.push({ layer: "Salida", tone: "ok", text: "Comprobó que cada cifra de la respuesta exista en los datos o en las fuentes." });
  }
  if (r.degraded) {
    steps.push({ layer: "Salida", tone: "warn", text: "Un componente falló y se usó la ruta determinista: la respuesta es más simple, pero correcta." });
  }
  return steps;
}
