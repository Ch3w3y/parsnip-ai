"""End-to-end pipeline test — verify the full OpenWebUI -> Pipeline -> Agent chain.

This test is marked as slow and integration; it requires the full stack to be running.
Run with:
  pytest tests/test_pipeline.py -v -m integration
"""

import json
import os
from urllib.parse import urljoin

import pytest

PIPELINE_AGENT_URL = os.environ.get("PIPELINE_AGENT_URL", "http://localhost:8000")


@pytest.mark.slow
@pytest.mark.integration
def test_pipeline_streaming_happy_path():
    """Send a minimal message through the pipeline streaming endpoint and verify tokens flow."""
    pytest.importorskip("requests")
    import requests

    # We call the agent directly here because the pipeline is just a middleware;
    # the real e2e would need OpenWebUI running. This validates the agent contract.
    payload = {
        "message": "say exactly 'pong' and nothing else",
        "thread_id": "e2e-test-stream",
    }
    try:
        r = requests.post(
            urljoin(PIPELINE_AGENT_URL, "/chat"),
            json=payload,
            stream=True,
            timeout=60,
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Agent not reachable")

    if r.status_code in (502, 503):
        pytest.skip(f"LLM backend unavailable ({r.status_code})")
    r.raise_for_status()

    tokens = []
    for raw_line in r.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if event.get("type") == "token":
            tokens.append(event.get("content", ""))
        elif event.get("type") == "done":
            break

    full_response = "".join(tokens)
    assert full_response, "No tokens received from streaming endpoint"
    # We don't assert exact content because LLM responses vary;
    # we just verify the pipeline mechanics work.


@pytest.mark.slow
@pytest.mark.integration
def test_pipeline_non_streaming_happy_path():
    """Send a minimal message through /chat/sync and verify response shape."""
    pytest.importorskip("requests")
    import requests

    payload = {
        "message": "say exactly 'pong' and nothing else",
        "thread_id": "e2e-test-sync",
    }
    try:
        r = requests.post(
            urljoin(PIPELINE_AGENT_URL, "/chat/sync"),
            json=payload,
            timeout=60,
        )
    except requests.exceptions.ConnectionError:
        pytest.skip("Agent not reachable")

    if r.status_code in (502, 503):
        pytest.skip(f"LLM backend unavailable ({r.status_code})")
    r.raise_for_status()

    data = r.json()
    assert "thread_id" in data
    assert "content" in data
    assert data["thread_id"] == payload["thread_id"]
    assert data["content"], "Empty response content"
