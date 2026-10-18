"""Cliente de consola para probar la API sin pelear con las comillas de la terminal.

Uso (desde backend/, con el servidor levantado):
    python scripts/chat_cli.py "¿Cuál es mi saldo?"
    python scripts/chat_cli.py --stream "¿Qué requisitos tiene el DPF?"
    python scripts/chat_cli.py --thread <thread_id> "y mis movimientos?"
    python scripts/chat_cli.py --approve <thread_id> --decision approve --reviewer OP-17
    python scripts/chat_cli.py --approve <thread_id> --decision reject --comment "No corresponde"
"""

import argparse
import json
import sys

import httpx


def print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def stream(url: str, payload: dict) -> int:
    """Muestra los eventos SSE a medida que llegan."""
    with httpx.stream("POST", f"{url}/chat/stream", json=payload, timeout=120) as response:
        if response.status_code != 200:
            response.read()
            print_json(response.json())
            return 1
        event = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
                if event == "token":
                    print(data["delta"], end="", flush=True)
                elif event == "node":
                    print(f"\n  · {data['node']} {data['status']}", end="", flush=True)
                elif event == "final":
                    print("\n\n--- final ---")
                    print_json({k: data[k] for k in ("thread_id", "status", "intent", "citations", "interrupt")})
                else:
                    print(f"\n[{event}] {json.dumps(data, ensure_ascii=False)}", end="")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Cliente de prueba del asistente bancario")
    parser.add_argument("message", nargs="?", help="Mensaje del cliente")
    parser.add_argument("--customer", default="CLI-001")
    parser.add_argument("--thread", default=None, help="Continuar una conversación existente")
    parser.add_argument("--stream", action="store_true", help="Usar /chat/stream (SSE)")
    parser.add_argument("--approve", metavar="THREAD_ID", help="Decidir sobre un reclamo pendiente")
    parser.add_argument("--decision", choices=["approve", "edit", "reject"], default="approve")
    parser.add_argument("--reviewer", default="OP-17")
    parser.add_argument("--comment", default=None)
    parser.add_argument("--url", default="http://localhost:8000")
    args = parser.parse_args()

    # Forzamos UTF-8 en la salida para que las tildes se vean bien en la consola de Windows
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        if args.approve:
            body = {"decision": args.decision, "reviewer_id": args.reviewer, "comment": args.comment}
            response = httpx.post(f"{args.url}/approve/{args.approve}", json=body, timeout=120)
            print_json(response.json())
            return 0 if response.is_success else 1

        if not args.message:
            parser.error("falta el mensaje")
        payload = {"customer_id": args.customer, "message": args.message, "thread_id": args.thread}
        if args.stream:
            return stream(args.url, payload)
        response = httpx.post(f"{args.url}/chat", json=payload, timeout=120)
        print_json(response.json())
        return 0 if response.is_success else 1
    except httpx.ConnectError:
        print(f"No se pudo conectar a {args.url}. ¿Está corriendo uvicorn?", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
