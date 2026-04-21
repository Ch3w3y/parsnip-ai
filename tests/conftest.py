import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_subprocess_run():
    """Mock subprocess.run for script execution."""
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(
            returncode=0,
            stdout="test output",
            stderr="",
        )
        yield mock


@pytest.fixture
def mock_httpx_client():
    """Mock httpx.AsyncClient for API calls."""

    class MockResponse:
        def __init__(self, status_code=200, json_data=None, text=""):
            self.status_code = status_code
            self._json_data = json_data or {}
            self.text = text or json.dumps(json_data)

        def json(self):
            return self._json_data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            return MockResponse(json_data={"status": "ok"})

        async def post(self, url, json=None, **kwargs):
            return MockResponse(
                json_data={"status": "ok", "path": json.get("path", "")}
            )

    with patch("httpx.AsyncClient", return_value=MockClient()):
        yield MockClient


@pytest.fixture
def mock_ollama():
    """Mock Ollama embedding endpoint."""

    class MockResponse:
        def __init__(self):
            self.status_code = 200

        def json(self):
            return {"embeddings": [[0.1] * 1024]}

        def raise_for_status(self):
            pass

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, **kwargs):
            return MockResponse()

    with patch("httpx.AsyncClient", return_value=MockClient()):
        yield MockClient


@pytest.fixture
def mock_db_connection():
    """Mock async psycopg connection."""
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.executemany = AsyncMock()
    mock_conn.cursor = AsyncMock(return_value=mock_cursor)
    mock_conn.execute = AsyncMock(return_value=AsyncMock(rowcount=1))
    mock_conn.transaction = MagicMock()
    mock_conn.transaction.return_value.__aenter__ = AsyncMock()
    mock_conn.transaction.return_value.__aexit__ = AsyncMock()
    return mock_conn


@pytest.fixture
def mock_analysis_server():
    """Mock the analysis FastAPI app."""
    from unittest.mock import patch as p

    with p("analysis.server.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="output", stderr="")
        yield mock_run


@pytest.fixture
def temp_output_dir(tmp_path):
    """Provide a temporary output directory."""
    with patch("analysis.server.OUTPUT_DIR", tmp_path):
        yield tmp_path
