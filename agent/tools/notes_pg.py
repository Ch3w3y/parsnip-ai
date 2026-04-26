"""notes_pg — Unified note access layer on the normalized notes tables in agent_kb.

Replaces both joplin_pg.py (direct PG on Joplin's items table) and notes.py
(embedding-only user_notes in knowledge_chunks).  All 14 @tool functions
operate on the new normalized schema (notebooks, notes, tags, note_tags,
note_resources) in the main agent_kb database and auto-index into
knowledge_chunks for KB search.

Uses get_pool('agent_kb') via lazy import — no separate Joplin pool.
"""

import base64
import logging
import re
import uuid

from langchain_core.tools import tool
from pgvector.psycopg import register_vector_async
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)


def _local_get_pool(*args, **kwargs):
    from tools.db_pool import get_pool
    return get_pool(*args, **kwargs)


def _local_get_embedding(*args, **kwargs):
    from tools.embed import get_embedding
    return get_embedding(*args, **kwargs)


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _index_note(note_id: str, title: str, content: str, notebook_id: str = "") -> None:
    """Embed and upsert a note into knowledge_chunks (source='user_notes').

    Called after create / update / edit.  Failure to embed logs a warning
    but does NOT prevent the note from being saved.
    """
    pool = _local_get_pool("agent_kb")
    combined = f"{title}\n\n{content}"
    source_id = f"{note_id}::chunk_0"
    meta = {"note_id": str(note_id), "title": title}
    if notebook_id:
        meta["notebook_id"] = str(notebook_id)

    try:
        embedding = await _local_get_embedding(combined)
    except Exception as exc:
        logger.warning("Embedding failed for note %s: %s", note_id, exc)
        return

    try:
        async with pool.connection() as conn:
            await register_vector_async(conn)
            await conn.execute(
                """
                INSERT INTO knowledge_chunks
                    (source, source_id, chunk_index, content, metadata, embedding, embedding_model)
                VALUES (%s, %s, 0, %s, %s, %s, %s)
                ON CONFLICT (source, source_id, chunk_index)
                DO UPDATE SET
                    content         = EXCLUDED.content,
                    metadata        = EXCLUDED.metadata,
                    embedding       = EXCLUDED.embedding,
                    embedding_model = EXCLUDED.embedding_model,
                    updated_at      = NOW()
                """,
                ("user_notes", source_id, combined, Jsonb(meta), embedding, "mxbai-embed-large"),
            )
    except Exception as exc:
        logger.warning("KB index upsert failed for note %s: %s", note_id, exc)


async def _ensure_tag(conn, tag_name: str) -> uuid.UUID:
    """Return the tag_id for *tag_name*, creating the tag row if needed."""
    cur = await conn.execute(
        "SELECT id FROM tags WHERE name = %s",
        (tag_name,),
    )
    row = await cur.fetchone()
    if row:
        return row["id"]
    tag_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO tags (id, name) VALUES (%s, %s)",
        (tag_id, tag_name),
    )
    return tag_id


