"""Configuración central de la aplicación (leída desde variables de entorno / .env)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Busca el .env en la raíz del repo o en backend/
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_name: str = "banking-agentic-assistant"
    app_version: str = "0.1.0"
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # Proveedor del LLM base (se utiliza a partir de la Fase 2)
    llm_provider: Literal["fake", "openai", "anthropic", "ollama"] = "fake"

    # Backend del router: en la Fase 1 solo existe "rules"
    router_backend: Literal["rules", "llm", "lora"] = "rules"
    router_min_confidence: float = 0.6


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia única de configuración (cacheada)."""
    return Settings()
