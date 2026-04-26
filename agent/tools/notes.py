import os
import psycopg
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool

from .embed import get_embedding


@tool
async def save_note(title: str, content: str) -> str:
    """Save a research note, summary, or conclusion to the personal knowledge base.

    Notes are stored as source='user_notes' and are searchable in future sessions
    via kb_search(source='user_notes'). Use this to persist important findings,
    research summaries, or conclusions the user wants to remember across conversations.

    Args:
        title: Short descriptive title for the note
        content: The note content (can be long — will be chunked if necessary)
    """
    if not title.strip() or not content.strip():
        return "Both title and content are required."

    try:
        embedding = await get_embedding(f"{title}\n\n{content}")
    except Exception as e:
        return f"[Embedding unavailable: {e}. Note not saved.]"

    db_url = os.environ["DATABASE_URL"]
    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).isoformat()
    source_id = f"{title}::{timestamp}"

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await register_vector_async(conn)
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO knowledge_chunks
                        (source, source_id, chunk_index, content, metadata, embedding, embedding_model)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source, source_id, chunk_index)
                    DO UPDATE SET
                        content         = EXCLUDED.content,
                        embedding       = EXCLUDED.embedding,
                        embedding_model = EXCLUDED.embedding_model,
                        updated_at      = NOW()
                    """,
                    (
                        "user_notes",
                        source_id,
                        f"{title}\n\n{content}",
                        psycopg.types.json.Jsonb({"title": title, "saved_at": timestamp}),
                        embedding,
                        "mxbai-embed-large",
                    ),
                )
    except Exception as e:
        return f"[Database error saving note: {e}]"

    return f"Note saved: '{title}' — searchable via kb_search(source='user_notes')."


@tool
async def list_documents() -> str:
    """List all user-uploaded PDFs and saved notes in the personal knowledge base.

    Returns filenames, page counts, chunk counts, and upload dates for all
    user_docs and user_notes entries.
    """
    db_url = os.environ["DATABASE_URL"]
    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            rows = await (await conn.execute(
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
            )).fetchall()
    except Exception as e:
        return f"[Database error: {e}]"

    if not rows:
        return "No personal documents or notes in the knowledge base yet.\n" \
               "Upload a PDF via POST /upload/pdf, or ask me to save_note."

    docs = [r for r in rows if r[0] == "user_docs"]
    notes = [r for r in rows if r[0] == "user_notes"]

    parts = []

    if docs:
        parts.append("**Uploaded Documents:**")
        for _, source_id, chunks, metadata, added in docs:
            meta = metadata or {}
            filename = meta.get("filename", source_id.split("::")[0])
            pages = meta.get("pages", "?")
            parts.append(f"  - {filename} — {pages} pages, {chunks} chunks (added {added})")

    if notes:
        parts.append("\n**Saved Notes:**")
        for _, source_id, chunks, metadata, added in notes:
            meta = metadata or {}
            title = meta.get("title", source_id.split("::")[0])
            parts.append(f"  - {title} (saved {added})")

    return "\n".join(parts)
