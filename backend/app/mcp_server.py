"""Servidor MCP (Model Context Protocol): expone el core bancario y la base de conocimiento a
cualquier host MCP (Claude Desktop, Claude Code, IDEs, otros agentes).

Mismos principios que el grafo:
- **Identidad fijada en el servidor**: el cliente sale de MCP_CUSTOMER_ID (en producción, del token
  OAuth de la sesión MCP). Ninguna herramienta recibe customer_id: el modelo del host no puede pedir
  datos de otro cliente aunque lo intente.
- **Guardrails antes de buscar**: la pregunta pasa por detección de injection y enmascarado de PII.
- **Acciones en dos pasos**: prepare_card_block devuelve un código; confirm_card_block lo ejecuta.
  El host debe mostrarle al usuario la confirmación; un solo tool call no basta para bloquear.

Uso:
    python -m app.mcp_server                 # stdio (Claude Desktop / Claude Code)
    python -m app.mcp_server --http          # streamable HTTP en :8765/mcp
"""

import argparse
import re
import secrets
import time
import uuid
from functools import lru_cache

from mcp.server.fastmcp import FastMCP

from app.config import get_settings
from app.db.repository import get_repository
from app.graph.nodes.card_action import CARD_TYPE_LABEL, REASONS
from app.guardrails.injection import check_injection
from app.guardrails.pii import mask_pii
from app.tools.knowledge_base import get_retriever, load_documents
from app.tools.sql_tools import BankingTools

CONFIRMATION_TTL_SECONDS = 300
VALID_REASONS = ("robo", "extravío", "fraude", "solicitud del cliente")
CUSTOMER_RE = re.compile(r"^CLI-\d{3}$")

mcp = FastMCP(
    "banco-asistente",
    instructions=(
        "Herramientas del asistente bancario para el cliente autenticado en esta sesión. "
        "Los montos y saldos vienen del core bancario: cítelos tal cual. "
        "Para bloquear una tarjeta, llame a prepare_card_block, muestre la confirmación al usuario "
        "y llame a confirm_card_block SOLO si el usuario confirma explícitamente."
    ),
)

# Bloqueos preparados que esperan confirmación: código -> datos (en memoria, expiran)
_pending: dict[str, dict] = {}


def session_customer() -> str:
    """Cliente de la sesión MCP. Con AUTH_MODE=jwt sale de MCP_ACCESS_TOKEN (verificado igual que en la
    API: firma, vencimiento, emisor y rol); MCP_CUSTOMER_ID solo vale con AUTH_MODE=off (desarrollo)."""
    from fastapi import HTTPException

    from app.api.auth import decode_token

    settings = get_settings()
    if settings.auth_mode == "jwt":
        if not settings.mcp_access_token:
            raise ValueError("Con AUTH_MODE=jwt el servidor MCP necesita MCP_ACCESS_TOKEN")
        try:
            identity = decode_token(settings.mcp_access_token)
        except HTTPException as error:
            raise ValueError(f"MCP_ACCESS_TOKEN rechazado: {error.detail}") from error
        if identity.role != "customer":
            raise ValueError("MCP_ACCESS_TOKEN debe ser de un cliente")
        return identity.subject
    customer = settings.mcp_customer_id
    if not CUSTOMER_RE.match(customer):
        raise ValueError("MCP_CUSTOMER_ID inválido")
    return customer


def _tools() -> BankingTools:
    return BankingTools(get_repository(), session_customer())


@lru_cache
def _documents() -> dict[str, dict]:
    settings = get_settings()
    return {d["id"]: d for d in load_documents(settings.seed_dir / "knowledge.json")}


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
@mcp.tool()
def get_accounts() -> list[dict]:
    """Cuentas del cliente autenticado con su saldo (número enmascarado)."""
    return _tools().get_accounts()


@mcp.tool()
def get_transactions(account: str, limit: int = 5) -> list[dict]:
    """Últimos movimientos de una cuenta del cliente. account: últimos 4 dígitos (p. ej. "2101" o "****2101")."""
    return _tools().get_transactions(account, limit)


@mcp.tool()
def search_policies(question: str, product: str | None = None, k: int = 3) -> dict:
    """Busca en las políticas y fichas de producto del banco (Graph RAG). Devuelve fragmentos con su id para citar.

    product (opcional): tarjeta_debito, tarjeta_credito, caja_ahorro, cuenta_corriente, dpf, credito_vivienda, ...
    """
    reason = check_injection(question, session_customer())
    if reason:
        return {"blocked": True, "reason": reason, "results": []}
    masked = mask_pii(question)
    docs = get_retriever().search(masked.masked_text, k=max(1, min(k, 5)), product_hint=product)
    return {
        "blocked": False,
        "pii_masked": masked.found,
        "results": [
            {"id": d["id"], "title": d["title"], "text": d["text"], "score": round(d["score"], 3)} for d in docs
        ],
    }


@mcp.resource("policy://{doc_id}")
def policy(doc_id: str) -> str:
    """Texto completo de una política o ficha de producto (p. ej. policy://POL-004)."""
    doc = _documents().get(doc_id.upper())
    if doc is None:
        raise ValueError(f"No existe el documento {doc_id}")
    return f"# {doc['id']} · {doc['title']}\n\n{doc['text']}"


