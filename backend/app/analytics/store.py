"""Almacén de analítica operativa: KPIs por caso de uso y cola del back-office HITL.

Qué se guarda y qué NO:
- Por turno: intención, confianza, estado, motivo de bloqueo, tipos de PII detectados, si hubo
  degradación, latencia, tokens, costo y latencia por nodo.
- NUNCA el texto del cliente ni la respuesta (pueden contener PII). El cliente se guarda
  seudonimizado (hash) en los turnos; la cola HITL sí guarda el customer_id porque el operador
  necesita saber de quién es el reclamo (y el borrador ya viene con la PII enmascarada).

SQLite aquí es deliberado: es analítica de una demo. En un banco esto iría a un data warehouse o a
Prometheus/Grafana, y la cola HITL a la base operativa del back-office.
"""

import hashlib
import json
import logging
import sqlite3
import statistics
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("analytics")

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,              -- ISO UTC
    kind          TEXT NOT NULL,              -- chat | approve
    thread_id     TEXT NOT NULL,
    customer_hash TEXT,
    intent        TEXT,
    confidence    REAL,
    status        TEXT NOT NULL,
    blocked_reason TEXT,
    pii_types     TEXT,                       -- "tarjeta,cedula"
    degraded      INTEGER NOT NULL DEFAULT 0,
    citations     INTEGER NOT NULL DEFAULT 0,
    latency_ms    REAL NOT NULL,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    nodes         TEXT,                       -- JSON {nodo: latencia_ms}
    source        TEXT NOT NULL DEFAULT 'live' -- live | demo
);
CREATE INDEX IF NOT EXISTS ix_turns_ts ON turns(ts);
CREATE TABLE IF NOT EXISTS hitl_queue (
    thread_id   TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    draft       TEXT NOT NULL,               -- JSON del borrador (PII ya enmascarada)
    priority    TEXT NOT NULL,
    status      TEXT NOT NULL,               -- pendiente | aprobado | editado | rechazado
    decided_at  TEXT,
    reviewer_id TEXT,
    comment     TEXT,
    source      TEXT NOT NULL DEFAULT 'live'
);
"""

PRIORITY_ORDER = {"alta": 0, "media": 1, "baja": 2}
DECISION_STATUS = {"approve": "aprobado", "edit": "editado", "reject": "rechazado"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def pseudonymize(customer_id: str | None) -> str | None:
    return hashlib.sha256(f"analytics:{customer_id}".encode()).hexdigest()[:12] if customer_id else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))], 1)


class AnalyticsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()  # SQLite + varios workers del servidor: serializamos escrituras
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- Escritura -----------------------------------------------------------
    def record_turn(self, response: dict, kind: str, customer_id: str | None, source: str = "live") -> None:
        metrics = response.get("metrics", {})
        guardrail = response.get("guardrail", {})
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO turns (ts, kind, thread_id, customer_hash, intent, confidence, status, blocked_reason, "
                "pii_types, degraded, citations, latency_ms, tokens_in, tokens_out, cost_usd, nodes, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now_iso(),
                    kind,
                    response["thread_id"],
                    pseudonymize(customer_id),
                    response.get("intent"),
                    response.get("intent_confidence"),
                    response["status"],
                    guardrail.get("reason") if guardrail.get("blocked") else None,
                    ",".join(guardrail.get("pii_found", [])) or None,
                    int(bool(response.get("degraded"))),
                    len(response.get("citations", [])),
                    metrics.get("latency_ms", 0),
                    metrics.get("tokens_in", 0),
                    metrics.get("tokens_out", 0),
                    metrics.get("cost_usd", 0.0),
                    json.dumps({n["node"]: n["latency_ms"] for n in metrics.get("nodes", [])}),
                    source,
                ),
            )

    def enqueue_complaint(self, thread_id: str, customer_id: str, draft: dict, source: str = "live") -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO hitl_queue (thread_id, customer_id, created_at, draft, priority, status, source) "
                "VALUES (?, ?, ?, ?, ?, 'pendiente', ?)",
                (
                    thread_id,
                    customer_id,
                    now_iso(),
                    json.dumps(draft, ensure_ascii=False),
                    draft.get("prioridad", "media"),
                    source,
                ),
            )

    def decide_complaint(self, thread_id: str, decision: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE hitl_queue SET status = ?, decided_at = ?, reviewer_id = ?, comment = ? WHERE thread_id = ?",
                (
                    DECISION_STATUS.get(decision["decision"], decision["decision"]),
                    now_iso(),
                    decision.get("reviewer_id"),
                    decision.get("comment"),
                    thread_id,
                ),
            )

    def customer_for(self, thread_id: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT customer_id FROM hitl_queue WHERE thread_id = ?", (thread_id,)).fetchone()
        return row["customer_id"] if row else None

    # --- Lectura -------------------------------------------------------------
    def pending_queue(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM hitl_queue WHERE status = 'pendiente'").fetchall()
        now = datetime.now(UTC)
        items = [
            {
                "thread_id": r["thread_id"],
                "customer_id": r["customer_id"],
                "created_at": r["created_at"],
                "age_minutes": round((now - datetime.fromisoformat(r["created_at"])).total_seconds() / 60, 1),
                "priority": r["priority"],
                "draft": json.loads(r["draft"]),
            }
            for r in rows
        ]
        # Primero lo urgente; dentro de la misma prioridad, lo que más espera
        return sorted(items, key=lambda i: (PRIORITY_ORDER.get(i["priority"], 9), -i["age_minutes"]))

    def summary(self, hours: int = 24) -> dict:
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
        with self._conn() as conn:
            turns = [dict(r) for r in conn.execute("SELECT * FROM turns WHERE ts >= ? AND kind = 'chat'", (since,))]
            queue = [dict(r) for r in conn.execute("SELECT * FROM hitl_queue WHERE created_at >= ?", (since,))]

        def rate(part: int, whole: int) -> float | None:
            return round(part / whole, 3) if whole else None

        total = len(turns)
        latencies = [t["latency_ms"] for t in turns]
        legit = [t for t in turns if t["status"] != "blocked"]
        resolved_by_agent = [t for t in legit if t["status"] == "completed"]

        by_intent = []
        for intent in (
            "consulta_cuenta",
            "conocimiento_producto",
            "queja_disputa",
            "fuera_de_alcance",
            "ambiguo",
            None,
        ):
            group = [t for t in turns if t["intent"] == intent]
            if not group:
                continue
            by_intent.append(
                {
                    "intent": intent or "bloqueado_antes_del_router",
                    "turns": len(group),
                    "completed_rate": rate(sum(t["status"] == "completed" for t in group), len(group)),
                    "hitl_rate": rate(sum(t["status"] == "interrupted" for t in group), len(group)),
                    "clarification_rate": rate(sum(t["status"] == "clarification" for t in group), len(group)),
                    "blocked_rate": rate(sum(t["status"] == "blocked" for t in group), len(group)),
                    "degraded_rate": rate(sum(t["degraded"] for t in group), len(group)),
                    "latency_p95_ms": percentile([t["latency_ms"] for t in group], 0.95),
                    "avg_cost_usd": round(statistics.mean(t["cost_usd"] for t in group), 6),
                }
            )

        hourly: dict[str, dict] = {}
        for t in turns:
            bucket = t["ts"][:13] + ":00"
            h = hourly.setdefault(bucket, {"hour": bucket, "turns": 0, "latencies": []})
            h["turns"] += 1
            h["latencies"].append(t["latency_ms"])
        series = [
            {"hour": h["hour"], "turns": h["turns"], "latency_p95_ms": percentile(h["latencies"], 0.95)}
            for h in sorted(hourly.values(), key=lambda x: x["hour"])
        ]

        decided = [q for q in queue if q["status"] != "pendiente"]
        pending = [q for q in queue if q["status"] == "pendiente"]
        decision_minutes = [
            (datetime.fromisoformat(q["decided_at"]) - datetime.fromisoformat(q["created_at"])).total_seconds() / 60
            for q in decided
            if q["decided_at"]
        ]
        now = datetime.now(UTC)
        blocked_reasons: dict[str, int] = {}
        pii_counts: dict[str, int] = {}
        for t in turns:
            if t["blocked_reason"]:
                blocked_reasons[t["blocked_reason"]] = blocked_reasons.get(t["blocked_reason"], 0) + 1
            for kind in (t["pii_types"] or "").split(","):
                if kind:
                    pii_counts[kind] = pii_counts.get(kind, 0) + 1

        return {
            "window_hours": hours,
            "generated_at": now_iso(),
            "kpis": {
                "turns": total,
                # Contención: pedidos legítimos resueltos por el agente sin intervención humana
                "containment_rate": rate(len(resolved_by_agent), len(legit)),
                "latency_p50_ms": percentile(latencies, 0.5),
                "latency_p95_ms": percentile(latencies, 0.95),
                "degraded_rate": rate(sum(t["degraded"] for t in turns), total),
                "blocked_rate": rate(sum(t["status"] == "blocked" for t in turns), total),
                "clarification_rate": rate(sum(t["status"] == "clarification" for t in turns), total),
                "cost_usd": round(sum(t["cost_usd"] for t in turns), 6),
                "tokens": sum(t["tokens_in"] + t["tokens_out"] for t in turns),
            },
            "hitl": {
                "created": len(queue),
                "pending": len(pending),
                "approved": sum(q["status"] == "aprobado" for q in decided),
                "edited": sum(q["status"] == "editado" for q in decided),
                "rejected": sum(q["status"] == "rechazado" for q in decided),
                # Calidad del borrador del agente: aprobado tal cual, sin que el operador lo corrija
                "approved_without_edit_rate": rate(sum(q["status"] == "aprobado" for q in decided), len(decided)),
                "avg_decision_minutes": round(statistics.mean(decision_minutes), 1) if decision_minutes else None,
                "oldest_pending_minutes": round(
                    max((now - datetime.fromisoformat(q["created_at"])).total_seconds() / 60 for q in pending), 1
                )
                if pending
                else None,
            },
            "by_intent": by_intent,
            "hourly": series,
            "safety": {"blocked_reasons": blocked_reasons, "pii_detected": pii_counts},
        }

    def is_empty(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0


@lru_cache
def get_analytics() -> AnalyticsStore:
    from app.config import get_settings

    return AnalyticsStore(get_settings().analytics_path)


def safe(fn, *args, **kwargs) -> None:
    """La analítica nunca debe romper una conversación: cualquier error solo se registra."""
    try:
        fn(*args, **kwargs)
    except Exception as error:  # noqa: BLE001
        logger.warning("No se pudo registrar analítica: %s", error)
