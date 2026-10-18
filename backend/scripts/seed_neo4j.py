"""Carga productos, políticas, clientes, contratos y quejas en Neo4j con sus embeddings.

Uso (desde backend/, con `docker compose up -d neo4j` ya levantado):
    python scripts/seed_neo4j.py            # idempotente (MERGE)
    python scripts/seed_neo4j.py --reset    # borra todo y recarga (p. ej. al cambiar de modelo)
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # permite "import app" desde scripts/

from app.config import get_settings  # noqa: E402
from app.rag.embeddings import get_embedder  # noqa: E402
from app.rag.graph_seed import seed_graph  # noqa: E402
from app.rag.neo4j_client import get_driver  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Borra el grafo y los índices antes de cargar")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # descarga del modelo: sin ruido
    sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    print(f"Neo4j: {settings.neo4j_uri} · embeddings: {settings.embeddings_backend} ({settings.embeddings_model})")
    started = time.perf_counter()
    try:
        get_driver().verify_connectivity()
    except Exception as error:  # noqa: BLE001
        print(f"No se pudo conectar a Neo4j: {error}\n¿Ejecutaste 'docker compose up -d neo4j'?", file=sys.stderr)
        return 1

    summary = seed_graph(get_driver(), get_embedder(), settings.seed_dir, reset=args.reset)
    print(f"Listo en {time.perf_counter() - started:.1f} s → {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
