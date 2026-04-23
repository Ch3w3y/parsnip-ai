"""Tests for OpenAI-compatible /v1/chat/completions and /v1/models endpoints.

These tests mock the LangGraph agent to verify the OpenAI format conversion
without requiring a running LLM backend.

Run:
  pytest tests/test_openai_compat.py -v
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ──────────────────────────────────────────────────────────────────


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
    """Create a TestClient with the agent patched before the app starts."""
    # Patch the module-level `agent` var and prevent lifespan from overwriting it.
    # We also patch build_graph and _load_l1_memory to avoid DB connections.
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


# ── /v1/models tests ─────────────────────────────────────────────────────────


class TestV1Models:
    def test_returns_model_list(self, client):
        """GET /v1/models returns OpenAI-compatible model list."""
        r = client.get("/v1/models")
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)
        assert len(data["data"]) >= 1

    def test_model_has_required_fields(self, client):
        """Each model in the list has id, object, owned_by."""
        r = client.get("/v1/models")
        data = r.json()
        model = data["data"][0]
        assert model["id"] == "parsnip-agent"
        assert model["object"] == "model"
        assert model["owned_by"] == "parsnip"


# ── /v1/chat/completions tests ───────────────────────────────────────────────


class TestV1ChatCompletions:
    def _make_stream_events(self, tokens=None, tool_calls=None, error=None):
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
                events.append({
                    "event": "on_tool_end",
                    "name": tc["name"],
                    "data": {},
                })
        return events

    def test_rejects_empty_messages(self, client):
        """POST /v1/chat/completions with no user message returns 400."""
        payload = {"model": "parsnip-agent", "messages": []}
        r = client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 400

    def test_stream_format_basic(self, client, mock_agent):
        """SSE chunks have OpenAI chat.completion.chunk structure."""
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
        assert r.headers["content-type"] == "text/event-stream; charset=utf-8"

        # Parse SSE lines
        lines = [l for l in r.text.strip().split("\n") if l.startswith("data:")]
        assert len(lines) >= 3  # 2 tokens + final chunk + [DONE]

        # Verify first content chunk
        first_data = json.loads(lines[0][5:])  # strip "data: "
        assert first_data["object"] == "chat.completion.chunk"
        assert first_data["id"].startswith("chatcmpl-")
        assert first_data["model"]  # must have model field
        assert "choices" in first_data
        assert len(first_data["choices"]) == 1
        choice = first_data["choices"][0]
        assert choice["index"] == 0
        assert "delta" in choice
        assert choice["delta"]["content"] == "Hello"

    def test_stream_ends_with_done(self, client, mock_agent):
        """Stream ends with 'data: [DONE]'."""
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
        # Last SSE line should be "data: [DONE]"
        assert lines[-1] == "data: [DONE]"

    def test_final_chunk_has_stop_reason(self, client, mock_agent):
        """Second-to-last chunk has finish_reason: 'stop'."""
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
        lines = [l.strip() for l in r.text.strip().split("\n") if l.strip().startswith("data:") and l.strip() != "data: [DONE]"]
        last_chunk = json.loads(lines[-1][5:])
        assert last_chunk["choices"][0]["finish_reason"] == "stop"

    def test_messages_extraction_uses_last_user(self, client, mock_agent):
        """The last user message in the messages array is used as the query."""
        captured_state = {}

        events = self._make_stream_events(tokens=["response"])
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
        # The last message in state should be the last user message
        assert "messages" in captured_state, f"State keys: {list(captured_state.keys())}"
        last_msg = captured_state["messages"][-1]
        from langchain_core.messages import HumanMessage
        assert isinstance(last_msg, HumanMessage)
        assert last_msg.content == "second question"

    def test_tool_calls_converted(self, client, mock_agent):
        """Tool start events are converted to OpenAI tool_calls format."""
        events = self._make_stream_events(
            tokens=["Let me search"],
            tool_calls=[{"name": "web_search", "input": {"query": "test"}}],
        )
        async def mock_astream(*args, **kwargs):
            for ev in events:
                yield ev

        mock_agent.astream_events = mock_astream

        payload = {
            "model": "parsnip-agent",
            "messages": [{"role": "user", "content": "search for test"}],
        }
        r = client.post("/v1/chat/completions", json=payload)

        # Parse all SSE data lines
        lines = [l.strip() for l in r.text.strip().split("\n") if l.strip().startswith("data:") and l.strip() != "data: [DONE]"]

        # Find the tool call chunk
        tool_call_chunks = []
        for line in lines:
            chunk = json.loads(line[5:])
            delta = chunk["choices"][0]["delta"]
            if "tool_calls" in delta:
                tool_call_chunks.append(delta["tool_calls"])

        assert len(tool_call_chunks) == 1, f"Expected 1 tool call chunk, got {len(tool_call_chunks)}. All deltas: {[json.loads(l[5:])['choices'][0]['delta'] for l in lines]}"
        tc = tool_call_chunks[0][0]
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "web_search"
        # Arguments should be valid JSON
        args = json.loads(tc["function"]["arguments"])
        assert args == {"query": "test"}
        # Must have an id
        assert tc["id"].startswith("call_")
        # Must have an index
        assert isinstance(tc["index"], int)

    def test_chatcmpl_id_consistent(self, client, mock_agent):
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
        lines = [l.strip() for l in r.text.strip().split("\n") if l.strip().startswith("data:") and l.strip() != "data: [DONE]"]

        ids = set()
        for line in lines:
            chunk = json.loads(line[5:])
            ids.add(chunk["id"])

        # All content chunks + final chunk should share one ID
        assert len(ids) == 1
        ids_list = list(ids)
        assert ids_list[0].startswith("chatcmpl-")

    def test_delta_role_only_on_first_chunk(self, client, mock_agent):
        """The 'role' field in delta appears only on the first chunk."""
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
        lines = [l.strip() for l in r.text.strip().split("\n") if l.strip().startswith("data:") and l.strip() != "data: [DONE]"]

        content_chunks = []
        for line in lines:
            chunk = json.loads(line[5:])
            if chunk["choices"][0]["delta"].get("content") or chunk["choices"][0]["delta"] == {}:
                content_chunks.append(chunk)

        # The first chunk should have role: assistant
        first_delta = content_chunks[0]["choices"][0]["delta"]
        assert first_delta.get("role") == "assistant"