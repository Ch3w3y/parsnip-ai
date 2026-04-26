"""
Structured JSON logging for ingestion pipelines.

Provides:
- StructuredFormatter: JSON-formatted log output with timestamp, level, message,
  source, source_id, and correlation_id fields.
- get_correlation_id() / set_correlation_id(): Thread-safe and async-safe
  correlation ID management via contextvars.
- get_ingestion_logger(source): Returns a logger pre-configured with the
  structured formatter (when enabled) or the default human-readable format.

Enable structured logging via:
  - Environment variable: STRUCTURED_LOGGING=true
  - Command-line flag: --structured-logging

When disabled, logs retain their original human-readable format:
  "2025-01-01 12:00:00 INFO message text"

When enabled, logs are emitted as JSON:
  {"timestamp":"2025-01-01T12:00:00","level":"INFO","message":"message text",
   "source":"arxiv","source_id":null,"correlation_id":"42"}
"""

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# ── Correlation ID (async-safe via contextvars) ─────────────────────────────

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """Return the current correlation ID (typically job_id), or None."""
    return _correlation_id.get()


def set_correlation_id(cid: str | None) -> None:
    """Set the correlation ID for the current async context."""
    _correlation_id.set(cid)


# ── Structured JSON formatter ──────────────────────────────────────────────

# Human-readable format matching the original basicConfig format used by
# all ingestion scripts.
_HUMAN_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class StructuredFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Fields produced:
      timestamp     ISO-8601 UTC
      level         INFO / WARNING / ERROR / DEBUG
      message       The log message
      source        Ingestion source name (e.g. "arxiv", "joplin_notes")
      source_id     Optional sub-item ID (e.g. note_id for Joplin)
      correlation_id  Pipeline run ID (typically job_id from ingestion_jobs)

    Additional keys in the ``extra`` dict are merged into the JSON object.
    """

    def __init__(self, source: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.source = source

    def format(self, record: logging.LogRecord) -> str:
        # Build the base structured record
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "source": getattr(record, "source", self.source),
            "source_id": getattr(record, "source_id", None),
            "correlation_id": getattr(record, "correlation_id", None) or get_correlation_id(),
        }

        # Merge any extra fields the caller passed (skip internal logging attrs)
        _RESERVED = {
            "name", "msg", "args", "created", "relativeCreated",
            "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "filename", "module", "pathname", "process", "processName",
            "thread", "threadName", "levelname", "levelno", "message",
            "msecs", "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in entry:
                entry[key] = value

        # Append exception info if present
        if record.exc_info and record.exc_text is None:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            entry["exception"] = record.exc_text

        return json.dumps(entry, default=str, ensure_ascii=False)


# ── Structured logging toggle ──────────────────────────────────────────────

_is_structured: bool | None = None


def is_structured_logging() -> bool:
    """Check whether structured JSON logging is enabled.

    Resolution order:
      1. Cached value (set once per process)
      2. STRUCTURED_LOGGING env var ("true"/"1"/"yes" → True)
      3. Default: False (human-readable)
    """
    global _is_structured
    if _is_structured is None:
        val = os.environ.get("STRUCTURED_LOGGING", "").strip().lower()
        _is_structured = val in ("true", "1", "yes")
    return _is_structured


def enable_structured_logging(enabled: bool = True) -> None:
    """Explicitly enable or disable structured logging (overrides env var)."""
    global _is_structured
    _is_structured = enabled


# ── Logger factory ─────────────────────────────────────────────────────────

_ingestion_loggers: dict[str, logging.Logger] = {}


def get_ingestion_logger(source: str) -> logging.Logger:
    """Return a logger configured for the given ingestion source.

    When structured logging is enabled (env var or explicit call), the logger
    uses StructuredFormatter for JSON output. Otherwise it falls back to the
    standard human-readable format.

    The ``source`` name is baked into every log record so downstream consumers
    can filter by source without parsing message text.
    """
    if source in _ingestion_loggers:
        return _ingestion_loggers[source]

    logger = logging.getLogger(f"ingestion.{source}")
    logger.setLevel(logging.DEBUG)  # handlers control the effective level

    # Avoid duplicate handlers on repeated calls
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        if is_structured_logging():
            handler.setFormatter(StructuredFormatter(source=source))
        else:
            handler.setFormatter(logging.Formatter(_HUMAN_FORMAT))
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)

    # Prevent propagation to root logger (avoid double-printing)
    logger.propagate = False

    _ingestion_loggers[source] = logger
    return logger


def configure_basic_logging(source: str) -> None:
    """Configure the root logger AND return structured-ready setup.

    Call this once at script startup in place of ``logging.basicConfig()``.
    It sets up the root handler with the appropriate formatter based on
    the structured logging toggle, and ensures the ingestion logger for
    ``source`` is initialized.

    This function replaces the common pattern::

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        logger = logging.getLogger(__name__)
    """
    if is_structured_logging():
        # Configure root logger with JSON formatter
        root = logging.getLogger()
        if not root.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(StructuredFormatter(source=source))
            handler.setLevel(logging.INFO)
            root.addHandler(handler)
            root.setLevel(logging.INFO)
    else:
        # Standard human-readable format (matches original basicConfig)
        logging.basicConfig(
            level=logging.INFO,
            format=_HUMAN_FORMAT,
        )