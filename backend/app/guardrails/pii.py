"""Detección y enmascarado de PII antes de que el texto llegue a cualquier LLM.

Tipos detectados: tarjeta (validada con Luhn), cédula de identidad boliviana, celular, email.
Es determinista a propósito: barato (< 1 ms), auditable y sin falsos negativos por
"creatividad" de un modelo. En producción se complementaría con un NER (p. ej. Presidio).
"""

import re
from dataclasses import dataclass, field

# 13–19 dígitos, con espacios o guiones opcionales entre grupos
CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
# CI: requiere contexto (palabra clave antes o código de departamento después) para no confundir con montos
CI_KEYWORD_RE = re.compile(
    r"(\b(?:ci|c\.i\.|carnet|c[eé]dula)(?:\s+de\s+identidad)?[\s:#nro.°]*)\d{5,8}(?:-\d[A-Z])?",
    re.IGNORECASE,
)
CI_DEPT_RE = re.compile(r"\b\d{5,8}(?:-\d[A-Z])?\s?(?:SC|LP|CB|OR|PT|TJ|CH|BE|PD)\b")
# Celular boliviano: 8 dígitos que empiezan en 6 o 7, con +591 opcional
PHONE_RE = re.compile(r"(?:\+?591[ -]?)?(?<!\d)[67]\d{7}(?!\d)")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def luhn_valid(number: str) -> bool:
    digits = [int(d) for d in number if d.isdigit()]
    checksum = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


@dataclass
class PIIResult:
    masked_text: str
    found: list[str] = field(default_factory=list)  # tipos encontrados, sin el valor


def _mask_card(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group())
    if not luhn_valid(digits):
        return match.group()
    return f"[TARJETA ****{digits[-4:]}]"


def mask_pii(text: str) -> PIIResult:
    found: list[str] = []
    masked = text

    def _sub(pattern: re.Pattern, kind: str, replacer) -> None:
        nonlocal masked
        new = pattern.sub(replacer, masked)
        if new != masked:
            found.append(kind)
        masked = new

    _sub(CARD_RE, "tarjeta", _mask_card)
    _sub(EMAIL_RE, "email", lambda m: "[EMAIL]")
    _sub(CI_KEYWORD_RE, "cedula", lambda m: f"{m.group(1)}[CI]")
    _sub(CI_DEPT_RE, "cedula", lambda m: "[CI]")
    _sub(PHONE_RE, "telefono", lambda m: "[CELULAR]")
    return PIIResult(masked_text=masked, found=list(dict.fromkeys(found)))


# Números largos (cuentas, tarjetas) en la SALIDA: se dejan solo los últimos 4 dígitos
LONG_NUMBER_RE = re.compile(r"\b\d{8,19}\b")


def mask_long_numbers(text: str) -> str:
    return LONG_NUMBER_RE.sub(lambda m: f"****{m.group()[-4:]}", text)
