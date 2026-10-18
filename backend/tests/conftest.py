"""Configuración de tests: modo determinista y bases de datos temporales.

Las variables se fijan ANTES de importar la app, porque la configuración se cachea.
"""

import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="bank-tests-"))
os.environ.update(
    {
        "APP_ENV": "test",
        "LLM_PROVIDER": "fake",
        "ROUTER_BACKEND": "rules",
        "SQLITE_PATH": str(_TMP / "bank.db"),
        "CHECKPOINTS_PATH": str(_TMP / "checkpoints.db"),
        "ANALYTICS_PATH": str(_TMP / "analytics.db"),
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.sqlite import init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def seeded_db(settings) -> Path:
    init_db(settings.sqlite_path, settings.seed_dir / "customers.json")
    return settings.sqlite_path


@pytest.fixture(scope="session")
def client(seeded_db):
    with TestClient(app) as c:
        yield c
