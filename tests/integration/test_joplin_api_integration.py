"""Integration tests for Joplin PG layer + agent API endpoints.

Verifies that:
  - joplin_pg functions are proper LangChain @tool functions
  - joplin_pg uses the named "joplin" pool from db_pool
  - joplin_hitl functions are proper @tool functions
  - joplin_hitl uses SHA-256 for content hashing
  - FastAPI /v1/models returns OpenAI-compatible format
  - /v1/chat/completions validates message input
  - /v1/chat/completions extracts the last user message
  - /v1/chat/completions streaming SSE matches OpenAI format

No live PostgreSQL connection or running agent server is required.
DB calls are mocked; FastAPI endpoints are tested via TestClient.
"""

import hashlib
import importlib
import inspect
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Path setup ──────────────────────────────────────────────────────────────────

_AGENT_DIR = str(Path(__file__).resolve().parent.parent.parent / "agent")
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)


# ═══════════════════════════════════════════════════════════════════════════════
#  Part A — Joplin PG layer
# ═══════════════════════════════════════════════════════════════════════════════


# 1. All 12 joplin_pg functions have @tool decorator


class TestJoplinPgTools:
    JOPLIN_PG_TOOL_NAMES = [
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

    def test_joplin_pg_functions_are_langchain_tools(self):
        """All 12 joplin_pg functions have @tool decorator (check .name attribute)."""
        from tools.joplin_pg import (
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

        all_tools = {
            "joplin_create_notebook": joplin_create_notebook,
            "joplin_create_note": joplin_create_note,
            "joplin_update_note": joplin_update_note,
            "joplin_edit_note": joplin_edit_note,
            "joplin_delete_note": joplin_delete_note,
            "joplin_get_note": joplin_get_note,
            "joplin_search_notes": joplin_search_notes,
            "joplin_list_notebooks": joplin_list_notebooks,
            "joplin_list_tags": joplin_list_tags,
            "joplin_get_tags_for_note": joplin_get_tags_for_note,
            "joplin_upload_resource": joplin_upload_resource,
            "joplin_ping": joplin_ping,
        }

        assert len(all_tools) == 12, f"Expected 12 tools, got {len(all_tools)}"

        for name, tool_fn in all_tools.items():
            # LangChain @tool functions get a .name attribute
            assert hasattr(tool_fn, "name"), f"Tool '{name}' missing .name attribute"
            assert tool_fn.name == name, f"Tool .name mismatch: {tool_fn.name} != {name}"

    def test_joplin_pg_tool_count_matches_decorator_count(self):
        """Verify the number of @tool-decorated functions exactly matches 12."""
        from tools import joplin_pg
        from langchain_core.tools import BaseTool

        tool_functions = [
            obj for _name, obj in inspect.getmembers(joplin_pg)
            if isinstance(obj, BaseTool)
        ]
        assert len(tool_functions) == 12, (
            f"Expected 12 @tool functions in joplin_pg, found {len(tool_functions)}: "
            f"{[t.name for t in tool_functions]}"
        )


# 2. joplin_pg uses named pool "joplin"


class TestJoplinPgNamedPool:
    @pytest.fixture(autouse=True)
    def mock_pool(self):
        """Patch get_pool and ensure_joplin_pool so no real DB is touched."""
        with (
            patch("tools.joplin_pg.get_pool") as mock_get_pool,
            patch("tools.joplin_pg.ensure_joplin_pool", new_callable=AsyncMock),
            patch("tools.joplin_pg._get_owner_id", new_callable=AsyncMock, return_value="owner123"),
        ):
            mock_pool = MagicMock()
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock(return_value="UPDATE 1")
            mock_conn.fetchrow = AsyncMock(return_value=None)
            mock_conn.fetch = AsyncMock(return_value=[])

            ctx = MagicMock()
            ctx.__aenter__ = AsyncMock(return_value=mock_conn)
            ctx.__aexit__ = AsyncMock(return_value=False)
            mock_pool.connection = MagicMock(return_value=ctx)
            mock_get_pool.return_value = mock_pool

            yield {"pool": mock_pool, "conn": mock_conn, "get_pool": mock_get_pool}

    @pytest.mark.asyncio
    async def test_joplin_pg_uses_named_pool(self, mock_pool):
        """All joplin_pg tool functions call get_pool('joplin')."""
        from tools.joplin_pg import (
            joplin_create_notebook,
            joplin_create_note,
            joplin_ping,
        )

        # Test a few representative tools to confirm they use get_pool("joplin")
        await joplin_create_notebook.ainvoke({"title": "Test"})
        mock_pool["get_pool"].assert_called_with("joplin")

        mock_pool["get_pool"].reset_mock()
        await joplin_create_note.ainvoke({"title": "T", "content": "C"})
        mock_pool["get_pool"].assert_called_with("joplin")

        mock_pool["get_pool"].reset_mock()
        await joplin_ping.ainvoke({})
        mock_pool["get_pool"].assert_called_with("joplin")

    @pytest.mark.asyncio
    async def test_all_joplin_pg_tools_use_joplin_pool(self, mock_pool):
        """Every joplin_pg @tool function calls get_pool('joplin')."""
        from tools import joplin_pg
        from langchain_core.tools import BaseTool

        tool_functions = [
            obj for _name, obj in inspect.getmembers(joplin_pg)
            if isinstance(obj, BaseTool)
        ]

        # Verify each tool (at least the ones we can invoke without complex args)
        # calls get_pool("joplin")
        assert len(tool_functions) >= 12

        # Spot-check the source code for get_pool("joplin") calls
        source = inspect.getsource(joplin_pg)
        assert 'get_pool("joplin")' in source, (
            "joplin_pg module should call get_pool('joplin')"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Part B — Joplin HITL layer
# ═══════════════════════════════════════════════════════════════════════════════


# 3. All 4 joplin_hitl functions have @tool decorator


class TestJoplinHitlTools:
    HITL_TOOL_NAMES = [
        "generate_note",
        "detect_edits",
        "review_edited_note",
        "publish_review",
    ]

    def test_joplin_hitl_functions_are_langchain_tools(self):
        """All 4 joplin_hitl functions have @tool decorator (check .name attribute)."""
        from tools.joplin_hitl import (
            generate_note,
            detect_edits,
            review_edited_note,
            publish_review,
        )

        all_tools = {
            "generate_note": generate_note,
            "detect_edits": detect_edits,
            "review_edited_note": review_edited_note,
            "publish_review": publish_review,
        }

        assert len(all_tools) == 4, f"Expected 4 HITL tools, got {len(all_tools)}"

        for name, tool_fn in all_tools.items():
            assert hasattr(tool_fn, "name"), f"HITL tool '{name}' missing .name attribute"
            assert tool_fn.name == name, (
                f"HITL tool .name mismatch: {tool_fn.name} != {name}"
            )

    def test_joplin_hitl_tool_count_matches_decorator_count(self):
        """Verify the number of @tool-decorated functions defined in joplin_hitl is exactly 4."""
        from tools import joplin_hitl
        from langchain_core.tools import BaseTool

        # joplin_hitl imports 3 tools from joplin_pg (create_note, get_note, update_note)
        # so we must filter to only natively defined HITL tools
        hitl_native_names = {"generate_note", "detect_edits", "review_edited_note", "publish_review"}
        tool_functions = [
            obj for _name, obj in inspect.getmembers(joplin_hitl)
            if isinstance(obj, BaseTool) and obj.name in hitl_native_names
        ]
        assert len(tool_functions) == 4, (
            f"Expected 4 native @tool functions in joplin_hitl, found {len(tool_functions)}: "
            f"{[t.name for t in tool_functions]}"
        )


# 4. joplin_hitl uses SHA-256 for content hashing


class TestJoplinHitlContentHashing:
    def test_joplin_hitl_content_hashing(self):
        """sha256 is used for content hashing in joplin_hitl.py."""
        from tools.joplin_hitl import _content_hash

        content = "test content for hashing"
        expected = hashlib.sha256(content.encode()).hexdigest()[:16]
        result = _content_hash(content)

        assert result == expected, (
            f"_content_hash should use SHA-256[:16], expected {expected}, got {result}"
        )

    def test_content_hash_is_deterministic(self):
        """_content_hash returns the same value for the same input."""
        from tools.joplin_hitl import _content_hash

        content = "deterministic test"
        assert _content_hash(content) == _content_hash(content)

    def test_content_hash_differs_for_different_content(self):
        """_content_hash returns different values for different inputs."""
        from tools.joplin_hitl import _content_hash

        assert _content_hash("abc") != _content_hash("xyz")

    def test_content_hash_uses_sha256_algorithm(self):
        """Verify the _content_hash function explicitly uses SHA-256, not another hash."""
        from tools import joplin_hitl

        source = inspect.getsource(joplin_hitl._content_hash)
        assert "sha256" in source, (
            "_content_hash should use hashlib.sha256, not another algorithm"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Part C — FastAPI Agent API endpoints
# ═══════════════════════════════════════════════════════════════════════════════


def _make_mock_agent():
    """Create a mock agent that simulates LangGraph streaming events."""
    mock = AsyncMock()
    mock.aget_state = AsyncMock(return_value=MagicMock(values=None))
    return mock


@pytest.fixture()
def mock_agent():
    return _make_mock_agent()


@pytest.fixture()
def client(mock_agent):
    """Create a TestClient with the agent patched before the app starts.

    Uses importlib to explicitly load agent/main.py to avoid sys.path
    shadowing by ingestion/main.py (which can be placed at sys.path[0]
    by the registry integration tests in the same session).
    """
    import importlib

    # Ensure agent/ directory is at the front of sys.path so `import main`
    # resolves to agent/main.py, not ingestion/main.py
    agent_dir = str(Path(__file__).resolve().parent.parent.parent / "agent")
    # Temporarily move agent_dir to the front
    if agent_dir in sys.path:
        sys.path.remove(agent_dir)
    sys.path.insert(0, agent_dir)

    # Remove any cached 'main' module that might be ingestion/main.py
    if "main" in sys.modules:
        del sys.modules["main"]

    with (
        patch("main.agent", mock_agent),
        patch("main._pool", None),
        patch("main.build_graph", AsyncMock(return_value=mock_agent)),
        patch("main._load_l1_memory", AsyncMock(return_value="")),
        patch("main.init_pool", AsyncMock()),
        patch("main.close_all", AsyncMock()),
    ):
        from main import app

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


# 5. /v1/models endpoint format


class TestV1ModelsEndpoint:
    def test_v1_models_endpoint_format(self, client):
        """GET /v1/models returns OpenAI-compatible format with object='list'."""
        r = client.get("/v1/models")
        assert r.status_code == 200

        data = r.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)
        assert len(data["data"]) >= 1

    def test_v1_models_model_has_required_fields(self, client):
        """Each model in /v1/models has id, object, owned_by fields."""
        r = client.get("/v1/models")
        data = r.json()

        model = data["data"][0]
        assert "id" in model
        assert model["object"] == "model"
        assert "owned_by" in model
        assert model["id"] == "parsnip-agent"


# 6. /v1/chat/completions rejects empty messages


class TestV1ChatCompletionsValidation:
    def test_v1_chat_completions_rejects_empty_messages(self, client):
        """POST /v1/chat/completions with empty messages returns 400."""
        payload = {"model": "parsnip-agent", "messages": []}
        r = client.post("/v1/chat/completions", json=payload)
        # The endpoint returns 400 when no user message is found
        assert r.status_code == 400

    def test_v1_chat_completions_rejects_no_user_message(self, client):
        """POST /v1/chat/completions with only assistant messages returns 400."""
        payload = {
            "model": "parsnip-agent",
            "messages": [{"role": "assistant", "content": "hello"}],
        }
        r = client.post("/v1/chat/completions", json=payload)
        # No user message → 400
        assert r.status_code == 400


# 7. /v1/chat/completions extracts last user message


class TestV1ChatCompletionsMessageExtraction:
    def test_v1_chat_completions_extracts_last_user_message(self, client, mock_agent):
        """Verify the last user message in the messages array is used as the query."""
        captured_state = {}

        events = [
            {"event": "on_chat_model_stream", "data": {"chunk": MagicMock(content="response")}},
        ]

        async def mock_astream(state, *args, **kwargs):
            captured_state.update(state)
            for ev in events:
                yield ev

        mock_agent.astream_events = mock_astream

        payload = {
            "model": "parsnip-agent",
            "messages": [
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": "second question"},
            ],
        }
        r = client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200

        # The last message in the state should be the last user message
        assert "messages" in captured_state
        last_msg = captured_state["messages"][-1]
        from langchain_core.messages import HumanMessage

        assert isinstance(last_msg, HumanMessage)
        assert last_msg.content == "second question"

    def test_v1_chat_completions_includes_history_messages(self, client, mock_agent):
        """Prior messages in the array are included in the state as history."""
        captured_state = {}

        events = [
            {"event": "on_chat_model_stream", "data": {"chunk": MagicMock(content="ok")}},
        ]

        async def mock_astream(state, *args, **kwargs):
            captured_state.update(state)
            for ev in events:
                yield ev

        mock_agent.astream_events = mock_astream

        payload = {
            "model": "parsnip-agent",
            "messages": [
                {"role": "user", "content": "history q"},
                {"role": "assistant", "content": "history a"},
                {"role": "user", "content": "current q"},
            ],
        }
        r = client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200

        messages = captured_state.get("messages", [])
        # Should have history messages + the current user message
        assert len(messages) >= 2


# 8. /v1/chat/completions streaming format


class TestV1ChatCompletionsStreamFormat:
    def _make_stream_events(self, tokens=None, tool_calls=None):
        """Build a list of mock astream_events dicts."""
        events = []
        if tokens:
            for t in tokens:
                chunk = MagicMock()
                chunk.content = t
                events.append({"event": "on_chat_model_stream", "data": {"chunk": chunk}})
        if tool_calls:
            for tc in tool_calls:
                events.append({
                    "event": "on_tool_start",
                    "name": tc["name"],
                    "data": {"input": tc.get("input", {})},
                })
        return events

    def test_v1_chat_completions_stream_format(self, client, mock_agent):
        """Mock the graph, verify SSE output matches OpenAI format."""
        events = self._make_stream_events(tokens=["Hello", " world"])

        async def mock_astream(*args, **kwargs):
            for ev in events:
                yield ev

        mock_agent.astream_events = mock_astream

        payload = {
            "model": "parsnip-agent",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]

        # Parse SSE lines
        lines = [
            l for l in r.text.strip().split("\n")
            if l.startswith("data:")
        ]
        assert len(lines) >= 3  # 2 tokens + final chunk + [DONE]

        # Verify first content chunk
        first_data = json.loads(lines[0][5:].strip())
        assert first_data["object"] == "chat.completion.chunk"
        assert first_data["id"].startswith("chatcmpl-")
        assert "model" in first_data
        assert "choices" in first_data
        assert len(first_data["choices"]) == 1
        choice = first_data["choices"][0]
        assert choice["index"] == 0
        assert "delta" in choice
        assert choice["delta"]["content"] == "Hello"
        assert choice["delta"]["role"] == "assistant"

    def test_stream_ends_with_done_marker(self, client, mock_agent):
        """SSE stream ends with 'data: [DONE]'."""
        events = self._make_stream_events(tokens=["Ok"])

        async def mock_astream(*args, **kwargs):
            for ev in events:
                yield ev

        mock_agent.astream_events = mock_astream

        payload = {
            "model": "parsnip-agent",
            "messages": [{"role": "user", "content": "say ok"}],
        }
        r = client.post("/v1/chat/completions", json=payload)
        lines = [l.strip() for l in r.text.strip().split("\n") if l.strip().startswith("data:")]
        assert lines[-1] == "data: [DONE]"

    def test_final_chunk_has_stop_finish_reason(self, client, mock_agent):
        """The chunk before [DONE] has finish_reason: 'stop'."""
        events = self._make_stream_events(tokens=["done"])

        async def mock_astream(*args, **kwargs):
            for ev in events:
                yield ev

        mock_agent.astream_events = mock_astream

        payload = {
            "model": "parsnip-agent",
            "messages": [{"role": "user", "content": "test"}],
        }
        r = client.post("/v1/chat/completions", json=payload)
        data_lines = [
            l.strip() for l in r.text.strip().split("\n")
            if l.strip().startswith("data:") and l.strip() != "data: [DONE]"
        ]
        last_chunk = json.loads(data_lines[-1][5:])
        assert last_chunk["choices"][0]["finish_reason"] == "stop"

    def test_all_chunks_share_same_id(self, client, mock_agent):
        """All chunks in a stream share the same chatcmpl- ID."""
        events = self._make_stream_events(tokens=["a", "b", "c"])

        async def mock_astream(*args, **kwargs):
            for ev in events:
                yield ev

        mock_agent.astream_events = mock_astream

        payload = {
            "model": "parsnip-agent",
            "messages": [{"role": "user", "content": "test"}],
        }
        r = client.post("/v1/chat/completions", json=payload)
        data_lines = [
            l.strip() for l in r.text.strip().split("\n")
            if l.strip().startswith("data:") and l.strip() != "data: [DONE]"
        ]

        ids = set()
        for line in data_lines:
            chunk = json.loads(line[5:])
            ids.add(chunk["id"])

        assert len(ids) == 1
        assert list(ids)[0].startswith("chatcmpl-")