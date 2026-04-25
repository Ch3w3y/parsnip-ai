#!/usr/bin/env python3
"""
Sync Docker named volumes to GCS for disaster recovery.

Targets (mounted RO into the scheduler container — see docker-compose.yml):
  /app/volumes/analysis_output    -> gs://<bucket>/volumes/analysis_output/
  /app/volumes/owui_data          -> gs://<bucket>/volumes/owui_data/
  /app/volumes/pipelines_data     -> gs://<bucket>/volumes/pipelines_data/

Strategy: file-by-file upload, skip if the GCS blob exists with matching md5.
Additive-only: we never delete from GCS, so stack rollback never destroys
upstream backups. Stale objects are pruned by a separate retention pass.

Usage:
  python sync_volumes.py                        # all configured volumes
  python sync_volumes.py --volume analysis_output
  python sync_volumes.py --dry-run              # list what would change
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from throttle import BackupThrottle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sync_volumes")

DEFAULT_VOLUMES = {
    "analysis_output": "/app/volumes/analysis_output",
    "owui_data": "/app/volumes/owui_data",
    "pipelines_data": "/app/volumes/pipelines_data",
}

# Skip noisy or transient files that bloat backups without recovery value.
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".tmp", ".swp", ".lock"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", ".cache", "tmp"}


@dataclass
class SyncStats:
    uploaded: int = 0
    skipped_unchanged: int = 0
    skipped_filter: int = 0
    bytes_uploaded: int = 0
    errors: int = 0


def file_md5_b64(path: Path, chunk: int = 1 << 20) -> str:
    """Return base64-encoded MD5, matching the format GCS reports in blob.md5_hash."""
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return base64.b64encode(h.digest()).decode("ascii")


def should_skip(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return True
    if path.suffix in SKIP_SUFFIXES:
        return True
    parts = set(path.parts)
    return bool(parts & SKIP_DIRS)


def sync_volume(gcs, throttle: BackupThrottle, name: str, local_root: Path, dry_run: bool) -> SyncStats:
    stats = SyncStats()
    if not local_root.exists():
        logger.warning(f"  Volume {name} not mounted at {local_root}, skipping.")
        return stats

    gcs_prefix = f"volumes/{name}/"
    # Build a lookup of existing GCS blobs once (keyed by relative path)
    logger.info(f"  Listing existing GCS objects under {gcs_prefix}...")
    existing: dict[str, str] = {}
    if not dry_run:
        for blob_name in gcs.list_objects(gcs_prefix):
            rel = blob_name[len(gcs_prefix):]
            # md5_hash isn't returned by list_objects in this wrapper — we'll
            # fall back to per-blob exists() checks for unchanged-detection
            existing[rel] = ""

    for path in local_root.rglob("*"):
        if not path.is_file():
            continue
        if should_skip(path):
            stats.skipped_filter += 1
            continue

        rel = path.relative_to(local_root).as_posix()
        gcs_path = f"{gcs_prefix}{rel}"

        try:
            local_md5 = file_md5_b64(path)
            # Cheap unchanged check: compare md5 against cached metadata blob
            if not dry_run and gcs.available:
                # google-cloud-storage exposes blob.md5_hash on reload
                blob = gcs._bucket.blob(gcs_path)
                blob.reload(timeout=10) if blob.exists() else None
                if blob.exists() and getattr(blob, "md5_hash", None) == local_md5:
                    stats.skipped_unchanged += 1
                    continue

            size = path.stat().st_size
            if dry_run:
                logger.info(f"  [dry-run] would upload {gcs_path} ({size:,} bytes)")
            else:
                if throttle:
                    throttle.upload_file(gcs, str(path), gcs_path)
                else:
                    gcs.upload_file(str(path), gcs_path)
                throttle.sleep_between_batches()
                logger.info(f"  uploaded {gcs_path} ({size:,} bytes)")
            stats.uploaded += 1
            stats.bytes_uploaded += size
        except Exception as e:
            stats.errors += 1
            logger.error(f"  ERROR uploading {gcs_path}: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Sync Docker volumes to GCS")
    parser.add_argument("--volume", choices=list(DEFAULT_VOLUMES.keys()),
                        help="Sync only this volume (default: all)")
    parser.add_argument("--gcs-bucket", default=os.environ.get("GCS_BUCKET", ""),
                        help="Override GCS bucket name")
    parser.add_argument("--dry-run", action="store_true",
                        help="List actions without uploading")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent / "storage"))
    from gcs import GCSClient  # noqa: E402

    throttle = BackupThrottle.from_env()
    throttle.nice()
    throttle.log_config()

    gcs = GCSClient()
    if args.gcs_bucket:
        gcs.bucket_name = args.gcs_bucket
        gcs._bucket = None
    if not args.dry_run and not gcs.available:
        logger.error("GCS not available (bucket missing or creds invalid). Aborting.")
        sys.exit(1)

    targets = {args.volume: DEFAULT_VOLUMES[args.volume]} if args.volume else DEFAULT_VOLUMES
    overall = {}
    started = datetime.now(timezone.utc)

    for name, mount in targets.items():
        logger.info(f"Syncing volume: {name} ({mount})")
        stats = sync_volume(gcs, throttle, name, Path(mount), args.dry_run)
        overall[name] = stats.__dict__
        logger.info(
            f"  Done: {stats.uploaded} uploaded, "
            f"{stats.skipped_unchanged} unchanged, "
            f"{stats.skipped_filter} filtered, "
            f"{stats.errors} errors, "
            f"{stats.bytes_uploaded / 1_048_576:.1f} MiB"
        )

    # Emit a summary manifest for the admin UI to read
    if not args.dry_run and gcs.available:
        summary = {
            "synced_at": started.isoformat(),
            "duration_s": (datetime.now(timezone.utc) - started).total_seconds(),
            "volumes": overall,
        }
        gcs.upload_bytes(
            json.dumps(summary, indent=2).encode("utf-8"),
            "volumes/_last_sync.json",
            content_type="application/json",
        )

    failed = sum(s["errors"] for s in overall.values())
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
