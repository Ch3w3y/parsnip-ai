#!/usr/bin/env python3
"""
Backs up project configuration and code to GCS as a tarball.
Includes .env, docker-compose.yml, pipelines/, agent/config.py, integrations/, etc.
Excludes large data volumes and temporary files.
"""

import argparse
import logging
import os
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Essential project files and directories to backup
INCLUDE_PATHS = [
    ".env",
    "docker-compose.yml",
    "pyproject.toml",
    "pi-ctl.sh",
    "run_ingest.sh",
    "agent/config.py",
    "agent/Dockerfile",
    "agent/main.py",
    "agent/graph.py",
    "agent/tools/",
    "pipelines/",
    "integrations/",
    "scheduler/",
    "ingestion/",
    "scripts/",
    "docs/",
    "ARCHITECTURE.md",
    "README.md",
]

# External mounted volumes (absolute path in container -> path in archive)
EXTERNAL_VOLUMES = {
    "/app/owui_data": "volumes/owui_data",
    "/app/pipelines_data": "volumes/pipelines_data",
}

# Exclude large or temporary directories within the included paths
EXCLUDE_NAMES = [
    "__pycache__",
    ".pytest_cache",
    ".venv",
    ".git",
    "node_modules",
    "output",
    "cache",
    "vector_db",
    "uploads",
]

def create_archive(project_root: Path, output_path: Path) -> bool:
    """Create a compressed tarball of the project configuration."""
    logger.info(f"Creating configuration backup: {output_path}")
    
    def _exclude_filter(tarinfo):
        path_parts = Path(tarinfo.name).parts
        for name in EXCLUDE_NAMES:
            if name in path_parts:
                return None
        if "ingestion" in path_parts and "data" in path_parts:
            return None
        return tarinfo

    try:
        with tarfile.open(output_path, "w:gz") as tar:
            for path_str in INCLUDE_PATHS:
                full_path = project_root / path_str
                if full_path.exists():
                    tar.add(str(full_path), arcname=path_str, filter=_exclude_filter)
                else:
                    logger.warning(f"Path not found, skipping: {path_str}")
            
            # Explicitly backup webui.db without crawling the giant owui_data cache
            webui_db_path = Path("/app/owui_data/webui.db")
            if webui_db_path.exists():
                logger.info("Found webui.db, adding to backup...")
                tar.add(str(webui_db_path), arcname="volumes/owui_data/webui.db")
            else:
                logger.warning("webui.db not found at /app/owui_data/webui.db")
                    
        return True
    except Exception as e:
        logger.error(f"Failed to create archive: {e}")
        return False

def upload_to_gcs(local_path: Path, bucket_name: str, retain: int = 30):
    """Upload the archive to GCS."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "storage"))
    from gcs import GCSClient

    gcs = GCSClient()
    gcs.bucket_name = bucket_name
    gcs._bucket = None

    if not gcs.available:
        logger.error("GCS not available. Cannot upload.")
        return False

    filename = local_path.name
    gcs_path = f"backups/config/{filename}"
    
    try:
        gcs.upload_file(str(local_path), gcs_path, content_type="application/gzip")
        logger.info(f"Uploaded to gs://{bucket_name}/{gcs_path}")
        
        # Rotate old config backups
        all_backups = gcs.list_objects("backups/config/")
        backups = sorted([obj for obj in all_backups if obj.endswith(".tar.gz")])
        if len(backups) > retain:
            for old_backup in backups[:-retain]:
                gcs.delete_file(old_backup)
                logger.info(f"Rotated old backup: {old_backup}")
        
        # Latest pointer
        gcs.upload_file(str(local_path), "backups/config/latest.tar.gz", content_type="application/gzip")
        return True
    except Exception as e:
        logger.error(f"GCS upload failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Backup project config and code")
    parser.add_argument("--local", action="store_true", help="Only backup locally")
    parser.add_argument("--gcs-bucket", default=os.environ.get("GCS_BUCKET", "agentic-data-storage"), help="GCS bucket name")
    parser.add_argument("--output-dir", default="/tmp/config-backups", help="Local output directory")
    args = parser.parse_args()

    # Detect project root - use mount if in container, else assume scripts/ is 1-level down
    project_root = Path("/app/project_root")
    if not project_root.exists():
        project_root = Path(__file__).parent.parent
        
    logger.info(f"Project root detected at: {project_root}")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    archive_name = f"parsnip_config_{timestamp}.tar.gz"
    archive_path = output_dir / archive_name

    if create_archive(project_root, archive_path):
        if not args.local:
            upload_to_gcs(archive_path, args.gcs_bucket)
        logger.info("Config backup complete.")
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
