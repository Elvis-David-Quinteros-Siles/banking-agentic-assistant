"""Configuración central de la aplicación (leída desde variables de entorno / .env)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    # Busca el .env en la raíz del repo o en backend/
    model_config = SettingsConfigDict(env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"), extra="ignore")

    app_name: str = "banking-agentic-assistant"
    app_version: str = "0.4.0"
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # --- LLM base ---------------------------------------------------------
    # "fake" = sin LLM: cada nodo usa su ruta determinista (CI, tests, demo sin GPU)
    llm_provider: Literal["fake", "ollama", "openai", "anthropic"] = "fake"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 512
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    # Los prompts más largos medidos rondan 600 tokens (evals/estimate_cost.py): 2048 de contexto
    # alcanza con margen y reduce a la mitad la caché KV frente al default de 4096 (VRAM de 4 GB)
    ollama_num_ctx: int = 2048
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-haiku-5-5"

    # --- Router -----------------------------------------------------------
    router_backend: Literal["rules", "llm", "lora"] = "rules"
    router_min_confidence: float = 0.6
    # Router LoRA (Fase 4): Qwen2.5-0.5B + adaptador entrenado con finetune/train_lora.py
    lora_base_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    lora_adapter_path: Path = REPO_DIR / "finetune" / "adapters" / "qwen2.5-0.5b-intent-lora"
    lora_device: Literal["auto", "cuda", "cpu"] = "auto"

    # --- Agentes ----------------------------------------------------------
    react_max_iterations: int = 4  # tope de vueltas del loop ReAct (control de costo/latencia)
    knowledge_backend: Literal["keyword", "neo4j"] = "keyword"
    knowledge_top_k: int = 4
    knowledge_min_score: float = 0.15  # BM25 normalizado: por debajo, el agente se abstiene

    # --- Graph RAG (Neo4j) -----------------------------------------------
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "bankdemo123"
    embeddings_backend: Literal["fastembed", "hash"] = "fastembed"
    embeddings_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    neo4j_min_similarity: float = 0.65  # coseno mínimo del mejor documento para no abstenerse
    rrf_k: int = 60  # constante de Reciprocal Rank Fusion
    complaint_high_priority_amount: float = 1000.0  # monto (Bs) desde el cual la queja es prioridad alta

    # --- Persistencia -----------------------------------------------------
    sqlite_path: Path = BACKEND_DIR / "data" / "bank.db"
    checkpoints_path: Path = BACKEND_DIR / "data" / "checkpoints.db"
    seed_dir: Path = REPO_DIR / "data" / "seed"


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia única de configuración (cacheada)."""
    return Settings()
