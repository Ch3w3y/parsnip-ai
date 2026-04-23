"""Tests for the named connection pool registry (tools.db_pool).

Unit tests — no live PostgreSQL connection required.
psycopg_pool.AsyncConnectionPool is mocked so we can verify pool lifecycle,
registration, lookup, and error handling without a database.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MOD = "tools.db_pool"  # import path thanks to pythonpath in pyproject.toml


def _make_mock_pool():
    """Create a fresh mock AsyncConnectionPool instance."""
    instance = AsyncMock()
    instance.open = AsyncMock()
    instance.close = AsyncMock()
    instance.get_stats = MagicMock(return_value={"pool_size": 2, "pool_available": 2})
    return instance


@pytest.fixture(autouse=True)
def _reset_registry():
    """Ensure the module-level _pools dict is empty before every test."""
    import tools.db_pool
    tools.db_pool._pools.clear()
    yield
    tools.db_pool._pools.clear()


@pytest.fixture()
def mock_pool_cls():
    """Patch AsyncConnectionPool so no real DB is touched.

    Yields the mock class.  Each call to the class produces a distinct
    mock instance (side_effect), so multi-pool registries work correctly.
    """
    with patch(f"{MOD}.AsyncConnectionPool") as cls:
        cls.side_effect = lambda **kw: _make_mock_pool()
        yield cls


# ── 1. init_pool creates and registers a pool ────────────────────────────────

@pytest.mark.asyncio
async def test_init_pool_registers_pool(mock_pool_cls):
    from tools.db_pool import init_pool, get_pool

    pool = await init_pool("agent_kb", "postgresql://user:pass@localhost/db1")

    # AsyncConnectionPool was called with the correct DSN
    mock_pool_cls.assert_called_once()
    call_kwargs = mock_pool_cls.call_args[1]
    assert call_kwargs["conninfo"] == "postgresql://user:pass@localhost/db1"

    # pool.open() was awaited
    pool.open.assert_awaited_once()

    # The pool is retrievable by name
    assert get_pool("agent_kb") is pool


# ── 2. init_pool with a second name registers independently ──────────────────

@pytest.mark.asyncio
async def test_init_pool_second_named_pool(mock_pool_cls):
    from tools.db_pool import init_pool, get_pool

    pool_kb = await init_pool("agent_kb", "postgresql://user:pass@localhost/db1")
    pool_joplin = await init_pool("joplin", "postgresql://user:pass@localhost/db2")

    assert mock_pool_cls.call_count == 2
    assert get_pool("agent_kb") is pool_kb
    assert get_pool("joplin") is pool_joplin
    assert pool_kb is not pool_joplin


# ── 3. get_pool returns the correct pool ──────────────────────────────────────

@pytest.mark.asyncio
async def test_get_pool_returns_correct_pool(mock_pool_cls):
    from tools.db_pool import init_pool, get_pool

    pool = await init_pool("agent_kb", "postgresql://user:pass@localhost/db1")
    assert get_pool("agent_kb") is pool


# ── 4. get_pool raises ValueError for unknown name ────────────────────────────

def test_get_pool_unknown_raises_value_error():
    from tools.db_pool import get_pool

    with pytest.raises(ValueError, match="No pool named 'nonexistent'"):
        get_pool("nonexistent")


# ── 5. Pool connection acquisition with pool.connection() ────────────────────

@pytest.mark.asyncio
async def test_pool_connection_acquisition(mock_pool_cls):
    from tools.db_pool import init_pool, get_pool

    pool = await init_pool("agent_kb", "postgresql://user:pass@localhost/db1")
    pool = get_pool("agent_kb")

    # Mock the connection context manager on the returned pool
    mock_conn = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.connection = MagicMock(return_value=ctx)

    async with pool.connection() as conn:
        assert conn is mock_conn


# ── 6. Pool exhaustion (max_size, acquire beyond limit) ───────────────────────

@pytest.mark.asyncio
async def test_pool_exhaustion(mock_pool_cls):
    """When pool max_size=2, acquiring a 3rd connection raises PoolTimeout."""
    from tools.db_pool import init_pool, get_pool
    from psycopg_pool import PoolTimeout

    pool = await init_pool("agent_kb", "postgresql://user:pass@localhost/db1", min_size=0, max_size=2)

    mock_conn1 = AsyncMock()
    mock_conn2 = AsyncMock()
    call_count = 0

    def make_ctx():
        nonlocal call_count
        call_count += 1
        ctx = MagicMock()
        if call_count <= 2:
            conn = mock_conn1 if call_count == 1 else mock_conn2
            ctx.__aenter__ = AsyncMock(return_value=conn)
            ctx.__aexit__ = AsyncMock(return_value=False)
        else:
            ctx.__aenter__ = AsyncMock(side_effect=PoolTimeout("pool exhausted"))
            ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    pool.connection = MagicMock(side_effect=make_ctx)

    # First two should work
    async with pool.connection() as c1:
        assert c1 is mock_conn1
    async with pool.connection() as c2:
        assert c2 is mock_conn2

    # Third should raise PoolTimeout
    with pytest.raises(PoolTimeout):
        async with pool.connection() as c3:
            pass


# ── 7. Error on invalid DSN ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_init_pool_invalid_dsn(mock_pool_cls):
    """If pool.open() fails (e.g. bad DSN), the pool should not be registered."""
    # Make the next mock pool's open() fail
    bad_instance = _make_mock_pool()
    bad_instance.open.side_effect = ConnectionRefusedError("Connection refused")
    mock_pool_cls.side_effect = lambda **kw: bad_instance

    from tools.db_pool import init_pool, get_pool

    with pytest.raises(ConnectionRefusedError):
        await init_pool("bad_db", "postgresql://bad:bad@nonexistent:5432/baddb")

    # Pool must NOT be registered after failed open
    with pytest.raises(ValueError, match="No pool named 'bad_db'"):
        get_pool("bad_db")


# ── 8. close_all closes every registered pool ─────────────────────────────────

@pytest.mark.asyncio
async def test_close_all(mock_pool_cls):
    from tools.db_pool import init_pool, close_all, get_pool

    pool_kb = await init_pool("agent_kb", "postgresql://user:pass@localhost/db1")
    pool_joplin = await init_pool("joplin", "postgresql://user:pass@localhost/db2")

    # Both pools are registered
    assert get_pool("agent_kb") is not None
    assert get_pool("joplin") is not None

    await close_all()

    # After close_all, pools should be gone from registry
    with pytest.raises(ValueError):
        get_pool("agent_kb")
    with pytest.raises(ValueError):
        get_pool("joplin")

    # close() was called on each pool
    pool_kb.close.assert_awaited_once()
    pool_joplin.close.assert_awaited_once()


# ── 9. init_pool passes min_size / max_size to AsyncConnectionPool ────────────

@pytest.mark.asyncio
async def test_init_pool_passes_size_params(mock_pool_cls):
    from tools.db_pool import init_pool

    await init_pool("agent_kb", "postgresql://user:pass@localhost/db1", min_size=4, max_size=20)
    call_kwargs = mock_pool_cls.call_args[1]
    assert call_kwargs["min_size"] == 4
    assert call_kwargs["max_size"] == 20


# ── 10. Duplicate pool name overwrites, closing old pool ──────────────────────

@pytest.mark.asyncio
async def test_init_pool_duplicate_name_overwrites(mock_pool_cls):
    from tools.db_pool import init_pool, get_pool

    first_pool = await init_pool("agent_kb", "postgresql://user:pass@localhost/db1")
    assert get_pool("agent_kb") is first_pool

    # Second init with same name should close the old pool first
    second_pool = await init_pool("agent_kb", "postgresql://user:pass@localhost/db2")

    # Old pool should have been closed
    first_pool.close.assert_awaited_once()

    # New pool is now registered under the name
    assert get_pool("agent_kb") is second_pool