"""LLM client construction and model resolution."""

import logging
import os

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


def _get_llm(model: str | None = None, streaming: bool = True) -> ChatOpenAI:
    from config import get_settings

    settings = get_settings()
    selected = settings.require_model(model or settings.default_llm)

    # Route to Ollama Cloud if model ID ends in :cloud
    if selected.endswith(":cloud") and settings.ollama_api_key:
        cloud_base = settings.ollama_cloud_url.rstrip("/")
        if not cloud_base.endswith("/v1"):
            cloud_base = f"{cloud_base}/v1"
        return ChatOpenAI(
            model=selected,
            base_url=cloud_base,
            api_key=settings.ollama_api_key,
            streaming=streaming,
        )

    # All other non-cloud models route to local GPU Ollama if enabled
    if settings.gpu_llm_enabled:
        return ChatOpenAI(
            model=selected,
            base_url=f"{settings.gpu_llm_url}/v1",
            api_key="ollama",
            streaming=streaming,
        )

    # If GPU is disabled, fall back to OpenRouter ONLY if a key exists
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        return ChatOpenAI(
            model=selected,
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_key,
            streaming=streaming,
            default_headers={
                "HTTP-Referer": "https://github.com/pi-agent",
                "X-Title": "pi-agent",
            },
        )

    raise RuntimeError(
        f"No LLM backend available for model '{selected}'. "
        "Please enable GPU_LLM or provide OLLAMA_API_KEY/OPENROUTER_API_KEY."
    )
