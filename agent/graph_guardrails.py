"""Agent guardrails: circuit breaker, cascade fallback, and message pruning."""

import fcntl
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

# ── Circuit breaker for OpenRouter rate-limit / quota exhaustion ───────────
# When OpenRouter returns 403 (key limit), 429 (rate limit), or 402 (payment),
# we rotate to the next model in the alias fallback chain.
# NOTE: GPU Ollama is reserved for embeddings ONLY — not used for agentic tasks.
#
# State is stored in a JSON file so it is shared across worker processes.
# Writes use temp-file + os.rename for atomicity; reads use fcntl.flock for
# safety during concurrent access.

_CIRCUIT_BREAKER_PATH = os.environ.get(
    "PARSNIP_CIRCUIT_BREAKER_PATH", "/tmp/parsnip_circuit_breaker.json"
)
_OPENROUTER_COOLDOWN_SECONDS = 300  # 5 minutes


def _cleanup_stale_temp_files() -> None:
    """Remove leftover .tmp files from prior crashed atomic writes."""
    state_dir = Path(_CIRCUIT_BREAKER_PATH).parent
    try:
        for entry in state_dir.iterdir():
            name = entry.name
            base = Path(_CIRCUIT_BREAKER_PATH).name
            if name.startswith(base) and ".tmp." in name:
                try:
                    entry.unlink()
                    logger.debug(f"Cleaned stale temp file: {entry}")
                except OSError:
                    pass
    except OSError:
        pass


def _trip_circuit() -> None:
    """Atomically write circuit-breaker state to disk (tripped=True)."""
    _cleanup_stale_temp_files()
    payload = json.dumps({"tripped": True, "tripped_at": time.time()})
    state_path = Path(_CIRCUIT_BREAKER_PATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to temp file in same directory, then rename
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp", prefix=state_path.name + ".", dir=state_path.parent
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, _CIRCUIT_BREAKER_PATH)
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    logger.warning("OpenRouter circuit breaker TRIPPED — rotating to fallback model")


def _reset_circuit() -> None:
    """Delete the circuit-breaker state file to reset the circuit."""
    try:
        os.unlink(_CIRCUIT_BREAKER_PATH)
    except FileNotFoundError:
        pass
    logger.info("OpenRouter circuit breaker RESET")


def _circuit_is_open() -> bool:
    """Check if circuit is tripped and cooldown has not expired.

    Reads the state file under a shared lock.  If the cooldown has elapsed,
    auto-resets the circuit by deleting the file.
    """
    _cleanup_stale_temp_files()

    state_path = Path(_CIRCUIT_BREAKER_PATH)
    if not state_path.exists():
        return False

    try:
        with open(_CIRCUIT_BREAKER_PATH, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                raw = f.read()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError, ValueError):
        # Corrupt or unreadable file — treat as closed and clean up
        try:
            os.unlink(_CIRCUIT_BREAKER_PATH)
        except OSError:
            pass
        return False

    if not data.get("tripped"):
        return False

    tripped_at = data.get("tripped_at")
    if tripped_at is None:
        return False

    elapsed = time.time() - tripped_at
    if elapsed >= _OPENROUTER_COOLDOWN_SECONDS:
        # Auto-reset after cooldown
        _reset_circuit()
        logger.info("OpenRouter circuit breaker auto-RESET after cooldown")
        return False

    return True


def _is_rate_limit_error(e: Exception) -> bool:
    """Detect OpenRouter rate-limit / quota errors from langchain/openai exceptions."""
    msg = str(e).lower()
    codes = ["403", "429", "402", "key limit exceeded", "rate limit", "quota",
             "insufficient_quota", "payment_required", "limit exceeded"]
    return any(c in msg for c in codes)


def _get_cascading_fallbacks(current_model: str, current_tier: str) -> list[str]:
    """Build a cascading fallback list: same-tier → mid-tier → low/free-tier (GPU).

    Each tier has exactly one model. GPU low-tier is the final safety net.
    """
    from config import TIER_ALIASES, get_settings

    model_aliases = get_settings().model_aliases
    fallbacks: list[str] = []

    # 1. If high tier, add mid-tier model
    if current_tier == "high":
        mid_alias = TIER_ALIASES.get("mid", "smart")
        mid_chain = model_aliases.get(mid_alias, [])
        if mid_chain and mid_chain[0] != current_model:
            fallbacks.append(mid_chain[0])

    # 2. Add low/free-tier model (OpenRouter)
    low_alias = TIER_ALIASES.get("low", "fast")
    low_chain = model_aliases.get(low_alias, [])
    if low_chain and low_chain[0] != current_model and low_chain[0] not in fallbacks:
        fallbacks.append(low_chain[0])

    return fallbacks


