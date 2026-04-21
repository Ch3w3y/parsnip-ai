#!/usr/bin/env python3
"""
Analysis Server — executes Python and R scripts for data analysis,
generates visualizations (PNG/SVG), markdown reports, and serves outputs.

Architecture:
  1. Agent sends script code → server executes it
  2. Outputs saved to /app/output (persistent volume)
  3. File server serves outputs at GET /outputs/{path}
  4. Agent gets URLs → references in Joplin notes as links
  5. Only small images go through Joplin resource upload

Tools exposed via HTTP API:
  POST /execute/python  — run a Python script (with pytest + git commit)
  POST /execute/r       — run an R script (with testthat + git commit)
  GET  /outputs         — list generated outputs
  GET  /outputs/{file}  — download/serve a generated file
  POST /git/log         — view git history of analysis scripts
"""

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
import uuid
import base64
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel

import sys
sys.path.insert(0, "/app")  # Allow importing storage module
from storage.gcs import GCSClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _ensure_dir(path: Path) -> Path:
    """Create directory if possible, else fall back to a temp directory."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except (OSError, PermissionError):
        fallback = Path(tempfile.gettempdir()) / path.name
        fallback.mkdir(parents=True, exist_ok=True)
        logger.warning(f"Could not create {path}, using fallback {fallback}")
        return fallback


OUTPUT_DIR = _ensure_dir(Path(os.environ.get("OUTPUT_DIR", "/app/output")))
SCHEDULES_DIR = _ensure_dir(Path(os.environ.get("SCHEDULES_DIR", "/app/schedules")))
JOBS_FILE = SCHEDULES_DIR / "jobs.json"

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Analysis Server", lifespan=lifespan)

_cache = OrderedDict()
_cache_max = 500
_cache_stats = {"hits": 0, "misses": 0}

# Execution log — tracks every script run with model/stack metadata
_execution_log: list[dict] = []
_execution_log_max = 1000

# Joplin MCP server URL
JOPLIN_MCP_URL = os.environ.get("JOPLIN_MCP_URL", "http://localhost:8090")
ANALYSIS_URL = os.environ.get("ANALYSIS_URL", "http://localhost:8095")

_gcs = GCSClient()

# Database connection for scripts
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "agent_kb")
DB_USER = os.environ.get("DB_USER", "agent")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "[REDACTED]")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")

# Git config
GIT_EMAIL = os.environ.get("GIT_EMAIL", "agent@pi-agent.local")
GIT_NAME = os.environ.get("GIT_NAME", "Research Agent")


class PythonScript(BaseModel):
    code: str
    description: str = ""
    save_to_joplin: bool = True
    notebook_id: str = ""
    run_tests: bool = True
    model: str = ""  # LLM model that generated this script


class RScript(BaseModel):
    code: str
    description: str = ""
    save_to_joplin: bool = True
    notebook_id: str = ""
    run_tests: bool = True
    model: str = ""  # LLM model that generated this script


class WorkspaceFile(BaseModel):
    path: str
    content: str = ""


class WorkspaceDir(BaseModel):
    path: str


class BashCommand(BaseModel):
    command: str
    workdir: str = ""
    timeout: int = 120


class WriteAndExecute(BaseModel):
    path: str
    code: str
    language: str = "python"
    run_tests: bool = True


class NotebookRequest(BaseModel):
    cells: list[dict]
    """List of cells: {'type': 'code'|'markdown', 'source': 'cell content'}"""
    description: str = ""
    save_to_joplin: bool = False
    notebook_id: str = ""


class DashboardRequest(BaseModel):
    title: str
    scripts: list[dict]
    """List of {name, code, language} to execute and include in dashboard."""


class ScheduleCreate(BaseModel):
    cron: str
    code: str
    language: str = "python"
    description: str = ""


class ScheduledJob(BaseModel):
    job_id: str
    cron: str
    code: str
    language: str = "python"
    description: str = ""
    created_at: str = ""
    last_run: str = ""
    last_status: str = ""


def _git_commit(work_dir: Path, message: str):
    """Git commit the script and its outputs."""
    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(work_dir),
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(work_dir),
            capture_output=True,
            timeout=10,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": GIT_NAME,
                "GIT_AUTHOR_EMAIL": GIT_EMAIL,
                "GIT_COMMITTER_NAME": GIT_NAME,
                "GIT_COMMITTER_EMAIL": GIT_EMAIL,
            },
        )
    except Exception as e:
        logger.warning(f"Git commit failed: {e}")


def _detect_type(path: str | Path) -> str:
    path = Path(path)
    ext = path.suffix.lower()
    types = {
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".html": "text/html",
        ".md": "text/markdown",
        ".csv": "text/csv",
        ".json": "application/json",
        ".txt": "text/plain",
        ".py": "text/x-python",
        ".R": "text/x-r",
        ".r": "text/x-r",
        ".Rmd": "text/x-rmarkdown",
        ".rmd": "text/x-rmarkdown",
        ".ipynb": "application/x-ipynb+json",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    return types.get(ext, "application/octet-stream")


def _upload_to_gcs(local_path: str, gcs_path: str) -> str | None:
    """Upload a file to GCS and return a signed URL, or None if GCS is unavailable."""
    if not _gcs.available:
        return None
    try:
        gcs_uri = _gcs.upload_file(local_path, gcs_path, content_type=_detect_type(local_path))
        signed_url = _gcs.signed_url(gcs_path, expiry_hours=168)
        logger.info(f"GCS upload: {Path(local_path).name} -> {gcs_uri}")
        return signed_url
    except Exception as e:
        logger.warning(f"GCS upload failed for {local_path}: {e}")
        return None


def _slug(text: str) -> str:
    """Convert description to a URL-safe slug (max 32 chars)."""
    import re
    s = re.sub(r"[^a-z0-9]+", "_", text.lower().strip())
    return s.strip("_")[:32] or "run"


def _git_hash(work_dir: Path) -> str:
    """Return short git hash of HEAD in work_dir, or empty string."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(work_dir), capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _archive_run_to_gcs(
    work_dir: Path,
    language: str,
    description: str,
    script_id: str,
    code: str,
    stdout: str,
    stderr: str,
    test_passed: bool | None,
    output_files: list[Path],
) -> dict:
    """Archive script + outputs to GCS under language_slug_githash/ prefix.

    Returns a dict mapping filename → signed URL (or {} if GCS unavailable).
    """
    if not _gcs.available:
        return {}

    git_hash = _git_hash(work_dir) or script_id
    slug = _slug(description or script_id)
    prefix = f"analysis/archive/{language}_{slug}_{git_hash}"

    urls: dict[str, str] = {}

    # Upload the script source
    script_ext = "py" if language == "python" else "R"
    script_gcs = f"{prefix}/script.{script_ext}"
    script_local = work_dir / f"script.{script_ext}"
    if script_local.exists():
        url = _upload_to_gcs(str(script_local), script_gcs)
        if url:
            urls[f"script.{script_ext}"] = url

    # Upload output files
    for f in output_files:
        url = _upload_to_gcs(str(f), f"{prefix}/{f.name}")
        if url:
            urls[f.name] = url

    # Upload metadata.json
    metadata = {
        "language": language,
        "description": description,
        "script_id": script_id,
        "git_hash": git_hash,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "test_passed": test_passed,
        "stdout": stdout[:4000],
        "stderr": stderr[:2000],
        "files": list(urls.keys()),
    }
    import tempfile as _tmp
    with _tmp.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as mf:
        json.dump(metadata, mf, indent=2)
        mf_path = mf.name
    url = _upload_to_gcs(mf_path, f"{prefix}/metadata.json")
    if url:
        urls["metadata.json"] = url
    Path(mf_path).unlink(missing_ok=True)

    logger.info(f"GCS archive: {prefix}/ ({len(urls)} files)")
    return urls


