import os
import json
import logging
import httpx
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from functools import lru_cache

logger = logging.getLogger(__name__)

def _model_chain_from_env(name: str, default: str = "") -> list[str]:
    """Read a comma-separated model fallback chain from the environment."""
    raw = os.environ.get(name, default)
    chain = [item.strip() for item in raw.split(",") if item.strip()]
    return chain


# Model alias registry — stable app names mapped to provider IDs. Environment
# variables are the source of truth for deployment-specific model choices.
# Values may be comma-separated fallback chains.
MODEL_ALIASES = {
    "fast": _model_chain_from_env("FAST_MODEL"),
    "smart": _model_chain_from_env("SMART_MODEL"),
    "reasoning": _model_chain_from_env("REASONING_MODEL"),
    "graph": _model_chain_from_env("GRAPH_MODEL"),
    "classifier": _model_chain_from_env("CLASSIFIER_MODEL"),
}

# Alias → complexity tier mapping
TIER_ALIASES = {
    "low": "fast",
    "mid": "smart",
    "high": "reasoning",
}


class Settings(BaseSettings):
    database_url: str
    openrouter_api_key: str
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_api_key: str = ""  # For Ollama Cloud
    ollama_cloud_url: str = "https://ollama.com/v1"
    embed_model: str = "mxbai-embed-large"

    # LLM routing — use alias names (fast/smart/reasoning) or raw provider IDs
    default_llm: str = "smart"
    research_llm: str = "reasoning"
    llm_provider: str = "openrouter"  # openrouter | openai_compat

    # Alias targets. .env is the source of truth; these fields exist so aliases
    # can be introspected and resolved consistently through Settings.
    fast_model: str = ""
    smart_model: str = ""
    reasoning_model: str = ""
    graph_model: str = ""
    classifier_model: str = ""

    # Optional OpenAI-compatible backend for non-GPU LLM routing
    openai_compat_base_url: str = ""
    openai_compat_api_key: str = ""

    # GPU LLM — when set, low-complexity and classifier tasks route to the
    # GPU Ollama instance instead of OpenRouter. Empty/absent = all OpenRouter.
    # Toggle: set GPU_LLM_MODEL to enable, comment out or empty to disable.
    gpu_llm_url: str = ""
    gpu_llm_model: str = ""

    # GPU mid-tier model — when set, mid-complexity tasks route to this
    # GPU model instead of OpenRouter. Saves costs on moderate queries.
    gpu_mid_model: str = ""

    # Optional search tool keys
    tavily_api_key: str = ""
    brave_api_key: str = ""
    github_token: str = ""

    # GCS storage
    gcs_bucket: str = ""
    gcs_project_id: str = "agentic-storage"

    log_level: str = "info"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def gpu_llm_enabled(self) -> bool:
        return bool(self.gpu_llm_url and self.gpu_llm_model)

    @property
    def gpu_mid_enabled(self) -> bool:
        return bool(self.gpu_llm_url and self.gpu_mid_model)

    @property
    def openai_compat_enabled(self) -> bool:
        return (
            self.llm_provider == "openai_compat"
            and bool(self.openai_compat_base_url and self.openai_compat_api_key)
        )

    def resolve_model(self, alias_or_id: str) -> str:
        """Resolve a model alias to an actual provider ID.

        If the input already looks like a provider ID (contains '/'), return it.
        Otherwise, look it up in the env-backed alias registry. Falls back to
        the first entry in the chain.
        """
        if "/" in alias_or_id:
            return alias_or_id
        aliases = self.model_aliases
        if alias_or_id in aliases:
            chain = aliases[alias_or_id]
            return chain[0] if chain else ""
        # Not an alias — could be a raw model name (e.g. gpt-oss:20b from GPU LLM config)
        return alias_or_id

    @property
    def model_aliases(self) -> dict[str, list[str]]:
        return {
            "fast": _model_chain_from_env("FAST_MODEL", self.fast_model),
            "smart": _model_chain_from_env("SMART_MODEL", self.smart_model),
            "reasoning": _model_chain_from_env("REASONING_MODEL", self.reasoning_model),
            "graph": _model_chain_from_env("GRAPH_MODEL", self.graph_model),
            "classifier": _model_chain_from_env("CLASSIFIER_MODEL", self.classifier_model),
        }

    def resolve_tier(self, tier: str) -> str:
        """Resolve a complexity tier (low/mid/high) to a model ID.

        When GPU LLM is enabled, 'low' tier routes to the GPU Ollama instance.
        When GPU_MID_MODEL is set, 'mid' tier routes to that GPU model.
        """
        if self.gpu_llm_enabled and tier == "low":
            return self.gpu_llm_model
        if self.gpu_mid_enabled and tier == "mid":
            return self.gpu_mid_model
        alias = TIER_ALIASES.get(tier, "fast")
        return self.resolve_model(alias)

    def require_model(self, alias_or_id: str) -> str:
        model = self.resolve_model(alias_or_id)
        if not model:
            raise RuntimeError(
                f"No model configured for '{alias_or_id}'. Set the corresponding "
                "model alias in .env, e.g. FAST_MODEL, SMART_MODEL, or REASONING_MODEL."
            )
        return model

    def is_gpu_model(self, model_id: str) -> bool:
        """Check if a model ID should be routed to GPU Ollama."""
        return self.gpu_llm_enabled and model_id in (self.gpu_llm_model, self.gpu_mid_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()


async def validate_model(model_id: str, api_key: str) -> bool:
    """Check if a model ID is valid on OpenRouter."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 1,
                },
            )
            return r.status_code != 400
        except Exception:
            return False


async def resolve_with_fallback(alias_or_id: str, api_key: str) -> str:
    """Resolve alias and try fallback chain until a valid model is found."""
    settings = get_settings()
    if "/" in alias_or_id:
        # Already a provider ID, try it directly
        if await validate_model(alias_or_id, api_key):
            return alias_or_id
        logger.warning(f"Model {alias_or_id} unavailable, no fallback chain")
        return alias_or_id

    chain = settings.model_aliases.get(alias_or_id, [alias_or_id])
    for model_id in chain:
        if await validate_model(model_id, api_key):
            if model_id != chain[0]:
                logger.info(f"Model {chain[0]} unavailable, using fallback {model_id}")
            return model_id

    logger.error(f"All models in chain unavailable for alias '{alias_or_id}': {chain}")
    return chain[0]
