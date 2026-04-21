"""
Filtered search tool — KB search with explicit source/date/user filters.
"""

import os

import psycopg
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool

from .embed import get_embedding


@tool
async def search_with_filters(
    query: str,
    source: str = "",
    days: int = 0,
    user_id: str = "",
    limit: int = 10,
) -> str:
    """Search the knowledge base with explicit filters for source, recency, and user.

    Provides more control than holistic_search or kb_search by exposing
    source, days, and user_id filters directly. Use when you need precise
    control over which sources or time range to search.

    Args:
        query: Search query
        source: Filter by source type. One of: 'wikipedia', 'arxiv', 'news',
                'biorxiv', 'joplin_notes', 'user_docs', 'github', 'pubmed',
                'rss', 'ssrn', 'hackernews'. Leave empty for all sources.
        days: Only return results from the last N days (0 = all time)
        user_id: Filter by user ID (for Joplin notes only). Leave empty for all.
        limit: Max results (default 10)
    """
    limit = min(int(limit), 50)
    db_url = os.environ["DATABASE_URL"]
    query_embedding = await get_embedding(query)

    conditions = ["embedding IS NOT NULL"]
    params = []

    if source:
        conditions.append("source = %s")
        params.append(source)

    if days > 0:
        conditions.append("created_at >= NOW() - INTERVAL '%s days'")
        params.append(int(days))

    if user_id:
        conditions.append("user_id = %s")
        params.append(user_id)

    where = " AND ".join(conditions)

    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await register_vector_async(conn)
        query_params = [query_embedding, *params, query_embedding, limit]

        rows = await (await conn.execute(
            f"""
            SELECT id, source, source_id, chunk_index, content, metadata, created_at,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM knowledge_chunks
            WHERE {where}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            query_params,
        )).fetchall()

    if not rows:
        scope = f"source={source}" if source else "all sources"
        recency = f" (last {days} days)" if days > 0 else ""
        return f"No results found for '{query}' in {scope}{recency}."

    parts = [f"**Found {len(rows)} results** (source={source or 'all'}, days={days or 'all'})\n"]
    for row_id, src, src_id, chunk_idx, content, meta, created, sim in rows:
        title = meta.get("title", src_id) if isinstance(meta, dict) else src_id
        snippet = content[:300] + "..." if len(content) > 300 else content
        parts.append(f"### {title}\nSource: {src} | Similarity: {sim:.3f} | {created.strftime('%Y-%m-%d') if created else ''}\n{snippet}\n")

    return "\n---\n".join(parts)
