"""Tests for stuck ingestion job recovery."""

import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "ingestion")))

from ingestion.utils import recover_stuck_jobs


def _make_execute_result(rowcount: int = 0, rows: list | None = None):
    """Build a mock awaitable that acts like conn.execute() return value."""
    result = AsyncMock()
    resultrowcount = rowcount
    # For the UPDATE, we need result that supports .rowcount
    # execute returns a cursor-like object
    mock_cursor = AsyncMock()
    mock_cursor.rowcount = rowcount
    # For the SELECT subquery, we need fetchall
    if rows is not None:
        mock_cursor.fetchall = AsyncMock(return_value=rows)
        mock_cursor.fetchone = AsyncMock(return_value=rows[0] if rows else None)
    return mock_cursor


class TestRecoverStuckJobs:
    """Tests for recover_stuck_jobs()."""

    @pytest.mark.asyncio
    async def test_marks_old_running_jobs_as_failed(self):
        """Jobs running >2h should be marked as failed."""
        mock_conn = AsyncMock()
        # The SELECT returns stuck jobs
        select_cursor = AsyncMock()
        select_cursor.fetchall = AsyncMock(return_value=[
            (1, "news", datetime(2026, 1, 1, tzinfo=timezone.utc)),
            (2, "arxiv", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ])
        # The UPDATE returns rowcount = 2 (two stuck jobs)
        update_cursor = AsyncMock()
        update_cursor.rowcount = 2
        mock_conn.execute = AsyncMock(side_effect=[select_cursor, update_cursor])

        count = await recover_stuck_jobs(mock_conn)
        assert count == 2
        # Verify execute was called twice (SELECT + UPDATE)
        assert mock_conn.execute.call_count == 2
        # The second call should be the UPDATE
        update_call_args = mock_conn.execute.call_args_list[1]
        sql = update_call_args[0][0]
        assert "UPDATE" in sql
        assert "ingestion_jobs" in sql
        assert "'failed'" in sql
        assert "'running'" in sql

    @pytest.mark.asyncio
    async def test_returns_count_of_recovered_jobs(self):
        """Should return the exact count of jobs that were recovered."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 5
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        count = await recover_stuck_jobs(mock_conn)
        assert count == 5

    @pytest.mark.asyncio
    async def test_does_not_touch_recent_running_jobs(self):
        """With a very large timeout, recent running jobs should not be touched.

        Verifies the SQL uses the timeout_hours parameter correctly — if we pass
        timeout_hours=10000, the INTERVAL clause should reflect that.
        """
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 0
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        count = await recover_stuck_jobs(mock_conn, timeout_hours=10000)
        assert count == 0
        # Verify the SQL includes the large timeout
        call_args = mock_conn.execute.call_args
        sql = call_args[0][0]
        assert "10000" in sql

    @pytest.mark.asyncio
    async def test_does_not_touch_done_or_failed_jobs(self):
        """The SQL should only update rows WHERE status='running', not done/failed."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 0
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        await recover_stuck_jobs(mock_conn, timeout_hours=2)
        call_args = mock_conn.execute.call_args
        sql = call_args[0][0]
        # The WHERE clause must filter on status='running' specifically
        assert "status='running'" in sql or "status = 'running'" in sql

    @pytest.mark.asyncio
    async def test_custom_timeout_via_env_variable(self):
        """If timeout_hours is None, should read from INGESTION_JOB_TIMEOUT_HOURS env var."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 1
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        with patch.dict(os.environ, {"INGESTION_JOB_TIMEOUT_HOURS": "0.5"}):
            count = await recover_stuck_jobs(mock_conn, timeout_hours=None)
            assert count == 1
            call_args = mock_conn.execute.call_args
            sql = call_args[0][0]
            # Should use the env var value 0.5, not the default 2
            assert "0.5" in sql

    @pytest.mark.asyncio
    async def test_default_timeout_is_2_hours(self):
        """Without env var or explicit timeout, default should be 2 hours."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 0
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        # Ensure env var is NOT set
        env = os.environ.copy()
        env.pop("INGESTION_JOB_TIMEOUT_HOURS", None)
        with patch.dict(os.environ, env, clear=True):
            await recover_stuck_jobs(mock_conn, timeout_hours=None)
            call_args = mock_conn.execute.call_args
            sql = call_args[0][0]
            assert "2" in sql

    @pytest.mark.asyncio
    async def test_zero_recovered_returns_zero(self):
        """When no stuck jobs exist, should return 0."""
        mock_conn = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.rowcount = 0
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

        count = await recover_stuck_jobs(mock_conn)
        assert count == 0