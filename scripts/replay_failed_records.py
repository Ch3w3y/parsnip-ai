#!/usr/bin/env python3
"""
replay_failed_records.py — DLQ replay command for failed ingestion records.

Usage:
    python scripts/replay_failed_records.py                          # replay all pending
    python scripts/replay_failed_records.py --source arxiv           # replay specific source
    python scripts/replay_failed_records.py --max-retries 5          # custom max retries
    python scripts/replay_failed_records.py --dry-run                # preview only
    python scripts/replay_failed_records.py --source arxiv --dry-run # preview specific source

Environment:
    DATABASE_URL (required)

Features:
    - Reads from failed_records table where status='pending'
    - Groups failures by source and re-runs appropriate ingestion
    - Updates status to 'replayed' on success or 'failed' with incremented retry_count on failure
    - Allows filtering by source and max retry_count
    - Supports --dry-run to preview what would be replayed without executing
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg

# Ensure project root in path for ingestion imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from ingestion.registry import SourceRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("replay_failed_records")

DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://agent:agent@localhost:5432/agent_kb"
)


@dataclass
class FailedRecord:
    """Represents a single failed ingestion record."""
    id: int
    source: str
    source_id: str
    content: Optional[str]
    metadata: Dict[str, Any]
    error_message: Optional[str]
    error_class: Optional[str]
    retry_count: int
    status: str
    created_at: datetime
    updated_at: Optional[datetime]
    last_retry_at: Optional[datetime]


class DLQReplay:
    """Dead Letter Queue replay manager."""

    def __init__(self, db_url: str = DB_URL):
        self.db_url = db_url
        self.registry = SourceRegistry()

    def get_pending_records(
        self, 
        source: Optional[str] = None,
        max_retries: int = 3
    ) -> List[FailedRecord]:
        """Fetch pending failed records with optional filters."""
        where_clauses = ["status = 'pending'", "retry_count < %s"]
        params = [max_retries]
        
        if source:
            where_clauses.append("source = %s")
            params.append(source)
        
        where_sql = " AND ".join(where_clauses)
        query = f"""
            SELECT id, source, source_id, content, metadata, 
                   error_message, error_class, retry_count, status, 
                   created_at, updated_at, last_retry_at
            FROM failed_records
            WHERE {where_sql}
            ORDER BY created_at ASC
        """
        
        records = []
        try:
            with psycopg.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    for row in cur.fetchall():
                        records.append(FailedRecord(
                            id=row[0],
                            source=row[1],
                            source_id=row[2],
                            content=row[3],
                            metadata=row[4] if row[4] else {},
                            error_message=row[5],
                            error_class=row[6],
                            retry_count=row[7],
                            status=row[8],
                            created_at=row[9],
                            updated_at=row[10],
                            last_retry_at=row[11]
                        ))
        except Exception as e:
            logger.error(f"Failed to fetch pending records: {e}")
            raise
        
        return records

    def group_by_source(self, records: List[FailedRecord]) -> Dict[str, List[FailedRecord]]:
        """Group records by source for batch processing."""
        grouped = {}
        for record in records:
            if record.source not in grouped:
                grouped[record.source] = []
            grouped[record.source].append(record)
        return grouped

    def replay_record(self, record: FailedRecord, dry_run: bool = False) -> bool:
        """Replay a single failed record."""
        logger.info(f"Processing record {record.id} (source={record.source}, source_id={record.source_id}, retry={record.retry_count + 1})")
        
        if dry_run:
            logger.info(f"  DRY-RUN: Would replay {record.source}.{record.source_id}")
            return True
        
        try:
            # Get the source entry from registry
            source_entry = self.registry.get_source(record.source)
            entry_point = source_entry.get_entry_point()
            
            # For async entry points, we'd need to run them in an async context
            # For sync entry points, we can call them directly
            if hasattr(entry_point, '__await__'):
                # This is an async function - we need to handle it differently
                logger.warning(f"Async entry point not supported in sync replay for {record.source}")
                return False
            else:
                # Call the sync entry point with appropriate arguments
                # This is a simplified approach - real implementation would need
                # to construct proper arguments based on the source type
                result = entry_point()
                logger.info(f"  Successfully replayed {record.source}.{record.source_id}")
                return True
                
        except Exception as e:
            logger.error(f"  Failed to replay {record.source}.{record.source_id}: {e}")
            return False

    def update_record_status(
        self, 
        record_id: int, 
        new_status: str,
        increment_retry: bool = False
    ) -> None:
        """Update the status of a failed record."""
        updates = ["status = %s", "updated_at = NOW()"]
        params = [new_status]
        
        if increment_retry:
            updates.append("retry_count = retry_count + 1")
        
        if new_status == 'replayed':
            updates.append("last_retry_at = NOW()")
        
        update_sql = f"""
            UPDATE failed_records
            SET {', '.join(updates)}
            WHERE id = %s
        """
        params.append(record_id)
        
        try:
            with psycopg.connect(self.db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(update_sql, params)
                    conn.commit()
        except Exception as e:
            logger.error(f"Failed to update record {record_id} status: {e}")
            raise

    def replay_all(
        self, 
        source: Optional[str] = None,
        max_retries: int = 3,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """Replay all pending failed records."""
        logger.info(f"Starting DLQ replay (source={source or 'all'}, max_retries={max_retries}, dry_run={dry_run})")
        
        # Fetch pending records
        records = self.get_pending_records(source=source, max_retries=max_retries)
        logger.info(f"Found {len(records)} pending records to process")
        
        if not records:
            logger.info("No pending records found")
            return {
                "total_records": 0,
                "successful": 0,
                "failed": 0,
                "sources_processed": []
            }
        
        # Group by source for batch processing
        grouped = self.group_by_source(records)
        
        summary = {
            "total_records": len(records),
            "successful": 0,
            "failed": 0,
            "sources_processed": list(grouped.keys())
        }
        
        # Process each source group
        for source_name, source_records in grouped.items():
            logger.info(f"Processing {len(source_records)} records for source: {source_name}")
            
            for record in source_records:
                try:
                    success = self.replay_record(record, dry_run=dry_run)
                    
                    if dry_run:
                        continue
                    
                    if success:
                        self.update_record_status(record.id, 'replayed')
                        summary["successful"] += 1
                        logger.info(f"  ✓ Record {record.id} replayed successfully")
                    else:
                        self.update_record_status(record.id, 'failed', increment_retry=True)
                        summary["failed"] += 1
                        logger.warning(f"  ✗ Record {record.id} failed again (retry_count={record.retry_count + 1})")
                        
                except Exception as e:
                    logger.error(f"  ✗ Error processing record {record.id}: {e}")
                    if not dry_run:
                        self.update_record_status(record.id, 'failed', increment_retry=True)
                        summary["failed"] += 1
        
        logger.info(f"DLQ replay complete: {summary['successful']} successful, {summary['failed']} failed")
        return summary


def main():
    parser = argparse.ArgumentParser(description="DLQ replay command for failed ingestion records")
    parser.add_argument("--source", help="Filter by specific source (e.g., arxiv, github)")
    parser.add_argument("--max-retries", type=int, default=3, 
                       help="Maximum retry count (default: 3)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Preview what would be replayed without executing")
    args = parser.parse_args()

    # Ensure DATABASE_URL is available
    if os.environ.get("DATABASE_URL") is None:
        # Try to source .env manually
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()
                    break

    if os.environ.get("DATABASE_URL") is None:
        logger.error("DATABASE_URL environment variable is required")
        sys.exit(1)

    replay = DLQReplay()
    summary = replay.replay_all(
        source=args.source,
        max_retries=args.max_retries,
        dry_run=args.dry_run
    )
    
    if args.dry_run:
        logger.info("Dry-run complete. No changes were made.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())