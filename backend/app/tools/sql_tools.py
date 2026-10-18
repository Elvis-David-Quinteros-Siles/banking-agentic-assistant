"""Tools de consulta bancaria sobre el core (SQLite o SQL Server), sin text-to-SQL.

Seguridad: el customer_id NO es un parámetro que el LLM pueda elegir. Las tools se crean
por request con el customer_id de la sesión ya fijado (closure), y toda query filtra por él.
Así, un prompt injection del tipo "dame el saldo de la cuenta 2000781201" no funciona:
la cuenta no pertenece al cliente y la query devuelve vacío.

Los números de cuenta se devuelven ENMASCARADOS: el LLM nunca ve el número completo.
El acceso a datos pasa por el repositorio (app/db/repository.py): estas tools no saben qué
motor hay debajo.
"""

from langchain_core.tools import StructuredTool

from app.db.repository import CoreBankingRepository

ACCOUNT_TYPE_LABELS = {"caja_ahorro": "Caja de ahorro", "cuenta_corriente": "Cuenta corriente"}


def mask_account(account_id: str) -> str:
    return f"****{account_id[-4:]}"


def format_money(amount: float, currency: str) -> str:
    """Formato boliviano (punto miles, coma decimales). Se entrega YA formateado al LLM para que
    lo copie: un modelo pequeño reformatea mal ("12,450.30 BOB") y rompe la exactitud del dato."""
    symbol = "Bs" if currency == "BOB" else "USD"
    text = f"{abs(amount):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if amount < 0 else ''}{symbol} {text}"


class BankingTools:
    """Operaciones de lectura del core bancario para UN cliente."""

    def __init__(self, repository: CoreBankingRepository, customer_id: str):
        self.repository = repository
        self.customer_id = customer_id

    def get_accounts(self) -> list[dict]:
        """Lista las cuentas del cliente con su saldo."""
        return [
            {
                "cuenta": mask_account(r["account_id"]),
                "tipo": ACCOUNT_TYPE_LABELS.get(r["account_type"], r["account_type"]),
                "moneda": r["currency"],
                "saldo": r["balance"],
                "saldo_texto": format_money(r["balance"], r["currency"]),
                "estado": r["status"],
            }
            for r in self.repository.list_accounts(self.customer_id)
        ]

    def get_transactions(self, account: str, limit: int = 5) -> list[dict]:
        """Últimos movimientos de una cuenta del cliente (máximo 20)."""
        suffix = account.replace("*", "").strip()[-4:]
        if len(suffix) < 4 or not suffix.isdigit():
            return []
        limit = max(1, min(int(limit), 20))
        return [
            {
                "fecha": str(r["posted_at"])[:16],
                "descripcion": r["description"],
                "monto": r["amount"],
                "monto_texto": format_money(r["amount"], r["currency"]),
                "moneda": r["currency"],
                "canal": r["channel"],
                "comercio": r["merchant"],
            }
            for r in self.repository.recent_transactions(self.customer_id, suffix, limit)
        ]

    def as_langchain_tools(self) -> list[StructuredTool]:
        """Expone las operaciones como tools para el loop ReAct."""
        return [
            StructuredTool.from_function(
                func=self.get_accounts,
                name="get_accounts",
                description="Lista las cuentas del cliente autenticado con tipo, moneda, saldo y estado.",
            ),
            StructuredTool.from_function(
                func=self.get_transactions,
                name="get_transactions",
                description=(
                    "Devuelve los últimos movimientos de una cuenta del cliente. "
                    "account: número enmascarado tal como lo devuelve get_accounts (ej. '****2101'). "
                    "limit: cantidad de movimientos (1-20, por defecto 5)."
                ),
            ),
        ]
