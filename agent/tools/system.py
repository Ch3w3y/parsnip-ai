"""
System status tool — checks the health of all pi-agent components.
Queries Ollama, PostgreSQL, and reports KB statistics.
"""

import os
import sys

import httpx
import psycopg
from langchain_core.tools import tool


@tool
async def system_status() -> str:
    """Check the health and status of all system components.

    Returns information about:
    - Ollama embedding and LLM service status and available models
    - GPU LLM availability
    - PostgreSQL connection and knowledge base row counts by source
    - Memory count
    - Recent ingestion job history
    - GCS storage status (if configured)

    Use this to diagnose issues or get an overview of the system state.
    """
    parts = ["## System Status\n"]

    # 1. Ollama (embedding service)
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{ollama_url}/api/tags")
            r.raise_for_status()
            models = r.json().get("models", [])
            model_names = [m["name"] for m in models]
            parts.append(f"### Ollama (Embeddings)\n✅ Connected ({ollama_url})\nModels: {', '.join(model_names)}\n")
    except Exception as e:
        parts.append(f"### Ollama (Embeddings)\n❌ Unreachable: {e}\n")

    # 2. GPU LLM
    gpu_url = os.environ.get("GPU_LLM_URL", "")
    gpu_model = os.environ.get("GPU_LLM_MODEL", "")
    if gpu_url and gpu_model:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{gpu_url}/v1/models")
                r.raise_for_status()
                parts.append(f"### GPU LLM\n✅ Connected ({gpu_url})\nModel: {gpu_model}\n")
        except Exception as e:
            parts.append(f"### GPU LLM\n⚠️ Unreachable: {e}\n  (Falling back to OpenRouter)\n")
    else:
        parts.append("### GPU LLM\n⚪ Not configured (all calls → OpenRouter)\n")

    # 3. PostgreSQL
    db_url = os.environ.get("DATABASE_URL", "")
    try:
        conn = await psycopg.AsyncConnection.connect(db_url)
        try:
            # KB stats
            rows = await (await conn.execute(
                "SELECT source, COUNT(*) FROM knowledge_chunks GROUP BY source ORDER BY COUNT(*) DESC"
            )).fetchall()
            total_kb = sum(r[1] for r in rows)
            parts.append(f"### PostgreSQL\n✅ Connected\nKnowledge base: {total_kb:,} chunks\n")
            for source, count in rows:
                parts.append(f"  - {source}: {count:,}")

            # Memory count
            mem_count = await (await conn.execute(
                "SELECT COUNT(*) FROM agent_memories WHERE deleted_at IS NULL"
            )).fetchone()
            parts.append(f"\nMemories: {mem_count[0]:,}")

            # Recent ingestion jobs
            jobs = await (await conn.execute(
                "SELECT source, status, processed, total, started_at FROM ingestion_jobs ORDER BY started_at DESC LIMIT 5"
            )).fetchall()
            if jobs:
                parts.append("\nRecent ingestion jobs:")
                for source, status, processed, total, started_at in jobs:
                    parts.append(f"  - {source}: {status} ({processed}/{total or '?'}) at {started_at}")
        finally:
            await conn.close()
    except Exception as e:
        parts.append(f"### PostgreSQL\n❌ Error: {e}\n")

    # 4. GCS
    gcs_bucket = os.environ.get("GCS_BUCKET", "")
    if gcs_bucket:
        try:
            sys.path.insert(0, "/app")
            from storage.gcs import GCSClient
            gcs = GCSClient()
            if gcs.available:
                backups = gcs.list_objects("backups/")
                latest = [obj for obj in backups if "/latest/" in obj]
                parts.append(f"### GCS Storage\n✅ Connected (bucket: {gcs_bucket})\nBackups: {len(backups)} objects\n")
            else:
                parts.append(f"### GCS Storage\n⚠️ Configured but not connected\n")
        except Exception as e:
            parts.append(f"### GCS Storage\n⚠️ Error: {e}\n")
    else:
        parts.append("### GCS Storage\n⚪ Not configured\n")

    return "\n".join(parts)