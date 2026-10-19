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
CREATE TABLE IF NOT EXISTS cards (
    card_id     TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    account_id  TEXT NOT NULL REFERENCES accounts(account_id),
    card_type   TEXT NOT NULL,
    last4       TEXT NOT NULL,
    status      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS card_actions (  -- auditoría de acciones confirmadas por el cliente
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id     TEXT NOT NULL REFERENCES cards(card_id),
    customer_id TEXT NOT NULL,
    action      TEXT NOT NULL,
    reason      TEXT,
    thread_id   TEXT NOT NULL,
    created_at  TEXT NOT NULL
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
    """Crea el esquema y carga los datos semilla en cada tabla vacía (o en todas si reset=True).

    Por tabla, no por base: una base creada antes de que existiera una tabla (cards llegó en la
    Fase 6) recibe sus datos en el siguiente arranque en lugar de quedar vacía para siempre.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if reset and db_path.exists():
        db_path.unlink()

    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        seed = json.loads(seed_file.read_text(encoding="utf-8"))
        for table, rows in (
            ("customers", seed["customers"]),
            ("accounts", seed["accounts"]),
            ("transactions", seed["transactions"]),
            ("cards", seed.get("cards", [])),
        ):
            if not rows or conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0:
                continue
            columns = list(rows[0].keys())
            placeholders = ", ".join("?" for _ in columns)
            conn.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                [tuple(row[c] for c in columns) for row in rows],
            )


def restore_seed_cards(db_path: Path, seed_file: Path) -> int:
    """Devuelve cada tarjeta al estado de la semilla. Solo para la demo pública: si un visitante
    bloquea la tarjeta de CLI-001, el siguiente vuelve a encontrarla activa. La auditoría
    (card_actions) no se toca. Devuelve cuántas tarjetas cambiaron."""
    seed = json.loads(seed_file.read_text(encoding="utf-8"))
    with connect(db_path) as conn:
        changed = 0
        for card in seed.get("cards", []):
            cursor = conn.execute(
                "UPDATE cards SET status = ? WHERE card_id = ? AND status <> ?",
                (card["status"], card["card_id"], card["status"]),
            )
            changed += cursor.rowcount
        return changed


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

    def list_cards(self, customer_id: str) -> list[dict]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT card_type, last4, status FROM cards WHERE customer_id = ? ORDER BY card_id", (customer_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def block_card(self, customer_id: str, last4: str, reason: str, thread_id: str) -> str:
        """Bloquea una tarjeta DEL cliente. Devuelve: bloqueada | ya_bloqueada | no_encontrada."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT card_id, status FROM cards WHERE customer_id = ? AND last4 = ?", (customer_id, last4)
            ).fetchone()
            if row is None:
                return "no_encontrada"
            if row["status"] == "bloqueada":
                return "ya_bloqueada"
            conn.execute("UPDATE cards SET status = 'bloqueada' WHERE card_id = ?", (row["card_id"],))
            conn.execute(
                "INSERT INTO card_actions (card_id, customer_id, action, reason, thread_id, created_at) "
                "VALUES (?, ?, 'bloqueo', ?, ?, datetime('now'))",
                (row["card_id"], customer_id, reason, thread_id),
            )
        return "bloqueada"

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
