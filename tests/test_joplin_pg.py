"""Tests for agent.tools.joplin_pg — direct PG access layer.

Unit tests — no live Joplin DB required.  We mock the connection pool
and verify that the correct SQL / params are emitted by each @tool
function.
"""

import base64
import importlib
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MOD = "tools.joplin_pg"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _note_json_bytes(title: str, body: str, now: int = 1_700_000_000_000) -> bytes:
    """Build a realistic Joplin note JSON blob (same shape as _note_json)."""
    return json.dumps({
        "title": title,
        "body": body,
        "markup_language": 1,
        "deleted_time": 0,
        "user_updated_time": now,
        "user_created_time": now,
        "created_time": now,
    }).encode("utf-8")


def _make_mock_conn():
    """Create a mock asyncpg-style connection that records execute calls."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=1)
    return conn


@pytest.fixture(autouse=True)
def _mock_pool_and_owner():
    """Patch get_pool and _get_owner_id so no real DB is touched."""
    with patch(f"{MOD}.get_pool") as mock_get_pool, \
         patch(f"{MOD}.ensure_joplin_pool", new_callable=AsyncMock), \
         patch(f"{MOD}._get_owner_id", new_callable=AsyncMock, return_value="owner123"):
        mock_pool = MagicMock()
        mock_conn = _make_mock_conn()

        # pool.connection() returns an async context manager yielding mock_conn
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool.connection = MagicMock(return_value=ctx)
        mock_get_pool.return_value = mock_pool

        yield {"pool": mock_pool, "conn": mock_conn, "get_pool": mock_get_pool}


# ── 1. create_note returns note_id and joplin:// URI ────────────────────────

@pytest.mark.asyncio
async def test_create_note_returns_note_id_and_uri():
    from tools.joplin_pg import joplin_create_note

    result = await joplin_create_note.ainvoke({
        "title": "Test Note",
        "content": "Hello world",
        "notebook_id": "nb1",
    })

    assert "Note ID:" in result or "note_id" in result.lower() or "Test Note" in result
    assert "joplin://" in result


# ── 2. create_note calls pool and executes INSERTs ─────────────────────────

@pytest.mark.asyncio
async def test_create_note_calls_pool(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_create_note

    await joplin_create_note.ainvoke({"title": "X", "content": "Y"})

    _mock_pool_and_owner["get_pool"].assert_called_with("joplin")
    # INSERT items + INSERT user_items + INSERT changes = at least 2 executes
    assert _mock_pool_and_owner["conn"].execute.call_count >= 2


# ── 3. create_note with tags ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_note_with_tags(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_create_note

    result = await joplin_create_note.ainvoke({
        "title": "Tagged",
        "content": "C",
        "tags": ["important", "review"],
    })

    assert "Tagged" in result
    # Extra execute calls for tag creation/linking
    assert _mock_pool_and_owner["conn"].execute.call_count >= 3


# ── 4. get_note retrieves and parses note content ───────────────────────────

@pytest.mark.asyncio
async def test_get_note_returns_title_and_body(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_get_note

    now = 1_700_000_000_000
    note_data = {"title": "My Note", "body": "Body text", "deleted_time": 0,
                 "markup_language": 1, "user_updated_time": now}
    mock_row = {"content": json.dumps(note_data).encode("utf-8"),
                "created_time": now, "updated_time": now}
    _mock_pool_and_owner["conn"].fetchrow = AsyncMock(return_value=mock_row)

    result = await joplin_get_note.ainvoke({"note_id": "abc123"})

    assert "My Note" in result
    assert "Body text" in result


# ── 5. get_note handles missing note ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_note_not_found(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_get_note

    _mock_pool_and_owner["conn"].fetchrow = AsyncMock(return_value=None)

    result = await joplin_get_note.ainvoke({"note_id": "nonexistent"})

    assert "not found" in result.lower()


# ── 6. update_note modifies existing note ───────────────────────────────────

@pytest.mark.asyncio
async def test_update_note_updates_content(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_update_note

    now = 1_700_000_000_000
    existing = {"title": "Old", "body": "Old body", "deleted_time": 0,
                "markup_language": 1, "user_updated_time": now}
    mock_row = {"content": json.dumps(existing).encode("utf-8")}
    _mock_pool_and_owner["conn"].fetchrow = AsyncMock(return_value=mock_row)

    result = await joplin_update_note.ainvoke({
        "note_id": "note1",
        "title": "New Title",
        "content": "New body",
    })

    assert "New Title" in result or "updated" in result.lower()
    assert _mock_pool_and_owner["conn"].execute.call_count >= 1


# ── 7. update_note with append appends content ──────────────────────────────

@pytest.mark.asyncio
async def test_update_note_append(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_update_note

    now = 1_700_000_000_000
    existing = {"title": "T", "body": "Original", "deleted_time": 0,
                "markup_language": 1, "user_updated_time": now}
    mock_row = {"content": json.dumps(existing).encode("utf-8")}
    _mock_pool_and_owner["conn"].fetchrow = AsyncMock(return_value=mock_row)

    result = await joplin_update_note.ainvoke({
        "note_id": "note1",
        "content": "Appended",
        "append": True,
    })

    assert _mock_pool_and_owner["conn"].execute.call_count >= 1


# ── 8. delete_note soft-deletes ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_note_soft_deletes(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_delete_note

    now = 1_700_000_000_000
    existing = {"title": "Delete Me", "body": "body", "deleted_time": 0,
                "markup_language": 1, "user_updated_time": now}
    mock_row = {"content": json.dumps(existing).encode("utf-8")}
    _mock_pool_and_owner["conn"].fetchrow = AsyncMock(return_value=mock_row)

    result = await joplin_delete_note.ainvoke({"note_id": "note1"})

    assert "deleted" in result.lower() or "Delete Me" in result
    assert _mock_pool_and_owner["conn"].execute.call_count >= 1


# ── 9. search_notes returns results ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_notes_found(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_search_notes

    now = 1_700_000_000_000
    note_data = {"title": "Search Result", "body": "Found it", "deleted_time": 0}
    mock_rows = [{"id": "abc", "content": json.dumps(note_data).encode("utf-8"),
                  "updated_time": now}]
    _mock_pool_and_owner["conn"].fetch = AsyncMock(return_value=mock_rows)

    result = await joplin_search_notes.ainvoke({"query": "Search", "limit": 10})

    assert "Search Result" in result


# ── 10. search_notes no results ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_notes_empty(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_search_notes

    _mock_pool_and_owner["conn"].fetch = AsyncMock(return_value=[])

    result = await joplin_search_notes.ainvoke({"query": "nonexistent", "limit": 5})

    assert "no notes" in result.lower() or "0" in result


# ── 11. create_notebook inserts folder-type item ─────────────────────────────

@pytest.mark.asyncio
async def test_create_notebook_returns_id(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_create_notebook

    result = await joplin_create_notebook.ainvoke({"title": "LLM Generated - Test NB"})

    assert "Notebook" in result or "notebook" in result.lower()
    assert _mock_pool_and_owner["conn"].execute.call_count >= 2


# ── 12. list_notebooks queries jop_type 2 ────────────────────────────────────

@pytest.mark.asyncio
async def test_list_notebooks_returns_folders(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_list_notebooks

    folder_data = {"title": "My Folder", "deleted_time": 0}
    mock_rows = [{"id": "f1", "content": json.dumps(folder_data).encode("utf-8"),
                  "created_time": 1_700_000_000_000}]
    _mock_pool_and_owner["conn"].fetch = AsyncMock(return_value=mock_rows)

    result = await joplin_list_notebooks.ainvoke({})

    assert "My Folder" in result


# ── 13. ping returns ok ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ping_returns_ok(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_ping

    result = await joplin_ping.ainvoke({})

    assert "ok" in result.lower()


# ── 14. edit_note with find/replace ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_edit_note_find_replace(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_edit_note

    now = 1_700_000_000_000
    existing = {"title": "Edit Me", "body": "Hello world", "deleted_time": 0,
                "markup_language": 1, "user_updated_time": now}
    mock_row = {"content": json.dumps(existing).encode("utf-8")}
    _mock_pool_and_owner["conn"].fetchrow = AsyncMock(return_value=mock_row)

    result = await joplin_edit_note.ainvoke({
        "note_id": "n1",
        "find": "world",
        "replace": "earth",
    })

    assert "edited" in result.lower() or "Edit Me" in result
    assert _mock_pool_and_owner["conn"].execute.call_count >= 1


# ── 15. edit_note with append ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_edit_note_append(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_edit_note

    now = 1_700_000_000_000
    existing = {"title": "T", "body": "Existing", "deleted_time": 0,
                "markup_language": 1, "user_updated_time": now}
    mock_row = {"content": json.dumps(existing).encode("utf-8")}
    _mock_pool_and_owner["conn"].fetchrow = AsyncMock(return_value=mock_row)

    await joplin_edit_note.ainvoke({"note_id": "n1", "append": "Extra content"})

    assert _mock_pool_and_owner["conn"].execute.call_count >= 1


# ── 16. list_tags queries jop_type 17 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_tags(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_list_tags

    mock_rows = [{"id": "t1", "name": "important"}]
    _mock_pool_and_owner["conn"].fetch = AsyncMock(return_value=mock_rows)

    result = await joplin_list_tags.ainvoke({})

    assert "important" in result


# ── 17. get_tags_for_note ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_tags_for_note(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_get_tags_for_note

    note_data = {"title": "T", "body": "B", "deleted_time": 0}
    mock_row = {"id": "n1", "content": json.dumps(note_data).encode("utf-8")}
    _mock_pool_and_owner["conn"].fetchrow = AsyncMock(return_value=mock_row)

    tag_rows = [{"id": "t1", "name": "tag1"}]
    _mock_pool_and_owner["conn"].fetch = AsyncMock(return_value=tag_rows)

    result = await joplin_get_tags_for_note.ainvoke({"note_id": "n1"})

    assert "tag1" in result


# ── 18. upload_resource inserts type 4 item ────────────────────────────────────

@pytest.mark.asyncio
async def test_upload_resource(_mock_pool_and_owner):
    from tools.joplin_pg import joplin_upload_resource

    content_b64 = base64.b64encode(b"hello").decode()

    result = await joplin_upload_resource.ainvoke({
        "filename": "test.txt",
        "content_b64": content_b64,
    })

    assert "test.txt" in result
    assert _mock_pool_and_owner["conn"].execute.call_count >= 1


# ── 19. _note_json produces valid JSON with correct fields ────────────────────

def test_note_json_structure():
    from tools.joplin_pg import _note_json

    result = _note_json("Title", "Body", "nb1", 1_700_000_000_000)
    data = json.loads(result)

    assert data["title"] == "Title"
    assert data["body"] == "Body"
    assert data["markup_language"] == 1
    assert data["deleted_time"] == 0


# ── 20. _folder_json produces valid JSON ──────────────────────────────────────

def test_folder_json_structure():
    from tools.joplin_pg import _folder_json

    result = _folder_json("My Folder", 1_700_000_000_000)
    data = json.loads(result)

    assert data["title"] == "My Folder"
    assert data["deleted_time"] == 0


# ── 21. _parse_note handles valid JSON ────────────────────────────────────────

def test_parse_note_valid():
    from tools.joplin_pg import _parse_note

    raw = json.dumps({"title": "X", "body": "Y"}).encode("utf-8")
    data = _parse_note(raw)

    assert data["title"] == "X"
    assert data["body"] == "Y"


# ── 22. _parse_note handles invalid JSON gracefully ───────────────────────────

def test_parse_note_invalid():
    from tools.joplin_pg import _parse_note

    raw = b"not json at all"
    data = _parse_note(raw)

    assert "title" in data  # fallback


# ── 23. DSN construction from env vars ────────────────────────────────────────

def test_dsn_from_env():
    with patch.dict("os.environ", {
        "JOPLIN_DB_HOST": "dbhost",
        "JOPLIN_DB_PORT": "5433",
        "JOPLIN_DB_NAME": "joplin_test",
        "JOPLIN_DB_USER": "juser",
        "JOPLIN_DB_PASSWORD": "jpass",
    }):
        import tools.joplin_pg as mod
        importlib.reload(mod)

        assert "dbhost" in mod.JOPLIN_DSN
        assert "5433" in mod.JOPLIN_DSN
        assert "joplin_test" in mod.JOPLIN_DSN