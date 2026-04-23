"""joplin_pg — Direct PostgreSQL access layer for Joplin Server.

Provides LangChain @tool functions that operate directly on the Joplin
PostgreSQL database, bypassing the MCP HTTP layer.  Uses the named pool
"joplin" from tools.db_pool.

Tools: create_notebook, create_note, update_note, edit_note, delete_note,
       get_note, search_notes, list_notebooks, list_tags, get_tags_for_note,
       upload_resource, ping.
"""

import base64
import hashlib
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

from langchain_core.tools import tool

from tools.db_pool import get_pool, init_pool

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────

DB_HOST = os.environ.get("JOPLIN_DB_HOST", "localhost")
DB_PORT = os.environ.get("JOPLIN_DB_PORT", "5432")
DB_NAME = os.environ.get("JOPLIN_DB_NAME", "joplin")
DB_USER = os.environ.get("JOPLIN_DB_USER", "agent")
DB_PASSWORD = os.environ.get("JOPLIN_DB_PASSWORD", "")

DEFAULT_OWNER_ID = os.environ.get("JOPLIN_OWNER_ID", "")

JOPLIN_DSN = (
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)


async def ensure_joplin_pool():
    """Initialise the 'joplin' pool if it hasn't been created yet.

    Safe to call multiple times — if the pool already exists, this is a no-op.
    """
    try:
        get_pool("joplin")
    except ValueError:
        if not DB_PASSWORD:
            raise RuntimeError("JOPLIN_DB_PASSWORD must be set to initialise the joplin pool")
        await init_pool("joplin", JOPLIN_DSN)


# ── Timestamp helpers ────────────────────────────────────────────────────────


def _iso_ms() -> int:
    return int(time.time() * 1000)


def _iso_str(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3]
        + "Z"
    )


# ── Content helpers ──────────────────────────────────────────────────────────


def _note_json(title: str, body: str, notebook_id: str, now: int) -> bytes:
    return json.dumps({
        "deleted_time": 0,
        "user_data": "",
        "master_key_id": "",
        "conflict_original_id": "",
        "is_shared": 0,
        "markup_language": 1,
        "encryption_cipher_text": "",
        "user_updated_time": now,
        "user_created_time": now,
        "order": 0,
        "application_data": "",
        "source_application": "net.cozic.joplin-server",
        "source": "joplinapp-server",
        "todo_completed": 0,
        "todo_due": 0,
        "is_todo": 0,
        "source_url": "",
        "author": "",
        "altitude": "0.0000",
        "longitude": "0.00000000",
        "latitude": "0.00000000",
        "is_conflict": 0,
        "created_time": now,
        "title": title,
        "body": body,
    }).encode("utf-8")


def _folder_json(title: str, now: int) -> bytes:
    return json.dumps({
        "deleted_time": 0,
        "user_data": "",
        "icon": "",
        "master_key_id": "",
        "is_shared": 0,
        "encryption_cipher_text": "",
        "user_updated_time": now,
        "user_created_time": now,
        "created_time": now,
        "title": title,
    }).encode("utf-8")


def _parse_note(raw: bytes) -> dict:
    """Parse note/folder JSON content, return dict with at least title and body."""
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"title": "", "body": raw.decode("utf-8", errors="replace")}


async def _get_owner_id(conn) -> str:
    """Get the default owner ID from the users table."""
    if DEFAULT_OWNER_ID:
        return DEFAULT_OWNER_ID
    row = await conn.fetchrow(
        "SELECT id FROM users WHERE email = $1 LIMIT 1", "admin@pi-agent.local"
    )
    if row:
        return row["id"]
    row = await conn.fetchrow("SELECT id FROM users LIMIT 1")
    return row["id"] if row else ""


async def _write_change(conn, item_id: str, item_name: str, jop_type: int,
                        owner_id: str, now: int, change_type: int = 1):
    """Write a sync change record so the desktop app picks up the item."""
    change_id = uuid.uuid4().hex
    await conn.execute(
        """
        INSERT INTO changes (id, item_type, item_id, item_name, type,
                             updated_time, created_time, previous_item, user_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (id) DO NOTHING
        """,
        change_id, jop_type, item_id, item_name, change_type,
        now, now, "", owner_id,
    )


