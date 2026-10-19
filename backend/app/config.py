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
    router_backend: Literal["rules", "llm", "lora", "cascade"] = "rules"
    router_min_confidence: float = 0.6
    # Cascada: las reglas resuelven solas estas intenciones (precisión 100% en train+val con soporte
    # >= 30, finetune/calibrate.py); el resto pasa al LoRA. Los reclamos van siempre al modelo.
    cascade_rule_intents: tuple[str, ...] = ("accion_tarjeta", "consulta_cuenta", "fuera_de_alcance")
    # Umbral de aclaración del LoRA: su confianza tiene otra escala que la de las reglas. Con 0.7, en
    # el test (plantillas nunca vistas) la cascada no responde ningún error y aclara el 2%
    lora_min_confidence: float = 0.7
    # Router LoRA (Fase 4): Qwen2.5-0.5B + adaptador entrenado con finetune/train_lora.py
    lora_base_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    lora_adapter_path: Path = REPO_DIR / "finetune" / "adapters" / "qwen2.5-0.5b-intent-lora"
    lora_device: Literal["auto", "cuda", "cpu"] = "auto"

    # --- Agentes ----------------------------------------------------------
    react_max_iterations: int = 4  # tope de vueltas del loop ReAct (control de costo/latencia)
    knowledge_backend: Literal["keyword", "neo4j", "pgvector", "llamaindex"] = "keyword"
    knowledge_top_k: int = 4
    knowledge_min_score: float = 0.15  # BM25 normalizado: por debajo, el agente se abstiene

    # --- Graph RAG (Neo4j) -----------------------------------------------
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "bankdemo123"  # noqa: S105 - solo desarrollo; en producción viene de .env o de un gestor de secretos
    embeddings_backend: Literal["fastembed", "hash"] = "fastembed"
    embeddings_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    neo4j_min_similarity: float = 0.65  # coseno mínimo del mejor documento para no abstenerse
    rrf_k: int = 60  # constante de Reciprocal Rank Fusion

    # --- RAG con pgvector (alternativa relacional a Neo4j) ------------------
    pgvector_dsn: str = "postgresql://bank:bankdemo123@localhost:5434/conocimiento"
    complaint_high_priority_amount: float = 1000.0  # monto (Bs) desde el cual la queja es prioridad alta

    # --- Core bancario: sqlite (desarrollo/CI) | sqlserver (on-premise) --------
    core_backend: Literal["sqlite", "sqlserver"] = "sqlite"
    sqlserver_host: str = "localhost"
    sqlserver_port: int = 1433
    sqlserver_database: str = "CoreBancario"
    # Login de APLICACIÓN (solo EXECUTE sobre procedimientos), no el administrador
    sqlserver_user: str = "bank_app"
    sqlserver_password: str = "BankApp#2026"  # noqa: S105 - solo desarrollo (login de aplicación)

    # --- Persistencia -----------------------------------------------------
    sqlite_path: Path = BACKEND_DIR / "data" / "bank.db"
    checkpoints_path: Path = BACKEND_DIR / "data" / "checkpoints.db"
    # Analítica operativa (KPIs por caso de uso + cola HITL). Sin texto de clientes.
    analytics_path: Path = BACKEND_DIR / "data" / "analytics.db"
    # Autenticación del canal (app/api/auth.py). "jwt": la identidad sale del token; "off": contrato
    # anterior con customer_id en el cuerpo (solo desarrollo y tests)
    auth_mode: Literal["jwt", "off"] = "jwt"
    jwt_secret: str | None = None  # sin definir: secreto aleatorio por proceso (demo)
    jwt_ttl_minutes: int = 30
    demo_auth_enabled: bool = True  # POST /auth/demo-token para los clientes ficticios
    # Producción: IdP del banco (OIDC). Con JWKS se verifican tokens RS256 y se apaga el emisor de demo
    oidc_jwks_url: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    # Con AUTH_MODE=off: si se define, /operator/* y /approve exigen la cabecera X-Operator-Token
    operator_token: str | None = None
    # Demo pública: al arrancar con analítica vacía, genera tráfico de ejemplo para el dashboard
    demo_seed_traffic: bool = False
    # Demo pública: cada N minutos las tarjetas vuelven al estado de la semilla (0 = nunca)
    demo_reset_minutes: int = 0
    # Servidor MCP: cliente de la sesión. MCP_CUSTOMER_ID solo con AUTH_MODE=off (desarrollo)
    mcp_customer_id: str = "CLI-001"
    mcp_access_token: str | None = None  # con AUTH_MODE=jwt: token del cliente (demo-token o IdP)
    # Observabilidad con Langfuse self-hosted (orchestration/observability/). Desactivado por defecto
    langfuse_enabled: bool = False
    langfuse_base_url: str = "http://localhost:3100"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    # Turnos por minuto por cliente en /chat, /chat/stream y /approve (0 = sin límite)
    rate_limit_per_minute: int = 0
    seed_dir: Path = REPO_DIR / "data" / "seed"
    # Build del frontend (npm run build). Si existe, FastAPI lo sirve en "/" (imagen de demo pública)
    frontend_dist: Path = REPO_DIR / "frontend" / "dist"


@lru_cache
def get_settings() -> Settings:
    """Devuelve una instancia única de configuración (cacheada)."""
    return Settings()
