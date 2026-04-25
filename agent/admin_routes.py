from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def require_admin(
    x_admin_token: Annotated[str, Header(..., alias="X-Admin-Token")],
) -> str:
    if os.getenv("ADMIN_ENABLED", "").lower() != "true":
        raise HTTPException(status_code=404, detail="Admin endpoints disabled")
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected or x_admin_token != expected:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return x_admin_token


class ServiceHealth(BaseModel):
    name: str
    status: str
    health: str
    uptime_seconds: float | None


class StackHealthResponse(BaseModel):
    services: list[ServiceHealth]


class BackupEntry(BaseModel):
    name: str
    size_bytes: int
    created_at: str
    type: str


class BackupListResponse(BaseModel):
    backups: list[BackupEntry]


class TriggerBackupRequest(BaseModel):
    type: str


class TriggerBackupResponse(BaseModel):
    job_id: str
    status: str


class RestorePreviewResponse(BaseModel):
    backup_id: str
    type: str
    size_bytes: int
    created_at: str
    would_restore_files: list[str]
    estimated_duration_sec: int


class ExecuteRestoreRequest(BaseModel):
    backup_id: str
    type: str
    confirm_token: str


class ExecuteRestoreResponse(BaseModel):
    status: str
    pid: int | None


async def _run_command(cmd: list[str], timeout: int = 60) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(status_code=504, detail="Command timed out")
    return proc.returncode, stdout.decode(), stderr.decode()


_restore_nonces: dict[str, tuple[str, float]] = {}


def _generate_nonce(backup_id: str) -> str:
    token = uuid.uuid4().hex
    _restore_nonces[token] = (backup_id, time.time())
    cutoff = time.time() - 300
    _restore_nonces.update(
        {k: v for k, v in _restore_nonces.items() if v[1] > cutoff}
    )
    return token


def _invalidate_nonce(token: str) -> bool:
    return _restore_nonces.pop(token, None) is not None


_KNOWN_SERVICES = [
    "pi_agent_postgres",
    "pi_agent_backend",
    "pi_agent_joplin",
    "pi_agent_searxng",
    "pi_agent_frontend",
    "pi_agent_analysis",
    "pi_agent_pipelines",
    "pi_agent_openwebui",
    "pi_agent_scheduler",
]

_HEALTH_ENDPOINTS: dict[str, str | None] = {
    "pi_agent_postgres": None,
    "pi_agent_backend": "http://pi_agent_backend:8000/health",
    "pi_agent_joplin": "http://pi_agent_joplin:22300/api/ping",
    "pi_agent_searxng": "http://pi_agent_searxng:8080/healthz",
    "pi_agent_frontend": None,
    "pi_agent_analysis": "http://pi_agent_analysis:8095/cache/stats",
    "pi_agent_pipelines": None,
    "pi_agent_openwebui": None,
    "pi_agent_scheduler": None,
}


async def _docker_ps_json() -> list[dict]:
    try:
        rc, out, _ = await _run_command(
            ["docker", "compose", "ps", "--format", "json"], timeout=30
        )
        if rc == 0 and out.strip():
            parsed = json.loads(out)
            return parsed if isinstance(parsed, list) else [parsed]
    except (json.JSONDecodeError, HTTPException):
        pass

    try:
        rc, out, _ = await _run_command(
            ["docker", "ps", "--format", "json"], timeout=30
        )
        if rc == 0 and out.strip():
            parsed = json.loads(out)
            return parsed if isinstance(parsed, list) else [parsed]
    except (json.JSONDecodeError, HTTPException):
        pass

    return []


async def _service_health(name: str, containers: list[dict]) -> ServiceHealth:
    container: dict | None = None
    for c in containers:
        cname = c.get("Names", c.get("Name", ""))
        if name in cname:
            container = c
            break

    status = (
        container.get("State", container.get("Status", "unknown"))
        if container
        else "not found"
    )
    health = "unknown"
    uptime_seconds: float | None = None

    if isinstance(status, str) and status.lower() == "running":
        endpoint = _HEALTH_ENDPOINTS.get(name)
        if endpoint:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(endpoint)
                    health = "healthy" if r.status_code == 200 else "unhealthy"
            except Exception:
                health = "unreachable"
        else:
            health = "running (no endpoint)"

        if container:
            started = container.get("StartedAt", "")
            if started:
                try:
                    started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    uptime_seconds = (
                        datetime.now(timezone.utc) - started_dt
                    ).total_seconds()
                except ValueError:
                    uptime_seconds = None
    else:
        health = "down"

    return ServiceHealth(
        name=name,
        status=str(status),
        health=health,
        uptime_seconds=uptime_seconds,
    )


