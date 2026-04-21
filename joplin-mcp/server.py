#!/usr/bin/env python3
"""
Joplin MCP Server — exposes Joplin Server operations as MCP tools.

Tools:
  - joplin_create_note: Create a note with title, content, notebook, tags
  - joplin_update_note: Update an existing note
  - joplin_edit_note: Precision edit (find/replace, append, prepend)
  - joplin_delete_note: Soft-delete a note
  - joplin_search_notes: Full-text search notes
  - joplin_list_notebooks: List available notebooks/folders
  - joplin_get_note: Retrieve a note by ID
  - joplin_list_tags: List all tags
  - joplin_get_tags_for_note: Get tags associated with a note
  - joplin_upload_resource: Upload a file (image, PDF, code) and attach to a note
  - joplin_ping: Health check (ok + DB status)

Runs as an HTTP SSE endpoint for MCP clients, or stdio for local tools.
"""

import asyncio
import hashlib
import json
import re
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

import asyncpg
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Database connection ──────────────────────────────────────────────────────
DB_HOST = os.environ.get("JOPLIN_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("JOPLIN_DB_PORT", "5432"))
DB_NAME = os.environ.get("JOPLIN_DB_NAME", "joplin")
DB_USER = os.environ.get("JOPLIN_DB_USER", "agent")
DB_PASSWORD = os.environ.get("JOPLIN_DB_PASSWORD", "")

# Default owner ID (from Joplin Server admin user)
DEFAULT_OWNER_ID = os.environ.get("JOPLIN_OWNER_ID", "")


def _iso_ms() -> int:
    return int(time.time() * 1000)


def _iso_str(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3]
        + "Z"
    )


@asynccontextmanager
async def get_db():
    if not DB_PASSWORD:
        raise RuntimeError("JOPLIN_DB_PASSWORD must be set")
    conn = await asyncpg.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    try:
        yield conn
    finally:
        await conn.close()


