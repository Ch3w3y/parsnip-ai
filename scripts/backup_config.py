#!/usr/bin/env python3
"""
Backup project config + code + secrets to GCS.

Splits output into THREE artifacts to limit blast radius if the bucket is
exfiltrated without the age key:

  1. config.tar.gz          (PLAIN)  — code subset, docker-compose, manifest
  2. secrets.tar.gz.age     (ENCRYPTED) — .env, gcs-key.json
  3. volume_manifest.json   (PLAIN)  — list of docker volumes + sizes (for restore planning)

The encrypted bundle uses age (https://age-encryption.org). The recipient public
key comes from the AGE_RECIPIENT environment variable (set during setup_age_key.sh).
If AGE_RECIPIENT is unset or `age` is missing, secrets are SKIPPED with a clear
warning — never silently embedded in the plain tarball.

Usage:
  python backup_config.py                  # full split, upload to GCS
  python backup_config.py --local          # local only
  python backup_config.py --no-secrets     # skip secrets bundle entirely
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backup_config")

# Plain (committable / shareable) project files
PLAIN_INCLUDE = [
    "docker-compose.yml",
    "pyproject.toml",
    "uv.lock",
    "pi-ctl.sh",
    "run_ingest.sh",
    "agent/",
    "pipelines/",
    "integrations/",
    "scheduler/",
    "ingestion/",
    "scripts/",
    "storage/",
    "infra/",
    "db/",
    "docs/",
    "ARCHITECTURE.md",
    "README.md",
    "INSTALL.md",
    "SECURITY.md",
]

# Encrypted-only files — anything containing credentials or per-deployment state
SECRETS_INCLUDE = [
    ".env",
    "gcs-key.json",
]

# Docker volumes whose presence we want recorded (NOT contents — sync_volumes.py handles that)
TRACKED_VOLUMES = ["pgdata", "owui_data", "pipelines_data", "analysis_output"]

EXCLUDE_DIR_NAMES = {
    "__pycache__", ".pytest_cache", ".venv", "node_modules", ".next",
    ".git", "output", "cache", "vector_db", "uploads", ".turbo",
}
EXCLUDE_FILE_NAMES = {".DS_Store", "Thumbs.db", "nohup.out"}


def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = set(Path(tarinfo.name).parts)
    if parts & EXCLUDE_DIR_NAMES:
        return None
    name = Path(tarinfo.name).name
    if name in EXCLUDE_FILE_NAMES:
        return None
    if "ingestion" in parts and "data" in parts:
        return None
    # Defense-in-depth: never accidentally include secrets in plain tarball
    if name in {".env", "gcs-key.json"}:
        return None
    return tarinfo


def make_plain_tarball(project_root: Path, out_path: Path) -> None:
    logger.info(f"Creating plain config tarball: {out_path}")
    with tarfile.open(out_path, "w:gz") as tar:
        for item in PLAIN_INCLUDE:
            full = project_root / item
            if full.exists():
                tar.add(str(full), arcname=item, filter=_filter)
            else:
                logger.warning(f"  missing: {item}")


def collect_volume_manifest(project_root: Path) -> dict:
    """Best-effort docker volume inventory. Falls back to static list if docker unavailable."""
    manifest = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "volumes": {},
    }
    for vol in TRACKED_VOLUMES:
        entry: dict = {"name": vol, "exists": False}
        try:
            res = subprocess.run(
                ["docker", "volume", "inspect", f"parsnip_{vol}"],
                capture_output=True, text=True, timeout=10,
            )
            if res.returncode == 0:
                data = json.loads(res.stdout)[0]
                entry["exists"] = True
                entry["mountpoint"] = data.get("Mountpoint")
                entry["driver"] = data.get("Driver")
                entry["labels"] = data.get("Labels", {})
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
            pass
        manifest["volumes"][vol] = entry
    return manifest


def make_secrets_bundle(project_root: Path, recipient: str, out_path: Path) -> bool:
    """Create an age-encrypted tarball of secret files. Returns False if no secrets present."""
    if not shutil.which("age"):
        logger.error("  age binary not found in PATH — cannot encrypt secrets bundle.")
        logger.error("  Install age (https://age-encryption.org) or pass --no-secrets.")
        return False

    found = [project_root / f for f in SECRETS_INCLUDE if (project_root / f).exists()]
    if not found:
        logger.warning("  No secret files present — skipping encrypted bundle.")
        return False

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tf:
        plain_path = Path(tf.name)
    try:
        with tarfile.open(plain_path, "w:gz") as tar:
            for f in found:
                tar.add(str(f), arcname=f.relative_to(project_root).as_posix())

        result = subprocess.run(
            ["age", "-r", recipient, "-o", str(out_path), str(plain_path)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logger.error(f"  age encryption failed: {result.stderr}")
            return False
        logger.info(f"  Encrypted secrets bundle: {out_path} ({out_path.stat().st_size} bytes)")
        return True
    finally:
        # Shred plain tarball — it contains decrypted secrets
        try:
            with plain_path.open("r+b") as f:
                f.write(b"\0" * plain_path.stat().st_size)
            plain_path.unlink()
        except Exception:
            plain_path.unlink(missing_ok=True)


def upload_artifacts(gcs, artifacts: list[tuple[Path, str]], retain: int) -> None:
    """Upload to gs://<bucket>/backups/config/ and update latest pointers."""
    for local_path, gcs_subpath in artifacts:
        if not local_path.exists():
            continue
        gcs_path = f"backups/config/{gcs_subpath}"
        gcs.upload_file(str(local_path), gcs_path)
        logger.info(f"  uploaded → gs://{gcs.bucket_name}/{gcs_path}")

        # Latest pointer for restore_stack.sh
        latest_key = "latest"
        if local_path.suffix == ".age":
            latest_key = "latest_secrets"
        elif local_path.name.endswith(".json"):
            latest_key = "latest_volume_manifest"
        ext = "".join(local_path.suffixes)
        gcs.upload_file(str(local_path), f"backups/config/{latest_key}{ext}")

    # Retention sweep — keep most recent N timestamped tarballs
    all_objects = gcs.list_objects("backups/config/")
    timestamped = sorted([o for o in all_objects
                          if "/parsnip_config_" in o or "/parsnip_secrets_" in o])
    by_kind: dict[str, list[str]] = {}
    for obj in timestamped:
        kind = "config" if "parsnip_config_" in obj else "secrets"
        by_kind.setdefault(kind, []).append(obj)
    for kind, objs in by_kind.items():
        for old in objs[:-retain]:
            gcs.delete(old)
            logger.info(f"  pruned old {kind}: {old}")


