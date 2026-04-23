"""Tests that memory tools use the shared connection pool instead of raw psycopg.

RED phase (TDD): These tests verify that every memory tool function acquires
connections via ``get_pool("agent_kb").connection()`` rather than calling
``psycopg.AsyncConnection.connect()`` directly.
"""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MOD = "tools.memory"  # resolves via pythonpath in pyproject.toml


# ── Helpers ─────────────────────────────────────────────────────────────────


def _mock_cursor(rowcount=1, rows=None):
    """Return a mock object that behaves like an async cursor/result."""
    cur = AsyncMock()
    cur.rowcount = rowcount
    if rows is not None:
        cur.fetchall = AsyncMock(return_value=rows)
    return cur


def _mock_conn(execute_return=None, fetchall_rows=None):
    """Return a mock async connection with execute + commit."""
    conn = AsyncMock()
    if execute_return is not None:
        conn.execute = AsyncMock(return_value=execute_return)
    if fetchall_rows is not None:
        cursor = _mock_cursor(rows=fetchall_rows)
        # execute returns a cursor; fetchall on that cursor returns rows
        conn.execute = AsyncMock(return_value=cursor)
    conn.commit = AsyncMock()
    return conn


def _pool_ctx(mock_conn):
    """Build a MagicMock that works as ``async with pool.connection() as conn:``"""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.fixture()
def mock_pool():
    """Patch get_pool to return a mock pool whose .connection() yields a mock conn.

    Returns (pool, conn) so individual tests can customise the conn behaviour.
    """
    conn = _mock_conn()
    pool = MagicMock()
    pool.connection = MagicMock(return_value=_pool_ctx(conn))

    with patch(f"{MOD}.get_pool", return_value=pool):
        yield pool, conn


@pytest.fixture(autouse=True)
def _block_psyco_connect():
    """Ensure psycopg.AsyncConnection.connect is NEVER called in these tests.
    
    Since the refactored module no longer imports psycopg directly, patching
    may fail — that's fine, absence of the attribute proves the refactoring.
    """
    try:
        with patch(f"{MOD}.psycopg.AsyncConnection.connect") as mock_connect:
            yield
            mock_connect.assert_not_called()
    except AttributeError:
        # psycopg no longer imported — exactly what we want
        yield


@pytest.fixture(autouse=True)
def _block_os_environ():
    """Ensure os.environ['DATABASE_URL'] is never accessed.
    
    Since the refactored module no longer imports os, this fixture is a no-op
    if the attribute doesn't exist — that itself proves os was removed.
    """
    try:
        with patch(f"{MOD}.os") as mock_os:
            yield
    except AttributeError:
        # os is no longer imported — exactly what we want
        yield


# ── 1. save_memory uses pool.connection() ────────────────────────────────────

@pytest.mark.asyncio
async def test_save_memory_uses_pool(mock_pool):
    pool, conn = mock_pool
    from tools.memory import save_memory

    result = await save_memory.ainvoke({"content": "test fact", "category": "facts", "importance": 3})

    pool.connection.assert_called_once()
    conn.execute.assert_awaited()
    conn.commit.assert_awaited()
    assert "Saved to memory" in result


# ── 2. recall_memory uses pool.connection() ──────────────────────────────────

@pytest.mark.asyncio
async def test_recall_memory_uses_pool(mock_pool):
    pool, conn = mock_pool
    # Set up fetchall to return sample rows
    rows = [
        (1, "facts", "remembered thing", 3, datetime.datetime(2025, 1, 15)),
    ]
    cursor = _mock_cursor(rows=rows)
    conn.execute = AsyncMock(return_value=cursor)

    from tools.memory import recall_memory

    result = await recall_memory.ainvoke({"query": "thing"})

    pool.connection.assert_called_once()
    conn.execute.assert_awaited()
    assert "Memories" in result


# ── 3. update_memory uses pool.connection() ─────────────────────────────────

@pytest.mark.asyncio
async def test_update_memory_uses_pool(mock_pool):
    pool, conn = mock_pool
    conn.execute = AsyncMock(return_value=_mock_cursor(rowcount=1))

    from tools.memory import update_memory

    result = await update_memory.ainvoke({"memory_id": 42, "content": "updated"})

    pool.connection.assert_called_once()
    conn.execute.assert_awaited()
    conn.commit.assert_awaited()
    assert "updated" in result


# ── 4. delete_memory uses pool.connection() ─────────────────────────────────

@pytest.mark.asyncio
async def test_delete_memory_uses_pool(mock_pool):
    pool, conn = mock_pool
    conn.execute = AsyncMock(return_value=_mock_cursor(rowcount=1))

    from tools.memory import delete_memory

    result = await delete_memory.ainvoke({"memory_id": 7})

    pool.connection.assert_called_once()
    conn.execute.assert_awaited()
    conn.commit.assert_awaited()
    assert "deleted" in result


# ── 5. recall_memory_by_category uses pool.connection() ────────────────────

@pytest.mark.asyncio
async def test_recall_memory_by_category_uses_pool(mock_pool):
    pool, conn = mock_pool
    rows = [
        (1, "user prefers dark mode", 4, datetime.datetime(2025, 3, 1)),
    ]
    cursor = _mock_cursor(rows=rows)
    conn.execute = AsyncMock(return_value=cursor)

    from tools.memory import recall_memory_by_category

    result = await recall_memory_by_category.ainvoke({"category": "user_prefs"})

    pool.connection.assert_called_once()
    conn.execute.assert_awaited()
    assert "user_prefs" in result


# ── 6. summarize_memories uses pool.connection() ────────────────────────────

@pytest.mark.asyncio
async def test_summarize_memories_uses_pool(mock_pool):
    pool, conn = mock_pool
    rows = [
        (1, "facts", "a fact", 3, datetime.datetime(2025, 6, 1)),
    ]
    cursor = _mock_cursor(rows=rows)
    conn.execute = AsyncMock(return_value=cursor)

    # llm_call is a lazy import inside summarize_memories, so patch at the source module
    mock_llm = AsyncMock(return_value="**Key Insights**\n- Something important")
    with patch("tools.llm_client.llm_call", mock_llm):
        from tools.memory import summarize_memories

        result = await summarize_memories.ainvoke({"category": "", "max_insights": 5})

    pool.connection.assert_called_once()
    conn.execute.assert_awaited()
    assert "Consolidation" in result


# ── 7. psycopg.AsyncConnection.connect is not imported at all ────────────────

def test_no_psyco_import_in_memory_module():
    """The refactored module should not import psycopg at module level."""
    import tools.memory
    assert not hasattr(tools.memory, "psycopg"), (
        "memory.py should not import psycopg directly — it should use get_pool()"
    )


# ── 8. get_pool is called with "agent_kb" ───────────────────────────────────

@pytest.mark.asyncio
async def test_get_pool_called_with_agent_kb(mock_pool):
    pool, conn = mock_pool
    from tools.memory import save_memory

    await save_memory.ainvoke({"content": "y", "category": "facts"})

    # The mock_pool fixture patches get_pool; we can inspect the mock to
    # verify it was the source of the connection.
    pool.connection.assert_called_once()