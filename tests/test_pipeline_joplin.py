"""Tests verifying Joplin enrichment dedup — the pipeline no longer fetches Joplin notes.

The agent already fetches Joplin content via its own tools (joplin_pg.py),
so the pipeline's redundant Joplin enrichment code was removed.
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure the pipelines directory is importable
sys.path.insert(0, ".")

from pipelines.research_agent import Pipeline


# ---------------------------------------------------------------------------
# 1. Removed methods do NOT exist on Pipeline
# ---------------------------------------------------------------------------

def test_fetch_joplin_note_removed():
    """_fetch_joplin_note should not exist on Pipeline instances."""
    pipe = Pipeline()
    assert not hasattr(pipe, "_fetch_joplin_note"), (
        "_fetch_joplin_note still exists on Pipeline — remove it"
    )


def test_enrich_with_joplin_removed():
    """_enrich_with_joplin should not exist on Pipeline instances."""
    pipe = Pipeline()
    assert not hasattr(pipe, "_enrich_with_joplin"), (
        "_enrich_with_joplin still exists on Pipeline — remove it"
    )


# ---------------------------------------------------------------------------
# 2. JOPLIN_MCP_URL removed from Valves
# ---------------------------------------------------------------------------

def test_joplin_mcp_url_not_in_valves():
    """JOPLIN_MCP_URL should not be a field on Valves."""
    valve_fields = set(Pipeline.Valves.model_fields.keys())
    assert "JOPLIN_MCP_URL" not in valve_fields, (
        f"JOPLIN_MCP_URL still in Valves fields: {valve_fields}"
    )


def test_joplin_mcp_url_not_in_valves_instance():
    """Pipeline instances should not carry JOPLIN_MCP_URL."""
    pipe = Pipeline()
    valve_attrs = set(pipe.valves.model_fields.keys())
    assert "JOPLIN_MCP_URL" not in valve_attrs


# ---------------------------------------------------------------------------
# 3. No joplin:// URL fetching logic in the pipeline source
# ---------------------------------------------------------------------------

def test_no_joplin_url_fetch_in_source():
    """The pipeline source should not contain joplin:// URL-fetching logic."""
    import pipelines.research_agent as mod

    source = inspect.getsource(mod)
    assert "joplin://x-callback-url/openNote" not in source, (
        "joplin:// deep-link matching still present in pipeline source"
    )


def test_no_enrich_with_joplin_in_source():
    """The pipeline source should not reference _enrich_with_joplin."""
    import pipelines.research_agent as mod

    source = inspect.getsource(mod)
    assert "_enrich_with_joplin" not in source, (
        "_enrich_with_joplin still referenced in pipeline source"
    )


def test_no_fetch_joplin_note_in_source():
    """The pipeline source should not reference _fetch_joplin_note."""
    import pipelines.research_agent as mod

    source = inspect.getsource(mod)
    assert "_fetch_joplin_note" not in source, (
        "_fetch_joplin_note still referenced in pipeline source"
    )


def test_no_joplin_mcp_url_in_source():
    """The pipeline source should not reference JOPLIN_MCP_URL."""
    import pipelines.research_agent as mod

    source = inspect.getsource(mod)
    assert "JOPLIN_MCP_URL" not in source, (
        "JOPLIN_MCP_URL still referenced in pipeline source"
    )


# ---------------------------------------------------------------------------
# 4. Pipeline still produces output (streaming + sync)
# ---------------------------------------------------------------------------

def _make_sse_events(events: list[dict]) -> bytes:
    """Encode a list of event dicts as SSE bytes (newline-separated)."""
    lines = []
    for ev in events:
        lines.append(f"data: {json.dumps(ev)}")
    return "\n".join(lines).encode("utf-8")


def test_streaming_produces_output():
    """Streaming pipeline should yield tokens when the agent responds."""
    sse_bytes = _make_sse_events([
        {"type": "token", "content": "Hello"},
        {"type": "token", "content": " world"},
        {"type": "done", "model_id": "test-model"},
    ])

    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.raise_for_status = MagicMock()
    mock_response.iter_lines.return_value = sse_bytes.split(b"\n")

    pipe = Pipeline()
    with patch("pipelines.research_agent.requests.post", return_value=mock_response):
        gen = pipe._stream_response(
            user_message="hi",
            body={"metadata": {"chat_id": "test-chat"}, "messages": []},
        )
        chunks = list(gen)

    combined = "".join(chunks)
    assert "Hello world" in combined
    assert "test-model" in combined


def test_sync_pipe_produces_output():
    """Non-streaming pipe() should return content from /chat/sync."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "content": "Sync response",
        "model_id": "sync-model",
    }

    pipe = Pipeline()
    with patch("pipelines.research_agent.requests.post", return_value=mock_response):
        result = pipe.pipe(
            user_message="hi",
            model_id="research-agent",
            messages=[],
            body={"metadata": {"chat_id": "test-chat"}, "messages": []},
        )

    assert "Sync response" in result
    assert "sync-model" in result


# ---------------------------------------------------------------------------
# import inspect used by source-checking tests
# ---------------------------------------------------------------------------
import inspect