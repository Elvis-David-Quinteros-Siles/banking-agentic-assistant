// Panel lateral: recorrido del grafo en vivo, métricas del último turno y salud del backend.

import type { ChatResponse, HealthResponse } from "../api/types";
import type { NodeProgress } from "../hooks/useChat";

interface Props {
  nodes: NodeProgress[];
  last: ChatResponse | null;
  health: HealthResponse | null;
  threadId: string | null;
}

export function TracePanel({ nodes, last, health, threadId }: Props) {
  return (
    <aside className="trace">
      <section>
        <h3>Recorrido del grafo</h3>
        {nodes.length === 0 ? (
          <p className="muted">Envía un mensaje para ver qué nodos se ejecutan.</p>
        ) : (
          <ol className="timeline">
            {nodes.map((n, i) => (
              <li key={`${n.node}-${i}`} className={`timeline__item timeline__item--${n.status}`}>
                <span className="timeline__dot" />
                <code>{n.node}</code>
                <span className="muted">{n.status === "running" ? "…" : `${n.latency_ms ?? 0} ms`}</span>
              </li>
            ))}
          </ol>
        )}
      </section>

      {last && (
        <section>
          <h3>Último turno</h3>
          <dl className="kv">
            <dt>Latencia</dt>
            <dd>{Math.round(last.metrics.latency_ms)} ms</dd>
            <dt>Tokens</dt>
            <dd>
              {last.metrics.tokens_in} in / {last.metrics.tokens_out} out
            </dd>
            <dt>Costo</dt>
            <dd>US$ {last.metrics.cost_usd.toFixed(6)}</dd>
            <dt>Estado</dt>
            <dd>{last.status}</dd>
          </dl>
        </section>
      )}

      <section>
        <h3>Backend</h3>
        {health ? (
          <dl className="kv">
            <dt>Estado</dt>
            <dd className={`health health--${health.status}`}>{health.status}</dd>
            {Object.entries(health.components).map(([k, v]) => (
              <KeyValue key={k} label={k} value={v} />
            ))}
          </dl>
        ) : (
          <p className="muted">Sin conexión con /health</p>
        )}
        {threadId && (
          <p className="muted thread">
            thread_id <code>{threadId.slice(0, 8)}…</code>
          </p>
        )}
      </section>
    </aside>
  );
}

function KeyValue({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}