def _file_url(relative_path: str, gcs_urls: dict | None = None) -> str:
    """Return a GCS signed URL if available in gcs_urls, else localhost fallback."""
    fname = Path(relative_path).name
    if gcs_urls and fname in gcs_urls:
        return gcs_urls[fname]
    return f"{ANALYSIS_URL}/outputs/{relative_path}"


@app.post("/execute/python")
async def execute_python(req: PythonScript, x_user_id: str | None = Header(None)):
    """Execute a Python script with pytest validation and git versioning."""
    t0 = time.time()
    ck = _cache_key(req.code, req.description)
    cached = _cache_get(ck)
    if cached:
        return cached

    script_id = str(uuid.uuid4())[:8]
    work_dir = _get_output_dir(x_user_id) / f"python_{script_id}"
    work_dir.mkdir()

    # Write script
    script_path = work_dir / "script.py"
    script_path.write_text(req.code, encoding="utf-8")

    # Environment variables for DB and output access
    env = os.environ.copy()
    env.update(
        {
            "DB_HOST": DB_HOST,
            "DB_PORT": DB_PORT,
            "DB_NAME": DB_NAME,
            "DB_USER": DB_USER,
            "DB_PASSWORD": DB_PASSWORD,
            "OUTPUT_DIR": str(work_dir),
            "ANALYSIS_URL": ANALYSIS_URL,
        }
    )

    try:
        # Phase 1: Run tests first if requested
        test_result = None
        if req.run_tests:
            test_path = work_dir / "test_script.py"
            test_code = f"""
import sys, os, subprocess, pathlib
sys.path.insert(0, str({repr(str(work_dir))}))
os.environ.update({{
    "DB_HOST": {repr(DB_HOST)}, "DB_PORT": {repr(DB_PORT)},
    "DB_NAME": {repr(DB_NAME)}, "DB_USER": {repr(DB_USER)},
    "DB_PASSWORD": {repr(DB_PASSWORD)}, "OUTPUT_DIR": {repr(str(work_dir))},
}})

def test_script_runs():
    \"\"\"Test that the script executes without errors.\"\"\"
    result = subprocess.run(
        [sys.executable, {repr(str(script_path))}],
        capture_output=True, text=True, timeout=120,
        env=os.environ.copy()
    )
    assert result.returncode == 0, f"Script failed: {{result.stderr[:500]}}"

def test_output_files_exist():
    \"\"\"Test that output files were generated.\"\"\"
    files = list(pathlib.Path({repr(str(work_dir))}).glob("*"))
    files = [f for f in files if f.name not in ("script.py", "test_script.py", "__pycache__")]
    assert len(files) > 0, "No output files generated"
"""
            test_path.write_text(test_code, encoding="utf-8")
            test_result = subprocess.run(
                ["python", "-m", "pytest", str(test_path), "-v", "--tb=short"],
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
                cwd=str(work_dir),
            )
            # If tests fail, don't proceed
            if test_result.returncode != 0:
                return {
                    "script_id": script_id,
                    "status": "tests_failed",
                    "test_stdout": test_result.stdout,
                    "test_stderr": test_result.stderr,
                    "message": "Unit tests failed. Fix the script and retry.",
                }

        # Phase 2: Run the main script
        result = subprocess.run(
            ["python", str(script_path)],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            cwd=str(work_dir),
        )

        # Phase 3: Git commit
        _git_commit(work_dir, f"Analysis: {req.description or script_id}")

        # Phase 4: Collect outputs
        output_files = list(work_dir.glob("*"))
        output_files = [
            f
            for f in output_files
            if f.name not in ("script.py", "test_script.py", "__pycache__", ".git")
        ]

        # Phase 4b: Archive to GCS (script + outputs + metadata)
        test_passed = test_result.returncode == 0 if test_result else None
        gcs_urls = _archive_run_to_gcs(
            work_dir, "python", req.description, script_id,
            req.code, result.stdout, result.stderr, test_passed, output_files,
        )

        response = {
            "script_id": script_id,
            "status": "success" if result.returncode == 0 else "error",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
            "output_files": [],
            "gcs_archive": gcs_urls or None,
        }

        if test_result:
            response["tests"] = {
                "passed": test_result.returncode == 0,
                "stdout": test_result.stdout[:2000],
            }

        for f in output_files:
            file_info = {
                "filename": f.name,
                "size": f.stat().st_size,
                "type": _detect_type(f),
                "url": _file_url(f"{work_dir.name}/{f.name}", gcs_urls=gcs_urls),
            }
            response["output_files"].append(file_info)

        # Phase 5: Save to Joplin if requested
        if req.save_to_joplin and response["output_files"]:
            joplin_result = await _save_to_joplin(
                f"Analysis: {req.description or script_id}",
                req.code,
                response["output_files"],
                req.notebook_id,
            )
            response["joplin"] = joplin_result

        _cache_set(ck, response)
        elapsed = time.time() - t0
        _log_execution(script_id, "python", req.description,
                       response["status"], elapsed, len(output_files),
                       model=req.model)
        return response

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Script timed out (5 min limit)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute/r")
async def execute_r(req: RScript, x_user_id: str | None = Header(None)):
    """Execute an R script with testthat validation and git versioning."""
    t0 = time.time()
    ck = _cache_key(req.code, req.description)
    cached = _cache_get(ck)
    if cached:
        return cached

    script_id = str(uuid.uuid4())[:8]
    work_dir = _get_output_dir(x_user_id) / f"r_{script_id}"
    work_dir.mkdir()

    script_path = work_dir / "script.R"
    script_path.write_text(req.code, encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "DB_HOST": DB_HOST,
            "DB_PORT": DB_PORT,
            "DB_NAME": DB_NAME,
            "DB_USER": DB_USER,
            "DB_PASSWORD": DB_PASSWORD,
            "OUTPUT_DIR": str(work_dir),
            "ANALYSIS_URL": ANALYSIS_URL,
        }
    )

    try:
        # Phase 1: Run testthat tests if requested
        test_result = None
        if req.run_tests:
            test_code = f"""
library(testthat)
Sys.setenv(DB_HOST="{DB_HOST}", DB_PORT="{DB_PORT}", DB_NAME="{DB_NAME}",
           DB_USER="{DB_USER}", DB_PASSWORD="{DB_PASSWORD}",
           OUTPUT_DIR="{work_dir}")

test_that("script runs without error", {{
    result <- system2("Rscript", {repr(str(script_path))},
                      stdout=TRUE, stderr=TRUE)
    expect_true(TRUE)  # If we get here, system2 doesn't crash
}})

test_that("output files exist", {{
    files <- list.files("{work_dir}", full.names=TRUE)
    files <- files[!basename(files) %in% c("script.R", "test_script.R")]
    expect_true(length(files) > 0, info="No output files generated")
}})
"""
            test_path = work_dir / "test_script.R"
            test_path.write_text(test_code, encoding="utf-8")
            test_result = subprocess.run(
                ["Rscript", str(test_path)],
                capture_output=True,
                text=True,
                timeout=180,
                env=env,
                cwd=str(work_dir),
            )
            if test_result.returncode != 0:
                return {
                    "script_id": script_id,
                    "status": "tests_failed",
                    "test_stdout": test_result.stdout,
                    "test_stderr": test_result.stderr,
                    "message": "R testthat tests failed. Fix the script and retry.",
                }

        # Phase 2: Run the main script
        result = subprocess.run(
            ["Rscript", str(script_path)],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            cwd=str(work_dir),
        )

        # Phase 3: Git commit
        _git_commit(work_dir, f"R Analysis: {req.description or script_id}")

        # Phase 4: Collect outputs
        output_files = list(work_dir.glob("*"))
        output_files = [
            f
            for f in output_files
            if f.name not in ("script.R", "test_script.R", ".git")
        ]

        # Phase 4b: Archive to GCS (script + outputs + metadata)
        test_passed = test_result.returncode == 0 if test_result else None
        gcs_urls = _archive_run_to_gcs(
            work_dir, "r", req.description, script_id,
            req.code, result.stdout, result.stderr, test_passed, output_files,
        )

        response = {
            "script_id": script_id,
            "status": "success" if result.returncode == 0 else "error",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
            "output_files": [],
            "gcs_archive": gcs_urls or None,
        }

        if test_result:
            response["tests"] = {
                "passed": test_result.returncode == 0,
                "stdout": test_result.stdout[:2000],
            }

        for f in output_files:
            file_info = {
                "filename": f.name,
                "size": f.stat().st_size,
                "type": _detect_type(f),
                "url": _file_url(f"{work_dir.name}/{f.name}", gcs_urls=gcs_urls),
            }
            response["output_files"].append(file_info)

        # Phase 5: Save to Joplin if requested
        if req.save_to_joplin and response["output_files"]:
            joplin_result = await _save_to_joplin(
                f"R Analysis: {req.description or script_id}",
                req.code,
                response["output_files"],
                req.notebook_id,
                language="r",
            )
            response["joplin"] = joplin_result

        _cache_set(ck, response)
        elapsed = time.time() - t0
        _log_execution(script_id, "r", req.description,
                       response["status"], elapsed, len(output_files),
                       model=req.model)
        return response

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Script timed out (5 min limit)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/outputs")
async def list_outputs():
    """List all generated output files with URLs."""
    files = []
    for d in sorted(OUTPUT_DIR.iterdir()):
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file() and f.name != ".git":
                    rel = f.relative_to(OUTPUT_DIR)
                    files.append(
                        {
                            "path": str(rel),
                            "size": f.stat().st_size,
                            "type": _detect_type(f),
                            "url": _file_url(str(rel)),
                            "modified": datetime.fromtimestamp(
                                f.stat().st_mtime, tz=timezone.utc
                            ).isoformat(),
                        }
                    )
    return {"files": files, "count": len(files)}


