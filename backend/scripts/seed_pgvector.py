"""Carga productos y políticas en PostgreSQL + pgvector con sus embeddings.

Uso (desde backend/, con `docker compose up -d pgvector`):
    python scripts/seed_pgvector.py            # idempotente (upsert)
    python scripts/seed_pgvector.py --reset    # borra las tablas y recarga
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.rag.embeddings import get_embedder  # noqa: E402
from app.rag.pgvector_seed import seed_pgvector  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    started = time.perf_counter()
    try:
        summary = seed_pgvector(settings.pgvector_dsn, get_embedder(), settings.seed_dir, reset=args.reset)
    except Exception as error:  # noqa: BLE001
        print(f"No se pudo cargar pgvector: {error}\n¿Ejecutaste 'docker compose up -d pgvector'?", file=sys.stderr)
        return 1
    print(f"Listo en {time.perf_counter() - started:.1f} s → {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
