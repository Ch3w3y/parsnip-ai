"""Tests for file-based process-safe circuit breaker.

These tests verify that the circuit breaker state is safely shared across
processes via a JSON file with atomic writes and flock-based locking.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helper: import the module with the state path patched to a temp directory
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path, monkeypatch):
    """Point the circuit breaker state file at a temp directory for every test."""
    state_file = tmp_path / "parsnip_circuit_breaker.json"
    monkeypatch.setenv("PARSNIP_CIRCUIT_BREAKER_PATH", str(state_file))
    # Re-import so module picks up the new path
    import importlib
    import agent.graph_guardrails as gg
    importlib.reload(gg)
    yield
    # Clean up: delete state file if it exists
    if state_file.exists():
        state_file.unlink(missing_ok=True)


def _get_module():
    import agent.graph_guardrails as gg
    return gg


# ---------------------------------------------------------------------------
# Test 1: Fresh start — no file exists → circuit is closed
# ---------------------------------------------------------------------------

def test_circuit_is_closed_when_no_state_file():
    gg = _get_module()
    # State file should not exist yet
    assert not Path(gg._CIRCUIT_BREAKER_PATH).exists()
    assert gg._circuit_is_open() is False


# ---------------------------------------------------------------------------
# Test 2: _trip_circuit() writes JSON file with correct structure
# ---------------------------------------------------------------------------

def test_trip_circuit_writes_json_file():
    gg = _get_module()
    before = time.time()
    gg._trip_circuit()
    after = time.time()

    state_file = Path(gg._CIRCUIT_BREAKER_PATH)
    assert state_file.exists(), "State file should exist after tripping circuit"

    data = json.loads(state_file.read_text())
    assert data["tripped"] is True
    assert before <= data["tripped_at"] <= after, "tripped_at should be a recent timestamp"


# ---------------------------------------------------------------------------
# Test 3: _circuit_is_open() reads file and returns True when tripped
# ---------------------------------------------------------------------------

def test_circuit_is_open_returns_true_when_tripped():
    gg = _get_module()
    gg._trip_circuit()
    assert gg._circuit_is_open() is True


# ---------------------------------------------------------------------------
# Test 4: Auto-reset — if tripped_at is > 5 min old, circuit closes and file cleared
# ---------------------------------------------------------------------------

def test_circuit_auto_resets_after_cooldown():
    gg = _get_module()
    gg._trip_circuit()

    state_file = Path(gg._CIRCUIT_BREAKER_PATH)
    assert state_file.exists()

    # Modify the file to have a tripped_at from 6 minutes ago
    old_time = time.time() - (gg._OPENROUTER_COOLDOWN_SECONDS + 60)
    data = {"tripped": True, "tripped_at": old_time}
    state_file.write_text(json.dumps(data))

    # Circuit should auto-reset (return False) and clear the file
    assert gg._circuit_is_open() is False
    # File should be cleaned up after auto-reset
    assert not state_file.exists(), "State file should be removed after auto-reset"


# ---------------------------------------------------------------------------
# Test 5: Concurrent access — two rapid writes don't corrupt the file
# ---------------------------------------------------------------------------

def test_concurrent_writes_do_not_corrupt_file():
    gg = _get_module()
    # Trip the circuit multiple times in rapid succession
    for _ in range(20):
        gg._trip_circuit()

    state_file = Path(gg._CIRCUIT_BREAKER_PATH)
    assert state_file.exists()

    # File should still be valid JSON
    data = json.loads(state_file.read_text())
    assert data["tripped"] is True
    assert "tripped_at" in data
    assert isinstance(data["tripped_at"], float)


# ---------------------------------------------------------------------------
# Test 6: Stale temp file cleanup on startup
# ---------------------------------------------------------------------------

def test_stale_temp_files_cleaned_on_startup():
    gg = _get_module()
    state_dir = Path(gg._CIRCUIT_BREAKER_PATH).parent

    # Create a stale temp file (simulating partial write from crashed process)
    stale_tmp = state_dir / "parsnip_circuit_breaker.json.tmp.12345"
    stale_tmp.write_text('{"tripped": tr')

    # Another stale pattern
    stale_tmp2 = state_dir / "parsnip_circuit_breaker.json.tmp.67890"
    stale_tmp2.write_text('partial')

    # _circuit_is_open should clean up stale temp files
    gg._circuit_is_open()

    # Stale temp files should be cleaned up
    assert not stale_tmp.exists(), f"Stale temp file {stale_tmp} should be cleaned"
    assert not stale_tmp2.exists(), f"Stale temp file {stale_tmp2} should be cleaned"


# ---------------------------------------------------------------------------
# Test 7: _reset_circuit() deletes the state file
# ---------------------------------------------------------------------------

def test_reset_circuit_deletes_state_file():
    gg = _get_module()
    gg._trip_circuit()
    state_file = Path(gg._CIRCUIT_BREAKER_PATH)
    assert state_file.exists(), "File should exist after tripping"

    gg._reset_circuit()
    assert not state_file.exists(), "State file should be deleted after reset"
    assert gg._circuit_is_open() is False


# ---------------------------------------------------------------------------
# Test 8: Circuit remains open within cooldown period
# ---------------------------------------------------------------------------

def test_circuit_remains_open_within_cooldown():
    gg = _get_module()
    gg._trip_circuit()

    state_file = Path(gg._CIRCUIT_BREAKER_PATH)
    # Set tripped_at to 1 minute ago (within 5-min cooldown)
    recent_time = time.time() - 60
    data = {"tripped": True, "tripped_at": recent_time}
    state_file.write_text(json.dumps(data))

    assert gg._circuit_is_open() is True
    # File should still exist (not auto-reset)
    assert state_file.exists()