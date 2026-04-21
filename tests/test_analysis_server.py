"""Tests for the analysis server endpoints."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))


@pytest.fixture
def app():
    """Create the FastAPI test app."""
    with patch.dict(os.environ, {"JOPLIN_MCP_URL": "http://localhost:8090"}):
        from analysis.server import app

        yield app


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


class TestExecutePython:
    def test_execute_python_success(self, client, mock_subprocess_run, tmp_path):
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            response = client.post(
                "/execute/python",
                json={
                    "code": "print('hello')",
                    "description": "test",
                    "run_tests": False,
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "success"

    def test_execute_python_with_cache(self, client, mock_subprocess_run, tmp_path):
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            code = "print('cached')"
            response1 = client.post(
                "/execute/python",
                json={"code": code, "description": "test", "run_tests": False},
            )
            response2 = client.post(
                "/execute/python",
                json={"code": code, "description": "test", "run_tests": False},
            )
            assert response1.status_code == 200
            assert response2.status_code == 200


class TestExecuteR:
    def test_execute_r_success(self, client, mock_subprocess_run, tmp_path):
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            response = client.post(
                "/execute/r",
                json={
                    "code": "cat('hello')",
                    "description": "test",
                    "run_tests": False,
                },
            )
            assert response.status_code == 200


class TestOutputs:
    def test_list_outputs_empty(self, client, tmp_path):
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            response = client.get("/outputs")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 0

    def test_list_outputs_with_files(self, client, tmp_path):
        (tmp_path / "test_dir").mkdir()
        (tmp_path / "test_dir" / "result.csv").write_text("a,b\n1,2")
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            response = client.get("/outputs")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 1


class TestWorkspace:
    def test_workspace_list(self, client, tmp_path):
        (tmp_path / "subdir").mkdir()
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            response = client.get("/workspace/list")
            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 1

    def test_workspace_write_and_read(self, client, tmp_path):
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            client.post(
                "/workspace/write", json={"path": "test.txt", "content": "hello"}
            )
            response = client.get("/workspace/read", params={"path": "test.txt"})
            assert response.status_code == 200
            assert response.json()["content"] == "hello"

    def test_workspace_mkdir(self, client, tmp_path):
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            response = client.post("/workspace/mkdir", json={"path": "new_dir"})
            assert response.status_code == 200
            assert (tmp_path / "new_dir").exists()

    def test_workspace_delete(self, client, tmp_path):
        (tmp_path / "delete_me.txt").write_text("data")
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            response = client.post("/workspace/delete", json={"path": "delete_me.txt"})
            assert response.status_code == 200
            assert not (tmp_path / "delete_me.txt").exists()

    def test_workspace_move(self, client, tmp_path):
        (tmp_path / "old.txt").write_text("data")
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            response = client.post(
                "/workspace/move",
                params={"source": "old.txt", "destination": "new.txt"},
            )
            assert response.status_code == 200
            assert (tmp_path / "new.txt").exists()


class TestWorkspaceUserIsolation:
    def test_workspace_isolated_by_user(self, client, tmp_path):
        with patch("analysis.server.OUTPUT_DIR", tmp_path):
            client.post(
                "/workspace/write",
                json={"path": "user1.txt", "content": "user1 data"},
                headers={"X-User-ID": "user1"},
            )
            client.post(
                "/workspace/write",
                json={"path": "user2.txt", "content": "user2 data"},
                headers={"X-User-ID": "user2"},
            )
            user1_dir = tmp_path / "user1"
            user2_dir = tmp_path / "user2"
            assert user1_dir.exists()
            assert user2_dir.exists()
            assert (user1_dir / "user1.txt").exists()
            assert (user2_dir / "user2.txt").exists()


class TestCache:
    def test_cache_stats_initial(self, client):
        import analysis.server as srv

        srv._cache_stats["hits"] = 0
        srv._cache_stats["misses"] = 0
        response = client.get("/cache/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["hits"] == 0
        assert data["misses"] == 0

    def test_cache_clear(self, client):
        import analysis.server as srv

        srv._cache["test"] = {"data": "value"}
        srv._cache_stats["hits"] = 5
        response = client.post("/cache/clear")
        assert response.status_code == 200
        assert response.json()["status"] == "cleared"
        assert len(srv._cache) == 0


class TestSchedules:
    def test_schedule_create(self, client, tmp_path):
        with patch("analysis.server.SCHEDULES_DIR", tmp_path):
            with patch("analysis.server.JOBS_FILE", tmp_path / "jobs.json"):
                response = client.post(
                    "/schedule/create",
                    json={
                        "cron": "0 6 * * *",
                        "code": "print('scheduled')",
                        "language": "python",
                        "description": "daily test",
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert "job_id" in data
                assert data["status"] == "created"

    def test_schedule_list(self, client, tmp_path):
        jobs_file = tmp_path / "jobs.json"
        jobs_file.write_text(json.dumps({"job1": {"job_id": "job1"}}))
        with patch("analysis.server.SCHEDULES_DIR", tmp_path):
            with patch("analysis.server.JOBS_FILE", jobs_file):
                response = client.get("/schedule/list")
                assert response.status_code == 200
                assert response.json()["count"] == 1

    def test_schedule_get(self, client, tmp_path):
        jobs_file = tmp_path / "jobs.json"
        jobs_file.write_text(
            json.dumps({"abc12345": {"job_id": "abc12345", "cron": "0 6 * * *"}})
        )
        with patch("analysis.server.SCHEDULES_DIR", tmp_path):
            with patch("analysis.server.JOBS_FILE", jobs_file):
                response = client.get("/schedule/abc12345")
                assert response.status_code == 200
                assert response.json()["job_id"] == "abc12345"

    def test_schedule_get_not_found(self, client, tmp_path):
        jobs_file = tmp_path / "jobs.json"
        jobs_file.write_text("{}")
        with patch("analysis.server.SCHEDULES_DIR", tmp_path):
            with patch("analysis.server.JOBS_FILE", jobs_file):
                response = client.get("/schedule/nonexistent")
                assert response.status_code == 404

    def test_schedule_delete(self, client, tmp_path):
        jobs_file = tmp_path / "jobs.json"
        jobs_file.write_text(json.dumps({"del12345": {"job_id": "del12345"}}))
        with patch("analysis.server.SCHEDULES_DIR", tmp_path):
            with patch("analysis.server.JOBS_FILE", jobs_file):
                response = client.delete("/schedule/del12345")
                assert response.status_code == 200
                assert response.json()["status"] == "deleted"

    def test_schedule_run(self, client, tmp_path):
        jobs_file = tmp_path / "jobs.json"
        jobs_file.write_text(
            json.dumps(
                {
                    "run12345": {
                        "job_id": "run12345",
                        "code": "print('hello')",
                        "language": "python",
                        "cron": "0 6 * * *",
                    }
                }
            )
        )
        with patch("analysis.server.SCHEDULES_DIR", tmp_path):
            with patch("analysis.server.JOBS_FILE", jobs_file):
                response = client.post("/schedule/run12345/run")
                assert response.status_code == 200
                assert response.json()["status"] == "triggered"
