"""Tests for agent.tools.joplin_hitl — HITL (Human-in-the-Loop) workflow.

Unit tests — mock the PG connection pool and joplin_pg tools to verify
the HITL workflow logic (generate → detect edits → review → publish).
"""

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MOD = "tools.joplin_hitl"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _make_mock_conn():
    """Create a mock asyncpg-style connection."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=1)
    return conn


@pytest.fixture(autouse=True)
def _mock_pool():
    """Patch get_pool and ensure_joplin_pool so no real DB is touched."""
    with patch(f"{MOD}.get_pool") as mock_get_pool, \
         patch(f"{MOD}.ensure_joplin_pool", new_callable=AsyncMock), \
         patch(f"{MOD}._ensure_hitl_table", new_callable=AsyncMock):
        mock_pool = MagicMock()
        mock_conn = _make_mock_conn()

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_pool.connection = MagicMock(return_value=ctx)
        mock_get_pool.return_value = mock_pool

        yield {"pool": mock_pool, "conn": mock_conn, "get_pool": mock_get_pool}


# ── 1. generate_note creates a note and returns note_id + joplin:// URI ─────

@pytest.mark.asyncio
async def test_generate_note_creates_session():
    with patch(f"{MOD}.joplin_create_note") as mock_create:
        mock_create.ainvoke = AsyncMock(
            return_value="Note created: Test\nNote ID: `abc123`\njoplin://x-callback-url/openNote?id=abc123"
        )

        from tools.joplin_hitl import generate_note

        result = await generate_note.ainvoke({
            "title": "Test Note",
            "content": "Hello world",
            "notebook_id": "nb1",
        })

        result_data = json.loads(result) if isinstance(result, str) else result
        assert result_data.get("note_id") == "abc123"
        assert "joplin://" in result_data.get("uri", "")


# ── 2. generate_note stores content hash ────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_note_stores_hash(_mock_pool):
    content = "Hello world"
    expected_hash = _content_hash(content)

    with patch(f"{MOD}.joplin_create_note") as mock_create:
        mock_create.ainvoke = AsyncMock(
            return_value="Note created: T\nNote ID: `deadbeef`\njoplin://x-callback-url/openNote?id=deadbeef"
        )

        from tools.joplin_hitl import generate_note

        result = await generate_note.ainvoke({
            "title": "T",
            "content": content,
            "notebook_id": "",
        })

        result_data = json.loads(result) if isinstance(result, str) else result
        assert result_data.get("content_hash") == expected_hash
        # Verify INSERT was called for HITL session
        conn = _mock_pool["conn"]
        assert conn.execute.call_count >= 1


# ── 3. detect_edits detects no change when content is unchanged ─────────────

@pytest.mark.asyncio
async def test_detect_edits_no_change(_mock_pool):
    content = "Original content"
    content_hash = _content_hash(content)

    session_row = {
        "id": 1,
        "note_id": "n1",
        "last_llm_hash": content_hash,
        "last_llm_content": content,
        "cycle_count": 0,
        "status": "generated",
    }
    _mock_pool["conn"].fetchrow = AsyncMock(return_value=session_row)

    with patch(f"{MOD}.joplin_get_note") as mock_get:
        mock_get.ainvoke = AsyncMock(return_value=f"# Note\n\n{content}\n\n---\nCreated: 2024Z")
        from tools.joplin_hitl import detect_edits

        result = await detect_edits.ainvoke({"note_id": "n1"})
        result_data = json.loads(result) if isinstance(result, str) else result

        assert result_data.get("edited") is False or result_data.get("status") == "unchanged"


# ── 4. detect_edits detects change when hash differs ────────────────────────

@pytest.mark.asyncio
async def test_detect_edits_detects_change(_mock_pool):
    original = "Original content"
    original_hash = _content_hash(original)
    edited = "Edited content by user"

    session_row = {
        "id": 1,
        "note_id": "n1",
        "last_llm_hash": original_hash,
        "last_llm_content": original,
        "cycle_count": 0,
        "status": "generated",
    }
    _mock_pool["conn"].fetchrow = AsyncMock(return_value=session_row)

    with patch(f"{MOD}.joplin_get_note") as mock_get:
        mock_get.ainvoke = AsyncMock(return_value=f"# Note\n\n{edited}\n\n---\nCreated: 2024Z")
        from tools.joplin_hitl import detect_edits

        result = await detect_edits.ainvoke({"note_id": "n1"})
        result_data = json.loads(result) if isinstance(result, str) else result

        assert result_data.get("edited") is True or result_data.get("status") == "edited"


# ── 5. review_edited_note returns structured diff ───────────────────────────

@pytest.mark.asyncio
async def test_review_edited_note_returns_diff(_mock_pool):
    original = "Line 1\nLine 2\nLine 3"
    edited = "Line 1\nLine 2 edited\nLine 3\nLine 4"

    session_row = {
        "id": 1,
        "note_id": "n1",
        "last_llm_hash": _content_hash(original),
        "last_llm_content": original,
        "cycle_count": 0,
        "status": "generated",
    }
    _mock_pool["conn"].fetchrow = AsyncMock(return_value=session_row)

    with patch(f"{MOD}.joplin_get_note") as mock_get:
        mock_get.ainvoke = AsyncMock(return_value=f"# Note\n\n{edited}\n\n---\nCreated: 2024Z")
        from tools.joplin_hitl import review_edited_note

        result = await review_edited_note.ainvoke({"note_id": "n1"})
        result_data = json.loads(result) if isinstance(result, str) else result

        assert "added_lines" in result_data
        assert "removed_lines" in result_data
        # The diff should have content since original != edited
        assert result_data.get("added_lines") or result_data.get("removed_lines")


# ── 6. publish_review updates note and stored version hash ──────────────────

@pytest.mark.asyncio
async def test_publish_review_updates_content_and_hash(_mock_pool):
    new_content = "Revised LLM content"
    new_hash = _content_hash(new_content)

    session_row = {
        "id": 1,
        "note_id": "n1",
        "last_llm_hash": "oldhash123456789",
        "last_llm_content": "Old content",
        "cycle_count": 1,
        "status": "edited",
    }
    _mock_pool["conn"].fetchrow = AsyncMock(return_value=session_row)

    with patch(f"{MOD}.joplin_update_note") as mock_update:
        mock_update.ainvoke = AsyncMock(return_value="Note updated")
        from tools.joplin_hitl import publish_review

        result = await publish_review.ainvoke({
            "note_id": "n1",
            "reviewed_content": new_content,
        })

        result_data = json.loads(result) if isinstance(result, str) else result
        assert result_data.get("status") == "published"
        assert result_data.get("content_hash") == new_hash
        assert result_data.get("cycle_count") == 2
        assert _mock_pool["conn"].execute.call_count >= 1


# ── 7. detect_edits with no session returns not-tracked ─────────────────────

@pytest.mark.asyncio
async def test_detect_edits_no_session(_mock_pool):
    _mock_pool["conn"].fetchrow = AsyncMock(return_value=None)

    from tools.joplin_hitl import detect_edits

    result = await detect_edits.ainvoke({"note_id": "n1"})
    result_data = json.loads(result) if isinstance(result, str) else result

    assert result_data.get("status") == "not_tracked"


# ── 8. review_edited_note with no session falls back gracefully ──────────────

@pytest.mark.asyncio
async def test_review_edited_note_no_session(_mock_pool):
    _mock_pool["conn"].fetchrow = AsyncMock(return_value=None)

    from tools.joplin_hitl import review_edited_note

    result = await review_edited_note.ainvoke({"note_id": "n1"})
    result_data = json.loads(result) if isinstance(result, str) else result

    assert result_data.get("status") == "not_tracked"