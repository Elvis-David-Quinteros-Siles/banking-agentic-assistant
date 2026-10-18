"""Formato de la tarea acotada del modelo LoRA: clasificación de intención + entidades en JSON.

Este módulo es la ÚNICA fuente de verdad del formato. Lo usan:
- finetune/generate_dataset.py y finetune/train_lora.py (entrenamiento),
- app/llm/lora_router.py (inferencia en producción).
Si el formato cambia en un lado y no en el otro, el modelo falla en silencio: por eso se comparte.

No importa torch: el backend puede cargarlo aunque el router LoRA esté desactivado.
"""

import json
from typing import Any

INTENT_LABELS = (
    "consulta_cuenta",
    "conocimiento_producto",
    "queja_disputa",
    "accion_tarjeta",
    "fuera_de_alcance",
    "ambiguo",
)
PRODUCT_TAGS = (
    "tarjeta_credito",
    "tarjeta_debito",
    "tarjeta",
    "caja_ahorro",
    "cuenta_corriente",
    "dpf",
    "credito_vivienda",
    "credito_pyme",
    "qr",
    "banca_internet",
    "transferencia",
)
CURRENCIES = ("BOB", "USD")
OUTPUT_KEYS = ("intent", "producto", "monto", "moneda")

# Prompt de sistema CORTO a propósito: se ejecuta en cada turno, cada token cuenta en latencia
SYSTEM_PROMPT = (
    "Clasifica el mensaje de un cliente bancario. Responde solo JSON con las claves "
    "intent, producto, monto, moneda. "
    f"intent: {', '.join(INTENT_LABELS)}. producto: {', '.join(PRODUCT_TAGS)} o null. "
    "monto: número o null. moneda: BOB, USD o null."
)


def build_messages(text: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}]


def serialize_label(label: dict[str, Any]) -> str:
    """Salida objetivo canónica: siempre las 4 claves, en orden, sin espacios extra."""
    return json.dumps({k: label.get(k) for k in OUTPUT_KEYS}, ensure_ascii=False, separators=(",", ":"))


def parse_output(raw: str) -> dict[str, Any]:
    """Parsea y valida la salida del modelo. Lanza ValueError si no cumple el esquema."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"sin JSON en la salida: {raw[:80]!r}")
    data = json.loads(raw[start : end + 1])
    if data.get("intent") not in INTENT_LABELS:
        raise ValueError(f"intent inválido: {data.get('intent')!r}")
    if data.get("producto") not in (None, *PRODUCT_TAGS):
        data["producto"] = None  # producto desconocido: se descarta, no invalida la intención
    if data.get("moneda") not in (None, *CURRENCIES):
        data["moneda"] = None
    if data.get("monto") is not None:
        try:
            data["monto"] = float(data["monto"])
        except (TypeError, ValueError):
            data["monto"] = None
    return {k: data.get(k) for k in OUTPUT_KEYS}
