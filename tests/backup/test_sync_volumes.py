"""Unit tests for scripts/sync_volumes.py — filter + md5 + dry-run behavior."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sync_volumes.py"


@pytest.fixture(scope="module")
def sv_module():
    sys.path.insert(0, str(ROOT / "storage"))
    spec = importlib.util.spec_from_file_location("sync_volumes", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_volumes_listed(sv_module):
    expected = {"analysis_output", "owui_data", "pipelines_data"}
    assert set(sv_module.DEFAULT_VOLUMES.keys()) == expected


def test_should_skip_pyc(sv_module):
    assert sv_module.should_skip(Path("foo/bar.pyc"))


def test_should_skip_pycache_dir(sv_module):
    assert sv_module.should_skip(Path("agent/__pycache__/main.cpython-313.pyc"))


def test_should_skip_node_modules(sv_module):
    assert sv_module.should_skip(Path("frontend/node_modules/react/index.js"))


def test_should_keep_normal_file(sv_module):
    assert not sv_module.should_skip(Path("analysis/output/chart.png"))


def test_should_skip_ds_store(sv_module):
    assert sv_module.should_skip(Path("foo/.DS_Store"))


def test_md5_matches_gcs_format(sv_module, tmp_path):
    """GCS reports md5_hash as base64-encoded 16-byte digest. Our helper must match exactly."""
    target = tmp_path / "data.bin"
    payload = b"hello world" * 1000
    target.write_bytes(payload)

    expected = base64.b64encode(hashlib.md5(payload).digest()).decode("ascii")
    actual = sv_module.file_md5_b64(target)
    assert actual == expected


def test_sync_volume_skips_missing_mount(sv_module):
    """If the mount doesn't exist (volume not configured), return empty stats not raise."""
    stats = sv_module.sync_volume(gcs=None, name="phantom",
                                  local_root=Path("/nonexistent/mount"),
                                  dry_run=True)
    assert stats.uploaded == 0
    assert stats.errors == 0
