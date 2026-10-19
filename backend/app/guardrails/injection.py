"""Detección de prompt injection y de intentos de acceso a datos de otros clientes.

Primera capa, determinista. La defensa principal NO es este filtro sino el diseño:
las tools reciben el customer_id desde el estado de la sesión, nunca desde el LLM,
por lo que aunque un ataque pase este filtro no puede leer cuentas ajenas.

El red teaming (evals/redteam/) mostró que la versión por palabras exactas se evadía con
trucos baratos (caracteres invisibles, homoglifos, leetspeak, base64, inglés) y que bloqueaba
preguntas legítimas ("retiros sin restricción", "la cuenta de otro banco"). Por eso:
1. el texto se des-ofusca antes de buscar patrones (variants);
2. los patrones piden la ESTRUCTURA del ataque (verbo + objeto), no una palabra suelta.
"""

import base64
import binascii
import re
import unicodedata

from app.text import normalize

INJECTION_PATTERNS = [
    # Anular las instrucciones
    r"(ignora|olvida|omite|descarta|salta)\w* (todas |todo )?(las |tus |lo de |sus )?(instrucciones|reglas|politicas|indicaciones)",
    r"(no sigas|deja de seguir|ya no sigues) (las |tus )?(instrucciones|reglas|politicas)",
    r"olvida\w* que eres",
    r"ignore (all |any |the |your )?(previous |prior |above |earlier )?(instructions|rules|prompts?)",
    r"forget (that )?you are|forget (all |your |the )?(previous )?(instructions|rules)",
    # Pedir el prompt o la configuración
    r"(prompt|mensaje|instrucciones|reglas|configuracion) (del sistema|internas?|iniciales|ocultas|originales)",
    r"(system|internal|initial|hidden|original) (prompt|instructions|rules|configuration)",
    r"repite\w*\b.{0,60}\b(texto|mensaje|instrucciones|prompt)\b.{0,40}\b(antes|arriba|anterior|inicial)",
    r"repeat\b.{0,60}\b(text|message|instructions|prompt)\b.{0,40}\b(above|before|previous|preceding)",
    r"revela\w* (tu|tus|el) (prompt|instrucciones|configuracion)",
    # Cambiar de identidad o quitar restricciones
    r"(a partir de ahora|desde ahora|de ahora en adelante),? (eres|seras|actua\w*)",
    r"ahora eres (un |una |el |la )?(asistente|ia|bot|chatbot|modelo|\w*gpt)\b",
    r"\b(you are now|from now on,? you are)\b",
    r"(asistente|ia|modo|bot|chatbot|modelo) sin (ninguna |ningun )?(restriccion|regla|filtro|limite|censura)",
    r"\b(without|no) (any )?(rules|restrictions|filters)\b",
    r"modo (desarrollador|developer|dios|sin restricciones|libre|admin)",
    r"developer mode|\bjailbreak\b",
    r"\bdan\b.*(modo|mode)",
    r"(actua|comportate) como (si fueras )?(un |el )?(administrador|admin|desarrollador|otro asistente)",
]
INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS))

# Datos de otros clientes: mención de un ID de cliente distinto al de la sesión
CUSTOMER_ID_RE = re.compile(r"\bCLI-\d{3}\b", re.IGNORECASE)
THIRD_PARTY = (
    r"(otro cliente|otra clienta|otro usuario|otra persona|un tercero|terceros|"
    r"mi (vecino|vecina|jefe|jefa|esposa|esposo|amigo|amiga|hermano|hermana|mama|papa))"
)
OTHER_CUSTOMER_PATTERNS = [
    # Un DATO de un tercero (depositar o transferir A la cuenta de otra persona es legítimo)
    rf"(saldo|saldos|movimientos?|extracto|dinero|plata|cuanto tiene)\w* (de la cuenta |de las cuentas )?(de|del) {THIRD_PARTY}",
    rf"(ver|mostrar|muestrame|dame|consultar|revisar|lista\w*) (el |la |los |las )?(cuenta|cuentas|tarjeta|tarjetas) de {THIRD_PARTY}",
    r"(de|a) (todos|cada uno de) los clientes",
    r"\b(cliente|clienta) (numero |nro\.? |n )?\d+\b",
    r"(balance|transactions|accounts?|cards?) of (another|other|every|all( the)?) (customer|client|user)s?",
]
OTHER_CUSTOMER_RE = re.compile("|".join(OTHER_CUSTOMER_PATTERNS))

# --- Des-ofuscación ---------------------------------------------------------
# Letras cirílicas y griegas que se ven igual que las latinas
CONFUSABLES = str.maketrans("асеорхуіјкмнтвАСЕОРХУІЈКМНТВαορτνικ", "aceopxyijkmhtbACEOPXYIJKMHTBaoptvik")
LEET = str.maketrans({"4": "a", "3": "e", "1": "i", "0": "o", "5": "s", "7": "t", "@": "a", "$": "s"})
SPACED_RE = re.compile(r"\b(?:\w ){3,}\w\b")  # "i g n o r a"
BASE64_RE = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def _clean(text: str) -> str:
    """Sin caracteres de formato invisibles (zero-width, marcas de dirección) y sin homoglifos."""
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf").translate(CONFUSABLES)
    return SPACED_RE.sub(lambda m: m.group().replace(" ", ""), text)


def _unleet(text: str) -> str:
    """Leetspeak solo en palabras que mezclan letras y dígitos: "Bs 350" no se toca (y "CLI-005"
    se revisa en la forma sin des-leetear)."""
    return " ".join(
        w.translate(LEET) if re.search(r"[a-z]", w, re.I) and re.search(r"[\d@$]", w) else w for w in text.split()
    )


def _decoded_payloads(text: str) -> list[str]:
    """Contenido en base64 del mensaje: se revisa igual que el texto visible."""
    payloads = []
    for token in BASE64_RE.findall(text):
        try:
            decoded = base64.b64decode(token + "=" * (-len(token) % 4), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if sum(ch.isprintable() for ch in decoded) >= 0.9 * len(decoded):
            payloads.append(decoded)
    return payloads


def variants(text: str) -> list[str]:
    """El texto tal cual, des-ofuscado y decodificado, normalizado (minúsculas, sin tildes)."""
    candidates = []
    for source in [text, *_decoded_payloads(text)]:
        cleaned = _clean(source)
        candidates += [source, cleaned, _unleet(cleaned)]
    return list(dict.fromkeys(normalize(c) for c in candidates))


def check_injection(text: str, customer_id: str) -> str | None:
    """Devuelve el motivo de bloqueo o None si el texto es aceptable."""
    forms = variants(text)
    if any(INJECTION_RE.search(form) for form in forms):
        return "prompt_injection"
    mentioned = {m.upper() for form in forms for m in CUSTOMER_ID_RE.findall(form)}
    if mentioned - {customer_id.upper()} or any(OTHER_CUSTOMER_RE.search(form) for form in forms):
        return "acceso_no_autorizado"
    return None
