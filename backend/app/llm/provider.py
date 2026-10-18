"""Factory del LLM base: ollama (on-premise, por defecto en demo) | openai | anthropic | fake.

Con "fake" se devuelve None y cada nodo usa su ruta determinista. Esa misma ruta es el
fallback cuando el LLM falla (timeout, Ollama apagado, JSON inválido): el sistema se
degrada en calidad de redacción, pero sigue respondiendo con datos correctos.
"""

from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from app.config import get_settings


@lru_cache
def get_chat_model() -> BaseChatModel | None:
    settings = get_settings()
    provider = settings.llm_provider

    if provider == "fake":
        return None

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=settings.llm_temperature,
            num_predict=settings.llm_max_tokens,
            num_ctx=settings.ollama_num_ctx,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(  # type: ignore[call-arg]  # max_tokens es alias de pydantic: válido en runtime
            model=settings.openai_model,
            api_key=SecretStr(settings.openai_api_key) if settings.openai_api_key else None,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout=30,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(  # type: ignore[call-arg]  # model/max_tokens son alias de pydantic
            model=settings.anthropic_model,
            api_key=SecretStr(settings.anthropic_api_key or ""),
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout=30,
        )

    raise ValueError(f"LLM_PROVIDER no soportado: {provider}")


def require_llm() -> BaseChatModel:
    """El LLM configurado, o un error claro si no hay (los nodos lo capturan y usan su ruta determinista)."""
    llm = get_chat_model()
    if llm is None:
        raise RuntimeError("No hay LLM configurado (LLM_PROVIDER=fake)")
    return llm


def model_name() -> str:
    """Nombre del modelo activo (para el cálculo de costo y los logs)."""
    settings = get_settings()
    return {
        "ollama": settings.ollama_model,
        "openai": settings.openai_model,
        "anthropic": settings.anthropic_model,
    }.get(settings.llm_provider, "fake")
