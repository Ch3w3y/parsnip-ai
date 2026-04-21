import os
import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool


@tool
async def find_similar(source_id: str, source: str, limit: int = 10) -> str:
    """Find documents semantically similar to a known document using its stored embedding.

    Use this after kb_search or research returns a relevant document — pass its
    source_id and source to discover related content across all KB sources without
    issuing a new search query.

    Args:
        source_id: The document identifier (from a previous search result)
        source: The source the document came from ('wikipedia', 'arxiv', 'news', 'biorxiv', 'user_docs')
        limit: Number of similar documents to return (default 10, max 20)
    """
    limit = min(int(limit), 20)
    db_url = os.environ["DATABASE_URL"]

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await register_vector_async(conn)

            # Fetch the representative embedding for the anchor document
            # Use chunk_index=0 as the anchor; fall back to any chunk if missing
            anchor = await (await conn.execute(
                """
                SELECT embedding FROM knowledge_chunks
                WHERE source = %s AND source_id = %s AND embedding IS NOT NULL
                ORDER BY chunk_index
                LIMIT 1
                """,
                (source, source_id),
            )).fetchone()

            if not anchor:
                return f"Document not found or has no embedding: source='{source}' source_id='{source_id}'"

            embedding = anchor[0]

            # Find similar documents — exclude the anchor document itself
            # Deduplicate at article level: one best chunk per source_id
            rows = await (await conn.execute(
                sql.SQL("""
                    WITH candidates AS (
                        SELECT
                            source_id, chunk_index, content, metadata, source,
                            embedding <=> {emb}::vector AS distance,
                            ROW_NUMBER() OVER (
                                PARTITION BY source, source_id
                                ORDER BY embedding <=> {emb}::vector
                            ) AS rn
                        FROM knowledge_chunks
                        WHERE embedding IS NOT NULL
                          AND NOT (source = {src} AND source_id = {sid})
                    )
                    SELECT source_id, chunk_index, content, metadata, source, distance
                    FROM candidates
                    WHERE rn = 1
                    ORDER BY distance
                    LIMIT {lim}
                """).format(
                    emb=sql.Literal(embedding),
                    src=sql.Literal(source),
                    sid=sql.Literal(source_id),
                    lim=sql.Literal(limit),
                )
            )).fetchall()
    except Exception as e:
        return f"[Database error: {e}]"

    if not rows:
        return "No similar documents found."

    parts = []
    for sid, chunk_idx, content, metadata, src, distance in rows:
        meta = metadata or {}
        url = meta.get("url", "")
        title = meta.get("title", sid)
        similarity = f"{(1 - distance) * 100:.0f}% similar"
        label = f"[{src}] **{title}** ({similarity})" + (f"\n{url}" if url else "")
        parts.append(f"{label}\n{content.strip()[:400]}{'…' if len(content) > 400 else ''}")

    header = f"*Documents similar to [{source}] {source_id} — {len(rows)} results*\n"
    return header + "\n\n---\n\n" + "\n\n---\n\n".join(parts)
