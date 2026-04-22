#!/usr/bin/env python3
"""
add_performance_indexes.py — post-migration index additions for parsnip-agent.

Targets tables that grew significantly and whose current indexes
are missing common access patterns observed in agent tools, ingestion,
and scheduler queries.

All index creation uses PostgreSQL's built-in IF NOT EXISTS idiom
and CONCURRENTLY where possible so the agent and ingestion can keep running.

Usage:
    # From the project root (reads .env for DATABASE_URL):
    python scripts/add_performance_indexes.py

    # Or with an explicit DSN:
    DATABASE_URL=postgresql://agent:pass@localhost:5432/agent_kb \n        python scripts/add_performance_indexes.py

    # Run with --dry-run to preview SQL without executing:
    python scripts/add_performance_indexes.py --dry-run

    # Run with --explain to validate query plans after creation:
    python scripts/add_performance_indexes.py --explain
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Index definitions ────────────────────────────────────────────────────────
# Each entry: (index_name, table, definition, why)
# Definitions should be idempotent (PostgreSQL does NOT create if name exists).

INDEXES = [
    # 1. High impact for knowledge_chunks date filtering
    #    Tools: timeline, filtered_search (date/source combo), any KB pruning
    (
        "knowledge_chunks_source_created_idx",
        "knowledge_chunks",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS knowledge_chunks_source_created_idx "
        "ON knowledge_chunks (source, created_at DESC)",
        "Timeline, filtered_search, and cleanup jobs frequently filter by source + recency",
    ),

    # 2. Medium impact: date-only filter (source-agnostic timeline / pruning)
    (
        "knowledge_chunks_created_at_idx",
        "knowledge_chunks",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS knowledge_chunks_created_at_idx "
        "ON knowledge_chunks (created_at DESC)",
        "Source-agnostic recency filtering (e.g. old-chunk purge, global timeline)",
    ),

    # 3. Agent memory: composite partial for fast L1/top-N retrieval
    #    _load_l1_memory: deleted_at IS NULL AND importance >= 3 ORDER BY importance DESC, created_at DESC
    (
        "agent_memories_active_importance_created_idx",
        "agent_memories",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS agent_memories_active_importance_created_idx "
        "ON agent_memories (importance DESC, created_at DESC) "
        "WHERE deleted_at IS NULL",
        "Speeds up L1 session memory load (top-15 by importance + recency)",
    ),

    # 4. Agent memory: category + importance queries (recall_memory by category)
    (
        "agent_memories_active_category_importance_idx",
        "agent_memories",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS agent_memories_active_category_importance_idx "
        "ON agent_memories (category, importance DESC) "
        "WHERE deleted_at IS NULL",
        "Fast category-scoped memory recall with importance ranking",
    ),

    # 5. Ingestion jobs: fast scheduler gates
    #    scheduler.py checks wikipedia seed/running status frequently
    (
        "ingestion_jobs_source_status_started_idx",
        "ingestion_jobs",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ingestion_jobs_source_status_started_idx "
        "ON ingestion_jobs (source, status, started_at DESC)",
        "Scheduler gate queries: 'wikipedia running/done' lookups",
    ),

    # 6. Knowledge chunks: partial for source IS NULL embeddings check (if any exist)
    #    Helps pgvector queries that need to skip NULL embeddings
    (
        "knowledge_chunks_has_embedding_idx",
        "knowledge_chunks",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS knowledge_chunks_has_embedding_idx "
        "ON knowledge_chunks (source) "
        "WHERE embedding IS NOT NULL",
        "Partial index: pgvector search only operates on rows with embeddings anyway",
    ),
]

# ── helpers ──────────────────────────────────────────────────────────────────


def _list_existing_indexes(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT indexname FROM pg_indexes
        WHERE tablename = %s AND schemaname = 'public'
        """,
        (table,),
    )
    return {row[0] for row in cur.fetchall()}