_MIME_MAP: dict[str, str] = {
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


def _detect_mime(filename: str, fallback: str = "") -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MIME_MAP.get(ext, fallback) or "application/octet-stream"


# ── 1. joplin_create_notebook ──────────────────────────────────────────────────


@tool
async def joplin_create_notebook(
    title: str,
    parent_notebook_id: str = "",
) -> str:
    """Create a new notebook (folder).

    Always prefix agent-created notebooks with "LLM Generated - " so they are
    visually distinct from the user's own notebooks.

    Args:
        title: Notebook name — prefix with "LLM Generated - " for agent-created notebooks
        parent_notebook_id: Parent notebook ID for nested notebooks (optional)
    """
    pool = _local_get_pool("agent_kb")
    nb_id = uuid.uuid4()

    parent_uuid: uuid.UUID | None = None
    if parent_notebook_id:
        try:
            parent_uuid = uuid.UUID(parent_notebook_id)
        except ValueError:
            return f"Invalid parent_notebook_id UUID: {parent_notebook_id}"

    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO notebooks (id, title, parent_id)
            VALUES (%s, %s, %s)
            """,
            (nb_id, title, parent_uuid),
        )

    return (
        f"Notebook created: **{title}**\n"
        f"Notebook ID: `{nb_id}`\n\n"
        f"*Pass this ID as `notebook_id` when calling joplin_create_note.*"
    )


# ── 2. joplin_create_note ──────────────────────────────────────────────────────


@tool
async def joplin_create_note(
    title: str,
    content: str,
    notebook_id: str = "",
    tags: list[str] | None = None,
) -> str:
    """Create a new note with title and Markdown body.

    The note is auto-indexed into knowledge_chunks (source='user_notes')
    so it is immediately searchable via kb_search.

    Args:
        title: Note title
        content: Markdown body
        notebook_id: Parent notebook ID (from joplin_list_notebooks)
        tags: Optional list of tags (created automatically if new)
    """
    pool = _local_get_pool("agent_kb")
    note_id = uuid.uuid4()

    nb_uuid: uuid.UUID | None = None
    if notebook_id:
        try:
            nb_uuid = uuid.UUID(notebook_id)
        except ValueError:
            return f"Invalid notebook_id UUID: {notebook_id}"

    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO notes (id, title, content, notebook_id)
            VALUES (%s, %s, %s, %s)
            """,
            (note_id, title, content, nb_uuid),
        )

        if tags:
            for tag_name in tags:
                tag_id = await _ensure_tag(conn, tag_name)
                await conn.execute(
                    "INSERT INTO note_tags (note_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (note_id, tag_id),
                )

    # Auto-index (best-effort; failure is logged, not raised)
    await _index_note(str(note_id), title, content, notebook_id)

    result = f"Note created: **{title}**\nNote ID: `{note_id}`"
    if tags:
        result += f"\nTags: {', '.join(tags)}"
    return result


# ── 3. joplin_update_note ──────────────────────────────────────────────────────


