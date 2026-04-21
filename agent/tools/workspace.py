"""Workspace management tools — file/dir CRUD and bash execution on the analysis server."""

import os
import httpx
from langchain_core.tools import tool

ANALYSIS_URL = os.environ.get("ANALYSIS_URL", "http://localhost:8095")


@tool
async def list_workspace(path: str = "") -> str:
    """List files and directories in a workspace path on the analysis server.

    Args:
        path: Relative path within the workspace. Empty string lists the root.

    Returns:
        JSON string with directory entries (name, path, type, size, modified).
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{ANALYSIS_URL}/workspace/list", params={"path": path})
        return r.text


@tool
async def read_workspace_file(path: str) -> str:
    """Read the content of a file in the workspace.

    Args:
        path: Relative path to the file within the workspace.

    Returns:
        JSON string with file content and encoding (utf-8 or base64 for binary).
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{ANALYSIS_URL}/workspace/read", params={"path": path})
        return r.text


@tool
async def write_workspace_file(path: str, content: str) -> str:
    """Write content to a file in the workspace. Creates parent directories if needed.

    Args:
        path: Relative path to the file within the workspace.
        content: The text content to write.

    Returns:
        JSON string with path, status, and size.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/workspace/write",
            json={"path": path, "content": content},
        )
        return r.text


@tool
async def make_workspace_dir(path: str) -> str:
    """Create a directory in the workspace. Parent directories are created if needed.

    Args:
        path: Relative path for the new directory.

    Returns:
        JSON string with path and status.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/workspace/mkdir",
            json={"path": path},
        )
        return r.text


@tool
async def delete_workspace_item(path: str) -> str:
    """Delete a file or empty directory in the workspace.

    Args:
        path: Relative path to the file or directory.

    Returns:
        JSON string with path and status.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/workspace/delete",
            json={"path": path},
        )
        return r.text


@tool
async def move_workspace_item(source: str, destination: str) -> str:
    """Move or rename a file or directory in the workspace.

    Args:
        source: Current relative path.
        destination: New relative path.

    Returns:
        JSON string with source, destination, and status.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/workspace/move",
            params={"source": source, "destination": destination},
        )
        return r.text


@tool
async def execute_bash_command(
    command: str, workdir: str = "", timeout: int = 120
) -> str:
    """Execute an arbitrary bash command on the analysis server.

    Use this for multi-step iterative development: running pip install,
    git operations, downloading data, chaining scripts together, etc.
    DB env vars (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD) and
    OUTPUT_DIR are set automatically.

    Args:
        command: The bash command to execute.
        workdir: Working directory relative to workspace root (empty = root).
        timeout: Maximum seconds to wait (default 120).

    Returns:
        JSON string with command, return_code, stdout, and stderr.
    """
    async with httpx.AsyncClient(timeout=max(timeout, 30)) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/workspace/bash",
            json={"command": command, "workdir": workdir, "timeout": timeout},
        )
        return r.text


@tool
async def write_and_execute_script(
    path: str, code: str, language: str = "python", run_tests: bool = True
) -> str:
    """Write a script file to the workspace AND execute it in one atomic call.

    Use this INSTEAD of write_workspace_file + execute_bash_command when you
    need to write and run code. This avoids the read-verify-write corruption
    loop that can happen with separate write + read calls.

    The script is written to the workspace, then executed immediately.
    Output files (CSV, PNG, etc.) generated alongside the script are returned.
    DB env vars and OUTPUT_DIR are set automatically.

    Args:
        path: Relative path for the script file (e.g. "project/script.py").
        code: The complete script content.
        language: Script language — "python", "r", or "bash" (default "python").
        run_tests: Whether to run tests before execution (default True).

    Returns:
        JSON string with write status, execution result (return_code, stdout, stderr),
        and any output files generated.
    """
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/workspace/write_and_execute",
            json={
                "path": path,
                "code": code,
                "language": language,
                "run_tests": run_tests,
            },
        )
        return r.text


@tool
async def execute_workspace_script(
    path: str, code: str, language: str = "python"
) -> str:
    """Atomic write+execute for complex scripts. THE PREFERRED tool for all code.

    This is the most robust way to run code: writes the file and executes it
    in a single operation. No read-back step, no corruption loop risk.

    Use this when:
    - A previous script failed and you need to fix and re-execute
    - Writing multi-file projects (call once per file)
    - You want to avoid the write → read → write corruption cycle entirely

    Unlike write_and_execute_script, this does NOT run tests first — it's
    for quick iteration. Use write_and_execute_script for final validated scripts.

    Args:
        path: Relative path for the script file (e.g. "project/analysis.py").
        code: The complete script content (must be fully self-contained).
        language: Script language — "python", "r", or "bash" (default "python").

    Returns:
        JSON string with write status, execution result (return_code, stdout, stderr),
        and any output files generated alongside the script.
    """
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{ANALYSIS_URL}/workspace/write_and_execute",
            json={
                "path": path,
                "code": code,
                "language": language,
                "run_tests": False,
            },
        )
        return r.text
