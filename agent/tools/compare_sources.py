import asyncio
import os
import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool

from .embed import get_embedding

SOURCES = ["wikipedia", "arxiv", "news", "biorxiv", "user_docs"]


async def _best_for_source(conn, embedding: list[float], query: str, source: str) -> tuple | None:
    """Return the single best RRF result for one source."""
    q = sql.SQL("""
        WITH vector_results AS (
            SELECT source_id, chunk_index, content, metadata,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> {emb}::vector) AS rank
            FROM knowledge_chunks
            WHERE source = {src} AND embedding IS NOT NULL
            LIMIT 40
        ),
        fts_results AS (
            SELECT source_id, chunk_index, content, metadata,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank(to_tsvector('english', content),
                                        plainto_tsquery('english', {qt})) DESC
                   ) AS rank
            FROM knowledge_chunks
            WHERE source = {src}
              AND to_tsvector('english', content) @@ plainto_tsquery('english', {qt})
            LIMIT 40
        ),
        combined AS (
            SELECT
                COALESCE(v.source_id, f.source_id)     AS source_id,
                COALESCE(v.chunk_index, f.chunk_index)  AS chunk_index,
                COALESCE(v.content,    f.content)       AS content,
                COALESCE(v.metadata,   f.metadata)      AS metadata,
                COALESCE(1.0/(60+v.rank), 0.0) + COALESCE(1.0/(60+f.rank), 0.0) AS rrf_score
            FROM vector_results v
            FULL OUTER JOIN fts_results f USING (source_id, chunk_index)
        )
        SELECT source_id, content, metadata, rrf_score
        FROM combined ORDER BY rrf_score DESC LIMIT 1
    """).format(
        emb=sql.Literal(embedding),
        qt=sql.Literal(query),
        src=sql.Literal(source),
    )
    row = await (await conn.execute(q)).fetchone()
    return row


@tool
async def compare_sources(topic: str) -> str:
    """Compare perspectives on a topic across all knowledge base sources.

    Retrieves the best matching document from each source type (Wikipedia,
    arXiv, news, bioRxiv, user_docs) and presents them side by side.

    Ideal for topics where academic, encyclopedic, and journalistic perspectives
    meaningfully differ — e.g. AI safety, climate change, new drug treatments.

    Args:
        topic: The topic to compare across sources
    """
    try:
        embedding = await get_embedding(topic)
    except Exception as e:
        return f"[Embedding unavailable: {e}]"

    db_url = os.environ["DATABASE_URL"]
    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await register_vector_async(conn)
            results = await asyncio.gather(
                *[_best_for_source(conn, embedding, topic, src) for src in SOURCES],
                return_exceptions=True,
            )
    except Exception as e:
        return f"[Database error: {e}]"

    found = []
    missing = []
    for src, result in zip(SOURCES, results):
        if isinstance(result, Exception) or result is None:
            missing.append(src)
        else:
            found.append((src, result))

    if not found:
        return f"No results found for '{topic}' in any knowledge base source."

    header = f"**Cross-source comparison: {topic}**\n"
    if missing:
        header += f"*No coverage in: {', '.join(missing)}*\n"
    header += "\n"

    parts = []
    source_labels = {
        "wikipedia": "Wikipedia — encyclopedic background",
        "arxiv":     "arXiv — academic research",
        "news":      "News — current coverage",
        "biorxiv":   "bioRxiv — life sciences preprints",
        "user_docs": "Your documents",
    }
    for src, (source_id, content, metadata, score) in found:
        meta = metadata or {}
        url = meta.get("url", "")
        title = meta.get("title", source_id)
        label = f"### {source_labels.get(src, src)}\n**{title}**" + (f"\n{url}" if url else "")
        parts.append(f"{label}\n\n{content.strip()}")

    return header + "\n\n".join(parts)
