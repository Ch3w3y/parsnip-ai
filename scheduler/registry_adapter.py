"""
Scheduler-registry adapter — thin bridge between the scheduler and SourceRegistry.

Provides `run_source()` which:
  1. Looks up a source by name in the registry
  2. Gets its entry point (main_async or main)
  3. Calls it appropriately (await async, call sync)
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from ingestion.registry import SourceRegistry

logger = logging.getLogger(__name__)


async def run_source(
    registry: SourceRegistry,
    source_name: str,
    **kwargs: Any,
) -> Any:
    """
    Resolve and execute an ingestion source via the plugin registry.

    Args:
        registry: The SourceRegistry instance to look up sources from.
        source_name: The registry key (e.g. "news_api", "arxiv").
        **kwargs: Optional keyword arguments to pass to the entry point.

    Returns:
        Whatever the entry point returns (usually None for ingestion sources).

    Raises:
        KeyError: If the source name is not found in the registry.
        Exception: Re-raises any exception from the entry point.
    """
    entry = registry.get_source(source_name)
    func = entry.get_entry_point()

    if inspect.iscoroutinefunction(func):
        return await func(**kwargs)
    else:
        return func(**kwargs)