// Dashboard de KPIs por caso de uso (GET /metrics/summary).
// Formas según la guía de dataviz: fila de stat tiles para los titulares, barras horizontales
// para el volumen por intención, columnas para la tendencia, y tablas para las métricas
// multi-columna (que además son la vista accesible de todo lo que muestran los gráficos).

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiRequestError, getSummary } from "../api/client";
import type { MetricsSummary } from "../api/types";
import { INTENT_LABELS } from "../labels";

const WINDOWS = [
  { hours: 24, label: "Últimas 24 h" },
  { hours: 24 * 7, label: "Últimos 7 días" },
];

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const ms = (v: number | null | undefined) => (v == null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);
const minutes = (v: number | null | undefined) => (v == null ? "—" : v >= 60 ? `${(v / 60).toFixed(1)} h` : `${Math.round(v)} min`);

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="tile">
      <span className="tile__label">{label}</span>
      <span className="tile__value">{value}</span>
      {hint && <span className="tile__hint">{hint}</span>}
    </div>
  );
}

// Tooltip compartido por los gráficos (texto renderizado por React: nunca innerHTML)
interface Tip {
  x: number;
  y: number;
  value: string;
  label: string;
}

function useTooltip() {
  const [tip, setTip] = useState<Tip | null>(null);
  const show = (event: React.PointerEvent | React.FocusEvent, value: string, label: string) => {
    const target = event.currentTarget as HTMLElement;
    const box = target.getBoundingClientRect();
    const host = target.closest(".viz-card")!.getBoundingClientRect();
    setTip({ x: box.left - host.left + box.width / 2, y: box.top - host.top, value, label });
  };
  const node = tip && (
    <div className="viz-tip" style={{ left: tip.x, top: tip.y }} role="tooltip">
      <strong>{tip.value}</strong>
      <span>{tip.label}</span>
    </div>
  );
  return { show, hide: () => setTip(null), node };
}

function IntentBars({ data }: { data: MetricsSummary["by_intent"] }) {
  const { show, hide, node } = useTooltip();
  const max = Math.max(1, ...data.map((d) => d.turns));
  return (
    <div className="viz-card">
      <h3>Conversaciones por intención</h3>
      <div className="hbars">
        {data.map((d) => (
          <div key={d.intent} className="hbars__row">
            <span className="hbars__label">{INTENT_LABELS[d.intent] ?? d.intent}</span>
            <div className="hbars__track">
              <div
                className="hbars__bar"
                style={{ width: `${(d.turns / max) * 100}%` }}
                tabIndex={0}
                aria-label={`${INTENT_LABELS[d.intent] ?? d.intent}: ${d.turns} conversaciones`}
                onPointerMove={(e) => show(e, `${d.turns} conversaciones`, `${INTENT_LABELS[d.intent] ?? d.intent} · resueltas ${pct(d.completed_rate)}`)}
                onFocus={(e) => show(e, `${d.turns} conversaciones`, INTENT_LABELS[d.intent] ?? d.intent)}
                onPointerLeave={hide}
                onBlur={hide}
              />
              <span className="hbars__value">{d.turns}</span>
            </div>
          </div>
        ))}
      </div>
      {node}
    </div>
  );
}

function niceMax(value: number): number {
  if (value <= 5) return 5;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / magnitude) * magnitude;
}

function TrendColumns({ hourly, windowHours }: { hourly: MetricsSummary["hourly"]; windowHours: number }) {
  const { show, hide, node } = useTooltip();
  const byDay = windowHours > 48;
  // Buckets completos (incluye horas/días sin tráfico) para que el eje de tiempo sea honesto
  const buckets = useMemo(() => {
    const count = byDay ? Math.ceil(windowHours / 24) : windowHours;
    const now = new Date();
    const result: { key: string; label: string; turns: number }[] = [];
    for (let i = count - 1; i >= 0; i--) {
      const d = new Date(now);
      if (byDay) d.setUTCDate(d.getUTCDate() - i);
      else d.setUTCHours(d.getUTCHours() - i);
      const key = byDay ? d.toISOString().slice(0, 10) : d.toISOString().slice(0, 13);
      const label = byDay
        ? d.toLocaleDateString("es-BO", { weekday: "short", day: "numeric" })
        : d.toLocaleTimeString("es-BO", { hour: "2-digit", minute: "2-digit" });
      result.push({ key, label, turns: 0 });
    }
    for (const h of hourly) {
      const key = byDay ? h.hour.slice(0, 10) : h.hour.slice(0, 13);
      const bucket = result.find((b) => b.key === key);
      if (bucket) bucket.turns += h.turns;
    }
    return result;
  }, [hourly, windowHours, byDay]);

  const top = niceMax(Math.max(...buckets.map((b) => b.turns), 1));
  const ticks = [0, top / 2, top];
  const labelEvery = byDay ? 1 : 6;

  return (
    <div className="viz-card">
      <h3>Conversaciones por {byDay ? "día" : "hora"}</h3>
      <div className="cols">
        <div className="cols__axis" aria-hidden>
          {ticks.slice().reverse().map((t) => (
            <span key={t}>{t}</span>
          ))}
        </div>
        <div className="cols__plot">
          {ticks.map((t) => (
            // La grilla vive en la zona de barras: 20px reservados abajo para las etiquetas del eje x
            <div key={t} className="cols__grid" style={{ bottom: `calc(20px + (100% - 20px) * ${t / top})` }} />
          ))}
          <div className="cols__bars">
            {buckets.map((b) => (
              <div key={b.key} className="cols__slot">
                <div
                  className="cols__bar"
                  style={{ height: `${(b.turns / top) * 100}%` }}
                  tabIndex={b.turns ? 0 : -1}
                  aria-label={`${b.label}: ${b.turns} conversaciones`}
                  onPointerMove={(e) => show(e, `${b.turns} conversaciones`, b.label)}
                  onFocus={(e) => show(e, `${b.turns} conversaciones`, b.label)}
                  onPointerLeave={hide}
                  onBlur={hide}
                />
              </div>
            ))}
          </div>
          <div className="cols__xlabels" aria-hidden>
            {buckets.map((b, i) => (
              <span key={b.key}>{i % labelEvery === 0 ? b.label : ""}</span>
            ))}
          </div>
        </div>
      </div>
      {node}
    </div>
  );
}