async def _insert_item(conn, item_id: str, name: str, mime: str, content_bytes: bytes,
                       jop_type: int, jop_parent_id: str, owner_id: str, now: int):
    await conn.execute(
        """
        INSERT INTO items (
            id, name, mime_type, updated_time, created_time,
            content, content_size, jop_id, jop_parent_id,
            jop_share_id, jop_type, jop_encryption_applied,
            jop_updated_time, owner_id, content_storage_id
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        """,
        item_id, name, mime, now, now,
        content_bytes, len(content_bytes),
        item_id, jop_parent_id, "",
        jop_type, 0, now, owner_id, 1,
    )
    await conn.execute(
        "INSERT INTO user_items (user_id, item_id, updated_time, created_time) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
        owner_id, item_id, now, now,
    )
    await _write_change(conn, item_id, name, jop_type, owner_id, now, change_type=1)


async def _add_tag_to_note(conn, note_id: str, tag_name: str, owner_id: str, now: int):
    """Add a tag to a note."""
    tag = await conn.fetchrow(
        "SELECT id FROM items WHERE jop_type = 17 AND name = $1 AND owner_id = $2",
        tag_name,
        owner_id,
    )
    if not tag:
        tag_id = uuid.uuid4().hex
        await conn.execute(
            """
            INSERT INTO items (id, name, mime_type, updated_time, created_time,
                content, content_size, jop_id, jop_parent_id, jop_share_id,
                jop_type, jop_encryption_applied, jop_updated_time, owner_id, content_storage_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            """,
            tag_id,
            tag_name,
            "text/plain",
            now,
            now,
            b"",
            0,
            tag_id,
            "",
            "",
            17,
            0,
            now,
            owner_id,
            1,
        )
    else:
        tag_id = tag["id"]

    await conn.execute(
        """
        INSERT INTO changes (item_type, item_id, type, created_time, source, user_updated_time)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        1,
        note_id,
        3,
        now,
        "joplin-pg",
        now,
    )


# ── LangChain @tool functions ────────────────────────────────────────────────


@tool
async def joplin_create_notebook(
    title: str,
    parent_notebook_id: str = "",
) -> str:
    """Create a new Joplin notebook (folder).

    Always prefix agent-created notebooks with "LLM Generated - " so they are
    visually distinct from the user's own notebooks.

    Args:
        title: Notebook name — prefix with "LLM Generated - " for agent-created notebooks
        parent_notebook_id: Parent notebook ID for nested notebooks (optional)
    """
    await ensure_joplin_pool()
    pool = get_pool("joplin")

    folder_id = uuid.uuid4().hex
    now = _iso_ms()

    async with pool.connection() as conn:
        owner_id = DEFAULT_OWNER_ID or await _get_owner_id(conn)
        content_bytes = _folder_json(title, now)
        await _insert_item(conn, folder_id, f"{folder_id}.md", "application/json",
                           content_bytes, 2, parent_notebook_id, owner_id, now)

    return (
        f"Notebook created: **{title}**\n"
        f"Notebook ID: `{folder_id}`\n\n"
        f"*Pass this ID as `notebook_id` when calling joplin_create_note.*"
    )


@tool
async def joplin_create_note(
    title: str,
    content: str,
    notebook_id: str = "",
    tags: list[str] | None = None,
) -> str:
    """Create a note in Joplin Server.

    Args:
        title: Note title
        content: Markdown body
        notebook_id: Parent folder ID (from joplin_list_notebooks)
        tags: Optional list of tags
    """
    await ensure_joplin_pool()
    pool = get_pool("joplin")

    note_id = uuid.uuid4().hex
    now = _iso_ms()

    async with pool.connection() as conn:
        owner_id = DEFAULT_OWNER_ID or await _get_owner_id(conn)
        content_bytes = _note_json(title, content, notebook_id, now)
        await _insert_item(conn, note_id, f"{note_id}.md", "application/json",
                           content_bytes, 1, notebook_id, owner_id, now)

        if tags:
            for tag_name in tags:
                await _add_tag_to_note(conn, note_id, tag_name, owner_id, now)

    uri = f"joplin://x-callback-url/openNote?id={note_id}"
    result = f"Note created: **{title}**\nNote ID: `{note_id}`\nOpen in Joplin: {uri}"
    if tags:
        result += f"\nTags: {', '.join(tags)}"

    return result


@tool
async def joplin_update_note(
    note_id: str,
    title: str = "",
    content: str = "",
    append: bool = False,
) -> str:
    """Update an existing Joplin note.

    Args:
        note_id: Note ID to update
        title: New title (optional — leave empty to keep existing)
        content: New content (optional — leave empty to keep existing)
        append: If true, append to existing content instead of replacing
    """
    await ensure_joplin_pool()
    pool = get_pool("joplin")
    now = _iso_ms()

    async with pool.connection() as conn:
        row = await conn.fetchrow(
            "SELECT content FROM items WHERE id = $1 AND jop_type = 1", note_id
        )
        if not row:
            return f"Note not found: {note_id}"

        data = _parse_note(row["content"])
        new_title = title if title else data.get("title", "")
        existing_body = data.get("body", "")
        if content:
            body = (existing_body + "\n\n" + content) if append else content
        else:
            body = existing_body

        data.update({"title": new_title, "body": body, "user_updated_time": now})
        content_bytes = json.dumps(data).encode("utf-8")

        await conn.execute(
            "UPDATE items SET content = $1, content_size = $2, updated_time = $3, jop_updated_time = $4 WHERE id = $5",
            content_bytes, len(content_bytes), now, now, note_id,
        )
        owner_id = DEFAULT_OWNER_ID or await _get_owner_id(conn)
        await _write_change(conn, note_id, f"{note_id}.md", 1, owner_id, now, change_type=1)

    uri = f"joplin://x-callback-url/openNote?id={note_id}"
    return f"Note updated: **{new_title}**\nOpen in Joplin: {uri}"


@tool
async def joplin_edit_note(
    note_id: str,
    find: str = "",
    replace: str = "",
    append: str = "",
    prepend: str = "",
    regex: bool = False,
) -> str:
    """Precision edit a Joplin note: find/replace, append, or prepend content.

    Args:
        note_id: Note ID to edit
        find: Text to find (or regex pattern if regex=True)
        replace: Replacement text for find/replace
        append: Text to append to the end of the note body
        prepend: Text to prepend to the beginning of the note body
        regex: Treat find as a regex pattern (default False)
    """
    await ensure_joplin_pool()
    pool = get_pool("joplin")
    now = _iso_ms()

    async with pool.connection() as conn:
        row = await conn.fetchrow(
            "SELECT content FROM items WHERE id = $1 AND jop_type = 1", note_id
        )
        if not row:
            return f"Note not found: {note_id}"

        data = _parse_note(row["content"])
        title = data.get("title", "")
        body = data.get("body", "")

        modified = False

        if find:
            if regex:
                try:
                    body = re.sub(find, replace, body)
                except re.error as e:
                    return f"Invalid regex pattern: {e}"
            else:
                body = body.replace(find, replace)
            modified = True

        if append:
            body = (body + "\n\n" + append) if body else append
            modified = True

        if prepend:
            body = (prepend + "\n\n" + body) if body else prepend
            modified = True

        if not modified:
            return f"No changes specified for note: {note_id}"

        data.update({"body": body, "user_updated_time": now})
        content_bytes = json.dumps(data).encode("utf-8")

        await conn.execute(
            "UPDATE items SET content = $1, content_size = $2, updated_time = $3, jop_updated_time = $4 WHERE id = $5",
            content_bytes, len(content_bytes), now, now, note_id,
        )
        owner_id = DEFAULT_OWNER_ID or await _get_owner_id(conn)
        await _write_change(conn, note_id, f"{note_id}.md", 1, owner_id, now, change_type=1)

    uri = f"joplin://x-callback-url/openNote?id={note_id}"
    return f"Note edited: **{title}**\nOpen in Joplin: {uri}"


@tool
async def joplin_delete_note(note_id: str) -> str:
    """Soft-delete a Joplin note by setting its deleted_time.

    Args:
        note_id: Note ID to delete
    """
    await ensure_joplin_pool()
    pool = get_pool("joplin")
    now = _iso_ms()

    async with pool.connection() as conn:
        row = await conn.fetchrow(
            "SELECT content FROM items WHERE id = $1 AND jop_type = 1", note_id
        )
        if not row:
            return f"Note not found: {note_id}"

        data = _parse_note(row["content"])
        title = data.get("title", note_id)
        data["deleted_time"] = now
        data["user_updated_time"] = now

        content_bytes = json.dumps(data).encode("utf-8")
        await conn.execute(
            "UPDATE items SET content = $1, content_size = $2, updated_time = $3, jop_updated_time = $4 WHERE id = $5",
            content_bytes, len(content_bytes), now, now, note_id,
        )
        owner_id = DEFAULT_OWNER_ID or await _get_owner_id(conn)
        await _write_change(conn, note_id, f"{note_id}.md", 1, owner_id, now, change_type=1)

    return f"Note deleted: **{title}** (ID: `{note_id}`)"


@tool
async def joplin_get_note(note_id: str) -> str:
    """Retrieve a Joplin note by ID.

    Args:
        note_id: Note ID
    """
    await ensure_joplin_pool()
    pool = get_pool("joplin")

    async with pool.connection() as conn:
        row = await conn.fetchrow(
            "SELECT content, created_time, updated_time FROM items WHERE id = $1 AND jop_type = 1",
            note_id,
        )
        if not row:
            return f"Note not found: {note_id}"

        data = _parse_note(row["content"])
        title = data.get("title", note_id)
        body = data.get("body", "")

    return (
        f"# {title}\n\n{body}\n\n"
        f"---\nCreated: {_iso_str(row['created_time'])}\nUpdated: {_iso_str(row['updated_time'])}"
    )


@tool
async def joplin_search_notes(query: str, limit: int = 10) -> str:
    """Search Joplin notes by keyword.

    Args:
        query: Search query
        limit: Max results (default 10)
    """
    await ensure_joplin_pool()
    pool = get_pool("joplin")

    async with pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, content, updated_time FROM (
                SELECT id, content, updated_time
                FROM items
                WHERE jop_type = 1
                  AND convert_from(content, 'UTF8') LIKE '{%'
            ) t
            WHERE (convert_from(content, 'UTF8')::jsonb->>'deleted_time')::bigint = 0
              AND (
                (convert_from(content, 'UTF8')::jsonb->>'title') ILIKE $1
                OR (convert_from(content, 'UTF8')::jsonb->>'body') ILIKE $1
              )
            ORDER BY updated_time DESC
            LIMIT $2
            """,
            f"%{query}%",
            limit,
        )

    if not rows:
        return f"No notes found for: {query}"

    parts = []
    for row in rows:
        data = _parse_note(row["content"])
        title = data.get("title", row["id"])
        snippet = data.get("body", "")[:300]
        parts.append(f"## {title}\n`{row['id']}`\n{snippet}...")

    return f"Found {len(rows)} notes:\n\n" + "\n\n---\n\n".join(parts)


