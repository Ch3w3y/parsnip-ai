import asyncio
import os
import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool

from .embed import get_embedding
from .llm_client import llm_call


async def _expand_queries(topic: str, n: int = 4) -> list[str]:
    """Use a fast LLM to generate query variants covering different angles of the topic."""
    prompt = (
        f"Generate {n} distinct search queries to comprehensively research this topic: \"{topic}\"\n"
        "Each query should cover a different angle (e.g. background, recent developments, "
        "technical details, real-world impact).\n"
        "Return only the queries, one per line. No numbering, no explanation."
    )

    result = await llm_call(
        messages=[{"role": "user", "content": prompt}],
        tier="simple",
        max_tokens=200,
        temperature=0.4,
    )

    if isinstance(result, str) and result:
        queries = [q.strip() for q in result.splitlines() if q.strip()]
        return queries[:n] if queries else [topic]
    return [topic]


async def _rrf_search(
    conn,
    embedding: list[float],
    query_text: str,
    sources: list[str] | None,
    days: int | None,
    candidate_limit: int = 60,
    user_id: str | None = None,
) -> list[tuple]:
    """Run one RRF query and return rows: (source_id, chunk_index, content, metadata, source, rrf_score).

    user_id: when set, joplin_notes results are filtered to that user (org-wide NULL rows also included).
    """
    conditions = []
    if sources:
        conditions.append(sql.SQL("source = ANY({})").format(sql.Literal(sources)))
    if days:
        conditions.append(sql.SQL("created_at >= NOW() - {} * INTERVAL '1 day'").format(sql.Literal(days)))
    if user_id and sources and "joplin_notes" in sources:
        # Allow org-wide (NULL) and this user's notes
        conditions.append(
            sql.SQL("(user_id IS NULL OR user_id = {})").format(sql.Literal(user_id))
        )
    elif user_id is None and sources and "joplin_notes" in sources:
        # No user context — only return org-wide notes (user_id IS NULL)
        conditions.append(sql.SQL("user_id IS NULL"))

    where = sql.SQL("")
    if conditions:
        where = sql.SQL("AND ") + sql.SQL(" AND ").join(conditions)

    q = sql.SQL("""
        WITH vector_results AS (
            SELECT source_id, chunk_index, content, metadata, source,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> {emb}::vector) AS rank
            FROM knowledge_chunks
            WHERE embedding IS NOT NULL
            {where}
            LIMIT {lim}
        ),
        fts_results AS (
            SELECT source_id, chunk_index, content, metadata, source,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank(to_tsvector('english', content),
                                        plainto_tsquery('english', {qt})) DESC
                   ) AS rank
            FROM knowledge_chunks
            WHERE to_tsvector('english', content) @@ plainto_tsquery('english', {qt})
            {where}
            LIMIT {lim}
        ),
        combined AS (
            SELECT
                COALESCE(v.source_id, f.source_id)    AS source_id,
                COALESCE(v.chunk_index, f.chunk_index) AS chunk_index,
                COALESCE(v.content,    f.content)      AS content,
                COALESCE(v.metadata,   f.metadata)     AS metadata,
                COALESCE(v.source,     f.source)       AS source,
                COALESCE(1.0/(60+v.rank), 0.0) + COALESCE(1.0/(60+f.rank), 0.0) AS rrf_score
            FROM vector_results v
            FULL OUTER JOIN fts_results f USING (source_id, chunk_index)
        )
        SELECT source_id, chunk_index, content, metadata, source, rrf_score
        FROM combined
        ORDER BY rrf_score DESC
        LIMIT {lim}
    """).format(
        emb=sql.Literal(embedding),
        qt=sql.Literal(query_text),
        where=where,
        lim=sql.Literal(candidate_limit),
    )

    rows = await (await conn.execute(q)).fetchall()
    return rows


@tool
async def research(
    topic: str,
    source: str | None = None,
    days: int | None = None,
    limit: int = 20,
) -> str:
    """Deep research tool — expands a topic into multiple search angles and merges results.

    Generates 4 query variants covering different aspects of the topic (background,
    recent developments, technical detail, real-world impact), runs them in parallel
    against the knowledge base, deduplicates at the article level, and returns the
    most relevant unique documents.

    Use this instead of kb_search when:
    - The topic is broad or multi-faceted
    - A single query might miss important angles
    - You want comprehensive coverage, not just the closest match

    Args:
        topic: Research topic or question (can be broad)
        source: Optional filter — 'wikipedia', 'arxiv', 'news', 'biorxiv', 'user_docs'
        days: Optional — restrict to content ingested in the last N days
        limit: Max unique articles to return (default 20)
    """
    limit = min(int(limit), 30)

    # 1. Expand topic into query variants
    queries = await _expand_queries(topic)

    # 2. Embed all queries in parallel
    try:
        embeddings = await asyncio.gather(*[get_embedding(q) for q in queries])
    except Exception as e:
        return f"[Embedding unavailable: {e}]"

    # 3. Run RRF search for each query variant in parallel
    db_url = os.environ["DATABASE_URL"]
    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await register_vector_async(conn)
            sources = [source] if source else None
            search_tasks = [
                _rrf_search(conn, emb, q, sources, days)
                for emb, q in zip(embeddings, queries)
            ]
            all_results = await asyncio.gather(*search_tasks)
    except Exception as e:
        return f"[Database error: {e}]"

    # 4. Merge: keep best-scoring chunk per (source, source_id) across all query results
    # This deduplicates at the article level so we don't flood with chunks from one doc
    best: dict[tuple, tuple] = {}
    for rows in all_results:
        for row in rows:
            source_id, chunk_idx, content, metadata, src, score = row
            key = (src, source_id)
            if key not in best or score > best[key][5]:
                best[key] = row

    # 5. Sort by score, take top N
    ranked = sorted(best.values(), key=lambda r: r[5], reverse=True)[:limit]

    if not ranked:
        scope = f" in {source}" if source else ""
        return f"No results found for '{topic}'{scope}."

    # 6. Format output
    header = (
        f"**Research: {topic}**\n"
        f"*Searched {len(queries)} query angles · "
        f"{len(best)} unique documents found · showing top {len(ranked)}*\n"
        f"Queries used: {' | '.join(queries)}\n"
    )

    parts = []
    for source_id, chunk_idx, content, metadata, src, score in ranked:
        meta = metadata or {}
        url = meta.get("url", "")
        title = meta.get("title", source_id)
        label = f"[{src}] **{title}**" + (f" (<{url}>)" if url else "")
        parts.append(f"{label}\n{content.strip()}")

    return header + "\n\n---\n\n" + "\n\n---\n\n".join(parts)
