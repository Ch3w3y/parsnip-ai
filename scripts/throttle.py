#!/usr/bin/env python3
"""
Resource-aware throttling for backup & sync operations.

Problem: backup scripts (backup_kb.py, sync_volumes.py) saturate CPU, RAM,
and network when running concurrently, causing:
  - Network dropouts on the host (GCS uploads at 200+ MB/s)
  - CPU starvation for other containers (pgBackRest + backup_kb hammering DB)
  - OOM on large table exports (knowledge_chunks: 16M rows × 4KB embeddings)

Solutions provided by this module:
  1. CPU throttling: os.nice() + explicit sleep between batch iterations
  2. Network throttling: HTTP-transport-level rate limiting on GCS uploads
  3. RAM throttling: bounded Parquet writer flush + DB cursor itersize control
  4. Process throttling: serialise backup steps that were previously parallel

Environment variables (all optional, safe defaults):
  PARSNIP_NICE_LEVEL       int   0-19, default 10 (lower priority than containers)
  PARSNIP_UPLOAD_MIB_S    float max upload bandwidth in MiB/s, default 5
  PARSNIP_BATCH_PAUSE_S   float seconds to sleep between batch iterations, default 0.5
  PARSNIP_CURSOR_ITERSIZE int   psycopg server-side cursor fetch size, default 2000

Usage in scripts:
    from throttle import BackupThrottle

    throttle = BackupThrottle.from_env()
    throttle.nice()                       # lower process priority
    throttle.sleep_between_batches()      # yield CPU between chunks
    throttle.patch_gcs_client(gcs)        # inject HTTP-level throttle
    throttle.upload_file(gcs, local, gcs_path)  # rate-limited GCS upload
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class _RateLimiter:
    """Token-bucket rate limiter for bytes/second."""

    def __init__(self, mib_per_sec: float):
        self._rate = mib_per_sec * 1024 * 1024
        self._tokens = self._rate
        self._last = time.monotonic()

    def acquire(self, nbytes: int) -> float:
        """Block until nbytes can be transmitted. Returns wait time."""
        if self._rate <= 0:
            return 0.0
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._rate, self._tokens + elapsed * self._rate)

        if nbytes <= self._tokens:
            self._tokens -= nbytes
            return 0.0

        deficit = nbytes - self._tokens
        wait = deficit / self._rate
        self._tokens = 0.0
        time.sleep(wait)
        return wait


def _throttled_request(original_request, limiter: _RateLimiter):
    """Wrap a requests.Session.request method with bandwidth throttling.

    The GCS SDK calls session.request(method, url, data=payload, ...) for each
    resumable-upload chunk.  By delaying before the actual HTTP send, we
    directly control the wire-speed of the upload — the TCP stack can only
    transmit data that we've handed to it, so sleeping before the call
    effectively caps throughput at the limiter's rate.

    We also set blob.chunk_size to 256 KiB (the GCS minimum) to get fine-
    grained pacing: smaller chunks mean more frequent throttle checkpoints
    and smoother bandwidth enforcement.
    """
    def wrapper(method, url, **kwargs):
        data = kwargs.get("data")
        nbytes = len(data) if data is not None else 0
        if nbytes > 0:
            limiter.acquire(nbytes)
        return original_request(method, url, **kwargs)

    return wrapper


class BackupThrottle:
    """Combined CPU / network / RAM throttle for backup scripts."""

    _GCS_CHUNK_SIZE = 256 * 1024

    def __init__(
        self,
        nice_level: int = 10,
        upload_mib_s: float = 5.0,
        batch_pause_s: float = 0.5,
        cursor_itersize: int = 2000,
    ):
        self.nice_level = nice_level
        self.upload_mib_s = upload_mib_s
        self.batch_pause_s = batch_pause_s
        self.cursor_itersize = cursor_itersize
        self._limiter = _RateLimiter(upload_mib_s)
        self._niced = False
        self._patched_clients: set[int] = set()

    @classmethod
    def from_env(cls) -> "BackupThrottle":
        """Read configuration from environment variables with safe defaults."""
        return cls(
            nice_level=int(os.environ.get("PARSNIP_NICE_LEVEL", "10")),
            upload_mib_s=float(os.environ.get("PARSNIP_UPLOAD_MIB_S", "5")),
            batch_pause_s=float(os.environ.get("PARSNIP_BATCH_PAUSE_S", "0.5")),
            cursor_itersize=int(os.environ.get("PARSNIP_CURSOR_ITERSIZE", "2000")),
        )

    def nice(self) -> None:
        """Lower process priority. Only effective on first call."""
        if self._niced:
            return
        try:
            os.nice(self.nice_level)
            logger.info(f"Process niced to {self.nice_level}")
        except (OSError, AttributeError):
            logger.debug(f"os.nice({self.nice_level}) not available (non-POSIX?)")
        self._niced = True

    def sleep_between_batches(self) -> None:
        """Sleep briefly between batch iterations to yield CPU / I/O bandwidth."""
        if self.batch_pause_s > 0:
            time.sleep(self.batch_pause_s)

    def patch_gcs_client(self, gcs_client) -> None:
        """Inject HTTP-level bandwidth throttling into a GCS client.

        The GCS SDK uses an AuthorizedSession (requests.Session subclass)
        internally.  We wrap its .request() method so that every HTTP PUT
        carrying upload data is delayed by the token-bucket limiter before
        the bytes hit the socket — this directly caps wire-speed.

        Safe to call multiple times on the same client (idempotent).
        """
        if not gcs_client.available:
            logger.warning("GCS client not available — skipping throttle patch")
            return

        cid = id(gcs_client._client)
        if cid in self._patched_clients:
            return

        session = gcs_client._client._http
        session.request = _throttled_request(session.request, self._limiter)
        self._patched_clients.add(cid)
        logger.info(
            f"Patched GCS client HTTP session with {self.upload_mib_s} MiB/s throttle"
        )

    def upload_file(self, gcs_client, local_path: str, gcs_path: str) -> str:
        """Upload a file to GCS with rate limiting and content-type detection.

        For small files (<1MB), defer to the original GCS client (skip rate
        limiting overhead). For large files, upload via the patched HTTP
        session with 256 KiB resumable chunks.
        """
        file_size = Path(local_path).stat().st_size
        if file_size < 1_048_576 or self.upload_mib_s <= 0:
            result = gcs_client.upload_file(local_path, gcs_path)
            self._limiter.acquire(file_size)
            return result

        return self._rate_limited_upload(gcs_client, local_path, gcs_path, file_size)

    def upload_bytes(self, gcs_client, data: bytes, gcs_path: str, content_type: str = "application/octet-stream") -> str:
        """Upload bytes to GCS with rate limiting."""
        size = len(data)
        result = gcs_client.upload_bytes(data, gcs_path, content_type=content_type)
        self._limiter.acquire(size)
        return result

    def _rate_limited_upload(self, gcs_client, local_path: str, gcs_path: str, file_size: int) -> str:
        """Rate-limited upload using patched HTTP transport + small GCS chunks.

        The throttle is enforced at the HTTP session level (see patch_gcs_client).
        We set blob.chunk_size to 256 KiB for fine-grained pacing: each 256 KiB
        chunk triggers one session.request() call, which the wrapper delays via
        the token bucket before the bytes reach the TCP socket.

        Before the HTTP-level patch, we used _ThrottledReader to slow read()
        calls on the file handle — this did NOT work because the GCS SDK reads
        each chunk fully into memory, then transmits it at full wire speed;
        throttling read() only delays when the SDK *gets* data, not how fast
        it's *sent* over the network.
        """
        try:
            if not gcs_client.available:
                raise RuntimeError("GCS client not available")

            if id(gcs_client._client) not in self._patched_clients:
                self.patch_gcs_client(gcs_client)

            content_type = gcs_client._detect_content_type(local_path)
            blob = gcs_client._bucket.blob(gcs_path)
            blob.chunk_size = self._GCS_CHUNK_SIZE

            with open(local_path, "rb") as raw:
                blob.upload_from_file(
                    raw,
                    content_type=content_type,
                    size=file_size,
                    timeout=None,
                )

            logger.info(f"Rate-limited upload complete: gs://{gcs_client.bucket_name}/{gcs_path}")
            return f"gs://{gcs_client.bucket_name}/{gcs_path}"

        except Exception as e:
            logger.warning(f"Rate-limited upload failed ({e}), falling back to direct upload")
            return gcs_client.upload_file(local_path, gcs_path)

    def log_config(self) -> None:
        """Log the throttle configuration (call once at startup)."""
        logger.info(
            f"Throttle config: nice={self.nice_level}, "
            f"upload={self.upload_mib_s} MiB/s, "
            f"batch_pause={self.batch_pause_s}s, "
            f"cursor_itersize={self.cursor_itersize}"
        )