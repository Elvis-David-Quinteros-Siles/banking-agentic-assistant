"""Cliente de consola para probar /chat sin pelear con las comillas de la terminal.

Uso (desde backend/, con el servidor levantado):
    python scripts/chat_cli.py "Quiero presentar un reclamo por doble cobro"
    python scripts/chat_cli.py --thread <thread_id> "y mis movimientos?"
"""

import argparse
import json
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Envía un mensaje a POST /chat")
    parser.add_argument("message")
    parser.add_argument("--customer", default="CLI-001")
    parser.add_argument("--thread", default=None)
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()

    payload = {"customer_id": args.customer, "message": args.message, "thread_id": args.thread}
    try:
        response = httpx.post(f"{args.url}/chat", json=payload, timeout=60)
    except httpx.ConnectError:
        print(f"No se pudo conectar a {args.url}. ¿Está corriendo uvicorn?", file=sys.stderr)
        return 1

    # Forzamos UTF-8 en la salida para que las tildes se vean bien en la consola de Windows
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0 if response.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
