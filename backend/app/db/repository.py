"""Repositorio del core bancario: la ÚNICA puerta de acceso a los datos de clientes.

Las tools del agente, el registro de reclamos (HITL) y /health hablan con esta interfaz y no
saben qué motor hay debajo:

- SQLiteRepository   (CORE_BACKEND=sqlite):    desarrollo, tests y CI, sin dependencias.
- SqlServerRepository (CORE_BACKEND=sqlserver): SQL Server real, como un core bancario on-premise.
  Usa procedimientos almacenados y un usuario de aplicación con permisos mínimos.

Cada método recibe el customer_id de la sesión: la pertenencia de una cuenta se valida en la
consulta (y en SQL Server, dentro del procedimiento almacenado), nunca en el LLM.
"""

from functools import lru_cache
from typing import Protocol


class CoreBankingRepository(Protocol):
    backend: str

    def list_accounts(self, customer_id: str) -> list[dict]:
        """Cuentas del cliente: account_id, account_type, currency, balance (float), status."""
        ...

    def recent_transactions(self, customer_id: str, account_suffix: str, limit: int) -> list[dict]:
        """Últimos movimientos de una cuenta DEL cliente (vacío si la cuenta no es suya)."""
        ...

    def insert_complaint(self, complaint: dict) -> None:
        """Registra un reclamo aprobado por el operador (sistema de registro)."""
        ...

    def ping(self) -> bool: ...


@lru_cache
def get_repository() -> CoreBankingRepository:
    from app.config import get_settings

    settings = get_settings()
    if settings.core_backend == "sqlserver":
        from app.db.sqlserver import SqlServerRepository

        return SqlServerRepository(
            host=settings.sqlserver_host,
            port=settings.sqlserver_port,
            database=settings.sqlserver_database,
            user=settings.sqlserver_user,
            password=settings.sqlserver_password,
        )
    from app.db.sqlite import SQLiteRepository

    return SQLiteRepository(settings.sqlite_path)