def _run_migration(dsn: str, dry_run: bool = False) -> dict:
    created = []
    skipped = []
    failed = []

    # CONCURRENTLY cannot run in a normal transaction block.
    # We use autocommit so each statement is its own transaction.
    logger.info("Connecting to database...")
    conn = psycopg.connect(dsn, autocommit=True)
    cur = conn.cursor()
    logger.info("Checking existing indexes against desired set...")

    existing = set()
    for _, table, _, _ in INDEXES:
        existing |= _list_existing_indexes(cur, table)

    for idx_name, table, sql, why in INDEXES:
        if idx_name in existing:
            logger.info(f"SKIP (already exists): {idx_name}  ({why})")
            skipped.append(idx_name)
            continue

        if dry_run:
            logger.info(f"DRY-RUN: {sql}  -- {why}")
            continue

        logger.info(f"Creating {idx_name} ...")
        try:
            cur.execute(sql)
            logger.info(f"  Created {idx_name} ✅")
            created.append(idx_name)
        except psycopg.errors.DuplicateTable:
            # IF NOT EXISTS usually prevents this, but belt-and-suspenders
            logger.info(f"SKIP (concurrent race?): {idx_name}")
            skipped.append(idx_name)
        except Exception as e:
            logger.error(f"  FAILED to create {idx_name}: {e}")
            failed.append(idx_name)

    cur.close()
    conn.close()

    return {"created": created, "skipped": skipped, "failed": failed}


def _run_explain_checks(dsn: str) -> dict:
    """Validate that the new indexes are usable by the query planner."""
    results = []
    conn = psycopg.connect(dsn, autocommit=True)
    cur = conn.cursor()

    test_queries: list[tuple[str, str]] = [
        (
            "knowledge_chunks_source_created_idx",
            """
            EXPLAIN (FORMAT JSON)
            SELECT source_id, chunk_index
            FROM knowledge_chunks
            WHERE source = 'news' AND created_at >= NOW() - INTERVAL '7 days'
            ORDER BY created_at DESC
            """,
        ),
        (
            "agent_memories_active_importance_created_idx",
            """
            EXPLAIN (FORMAT JSON)
            SELECT category, content, importance
            FROM agent_memories
            WHERE deleted_at IS NULL AND importance >= 3
            ORDER BY importance DESC, created_at DESC
            LIMIT 15
            """,
        ),
        (
            "ingestion_jobs_source_status_started_idx",
            """
            EXPLAIN (FORMAT JSON)
            SELECT id, source, status
            FROM ingestion_jobs
            WHERE source = 'wikipedia' AND status = 'done'
            ORDER BY started_at DESC
            LIMIT 1
            """,
        ),
    ]

    for idx_name, sql in test_queries:
        try:
            cur.execute(sql)
            plan = cur.fetchone()[0]
            # Quick and dirty: inspect plan JSON for index scan type
            plan_str = str(plan)
            if "Index Scan" in plan_str or "Index Only Scan" in plan_str:
                results.append((idx_name, "Index scan detected ✅"))
            elif "Seq Scan" in plan_str:
                results.append((idx_name, "WARNING: Sequential Scan ⚠️"))
            else:
                results.append((idx_name, "Unknown plan type ℹ️"))
        except Exception as e:
            results.append((idx_name, f"EXPLAIN error: {e}"))

    cur.close()
    conn.close()
    return results


def main():
    parser = argparse.ArgumentParser(description="Add performance indexes post-migration")
    parser.add_argument("--dry-run", action="store_true", help="Preview SQL, do not execute")
    parser.add_argument("--explain", action="store_true", help="Validate plans after applying indexes")
    parser.add_argument("--explain-only", action="store_true", help="Only validate plans, do not create indexes")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit(
            "DATABASE_URL is required. Set it in .env or pass as an environment variable."
        )

    if args.explain_only:
        logger.info("Running EXPLAIN checks only...")
        results = _run_explain_checks(dsn)
        print("\n" + "━" * 60)
        print("EXPLAIN results:")
        for idx_name, status in results:
            print(f"  {idx_name:<52} {status}")
        print("━" * 60)
        return

    logger.info(f"Running index migration (dry_run={args.dry_run})...")
    print("━" * 60)
    report = _run_migration(dsn, dry_run=args.dry_run)
    print("━" * 60)
    print(f"\nSUMMARY")
    print(f"  Created : {len(report['created'])}")
    for c in report["created"]:
        print(f"    - {c}")
    print(f"  Skipped : {len(report['skipped'])}")
    for s in report["skipped"]:
        print(f"    - {s}")
    if report["failed"]:
        print(f"  Failed  : {len(report['failed'])}")
        for f in report["failed"]:
            print(f"    - {f}")
    print()

    if not args.dry_run and args.explain and report["created"]:
        logger.info("Running EXPLAIN checks on newly-created indexes...")
        results = _run_explain_checks(dsn)
        print("━" * 60)
        print("EXPLAIN results:")
        for idx_name, status in results:
            print(f"  {idx_name:<52} {status}")
        print("━" * 60)


if __name__ == "__main__":
    main()
