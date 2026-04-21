"""
GCS client for pi-agent — signed URLs for analysis outputs,
private access for backups.

Environment variables:
  GCS_BUCKET:              Bucket name (default: agentic-data-storage)
  GCS_PROJECT_ID:         GCP project ID (default: agentic-storage)
  GOOGLE_APPLICATION_CREDENTIALS: Path to service account JSON key file

If GCS_BUCKET is empty or unset, all methods become no-ops and return
localhost fallback URLs, allowing the system to run without GCS.
"""

import hashlib
import logging
import os
from datetime import timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_GCS_AVAILABLE = False

try:
    from google.cloud import storage
    from google.oauth2 import service_account

    _GCS_AVAILABLE = True
except ImportError:
    pass


class GCSClient:
    """Thin wrapper around google-cloud-storage for pi-agent.

    Falls back gracefully when GCS is not configured: uploads become no-ops
    and signed_url() returns localhost URLs.
    """

    def __init__(self):
        self.bucket_name = os.environ.get("GCS_BUCKET", "")
        self.project_id = os.environ.get("GCS_PROJECT_ID", "agentic-storage")
        self._client = None
        self._bucket = None

    def _init(self):
        if not self.bucket_name:
            return False
        if self._client is not None:
            return True
        if not _GCS_AVAILABLE:
            logger.warning("google-cloud-storage not installed; GCS uploads disabled")
            return False
        try:
            creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
            if creds_path and os.path.exists(creds_path):
                creds = service_account.Credentials.from_service_account_file(creds_path)
                self._client = storage.Client(project=self.project_id, credentials=creds)
            else:
                self._client = storage.Client(project=self.project_id)
            self._bucket = self._client.bucket(self.bucket_name)
            logger.info(f"GCS client initialised: bucket={self.bucket_name}")
            return True
        except Exception as e:
            logger.error(f"GCS client init failed: {e}")
            self._client = None
            self._bucket = None
            return False

    @property
    def available(self) -> bool:
        if not self.bucket_name:
            return False
        return self._init()

    def upload_bytes(self, data: bytes, gcs_path: str, content_type: str = "application/octet-stream") -> str:
        if not self._init():
            return ""
        blob = self._bucket.blob(gcs_path)
        blob.upload_from_string(data, content_type=content_type)
        logger.debug(f"Uploaded {len(data)} bytes to gs://{self.bucket_name}/{gcs_path}")
        return f"gs://{self.bucket_name}/{gcs_path}"

    def upload_file(self, local_path: str, gcs_path: str, content_type: str | None = None) -> str:
        if not self._init():
            return ""
        blob = self._bucket.blob(gcs_path)
        blob.upload_from_filename(local_path, content_type=content_type or self._detect_content_type(local_path))
        logger.debug(f"Uploaded {local_path} to gs://{self.bucket_name}/{gcs_path}")
        return f"gs://{self.bucket_name}/{gcs_path}"

    def download_bytes(self, gcs_path: str) -> bytes:
        if not self._init():
            return b""
        blob = self._bucket.blob(gcs_path)
        return blob.download_as_bytes()

    def download_to_file(self, gcs_path: str, local_path: str) -> str:
        if not self._init():
            return ""
        blob = self._bucket.blob(gcs_path)
        blob.download_to_filename(local_path)
        return local_path

    def signed_url(self, gcs_path: str, expiry_hours: int = 168) -> str:
        if not self._init():
            return ""
        blob = self._bucket.blob(gcs_path)
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=expiry_hours),
            method="GET",
        )

    def delete(self, gcs_path: str) -> bool:
        if not self._init():
            return False
        blob = self._bucket.blob(gcs_path)
        blob.delete()
        return True

    def exists(self, gcs_path: str) -> bool:
        if not self._init():
            return False
        blob = self._bucket.blob(gcs_path)
        return blob.exists()

    def list_objects(self, prefix: str) -> list[str]:
        if not self._init():
            return []
        return [blob.name for blob in self._bucket.list_blobs(prefix=prefix)]

    def delete_prefix(self, prefix: str) -> int:
        if not self._init():
            return 0
        blobs = list(self._bucket.list_blobs(prefix=prefix))
        for blob in blobs:
            blob.delete()
        logger.info(f"Deleted {len(blobs)} objects with prefix {prefix}")
        return len(blobs)

    @staticmethod
    def sha256_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _detect_content_type(path: str) -> str:
        ext = Path(path).suffix.lower()
        ct_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".svg": "image/svg+xml",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
            ".html": "text/html",
            ".csv": "text/csv",
            ".json": "application/json",
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".py": "text/x-python",
            ".R": "text/x-r",
            ".r": "text/x-r",
            ".ipynb": "application/json",
            ".parquet": "application/octet-stream",
        }
        return ct_map.get(ext, "application/octet-stream")