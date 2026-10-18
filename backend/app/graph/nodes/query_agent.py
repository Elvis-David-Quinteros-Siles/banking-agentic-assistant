"""Subagente de consultas (saldos y movimientos).

- Con LLM: loop ReAct explícito (razonar → llamar tool → observar) con tope de iteraciones.
  Se implementa a mano en lugar de usar un agente prebuilt para que el control de costo
  (máx. vueltas, historial recortado) y la instrumentación sean visibles y auditables.
  Context engineering (detectado con Ollama + qwen2.5:3b en los Evals): las cuentas del cliente
  se PRECARGAN en el contexto porque siempre hacen falta; el modelo pequeño no llamaba a la
  tool y le pedía el número de cuenta al cliente. El LLM solo decide lo que requiere criterio
  (qué cuenta y cuántos movimientos, vía get_transactions).
- Sin LLM (o si falla): ruta determinista que elige la tool por palabras clave y
  responde con plantilla. Los datos son los mismos; cambia solo la redacción.
"""

import json
import re

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from app.config import get_settings
from app.db.repository import get_repository
from app.graph.nodes.common import last_user_text, log_fallback, recent_history
from app.graph.state import AgentState
from app.llm.pricing import add_usage, usage_from_message
from app.llm.provider import get_chat_model, model_name
from app.observability.tracing import traced
from app.text import normalize
from app.tools.sql_tools import BankingTools, format_money

SYSTEM_PROMPT = """Eres el agente de consultas de un banco boliviano. Respondes en español, de forma breve y cordial.
Ya conoces las cuentas del cliente autenticado (lista CUENTAS, datos oficiales del core bancario).
- Saldos: responde con los datos de CUENTAS. Si no especifica la cuenta, lista TODAS sus cuentas,
  una por línea: tipo, número enmascarado y saldo_texto.
- Movimientos: llama a get_transactions con el número enmascarado de la cuenta ("****1234"). Si no
  especifica la cuenta, usa la que coincida con la moneda o el tipo mencionado, o la primera.
- NUNCA pidas el número de cuenta al cliente: ya lo tienes.
- Copia los montos EXACTAMENTE como aparecen en saldo_texto o monto_texto. No inventes datos.
- Los números de cuenta solo se muestran enmascarados."""

# Si el modelo aun así pide datos que ya tiene, la respuesta se descarta (ruta determinista)
# Solo PEDIDOS de datos ("necesito el número de cuenta", "¿podría proporcionarlo?"), no menciones
# ("su caja de ahorro con número de cuenta ****2101" es una respuesta válida: falso positivo observado)
EVASIVE_RE = re.compile(
    r"(necesit\w*|indique|ind[ií]came|proporci[oó]n\w*|podr[ií]a\w* (darme|indicarme|proporcionar))[^.?!]{0,60}(n[uú]mero|cuenta|dato)",
    re.IGNORECASE,
)
# Fuga del contexto interno: observado con qwen2.5:3b, que devolvió literalmente "CUENTAS: [{...}]"
INTERNAL_LEAK_RE = re.compile(r'CUENTAS:|"saldo_texto"|"monto_texto"|\[\s*\{\s*"')


def pick_account(accounts: list[dict], text: str) -> dict | None:
    """Elige la cuenta mencionada (por moneda, tipo o últimos 4 dígitos); si no, la primera."""
    normalized = normalize(text)
    for acc in accounts:
        if acc["cuenta"][-4:] in normalized:
            return acc
    for acc in accounts:
        if acc["moneda"] == "USD" and ("dolar" in normalized or "usd" in normalized):
            return acc
        if "corriente" in normalized and "corriente" in acc["tipo"].lower():
            return acc
    return accounts[0] if accounts else None


def mentions_specific_account(text: str, accounts: list[dict]) -> bool:
    normalized = normalize(text)
    return any(acc["cuenta"][-4:] in normalized for acc in accounts) or any(
        w in normalized for w in ("corriente", "dolar", "usd", "bolivianos", "ahorro en")
    )


def ensure_all_balances(answer: str, accounts: list[dict], question: str) -> str:
    """Si el cliente no pidió una cuenta específica, ninguna cuenta puede quedar fuera de la respuesta.

    Observado con qwen2.5:3b: mostraba la caja de ahorro en Bs y omitía la de USD. Se agregan de
    forma determinista las cuentas faltantes en lugar de descartar toda la respuesta.
    """
    if mentions_specific_account(question, accounts):
        return answer
    missing = [a for a in accounts if a["saldo_texto"] not in answer]
    if not missing:
        return answer
    lines = "\n".join(f"- {a['tipo']} {a['cuenta']} ({a['moneda']}): {a['saldo_texto']}" for a in missing)
    return f"{answer.rstrip()}\n\nTambién tiene:\n{lines}"


