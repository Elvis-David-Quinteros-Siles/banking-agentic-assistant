"""Crea el core bancario en SQL Server: base, esquema, procedimientos, rol, login de aplicación y datos.

Se ejecuta con un usuario administrador (sa en desarrollo); la aplicación después se conecta con
el login de aplicación, que solo puede ejecutar procedimientos. Idempotente.

Uso (desde backend/, con `docker compose up -d sqlserver`):
    python scripts/init_sqlserver.py            # crea lo que falte y carga datos si está vacío
    python scripts/init_sqlserver.py --reset    # borra y recrea la base
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "app" / "db" / "sqlserver_schema.sql"


def connect(database: str, autocommit: bool = True):
    import pymssql

    settings = get_settings()
    return pymssql.connect(
        server=settings.sqlserver_host,
        port=settings.sqlserver_port,
        database=database,
        user=os.environ.get("SQLSERVER_ADMIN_USER", "sa"),
        password=os.environ.get("MSSQL_SA_PASSWORD", "BankDemo#2026"),
        autocommit=autocommit,
        login_timeout=5,
    )


def wait_for_server(seconds: int = 120) -> None:
    for _ in range(seconds // 3):
        try:
            connect("master").close()
            return
        except Exception:  # noqa: BLE001
            time.sleep(3)
    raise SystemExit("SQL Server no responde. ¿Ejecutaste 'docker compose up -d sqlserver'?")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    db, app_user, app_password = settings.sqlserver_database, settings.sqlserver_user, settings.sqlserver_password
    started = time.perf_counter()

    print(
        f"SQL Server {settings.sqlserver_host}:{settings.sqlserver_port} · base {db} · login de aplicación {app_user}"
    )
    wait_for_server()

    master = connect("master")
    cur = master.cursor()
    if args.reset:
        cur.execute(
            f"IF DB_ID('{db}') IS NOT NULL BEGIN ALTER DATABASE [{db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{db}]; END"
        )
    cur.execute(f"IF DB_ID('{db}') IS NULL CREATE DATABASE [{db}]")
    # Login de aplicación a nivel servidor (la contraseña viene de la configuración, nunca del código)
    cur.execute(
        f"IF SUSER_ID('{app_user}') IS NULL CREATE LOGIN [{app_user}] WITH PASSWORD = %s, CHECK_POLICY = ON",
        (app_password,),
    )
    master.close()

    conn = connect(db)
    cur = conn.cursor()
    for batch in re.split(r"^\s*GO\s*$", SCHEMA_FILE.read_text(encoding="utf-8"), flags=re.MULTILINE):
        if batch.strip():
            cur.execute(batch)
    cur.execute(f"IF DATABASE_PRINCIPAL_ID('{app_user}') IS NULL CREATE USER [{app_user}] FOR LOGIN [{app_user}]")
    cur.execute(f"IF IS_ROLEMEMBER('app_banking', '{app_user}') = 0 ALTER ROLE app_banking ADD MEMBER [{app_user}]")

    # Carga por tabla vacía: idempotente y agrega tablas nuevas (p. ej. cards) a una base existente
    seed = json.loads((settings.seed_dir / "customers.json").read_text(encoding="utf-8"))
    for table in ("customers", "accounts", "transactions", "cards"):
        cur.execute(f"SELECT COUNT(*) FROM dbo.{table}")
        rows = seed.get(table, [])
        if cur.fetchone()[0] or not rows:
            continue
        columns = list(rows[0].keys())
        cur.executemany(
            f"INSERT INTO dbo.{table} ({', '.join(columns)}) VALUES ({', '.join('%s' for _ in columns)})",
            [tuple(r[c] for c in columns) for r in rows],
        )
        print(f"Datos cargados: {len(rows)} filas en {table}")
    # Estado mutable de la demo: las tarjetas vuelven a 'activa' (Evals y demo repetibles)
    cur.execute("UPDATE dbo.cards SET status = 'activa' WHERE status <> 'activa'")
    conn.close()
    print(f"Listo en {time.perf_counter() - started:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
