"""Conexión a Neo4j (driver oficial, protocolo Bolt) y helpers de salud."""

import logging
from functools import lru_cache

from neo4j import Driver, GraphDatabase

from app.config import get_settings

logger = logging.getLogger("neo4j")


@lru_cache
def get_driver() -> Driver:
    settings = get_settings()
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
        connection_timeout=3,
        max_connection_lifetime=300,
        # Solo avisos relevantes en los logs (no los informativos de "índice ya existe")
        notifications_min_severity="WARNING",
    )


def neo4j_available() -> bool:
    """True si Neo4j responde y ya tiene el grafo cargado (hay al menos una Politica)."""
    try:
        records, _, _ = get_driver().execute_query("MATCH (p:Politica) RETURN count(p) AS n")
        return records[0]["n"] > 0
    except Exception as error:  # noqa: BLE001
        logger.warning("Neo4j no disponible: %s", error)
        return False
