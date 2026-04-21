"""Analysis server tools — execute Python and R scripts via the analysis server."""

import json
import os
import re
import httpx
import psycopg
from langchain_core.tools import tool

ANALYSIS_URL = os.environ.get("ANALYSIS_URL", "http://localhost:8095")
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _get_agent_model() -> str:
    return os.environ.get("AGENT_CURRENT_MODEL", "")


def _normalize_code(code) -> str:
    """Normalize code argument — some models (Qwen) pass code as a list of strings
    or nested lists instead of a single string. Flatten and join."""
    if isinstance(code, str):
        return code
    if isinstance(code, list):
        parts = []
        for item in code:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, list):
                parts.extend(item)
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(code)


def _fail_fast_error(kind: str, missing: list[str], detail: str) -> str:
    return json.dumps(
        {
            "status": "error",
            "error_type": "fail_fast_missing_requirements",
            "kind": kind,
            "missing": missing,
            "detail": detail,
            "message": (
                "Required identifiers are missing from the knowledge base. "
                "Analysis was not executed to avoid costly fallback runs."
            ),
        }
    )


async def _preflight_required_identifiers(code: str, description: str = "") -> str | None:
    """Fail fast when scripts reference explicit identifiers that are absent in structured tables.

    Current checks:
    - World Bank indicator codes (e.g. NY.GDP.MKTP.KD.ZG) against world_bank_data.indicator_code
    - Forex pairs (e.g. GBP/BRL) against forex_rates.pair
    """
    if not DATABASE_URL:
        return None

    user_request = os.environ.get("AGENT_USER_REQUEST", "")
    text = f"{user_request}\n{description}\n{code}"
    lower = text.lower()
    request_lower = user_request.lower()

    # Enforce required source usage contracts from the user request.
    if "world bank" in request_lower and "world_bank_data" not in lower:
        return _fail_fast_error(
            kind="required_source_missing",
            missing=["world_bank_data"],
            detail=(
                "User requested World Bank data, but the script does not query world_bank_data."
            ),
        )

    # World Bank indicator codes
    wb_codes = sorted(
        set(re.findall(r"\b[A-Z]{2}(?:\.[A-Z0-9]{2,}){3,5}\b", text))
    )
    if wb_codes and ("world_bank_data" in lower or "world bank" in lower):
        # Resolve partial indicator tokens to full codes when there is a unique match.
        resolved_codes: list[str] = []
        async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT DISTINCT indicator_code FROM world_bank_data WHERE indicator_code = ANY(%s)",
                    (wb_codes,),
                )
                found = {row[0] async for row in cur}
            for code_token in wb_codes:
                if code_token in found:
                    resolved_codes.append(code_token)
                    continue
                await cur.execute(
                    "SELECT DISTINCT indicator_code FROM world_bank_data WHERE indicator_code LIKE %s",
                    (f"{code_token}.%",),
                )
                candidates = [row[0] async for row in cur]
                if len(candidates) == 1:
                    resolved_codes.append(candidates[0])
                else:
                    resolved_codes.append(code_token)

                await cur.execute(
                    "SELECT DISTINCT indicator_code FROM world_bank_data WHERE indicator_code = ANY(%s)",
                    (resolved_codes,),
                )
                found = {row[0] async for row in cur}
        missing = [c for c in resolved_codes if c not in found]
        if missing:
            return _fail_fast_error(
                kind="world_bank_indicator_code",
                missing=missing,
                detail=(
                    "Do not substitute with 'closest available' indicators unless the user explicitly approves."
                ),
            )

        # If countries are explicitly specified in user request/script, require data coverage
        # for each country+indicator pair.
        country_codes = set(re.findall(r"'([A-Z]{3})'", code))

        # Match explicit country names/codes from user request against available World Bank countries.
        async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT DISTINCT country_code, country_name FROM world_bank_data")
                wb_countries = [(row[0], row[1]) async for row in cur]
        for cc, cn in wb_countries:
            if cc and cc in text:
                country_codes.add(cc)
            if cn and cn.lower() in request_lower:
                country_codes.add(cc)

        country_codes = sorted(country_codes)
        if country_codes:
            async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT country_code, indicator_code, COUNT(*) FILTER (WHERE value IS NOT NULL) AS n
                        FROM world_bank_data
                        WHERE country_code = ANY(%s) AND indicator_code = ANY(%s)
                        GROUP BY country_code, indicator_code
                        """,
                        (country_codes, resolved_codes),
                    )
                    observed = {(row[0], row[1]): int(row[2]) async for row in cur}
            missing_pairs = []
            for cc in country_codes:
                for ic in resolved_codes:
                    if observed.get((cc, ic), 0) <= 0:
                        missing_pairs.append(f"{cc}:{ic}")
            if missing_pairs:
                return _fail_fast_error(
                    kind="world_bank_country_indicator_coverage",
                    missing=missing_pairs,
                    detail=(
                        "At least one required indicator has no non-null data for a requested country. "
                        "Do not substitute indicators or countries without explicit user approval."
                    ),
                )

    # Forex pairs
    fx_pairs = sorted(set(re.findall(r"\b[A-Z]{3}/[A-Z]{3}\b", text)))
    if fx_pairs and "forex_rates" in lower:
        async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT DISTINCT pair FROM forex_rates WHERE pair = ANY(%s)",
                    (fx_pairs,),
                )
                found_pairs = {row[0] async for row in cur}
        missing_pairs = [p for p in fx_pairs if p not in found_pairs]
        if missing_pairs:
            return _fail_fast_error(
                kind="forex_pair",
                missing=missing_pairs,
                detail="Do not substitute currency pairs unless the user explicitly approves.",
            )

    return None


@tool
async def execute_python_script(
    code: str,
    description: str = "",
    save_to_joplin: bool = True,
    notebook_id: str = "",
    run_tests: bool = False,
) -> str:
    """Execute a Python script on the analysis server.

    The script can access the knowledge base via env vars:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.
    Save outputs (PNG, CSV, etc.) to the OUTPUT_DIR env var.

    Args:
        code: The complete Python script to execute.
        description: Brief description of what the script does.
        save_to_joplin: Whether to save results as a Joplin note.
        notebook_id: Joplin notebook ID to save to (empty = default).
        run_tests: Whether to run pytest before execution (disabled by default — subprocess tests are restricted in this container).

    Returns:
        JSON string with execution status, stdout/stderr, and output file URLs.
    """
    normalized_code = _normalize_code(code)
    preflight_error = await _preflight_required_identifiers(
        code=normalized_code,
        description=description,
    )
    if preflight_error:
        return preflight_error

    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/execute/python",
            json={
                "code": normalized_code,
                "description": description,
                "save_to_joplin": save_to_joplin,
                "notebook_id": notebook_id,
                "run_tests": run_tests,
                "model": _get_agent_model(),
            },
        )
        return r.text


@tool
async def execute_r_script(
    code: str,
    description: str = "",
    save_to_joplin: bool = True,
    notebook_id: str = "",
    run_tests: bool = False,
) -> str:
    """Execute an R script on the analysis server.

    The script can access the knowledge base via env vars:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.
    Save outputs (PNG, CSV, etc.) to the OUTPUT_DIR env var.

    Args:
        code: The complete R script to execute.
        description: Brief description of what the script does.
        save_to_joplin: Whether to save results as a Joplin note.
        notebook_id: Joplin notebook ID to save to (empty = default).
        run_tests: Whether to run testthat before execution (disabled by default — testthat subprocess is restricted in this container).

    Returns:
        JSON string with execution status, stdout/stderr, and output file URLs.
    """
    normalized_code = _normalize_code(code)
    preflight_error = await _preflight_required_identifiers(
        code=normalized_code,
        description=description,
    )
    if preflight_error:
        return preflight_error

    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/execute/r",
            json={
                "code": normalized_code,
                "description": description,
                "save_to_joplin": save_to_joplin,
                "notebook_id": notebook_id,
                "run_tests": run_tests,
                "model": _get_agent_model(),
            },
        )
        return r.text


@tool
async def list_analysis_outputs() -> str:
    """List all generated output files from previous analysis runs.

    Returns:
        JSON string with file paths, sizes, types, and URLs.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{ANALYSIS_URL}/outputs")
        return r.text


