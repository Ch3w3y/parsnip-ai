"""joplin_hitl — Human-in-the-Loop (HITL) workflow for Joplin notes.

Provides LangChain @tool functions for the generate → detect edits →
review → publish cycle.  Tracks LLM-generated note versions via a
`joplin_hitl_sessions` table so edits by the user can be detected and
reviewed before the LLM publishes a new version.

Tools: generate_note, detect_edits, review_edited_note, publish_review.
"""

import hashlib
import json
import logging
import time
from difflib import unified_diff

from langchain_core.tools import tool

from tools.db_pool import get_pool
from tools.joplin_pg import (
    joplin_create_note,
    joplin_get_note,
    joplin_update_note,
    ensure_joplin_pool,
)

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _content_hash(content: str) -> str:
    """Compute a short SHA-256 hash of the content for change detection."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _extract_body(note_text: str) -> str:
    """Extract the body from joplin_get_note output (strips title heading and metadata)."""
    lines = note_text.split("\n")
    # Skip the title line (# Title) and any blank line after it
    body_start = 0
    if lines and lines[0].startswith("# "):
        body_start = 1
        # Skip blank line after title
        if len(lines) > 1 and lines[1] == "":
            body_start = 2

    # Stop at the metadata separator (---)
    body_lines = []
    for line in lines[body_start:]:
        if line.strip() == "---":
            break
        body_lines.append(line)

    return "\n".join(body_lines).strip()


# ── HITL sessions table DDL ─────────────────────────────────────────────────

_HITL_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS joplin_hitl_sessions (
    id            SERIAL PRIMARY KEY,
    note_id       TEXT NOT NULL,
    last_llm_content TEXT NOT NULL,
    last_llm_hash TEXT NOT NULL,
    cycle_count   INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'generated',
    created_at    BIGINT NOT NULL,
    updated_at    BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_joplin_hitl_note_id ON joplin_hitl_sessions (note_id);
"""


async def _ensure_hitl_table():
    """Make sure the joplin_hitl_sessions table exists."""
    await ensure_joplin_pool()
    pool = get_pool("joplin")
    async with pool.connection() as conn:
        await conn.execute(_HITL_TABLE_DDL)


# ── LangChain @tool functions ────────────────────────────────────────────────


@tool
async def generate_note(
    title: str,
    content: str,
    notebook_id: str = "",
) -> str:
    """Generate a new Joplin note and track it in the HITL workflow.

    Creates the note via joplin_pg and stores the LLM content hash in
    joplin_hitl_sessions so user edits can be detected later.

    Args:
        title: Note title
        content: Markdown body for the note
        notebook_id: Parent folder ID (optional)
    """
    await _ensure_hitl_table()

    # Create the note
    create_result = await joplin_create_note.ainvoke({
        "title": title,
        "content": content,
        "notebook_id": notebook_id,
    })

    # Extract note_id from the result text
    note_id = ""
    for line in create_result.split("\n"):
        if "Note ID:" in line:
            # Extract from backticked ID
            start = line.find("`") + 1
            end = line.rfind("`")
            if start > 0 and end > start:
                note_id = line[start:end]
                break

    if not note_id:
        return f"Note created but HITL tracking failed — could not extract note_id.\n{create_result}"

    # Compute content hash
    content_hash = _content_hash(content)
    now = int(time.time() * 1000)

    pool = get_pool("joplin")
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO joplin_hitl_sessions
                (note_id, last_llm_content, last_llm_hash, cycle_count, status, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            note_id, content, content_hash, 0, "generated", now, now,
        )

    uri = f"joplin://x-callback-url/openNote?id={note_id}"
    return json.dumps({
        "note_id": note_id,
        "uri": uri,
        "hitl_status": "generated",
        "content_hash": content_hash,
    })


