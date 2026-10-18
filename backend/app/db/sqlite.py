"""Acceso a SQLite, que simula el core bancario (SQL Server on-premise en producción).

Las queries usan SQL estándar y parámetros con "?"; migrar a SQL Server implica cambiar
el driver (pyodbc) y la cadena de conexión, no la lógica de negocio.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id   TEXT PRIMARY KEY,
    full_name     TEXT NOT NULL,
    doc_id_masked TEXT NOT NULL,
    segment       TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    account_id   TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    account_type TEXT NOT NULL,
    currency     TEXT NOT NULL CHECK (currency IN ('BOB', 'USD')),
    balance      NUMERIC NOT NULL,
    status       TEXT NOT NULL,
    opened_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    tx_id       TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL REFERENCES accounts(account_id),
    posted_at   TEXT NOT NULL,
    description TEXT NOT NULL,
    amount      NUMERIC NOT NULL,
    currency    TEXT NOT NULL,
    channel     TEXT NOT NULL,
    merchant    TEXT
);
CREATE TABLE IF NOT EXISTS complaints (
    complaint_id TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    thread_id    TEXT NOT NULL,
    category     TEXT NOT NULL,
    product      TEXT,
    amount       NUMERIC,
    currency     TEXT,
    priority     TEXT NOT NULL,
    summary      TEXT NOT NULL,
    status       TEXT NOT NULL,
    reviewer_id  TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tx_account_date ON transactions(account_id, posted_at);
"""


@contextmanager
def connect(db_path: Path):
    """Conexión con filas tipo dict y commit/rollback automático."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path, seed_file: Path, reset: bool = False) -> None:
    """Crea el esquema y carga los datos semilla si la base está vacía (o si reset=True)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if reset and db_path.exists():
        db_path.unlink()

    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] > 0:
            return

        seed = json.loads(seed_file.read_text(encoding="utf-8"))
        for table, rows in (
            ("customers", seed["customers"]),
            ("accounts", seed["accounts"]),
            ("transactions", seed["transactions"]),
        ):
            columns = list(rows[0].keys())
            placeholders = ", ".join("?" for _ in columns)
            conn.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                [tuple(row[c] for c in columns) for row in rows],
            )


class SQLiteRepository:
    """Core bancario simulado en SQLite (desarrollo, tests y CI)."""

    backend = "sqlite"

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def list_accounts(self, customer_id: str) -> list[dict]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT account_id, account_type, currency, balance, status "
                "FROM accounts WHERE customer_id = ? ORDER BY account_id",
                (customer_id,),
            ).fetchall()
        return [{**dict(r), "balance": float(r["balance"])} for r in rows]

    def recent_transactions(self, customer_id: str, account_suffix: str, limit: int) -> list[dict]:
        # El JOIN con accounts filtra por cliente: una cuenta ajena devuelve vacío
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT t.posted_at, t.description, t.amount, t.currency, t.channel, t.merchant "
                "FROM transactions t JOIN accounts a ON a.account_id = t.account_id "
                "WHERE a.customer_id = ? AND substr(a.account_id, -4) = ? "
                "ORDER BY t.posted_at DESC LIMIT ?",
                (customer_id, account_suffix, limit),
            ).fetchall()
        return [{**dict(r), "amount": float(r["amount"])} for r in rows]

    def insert_complaint(self, complaint: dict) -> None:
        columns = list(complaint.keys())
        with connect(self.db_path) as conn:
            conn.execute(
                f"INSERT INTO complaints ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(complaint[c] for c in columns),
            )

    def ping(self) -> bool:
        try:
            with connect(self.db_path) as conn:
                conn.execute("SELECT 1 FROM accounts LIMIT 1")
            return True
        except Exception:  # noqa: BLE001
            return False