@tool
async def execute_notebook(
    cells: list[dict],
    description: str = "",
    save_to_joplin: bool = False,
) -> str:
    """Execute a Jupyter notebook with code and markdown cells on the analysis server.

    Each cell dict should have 'type' ('code' or 'markdown') and 'content' (str).
    Returns notebook and HTML URLs for viewing results.

    Args:
        cells: List of cell dicts, e.g. [{"type": "markdown", "content": "# Title"}, {"type": "code", "content": "import pandas"}]
        description: Brief description of the notebook.
        save_to_joplin: Whether to save the notebook as a Joplin note.
    """
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/execute/notebook",
            json={
                "cells": cells,
                "description": description,
                "save_to_joplin": save_to_joplin,
            },
        )
        return r.text


@tool
async def generate_dashboard(
    scripts: list[dict],
    title: str = "",
    description: str = "",
) -> str:
    """Generate an HTML dashboard from multiple analysis scripts.

    Each script dict should have 'language' ('python' or 'r') and 'code' (str).
    Returns a URL to the generated dashboard.

    Args:
        scripts: List of script dicts, e.g. [{"language": "python", "code": "import matplotlib.pyplot as plt; ..."}]
        title: Dashboard title.
        description: Dashboard description.
    """
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/generate/dashboard",
            json={
                "scripts": scripts,
                "title": title,
                "description": description,
            },
        )
        return r.text


@tool
async def create_scheduled_job(
    cron: str,
    code: str,
    language: str = "python",
    description: str = "",
) -> str:
    """Create a scheduled analysis job that runs on a cron schedule.

    The job executes the script code at the specified intervals. Results are
    saved to the analysis output directory.

    Args:
        cron: Cron expression (e.g. '0 9 * * *' for daily at 9am, '0 */6 * * *' for every 6 hours)
        code: Script code to execute
        language: 'python' or 'r' (default 'python')
        description: Human-readable description of the job
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/schedule/create",
            json={
                "cron": cron,
                "code": code,
                "language": language,
                "description": description,
            },
        )
        return r.text


@tool
async def list_scheduled_jobs() -> str:
    """List all scheduled analysis jobs on the analysis server."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{ANALYSIS_URL}/schedule/list")
        return r.text


@tool
async def delete_scheduled_job(job_id: str) -> str:
    """Delete a scheduled analysis job.

    Args:
        job_id: The job ID to delete (from list_scheduled_jobs)
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.delete(f"{ANALYSIS_URL}/schedule/{job_id}")
        return r.text