def format_movements(account: dict, movements: list[dict]) -> str:
    """Extracto redactado por código: fechas, comercios y montos textuales del core, siempre."""
    # El saldo va en el encabezado, como en un extracto real (y cubre el caso observado de un
    # LLM que eligió "movimientos" ante una pregunta de saldo)
    header = f"Su {account['tipo'].lower()} {account['cuenta']} tiene un saldo de {account['saldo_texto']}."
    if not movements:
        return f"{header} No registra movimientos recientes."
    lines = [f"- {m['fecha']} · {m['descripcion']} ({m['comercio']}): {m['monto_texto']}" for m in movements]
    return f"{header}\nÚltimos movimientos:\n" + "\n".join(lines)


def deterministic_answer(tools: BankingTools, text: str) -> str:
    accounts = tools.get_accounts()
    if not accounts:
        return "No encontré cuentas asociadas a su usuario."

    normalized = normalize(text)
    if any(w in normalized for w in ("movimiento", "extracto", "transaccion", "gaste", "depositaron")):
        account = pick_account(accounts, text) or accounts[0]
        return format_movements(account, tools.get_transactions(account["cuenta"], limit=5))

    lines = [
        f"- {a['tipo']} {a['cuenta']} ({a['moneda']}): {format_money(a['saldo'], a['moneda'])}"
        + ("" if a["estado"] == "activa" else f" — estado: {a['estado']}")
        for a in accounts
    ]
    return "Estos son los saldos de sus cuentas:\n" + "\n".join(lines)


def react_answer(tools: BankingTools, state: AgentState) -> tuple[str, dict]:
    """Loop ReAct con tope de iteraciones. Devuelve (respuesta, uso de tokens)."""
    settings = get_settings()
    lc_tools = {t.name: t for t in tools.as_langchain_tools() if t.name == "get_transactions"}
    base_llm = get_chat_model()
    if base_llm is None:
        raise RuntimeError("No hay LLM configurado")
    llm = base_llm.bind_tools(list(lc_tools.values()))

    accounts = tools.get_accounts()
    system = SystemMessage(content=f"{SYSTEM_PROMPT}\n\nCUENTAS: {json.dumps(accounts, ensure_ascii=False)}")
    messages = [system, *recent_history(state["messages"])]
    usages = []
    for _ in range(settings.react_max_iterations):
        response = llm.invoke(messages)
        usages.append(usage_from_message(response, model_name()))
        messages.append(response)
        if not response.tool_calls and not str(response.content).strip():
            # Falla observada con qwen2.5:3b vía Ollama: con tools disponibles emite una tool call
            # malformada que el parser descarta y la respuesta llega vacía. Se reintenta UNA vez
            # sin tools (el contexto ya trae las cuentas); si vuelve vacía, ruta determinista.
            response = base_llm.invoke(messages)
            usages.append(usage_from_message(response, model_name()))
            if not str(response.content).strip():
                raise ValueError("respuesta vacía del LLM")
        if not response.tool_calls:
            answer = str(response.content)
            if EVASIVE_RE.search(answer):
                raise ValueError(f"respuesta evasiva del LLM: {answer[:80]!r}")
            if INTERNAL_LEAK_RE.search(answer):
                raise ValueError("la respuesta expone el contexto interno")
            # Respuesta de saldos: ninguna cuenta puede quedar fuera
            return ensure_all_balances(answer, accounts, last_user_text(state)), add_usage(*usages)
        # El LLM ya decidió QUÉ consultar (cuenta y cantidad): el extracto lo redacta el código.
        # Con qwen2.5:3b se observó que reescribía los comercios ("compra de joyería" en lugar de
        # "Joyería Brillante"); un extracto bancario no se parafrasea. Además ahorra una llamada.
        sections = []
        for call in response.tool_calls:
            if call["name"] not in lc_tools:
                messages.append(ToolMessage(content=f"tool desconocida {call['name']}", tool_call_id=call["id"]))
                continue
            account_ref = str(call["args"].get("account", "")).replace("*", "")[-4:]
            account = next((a for a in accounts if a["cuenta"][-4:] == account_ref), None)
            if account is None:
                messages.append(ToolMessage(content="esa cuenta no pertenece al cliente", tool_call_id=call["id"]))
                continue
            limit = call["args"].get("limit", 5)
            sections.append(format_movements(account, tools.get_transactions(account["cuenta"], limit=limit)))
        if sections:
            return "\n\n".join(sections), add_usage(*usages)
    raise RuntimeError("se alcanzó el máximo de iteraciones ReAct sin respuesta final")


@traced("query_agent")
def query_agent_node(state: AgentState) -> dict:
    tools = BankingTools(get_repository(), state["customer_id"])
    update: dict = {"status": "completed"}

    if get_chat_model() is not None:
        try:
            answer, usage = react_answer(tools, state)
            update["_usage"] = usage
        except Exception as error:  # noqa: BLE001
            log_fallback("query_agent", error)
            answer = deterministic_answer(tools, last_user_text(state))
            update["degraded"] = True
    else:
        answer = deterministic_answer(tools, last_user_text(state))

    update.update({"final_answer": answer, "messages": [AIMessage(content=answer)]})
    return update