# ── MCP Server ───────────────────────────────────────────────────────────────
server = Server("joplin-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="joplin_create_note",
            description="Create a new note in Joplin Server. Returns the note ID and a joplin:// deep-link URI.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Note title"},
                    "content": {"type": "string", "description": "Markdown body"},
                    "notebook_id": {
                        "type": "string",
                        "description": "Parent folder ID (from joplin_list_notebooks)",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags to apply",
                    },
                },
                "required": ["title", "content"],
            },
        ),
        Tool(
            name="joplin_update_note",
            description="Update an existing note by ID. Provide title and/or content to update.",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "Note ID to update"},
                    "title": {"type": "string", "description": "New title (optional)"},
                    "content": {
                        "type": "string",
                        "description": "New content (optional)",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "If true, append to existing content instead of replacing",
                    },
                },
                "required": ["note_id"],
            },
        ),
        Tool(
            name="joplin_get_note",
            description="Retrieve a note by ID, returning title, content, and metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "Note ID"},
                },
                "required": ["note_id"],
            },
        ),
        Tool(
            name="joplin_search_notes",
            description="Search notes by keyword. Returns matching notes with snippets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {
                        "type": "integer",
                        "description": "Max results (default 10)",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="joplin_create_notebook",
            description="Create a new notebook (folder) in Joplin Server. Always prefix agent-created notebooks with 'LLM Generated - '.",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Notebook name — prefix with 'LLM Generated - ' for agent-created notebooks",
                    },
                    "parent_notebook_id": {
                        "type": "string",
                        "description": "Parent notebook ID for nested notebooks (optional)",
                    },
                },
                "required": ["title"],
            },
        ),
        Tool(
            name="joplin_list_notebooks",
            description="List all notebooks (folders) available in Joplin Server.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="joplin_upload_resource",
            description="Upload a file (image, PDF, code) and optionally attach it to a note.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Filename"},
                    "content_b64": {
                        "type": "string",
                        "description": "Base64-encoded file content",
                    },
                    "note_id": {
                        "type": "string",
                        "description": "Note ID to attach resource to (optional)",
                    },
                    "mime_type": {
                        "type": "string",
                        "description": "MIME type (auto-detected if not provided)",
                    },
                },
                "required": ["filename", "content_b64"],
            },
        ),
        Tool(
            name="joplin_edit_note",
            description="Precision edit a note: find/replace, append, or prepend content without replacing the entire note.",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "Note ID to edit"},
                    "find": {"type": "string", "description": "Text to find (or regex pattern if regex=true)"},
                    "replace": {"type": "string", "description": "Replacement text for find/replace"},
                    "append": {"type": "string", "description": "Text to append to the end of the note body"},
                    "prepend": {"type": "string", "description": "Text to prepend to the beginning of the note body"},
                    "regex": {"type": "boolean", "description": "Treat find as a regex pattern (default false)"},
                },
                "required": ["note_id"],
            },
        ),
        Tool(
            name="joplin_delete_note",
            description="Soft-delete a note by setting its deleted_time. The note can be recovered later.",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "Note ID to delete"},
                },
                "required": ["note_id"],
            },
        ),
        Tool(
            name="joplin_list_tags",
            description="List all tags in Joplin Server.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="joplin_get_tags_for_note",
            description="Get all tags associated with a note.",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {"type": "string", "description": "Note ID"},
                },
                "required": ["note_id"],
            },
        ),
        Tool(
            name="joplin_ping",
            description="Health check — returns 'ok' and PostgreSQL connection status.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    async with get_db() as db:
        if name == "joplin_create_note":
            return await _create_note(db, arguments)
        elif name == "joplin_update_note":
            return await _update_note(db, arguments)
        elif name == "joplin_get_note":
            return await _get_note(db, arguments)
        elif name == "joplin_search_notes":
            return await _search_notes(db, arguments)
        elif name == "joplin_create_notebook":
            return await _create_notebook(db, arguments)
        elif name == "joplin_list_notebooks":
            return await _list_notebooks(db)
        elif name == "joplin_upload_resource":
            return await _upload_resource(db, arguments)
        elif name == "joplin_edit_note":
            return await _edit_note(db, arguments)
        elif name == "joplin_delete_note":
            return await _delete_note(db, arguments)
        elif name == "joplin_list_tags":
            return await _list_tags(db)
        elif name == "joplin_get_tags_for_note":
            return await _get_tags_for_note(db, arguments)
        elif name == "joplin_ping":
            return await _ping(db)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ── Content helpers ───────────────────────────────────────────────────────────


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


async def _write_change(db, item_id: str, item_name: str, jop_type: int,
                        owner_id: str, now: int, change_type: int = 1):
    """Write a sync change record so the desktop app picks up the item."""
    change_id = uuid.uuid4().hex
    await db.execute(
        """
        INSERT INTO changes (id, item_type, item_id, item_name, type,
                             updated_time, created_time, previous_item, user_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (id) DO NOTHING
        """,
        change_id, jop_type, item_id, item_name, change_type,
        now, now, "", owner_id,
    )


async def _insert_item(db, item_id: str, name: str, mime: str, content_bytes: bytes,
                       jop_type: int, jop_parent_id: str, owner_id: str, now: int):
    await db.execute(
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
    await db.execute(
        "INSERT INTO user_items (user_id, item_id, updated_time, created_time) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
        owner_id, item_id, now, now,
    )
    await _write_change(db, item_id, name, jop_type, owner_id, now, change_type=1)


# ── Tool implementations ─────────────────────────────────────────────────────


async def _create_note(db, args: dict) -> list[TextContent]:
    title = args["title"]
    body = args.get("content", "")
    notebook_id = args.get("notebook_id", "")
    tags = args.get("tags", [])

    note_id = uuid.uuid4().hex
    now = _iso_ms()
    owner_id = DEFAULT_OWNER_ID or await _get_owner_id(db)

    content_bytes = _note_json(title, body, notebook_id, now)
    await _insert_item(db, note_id, f"{note_id}.md", "application/json",
                       content_bytes, 1, notebook_id, owner_id, now)

    uri = f"joplin://x-callback-url/openNote?id={note_id}"
    result = f"Note created: **{title}**\nNote ID: `{note_id}`\nOpen in Joplin: {uri}"

    if tags:
        for tag in tags:
            await _add_tag_to_note(db, note_id, tag, owner_id, now)
        result += f"\nTags: {', '.join(tags)}"

    return [TextContent(type="text", text=result)]


async def _update_note(db, args: dict) -> list[TextContent]:
    note_id = args["note_id"]
    new_title = args.get("title")
    new_body = args.get("content")
    append = args.get("append", False)

    now = _iso_ms()

    row = await db.fetchrow(
        "SELECT content FROM items WHERE id = $1 AND jop_type = 1", note_id
    )
    if not row:
        return [TextContent(type="text", text=f"Note not found: {note_id}")]

    data = _parse_note(row["content"])
    title = new_title or data.get("title", "")
    existing_body = data.get("body", "")
    body = (existing_body + "\n\n" + new_body) if append and new_body else (new_body if new_body is not None else existing_body)

    data.update({"title": title, "body": body, "user_updated_time": now})
    content_bytes = json.dumps(data).encode("utf-8")

    await db.execute(
        "UPDATE items SET content = $1, content_size = $2, updated_time = $3, jop_updated_time = $4 WHERE id = $5",
        content_bytes, len(content_bytes), now, now, note_id,
    )
    owner_id = DEFAULT_OWNER_ID or await _get_owner_id(db)
    await _write_change(db, note_id, f"{note_id}.md", 1, owner_id, now, change_type=1)

    uri = f"joplin://x-callback-url/openNote?id={note_id}"
    return [TextContent(type="text", text=f"Note updated: **{title}**\nOpen in Joplin: {uri}")]


async def _get_note(db, args: dict) -> list[TextContent]:
    note_id = args["note_id"]

    row = await db.fetchrow(
        "SELECT content, created_time, updated_time FROM items WHERE id = $1 AND jop_type = 1",
        note_id,
    )
    if not row:
        return [TextContent(type="text", text=f"Note not found: {note_id}")]

    data = _parse_note(row["content"])
    title = data.get("title", note_id)
    body = data.get("body", "")

    return [TextContent(type="text", text=(
        f"# {title}\n\n{body}\n\n"
        f"---\nCreated: {_iso_str(row['created_time'])}\nUpdated: {_iso_str(row['updated_time'])}"
    ))]


async def _search_notes(db, args: dict) -> list[TextContent]:
    query = args["query"]
    limit = args.get("limit", 10)

    rows = await db.fetch(
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
        return [TextContent(type="text", text=f"No notes found for: {query}")]

    parts = []
    for row in rows:
        data = _parse_note(row["content"])
        title = data.get("title", row["id"])
        snippet = data.get("body", "")[:300]
        parts.append(f"## {title}\n`{row['id']}`\n{snippet}...")

    return [TextContent(type="text", text=f"Found {len(rows)} notes:\n\n" + "\n\n---\n\n".join(parts))]


async def _create_notebook(db, args: dict) -> list[TextContent]:
    title = args["title"]
    parent_id = args.get("parent_notebook_id", "")

    folder_id = uuid.uuid4().hex
    now = _iso_ms()
    owner_id = DEFAULT_OWNER_ID or await _get_owner_id(db)

    content_bytes = _folder_json(title, now)
    await _insert_item(db, folder_id, f"{folder_id}.md", "application/json",
                       content_bytes, 2, parent_id, owner_id, now)

    result = (
        f"Notebook created: **{title}**\n"
        f"Notebook ID: `{folder_id}`\n\n"
        f"*Pass this ID as `notebook_id` when calling joplin_create_note.*"
    )
    return [TextContent(type="text", text=result)]


async def _list_notebooks(db) -> list[TextContent]:
    rows = await db.fetch(
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
        return [TextContent(type="text", text="No notebooks found.")]

    parts = []
    for row in rows:
        data = _parse_note(row["content"])
        title = data.get("title", row["id"])
        parts.append(f"- **{title}**\n  ID: `{row['id']}`")

    return [TextContent(type="text", text="## Notebooks\n\n" + "\n".join(parts))]


async def _upload_resource(db, args: dict) -> list[TextContent]:
    import base64

    filename = args["filename"]
    content_b64 = args["content_b64"]
    note_id = args.get("note_id", "")
    mime_type = args.get("mime_type", "application/octet-stream")

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

    resource_id = uuid.uuid4().hex
    now = _iso_ms()
    owner_id = DEFAULT_OWNER_ID or await _get_owner_id(db)

    content_bytes = base64.b64decode(content_b64)

    # Insert resource as an item (type 4 = resource)
    await db.execute(
        """
        INSERT INTO items (
            id, name, mime_type, updated_time, created_time,
            content, content_size, jop_id, jop_parent_id,
            jop_share_id, jop_type, jop_encryption_applied,
            jop_updated_time, owner_id, content_storage_id
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        """,
        resource_id,
        filename,
        mime_type,
        now,
        now,
        content_bytes,
        len(content_bytes),
        resource_id,
        "",
        "",
        4,  # type = resource
        0,
        now,
        owner_id,
        1,
    )

    result = f"Resource uploaded: **{filename}**\nResource ID: `{resource_id}`\nMIME: {mime_type}"

    # If attaching to a note, append a resource link to the note's body
    if note_id:
        note_row = await db.fetchrow(
            "SELECT content FROM items WHERE id = $1 AND jop_type = 1", note_id
        )
        if note_row:
            data = _parse_note(note_row["content"])
            link = f"\n\n![{filename}](:/{resource_id})"
            data["body"] = data.get("body", "") + link
            data["user_updated_time"] = now
            new_bytes = json.dumps(data).encode("utf-8")
            await db.execute(
                "UPDATE items SET content = $1, content_size = $2, updated_time = $3, jop_updated_time = $3 WHERE id = $4",
                new_bytes, len(new_bytes), now, note_id,
            )
            result += f"\nAttached to note: `{note_id}`"

    return [TextContent(type="text", text=result)]


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_owner_id(db) -> str:
    """Get the default owner ID from the users table."""
    row = await db.fetchrow(
        "SELECT id FROM users WHERE email = $1 LIMIT 1", "admin@pi-agent.local"
    )
    if row:
        return row["id"]
    row = await db.fetchrow("SELECT id FROM users LIMIT 1")
    return row["id"] if row else ""


async def _add_tag_to_note(db, note_id: str, tag_name: str, owner_id: str, now: int):
    """Add a tag to a note."""
    # Find or create tag
    tag = await db.fetchrow(
        "SELECT id FROM items WHERE jop_type = 17 AND name = $1 AND owner_id = $2",
        tag_name,
        owner_id,
    )
    if not tag:
        tag_id = uuid.uuid4().hex
        await db.execute(
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

    # Link tag to note via changes table
    await db.execute(
        """
        INSERT INTO changes (item_type, item_id, type, created_time, source, user_updated_time)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        1,
        note_id,
        3,
        now,
        "joplin-mcp",
        now,
    )


async def _edit_note(db, args: dict) -> list[TextContent]:
    note_id = args["note_id"]
    find_text = args.get("find")
    replace_text = args.get("replace", "")
    append_text = args.get("append")
    prepend_text = args.get("prepend")
    use_regex = args.get("regex", False)

    now = _iso_ms()

    row = await db.fetchrow(
        "SELECT content FROM items WHERE id = $1 AND jop_type = 1", note_id
    )
    if not row:
        return [TextContent(type="text", text=f"Note not found: {note_id}")]

    data = _parse_note(row["content"])
    title = data.get("title", "")
    body = data.get("body", "")

    modified = False

    if find_text is not None:
        if use_regex:
            try:
                body = re.sub(find_text, replace_text, body)
            except re.error as e:
                return [TextContent(type="text", text=f"Invalid regex pattern: {e}")]
        else:
            body = body.replace(find_text, replace_text)
        modified = True

    if append_text is not None:
        body = (body + "\n\n" + append_text) if body else append_text
        modified = True

    if prepend_text is not None:
        body = (prepend_text + "\n\n" + body) if body else prepend_text
        modified = True

    if not modified:
        return [TextContent(type="text", text=f"No changes specified for note: {note_id}")]

    data.update({"body": body, "user_updated_time": now})
    content_bytes = json.dumps(data).encode("utf-8")

    await db.execute(
        "UPDATE items SET content = $1, content_size = $2, updated_time = $3, jop_updated_time = $4 WHERE id = $5",
        content_bytes, len(content_bytes), now, now, note_id,
    )
    owner_id = DEFAULT_OWNER_ID or await _get_owner_id(db)
    await _write_change(db, note_id, f"{note_id}.md", 1, owner_id, now, change_type=1)

    uri = f"joplin://x-callback-url/openNote?id={note_id}"
    return [TextContent(type="text", text=f"Note edited: **{title}**\nOpen in Joplin: {uri}")]


async def _delete_note(db, args: dict) -> list[TextContent]:
    note_id = args["note_id"]
    now = _iso_ms()

    row = await db.fetchrow(
        "SELECT content FROM items WHERE id = $1 AND jop_type = 1", note_id
    )
    if not row:
        return [TextContent(type="text", text=f"Note not found: {note_id}")]

    data = _parse_note(row["content"])
    title = data.get("title", note_id)
    data["deleted_time"] = now
    data["user_updated_time"] = now

    content_bytes = json.dumps(data).encode("utf-8")
    await db.execute(
        "UPDATE items SET content = $1, content_size = $2, updated_time = $3, jop_updated_time = $4 WHERE id = $5",
        content_bytes, len(content_bytes), now, now, note_id,
    )
    owner_id = DEFAULT_OWNER_ID or await _get_owner_id(db)
    await _write_change(db, note_id, f"{note_id}.md", 1, owner_id, now, change_type=1)

    return [TextContent(type="text", text=f"Note deleted: **{title}** (ID: `{note_id}`)")]


async def _list_tags(db) -> list[TextContent]:
    rows = await db.fetch(
        "SELECT id, name FROM items WHERE jop_type = 17 ORDER BY name"
    )

    if not rows:
        return [TextContent(type="text", text="No tags found.")]

    parts = []
    for row in rows:
        parts.append(f"- {row['name']} (ID: `{row['id']}`)")

    return [TextContent(type="text", text="## Tags\n\n" + "\n".join(parts))]


async def _get_tags_for_note(db, args: dict) -> list[TextContent]:
    note_id = args["note_id"]

    row = await db.fetchrow(
        "SELECT id, content FROM items WHERE id = $1 AND jop_type = 1", note_id
    )
    if not row:
        return [TextContent(type="text", text=f"Note not found: {note_id}")]

    tag_rows = await db.fetch(
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
            tag_rows = await db.fetch(
                "SELECT id, name FROM items WHERE jop_type = 17 AND name = ANY($1) ORDER BY name",
                list(tag_names),
            )

    if not tag_rows:
        return [TextContent(type="text", text=f"No tags found for note: {note_id}")]

    parts = []
    for tr in tag_rows:
        parts.append(f"- {tr['name']} (ID: `{tr['id']}`)")

    return [TextContent(type="text", text=f"Tags for note {note_id}:\n" + "\n".join(parts))]


async def _ping(db) -> list[TextContent]:
    try:
        await db.fetchval("SELECT 1")
        return [TextContent(type="text", text="ok\nDatabase: connected")]
    except Exception as e:
        return [TextContent(type="text", text=f"ok\nDatabase: error — {e}")]


# ── Server startup ───────────────────────────────────────────────────────────


async def main():
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount, Route
    from starlette.responses import JSONResponse
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await server.run(
                streams[0], streams[1], server.create_initialization_options()
            )

    async def handle_rest_tool_call(request):
        """REST endpoint for direct tool calls (bypasses SSE/MCP protocol)."""
        try:
            body = await request.json()
            tool_name = body.get("tool", "")
            arguments = body.get("arguments", {})

            async with get_db() as db:
                if tool_name == "joplin_create_note":
                    result = await _create_note(db, arguments)
                elif tool_name == "joplin_update_note":
                    result = await _update_note(db, arguments)
                elif tool_name == "joplin_get_note":
                    result = await _get_note(db, arguments)
                elif tool_name == "joplin_search_notes":
                    result = await _search_notes(db, arguments)
                elif tool_name == "joplin_create_notebook":
                    result = await _create_notebook(db, arguments)
                elif tool_name == "joplin_list_notebooks":
                    result = await _list_notebooks(db)
                elif tool_name == "joplin_upload_resource":
                    result = await _upload_resource(db, arguments)
                elif tool_name == "joplin_edit_note":
                    result = await _edit_note(db, arguments)
                elif tool_name == "joplin_delete_note":
                    result = await _delete_note(db, arguments)
                elif tool_name == "joplin_list_tags":
                    result = await _list_tags(db)
                elif tool_name == "joplin_get_tags_for_note":
                    result = await _get_tags_for_note(db, arguments)
                elif tool_name == "joplin_ping":
                    result = await _ping(db)
                else:
                    return JSONResponse(
                        {"error": f"Unknown tool: {tool_name}"},
                        status_code=400,
                    )

            return JSONResponse({"result": result[0].text if result else ""})

        except Exception as e:
            logger.exception(f"REST tool call failed: {e}")
            return JSONResponse(
                {"error": str(e)},
                status_code=500,
            )

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
            Route(
                "/tools/{tool_name}", endpoint=handle_rest_tool_call, methods=["POST"]
            ),
        ]
    )

    config = uvicorn.Config(app, host="0.0.0.0", port=8090)
    server_uvicorn = uvicorn.Server(config)
    logger.info("Joplin MCP server starting on port 8090")
    await server_uvicorn.serve()


if __name__ == "__main__":
    import uvicorn

    asyncio.run(main())
