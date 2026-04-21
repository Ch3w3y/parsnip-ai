import os
import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool

from .embed import get_embedding


@tool
async def timeline(query: str, source: str | None = None, limit: int = 20) -> str:
    """Search the knowledge base and return results sorted chronologically.

    Unlike kb_search (which ranks by relevance), timeline returns matching documents
    ordered by publication/ingestion date — useful for tracking how a topic evolved
    over time, finding the latest coverage, or spotting when a story broke.

    Args:
        query: The topic to search for
        source: Optional source filter ('wikipedia', 'arxiv', 'news', 'biorxiv', 'user_docs', 'user_notes')
        limit: Number of results (default 20, max 40)
    """
    limit = min(int(limit), 40)

    try:
        embedding = await get_embedding(query)
    except Exception as e:
        return f"[Embedding unavailable: {e}]"

    source_filter = sql.SQL("AND source = {src}").format(src=sql.Literal(source)) if source else sql.SQL("")
    db_url = os.environ["DATABASE_URL"]

    # Hybrid: combine vector candidates + FTS candidates, deduplicate per article,
    # then sort the merged set by date rather than by relevance score.
    q = sql.SQL("""
        WITH vector_hits AS (
            SELECT source_id, chunk_index, content, metadata, source, created_at
            FROM knowledge_chunks
            WHERE embedding IS NOT NULL
              {src_filter}
            ORDER BY embedding <=> {emb}::vector
            LIMIT 200
        ),
        fts_hits AS (
            SELECT source_id, chunk_index, content, metadata, source, created_at
            FROM knowledge_chunks
            WHERE to_tsvector('english', content) @@ plainto_tsquery('english', {qt})
              {src_filter}
            LIMIT 200
        ),
        candidates AS (
            SELECT * FROM vector_hits
            UNION
            SELECT * FROM fts_hits
        ),
        deduped AS (
            SELECT DISTINCT ON (source, source_id)
                source_id, content, metadata, source, created_at
            FROM candidates
            ORDER BY source, source_id, created_at DESC
        )
        SELECT source_id, content, metadata, source, created_at
        FROM deduped
        ORDER BY created_at DESC
        LIMIT {lim}
    """).format(
        emb=sql.Literal(embedding),
        qt=sql.Literal(query),
        src_filter=source_filter,
        lim=sql.Literal(limit),
    )

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await register_vector_async(conn)
            rows = await (await conn.execute(q)).fetchall()
    except Exception as e:
        return f"[Database error: {e}]"

    if not rows:
        return f"No results found for '{query}'."

    parts = []
    for source_id, content, metadata, src, created_at in rows:
        meta = metadata or {}
        url = meta.get("url", "")
        title = meta.get("title", source_id.split("::")[0])
        date_str = created_at.strftime("%Y-%m-%d") if created_at else "unknown date"
        label = f"[{date_str}] [{src}] **{title}**" + (f"\n{url}" if url else "")
        parts.append(f"{label}\n{content.strip()[:350]}{'…' if len(content) > 350 else ''}")

    header = f"*Timeline for '{query}' — {len(rows)} results, newest first*\n"
    return header + "\n\n---\n\n" + "\n\n---\n\n".join(parts)
