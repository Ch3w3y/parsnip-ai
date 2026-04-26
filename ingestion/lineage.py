"""
OpenLineage-lite: lightweight data lineage tracking for the ingestion pipeline.

Records lineage events in the `lineage_events` table, capturing source,
input/output types, record counts, and schema version for each successful
ingestion run.  Zero hard dependencies beyond psycopg + Python stdlib.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "v1.0"


@dataclass
class LineageEvent:
    """A single lineage event representing a data flow within the pipeline."""

    run_id: str
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    input_type: str | None = None
    output_type: str | None = None
    record_count: int | None = None
    schema_version: str = SCHEMA_VERSION
    job_id: int | None = None
    metadata: dict[str, Any] | None = None


async def record_lineage(conn, event: LineageEvent) -> None:
    """Insert a lineage event into the database.

    Uses parameterized SQL — same pattern as the rest of the ingestion layer.
    Failures are logged but never raise; lineage is metadata, not critical path.
    """
    try:
        await conn.execute(
            """
            INSERT INTO lineage_events
                (run_id, source, job_id, timestamp, input_type, output_type,
                 record_count, schema_version, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.run_id,
                event.source,
                event.job_id,
                event.timestamp,
                event.input_type,
                event.output_type,
                event.record_count,
                event.schema_version,
                psycopg.types.json.Jsonb(event.metadata) if event.metadata else None,
            ),
        )
        logger.debug(
            f"Lineage recorded: source={event.source} run_id={event.run_id} "
            f"input={event.input_type} output={event.output_type} "
            f"records={event.record_count}"
        )
    except Exception as e:
        logger.warning(f"Failed to record lineage event: {e}")


async def emit_lineage(
    conn,
    source: str,
    input_type: str,
    output_type: str,
    record_count: int | None = None,
    *,
    run_id: str | None = None,
    job_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Convenience wrapper: build and record a LineageEvent.

    Parameters
    ----------
    conn : async psycopg connection
    source : str
        Ingestion source name (e.g. "arxiv", "forex", "joplin_notes").
    input_type : str
        Data origin (e.g. "arxiv_api", "frankfurter_api", "joplin_notes").
    output_type : str
        Data destination (e.g. "knowledge_chunks", "forex_rates", "failed_records").
    record_count : int | None
        Number of records produced in this run.
    run_id : str | None
        External run identifier; defaults to str(job_id) or "unknown".
    job_id : int | None
        Associated ingestion_jobs.id (nullable).
    metadata : dict | None
        Optional extra fields stored as JSONB.
    """
    event = LineageEvent(
        run_id=run_id or (str(job_id) if job_id is not None else "unknown"),
        source=source,
        input_type=input_type,
        output_type=output_type,
        record_count=record_count,
        job_id=job_id,
        metadata=metadata,
    )
    await record_lineage(conn, event)


async def emit_job_lineage(conn, job_id: int) -> None:
    """Fetch an ingestion_jobs row and emit lineage from it.

    Looks up the job by id, then maps the source to input/output types
    using the built-in registry and emits a lineage event.
    """
    try:
        row = await (
            await conn.execute(
                "SELECT source, processed FROM ingestion_jobs WHERE id = %s",
                (job_id,),
            )
        ).fetchone()
    except Exception as e:
        logger.warning(f"Failed to fetch job {job_id} for lineage: {e}")
        return

    if row is None:
        logger.warning(f"No ingestion_jobs row found for id={job_id}")
        return

    source, processed = row
    mapping = SOURCE_TYPE_MAP.get(source)
    if mapping is None:
        logger.debug(f"No lineage type mapping for source={source}, using defaults")
        input_type = f"{source}_api"
        output_type = "knowledge_chunks"
    else:
        input_type, output_type = mapping

    await emit_lineage(
        conn,
        source=source,
        input_type=input_type,
        output_type=output_type,
        record_count=processed,
        run_id=str(job_id),
        job_id=job_id,
    )


async def emit_dlq_lineage(
    conn,
    source: str,
    job_id: int | None = None,
) -> None:
    """Emit a lineage event for a DLQ (dead-letter queue) write.

    These track that some records failed and were diverted to failed_records
    instead of reaching their normal output destination.
    """
    await emit_lineage(
        conn,
        source=source,
        input_type="ingestion_job",
        output_type="failed_records",
        run_id=str(job_id) if job_id is not None else "unknown",
        job_id=job_id,
    )


# ── Source → (input_type, output_type) mapping ──────────────────────────────────
# Maps each ingestion source to its canonical input/output type strings.
# Sources not in this map will default to ("<source>_api", "knowledge_chunks").

SOURCE_TYPE_MAP: dict[str, tuple[str, str]] = {
    "arxiv":            ("arxiv_api",          "knowledge_chunks"),
    "biorxiv":          ("biorxiv_api",        "knowledge_chunks"),
    "medrxiv":          ("medrxiv_api",        "knowledge_chunks"),
    "forex":            ("frankfurter_api",    "forex_rates"),
    "joplin_notes":     ("joplin_notes",       "knowledge_chunks"),
    "news":             ("news_api",           "knowledge_chunks"),
    "world_bank":       ("worldbank_api",      "world_bank_data"),
    "worldbank":        ("worldbank_api",      "world_bank_data"),
    "wikipedia":        ("wikipedia_dump",      "knowledge_chunks"),
    "wikipedia_update": ("mediwiki_api",       "knowledge_chunks"),
    "github":           ("github_api",         "knowledge_chunks"),
    "hackernews":       ("hn_api",             "knowledge_chunks"),
    "pubmed":           ("pubmed_api",         "knowledge_chunks"),
    "rss":              ("rss_feeds",          "knowledge_chunks"),
    "ssrn":             ("openalex_api",       "knowledge_chunks"),
}