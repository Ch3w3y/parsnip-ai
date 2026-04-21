import os
import psycopg
from langchain_core.tools import tool


@tool
async def get_document(source_id: str, source: str) -> str:
    """Retrieve the full text of a document from the knowledge base by its source ID.

    Use this when kb_search or research returns a relevant chunk and you need the
    complete article for full context. Assembles all chunks in order.

    Args:
        source_id: The document identifier returned in search results (e.g. article title, DOI, URL)
        source: The knowledge base source — one of: 'wikipedia', 'arxiv', 'news', 'biorxiv', 'user_docs'
    """
    db_url = os.environ["DATABASE_URL"]
    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            rows = await (await conn.execute(
                """
                SELECT chunk_index, content, metadata
                FROM knowledge_chunks
                WHERE source = %s AND source_id = %s
                ORDER BY chunk_index
                """,
                (source, source_id),
            )).fetchall()
    except Exception as e:
        return f"[Database error: {e}]"

    if not rows:
        return f"No document found for source_id='{source_id}' in source='{source}'."

    meta = rows[0][2] or {}
    url = meta.get("url", "")
    title = meta.get("title", source_id)

    header = f"**{title}**"
    if url:
        header += f"\n{url}"
    header += f"\n*{len(rows)} chunk(s) from {source}*\n"

    # Join chunks — overlap means adjacent chunks share ~50 words; trim leading overlap
    assembled = []
    for i, (chunk_idx, content, _) in enumerate(rows):
        assembled.append(content.strip())

    return header + "\n\n" + "\n\n[...]\n\n".join(assembled)