export function KpiView() {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState<MetricsSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getSummary(hours));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "No se pudieron cargar los KPIs.");
    } finally {
      setLoading(false);
    }
  }, [hours]);

  useEffect(() => {
    void load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  const k = data?.kpis;
  const h = data?.hitl;
  return (
    <section className="page viz-root">
      <header className="page__header">
        <div>
          <h2>KPIs por caso de uso</h2>
          <p className="muted">Calculados en vivo a partir de cada turno. No se guarda el texto de los clientes.</p>
        </div>
        <div className="filters" role="group" aria-label="Ventana de tiempo">
          {WINDOWS.map((w) => (
            <button
              key={w.hours}
              className={`chip ${hours === w.hours ? "chip--active" : ""}`}
              aria-pressed={hours === w.hours}
              onClick={() => setHours(w.hours)}
            >
              {w.label}
            </button>
          ))}
        </div>
      </header>

      {error && <div className="error">{error}</div>}

      {data && k && h && (
        <div className={`dashboard ${loading ? "dashboard--loading" : ""}`}>
          <div className="tiles">
            <StatTile label="Conversaciones" value={k.turns.toLocaleString("es-BO")} />
            <StatTile label="Contención" value={pct(k.containment_rate)} hint="resueltas por el agente sin humano" />
            <StatTile label="Latencia p95" value={ms(k.latency_p95_ms)} hint={`p50 ${ms(k.latency_p50_ms)}`} />
            <StatTile label="Modo degradado" value={pct(k.degraded_rate)} hint="fallback determinista" />
            <StatTile label="Bloqueadas" value={pct(k.blocked_rate)} hint="guardrails" />
            <StatTile label="Costo" value={`US$ ${k.cost_usd.toFixed(4)}`} hint={`${k.tokens.toLocaleString("es-BO")} tokens`} />
          </div>

          <h3 className="section-title">Reclamos (Human-in-the-Loop)</h3>
          <div className="tiles">
            <StatTile label="Pendientes" value={String(h.pending)} hint={h.oldest_pending_minutes != null ? `el más antiguo: ${minutes(h.oldest_pending_minutes)}` : "cola vacía"} />
            <StatTile label="Aprobados sin editar" value={pct(h.approved_without_edit_rate)} hint="calidad del borrador del agente" />
            <StatTile label="Tiempo de decisión" value={minutes(h.avg_decision_minutes)} hint="promedio del operador" />
            <StatTile label="Decisiones" value={`${h.approved + h.edited + h.rejected}`} hint={`${h.approved} aprob. · ${h.edited} edit. · ${h.rejected} rech.`} />
          </div>

          <div className="charts">
            <IntentBars data={data.by_intent} />
            <TrendColumns hourly={data.hourly} windowHours={data.window_hours} />
          </div>

          <div className="viz-card">
            <h3>Detalle por caso de uso</h3>
            <div className="table-wrap">
              <table className="kpi-table">
                <thead>
                  <tr>
                    <th scope="col">Intención</th>
                    <th scope="col">Conversaciones</th>
                    <th scope="col">Resueltas</th>
                    <th scope="col">A HITL</th>
                    <th scope="col">Aclaración</th>
                    <th scope="col">Bloqueadas</th>
                    <th scope="col">Degradadas</th>
                    <th scope="col">Latencia p95</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_intent.map((row) => (
                    <tr key={row.intent}>
                      <th scope="row">{INTENT_LABELS[row.intent] ?? row.intent}</th>
                      <td>{row.turns}</td>
                      <td>{pct(row.completed_rate)}</td>
                      <td>{pct(row.hitl_rate)}</td>
                      <td>{pct(row.clarification_rate)}</td>
                      <td>{pct(row.blocked_rate)}</td>
                      <td>{pct(row.degraded_rate)}</td>
                      <td>{ms(row.latency_p95_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="charts">
            <div className="viz-card">
              <h3>Bloqueos por motivo</h3>
              <SimpleTable rows={data.safety.blocked_reasons} empty="Sin bloqueos en la ventana" />
            </div>
            <div className="viz-card">
              <h3>PII detectada y enmascarada</h3>
              <SimpleTable rows={data.safety.pii_detected} empty="Sin PII en la ventana" />
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

const REASON_LABELS: Record<string, string> = {
  prompt_injection: "Prompt injection",
  acceso_no_autorizado: "Acceso a datos ajenos",
  fuera_de_alcance: "Fuera de alcance",
  tarjeta: "Tarjeta",
  cedula: "Cédula de identidad",
  telefono: "Celular",
  email: "Email",
};

function SimpleTable({ rows, empty }: { rows: Record<string, number>; empty: string }) {
  const entries = Object.entries(rows).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return <p className="muted">{empty}</p>;
  return (
    <table className="kpi-table">
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <th scope="row">{REASON_LABELS[key] ?? key}</th>
            <td>{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