@tool
async def joplin_update_note(
    note_id: str,
    title: str = "",
    content: str = "",
    append: bool = False,
) -> str:
    """Update an existing note's title or content.

    If content is provided with append=True, the new content is appended
    to the existing body.  Re-indexes into knowledge_chunks when content
    changes.

    Args:
        note_id: Note ID to update
        title: New title (optional — leave empty to keep existing)
        content: New content (optional — leave empty to keep existing)
        append: If true, append to existing content instead of replacing
    """
    pool = _local_get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        return f"Invalid note_id UUID: {note_id}"

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT title, content, notebook_id FROM notes WHERE id = %s AND deleted_at IS NULL",
            (nid,),
        )
        row = await cur.fetchone()
        if not row:
            return f"Note not found or deleted: {note_id}"

        new_title = title or row["title"]
        existing_body = row["content"]
        if content:
            new_body = (existing_body + "\n\n" + content) if append else content
        else:
            new_body = existing_body

        await conn.execute(
            """
            UPDATE notes SET title = %s, content = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (new_title, new_body, nid),
        )

    # Re-index if content actually changed
    if content or title:
        await _index_note(note_id, new_title, new_body, str(row["notebook_id"] or ""))

    return f"Note updated: **{new_title}**\nNote ID: `{note_id}`"


# ── 4. joplin_edit_note ────────────────────────────────────────────────────────


@tool
async def joplin_edit_note(
    note_id: str,
    find: str = "",
    replace: str = "",
    append: str = "",
    prepend: str = "",
    regex: bool = False,
) -> str:
    """Precision edit a note: find/replace, append, or prepend content.

    Re-indexes into knowledge_chunks after any change.

    Args:
        note_id: Note ID to edit
        find: Text to find (or regex pattern if regex=True)
        replace: Replacement text for find/replace
        append: Text to append to the end of the note body
        prepend: Text to prepend to the beginning of the note body
        regex: Treat find as a regex pattern (default False)
    """
    pool = _local_get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        return f"Invalid note_id UUID: {note_id}"

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT title, content, notebook_id FROM notes WHERE id = %s AND deleted_at IS NULL",
            (nid,),
        )
        row = await cur.fetchone()
        if not row:
            return f"Note not found or deleted: {note_id}"

        title = row["title"]
        body = row["content"]
        modified = False

        if find:
            if regex:
                try:
                    body = re.sub(find, replace, body)
                except re.error as exc:
                    return f"Invalid regex pattern: {exc}"
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

        await conn.execute(
            "UPDATE notes SET content = %s, updated_at = NOW() WHERE id = %s",
            (body, nid),
        )

    # Re-index
    await _index_note(note_id, title, body, str(row["notebook_id"] or ""))

    return f"Note edited: **{title}**\nNote ID: `{note_id}`"


# ── 5. joplin_delete_note ──────────────────────────────────────────────────────


@tool
async def joplin_delete_note(note_id: str) -> str:
    """Soft-delete a note by setting deleted_at.

    Also marks any associated knowledge_chunks as updated so search
    indices can adjust.

    Args:
        note_id: Note ID to delete
    """
    pool = _local_get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        return f"Invalid note_id UUID: {note_id}"

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT title FROM notes WHERE id = %s AND deleted_at IS NULL",
            (nid,),
        )
        row = await cur.fetchone()
        if not row:
            return f"Note not found or already deleted: {note_id}"

        title = row["title"]

        await conn.execute(
            "UPDATE notes SET deleted_at = NOW(), updated_at = NOW() WHERE id = %s",
            (nid,),
        )

        # Mark knowledge_chunks rows so they can be excluded from search
        like_pattern = f"{note_id}::chunk_%"
        await conn.execute(
            """
            UPDATE knowledge_chunks
            SET updated_at = NOW()
            WHERE source = 'user_notes' AND source_id LIKE %s
            """,
            (like_pattern,),
        )

    return f"Note deleted: **{title}** (ID: `{note_id}`)"


# ── 6. joplin_get_note ─────────────────────────────────────────────────────────


@tool
async def joplin_get_note(note_id: str) -> str:
    """Retrieve a note by ID, including its tags.

    Returns formatted markdown with title, body, tags, and timestamps.

    Args:
        note_id: Note ID
    """
    pool = _local_get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        return f"Invalid note_id UUID: {note_id}"

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT n.title, n.content, n.notebook_id,
                   n.created_at, n.updated_at, n.source_url, n.author
            FROM notes n
            WHERE n.id = %s AND n.deleted_at IS NULL
            """,
            (nid,),
        )
        row = await cur.fetchone()
        if not row:
            return f"Note not found: {note_id}"

        cur = await conn.execute(
            """
            SELECT t.name
            FROM note_tags nt
            JOIN tags t ON t.id = nt.tag_id
            WHERE nt.note_id = %s
            ORDER BY t.name
            """,
            (nid,),
        )
        tag_rows = await cur.fetchall()

    title = row["title"]
    body = row["content"]
    tags_str = ", ".join(t["name"] for t in tag_rows) if tag_rows else "none"
    created = row["created_at"].isoformat() if row["created_at"] else "unknown"
    updated = row["updated_at"].isoformat() if row["updated_at"] else "unknown"

    result = f"# {title}\n\n{body}\n\n---\nTags: {tags_str}"
    if row["source_url"]:
        result += f"\nSource URL: {row['source_url']}"
    if row["author"]:
        result += f"\nAuthor: {row['author']}"
    if row["notebook_id"]:
        result += f"\nNotebook: `{row['notebook_id']}`"
    result += f"\nCreated: {created}\nUpdated: {updated}"

    return result


# ── 7. joplin_search_notes ─────────────────────────────────────────────────────


@tool
async def joplin_search_notes(query: str, limit: int = 10) -> str:
    """Search notes by keyword using ILIKE and full-text search.

    Excludes soft-deleted notes.  Returns formatted results with snippets.

    Args:
        query: Search query
        limit: Max results (default 10)
    """
    pool = _local_get_pool("agent_kb")
    ilike_pat = f"%{query}%"

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, title,
                   LEFT(content, 300) AS snippet,
                   updated_at
            FROM notes
            WHERE deleted_at IS NULL
              AND (
                  title ILIKE %s
                  OR content ILIKE %s
                  OR to_tsvector('english', content) @@ plainto_tsquery('english', %s)
              )
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (ilike_pat, ilike_pat, query, limit),
        )
        rows = await cur.fetchall()

    if not rows:
        return f"No notes found for: {query}"

    parts = []
    for row in rows:
        nid = row["id"]
        title = row["title"]
        snippet = row["snippet"] or ""
        parts.append(f"## {title}\n`{nid}`\n{snippet}...")

    return f"Found {len(rows)} notes:\n\n" + "\n\n---\n\n".join(parts)


