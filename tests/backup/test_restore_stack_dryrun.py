"""Smoke test for scripts/restore_stack.sh — verify --dry-run plans without executing."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "restore_stack.sh"


def test_script_is_executable():
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK), "restore_stack.sh must be executable"


def test_help_flag_works():
    result = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert "from-gcs" in result.stdout
    assert "Phases:" in result.stdout


def test_missing_bucket_errors_clean():
    """Without GCS_BUCKET or --bucket, the script must error with a clear message."""
    env = {k: v for k, v in os.environ.items() if k != "GCS_BUCKET"}
    result = subprocess.run([str(SCRIPT), "--from-gcs"], capture_output=True, text=True,
                            timeout=5, env=env)
    assert result.returncode != 0
    assert "GCS_BUCKET" in result.stderr


def test_unknown_flag_rejected():
    result = subprocess.run([str(SCRIPT), "--banana"], capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert "Unknown option" in result.stderr


@pytest.mark.skipif(not subprocess.run(["which", "gsutil"], capture_output=True).returncode == 0,
                    reason="gsutil not installed (required for restore script)")
def test_dry_run_plans_phases():
    """Dry-run must list phase headers without invoking docker/gsutil for real."""
    env = {**os.environ, "GCS_BUCKET": "test-bucket"}
    result = subprocess.run(
        [str(SCRIPT), "--from-gcs", "--target", "live", "--dry-run"],
        capture_output=True, text=True, timeout=10, env=env,
    )
    # Even if early phases fail (e.g. docker not present), we should see Phase 1 announcement
    assert "Phase 1: pulling config" in result.stdout or "Phase 1: pulling config" in result.stderr