def _try_gpu_fallback(messages, tools, streaming):
    """Final fallback to GPU Ollama for low-tier tasks."""
    from config import get_settings
    settings = get_settings()
    if not settings.gpu_llm_enabled or not settings.gpu_llm_model:
        return None
    from langchain_openai import ChatOpenAI
    logger.info(f"Trying GPU fallback: {settings.gpu_llm_model}")
    fallback_llm = ChatOpenAI(
        model=settings.gpu_llm_model,
        base_url=f"{settings.gpu_llm_url}/v1",
        api_key="not-needed",
        streaming=streaming,
    )
    if tools:
        fallback_llm = fallback_llm.bind_tools(tools)
    try:
        return fallback_llm.invoke(messages)
    except Exception as e:
        logger.warning(f"GPU fallback failed: {e}")
        return None


def _invoke_with_fallback(llm, messages, tools: list | None = None, tier: str = "mid"):
    """Invoke LLM; on failure/rate-limit, cascade down model tiers using the hybrid router.

    Order: same-tier → mid-tier → low/free-tier (Local GPU/Cloud).
    """
    from config import get_settings
    settings = get_settings()
    current_model = getattr(llm, "model_name", "")
    fallback_chain = _get_cascading_fallbacks(current_model, tier)
    streaming = getattr(llm, "streaming", True)

    # If circuit is open, jump straight to fallbacks (skip primary model entirely)
    if _circuit_is_open():
        logger.warning("Primary model circuit is OPEN. Skipping to fallbacks.")
        for fallback_id in fallback_chain:
            logger.info(f"Circuit open — trying fallback model {fallback_id}")
            from graph_llm import _get_llm
            fallback_llm = _get_llm(model=fallback_id, streaming=streaming)
            if tools:
                fallback_llm = fallback_llm.bind_tools(tools)
            try:
                return fallback_llm.invoke(messages)
            except Exception as e2:
                logger.warning(f"Fallback {fallback_id} failed: {e2}")
                continue
        
        # Final safety net: GPU Ollama
        gpu_result = _try_gpu_fallback(messages, tools, streaming)
        if gpu_result is not None:
            return gpu_result
            
        raise RuntimeError(
            "Model circuit is open and all fallback options (including GPU) failed. "
            "Please check your Ollama Cloud subscription or local GPU status."
        )

    try:
        return llm.invoke(messages)
    except Exception as e:
        if _is_rate_limit_error(e):
            _trip_circuit()
            logger.warning(f"Primary model blocked ({e}). Cascading through fallback chain ...")
            for fallback_id in fallback_chain:
                logger.info(f"Retrying with fallback model {fallback_id}")
                from graph_llm import _get_llm
                fallback_llm = _get_llm(model=fallback_id, streaming=streaming)
                if tools:
                    fallback_llm = fallback_llm.bind_tools(tools)
                try:
                    result = fallback_llm.invoke(messages)
                    logger.info(f"Fallback succeeded with {fallback_id}")
                    return result
                except Exception as e2:
                    logger.warning(f"Fallback {fallback_id} failed: {e2}")
                    continue
            
            # Final safety net: GPU Ollama
            gpu_result = _try_gpu_fallback(messages, tools, streaming)
            if gpu_result is not None:
                return gpu_result
            logger.error("All fallback models exhausted; GPU fallback also failed")
        raise


def _prune_messages(messages: list[BaseMessage], max_tool_chars: int = 12000) -> list[BaseMessage]:
    """Prune long tool outputs and history to keep context manageable (~10-15k tokens)."""
    pruned = []
    if not messages:
        return []

    # Always keep system prompt if first
    start_idx = 0
    if isinstance(messages[0], SystemMessage):
        pruned.append(messages[0])
        start_idx = 1

    # Keep the rest, but truncate giant tool messages
    for msg in messages[start_idx:]:
        if isinstance(msg, ToolMessage) and len(str(msg.content)) > max_tool_chars:
            new_content = str(msg.content)[:max_tool_chars] + "\n\n[... output truncated to save context ...]"
            msg = ToolMessage(
                content=new_content,
                tool_call_id=msg.tool_call_id,
                status=getattr(msg, "status", "success"),
            )
        pruned.append(msg)

    # If history is getting very long, drop middle messages but keep context
    # (Keep System + First User Msg + Last 20 messages, aligned to tool boundaries)
    if len(pruned) > 25:
        # Find first human message to keep head
        first_user_idx = -1
        for i, m in enumerate(pruned):
            if isinstance(m, HumanMessage):
                first_user_idx = i
                break
        
        # Keep head: system + first user
        head = pruned[:first_user_idx + 1] if first_user_idx != -1 else pruned[:1]
        
        # Take tail and align: must start with an AIMessage or HumanMessage, 
        # NOT a ToolMessage or a paired AIMessage with tool_calls.
        tail_size = 20
        tail_start_idx = len(pruned) - tail_size
        
        # Walk backward to find a clean break point (HumanMessage or non-tool AIMessage)
        while tail_start_idx < len(pruned) - 1:
            m = pruned[tail_start_idx]
            if isinstance(m, HumanMessage):
                break
            if isinstance(m, AIMessage) and not m.tool_calls:
                break
            tail_start_idx += 1
            
        tail = pruned[tail_start_idx:]
        pruned = head + [HumanMessage(content="[... older history omitted to save context ...]")] + tail

    return pruned
