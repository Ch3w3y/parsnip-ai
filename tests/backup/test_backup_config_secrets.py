"""Unit tests for scripts/backup_config.py — split tarball + age round-trip."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup_config.py"


@pytest.fixture(scope="module")
def cfg_module():
    sys.path.insert(0, str(ROOT / "storage"))
    spec = importlib.util.spec_from_file_location("backup_config", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_filter_excludes_excluded_dirs(cfg_module):
    """The plain tarball filter must reject __pycache__, .git, node_modules."""
    import tarfile as tf
    for bad in ("agent/__pycache__/foo.pyc", ".git/HEAD", "frontend/node_modules/x.js"):
        info = tf.TarInfo(name=bad)
        assert cfg_module._filter(info) is None, f"should have excluded {bad}"


def test_filter_excludes_secrets_from_plain_tarball(cfg_module):
    """Defense-in-depth: .env and gcs-key.json must NEVER appear in the plain bundle."""
    import tarfile as tf
    for secret in (".env", "gcs-key.json"):
        info = tf.TarInfo(name=secret)
        assert cfg_module._filter(info) is None, f"{secret} must be excluded from plain tarball"


def test_filter_keeps_normal_files(cfg_module):
    import tarfile as tf
    for ok in ("agent/main.py", "docker-compose.yml", "docs/ARCHITECTURE.md"):
        info = tf.TarInfo(name=ok)
        assert cfg_module._filter(info) is not None, f"should have kept {ok}"


def test_make_plain_tarball_excludes_secrets(cfg_module, tmp_path):
    """End-to-end: build a tarball from a fake project root containing .env, verify it's absent."""
    fake_root = tmp_path / "project"
    fake_root.mkdir()
    (fake_root / "docker-compose.yml").write_text("services: {}")
    (fake_root / ".env").write_text("SECRET=should_not_appear")
    (fake_root / "gcs-key.json").write_text('{"private_key":"PEM"}')
    (fake_root / "agent").mkdir()
    (fake_root / "agent" / "main.py").write_text("print('hi')")

    out = tmp_path / "plain.tar.gz"
    cfg_module.make_plain_tarball(fake_root, out)

    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    assert "docker-compose.yml" in names
    assert any("agent/main.py" in n for n in names)
    assert ".env" not in names
    assert "gcs-key.json" not in names


@pytest.mark.skipif(not shutil.which("age"), reason="age binary not installed")
def test_secrets_bundle_age_roundtrip(cfg_module, tmp_path):
    """Generate ephemeral age key, encrypt secrets, decrypt, verify content matches."""
    # Generate keypair
    keygen = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True)
    key_lines = keygen.stdout.splitlines()
    private_key = "\n".join(line for line in key_lines if not line.startswith("Public key:"))
    pub_line = next(line for line in key_lines if line.startswith("# public key:"))
    public_key = pub_line.split(": ", 1)[1].strip()

    key_path = tmp_path / "age.key"
    key_path.write_text(private_key)
    key_path.chmod(0o600)

    fake_root = tmp_path / "project"
    fake_root.mkdir()
    (fake_root / ".env").write_text("FOO=bar\nBAZ=qux")
    (fake_root / "gcs-key.json").write_text('{"private_key": "stub"}')

    encrypted = tmp_path / "secrets.tar.gz.age"
    ok = cfg_module.make_secrets_bundle(fake_root, public_key, encrypted)
    assert ok, "make_secrets_bundle should succeed when age + recipient + files all present"
    assert encrypted.exists()
    assert encrypted.stat().st_size > 0

    # Decrypt and verify
    decrypted_path = tmp_path / "secrets.tar.gz"
    subprocess.run(
        ["age", "-d", "-i", str(key_path), "-o", str(decrypted_path), str(encrypted)],
        check=True,
    )
    with tarfile.open(decrypted_path, "r:gz") as tar:
        names = tar.getnames()
        assert ".env" in names
        assert "gcs-key.json" in names
        env_member = tar.extractfile(".env")
        assert env_member is not None
        assert env_member.read().decode() == "FOO=bar\nBAZ=qux"


def test_make_secrets_bundle_returns_false_with_no_files(cfg_module, tmp_path):
    """If no secret files exist, returns False instead of producing an empty bundle."""
    if not shutil.which("age"):
        pytest.skip("age binary not installed")
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    out = tmp_path / "secrets.tar.gz.age"
    ok = cfg_module.make_secrets_bundle(empty_root, "age1placeholder", out)
    assert ok is False
    assert not out.exists()


def test_volume_manifest_structure(cfg_module, tmp_path):
    """Even when docker is unavailable, manifest returns the right structure."""
    manifest = cfg_module.collect_volume_manifest(tmp_path)
    assert "captured_at" in manifest
    assert "volumes" in manifest
    for vol in ("pgdata", "owui_data", "pipelines_data", "analysis_output"):
        assert vol in manifest["volumes"]
        assert "exists" in manifest["volumes"][vol]
