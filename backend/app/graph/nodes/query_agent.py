"""Subagente de consultas (saldos y movimientos).

- Con LLM: loop ReAct explícito (razonar → llamar tool → observar) con tope de iteraciones.
  Se implementa a mano en lugar de usar un agente prebuilt para que el control de costo
  (máx. vueltas, historial recortado) y la instrumentación sean visibles y auditables.
- Sin LLM (o si falla): ruta determinista que elige la tool por palabras clave y
  responde con plantilla. Los datos son los mismos; cambia solo la redacción.
"""

import json

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from app.config import get_settings
from app.graph.nodes.common import last_user_text, log_fallback, recent_history
from app.graph.state import AgentState
from app.llm.pricing import add_usage, usage_from_message
from app.llm.provider import get_chat_model, model_name
from app.observability.tracing import traced
from app.text import normalize
from app.tools.sql_tools import BankingTools

SYSTEM_PROMPT = """Eres el agente de consultas de un banco boliviano. Respondes en español, de forma breve y cordial.
Usa SIEMPRE las tools para obtener datos; nunca inventes saldos ni movimientos.
Los números de cuenta solo se muestran enmascarados (****1234). Montos con 2 decimales y su moneda (Bs o USD).
Si el cliente no especifica la cuenta y tiene varias, muestra todas."""


def format_money(amount: float, currency: str) -> str:
    symbol = "Bs" if currency == "BOB" else "USD"
    # Formato boliviano: punto para miles, coma para decimales
    text = f"{abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if amount < 0 else ''}{symbol} {text}"


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


def deterministic_answer(tools: BankingTools, text: str) -> str:
    accounts = tools.get_accounts()
    if not accounts:
        return "No encontré cuentas asociadas a su usuario."

    normalized = normalize(text)
    if any(w in normalized for w in ("movimiento", "extracto", "transaccion", "gaste", "depositaron")):
        account = pick_account(accounts, text)
        movements = tools.get_transactions(account["cuenta"], limit=5)
        if not movements:
            return f"La cuenta {account['cuenta']} no registra movimientos recientes."
        lines = [
            f"- {m['fecha']} · {m['descripcion']} ({m['comercio']}): {format_money(m['monto'], m['moneda'])}"
            for m in movements
        ]
        return f"Últimos movimientos de su {account['tipo'].lower()} {account['cuenta']}:\n" + "\n".join(lines)

    lines = [
        f"- {a['tipo']} {a['cuenta']} ({a['moneda']}): {format_money(a['saldo'], a['moneda'])}"
        + ("" if a["estado"] == "activa" else f" — estado: {a['estado']}")
        for a in accounts
    ]
    return "Estos son los saldos de sus cuentas:\n" + "\n".join(lines)


def react_answer(tools: BankingTools, state: AgentState) -> tuple[str, dict]:
    """Loop ReAct con tope de iteraciones. Devuelve (respuesta, uso de tokens)."""
    settings = get_settings()
    lc_tools = {t.name: t for t in tools.as_langchain_tools()}
    llm = get_chat_model().bind_tools(list(lc_tools.values()))

    messages = [SystemMessage(content=SYSTEM_PROMPT), *recent_history(state["messages"])]
    usages = []
    for _ in range(settings.react_max_iterations):
        response = llm.invoke(messages)
        usages.append(usage_from_message(response, model_name()))
        messages.append(response)
        if not response.tool_calls:
            return str(response.content), add_usage(*usages)
        for call in response.tool_calls:
            tool = lc_tools.get(call["name"])
            result = tool.invoke(call["args"]) if tool else {"error": f"tool desconocida {call['name']}"}
            messages.append(ToolMessage(content=json.dumps(result, ensure_ascii=False), tool_call_id=call["id"]))
    raise RuntimeError("se alcanzó el máximo de iteraciones ReAct sin respuesta final")


@traced("query_agent")
def query_agent_node(state: AgentState) -> dict:
    settings = get_settings()
    tools = BankingTools(settings.sqlite_path, state["customer_id"])
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
