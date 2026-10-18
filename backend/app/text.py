"""Utilidades de texto compartidas."""

import unicodedata


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para que 'Comisión' y 'comision' coincidan."""
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))
