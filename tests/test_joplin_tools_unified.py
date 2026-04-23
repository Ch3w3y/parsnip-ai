"""Tests verifying Joplin tools resolve to joplin_pg (not joplin_mcp) in the unified PG layer.

Validates:
- All 12 NOTE_TOOLS functions come from joplin_pg
- No httpx dependency in the joplin_pg tool path
- joplin_mcp emits a DeprecationWarning when imported
"""

import importlib
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── The 12 Joplin tool names ─────────────────────────────────────────────────

JOPLIN_TOOL_NAMES = [
    "joplin_create_notebook",
    "joplin_create_note",
    "joplin_update_note",
    "joplin_edit_note",
    "joplin_delete_note",
    "joplin_get_note",
    "joplin_search_notes",
    "joplin_list_notebooks",
    "joplin_list_tags",
    "joplin_get_tags_for_note",
    "joplin_upload_resource",
    "joplin_ping",
]


# ── Test: NOTE_TOOLS functions resolve to joplin_pg ───────────────────────────


@pytest.mark.parametrize("tool_name", JOPLIN_TOOL_NAMES)
def test_note_tool_resolves_to_joplin_pg(tool_name):
    """Each Joplin tool in graph_tools.NOTE_TOOLS comes from joplin_pg, not joplin_mcp."""
    from graph_tools import NOTE_TOOLS

    # Find the tool object matching the name
    tool_obj = None
    for t in NOTE_TOOLS:
        if t.name == tool_name:
            tool_obj = t
            break

    assert tool_obj is not None, f"{tool_name} not found in NOTE_TOOLS"
    # @tool wraps the function in a StructuredTool — check the wrapped coroutine's module
    coroutine_mod = getattr(tool_obj, "coroutine", tool_obj).__module__ or ""
    assert "joplin_pg" in coroutine_mod, (
        f"{tool_name} coroutine resolved to {coroutine_mod}, expected joplin_pg"
    )
    assert "joplin_mcp" not in coroutine_mod, (
        f"{tool_name} coroutine unexpectedly resolved to joplin_mcp module"
    )


# ── Test: tools package exports resolve to joplin_pg ──────────────────────────


@pytest.mark.parametrize("tool_name", JOPLIN_TOOL_NAMES)
def test_tools_package_exports_joplin_pg(tool_name):
    """tools.__init__ exports the joplin_pg version of each tool."""
    import tools

    func = getattr(tools, tool_name)
    # @tool wraps in StructuredTool — check the wrapped coroutine's module
    coroutine_mod = getattr(func, "coroutine", func).__module__ or ""
    assert "joplin_pg" in coroutine_mod, (
        f"tools.{tool_name} coroutine resolved to {coroutine_mod}, expected joplin_pg"
    )


# ── Test: no httpx in joplin_pg tool path ────────────────────────────────────


@pytest.mark.parametrize("tool_name", JOPLIN_TOOL_NAMES)
def test_joplin_pg_tools_have_no_httpx_dependency(tool_name):
    """Verify that the joplin_pg module and tool functions don't reference httpx."""
    from tools import joplin_pg

    # Verify httpx is not in the module's namespace
    assert not hasattr(joplin_pg, "httpx"), (
        f"joplin_pg module should not have httpx attribute"
    )
    # Verify httpx is not in the module's source-level imports
    source_lines = open(joplin_pg.__file__).read()
    assert "httpx" not in source_lines, (
        f"joplin_pg module source references httpx — should use PG pool only"
    )


# ── Test: joplin_pg tool calls go through pool.connection(), not httpx ────────


@pytest.fixture
def _mock_pg_pool():
    """Mock the joplin_pg pool so we can track that tools use pool.connection()."""
    with patch("tools.joplin_pg.get_pool") as mock_get_pool, \
         patch("tools.joplin_pg.ensure_joplin_pool", new_callable=AsyncMock), \
         patch("tools.joplin_pg._get_owner_id", new_callable=AsyncMock, return_value="owner123"):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_conn.fetchrow = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=1)

        mock_pool = MagicMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool.connection = MagicMock(return_value=ctx)
        mock_get_pool.return_value = mock_pool

        yield {"pool": mock_pool, "conn": mock_conn, "get_pool": mock_get_pool}


