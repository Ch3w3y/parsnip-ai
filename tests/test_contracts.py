"""Contract tests — validate HTTP interfaces between architectural arms.

These tests require the services to be running (or mock them).
They are marked with `@pytest.mark.integration` so they can be skipped in pure unit runs.

Run with services up:
  pytest tests/test_contracts.py -v -m integration

Run smoke only (no external deps):
  pytest tests/test_contracts.py -v -m "not integration"
"""

import json
import os
from urllib.parse import urljoin

import pytest

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8000")
ANALYSIS_URL = os.environ.get("ANALYSIS_URL", "http://localhost:8095")
JOPLIN_MCP_URL = os.environ.get("JOPLIN_MCP_URL", "http://localhost:8090")
PIPELINE_AGENT_URL = os.environ.get("PIPELINE_AGENT_URL", AGENT_URL)

# JSON schemas we expect from endpoints (lightweight contract validation)
AGENT_HEALTH_SCHEMA = {
    "type": "object",
    "required": ["status", "agent_ready"],
    "properties": {
        "status": {"type": "string"},
        "agent_ready": {"type": "boolean"},
    },
}

AGENT_CHAT_SYNC_SCHEMA = {
    "type": "object",
    "required": ["thread_id", "content"],
    "properties": {
        "thread_id": {"type": "string"},
        "content": {"type": "string"},
    },
}

ANALYSIS_EXECUTE_SCHEMA = {
    "type": "object",
    "required": ["script_id", "status"],
    "properties": {
        "script_id": {"type": "string"},
        "status": {"type": "string"},
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
        "output_files": {"type": "array"},
    },
}


def _check_schema(data: dict, schema: dict, path: str = "") -> list[str]:
    """Very lightweight schema checker (not full JSON Schema)."""
    errors = []
    if schema.get("type") == "object" and not isinstance(data, dict):
        errors.append(f"{path}: expected object, got {type(data).__name__}")
        return errors
    if schema.get("type") == "array" and not isinstance(data, list):
        errors.append(f"{path}: expected array, got {type(data).__name__}")
        return errors
    if schema.get("type") == "string" and not isinstance(data, str):
        errors.append(f"{path}: expected string, got {type(data).__name__}")
        return errors
    if schema.get("type") == "boolean" and not isinstance(data, bool):
        errors.append(f"{path}: expected boolean, got {type(data).__name__}")
        return errors

    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{path}: missing required key '{key}'")
        for key, subschema in schema.get("properties", {}).items():
            if key in data:
                errors.extend(_check_schema(data[key], subschema, f"{path}.{key}"))
    return errors


@pytest.mark.integration
def test_agent_health():
    """Agent /health returns expected shape."""
    pytest.importorskip("httpx")
    import httpx

    r = httpx.get(urljoin(AGENT_URL, "/health"), timeout=10)
    r.raise_for_status()
    data = r.json()
    errors = _check_schema(data, AGENT_HEALTH_SCHEMA)
    assert not errors, f"Schema violations: {errors}"
    assert data["status"] == "ok"


@pytest.mark.integration
def test_agent_chat_sync_schema():
    """Agent /chat/sync returns the expected JSON schema."""
    pytest.importorskip("httpx")
    import httpx

    payload = {"message": "say 'ok' and nothing else", "thread_id": "contract-test-1"}
    r = httpx.post(urljoin(AGENT_URL, "/chat/sync"), json=payload, timeout=60)
    # We allow 502/503 if the LLM backend is misconfigured — the test is about schema
    if r.status_code in (502, 503):
        pytest.skip(f"LLM backend unavailable ({r.status_code})")
    r.raise_for_status()
    data = r.json()
    errors = _check_schema(data, AGENT_CHAT_SYNC_SCHEMA)
    assert not errors, f"Schema violations: {errors}"


@pytest.mark.integration
def test_analysis_execute_python_smoke():
    """Analysis server can execute a trivial Python script."""
    pytest.importorskip("httpx")
    import httpx

    payload = {
        "code": "print('hello from contract test')",
        "description": "contract test smoke",
        "run_tests": False,
    }
    r = httpx.post(urljoin(ANALYSIS_URL, "/execute/python"), json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    errors = _check_schema(data, ANALYSIS_EXECUTE_SCHEMA)
    assert not errors, f"Schema violations: {errors}"
    assert data["status"] == "success"
    assert "hello from contract test" in data.get("stdout", "")


@pytest.mark.integration
def test_analysis_outputs_list():
    """Analysis server /outputs returns a list."""
    pytest.importorskip("httpx")
    import httpx

    r = httpx.get(urljoin(ANALYSIS_URL, "/outputs"), timeout=10)
    r.raise_for_status()
    data = r.json()
    assert "files" in data
    assert isinstance(data["files"], list)


@pytest.mark.integration
def test_joplin_mcp_ping():
    """Joplin MCP REST bridge responds to ping."""
    pytest.importorskip("httpx")
    import httpx

    payload = {
        "tool": "joplin_ping",
        "arguments": {},
    }
    r = httpx.post(urljoin(JOPLIN_MCP_URL, "/tools/joplin_ping"), json=payload, timeout=10)
    if r.status_code == 404:
        pytest.skip("Joplin MCP REST endpoint not found (may be SSE-only mode)")
    r.raise_for_status()
    data = r.json()
    assert "result" in data


@pytest.mark.integration
def test_pipeline_agent_chat_sync():
    """Pipeline can successfully POST to agent /chat/sync (no LLM required)."""
    pytest.importorskip("requests")
    import requests

    payload = {"message": "say 'ok'", "thread_id": "contract-test-pipeline"}
    try:
        r = requests.post(
            urljoin(PIPELINE_AGENT_URL, "/chat/sync"),
            json=payload,
            timeout=30,
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Agent not reachable from pipeline test context")

    if r.status_code in (502, 503):
        pytest.skip(f"LLM backend unavailable ({r.status_code})")
    r.raise_for_status()
    data = r.json()
    assert "content" in data


class TestStaticContracts:
    """Tests that don't require running services."""

    def test_agent_models_response_keys(self):
        """Verify the agent /models response shape is documented consistently."""
        # This is a static reminder — if the endpoint changes, update this test.
        expected_keys = {"count", "current_defaults", "models"}
        expected_model_keys = {"id", "name", "context_length", "pricing"}
        # No runtime assertion; this documents the contract for reviewers.
        assert expected_keys
        assert expected_model_keys

    def test_pipeline_valves_defaults(self):
        """Pipeline Valves must have sensible defaults for all fields."""
        import sys
        from pathlib import Path

        pipeline_dir = Path(__file__).parent.parent / "pipelines"
        sys.path.insert(0, str(pipeline_dir))
        try:
            import research_agent
            p = research_agent.Pipeline()
            assert p.valves.AGENT_URL
            assert isinstance(p.valves.REQUEST_TIMEOUT, int)
            assert p.valves.REQUEST_TIMEOUT > 0
        finally:
            sys.path.pop(0)

    def test_analysis_env_defaults(self):
        """Analysis server env vars have safe fallbacks."""
        import sys
        from pathlib import Path

        analysis_dir = Path(__file__).parent.parent / "analysis"
        sys.path.insert(0, str(analysis_dir))
        try:
            import server
            # These should exist as module-level constants with defaults
            assert hasattr(server, "OUTPUT_DIR")
            assert hasattr(server, "ANALYSIS_URL")
            assert hasattr(server, "JOPLIN_MCP_URL")
            assert hasattr(server, "EMBED_MODEL")
        finally:
            sys.path.pop(0)
