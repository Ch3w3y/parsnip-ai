"""Tests for ingestion utility functions."""

import asyncio
import gzip
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))


class TestChunkText:
    def test_chunk_text_basic(self):
        from utils import chunk_text

        text = "one two three four five six seven eight nine ten"
        chunks = chunk_text(text, chunk_words=5, overlap_words=1)
        assert len(chunks) > 0
        assert "one" in chunks[0]

    def test_chunk_text_empty(self):
        from utils import chunk_text

        assert chunk_text("") == []

    def test_chunk_text_single_word(self):
        from utils import chunk_text

        chunks = chunk_text("hello", chunk_words=5, overlap_words=1)
        assert len(chunks) == 1
        assert chunks[0] == "hello"

    def test_chunk_text_overlap(self):
        from utils import chunk_text

        text = " ".join(str(i) for i in range(10))
        chunks = chunk_text(text, chunk_words=5, overlap_words=2)
        assert len(chunks) > 1
        first_words = chunks[0].split()
        second_words = chunks[1].split()
        overlap = set(first_words) & set(second_words)
        assert len(overlap) > 0


class TestEmbedBatch:
    @pytest.mark.asyncio
    async def test_embed_batch_success(self, mock_ollama):
        from utils import embed_batch

        texts = ["hello world", "test text"]
        result = await embed_batch(texts, model="test-model")
        assert result is not None
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_embed_batch_empty(self, mock_ollama):
        from utils import embed_batch

        result = await embed_batch([])
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_batch_whitespace_only(self, mock_ollama):
        from utils import embed_batch

        result = await embed_batch(["   ", "", "\n"])
        assert result is None


class TestSaveRaw:
    def test_save_raw_creates_file(self, tmp_path):
        from utils import save_raw, RAW_DATA_DIR

        with patch("utils.RAW_DATA_DIR", tmp_path):
            records = [{"id": 1, "title": "test"}]
            path = save_raw(records, "test_source")
            assert path.exists()
            assert path.suffixes == [".jsonl", ".gz"]

    def test_save_raw_writes_records(self, tmp_path):
        from utils import save_raw

        with patch("utils.RAW_DATA_DIR", tmp_path):
            records = [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]
            path = save_raw(records, "test_source")
            with gzip.open(path, "rt") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            assert len(lines) == 2
            assert lines[0]["title"] == "a"


class TestIterRaw:
    def test_iter_raw_reads_records(self, tmp_path):
        from utils import save_raw, iter_raw

        with patch("utils.RAW_DATA_DIR", tmp_path):
            records = [{"id": 1}, {"id": 2}, {"id": 3}]
            path = save_raw(records, "test_source")
            read_records = list(iter_raw(path))
            assert len(read_records) == 3
            assert read_records[0]["id"] == 1


class TestLatestRaw:
    def test_latest_raw_returns_newest(self, tmp_path):
        from utils import save_raw, latest_raw

        with patch("utils.RAW_DATA_DIR", tmp_path):
            save_raw([{"id": 1}], "test_source", "old")
            save_raw([{"id": 2}], "test_source", "new")
            latest = latest_raw("test_source")
            assert latest is not None

    def test_latest_raw_none_when_no_files(self, tmp_path):
        from utils import latest_raw

        with patch("utils.RAW_DATA_DIR", tmp_path):
            assert latest_raw("nonexistent") is None


class TestUpsertChunks:
    @pytest.mark.asyncio
    async def test_upsert_chunks(self, mock_db_connection):
        from utils import upsert_chunks

        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db_connection.execute = AsyncMock(return_value=mock_result)
        mock_db_connection.transaction = MagicMock()
        mock_db_connection.transaction.return_value.__aenter__ = AsyncMock()
        mock_db_connection.transaction.return_value.__aexit__ = AsyncMock()

        count = await upsert_chunks(
            mock_db_connection,
            source="test",
            source_id="doc_1",
            chunks=["test content"],
            embeddings=[[0.1] * 1024],
            metadata={"title": "test"},
        )
        assert count == 1


class TestBulkUpsertChunks:
    @pytest.mark.asyncio
    async def test_bulk_upsert_empty(self, mock_db_connection):
        from utils import bulk_upsert_chunks

        result = await bulk_upsert_chunks(mock_db_connection, [])
        assert result == 0

    @pytest.mark.asyncio
    async def test_bulk_upsert_rows(self, mock_db_connection):
        from utils import bulk_upsert_chunks

        mock_cursor = AsyncMock()
        mock_cursor.executemany = AsyncMock()
        mock_db_connection.cursor = AsyncMock(return_value=mock_cursor)
        mock_db_connection.transaction = MagicMock()
        mock_db_connection.transaction.return_value.__aenter__ = AsyncMock()
        mock_db_connection.transaction.return_value.__aexit__ = AsyncMock()

        rows = [
            (
                "test",
                "doc_1",
                0,
                "content",
                "abc123hash",
                {"title": "test"},
                [0.1] * 1024,
                "mxbai-embed-large",
            ),
        ]
        result = await bulk_upsert_chunks(mock_db_connection, rows)
        assert result == 1


