"""Unit tests for scripts/backup_kb.py — manifest lifecycle and table catalog."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup_kb.py"


@pytest.fixture(scope="module")
def kb_module():
    """Import scripts/backup_kb.py as a module without running main()."""
    sys.path.insert(0, str(ROOT / "storage"))
    spec = importlib.util.spec_from_file_location("backup_kb", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_table_catalog_covers_critical_tables(kb_module):
    names = {t.name for t in kb_module.ALL_TABLES}
    must_have = {
        "knowledge_chunks", "agent_memories",
        "notes", "notebooks", "note_resources", "note_tags", "tags",
        "hitl_sessions", "thread_metadata",
        "forex_rates", "world_bank_data",
        "checkpoints", "checkpoint_blobs", "checkpoint_writes",
    }
    missing = must_have - names
    assert not missing, f"backup catalog missing critical tables: {missing}"


def test_knowledge_chunks_has_vector_flag(kb_module):
    spec = next(t for t in kb_module.ALL_TABLES if t.name == "knowledge_chunks")
    assert spec.has_vector is True
    assert spec.cursor_column == "updated_at"


def test_note_resources_marked_bytea(kb_module):
    spec = next(t for t in kb_module.ALL_TABLES if t.name == "note_resources")
    assert spec.has_bytea is True


def test_manifest_roundtrip_local(tmp_path, kb_module):
    """Save then load a manifest using the local fallback (no GCS)."""
    manifest = {
        "version": 1,
        "tables": {
            "knowledge_chunks": {"last_cursor": "2026-04-25T10:00:00+00:00",
                                 "last_run": "2026-04-25T10:05:00+00:00",
                                 "last_mode": "incremental"},
        },
    }
    kb_module.save_manifest(gcs=None, local_dir=tmp_path, manifest=manifest, use_gcs=False)
    loaded = kb_module.load_manifest(gcs=None, local_dir=tmp_path, use_gcs=False)
    assert loaded == manifest


def test_manifest_initialises_when_missing(tmp_path, kb_module):
    loaded = kb_module.load_manifest(gcs=None, local_dir=tmp_path, use_gcs=False)
    assert loaded == {"version": 1, "tables": {}}


def test_junction_table_has_no_cursor(kb_module):
    """note_tags is a junction — must be marked cursor=None so we always full-snapshot it."""
    spec = next(t for t in kb_module.ALL_TABLES if t.name == "note_tags")
    assert spec.cursor_column is None


def test_langgraph_tables_use_wildcard_columns(kb_module):
    """LangGraph schema evolves between versions — backup uses SELECT * defensively."""
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        spec = next(t for t in kb_module.ALL_TABLES if t.name == table)
        assert spec.columns == ["*"], f"{table} should use SELECT *"
