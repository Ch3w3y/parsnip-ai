"""
Shared LLM client — routes tool-internal LLM calls to GPU Ollama or OpenRouter.

All tool-internal LLM calls (query expansion, HyDE, classification, concept extraction)
should go through llm_call() instead of making raw httpx calls to OpenRouter. This
ensures simple tasks are routed to the local GPU when available, reducing OpenRouter costs.

Routing logic:
  - tier="simple": Routes to GPU Ollama (qwen3:8b) when GPU_LLM is enabled, else OpenRouter
  - tier="complex": Always routes to OpenRouter (too large for local GPU)
  - model= override: Uses the specified model on OpenRouter (backward compat)

Fallback: If the GPU is unavailable (gaming, OOM, network error), automatically retries
on OpenRouter so tool calls never fail silently.
"""

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)


async def llm_call(
    messages: list[dict],
    model: str | None = None,
    tier: str = "simple",
    max_tokens: int = 200,
    temperature: float = 0.4,
    response_format: dict | None = None,
    timeout: int = 30,
) -> str | dict | None:
    """Route an LLM call to GPU Ollama or OpenRouter based on tier.

    Args:
        messages: Chat messages list [{"role": "user", "content": "..."}].
        model: Explicit model override (bypasses tier routing).
        tier: "simple" routes to GPU when available; "complex" always uses OpenRouter.
        max_tokens: Maximum tokens in the response.
        temperature: Sampling temperature.
        response_format: Optional JSON format hint, e.g. {"type": "json_object"}.
        timeout: Request timeout in seconds.

    Returns:
        str: The text content of the response.
        dict: Parsed JSON if response_format was set and content is valid JSON.
        None: If the call fails on all backends.
    """

    def _parse_response(content: str) -> str | dict:
        if response_format and response_format.get("type") == "json_object":
            try:
                return json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return content
        return content

    def _build_payload(target_model: str) -> dict:
        payload = {
            "model": target_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        return payload

    gpu_url = os.environ.get("GPU_LLM_URL", "")
    gpu_model = os.environ.get("GPU_LLM_MODEL", "")
    gpu_enabled = bool(gpu_url and gpu_model)
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    # Determine target backend
    use_gpu = False
    target_model = model

    if model:
        # Explicit model override — use OpenRouter unless it matches the GPU model
        if gpu_enabled and model == gpu_model:
            use_gpu = True
    elif tier == "simple" and gpu_enabled:
        # Simple tasks → local GPU
        use_gpu = True
        target_model = gpu_model
    else:
        # Complex tasks or no GPU → OpenRouter
        target_model = model or os.environ.get(
            "DEFAULT_LLM", "google/gemini-2.0-flash-001"
        )

    # Try GPU Ollama first if applicable
    if use_gpu:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{gpu_url}/v1/chat/completions",
                    json=_build_payload(target_model),
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"].strip()
                return _parse_response(content)
        except Exception as e:
            logger.warning(f"GPU LLM call failed ({target_model}): {e}. Falling back to OpenRouter.")

    # OpenRouter (primary or fallback)
    if not openrouter_key:
        logger.error("No OPENROUTER_API_KEY and GPU unavailable")
        return None

    if not target_model:
        target_model = "google/gemini-2.0-flash-001"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "HTTP-Referer": "https://github.com/pi-agent",
                    "X-Title": "pi-agent",
                },
                json=_build_payload(target_model),
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
            return _parse_response(content)
    except Exception as e:
        logger.error(f"OpenRouter call failed ({target_model}): {e}")
        return None