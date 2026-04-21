import os
import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool

from .embed import get_embedding

SOURCE_MODEL_MAP = {
    "github": "bge-m3",
}
DEFAULT_MODEL = "mxbai-embed-large"


@tool
async def kb_search(
    query: str,
    source: str | None = None,
    days: int | None = None,
    limit: int = 8,
) -> str:
    """Search the local knowledge base using hybrid semantic + full-text search (RRF).

    Covers all ingested sources: Wikipedia, arXiv, news, bioRxiv, GitHub, and uploaded documents.
    Use source and days filters to scope results when you know what type of content you need.

    Args:
        query: Natural language search query
        source: Optional filter — one of: 'wikipedia', 'arxiv', 'news', 'biorxiv', 'github', 'user_docs'
        days: Optional — only return chunks ingested in the last N days (useful for recent news)
        limit: Number of results (default 8, max 20)
    """
    limit = min(int(limit), 20)

    embed_model = SOURCE_MODEL_MAP.get(source, DEFAULT_MODEL)
    try:
        embedding = await get_embedding(query, model=embed_model)
    except Exception as e:
        return f"[Embedding unavailable: {e}. KB search skipped.]"

    # Build dynamic WHERE conditions for both CTEs
    conditions = []

    if source:
        conditions.append(sql.SQL("source = {}").format(sql.Literal(source)))
    if days:
        conditions.append(
            sql.SQL("created_at >= NOW() - {} * INTERVAL '1 day'").format(
                sql.Literal(days)
            )
        )

    where = sql.SQL("")
    if conditions:
        where = sql.SQL("AND ") + sql.SQL(" AND ").join(conditions)

    query_sql = sql.SQL("""
        WITH vector_results AS (
            SELECT source_id, chunk_index, content, metadata, source,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> {emb}::vector) AS rank
            FROM knowledge_chunks
            WHERE embedding IS NOT NULL
            {where}
            LIMIT 60
        ),
        fts_results AS (
            SELECT source_id, chunk_index, content, metadata, source,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank(to_tsvector('english', content),
                                        plainto_tsquery('english', {qtext})) DESC
                   ) AS rank
            FROM knowledge_chunks
            WHERE to_tsvector('english', content) @@ plainto_tsquery('english', {qtext})
            {where}
            LIMIT 60
        ),
        combined AS (
            SELECT
                COALESCE(v.source_id, f.source_id)     AS source_id,
                COALESCE(v.chunk_index, f.chunk_index)  AS chunk_index,
                COALESCE(v.content,    f.content)       AS content,
                COALESCE(v.metadata,   f.metadata)      AS metadata,
                COALESCE(v.source,     f.source)        AS source,
                COALESCE(1.0 / (60 + v.rank), 0.0)
                    + COALESCE(1.0 / (60 + f.rank), 0.0) AS rrf_score
            FROM vector_results v
            FULL OUTER JOIN fts_results f USING (source_id, chunk_index)
        )
        SELECT source_id, chunk_index, content, metadata, source, rrf_score
        FROM combined
        ORDER BY rrf_score DESC
        LIMIT {lim}
    """).format(
        emb=sql.Literal(embedding),
        qtext=sql.Literal(query),
        where=where,
        lim=sql.Literal(limit),
    )

    db_url = os.environ["DATABASE_URL"]
    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await register_vector_async(conn)
            rows = await (await conn.execute(query_sql)).fetchall()
    except Exception as e:
        return f"[Database error: {e}]"

    if not rows:
        scope = f" in {source}" if source else ""
        time_scope = f" (last {days} days)" if days else ""
        return f"No results found{scope}{time_scope}."

    parts = []
    for source_id, chunk_idx, content, metadata, src, score in rows:
        meta = metadata or {}
        url = meta.get("url", "")
        title = meta.get("title", source_id)
        label = f"[{src}] **{title}**" + (f" (<{url}>)" if url else "")
        parts.append(f"{label}\n{content.strip()}")

    return "\n\n---\n\n".join(parts)
