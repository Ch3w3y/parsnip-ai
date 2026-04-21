"""Tests for workspace management tools."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))


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


class MockAsyncClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return MockResponse(json_data={"status": "ok"})

    async def post(self, url, json=None, **kwargs):
        self.calls.append(("post", url, json))
        return MockResponse(
            json_data={"status": "ok", "path": json.get("path", "") if json else ""}
        )


@pytest.mark.asyncio
async def test_list_workspace():
    from tools.workspace import list_workspace

    with patch("tools.workspace.httpx.AsyncClient", return_value=MockAsyncClient()):
        result = await list_workspace.ainvoke({"path": ""})
        assert "ok" in result or "status" in result


@pytest.mark.asyncio
async def test_read_workspace_file():
    from tools.workspace import read_workspace_file

    client = MockAsyncClient()
    with patch("tools.workspace.httpx.AsyncClient", return_value=client):
        result = await read_workspace_file.ainvoke({"path": "test.txt"})
        assert result is not None


@pytest.mark.asyncio
async def test_write_workspace_file():
    from tools.workspace import write_workspace_file

    client = MockAsyncClient()
    with patch("tools.workspace.httpx.AsyncClient", return_value=client):
        result = await write_workspace_file.ainvoke(
            {"path": "test.txt", "content": "hello"}
        )
        assert result is not None
        assert any(call[0] == "post" and "write" in call[1] for call in client.calls)


@pytest.mark.asyncio
async def test_make_workspace_dir():
    from tools.workspace import make_workspace_dir

    client = MockAsyncClient()
    with patch("tools.workspace.httpx.AsyncClient", return_value=client):
        result = await make_workspace_dir.ainvoke({"path": "new_dir"})
        assert result is not None


@pytest.mark.asyncio
async def test_delete_workspace_item():
    from tools.workspace import delete_workspace_item

    client = MockAsyncClient()
    with patch("tools.workspace.httpx.AsyncClient", return_value=client):
        result = await delete_workspace_item.ainvoke({"path": "old_file.txt"})
        assert result is not None


@pytest.mark.asyncio
async def test_move_workspace_item():
    from tools.workspace import move_workspace_item

    client = MockAsyncClient()
    with patch("tools.workspace.httpx.AsyncClient", return_value=client):
        result = await move_workspace_item.ainvoke(
            {"source": "old.txt", "destination": "new.txt"}
        )
        assert result is not None


@pytest.mark.asyncio
async def test_execute_bash_command():
    from tools.workspace import execute_bash_command

    client = MockAsyncClient()
    with patch("tools.workspace.httpx.AsyncClient", return_value=client):
        result = await execute_bash_command.ainvoke(
            {"command": "ls -la", "workdir": "", "timeout": 30}
        )
        assert result is not None


@pytest.mark.asyncio
async def test_write_and_execute_script():
    from tools.workspace import write_and_execute_script

    client = MockAsyncClient()
    with patch("tools.workspace.httpx.AsyncClient", return_value=client):
        result = await write_and_execute_script.ainvoke(
            {
                "path": "test.py",
                "code": "print('hello')",
                "language": "python",
                "run_tests": True,
            }
        )
        assert result is not None
        assert any(
            call[0] == "post" and "write_and_execute" in call[1]
            for call in client.calls
        )


@pytest.mark.asyncio
async def test_execute_workspace_script():
    from tools.workspace import execute_workspace_script

    client = MockAsyncClient()
    with patch("tools.workspace.httpx.AsyncClient", return_value=client):
        result = await execute_workspace_script.ainvoke(
            {
                "path": "analysis.py",
                "code": "import pandas; print('done')",
                "language": "python",
            }
        )
        assert result is not None
        call_json = [c[2] for c in client.calls if c[0] == "post"][0]
        assert call_json["run_tests"] is False
