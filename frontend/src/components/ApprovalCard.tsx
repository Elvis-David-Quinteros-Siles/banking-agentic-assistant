// Tarjeta HITL: aparece cuando el grafo se interrumpe en hitl_review.
// En producción viviría en el back-office del operador; aquí se muestra junto al chat para la demo.

import { useState } from "react";
import type { ApproveRequest, ComplaintDraft, InterruptInfo } from "../api/types";

const CATEGORIES = ["cobro_indebido", "fraude", "atencion", "transferencia", "producto", "otro"];
const PRIORITIES: ComplaintDraft["prioridad"][] = ["alta", "media", "baja"];

interface Props {
  interrupt: Extract<InterruptInfo, { type: "complaint_review" }>;
  busy: boolean;
  onDecide: (request: ApproveRequest) => void;
}

export function ApprovalCard({ interrupt, busy, onDecide }: Props) {
  const draft = interrupt.payload;
  const [reviewer, setReviewer] = useState("OP-17");
  const [category, setCategory] = useState(draft.categoria);
  const [priority, setPriority] = useState(draft.prioridad);
  const [comment, setComment] = useState("");

  const edited = category !== draft.categoria || priority !== draft.prioridad;
  const amount =
    draft.monto != null ? `${draft.moneda === "USD" ? "USD" : "Bs"} ${draft.monto.toLocaleString("es-BO")}` : "—";

  const submit = (decision: ApproveRequest["decision"]) =>
    onDecide({
      decision,
      reviewer_id: reviewer.trim() || "OP-17",
      comment: comment.trim() || null,
      edited_draft: decision === "edit" ? { categoria: category, prioridad: priority } : null,
    });

  return (
    <section className="approval" aria-label="Revisión de reclamo">
      <header className="approval__header">
        <span className="approval__badge">Human-in-the-Loop</span>
        <h3>Reclamo pendiente de revisión</h3>
        <p>El grafo está pausado en <code>hitl_review</code>. Ningún reclamo se registra sin aprobación.</p>
      </header>

      <dl className="approval__fields">
        <dt>Cliente</dt>
        <dd>{draft.customer_id}</dd>
        <dt>Producto</dt>
        <dd>{draft.producto ?? "—"}</dd>
        <dt>Monto</dt>
        <dd>{amount}</dd>
        <dt>Resumen</dt>
        <dd>{draft.resumen}</dd>
      </dl>

      <div className="approval__form">
        <label>
          Categoría
          <select value={category} onChange={(e) => setCategory(e.target.value)} disabled={busy}>
            {CATEGORIES.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          Prioridad
          <select value={priority} onChange={(e) => setPriority(e.target.value as ComplaintDraft["prioridad"])} disabled={busy}>
            {PRIORITIES.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        </label>
        <label>
          Operador
          <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} disabled={busy} />
        </label>
        <label className="approval__comment">
          Comentario (se muestra al cliente si se rechaza)
          <input value={comment} onChange={(e) => setComment(e.target.value)} disabled={busy} placeholder="Opcional" />
        </label>
      </div>

      <div className="approval__actions">
        <button className="btn btn--primary" disabled={busy} onClick={() => submit(edited ? "edit" : "approve")}>
          {edited ? "Guardar cambios y aprobar" : "Aprobar"}
        </button>
        <button className="btn btn--danger" disabled={busy} onClick={() => submit("reject")}>
          Rechazar
        </button>
      </div>
    </section>
  );
}