@tool
async def detect_edits(note_id: str) -> str:
    """Detect whether a HITL-tracked note has been edited by the user.

    Compares the current note content hash against the stored LLM hash.
    Returns whether edits were detected and the current status.

    Args:
        note_id: Note ID to check
    """
    await _ensure_hitl_table()

    pool = get_pool("joplin")
    async with pool.connection() as conn:
        session = await conn.fetchrow(
            "SELECT * FROM joplin_hitl_sessions WHERE note_id = $1 ORDER BY id DESC LIMIT 1",
            note_id,
        )

    if not session:
        return json.dumps({
            "note_id": note_id,
            "status": "not_tracked",
            "message": "No HITL session found for this note. Use generate_note first.",
        })

    # Get the current note content
    note_result = await joplin_get_note.ainvoke({"note_id": note_id})

    # If not found, report
    if "not found" in note_result.lower():
        return json.dumps({
            "note_id": note_id,
            "status": "note_missing",
            "message": "Note not found in Joplin.",
        })

    current_body = _extract_body(note_result)
    current_hash = _content_hash(current_body)
    stored_hash = session["last_llm_hash"]

    edited = current_hash != stored_hash

    if edited:
        # Update status in DB
        now = int(time.time() * 1000)
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE joplin_hitl_sessions SET status = $1, updated_at = $2 WHERE id = $3",
                "edited", now, session["id"],
            )

    return json.dumps({
        "note_id": note_id,
        "edited": edited,
        "status": "edited" if edited else "unchanged",
        "stored_hash": stored_hash,
        "current_hash": current_hash,
    })


@tool
async def review_edited_note(note_id: str) -> str:
    """Review the diff between the LLM's original version and the user's edits.

    Returns a structured diff showing added lines, removed lines, and
    modified sections.

    Args:
        note_id: Note ID to review
    """
    await _ensure_hitl_table()

    pool = get_pool("joplin")
    async with pool.connection() as conn:
        session = await conn.fetchrow(
            "SELECT * FROM joplin_hitl_sessions WHERE note_id = $1 ORDER BY id DESC LIMIT 1",
            note_id,
        )

    if not session:
        return json.dumps({
            "note_id": note_id,
            "status": "not_tracked",
            "message": "No HITL session found for this note. Use generate_note first.",
        })

    # Get current note content
    note_result = await joplin_get_note.ainvoke({"note_id": note_id})

    if "not found" in note_result.lower():
        return json.dumps({
            "note_id": note_id,
            "status": "note_missing",
            "message": "Note not found in Joplin.",
        })

    current_body = _extract_body(note_result)
    original_content = session["last_llm_content"]

    # Compute unified diff
    original_lines = original_content.splitlines(keepends=True)
    current_lines = current_body.splitlines(keepends=True)
    diff_lines = list(unified_diff(
        original_lines,
        current_lines,
        fromfile="llm_version",
        tofile="user_version",
        lineterm="",
    ))

    # Categorize changes
    added_lines = [l[1:].strip() for l in diff_lines if l.startswith("+") and not l.startswith("+++")]
    removed_lines = [l[1:].strip() for l in diff_lines if l.startswith("-") and not l.startswith("---")]

    return json.dumps({
        "note_id": note_id,
        "status": session["status"],
        "cycle_count": session["cycle_count"],
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "diff": "\n".join(diff_lines),
    })


@tool
async def publish_review(
    note_id: str,
    reviewed_content: str,
) -> str:
    """Publish a reviewed version of the note after LLM review.

    Updates the note content in Joplin and stores the new LLM version
    hash in the HITL session. Increments the cycle count.

    Args:
        note_id: Note ID to update
        reviewed_content: The new reviewed content for the note
    """
    await _ensure_hitl_table()

    pool = get_pool("joplin")
    async with pool.connection() as conn:
        session = await conn.fetchrow(
            "SELECT * FROM joplin_hitl_sessions WHERE note_id = $1 ORDER BY id DESC LIMIT 1",
            note_id,
        )

    if not session:
        return json.dumps({
            "note_id": note_id,
            "status": "not_tracked",
            "message": "No HITL session found. Use generate_note first.",
        })

    # Update the note content
    update_result = await joplin_update_note.ainvoke({
        "note_id": note_id,
        "content": reviewed_content,
    })

    # Update the HITL session with new hash
    new_hash = _content_hash(reviewed_content)
    new_cycle = session["cycle_count"] + 1
    now = int(time.time() * 1000)

    async with pool.connection() as conn:
        await conn.execute(
            """
            UPDATE joplin_hitl_sessions
            SET last_llm_content = $1, last_llm_hash = $2,
                cycle_count = $3, status = $4, updated_at = $5
            WHERE id = $6
            """,
            reviewed_content, new_hash, new_cycle, "published", now, session["id"],
        )

    return json.dumps({
        "note_id": note_id,
        "status": "published",
        "cycle_count": new_cycle,
        "content_hash": new_hash,
        "update_result": update_result,
    })