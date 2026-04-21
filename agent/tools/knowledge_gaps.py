import os
import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool

from .embed import get_embedding

SOURCES = ["wikipedia", "arxiv", "news", "biorxiv", "user_docs"]


@tool
async def knowledge_gaps(question: str) -> str:
    """Assess how well the knowledge base covers a question and identify what's missing.

    Scores KB coverage per source type, reports the best-matching content, and flags
    sources with no coverage. Use before a deep research session to understand where
    to supplement with live searches (arxiv_search, web_search).

    Args:
        question: The research question or topic to assess
    """
    try:
        embedding = await get_embedding(question)
    except Exception as e:
        return f"[Embedding unavailable: {e}]"

    db_url = os.environ["DATABASE_URL"]

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await register_vector_async(conn)

            coverage = {}
            for source in SOURCES:
                q = sql.SQL("""
                    SELECT source_id, content, metadata,
                           1 - (embedding <=> {emb}::vector) AS similarity
                    FROM knowledge_chunks
                    WHERE source = {src} AND embedding IS NOT NULL
                    ORDER BY embedding <=> {emb}::vector
                    LIMIT 3
                """).format(
                    emb=sql.Literal(embedding),
                    src=sql.Literal(source),
                )
                rows = await (await conn.execute(q)).fetchall()
                coverage[source] = rows

            # Get total chunk counts per source for context
            count_rows = await (await conn.execute("""
                SELECT source, COUNT(*) FROM knowledge_chunks GROUP BY source
            """)).fetchall()
            source_counts = {r[0]: r[1] for r in count_rows}

    except Exception as e:
        return f"[Database error: {e}]"

    SCORE_THRESHOLDS = {
        "strong": 0.75,
        "moderate": 0.55,
        "weak": 0.35,
    }

    lines = [f"**Knowledge base coverage assessment: '{question}'**\n"]
    strong, moderate, weak, missing = [], [], [], []

    for source in SOURCES:
        rows = coverage.get(source, [])
        total = source_counts.get(source, 0)

        if not rows:
            missing.append(source)
            continue

        top_sim = rows[0][3]
        if top_sim >= SCORE_THRESHOLDS["strong"]:
            tier = "strong"
            strong.append(source)
        elif top_sim >= SCORE_THRESHOLDS["moderate"]:
            tier = "moderate"
            moderate.append(source)
        elif top_sim >= SCORE_THRESHOLDS["weak"]:
            tier = "weak"
            weak.append(source)
        else:
            missing.append(source)
            continue

        meta = rows[0][2] or {}
        title = meta.get("title", rows[0][0].split("::")[0])
        url = meta.get("url", "")
        snippet = rows[0][1].strip()[:200]
        tier_icon = {"strong": "✓", "moderate": "~", "weak": "?"}[tier]
        lines.append(
            f"**{tier_icon} {source}** ({tier}, {top_sim:.0%} match, {total:,} chunks total)\n"
            f"  Best: *{title}*" + (f" — {url}" if url else "") + f"\n  > {snippet}…\n"
        )

    if missing:
        lines.append(f"**✗ No coverage:** {', '.join(missing)}")

    lines.append("\n**Recommendation:**")
    if strong:
        lines.append(f"- Use `kb_search` or `research` — strong local coverage in: {', '.join(strong)}")
    if weak or missing:
        supplement = weak + missing
        lines.append(f"- Supplement with live tools for: {', '.join(supplement)}")
        if "arxiv" in supplement:
            lines.append("  → `arxiv_search` for recent academic papers")
        if "news" in supplement:
            lines.append("  → `web_search` for current news coverage")

    return "\n".join(lines)