admin_router = APIRouter(prefix="/admin", tags=["admin"])


@admin_router.get("/stack/health", response_model=StackHealthResponse)
async def stack_health(_: str = Depends(require_admin)) -> StackHealthResponse:
    containers = await _docker_ps_json()
    services = [await _service_health(name, containers) for name in _KNOWN_SERVICES]
    return StackHealthResponse(services=services)


@admin_router.get("/backups/list", response_model=BackupListResponse)
async def list_backups(
    _type: Annotated[str | None, Query(alias="type")] = None,
    _: str = Depends(require_admin),
) -> BackupListResponse:
    gcs_bucket = os.getenv("GCS_BUCKET", "")
    if not gcs_bucket:
        raise HTTPException(status_code=503, detail="GCS_BUCKET not configured")

    accepted = {"pg", "parquet", "volumes", "config"}
    types_to_query = {_type} if _type else accepted
    if not types_to_query.issubset(accepted):
        raise HTTPException(status_code=400, detail=f"Invalid type: {_type}")

    backups: list[BackupEntry] = []
    for btype in sorted(types_to_query):
        if btype == "pg":
            backups.extend(await _list_pg_backups())
        elif btype == "parquet":
            backups.extend(await _list_parquet_backups(gcs_bucket))
        elif btype == "config":
            backups.extend(await _list_config_backups(gcs_bucket))
        elif btype == "volumes":
            backups.extend(await _list_volumes_backups(gcs_bucket))

    return BackupListResponse(backups=backups)


async def _list_pg_backups() -> list[BackupEntry]:
    try:
        rc, out, _ = await _run_command(
            ["pgbackrest", "info", "--output", "json"], timeout=30
        )
        if rc != 0 or not out.strip():
            return []
        data = json.loads(out)
        entries: list[BackupEntry] = []
        for stanza in data if isinstance(data, list) else [data]:
            for backup in stanza.get("backup", []):
                label = backup.get("label", "unknown")
                size_raw = backup.get("info", {}).get("size", 0)
                timestamp = backup.get("timestamp", {}).get("stop", 0)
                if timestamp:
                    created = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
                else:
                    created = ""
                entries.append(
                    BackupEntry(
                        name=label,
                        size_bytes=int(size_raw),
                        created_at=created,
                        type="pg",
                    )
                )
        return entries
    except (json.JSONDecodeError, HTTPException):
        return []


async def _list_parquet_backups(gcs_bucket: str) -> list[BackupEntry]:
    try:
        rc, out, _ = await _run_command(
            [
                "gsutil",
                "cat",
                f"gs://{gcs_bucket}/backups/parquet/_manifest.json",
            ],
            timeout=30,
        )
        if rc != 0 or not out.strip():
            return []
        manifest = json.loads(out)
        entries: list[BackupEntry] = []
        ts = manifest.get("last_run", "")
        for table, meta in manifest.get("tables", {}).items():
            entries.append(
                BackupEntry(
                    name=f"{table}_backup",
                    size_bytes=0,
                    created_at=meta.get("last_run", ts),
                    type="parquet",
                )
            )
        return entries
    except (json.JSONDecodeError, HTTPException):
        return []


async def _list_config_backups(gcs_bucket: str) -> list[BackupEntry]:
    entries: list[BackupEntry] = []
    try:
        rc, out, _ = await _run_command(
            ["gsutil", "ls", f"gs://{gcs_bucket}/backups/config/"],
            timeout=30,
        )
        if rc == 0 and out.strip():
            for line in out.strip().splitlines():
                if line.startswith("gs://"):
                    entries.append(
                        BackupEntry(
                            name=line.rstrip("/").split("/")[-1],
                            size_bytes=0,
                            created_at="",
                            type="config",
                        )
                    )
    except HTTPException:
        pass
    return entries


async def _list_volumes_backups(gcs_bucket: str) -> list[BackupEntry]:
    entries: list[BackupEntry] = []
    try:
        rc, out, _ = await _run_command(
            ["gsutil", "ls", f"gs://{gcs_bucket}/volumes/"],
            timeout=30,
        )
        if rc == 0 and out.strip():
            for line in out.strip().splitlines():
                if line.startswith("gs://"):
                    entries.append(
                        BackupEntry(
                            name=line.rstrip("/").split("/")[-1],
                            size_bytes=0,
                            created_at="",
                            type="volumes",
                        )
                    )
    except HTTPException:
        pass
    return entries