# ── 8. joplin_list_notebooks ───────────────────────────────────────────────────


@tool
async def joplin_list_notebooks() -> str:
    """List all notebooks (folders).

    Only active notebooks are shown (no deleted_at support on the notebooks
    table yet — all rows are returned).
    """
    pool = _local_get_pool("agent_kb")

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT n.id, n.title, n.parent_id,
                   (SELECT COUNT(*) FROM notes WHERE notebook_id = n.id AND deleted_at IS NULL) AS note_count
            FROM notebooks n
            ORDER BY n.title
            """,
        )
        rows = await cur.fetchall()

    if not rows:
        return "No notebooks found."

    parts = []
    for row in rows:
        parent_str = f" (parent: `{row['parent_id']}`)" if row["parent_id"] else ""
        parts.append(
            f"- **{row['title']}** — {row['note_count']} notes\n  ID: `{row['id']}`{parent_str}"
        )

    return "## Notebooks\n\n" + "\n".join(parts)


# ── 9. joplin_list_tags ────────────────────────────────────────────────────────


@tool
async def joplin_list_tags() -> str:
    """List all tags ordered by name."""
    pool = _local_get_pool("agent_kb")

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT t.id, t.name,
                   (SELECT COUNT(*) FROM note_tags WHERE tag_id = t.id) AS usage_count
            FROM tags t
            ORDER BY t.name
            """,
        )
        rows = await cur.fetchall()

    if not rows:
        return "No tags found."

    parts = []
    for row in rows:
        parts.append(f"- {row['name']} ({row['usage_count']} notes) — ID: `{row['id']}`")

    return "## Tags\n\n" + "\n".join(parts)


# ── 10. joplin_get_tags_for_note ───────────────────────────────────────────────


@tool
async def joplin_get_tags_for_note(note_id: str) -> str:
    """Get all tags attached to a specific note.

    Args:
        note_id: Note ID
    """
    pool = _local_get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        return f"Invalid note_id UUID: {note_id}"

    async with pool.connection() as conn:
        # Verify note exists
        cur = await conn.execute(
            "SELECT id FROM notes WHERE id = %s AND deleted_at IS NULL",
            (nid,),
        )
        note = await cur.fetchone()
        if not note:
            return f"Note not found: {note_id}"

        cur = await conn.execute(
            """
            SELECT t.id, t.name
            FROM note_tags nt
            JOIN tags t ON t.id = nt.tag_id
            WHERE nt.note_id = %s
            ORDER BY t.name
            """,
            (nid,),
        )
        rows = await cur.fetchall()

    if not rows:
        return f"No tags found for note: {note_id}"

    parts = []
    for row in rows:
        parts.append(f"- {row['name']} (ID: `{row['id']}`)")

    return f"Tags for note {note_id}:\n" + "\n".join(parts)


# ── 11. joplin_upload_resource ─────────────────────────────────────────────────


@tool
async def joplin_upload_resource(
    filename: str,
    content_b64: str,
    note_id: str = "",
    mime_type: str = "",
) -> str:
    """Upload a binary resource (image, PDF, code) and optionally attach to a note.

    Args:
        filename: Filename (used for MIME auto-detection)
        content_b64: Base64-encoded file content
        note_id: Note ID to attach resource to (optional)
        mime_type: MIME type (auto-detected from filename if not provided)
    """
    pool = _local_get_pool("agent_kb")
    resource_id = uuid.uuid4()

    mime = mime_type or _detect_mime(filename, mime_type)
    raw = base64.b64decode(content_b64)

    note_uuid: uuid.UUID | None = None
    if note_id:
        try:
            note_uuid = uuid.UUID(note_id)
        except ValueError:
            return f"Invalid note_id UUID: {note_id}"

    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO note_resources (id, note_id, filename, mime_type, content, size)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (resource_id, note_uuid, filename, mime, raw, len(raw)),
        )

    result = (
        f"Resource uploaded: **{filename}**\n"
        f"Resource ID: `{resource_id}`\n"
        f"MIME: {mime}\nSize: {len(raw)} bytes"
    )
    if note_uuid:
        result += f"\nAttached to note: `{note_id}`"

    return result


