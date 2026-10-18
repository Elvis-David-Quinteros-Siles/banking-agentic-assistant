"""Tools de consulta bancaria sobre SQLite, con SQL parametrizado (sin text-to-SQL).

Seguridad: el customer_id NO es un parámetro que el LLM pueda elegir. Las tools se crean
por request con el customer_id de la sesión ya fijado (closure), y toda query filtra por él.
Así, un prompt injection del tipo "dame el saldo de la cuenta 2000781201" no funciona:
la cuenta no pertenece al cliente y la query devuelve vacío.

Los números de cuenta se devuelven ENMASCARADOS: el LLM nunca ve el número completo.
"""

from pathlib import Path

from langchain_core.tools import StructuredTool

from app.db.sqlite import connect

ACCOUNT_TYPE_LABELS = {"caja_ahorro": "Caja de ahorro", "cuenta_corriente": "Cuenta corriente"}


def mask_account(account_id: str) -> str:
    return f"****{account_id[-4:]}"


class BankingTools:
    """Operaciones de lectura del core bancario para UN cliente."""

    def __init__(self, db_path: Path, customer_id: str):
        self.db_path = db_path
        self.customer_id = customer_id

    def _owned_account_id(self, conn, account_ref: str) -> str | None:
        """Resuelve una referencia ("****2101" o "2101") a un account_id propio del cliente."""
        suffix = account_ref.replace("*", "").strip()[-4:]
        if len(suffix) < 4 or not suffix.isdigit():
            return None
        row = conn.execute(
            "SELECT account_id FROM accounts WHERE customer_id = ? AND account_id LIKE ?",
            (self.customer_id, f"%{suffix}"),
        ).fetchone()
        return row["account_id"] if row else None

    def get_accounts(self) -> list[dict]:
        """Lista las cuentas del cliente con su saldo."""
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT account_id, account_type, currency, balance, status "
                "FROM accounts WHERE customer_id = ? ORDER BY account_id",
                (self.customer_id,),
            ).fetchall()
        return [
            {
                "cuenta": mask_account(r["account_id"]),
                "tipo": ACCOUNT_TYPE_LABELS.get(r["account_type"], r["account_type"]),
                "moneda": r["currency"],
                "saldo": float(r["balance"]),
                "estado": r["status"],
            }
            for r in rows
        ]

    def get_transactions(self, account: str, limit: int = 5) -> list[dict]:
        """Últimos movimientos de una cuenta del cliente (máximo 20)."""
        limit = max(1, min(int(limit), 20))
        with connect(self.db_path) as conn:
            account_id = self._owned_account_id(conn, account)
            if account_id is None:
                return []
            rows = conn.execute(
                "SELECT posted_at, description, amount, currency, channel, merchant "
                "FROM transactions WHERE account_id = ? ORDER BY posted_at DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        return [
            {
                "fecha": r["posted_at"],
                "descripcion": r["description"],
                "monto": float(r["amount"]),
                "moneda": r["currency"],
                "canal": r["channel"],
                "comercio": r["merchant"],
            }
            for r in rows
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
