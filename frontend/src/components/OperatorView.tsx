// Back-office del operador: cola de reclamos pendientes, ordenada por prioridad y antigüedad.
// Cada decisión llama a POST /approve/{thread_id}, que reanuda el grafo pausado en hitl_review.

import { useCallback, useEffect, useState } from "react";
import { ApiRequestError, approve, getQueue } from "../api/client";
import type { ApproveRequest, QueueItem } from "../api/types";
import { ApprovalCard } from "./ApprovalCard";

const PRIORITY_LABEL: Record<QueueItem["priority"], string> = { alta: "▲ Alta", media: "● Media", baja: "▼ Baja" };

function formatAge(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${Math.round(minutes % 60)} min`;
}

export function OperatorView() {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const queue = await getQueue();
      setItems(queue.items);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "No se pudo cargar la cola del operador.");
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(load, 5000); // la cola se actualiza sola: los reclamos llegan desde el chat
    return () => clearInterval(id);
  }, [load]);

  const decide = async (item: QueueItem, request: ApproveRequest) => {
    setBusy(item.thread_id);
    try {
      const result = await approve(item.thread_id, request);
      setLastResult(`${item.customer_id}: ${result.answer}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="page">
      <header className="page__header">
        <div>
          <h2>Cola de reclamos</h2>
          <p className="muted">
            Ningún reclamo se registra sin revisión humana. El grafo de cada conversación está pausado hasta tu decisión.
          </p>
        </div>
        <span className="counter" aria-live="polite">
          {items === null ? "…" : `${items.length} pendiente${items.length === 1 ? "" : "s"}`}
        </span>
      </header>

      {error && <div className="error">{error}</div>}
      {lastResult && <div className="system-note system-note--block">Respuesta enviada al cliente · {lastResult}</div>}

      {items !== null && items.length === 0 && (
        <div className="empty empty--card">
          <h3>No hay reclamos pendientes</h3>
          <p className="muted">Cuando un cliente presente un reclamo en el chat, aparecerá aquí.</p>
        </div>
      )}

      <div className="queue">
        {items?.map((item) => (
          <article key={item.thread_id} className="queue__item">
            <div className="queue__meta">
              <span className={`priority priority--${item.priority}`}>{PRIORITY_LABEL[item.priority]}</span>
              <span>{item.customer_id}</span>
              <span className="muted">esperando {formatAge(item.age_minutes)}</span>
            </div>
            <ApprovalCard
              interrupt={{ type: "complaint_review", payload: item.draft }}
              busy={busy === item.thread_id}
              onDecide={(request) => void decide(item, request)}
            />
          </article>
        ))}
      </div>
    </section>
  );
}
