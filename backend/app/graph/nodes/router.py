"""Nodo router: clasifica la intención y extrae entidades.

Backends (ROUTER_BACKEND):
- rules: baseline por palabras clave ponderadas. Fallback y piso de comparación en Evals.
- llm:   LLM base con salida estructurada (Pydantic). Si falla, cae a rules.
- lora:  modelo pequeño fine-tuneado (Fase 4).
"""

import re
from typing import Literal

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, Field

from app.config import get_settings
from app.graph.nodes.common import last_user_text, log_fallback
from app.graph.state import AgentState
from app.llm.pricing import usage_from_message
from app.llm.provider import get_chat_model, model_name
from app.observability.tracing import traced
from app.text import normalize

# ---------------------------------------------------------------------------
# Backend "rules"
# ---------------------------------------------------------------------------
# (palabra clave sin tildes, peso). Las palabras de queja pesan más: ante la duda entre
# "pregunta sobre tarjeta" y "reclamo sobre tarjeta", conviene no perder un reclamo.
KEYWORDS: dict[str, tuple[tuple[str, float], ...]] = {
    "consulta_cuenta": (
        ("saldo", 2), ("saldos", 2), ("movimiento", 2), ("movimientos", 2), ("extracto", 2),
        ("cuanto tengo", 2), ("mis cuentas", 1.5), ("mi cuenta", 1), ("ultimas transacciones", 2),
        ("transacciones", 1.5), ("cuanto dinero", 2), ("depositaron", 1.5), ("gaste", 1.5),
    ),
    "conocimiento_producto": (
        ("requisito", 2), ("requisitos", 2), ("comision", 1.5), ("comisiones", 1.5), ("tasa", 1.5),
        ("interes", 1.5), ("tarjeta", 0.5), ("credito", 0.5), ("prestamo", 1), ("dpf", 2),
        ("plazo fijo", 2), ("abrir", 1.5), ("abro", 1.5), ("limite", 1.5), ("limites", 1.5),
        ("politica", 1.5), ("producto", 1), ("productos", 1), ("cuanto cuesta", 1.5), ("como funciona", 1),
        ("vivienda", 1), ("pyme", 1), ("qr", 0.5), ("chequera", 1.5), ("bloquear", 1.5), ("bloqueo", 1.5),
        ("transferencias", 0.5), ("pago minimo", 2), ("que necesito", 1.5),
    ),
    "queja_disputa": (
        ("queja", 3), ("reclamo", 3), ("reclamar", 3), ("disputa", 3), ("no reconozco", 3),
        ("cobro indebido", 3), ("doble cobro", 3), ("cobraron dos veces", 3), ("me cobraron", 2),
        ("fraude", 3), ("mal atendido", 3), ("pesimo", 2), ("molesto", 1.5), ("indignado", 2),
        ("no autorice", 3), ("estafa", 3), ("denunciar", 2),
    ),
}
# Señales explícitas de fuera de alcance (sin estas, un texto sin palabras clave es "ambiguo")
OUT_OF_SCOPE = (
    "futbol", "partido", "receta", "clima", "pelicula", "chiste", "elecciones", "presidente",
    "tarea", "poema", "codigo python", "programar", "horoscopo", "bitcoin", "cripto", "apuesta",
    "capital de", "quien gano", "musica", "cancion",
)


