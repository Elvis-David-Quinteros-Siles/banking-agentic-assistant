"""Tabla de precios (USD por 1M de tokens) para calcular el costo por nodo y por turno.

Los precios de proveedores cloud son referenciales y deben actualizarse antes de usarlos
en un análisis de costo real. Los modelos locales (Ollama/LoRA) tienen costo marginal 0:
su costo real es infraestructura (GPU/CPU on-premise), que se documenta aparte.
"""

import logging
from typing import Any

# modelo -> (input, output) USD por 1M tokens. Referenciales: verificar el precio vigente en la
# página del proveedor y agregar aquí cualquier modelo que se use. Un modelo sin precio cuenta
# costo 0 y se advierte una vez en el log (los modelos locales son 0 por definición).
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
}
_warned: set[str] = set()


def usage_from_message(message: Any, model: str) -> dict[str, Any]:
    """Extrae tokens del AIMessage (usage_metadata) y calcula el costo según la tabla."""
    usage = getattr(message, "usage_metadata", None) or {}
    tokens_in = int(usage.get("input_tokens", 0))
    tokens_out = int(usage.get("output_tokens", 0))
    if model not in PRICES_PER_MTOK and model not in _warned and ":" not in model:  # "qwen2.5:3b" = Ollama local
        _warned.add(model)
        logging.getLogger("pricing").warning("Sin precio configurado para %s: el costo se reportará como 0", model)
    price_in, price_out = PRICES_PER_MTOK.get(model, (0.0, 0.0))
    cost = (tokens_in * price_in + tokens_out * price_out) / 1_000_000
    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": round(cost, 6)}


def add_usage(*usages: dict[str, Any]) -> dict[str, Any]:
    """Suma varios registros de uso (p. ej. las vueltas de un loop ReAct)."""
    return {
        "tokens_in": sum(u.get("tokens_in", 0) for u in usages),
        "tokens_out": sum(u.get("tokens_out", 0) for u in usages),
        "cost_usd": round(sum(u.get("cost_usd", 0.0) for u in usages), 6),
    }