@tool
async def joplin_list_notebooks() -> str:
    """List all Joplin notebooks (folders)."""
    await ensure_joplin_pool()
    pool = get_pool("joplin")

    async with pool.connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, content, created_time FROM (
                SELECT id, content, created_time
                FROM items
                WHERE jop_type = 2
                  AND convert_from(content, 'UTF8') LIKE '{%'
            ) t
            WHERE (convert_from(content, 'UTF8')::jsonb->>'deleted_time')::bigint = 0
            ORDER BY created_time
            """
        )

    if not rows:
        return "No notebooks found."

    parts = []
    for row in rows:
        data = _parse_note(row["content"])
        title = data.get("title", row["id"])
        parts.append(f"- **{title}**\n  ID: `{row['id']}`")

    return "## Notebooks\n\n" + "\n".join(parts)


@tool
async def joplin_list_tags() -> str:
    """List all tags in Joplin Server."""
    await ensure_joplin_pool()
    pool = get_pool("joplin")

    async with pool.connection() as conn:
        rows = await conn.fetch(
            "SELECT id, name FROM items WHERE jop_type = 17 ORDER BY name"
        )

    if not rows:
        return "No tags found."

    parts = []
    for row in rows:
        parts.append(f"- {row['name']} (ID: `{row['id']}`)")

    return "## Tags\n\n" + "\n".join(parts)


@tool
async def joplin_get_tags_for_note(note_id: str) -> str:
    """Get all tags associated with a Joplin note.

    Args:
        note_id: Note ID
    """
    await ensure_joplin_pool()
    pool = get_pool("joplin")

    async with pool.connection() as conn:
        row = await conn.fetchrow(
            "SELECT id, content FROM items WHERE id = $1 AND jop_type = 1", note_id
        )
        if not row:
            return f"Note not found: {note_id}"

        tag_rows = await conn.fetch(
            """
            SELECT t.id, t.name
            FROM items t
            WHERE t.jop_type = 17
              AND t.id IN (
                SELECT link.jop_parent_id
                FROM items link
                WHERE link.jop_type = 6
                  AND convert_from(link.content, 'UTF8') LIKE '%' || $1 || '%'
              )
            ORDER BY t.name
            """,
            note_id,
        )

        if not tag_rows:
            data = _parse_note(row["content"])
            body = data.get("body", "")
            tag_names = set(re.findall(r'(?:^|\s)#([a-zA-Z][\w\-]*)', body))
            if tag_names:
                tag_rows = await conn.fetch(
                    "SELECT id, name FROM items WHERE jop_type = 17 AND name = ANY($1) ORDER BY name",
                    list(tag_names),
                )

    if not tag_rows:
        return f"No tags found for note: {note_id}"

    parts = []
    for tr in tag_rows:
        parts.append(f"- {tr['name']} (ID: `{tr['id']}`)")

    return f"Tags for note {note_id}:\n" + "\n".join(parts)


@tool
async def joplin_upload_resource(
    filename: str,
    content_b64: str,
    note_id: str = "",
    mime_type: str = "",
) -> str:
    """Upload a file (image, PDF, code) to Joplin and optionally attach to a note.

    Args:
        filename: Filename
        content_b64: Base64-encoded file content
        note_id: Note ID to attach resource to (optional)
        mime_type: MIME type (auto-detected if not provided)
    """
    await ensure_joplin_pool()
    pool = get_pool("joplin")

    # Auto-detect MIME from extension
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "svg": "image/svg+xml",
        "webp": "image/webp",
        "pdf": "application/pdf",
        "py": "text/x-python",
        "js": "text/javascript",
        "ts": "text/typescript",
        "md": "text/markdown",
        "txt": "text/plain",
        "html": "text/html",
        "csv": "text/csv",
        "json": "application/json",
    }
    if ext in mime_map:
        mime_type = mime_map[ext]
    elif not mime_type:
        mime_type = "application/octet-stream"

    resource_id = uuid.uuid4().hex
    now = _iso_ms()
    content_bytes = base64.b64decode(content_b64)

    async with pool.connection() as conn:
        owner_id = DEFAULT_OWNER_ID or await _get_owner_id(conn)

        await conn.execute(
            """
            INSERT INTO items (
                id, name, mime_type, updated_time, created_time,
                content, content_size, jop_id, jop_parent_id,
                jop_share_id, jop_type, jop_encryption_applied,
                jop_updated_time, owner_id, content_storage_id
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            """,
            resource_id, filename, mime_type, now, now,
            content_bytes, len(content_bytes),
            resource_id, "", "",
            4, 0, now, owner_id, 1,
        )

        result = f"Resource uploaded: **{filename}**\nResource ID: `{resource_id}`\nMIME: {mime_type}"

        # If attaching to a note, append a resource link to the note's body
        if note_id:
            note_row = await conn.fetchrow(
                "SELECT content FROM items WHERE id = $1 AND jop_type = 1", note_id
            )
            if note_row:
                data = _parse_note(note_row["content"])
                link = f"\n\n![{filename}](:/{resource_id})"
                data["body"] = data.get("body", "") + link
                data["user_updated_time"] = now
                new_bytes = json.dumps(data).encode("utf-8")
                await conn.execute(
                    "UPDATE items SET content = $1, content_size = $2, updated_time = $3, jop_updated_time = $3 WHERE id = $4",
                    new_bytes, len(new_bytes), now, note_id,
                )
                result += f"\nAttached to note: `{note_id}`"

    return result


@tool
async def joplin_ping() -> str:
    """Health check — returns 'ok' and PostgreSQL connection status for the Joplin DB."""
    await ensure_joplin_pool()
    pool = get_pool("joplin")

    async with pool.connection() as conn:
        try:
            await conn.fetchval("SELECT 1")
            return "ok\nDatabase: connected"
        except Exception as e:
            return f"ok\nDatabase: error — {e}"