"""
Lightweight OpenTelemetry tracing for ingestion pipelines.

Provides:
- ``setup_tracing()``: Configure a TracerProvider with ConsoleSpanExporter
  (or env-configured OTLP exporter).
- ``get_tracer()``: Return a real Tracer when opentelemetry-api is installed,
  or a _NoopTracer that silently discards all span operations.
- Decorators: ``trace_embed_batch``, ``trace_upsert_chunks``,
  ``trace_bulk_upsert_chunks``, ``trace_db_write``, ``trace_job``, ``trace_dlq``
  that wrap key ingestion operations with spans.

All tracing is NOOP when ``opentelemetry-api`` is not installed — no hard
dependency, no import errors, no performance overhead.
"""

import functools
import inspect
import logging
import os
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

# ── OpenTelemetry availability ───────────────────────────────────────────────

_OTEL_AVAILABLE = False
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    _OTEL_AVAILABLE = True
except ImportError:
    pass


# ── Noop implementations (used when opentelemetry-api is not installed) ─────

class _NoopSpan:
    """A span that accepts set_attribute / add_event / set_status but discards them."""

    def set_attribute(self, key: str, value: Any) -> "_NoopSpan":
        return self

    def add_event(self, name: str, attributes: dict | None = None) -> "_NoopSpan":
        return self

    def set_status(self, status: Any, description: str | None = None) -> None:
        pass

    def record_exception(self, exception: Exception, attributes: dict | None = None) -> None:
        pass

    def end(self, end_time: int | None = None) -> None:
        pass

    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _NoopTracer:
    """A tracer that yields _NoopSpan instances — zero overhead when OTEL is absent."""

    def start_as_current_span(
        self, name: str, attributes: dict | None = None, **kwargs: Any
    ) -> _NoopSpan:
        return _NoopSpan()

    def start_span(
        self, name: str, attributes: dict | None = None, **kwargs: Any
    ) -> _NoopSpan:
        return _NoopSpan()


# ── Setup and tracer factory ────────────────────────────────────────────────

_tracing_initialized = False


def setup_tracing(service_name: str = "parsnip-ingestion") -> None:
    """Configure OpenTelemetry tracing for the process.

    Uses a ConsoleSpanExporter by default. Set ``OTEL_EXPORTER_OTLP_ENDPOINT``
    to send spans to an OTLP-compatible collector instead.

    Safe to call multiple times — only initializes once.
    Noop when opentelemetry-api is not installed.
    """
    global _tracing_initialized
    if _tracing_initialized or not _OTEL_AVAILABLE:
        return

    try:
        otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
                exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            except ImportError:
                logger.warning(
                    "OTEL_EXPORTER_OTLP_ENDPOINT set but opentelemetry-exporter-otlp "
                    "not installed; falling back to ConsoleSpanExporter"
                )
                exporter = ConsoleSpanExporter()
        else:
            exporter = ConsoleSpanExporter()

        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracing_initialized = True
        logger.info(f"OpenTelemetry tracing initialized (service={service_name})")
    except Exception as e:
        logger.warning(f"Failed to initialize OpenTelemetry tracing: {e}")


def get_tracer(name: str = "parsnip.ingestion") -> Any:
    """Return a Tracer or _NoopTracer depending on OTEL availability.

    Callers should use ``tracer.start_as_current_span()`` — both the real
    Tracer and _NoopTracer support this interface.
    """
    if _OTEL_AVAILABLE:
        return trace.get_tracer(name)
    return _NoopTracer()


# ── Convenience: module-level tracer ────────────────────────────────────────

_tracer = get_tracer("parsnip.ingestion")


def set_span_error(span: Any, exc: Exception) -> None:
    """Record an exception on a span and set its status to ERROR.

    Safe to call with _NoopSpan (no-op) or a real OTel span.
    """
    if isinstance(span, _NoopSpan):
        return
    span.record_exception(exc)
    if _OTEL_AVAILABLE:
        span.set_status(trace.StatusCode.ERROR, str(exc))


# ── Decorator helpers ───────────────────────────────────────────────────────

F = TypeVar("F", bound=Callable)


def _make_span_decorator(span_name: str, default_attrs: dict | None = None):
    """Create a decorator that wraps a function in a tracing span.

    The decorator:
    - Starts a span with the given name before calling the function.
    - Sets ``span.kind`` attributes from *default_attrs*.
    - On success, records ``result`` attribute.
    - On exception, records the exception and sets ERROR status, then re-raises.
    - Works with both sync and async functions.
    """
    attrs = default_attrs or {}

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with _tracer.start_as_current_span(span_name) as span:
                    if isinstance(span, _NoopSpan):
                        return await func(*args, **kwargs)
                    for k, v in attrs.items():
                        span.set_attribute(k, v)
                    try:
                        result = await func(*args, **kwargs)
                        return result
                    except Exception as exc:
                        span.record_exception(exc)
                        if _OTEL_AVAILABLE:
                            span.set_status(trace.StatusCode.ERROR, str(exc))
                        raise
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with _tracer.start_as_current_span(span_name) as span:
                    if isinstance(span, _NoopSpan):
                        return func(*args, **kwargs)
                    for k, v in attrs.items():
                        span.set_attribute(k, v)
                    try:
                        result = func(*args, **kwargs)
                        return result
                    except Exception as exc:
                        span.record_exception(exc)
                        if _OTEL_AVAILABLE:
                            span.set_status(trace.StatusCode.ERROR, str(exc))
                        raise

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper  # type: ignore[return-value]

    return decorator


# ── Public decorator API ────────────────────────────────────────────────────

trace_embed_batch = _make_span_decorator("ingestion.embed_batch", {"ingestion.operation": "embed"})

trace_upsert_chunks = _make_span_decorator("ingestion.upsert_chunks", {"ingestion.operation": "db_write"})

trace_bulk_upsert_chunks = _make_span_decorator("ingestion.bulk_upsert_chunks", {"ingestion.operation": "db_write"})

trace_db_write = _make_span_decorator("ingestion.db_write", {"ingestion.operation": "db_write"})

trace_job = _make_span_decorator("ingestion.job", {"ingestion.operation": "job_tracking"})

trace_dlq = _make_span_decorator("ingestion.dlq", {"ingestion.operation": "dead_letter"})