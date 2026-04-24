"""Named connection pool registry for psycopg async pools.

Provides a simple dict-based registry of `psycopg_pool.AsyncConnectionPool`
instances keyed by name.  This allows different parts of the agent (KB tools,
Joplin sync, etc.) to share named pools instead of opening fresh connections
per call.

Usage::

    from tools.db_pool import init_pool, get_pool, close_all

    # At app startup (lifespan):
    await init_pool("agent_kb", settings.database_url)

    # In any tool/module:
    pool = get_pool("agent_kb")
    async with pool.connection() as conn:
        await conn.execute("SELECT 1")

    # At shutdown:
    await close_all()
"""

import logging
from typing import Optional

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

# ── Module-level registry ────────────────────────────────────────────────────

_pools: dict[str, AsyncConnectionPool] = {}


# ── Public API ──────────────────────────────────────────────────────────────


async def init_pool(
    name: str,
    dsn: str,
    *,
    min_size: int = 2,
    max_size: int = 10,
    open_timeout: Optional[float] = 30.0,
    **pool_kwargs,
) -> AsyncConnectionPool:
    """Create, open, and register a named async connection pool.

    If a pool with the same *name* already exists it is closed and replaced.

    Args:
        name:    Logical name for the pool (e.g. ``"agent_kb"``, ``"joplin"``).
        dsn:     PostgreSQL connection string.
        min_size: Minimum number of connections kept idle in the pool.
        max_size: Maximum number of connections the pool may hold.
        open_timeout: Seconds to wait for ``pool.open()`` before raising.
        **pool_kwargs: Extra keyword arguments forwarded to
            ``AsyncConnectionPool`` (e.g. ``kwargs`` for connection params).

    Returns:
        The newly created ``AsyncConnectionPool`` instance.

    Raises:
        Any exception raised by ``pool.open()`` (e.g. connection errors).
    """
    # Close existing pool with the same name (if any)
    existing = _pools.get(name)
    if existing is not None:
        try:
            await existing.close()
            logger.info("Closed existing pool '%s' before re-initialisation.", name)
        except Exception:
            logger.warning("Error closing existing pool '%s', continuing.", name, exc_info=True)

    conn_kwargs = pool_kwargs.pop("kwargs", {})
    conn_kwargs.setdefault("row_factory", dict_row)

    pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=min_size,
        max_size=max_size,
        open=False,
        kwargs=conn_kwargs,
        **pool_kwargs,
    )

    await pool.open()
    _pools[name] = pool
    logger.info(
        "Pool '%s' opened (min=%d, max=%d, dsn=%s…).",
        name, min_size, max_size, dsn[:40],
    )
    return pool


def get_pool(name: str) -> AsyncConnectionPool:
    """Return a registered pool by name.

    Raises:
        ValueError: If no pool has been registered under *name*.
    """
    pool = _pools.get(name)
    if pool is None:
        raise ValueError(
            f"No pool named '{name}'. Available pools: {list(_pools)}"
        )
    return pool


async def close_all() -> None:
    """Close and remove every registered pool (for app shutdown)."""
    names = list(_pools.keys())
    for name in names:
        pool = _pools.pop(name, None)
        if pool is not None:
            try:
                await pool.close()
                logger.info("Pool '%s' closed.", name)
            except Exception:
                logger.warning("Error closing pool '%s'.", name, exc_info=True)
    logger.info("All connection pools closed.")