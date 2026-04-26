"""
Alerting thresholds and monitoring for the ingestion pipeline.

Provides threshold-based checks for DLQ depth, error rate, stuck jobs,
and embedding circuit breaker state. Alerts are dispatched via logging
and an optional webhook (ALERT_WEBHOOK_URL env var).

Designed to be lightweight — the scheduler runs run_all_checks() every
5 minutes, and the CLI script runs it on demand.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

from ingestion.utils import _embed_cb

logger = logging.getLogger(__name__)

# ── Environment-configurable thresholds ─────────────────────────────────────

_DLQ_THRESHOLD = int(os.environ.get("ALERT_DLQ_THRESHOLD", "100"))
_ERROR_RATE_THRESHOLD = float(os.environ.get("ALERT_ERROR_RATE_THRESHOLD", "0.05"))
_STUCK_JOB_AGE_HOURS = float(os.environ.get("ALERT_STUCK_JOB_AGE_HOURS", "2"))
_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL")


# ── Alert types ──────────────────────────────────────────────────────────────


class AlertLevel(Enum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


@dataclass
class Alert:
    level: AlertLevel
    source: str
    metric: str
    threshold: Any
    actual: Any
    message: str


# ── Check functions ──────────────────────────────────────────────────────────


async def check_dlq_depth(conn, threshold: int | None = None) -> Alert | None:
    """Emit an alert if pending DLQ records exceed *threshold* (default from env)."""
    if threshold is None:
        threshold = _DLQ_THRESHOLD

    row = await (
        await conn.execute(
            "SELECT COUNT(*) FROM failed_records WHERE status = 'pending'"
        )
    ).fetchone()
    count = row[0] if row else 0

    if count > threshold:
        level = AlertLevel.CRITICAL if count > threshold * 2 else AlertLevel.WARN
        return Alert(
            level=level,
            source="dlq",
            metric="pending_records",
            threshold=threshold,
            actual=count,
            message=f"DLQ depth {count} exceeds threshold {threshold}",
        )
    return None


async def check_error_rate(
    conn, hours: int = 1, threshold: float | None = None
) -> Alert | None:
    """Emit an alert if failed/jobs ratio exceeds *threshold* in the last *hours*."""
    if threshold is None:
        threshold = _ERROR_RATE_THRESHOLD

    row = await (
        await conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                COUNT(*) AS total
            FROM ingestion_jobs
            WHERE started_at >= NOW() - INTERVAL '%s hours'
            """,
            (hours,),
        )
    ).fetchone()

    if row is None:
        return None

    failed, total = row[0], row[1]
    if total == 0:
        return None

    rate = failed / total
    if rate > threshold:
        level = AlertLevel.CRITICAL if rate > threshold * 2 else AlertLevel.WARN
        return Alert(
            level=level,
            source="error_rate",
            metric="failed_job_ratio",
            threshold=f"{threshold:.0%}",
            actual=f"{rate:.0%}",
            message=(
                f"Error rate {rate:.1%} ({failed}/{total} jobs in last {hours}h) "
                f"exceeds threshold {threshold:.0%}"
            ),
        )
    return None


async def check_stuck_jobs(
    conn, age_hours: float | None = None
) -> Alert | None:
    """Emit an alert if any running jobs are older than *age_hours*."""
    if age_hours is None:
        age_hours = _STUCK_JOB_AGE_HOURS

    row = await (
        await conn.execute(
            """
            SELECT COUNT(*)
            FROM ingestion_jobs
            WHERE status = 'running'
              AND started_at < NOW() - INTERVAL '%s hours'
            """,
            (age_hours,),
        )
    ).fetchone()

    count = row[0] if row else 0
    if count > 0:
        return Alert(
            level=AlertLevel.CRITICAL,
            source="stuck_jobs",
            metric="running_jobs_over_age",
            threshold=f"{age_hours}h",
            actual=count,
            message=f"{count} job(s) running longer than {age_hours}h",
        )
    return None


async def check_circuit_breaker() -> Alert | None:
    """Emit an alert if the embedding circuit breaker is OPEN."""
    is_open = await _embed_cb.is_open()
    if is_open:
        return Alert(
            level=AlertLevel.CRITICAL,
            source="circuit_breaker",
            metric="embed_cb_state",
            threshold="closed",
            actual="open",
            message="Embedding circuit breaker is OPEN — embed calls are fast-failing",
        )
    return None


# ── Alert dispatch ───────────────────────────────────────────────────────────


def send_alert(alert: Alert) -> None:
    """Log the alert at the appropriate level and POST to webhook if configured."""
    log_msg = (
        f"[{alert.level.value.upper()}] {alert.source}/{alert.metric}: "
        f"{alert.message} (threshold={alert.threshold}, actual={alert.actual})"
    )

    if alert.level == AlertLevel.CRITICAL:
        logger.critical(log_msg)
    elif alert.level == AlertLevel.WARN:
        logger.warning(log_msg)
    else:
        logger.info(log_msg)

    webhook_url = _WEBHOOK_URL
    if webhook_url:
        try:
            payload = json.dumps({
                "level": alert.level.value,
                "source": alert.source,
                "metric": alert.metric,
                "threshold": str(alert.threshold),
                "actual": str(alert.actual),
                "message": alert.message,
            }).encode("utf-8")
            req = Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                if resp.status >= 400:
                    logger.warning(
                        f"Webhook returned HTTP {resp.status} for alert {alert.source}/{alert.metric}"
                    )
        except (URLError, OSError) as exc:
            logger.warning(f"Webhook dispatch failed: {exc}")


# ── Run all checks ──────────────────────────────────────────────────────────


async def run_all_checks(conn) -> list[Alert]:
    """Run all health checks and return a list of triggered alerts."""
    alerts: list[Alert] = []

    check_results = await asyncio.gather(
        check_dlq_depth(conn),
        check_error_rate(conn),
        check_stuck_jobs(conn),
        return_exceptions=True,
    )
    for result in check_results:
        if isinstance(result, Exception):
            logger.warning(f"Health check raised exception: {result}")
            alerts.append(Alert(
                level=AlertLevel.WARN,
                source="alerting",
                metric="check_error",
                threshold="no_exception",
                actual=str(result),
                message=f"Health check failed: {result}",
            ))
        elif result is not None:
            alerts.append(result)

    try:
        cb_alert = await check_circuit_breaker()
        if cb_alert is not None:
            alerts.append(cb_alert)
    except Exception as exc:
        logger.warning(f"Circuit breaker check raised exception: {exc}")
        alerts.append(Alert(
            level=AlertLevel.WARN,
            source="alerting",
            metric="check_error",
            threshold="no_exception",
            actual=str(exc),
            message=f"Circuit breaker check failed: {exc}",
        ))

    for alert in alerts:
        send_alert(alert)

    if not alerts:
        logger.info("Pipeline health check: all clear — no alerts triggered")

    return alerts