@admin_router.post("/backups/trigger", response_model=TriggerBackupResponse)
async def trigger_backup(
    req: TriggerBackupRequest,
    _: str = Depends(require_admin),
) -> TriggerBackupResponse:
    if req.type not in {"kb", "config", "volumes"}:
        raise HTTPException(
            status_code=400,
            detail="type must be one of: kb, config, volumes",
        )

    script_map = {
        "kb": "scripts/backup_kb.py",
        "config": "scripts/backup_config.py",
        "volumes": "scripts/sync_volumes.py",
    }
    script = script_map[req.type]
    job_id = uuid.uuid4().hex
    asyncio.create_task(_run_command(["python", script], timeout=3600))
    return TriggerBackupResponse(job_id=job_id, status="triggered")


@admin_router.get("/restore/preview", response_model=RestorePreviewResponse)
async def restore_preview(
    backup_id: Annotated[str, Query(...)],
    type_: Annotated[str, Query(..., alias="type")],
    _: str = Depends(require_admin),
) -> RestorePreviewResponse:
    if type_ not in {"pg", "parquet", "config", "volumes"}:
        raise HTTPException(
            status_code=400,
            detail="type must be one of: pg, parquet, config, volumes",
        )

    would_restore_files: list[str] = []
    size_bytes = 0
    created_at = ""

    if type_ == "pg":
        try:
            rc, out, _ = await _run_command(
                ["pgbackrest", "info", "--output", "json"],
                timeout=30,
            )
            if rc == 0 and out.strip():
                data = json.loads(out)
                for stanza in data if isinstance(data, list) else [data]:
                    for backup in stanza.get("backup", []):
                        if backup.get("label") == backup_id:
                            size_raw = backup.get("info", {}).get("size", 0)
                            size_bytes = int(size_raw)
                            ts = backup.get("timestamp", {}).get("stop", 0)
                            if ts:
                                created_at = datetime.fromtimestamp(
                                    ts, tz=timezone.utc
                                ).isoformat()
                            would_restore_files.append(f"restore {backup_id}")
        except (json.JSONDecodeError, HTTPException):
            pass

    elif type_ == "parquet":
        try:
            rc, out, _ = await _run_command(
                [
                    "gsutil",
                    "cat",
                    f"gs://{os.getenv('GCS_BUCKET', '')}/backups/parquet/_manifest.json",
                ],
                timeout=30,
            )
            if rc == 0 and out.strip():
                manifest = json.loads(out)
                for table in manifest.get("tables", {}):
                    would_restore_files.append(f"restore table: {table}")
        except (json.JSONDecodeError, HTTPException):
            pass

    elif type_ == "config":
        try:
            rc, out, _ = await _run_command(
                [
                    "gsutil",
                    "ls",
                    f"gs://{os.getenv('GCS_BUCKET', '')}/backups/config/",
                ],
                timeout=30,
            )
            if rc == 0 and out.strip():
                would_restore_files = [
                    line.strip() for line in out.strip().splitlines()
                ]
        except HTTPException:
            pass

    elif type_ == "volumes":
        try:
            rc, out, _ = await _run_command(
                [
                    "gsutil",
                    "ls",
                    f"gs://{os.getenv('GCS_BUCKET', '')}/volumes/",
                ],
                timeout=30,
            )
            if rc == 0 and out.strip():
                would_restore_files = [
                    line.strip() for line in out.strip().splitlines()
                ]
        except HTTPException:
            pass

    estimated_duration_sec = max(60, len(would_restore_files) * 30)
    _generate_nonce(backup_id)

    return RestorePreviewResponse(
        backup_id=backup_id,
        type=type_,
        size_bytes=size_bytes,
        created_at=created_at,
        would_restore_files=would_restore_files,
        estimated_duration_sec=estimated_duration_sec,
    )


@admin_router.post("/restore/execute", response_model=ExecuteRestoreResponse)
async def execute_restore(
    req: ExecuteRestoreRequest,
    _: str = Depends(require_admin),
) -> ExecuteRestoreResponse:
    nonce_entry = _restore_nonces.get(req.confirm_token)
    if not nonce_entry or nonce_entry[0] != req.backup_id:
        raise HTTPException(status_code=403, detail="Invalid or expired confirm token")

    _invalidate_nonce(req.confirm_token)

    proc = await asyncio.create_subprocess_exec(
        "scripts/restore_stack.sh",
        "--from-gcs",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    return ExecuteRestoreResponse(status="started", pid=proc.pid)