@mcp.tool()
def list_cards() -> list[dict]:
    """Tarjetas del cliente autenticado (tipo, últimos 4 dígitos, estado)."""
    return [
        {"tarjeta": f"{CARD_TYPE_LABEL.get(c['card_type'], c['card_type'])} ****{c['last4']}", **c}
        for c in get_repository().list_cards(session_customer())
    ]


# ---------------------------------------------------------------------------
# Acción con confirmación
# ---------------------------------------------------------------------------
@mcp.tool()
def prepare_card_block(last4: str, reason: str = "solicitud del cliente") -> dict:
    """Paso 1 de 2: prepara el bloqueo de una tarjeta del cliente. NO bloquea nada.

    Devuelve un código de confirmación válido por 5 minutos. Muestre al usuario el texto de
    'confirmation_prompt' y llame a confirm_card_block solo si responde que sí.
    reason: robo | extravío | fraude | solicitud del cliente
    """
    customer = session_customer()
    if reason not in VALID_REASONS:
        reason = next((r for r, words in REASONS if any(w in reason.lower() for w in words)), "solicitud del cliente")
    card = next((c for c in get_repository().list_cards(customer) if c["last4"] == last4.strip()[-4:]), None)
    if card is None:
        return {"ok": False, "error": "El cliente no tiene una tarjeta con esos últimos 4 dígitos."}
    label = f"tarjeta de {CARD_TYPE_LABEL.get(card['card_type'], card['card_type'])} ****{card['last4']}"
    if card["status"] == "bloqueada":
        return {"ok": False, "error": f"La {label} ya está bloqueada."}
    _purge_expired()
    code = secrets.token_hex(3).upper()
    _pending[code] = {"customer": customer, "last4": card["last4"], "reason": reason, "label": label,
                      "expires": time.monotonic() + CONFIRMATION_TTL_SECONDS}  # fmt: skip
    return {
        "ok": True,
        "confirmation_code": code,
        "confirmation_prompt": f"¿Confirma que desea bloquear su {label} (motivo: {reason})? "
        "No podrá usarla hasta reponerla.",
        "expires_in_seconds": CONFIRMATION_TTL_SECONDS,
    }


@mcp.tool()
def confirm_card_block(confirmation_code: str) -> dict:
    """Paso 2 de 2: ejecuta un bloqueo preparado. Llamar SOLO tras la confirmación explícita del usuario."""
    _purge_expired()
    pending = _pending.pop(confirmation_code.strip().upper(), None)
    if pending is None or pending["customer"] != session_customer():
        return {"ok": False, "error": "Código inválido o vencido. Prepare el bloqueo de nuevo."}
    result = get_repository().block_card(
        pending["customer"], pending["last4"], pending["reason"], f"mcp-{uuid.uuid4().hex[:12]}"
    )
    messages = {
        "bloqueada": f"Su {pending['label']} quedó bloqueada. La reposición cuesta Bs 30 [POL-004].",
        "ya_bloqueada": f"Su {pending['label']} ya estaba bloqueada.",
    }
    return {"ok": result in messages, "result": result, "message": messages.get(result, "No se pudo bloquear.")}


def _purge_expired() -> None:
    now = time.monotonic()
    for code in [c for c, p in _pending.items() if p["expires"] < now]:
        _pending.pop(code, None)


# ---------------------------------------------------------------------------
# El sistema multi-agente completo como una herramienta
# ---------------------------------------------------------------------------
@lru_cache
def _graph():
    from app.graph.builder import build_graph

    return build_graph()  # checkpointer en memoria: una conversación por proceso del servidor


_thread_id = f"mcp-{uuid.uuid4().hex[:12]}"


@mcp.tool()
async def ask_assistant(message: str) -> dict:
    """Envía un mensaje al asistente multi-agente completo (guardrails → router → agentes → verificación).

    Mantiene la conversación entre llamadas (seguimientos como "¿y en dólares?"). Los reclamos
    quedan pendientes de un ejecutivo y los bloqueos se hacen con prepare/confirm_card_block.
    """
    from app.services.chat_service import chat_input

    graph = _graph()
    config = {"configurable": {"thread_id": _thread_id}}
    snapshot = await graph.aget_state(config)
    if snapshot.next:  # una pausa anterior sin resolver no debe bloquear la conversación
        await graph.aupdate_state(config, None, as_node="output_guardrail")
    state = await graph.ainvoke(chat_input(session_customer(), message), config)
    pending = (await graph.aget_state(config)).next
    answer = state.get("final_answer") or ""
    if "action_confirmation" in pending:
        answer += " (Desde MCP, use prepare_card_block y confirm_card_block para confirmar el bloqueo.)"
    elif "hitl_review" in pending:
        answer = "Su reclamo quedó pendiente de revisión por un ejecutivo."
    return {
        "answer": answer,
        "intent": state.get("intent"),
        "citations": state.get("citations", []),
        "status": "interrupted" if pending else state.get("status"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidor MCP del asistente bancario")
    parser.add_argument("--http", action="store_true", help="streamable HTTP en lugar de stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.http:
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