def _matches(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def classify_rules(text: str) -> tuple[str, float]:
    """Devuelve (intención, confianza). "ambiguo" con confianza baja dispara una aclaración."""
    normalized = normalize(text)
    scores = {
        intent: sum(weight for phrase, weight in keywords if _matches(normalized, phrase))
        for intent, keywords in KEYWORDS.items()
    }
    total = sum(scores.values())
    if total == 0:
        if any(_matches(normalized, p) for p in OUT_OF_SCOPE):
            return "fuera_de_alcance", 0.8
        return "ambiguo", 0.3
    best = max(scores, key=scores.get)
    return best, round(scores[best] / total, 2)


# Mantener el nombre usado en la Fase 1
classify = classify_rules

# Monto con moneda explícita antes ("Bs 350", "$us 20") o después ("350 bolivianos")
AMOUNT_RE = re.compile(
    r"(?P<pre>bs\.?|bob|usd|\$us|\$)\s*(?P<num>\d[\d.,]*\d|\d)"
    r"|(?P<num2>\d[\d.,]*\d|\d)\s*(?P<suf>bs\b|bolivianos|bob|usd|dolares|\$us)"
)
PRODUCT_HINTS = {
    "tarjeta de credito": "tarjeta_credito", "tarjeta de debito": "tarjeta_debito", "tarjeta": "tarjeta",
    "caja de ahorro": "caja_ahorro", "cuenta corriente": "cuenta_corriente", "dpf": "dpf",
    "plazo fijo": "dpf", "vivienda": "credito_vivienda", "pyme": "credito_pyme", "qr": "qr",
    "banca por internet": "banca_internet", "app": "banca_internet", "transferencia": "transferencia",
}


def parse_amount(raw: str) -> float | None:
    """'1.250,50' -> 1250.5 ; '350' -> 350.0 (formato boliviano: punto miles, coma decimales)."""
    if re.search(r",\d{1,2}$", raw):
        raw = raw.replace(".", "").replace(",", ".")
    elif re.search(r"\.\d{3}($|\D)", raw):
        raw = raw.replace(".", "")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def extract_entities(text: str) -> dict:
    """Extracción determinista de producto, monto y moneda."""
    normalized = normalize(text)
    entities: dict = {}
    for hint, product in PRODUCT_HINTS.items():
        if _matches(normalized, hint):
            entities["producto"] = product
            break
    match = AMOUNT_RE.search(normalized)
    if match:
        amount = parse_amount(match.group("num") or match.group("num2"))
        currency_token = match.group("pre") or match.group("suf") or ""
        if amount:
            entities["monto"] = amount
            entities["moneda"] = "USD" if re.search(r"usd|dolar|\$", currency_token) else "BOB"
    if "moneda" not in entities and re.search(r"\b(dolares|usd)\b", normalized):
        entities["moneda"] = "USD"
    return entities


# ---------------------------------------------------------------------------
# Backend "llm"
# ---------------------------------------------------------------------------
class RouterOutput(BaseModel):
    intent: Literal["consulta_cuenta", "conocimiento_producto", "queja_disputa", "fuera_de_alcance", "ambiguo"]
    confidence: float = Field(ge=0, le=1)
    producto: str | None = None
    monto: float | None = None
    moneda: Literal["BOB", "USD"] | None = None


ROUTER_PROMPT = """Eres el clasificador de intenciones de un asistente bancario. Clasifica el ÚLTIMO mensaje del cliente:
- consulta_cuenta: saldos, movimientos, extractos de SUS cuentas.
- conocimiento_producto: requisitos, comisiones, tasas, límites, cómo funciona un producto o canal.
- queja_disputa: reclamos, cobros no reconocidos, doble cobro, fraude, mala atención.
- fuera_de_alcance: cualquier tema no bancario.
- ambiguo: no se puede saber qué quiere (por ejemplo, solo un saludo).
Extrae producto, monto y moneda (BOB o USD) si aparecen. confidence entre 0 y 1."""


def classify_llm(text: str) -> tuple[RouterOutput, dict]:
    llm = get_chat_model()
    structured = llm.with_structured_output(RouterOutput, include_raw=True)
    result = structured.invoke([SystemMessage(content=ROUTER_PROMPT), ("human", text)])
    if result.get("parsing_error") or result.get("parsed") is None:
        raise ValueError(f"salida no parseable: {result.get('parsing_error')}")
    return result["parsed"], usage_from_message(result["raw"], model_name())


@traced("router")
def router_node(state: AgentState) -> dict:
    settings = get_settings()
    text = last_user_text(state)
    entities = extract_entities(text)

    if settings.router_backend == "llm" and get_chat_model() is not None:
        try:
            output, usage = classify_llm(text)
            llm_entities = {k: v for k, v in output.model_dump(include={"producto", "monto", "moneda"}).items() if v}
            # Las entidades deterministas (regex) prevalecen: son más fiables que un modelo pequeño
            return {
                "intent": output.intent,
                "intent_confidence": round(output.confidence, 2),
                "entities": {**llm_entities, **entities},
                "_usage": usage,
            }
        except Exception as error:  # noqa: BLE001 - cualquier fallo del LLM degrada a reglas
            log_fallback("router", error)
            intent, confidence = classify_rules(text)
            return {"intent": intent, "intent_confidence": confidence, "entities": entities, "degraded": True}

    intent, confidence = classify_rules(text)
    return {"intent": intent, "intent_confidence": confidence, "entities": entities}


def route_after_router(state: AgentState) -> str:
    intent = state.get("intent")
    if intent == "fuera_de_alcance":
        return "blocked_response"
    if intent == "ambiguo" or (state.get("intent_confidence") or 0) < get_settings().router_min_confidence:
        return "clarify"
    return {
        "consulta_cuenta": "query_agent",
        "conocimiento_producto": "knowledge_agent",
        "queja_disputa": "complaint_agent",
    }[intent]