@pytest.mark.parametrize("tool_name", JOPLIN_TOOL_NAMES)
@pytest.mark.asyncio
async def test_joplin_tool_uses_pg_pool_not_httpx(tool_name, _mock_pg_pool):
    """Each Joplin tool calls through pool.connection() — no httpx involved."""
    import tools.joplin_pg as pg_mod

    tool_func = getattr(pg_mod, tool_name)

    # Provide minimal valid args for each tool
    args_map = {
        "joplin_create_notebook": {"title": "Test NB"},
        "joplin_create_note": {"title": "T", "content": "C"},
        "joplin_update_note": {"note_id": "n1", "title": "New"},
        "joplin_edit_note": {"note_id": "n1", "append": "extra"},
        "joplin_delete_note": {"note_id": "n1"},
        "joplin_get_note": {"note_id": "n1"},
        "joplin_search_notes": {"query": "test"},
        "joplin_list_notebooks": {},
        "joplin_list_tags": {},
        "joplin_get_tags_for_note": {"note_id": "n1"},
        "joplin_upload_resource": {"filename": "f.txt", "content_b64": "aGVsbG8="},
        "joplin_ping": {},
    }

    # For tools that need fetchrow to return data
    import json
    now = 1_700_000_000_000
    if tool_name in ("joplin_update_note", "joplin_edit_note", "joplin_delete_note"):
        note_data = {"title": "T", "body": "B", "deleted_time": 0,
                     "markup_language": 1, "user_updated_time": now}
        _mock_pg_pool["conn"].fetchrow = AsyncMock(
            return_value={"content": json.dumps(note_data).encode("utf-8")}
        )
    elif tool_name in ("joplin_get_note", "joplin_get_tags_for_note"):
        note_data = {"title": "T", "body": "B", "deleted_time": 0}
        _mock_pg_pool["conn"].fetchrow = AsyncMock(
            return_value={"id": "n1", "content": json.dumps(note_data).encode("utf-8"),
                          "created_time": now, "updated_time": now}
        )

    # Call the tool
    await tool_func.ainvoke(args_map[tool_name])

    # Verify the pool's connection() was called — this is the PG path
    _mock_pg_pool["get_pool"].assert_called_with("joplin")
    _mock_pg_pool["pool"].connection.assert_called()


# ── Test: import resolution — joplin_pg functions are what graph_tools uses ──


def test_import_resolution_joplin_pg_wins():
    """Verify that importing from the tools package gives us joplin_pg functions."""
    from tools import (
        joplin_create_notebook,
        joplin_create_note,
        joplin_update_note,
        joplin_edit_note,
        joplin_delete_note,
        joplin_get_note,
        joplin_search_notes,
        joplin_list_notebooks,
        joplin_list_tags,
        joplin_get_tags_for_note,
        joplin_upload_resource,
        joplin_ping,
    )

    all_funcs = [
        joplin_create_notebook, joplin_create_note, joplin_update_note,
        joplin_edit_note, joplin_delete_note, joplin_get_note,
        joplin_search_notes, joplin_list_notebooks, joplin_list_tags,
        joplin_get_tags_for_note, joplin_upload_resource, joplin_ping,
    ]
    for func in all_funcs:
        # @tool wraps in StructuredTool — check wrapped coroutine's module
        coroutine_mod = getattr(func, "coroutine", func).__module__ or ""
        assert "joplin_pg" in coroutine_mod, (
            f"{func.name} coroutine comes from {coroutine_mod}, expected joplin_pg"
        )


# ── Test: joplin_mcp emits DeprecationWarning ────────────────────────────────


def test_joplin_mcp_deprecation_warning():
    """Importing joplin_mcp should emit a DeprecationWarning."""
    # Force re-import so the module-level warnings.warn() fires again
    import sys
    for key in list(sys.modules):
        if "joplin_mcp" in key:
            del sys.modules[key]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import tools.joplin_mcp  # noqa: F401

    deprecation_warnings = [
        w for w in caught if issubclass(w.category, DeprecationWarning)
                         and "joplin_mcp" in str(w.message)
    ]
    assert len(deprecation_warnings) >= 1, (
        "Expected DeprecationWarning when importing joplin_mcp"
    )
    assert "joplin_pg" in str(deprecation_warnings[0].message), (
        "Deprecation warning should mention joplin_pg as the replacement"
    )


# ── Test: joplin_mcp still importable ─────────────────────────────────────────


def test_joplin_mcp_still_importable():
    """joplin_mcp.py should still be importable (not deleted)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import tools.joplin_mcp as mcp_mod

    assert hasattr(mcp_mod, "joplin_ping"), (
        "joplin_mcp module should still expose joplin_ping"
    )
    assert hasattr(mcp_mod, "_mcp_call"), (
        "joplin_mcp module should still expose _mcp_call helper"
    )


# ── Test: no joplin_mcp_* exports in tools package ───────────────────────────


def test_no_joplin_mcp_aliases_in_tools_package():
    """tools.__init__ should no longer export joplin_mcp_* aliases."""
    import tools

    for name in JOPLIN_TOOL_NAMES:
        mcp_alias = name.replace("joplin_", "joplin_mcp_")
        assert not hasattr(tools, mcp_alias), (
            f"tools package still exports {mcp_alias} — backward-compat aliases should be removed"
        )


# ── Test: joplin_pg source has no httpx ──────────────────────────────────────


def test_joplin_pg_source_no_httpx():
    """joplin_pg.py source should not contain any httpx references."""
    from tools import joplin_pg

    source = open(joplin_pg.__file__).read()
    assert "httpx" not in source, "joplin_pg should not reference httpx"


# ── Test: joplin_mcp source still has httpx (it's the HTTP proxy) ─────────────


def test_joplin_mcp_source_uses_httpx():
    """joplin_mcp.py should still use httpx (it's the HTTP proxy module)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import tools.joplin_mcp as mcp_mod

    source = open(mcp_mod.__file__).read()
    assert "httpx" in source, (
        "joplin_mcp should still use httpx (it's the HTTP proxy layer)"
    )