class TestEmbedCircuitBreaker:
    def _make_cb(self):
        from utils import _EmbedCircuitBreaker, _CircuitState

        cb = _EmbedCircuitBreaker()
        assert cb._state == _CircuitState.CLOSED
        assert cb._consecutive_failures == 0
        return cb

    @pytest.mark.asyncio
    async def test_cb_starts_closed(self):
        cb = self._make_cb()
        assert await cb.is_open() is False

    @pytest.mark.asyncio
    async def test_cb_connect_error_trips(self):
        from utils import _CircuitState

        cb = self._make_cb()
        await cb.record_failure(httpx.ConnectError("Connection refused"))
        assert cb._consecutive_failures == 1
        assert cb._state == _CircuitState.CLOSED

        await cb.record_failure(httpx.ConnectError("Connection refused"))
        await cb.record_failure(httpx.ConnectError("Connection refused"))
        assert cb._consecutive_failures == 3
        assert cb._state == _CircuitState.OPEN
        assert await cb.is_open() is True

    @pytest.mark.asyncio
    async def test_cb_400_does_not_trip(self):
        cb = self._make_cb()
        response = MagicMock()
        response.status_code = 400
        exc = httpx.HTTPStatusError("400", request=MagicMock(), response=response)
        await cb.record_failure(exc)
        assert cb._consecutive_failures == 0
        assert await cb.is_open() is False

    @pytest.mark.asyncio
    async def test_cb_500_trips(self):
        from utils import _CircuitState

        cb = self._make_cb()
        response = MagicMock()
        response.status_code = 500
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=response)
        for _ in range(3):
            await cb.record_failure(exc)
        assert cb._state == _CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_cb_success_resets(self):
        from utils import _CircuitState

        cb = self._make_cb()
        response = MagicMock()
        response.status_code = 500
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=response)
        for _ in range(3):
            await cb.record_failure(exc)
        assert cb._state == _CircuitState.OPEN

        await cb.record_success()
        assert cb._state == _CircuitState.CLOSED
        assert cb._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_cb_escalating_cooldown(self):
        cb = self._make_cb()
        response = MagicMock()
        response.status_code = 503
        exc = httpx.HTTPStatusError("503", request=MagicMock(), response=response)

        for _ in range(5):
            await cb.record_failure(exc)
        assert cb._open_duration == 300.0

    @pytest.mark.asyncio
    async def test_cb_half_open_transition(self):
        from utils import _CircuitState

        cb = self._make_cb()
        response = MagicMock()
        response.status_code = 500
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=response)

        for _ in range(3):
            await cb.record_failure(exc)
        assert cb._state == _CircuitState.OPEN
        assert cb._opened_at is not None

        cb._opened_at = time.monotonic() - 31
        assert await cb.is_open() is False
        assert cb._state == _CircuitState.HALF_OPEN

        await cb.record_success()
        assert cb._state == _CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_reset_circuit_breaker(self):
        from utils import _CircuitState, reset_circuit_breaker, _embed_cb

        response = MagicMock()
        response.status_code = 500
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=response)
        for _ in range(3):
            await _embed_cb.record_failure(exc)
        assert _embed_cb._state == _CircuitState.OPEN

        await reset_circuit_breaker()
        assert _embed_cb._state == _CircuitState.CLOSED
        assert _embed_cb._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_embed_batch_fast_fails_when_open(self):
        from utils import embed_batch, _embed_cb, _CircuitState, reset_circuit_breaker

        response = MagicMock()
        response.status_code = 500
        exc = httpx.HTTPStatusError("500", request=MagicMock(), response=response)
        for _ in range(3):
            await _embed_cb.record_failure(exc)
        assert _embed_cb._state == _CircuitState.OPEN

        result = await embed_batch(["hello"])
        assert result is None

        await reset_circuit_breaker()

    @pytest.mark.asyncio
    async def test_embed_batch_success_resets_cb(self, mock_ollama):
        from utils import embed_batch, reset_circuit_breaker, _embed_cb, _CircuitState

        await reset_circuit_breaker()
        assert _embed_cb._state == _CircuitState.CLOSED

        result = await embed_batch(["test"], model="test-model")
        assert result is not None
        assert _embed_cb._consecutive_failures == 0
