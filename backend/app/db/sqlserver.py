"""Core bancario en SQL Server (CORE_BACKEND=sqlserver), vía procedimientos almacenados.

La aplicación se conecta con un login de aplicación que solo puede EJECUTAR procedimientos
(ver sqlserver_schema.sql): aunque un ataque lograra inyectar algo en una consulta, ese usuario
no tiene permisos para leer tablas directamente ni para ver datos de otros clientes.

Driver: pymssql (incluye FreeTDS, no requiere instalar el ODBC de Microsoft). En un banco se
usaría pyodbc + "ODBC Driver 18 for SQL Server" con TLS obligatorio y pool de conexiones.
"""

import logging
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("sqlserver")


class SqlServerRepository:
    backend = "sqlserver"

    def __init__(self, host: str, port: int, database: str, user: str, password: str):
        self._params: dict[str, Any] = {
            "server": host,
            "port": port,
            "database": database,
            "user": user,
            "password": password,
            "login_timeout": 5,
            "timeout": 10,
        }

    @contextmanager
    def _cursor(self):
        import pymssql  # import diferido: solo se necesita con CORE_BACKEND=sqlserver

        conn = pymssql.connect(**self._params)
        try:
            cursor = conn.cursor(as_dict=True)
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_accounts(self, customer_id: str) -> list[dict]:
        with self._cursor() as cur:
            cur.execute("EXEC dbo.usp_CustomerAccounts @CustomerId = %s", (customer_id,))
            rows = cur.fetchall()
        return [{**r, "balance": float(r["balance"])} for r in rows]

    def recent_transactions(self, customer_id: str, account_suffix: str, limit: int) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "EXEC dbo.usp_RecentTransactions @CustomerId = %s, @AccountSuffix = %s, @Limit = %d",
                (customer_id, account_suffix, int(limit)),
            )
            rows = cur.fetchall()
        return [{**r, "amount": float(r["amount"])} for r in rows]

    def insert_complaint(self, complaint: dict) -> None:
        with self._cursor() as cur:
            cur.execute(
                "EXEC dbo.usp_RegisterComplaint @ComplaintId=%s, @CustomerId=%s, @ThreadId=%s, @Category=%s, "
                "@Product=%s, @Amount=%s, @Currency=%s, @Priority=%s, @Summary=%s, @Status=%s, @ReviewerId=%s",
                (
                    complaint["complaint_id"],
                    complaint["customer_id"],
                    complaint["thread_id"],
                    complaint["category"],
                    complaint.get("product"),
                    complaint.get("amount"),
                    complaint.get("currency"),
                    complaint["priority"],
                    complaint["summary"],
                    complaint["status"],
                    complaint["reviewer_id"],
                ),
            )

    def ping(self) -> bool:
        try:
            with self._cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                cur.fetchall()
            return True
        except Exception as error:  # noqa: BLE001
            logger.warning("SQL Server no disponible: %s", error)
            return False
