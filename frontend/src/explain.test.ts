import { describe, expect, it } from "vitest";
import type { ChatResponse } from "./api/types";
import { explainTurn } from "./explain";

function response(overrides: Partial<ChatResponse>, nodes: string[]): ChatResponse {
  return {
    thread_id: "t-1",
    status: "completed",
    answer: "",
    intent: null,
    intent_confidence: null,
    router_stage: null,
    citations: [],
    interrupt: null,
    guardrail: { blocked: false, reason: null, pii_found: [] },
    degraded: false,
    metrics: {
      latency_ms: 10,
      tokens_in: 0,
      tokens_out: 0,
      cost_usd: 0,
      nodes: nodes.map((node) => ({ node, latency_ms: 1, tokens_in: 0, tokens_out: 0, cost_usd: 0 })),
    },
    ...overrides,
  };
}

const layers = (r: ChatResponse) => explainTurn(r).map((s) => s.layer);

describe("explainTurn", () => {
  it("una consulta recorre entrada, router, agente y salida", () => {
    const r = response(
      { intent: "consulta_cuenta", intent_confidence: 1, router_stage: "rules" },
      ["input_guardrail", "router", "query_agent", "output_guardrail"],
    );
    expect(layers(r)).toEqual(["Entrada", "Router", "Agente", "Salida"]);
    expect(explainTurn(r)[1].text).toContain("«Consulta de cuenta» (100%), por reglas");
  });

  it("un bloqueo en la entrada explica el motivo y no menciona agentes", () => {
    const r = response(
      { status: "blocked", guardrail: { blocked: true, reason: "prompt_injection", pii_found: [] } },
      ["input_guardrail", "blocked_response"],
    );
    const steps = explainTurn(r);
    expect(steps).toHaveLength(1);
    expect(steps[0].tone).toBe("warn");
    expect(steps[0].text).toContain("prompt injection");
  });

  it("la PII enmascarada se informa sin repetir el dato", () => {
    const r = response(
      { intent: "conocimiento_producto", intent_confidence: 1, router_stage: "rules", citations: ["POL-003"],
        guardrail: { blocked: false, reason: null, pii_found: ["tarjeta"] } },
      ["input_guardrail", "router", "knowledge_agent", "output_guardrail"],
    );
    const steps = explainTurn(r);
    expect(steps[0].text).toContain("(tarjeta)");
    expect(steps[2].text).toContain("POL-003");
  });

  it("las pausas del grafo dicen quién debe decidir", () => {
    const complaint = response(
      { status: "interrupted", intent: "queja_disputa", intent_confidence: 0.8, router_stage: "rules",
        interrupt: { type: "complaint_review", payload: { categoria: "cobro_indebido", producto: null, monto: 350, moneda: "BOB", prioridad: "alta", resumen: "", customer_id: "CLI-001" } } },
      ["input_guardrail", "router", "complaint_agent"],
    );
    expect(explainTurn(complaint).at(-1)?.text).toContain("pestaña Operador");

    const card = response(
      { status: "interrupted", intent: "accion_tarjeta", intent_confidence: 0.86, router_stage: "rules",
        interrupt: { type: "action_confirmation", payload: { accion: "bloquear_tarjeta", tarjeta: "débito ****4821", last4: "4821", motivo: "robo" } } },
      ["input_guardrail", "router", "card_action_agent"],
    );
    expect(explainTurn(card).at(-1)?.text).toContain("hasta que usted confirme");
  });

  it("una aclaración explica que no adivina", () => {
    const r = response(
      { status: "clarification", intent: "ambiguo", intent_confidence: 0.3, router_stage: "rules" },
      ["input_guardrail", "router", "clarify"],
    );
    expect(explainTurn(r)[1].text).toContain("En lugar de adivinar");
  });
});
