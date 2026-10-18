"""Detección de prompt injection y de intentos de acceso a datos de otros clientes.

Primera capa, determinista. La defensa principal NO es este filtro sino el diseño:
las tools reciben el customer_id desde el estado de la sesión, nunca desde el LLM,
por lo que aunque un ataque pase este filtro no puede leer cuentas ajenas.
"""

import re

from app.text import normalize

INJECTION_PATTERNS = [
    r"ignora\w* (todas )?(las |tus )?(instrucciones|reglas)",
    r"olvida\w* (todas )?(las |tus )?(instrucciones|reglas)",
    r"ignore (all |any )?(previous |prior |the )?(instructions|rules)",
    r"(prompt|mensaje|instrucciones) del sistema",
    r"system prompt",
    r"revela\w* (tu|tus|el) (prompt|instrucciones|configuracion)",
    r"modo (desarrollador|developer|dios|sin restricciones)",
    r"\bjailbreak\b",
    r"\bdan\b.*(modo|mode)",
    r"(actua|comportate) como (si fueras )?(un |el )?(administrador|admin|desarrollador|otro asistente)",
    r"ahora eres",
    r"sin (ninguna )?restriccion",
]
INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS))

# Datos de otros clientes: mención de un ID de cliente distinto al de la sesión
CUSTOMER_ID_RE = re.compile(r"\bCLI-\d{3}\b", re.IGNORECASE)
OTHER_CUSTOMER_RE = re.compile(r"(cuenta|saldo|movimientos?) de (otro|otra|mi (vecino|jefe|esposa|esposo|amigo))")


def check_injection(text: str, customer_id: str) -> str | None:
    """Devuelve el motivo de bloqueo o None si el texto es aceptable."""
    normalized = normalize(text)
    if INJECTION_RE.search(normalized):
        return "prompt_injection"
    mentioned = {m.upper() for m in CUSTOMER_ID_RE.findall(text)}
    if mentioned - {customer_id.upper()} or OTHER_CUSTOMER_RE.search(normalized):
        return "acceso_no_autorizado"
    return None
