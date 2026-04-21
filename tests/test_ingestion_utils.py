"""Tests for ingestion utility functions."""

import gzip
import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

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
                {"title": "test"},
                [0.1] * 1024,
                "mxbai-embed-large",
            ),
        ]
        result = await bulk_upsert_chunks(mock_db_connection, rows)
        assert result == 1