@app.get("/outputs/{path:path}")
async def get_output(path: str):
    """Serve a generated file (image, CSV, HTML, notebook, etc.)."""
    file_path = OUTPUT_DIR / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path), media_type=_detect_type(file_path))


@app.post("/git/log")
async def git_log(path: str = ""):
    """View git history for analysis scripts."""
    target = OUTPUT_DIR / path if path else OUTPUT_DIR
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--all", "-20"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(target),
        )
        return {"log": result.stdout, "return_code": result.returncode}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/workspace/list")
async def workspace_list(path: str = "", x_user_id: str | None = Header(None)):
    """List files and directories in a workspace path."""
    base = _get_output_dir(x_user_id)
    target = base / path if path else base
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    entries = []
    for entry in sorted(target.iterdir()):
        rel = str(entry.relative_to(base))
        stat = entry.stat()
        entries.append(
            {
                "name": entry.name,
                "path": rel,
                "type": "directory" if entry.is_dir() else "file",
                "size": stat.st_size if entry.is_file() else None,
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        )
    return {"path": path or ".", "entries": entries, "count": len(entries)}


@app.get("/workspace/read")
async def workspace_read(path: str, x_user_id: str | None = Header(None)):
    """Read the content of a file in the workspace."""
    base = _get_output_dir(x_user_id)
    target = base / path
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if target.stat().st_size > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large to read (>5MB)")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = base64.b64encode(target.read_bytes()).decode("ascii")
        return {"path": path, "content": content, "encoding": "base64"}
    return {"path": path, "content": content, "encoding": "utf-8"}


@app.post("/workspace/write")
async def workspace_write(req: WorkspaceFile, x_user_id: str | None = Header(None)):
    """Write content to a file in the workspace. Creates parent directories if needed."""
    base = _get_output_dir(x_user_id)
    target = base / req.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    return {"path": req.path, "status": "written", "size": target.stat().st_size}


@app.post("/workspace/mkdir")
async def workspace_mkdir(req: WorkspaceDir, x_user_id: str | None = Header(None)):
    """Create a directory in the workspace (parents created if needed)."""
    base = _get_output_dir(x_user_id)
    target = base / req.path
    target.mkdir(parents=True, exist_ok=True)
    return {"path": req.path, "status": "created"}


@app.post("/workspace/delete")
async def workspace_delete(req: WorkspaceFile, x_user_id: str | None = Header(None)):
    """Delete a file or empty directory in the workspace."""
    base = _get_output_dir(x_user_id)
    target = base / req.path
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if target.is_dir():
        try:
            target.rmdir()
        except OSError:
            raise HTTPException(
                status_code=400, detail="Directory not empty — delete contents first"
            )
    else:
        target.unlink()
    return {"path": req.path, "status": "deleted"}


@app.post("/workspace/move")
async def workspace_move(
    source: str, destination: str, x_user_id: str | None = Header(None)
):
    """Move or rename a file/directory in the workspace."""
    base = _get_output_dir(x_user_id)
    src = base / source
    dst = base / destination
    if not src.exists():
        raise HTTPException(status_code=404, detail="Source not found")
    src.rename(dst)
    return {"source": source, "destination": destination, "status": "moved"}


@app.post("/workspace/bash")
async def workspace_bash(req: BashCommand, x_user_id: str | None = Header(None)):
    """Execute a bash command in the workspace. Use for multi-step iterative development."""
    base = _get_output_dir(x_user_id)
    workdir = base / req.workdir if req.workdir else base
    if not workdir.exists():
        raise HTTPException(status_code=404, detail="Working directory not found")

    env = os.environ.copy()
    env.update(
        {
            "DB_HOST": DB_HOST,
            "DB_PORT": DB_PORT,
            "DB_NAME": DB_NAME,
            "DB_USER": DB_USER,
            "DB_PASSWORD": DB_PASSWORD,
            "OUTPUT_DIR": str(workdir),
            "ANALYSIS_URL": ANALYSIS_URL,
        }
    )

    try:
        result = subprocess.run(
            req.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=req.timeout,
            env=env,
            cwd=str(workdir),
        )
        return {
            "command": req.command,
            "return_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504, detail=f"Command timed out ({req.timeout}s limit)"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/workspace/write_and_execute")
async def workspace_write_and_execute(
    req: WriteAndExecute, x_user_id: str | None = Header(None)
):
    """Write a script file and execute it in one atomic operation.

    Use this instead of write_workspace_file + execute_bash_command for code.
    Avoids the read-verify-write corruption loop that LLMs fall into.
    """
    base = _get_output_dir(x_user_id)
    target = base / req.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.code, encoding="utf-8")

    lang = req.language.lower()
    if lang in ("python", "py"):
        interpreter = ["python"]
    elif lang in ("r", "rscript"):
        interpreter = ["Rscript"]
    elif lang in ("bash", "sh"):
        interpreter = ["bash"]
    else:
        interpreter = ["python"]

    env = os.environ.copy()
    env.update(
        {
            "DB_HOST": DB_HOST,
            "DB_PORT": DB_PORT,
            "DB_NAME": DB_NAME,
            "DB_USER": DB_USER,
            "DB_PASSWORD": DB_PASSWORD,
            "OUTPUT_DIR": str(target.parent),
            "ANALYSIS_URL": ANALYSIS_URL,
        }
    )

    try:
        result = subprocess.run(
            interpreter + [str(target)],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            cwd=str(target.parent),
        )

        output_files = []
        for f in target.parent.iterdir():
            if f.is_file() and f.name != target.name:
                rel = f.relative_to(OUTPUT_DIR)
                output_files.append(
                    {
                        "filename": f.name,
                        "size": f.stat().st_size,
                        "type": _detect_type(f),
                        "url": _file_url(str(rel), local_path=str(f)),
                    }
                )

        return {
            "path": req.path,
            "status": "written",
            "size": target.stat().st_size,
            "execution": {
                "return_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            "output_files": output_files,
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Script timed out (5 min limit)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/execute/notebook")
async def execute_notebook(req: NotebookRequest):
    """Execute a Jupyter-style notebook with code and markdown cells.

    Cells are executed sequentially. Code cell outputs (text, plots) are captured.
    The executed notebook is saved as .ipynb and an HTML export is generated.
    """
    import nbformat
    from nbconvert import HTMLExporter
    import nbclient

    script_id = str(uuid.uuid4())[:8]
    work_dir = OUTPUT_DIR / f"notebook_{script_id}"
    work_dir.mkdir()

    nb_path = work_dir / "notebook.ipynb"

    try:
        nb = nbformat.v4.new_notebook()
        for cell in req.cells:
            if cell.get("type") == "markdown":
                nb.cells.append(nbformat.v4.new_markdown_cell(cell.get("source", "")))
            else:
                nb.cells.append(nbformat.v4.new_code_cell(cell.get("source", "")))

        # Execute the notebook
        client = nbclient.NotebookClient(nb, timeout=300, kernel_name="python3")
        client.execute()

        # Save executed notebook
        nbformat.write(nb, str(nb_path))

        # Export to HTML
        html_exporter = HTMLExporter()
        html_body, _ = html_exporter.from_notebook_node(nb)
        html_path = work_dir / "notebook.html"
        html_path.write_text(html_body, encoding="utf-8")

        # Collect outputs
        output_files = []
        for f in work_dir.iterdir():
            if f.is_file() and f.name not in (".git",):
                rel = f.relative_to(OUTPUT_DIR)
                output_files.append(
                    {
                        "filename": f.name,
                        "size": f.stat().st_size,
                        "type": _detect_type(f),
                        "url": _file_url(str(rel), local_path=str(f)),
                    }
                )

        # Extract text outputs from code cells
        cell_outputs = []
        for cell in nb.cells:
            if cell.cell_type == "code" and cell.outputs:
                text_out = []
                for output in cell.outputs:
                    if output.output_type == "stream":
                        text_out.append(output.text)
                    elif output.output_type == "execute_result":
                        text_out.append(output.data.get("text/plain", ""))
                if text_out:
                    cell_outputs.append(
                        {
                            "cell_index": cell.execution_count,
                            "output": "\n".join(text_out),
                        }
                    )

        response = {
            "notebook_id": script_id,
            "status": "success",
            "cell_outputs": cell_outputs,
            "output_files": output_files,
        }

        if req.save_to_joplin and output_files:
            code_repr = "\n\n".join(
                c.get("source", "") for c in req.cells if c.get("type") == "code"
            )
            joplin_result = await _save_to_joplin(
                f"Notebook: {req.description or script_id}",
                code_repr,
                output_files,
                req.notebook_id,
            )
            response["joplin"] = joplin_result

        return response

    except nbclient.exceptions.CellExecutionError as e:
        raise HTTPException(
            status_code=400, detail=f"Notebook cell execution failed: {e}"
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Notebook timed out (5 min limit)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate/dashboard")
async def generate_dashboard(req: DashboardRequest):
    """Execute multiple scripts and generate an HTML dashboard with all results.

    Each script is executed in order. Outputs (plots, tables, text) are collected
    into a single HTML dashboard with navigation.
    """
    script_id = str(uuid.uuid4())[:8]
    work_dir = OUTPUT_DIR / f"dashboard_{script_id}"
    work_dir.mkdir()

    all_outputs = []
    all_files = []

    env = os.environ.copy()
    env.update(
        {
            "DB_HOST": DB_HOST,
            "DB_PORT": DB_PORT,
            "DB_NAME": DB_NAME,
            "DB_USER": DB_USER,
            "DB_PASSWORD": DB_PASSWORD,
            "OUTPUT_DIR": str(work_dir),
            "ANALYSIS_URL": ANALYSIS_URL,
        }
    )

    for i, script in enumerate(req.scripts):
        lang = script.get("language", "python").lower()
        interpreter = ["python"] if lang in ("python", "py") else ["Rscript"]
        script_path = (
            work_dir / f"script_{i}.{'py' if lang in ('python', 'py') else 'R'}"
        )
        script_path.write_text(script["code"], encoding="utf-8")

        try:
            result = subprocess.run(
                interpreter + [str(script_path)],
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
                cwd=str(work_dir),
            )
            all_outputs.append(
                {
                    "name": script.get("name", f"Script {i + 1}"),
                    "return_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
        except subprocess.TimeoutExpired:
            all_outputs.append(
                {"name": script.get("name", f"Script {i + 1}"), "error": "Timed out"}
            )

    # Collect all output files
    for f in work_dir.iterdir():
        if f.is_file() and f.name.startswith("script_"):
            continue
        rel = f.relative_to(OUTPUT_DIR)
        all_files.append(
            {
                "filename": f.name,
                "size": f.stat().st_size,
                "type": _detect_type(f),
                "url": _file_url(str(rel), local_path=str(f)),
            }
        )

    # Generate HTML dashboard
    html_parts = [
        "<!DOCTYPE html><html><head>",
        f"<title>{req.title}</title>",
        "<style>",
        "body { font-family: -apple-system, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }",
        "h1 { color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 10px; }",
        ".section { margin: 20px 0; padding: 15px; background: #f8f9fa; border-radius: 8px; }",
        ".section h2 { color: #0066cc; margin-top: 0; }",
        ".output { background: #fff; padding: 10px; border: 1px solid #ddd; border-radius: 4px; overflow-x: auto; }",
        ".error { color: #dc3545; }",
        "img { max-width: 100%; height: auto; margin: 10px 0; }",
        "nav { background: #0066cc; padding: 10px 20px; border-radius: 8px; margin-bottom: 20px; }",
        "nav a { color: white; text-decoration: none; margin-right: 15px; }",
        "nav a:hover { text-decoration: underline; }",
        "</style></head><body>",
        f"<h1>{req.title}</h1>",
        f"<p>Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>",
        "<nav>",
    ]

    for i, output in enumerate(all_outputs):
        anchor = output["name"].lower().replace(" ", "-")
        html_parts.append(f'<a href="#{anchor}">{output["name"]}</a>')

    html_parts.append("</nav>")

    for i, output in enumerate(all_outputs):
        anchor = output["name"].lower().replace(" ", "-")
        html_parts.append(f'<div class="section" id="{anchor}">')
        html_parts.append(f"<h2>{output['name']}</h2>")

        if "error" in output:
            html_parts.append(f'<div class="output error">{output["error"]}</div>')
        else:
            if output.get("stdout"):
                html_parts.append(
                    f'<div class="output"><pre>{output["stdout"]}</pre></div>'
                )
            if output.get("stderr"):
                html_parts.append(
                    f'<div class="output error"><pre>{output["stderr"]}</pre></div>'
                )

        html_parts.append("</div>")

    # Add image files to dashboard
    images = [f for f in all_files if f["type"].startswith("image/")]
    if images:
        html_parts.append('<div class="section"><h2>Visualizations</h2>')
        for img in images:
            html_parts.append(
                f"<h3>{img['filename']}</h3>"
                f'<img src="{img["url"]}" alt="{img["filename"]}">'
            )
        html_parts.append("</div>")

    html_parts.append("</body></html>")

    dashboard_path = work_dir / "dashboard.html"
    dashboard_path.write_text("\n".join(html_parts), encoding="utf-8")

    dashboard_url = _file_url(f"dashboard_{script_id}/dashboard.html", local_path=str(dashboard_path))

    return {
        "dashboard_id": script_id,
        "title": req.title,
        "url": dashboard_url,
        "scripts_executed": len(all_outputs),
        "outputs": all_outputs,
        "files": all_files,
    }


async def _save_to_joplin(
    title: str, code: str, files: list[dict], notebook_id: str, language: str = "python"
) -> dict:
    """Save analysis results to Joplin via MCP server.

    Flow:
    1. Create a markdown note with code and file URLs
    2. For images, also upload as Joplin resources (for inline display)
    3. Link to file server for downloads (notebooks, datasets, etc.)
    """
    lang_fence = "r" if language == "r" else "python"
    md_parts = [
        f"# {title}",
        f"\n*Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}*\n",
    ]
    md_parts.append(f"\n## Script\n\n```{lang_fence}\n" + code + "\n```\n")
    md_parts.append("\n## Outputs\n")

    for f in files:
        if f["type"].startswith("image/"):
            md_parts.append(f"\n![{f['filename']}]({f['url']})")
        elif f["type"] in ("text/html", "application/x-ipynb+json"):
            md_parts.append(f"\n- [{f['filename']}]({f['url']}) (view/download)")
        else:
            md_parts.append(f"\n- [{f['filename']}]({f['url']}) ({f['type']})")

    content = "\n".join(md_parts)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{JOPLIN_MCP_URL}/tools/joplin_create_note",
                json={
                    "tool": "joplin_create_note",
                    "arguments": {
                        "title": title,
                        "content": content,
                        "notebook_id": notebook_id,
                    },
                },
            )
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.warning(f"Failed to save to Joplin: {e}")

    return {
        "title": title,
        "files_linked": len(files),
        "note": "Joplin save failed, files still available at URLs",
    }


def _get_output_dir(user_id: str | None = None) -> Path:
    """Return the output directory, optionally isolated by user_id."""
    if user_id:
        d = OUTPUT_DIR / user_id
        d.mkdir(exist_ok=True)
        return d
    return OUTPUT_DIR


def _cache_key(code: str, description: str) -> str:
    return hashlib.sha256(f"{code}:{description}".encode()).hexdigest()[:16]


def _cache_get(key: str):
    if key in _cache:
        _cache.move_to_end(key)
        _cache_stats["hits"] += 1
        return _cache[key]
    _cache_stats["misses"] += 1
    return None


def _cache_set(key: str, value: dict):
    _cache[key] = value
    _cache.move_to_end(key)
    while len(_cache) > _cache_max:
        _cache.popitem(last=False)


def _log_execution(script_id: str, language: str, description: str,
                   status: str, elapsed_s: float, output_count: int,
                   triggered_by: str = "direct", model: str = ""):
    """Log an execution with model/stack metadata."""
    entry = {
        "script_id": script_id,
        "language": language,
        "description": description,
        "status": status,
        "elapsed_s": round(elapsed_s, 3),
        "output_files": output_count,
        "triggered_by": triggered_by,
        "model": model or "direct (no LLM)",
        "stack": {
            "r_packages": "ggplot2, tidyverse, patchwork, ggrepel, scales" if language == "r" else None,
            "python_libs": "matplotlib, pandas, numpy, scipy, scikit-learn" if language == "python" else None,
            "embedding_model": EMBED_MODEL if language != "r" else "N/A (no embedding)",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _execution_log.append(entry)
    while len(_execution_log) > _execution_log_max:
        _execution_log.pop(0)
    logger.info(f"Execution: {language}/{script_id} [{status}] {elapsed_s:.3f}s "
                f"outputs={output_count} model=\"{model}\" desc=\"{description}\"")


def _load_jobs() -> dict:
    if JOBS_FILE.exists():
        with open(JOBS_FILE) as f:
            return json.load(f)
    return {}


def _save_jobs(jobs: dict):
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2)


async def _execute_scheduled_job(job_id: str):
    """Execute a scheduled job's script and save output."""
    jobs = _load_jobs()
    job = jobs.get(job_id)
    if not job:
        return

    lang = job.get("language", "python").lower()
    code = job.get("code", "")
    script_id = str(uuid.uuid4())[:8]
    work_dir = OUTPUT_DIR / f"scheduled_{script_id}"
    work_dir.mkdir()

    ext = "py" if lang in ("python", "py") else "R" if lang == "r" else "py"
    script_path = work_dir / f"script.{ext}"
    script_path.write_text(code, encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "DB_HOST": DB_HOST,
            "DB_PORT": DB_PORT,
            "DB_NAME": DB_NAME,
            "DB_USER": DB_USER,
            "DB_PASSWORD": DB_PASSWORD,
            "OUTPUT_DIR": str(work_dir),
            "ANALYSIS_URL": ANALYSIS_URL,
        }
    )

    interpreter = ["python"] if lang in ("python", "py") else ["Rscript"]

    try:
        result = subprocess.run(
            interpreter + [str(script_path)],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            cwd=str(work_dir),
        )

        output_files = []
        for f in work_dir.iterdir():
            if f.is_file() and f.name not in (f"script.{ext}",):
                rel = f.relative_to(OUTPUT_DIR)
                output_files.append(
                    {
                        "filename": f.name,
                        "size": f.stat().st_size,
                        "type": _detect_type(f),
                        "url": _file_url(str(rel), local_path=str(f)),
                    }
                )

        job["last_run"] = datetime.now(timezone.utc).isoformat()
        job["last_status"] = "success" if result.returncode == 0 else "error"
        jobs[job_id] = job
        _save_jobs(jobs)

        logger.info(f"Scheduled job {job_id} completed: {job['last_status']}")
    except subprocess.TimeoutExpired:
        job["last_run"] = datetime.now(timezone.utc).isoformat()
        job["last_status"] = "timeout"
        jobs[job_id] = job
        _save_jobs(jobs)
    except Exception as e:
        logger.error(f"Scheduled job {job_id} failed: {e}")
        job["last_run"] = datetime.now(timezone.utc).isoformat()
        job["last_status"] = f"error: {e}"
        jobs[job_id] = job
        _save_jobs(jobs)


def _parse_cron(cron_str: str) -> dict:
    """Parse a cron expression into APScheduler kwargs."""
    parts = cron_str.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {cron_str} (expected 5 fields)")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


@app.post("/schedule/create")
async def schedule_create(req: ScheduleCreate):
    """Create a scheduled job with cron expression, script code, and language."""
    job_id = str(uuid.uuid4())[:8]
    cron_kwargs = _parse_cron(req.cron)

    job_def = {
        "job_id": job_id,
        "cron": req.cron,
        "code": req.code,
        "language": req.language,
        "description": req.description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_run": "",
        "last_status": "",
    }

    jobs = _load_jobs()
    jobs[job_id] = job_def
    _save_jobs(jobs)

    try:
        scheduler.add_job(
            _execute_scheduled_job,
            CronTrigger(**cron_kwargs),
            args=[job_id],
            id=f"schedule_{job_id}",
            replace_existing=True,
        )
    except Exception as e:
        del jobs[job_id]
        _save_jobs(jobs)
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {e}")

    return {"job_id": job_id, "status": "created", **job_def}


@app.get("/schedule/list")
async def schedule_list():
    """List all scheduled jobs."""
    jobs = _load_jobs()
    return {"jobs": list(jobs.values()), "count": len(jobs)}


@app.get("/schedule/{job_id}")
async def schedule_get(job_id: str):
    """Get a specific scheduled job."""
    jobs = _load_jobs()
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.delete("/schedule/{job_id}")
async def schedule_delete(job_id: str):
    """Remove a scheduled job."""
    jobs = _load_jobs()
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        scheduler.remove_job(f"schedule_{job_id}")
    except Exception:
        pass

    del jobs[job_id]
    _save_jobs(jobs)
    return {"job_id": job_id, "status": "deleted"}


@app.post("/schedule/{job_id}/run")
async def schedule_run(job_id: str):
    """Manually trigger a scheduled job."""
    jobs = _load_jobs()
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    await _execute_scheduled_job(job_id)
    return {"job_id": job_id, "status": "triggered"}


@app.get("/cache/stats")
async def cache_stats():
    """Show cache hit/miss rates."""
    total = _cache_stats["hits"] + _cache_stats["misses"]
    hit_rate = _cache_stats["hits"] / total if total > 0 else 0.0
    return {
        "hits": _cache_stats["hits"],
        "misses": _cache_stats["misses"],
        "total": total,
        "hit_rate": round(hit_rate, 4),
        "size": len(_cache),
        "max_size": _cache_max,
    }


@app.get("/executions")
async def list_executions(limit: int = 20):
    """List recent script executions with model/stack metadata."""
    return {
        "executions": _execution_log[-limit:][::-1],  # newest first
        "total": len(_execution_log),
    }


@app.post("/cache/clear")
async def cache_clear():
    """Clear the execution result cache."""
    _cache.clear()
    _cache_stats["hits"] = 0
    _cache_stats["misses"] = 0
    return {"status": "cleared"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8095)