# ── 12. joplin_ping ────────────────────────────────────────────────────────────


@tool
async def joplin_ping() -> str:
    """Health check — verify connectivity to the agent_kb database pool."""
    pool = _local_get_pool("agent_kb")

    async with pool.connection() as conn:
        try:
            cur = await conn.execute("SELECT 1")
            await cur.fetchone()
            return "ok\nDatabase: agent_kb connected"
        except Exception as exc:
            return f"ok\nDatabase: agent_kb error — {exc}"


# ── 13. save_note (convenience wrapper from notes.py) ──────────────────────────


@tool
async def save_note(title: str, content: str) -> str:
    """Save a research note, summary, or conclusion to the personal knowledge base.

    Notes are stored in the notes table and auto-indexed into knowledge_chunks
    with source='user_notes'.  They are searchable in future sessions via
    kb_search(source='user_notes').  Use this to persist important findings,
    research summaries, or conclusions the user wants to remember across
    conversations.

    Args:
        title: Short descriptive title for the note
        content: The note content (markdown)
    """
    if not title.strip() or not content.strip():
        return "Both title and content are required."

    # Delegate to joplin_create_note without a notebook
    return await joplin_create_note(
        title=title,
        content=content,
        notebook_id="",
        tags=None,
    )


# ── 14. list_documents (unified view from notes.py) ────────────────────────────


@tool
async def list_documents() -> str:
    """List all user-uploaded PDFs and saved notes in the personal knowledge base.

    Returns information from both knowledge_chunks (user_docs and user_notes
    sources) AND the notes table for a complete picture.
    """
    pool = _local_get_pool("agent_kb")

    try:
        async with pool.connection() as conn:
            # KB-sourced documents and notes
            cur = await conn.execute(
                """
                SELECT
                    source,
                    source_id,
                    COUNT(*)                                    AS chunks,
                    metadata,
                    MIN(created_at)::date                       AS added
                FROM knowledge_chunks
                WHERE source IN ('user_docs', 'user_notes')
                GROUP BY source, source_id, metadata
                ORDER BY source, MIN(created_at) DESC
                """
            )
            kb_rows = await cur.fetchall()

            # Notes table records (those not represented in KB yet)
            cur2 = await conn.execute(
                """
                SELECT id, title, created_at::date AS added
                FROM notes
                WHERE deleted_at IS NULL
                ORDER BY created_at DESC
                """
            )
            note_rows = await cur2.fetchall()
    except Exception as exc:
        return f"[Database error: {exc}]"

    parts = []

    # KB documents
    docs = [r for r in kb_rows if r["source"] == "user_docs"]
    if docs:
        parts.append("**Uploaded Documents:**")
        for row in docs:
            meta = row["metadata"] or {}
            filename = meta.get("filename", row["source_id"].split("::")[0])
            pages = meta.get("pages", "?")
            parts.append(f"  - {filename} — {pages} pages, {row['chunks']} chunks (added {row['added']})")

    # KB notes
    kb_notes = [r for r in kb_rows if r["source"] == "user_notes"]
    if kb_notes:
        parts.append("\n**Saved Notes (KB-indexed):**")
        for row in kb_notes:
            meta = row["metadata"] or {}
            title = meta.get("title", row["source_id"].split("::")[0])
            parts.append(f"  - {title} (saved {row['added']})")

    # Notes table records
    if note_rows:
        parts.append("\n**Notes (table):**")
        for row in note_rows:
            parts.append(f"  - {row['title']} — ID: `{row['id']}` (added {row['added']})")

    if not parts:
        return (
            "No personal documents or notes in the knowledge base yet.\n"
            "Upload a PDF via POST /upload/pdf, or ask me to save_note."
        )

    return "\n".join(parts)
