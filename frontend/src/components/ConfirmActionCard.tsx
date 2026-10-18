// Confirmación del CLIENTE: aparece cuando el grafo se pausa en action_confirmation (bloqueo de tarjeta).
// A diferencia del HITL de reclamos, decide el dueño de la cuenta; nada se ejecuta sin su "sí".

import type { CardAction } from "../api/types";

interface Props {
  action: CardAction;
  busy: boolean;
  onConfirm: (confirm: boolean) => void;
}

export function ConfirmActionCard({ action, busy, onConfirm }: Props) {
  return (
    <section className="approval approval--action" aria-label="Confirmación de acción">
      <header className="approval__header">
        <span className="approval__badge">Requiere su confirmación</span>
        <h3>Bloquear {action.tarjeta}</h3>
        <p>
          El grafo está pausado en <code>action_confirmation</code>. La tarjeta no se bloquea hasta que usted confirme.
        </p>
      </header>

      <dl className="approval__fields">
        <dt>Tarjeta</dt>
        <dd>{action.tarjeta}</dd>
        <dt>Motivo</dt>
        <dd>{action.motivo}</dd>
      </dl>

      <div className="approval__actions">
        <button className="btn btn--danger" disabled={busy} onClick={() => onConfirm(true)}>
          Sí, bloquear
        </button>
        <button className="btn" disabled={busy} onClick={() => onConfirm(false)}>
          Cancelar
        </button>
      </div>
    </section>
  );
}