def main():
    parser = argparse.ArgumentParser(description="Backup project config + secrets to GCS")
    parser.add_argument("--local", action="store_true", help="Local only, skip GCS")
    parser.add_argument("--no-secrets", action="store_true",
                        help="Skip the encrypted secrets bundle entirely")
    parser.add_argument("--gcs-bucket", default=os.environ.get("GCS_BUCKET", ""))
    parser.add_argument("--output-dir", default="/tmp/config-backups")
    parser.add_argument("--retain", type=int, default=30)
    args = parser.parse_args()

    # Detect project root
    project_root = Path("/app/project_root")
    if not project_root.exists():
        project_root = Path(__file__).parent.parent
    logger.info(f"Project root: {project_root}")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    # 1. Plain config tarball
    config_path = out_dir / f"parsnip_config_{timestamp}.tar.gz"
    make_plain_tarball(project_root, config_path)

    # 2. Volume manifest
    manifest_path = out_dir / f"volume_manifest_{timestamp}.json"
    manifest_path.write_text(json.dumps(collect_volume_manifest(project_root), indent=2))
    logger.info(f"Volume manifest: {manifest_path}")

    # 3. Encrypted secrets bundle (optional)
    secrets_path = out_dir / f"parsnip_secrets_{timestamp}.tar.gz.age"
    secrets_made = False
    if not args.no_secrets:
        recipient = os.environ.get("AGE_RECIPIENT", "")
        if not recipient:
            logger.warning(
                "AGE_RECIPIENT not set — skipping secrets bundle. "
                "Run scripts/setup_age_key.sh and add the public key to .env."
            )
        else:
            secrets_made = make_secrets_bundle(project_root, recipient, secrets_path)

    if args.local:
        logger.info("Local-only mode — skipping GCS upload.")
        return

    # GCS upload
    sys.path.insert(0, str(project_root / "storage"))
    from gcs import GCSClient  # noqa: E402

    gcs = GCSClient()
    if args.gcs_bucket:
        gcs.bucket_name = args.gcs_bucket
        gcs._bucket = None
    if not gcs.available:
        logger.error("GCS not available; secrets and config NOT uploaded.")
        sys.exit(1)

    artifacts = [
        (config_path, config_path.name),
        (manifest_path, manifest_path.name),
    ]
    if secrets_made:
        artifacts.append((secrets_path, secrets_path.name))

    upload_artifacts(gcs, artifacts, args.retain)
    logger.info("Config backup complete.")


if __name__ == "__main__":
    main()
