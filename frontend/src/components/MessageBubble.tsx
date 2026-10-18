import type { Message } from "../hooks/useChat";

const INTENT_LABELS: Record<string, string> = {
  consulta_cuenta: "Consulta de cuenta",
  conocimiento_producto: "Productos y políticas",
  queja_disputa: "Reclamo",
  fuera_de_alcance: "Fuera de alcance",
  ambiguo: "Ambiguo",
};

const STATUS_LABELS: Record<string, string> = {
  blocked: "Bloqueado por guardrail",
  interrupted: "Pendiente de aprobación",
  clarification: "Aclaración",
};

export function MessageBubble({ message }: { message: Message }) {
  if (message.role === "system") {
    return <div className="system-note">{message.text}</div>;
  }

  const r = message.response;
  return (
    <div className={`bubble bubble--${message.role}`}>
      <div className="bubble__text">
        {message.text}
        {message.streaming && <span className="cursor" aria-hidden />}
      </div>

      {r && (
        <div className="bubble__meta">
          {r.intent && (
            <span className="tag" title="Intención detectada por el router">
              {INTENT_LABELS[r.intent] ?? r.intent}
              {r.intent_confidence != null && ` · ${Math.round(r.intent_confidence * 100)}%`}
            </span>
          )}
          {STATUS_LABELS[r.status] && <span className={`tag tag--${r.status}`}>{STATUS_LABELS[r.status]}</span>}
          {r.guardrail.pii_found.length > 0 && (
            <span className="tag tag--warn" title="Datos enmascarados antes de llegar al LLM">
              PII enmascarada: {r.guardrail.pii_found.join(", ")}
            </span>
          )}
          {r.degraded && (
            <span className="tag tag--warn" title="Un nodo usó su ruta determinista por fallo del LLM o de Neo4j">
              Modo degradado
            </span>
          )}
          {r.citations.map((c) => (
            <span key={c} className="tag tag--cite" title="Fuente en el grafo de conocimiento">
              {c}
            </span>
          ))}
          <span className="tag tag--muted">{Math.round(r.metrics.latency_ms)} ms</span>
        </div>
      )}
    </div>
  );
}
