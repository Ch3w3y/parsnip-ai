"""
holistic_search — Knowledge Horizon Retrieval

Traverses all knowledge sources in priority order, returning a layered picture:
  1. Current Events  (news)             — what is happening now
  2. Research Frontier (arXiv, bioRxiv) — what is being discovered
  3. Established Knowledge (Wikipedia)  — what is known / historical context
  4. Your Notes (Joplin)                — your existing perspective, if relevant
  5. Code & Implementation (GitHub)     — how it's built in real projects

This design reflects the platform's core purpose: compensating for LLM training
cutoffs with a private, curated, continuously-refreshed context bank. Rather than
flattening all sources into a single similarity rank, it models knowledge as having
recency and authority gradients — and retrieves across those gradients explicitly.

Query intent detection dynamically reorders layers and adjusts budgets:
  - Code queries (implementation, framework, API, "how to build"): GitHub moves first
  - Research queries (paper, study, model, algorithm): arXiv/bioRxiv moves first
  - General queries: default order

Each layer is queried independently with its own result budget. Layers with no
relevant content are silently omitted. Multiple query variants are generated and
run in parallel per layer to reduce the chance of missing important angles.
"""

import asyncio
import os
import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool

from .embed import get_embedding
from .research import _expand_queries, _rrf_search
from .router import (
    detect_intent,
    ROUTING_CONFIG,
    SOURCE_MODEL_MAP,
    DEFAULT_MODEL,
)


def _reorder_layers(topic: str, intent: str) -> list[tuple]:
    """Return layers ordered by query intent with adjusted budgets."""
    budgets = ROUTING_CONFIG["layer_budgets"]
    if intent == "code":
        return [
            ("Code & Implementation", ["github"], budgets.get("github", 6), None),
            ("Current Events", ["news"], budgets.get("news", 2), 30),
            ("Established Knowledge", ["wikipedia"], budgets.get("wikipedia", 3), None),
            ("Your Notes", ["joplin_notes"], budgets.get("joplin_notes", 2), None),
            ("Research Frontier", ["arxiv", "biorxiv"], budgets.get("arxiv", 2), None),
        ]
    if intent == "research":
        return [
            ("Research Frontier", ["arxiv", "biorxiv"], budgets.get("arxiv", 6), None),
            ("Current Events", ["news"], budgets.get("news", 2), 30),
            ("Established Knowledge", ["wikipedia"], budgets.get("wikipedia", 3), None),
            ("Your Notes", ["joplin_notes"], budgets.get("joplin_notes", 2), None),
            ("Code & Implementation", ["github"], budgets.get("github", 3), None),
        ]
    return [
        ("Current Events", ["news"], budgets.get("news", 4), 30),
        ("Research Frontier", ["arxiv", "biorxiv"], budgets.get("arxiv", 4), None),
        ("Established Knowledge", ["wikipedia"], budgets.get("wikipedia", 3), None),
        ("Your Notes", ["joplin_notes"], budgets.get("joplin_notes", 2), None),
        ("Code & Implementation", ["github"], budgets.get("github", 3), None),
    ]


async def _search_layer(
    conn,
    embeddings: list[list[float]],
    queries: list[str],
    sources: list[str],
    budget: int,
    days: int | None,
    user_id: str | None = None,
) -> list[tuple]:
    """Run all query variants against one layer and return top-budget deduplicated rows."""
    tasks = [
        _rrf_search(conn, emb, q, sources, days, user_id=user_id)
        for emb, q in zip(embeddings, queries)
    ]
    all_results = await asyncio.gather(*tasks)

    # Keep best-scoring chunk per (source, source_id) across query variants
    best: dict[tuple, tuple] = {}
    for rows in all_results:
        for row in rows:
            source_id, chunk_idx, content, metadata, src, score = row
            key = (src, source_id)
            if key not in best or score > best[key][5]:
                best[key] = row

    return sorted(best.values(), key=lambda r: r[5], reverse=True)[:budget]


def _format_layer(label: str, rows: list[tuple]) -> str:
    parts = []
    for source_id, chunk_idx, content, metadata, src, score in rows:
        meta = metadata or {}
        url = meta.get("url", "")
        title = meta.get("title", source_id)
        published = meta.get("published", "")
        date_tag = f" · {published[:10]}" if published else ""
        citation = f"[{src}{date_tag}] **{title}**" + (f" (<{url}>)" if url else "")
        parts.append(f"{citation}\n{content.strip()}")
    return "\n\n".join(parts)


@tool
async def holistic_search(
    topic: str,
    news_days: int = 30,
    user_id: str | None = None,
) -> str:
    """Search all knowledge horizons in priority order to build a complete picture of a topic.

    Covers the full knowledge stack automatically:
      - Current Events  (news, last news_days days)   — what is happening now
      - Research Frontier (arXiv + bioRxiv)           — latest scientific developments
      - Established Knowledge (Wikipedia)             — background and historical context
      - Your Notes (Joplin)                           — your existing perspective

    Use this as the DEFAULT tool for any broad question about a topic. It compensates
    for the LLM's training cutoff by drawing on a continuously-refreshed local knowledge
    base curated from trusted sources. Only use kb_search or research when you need
    a precise targeted lookup or deep single-source coverage.

    Args:
        topic: The topic, question, or concept to research
        news_days: How many days back to search for news (default 30)
        user_id: Optional — restrict Joplin notes to this user (plus org-wide notes).
                 Omit to see only org-wide notes.
    """
    # Detect query intent and reorder layers accordingly
    intent = detect_intent(topic)
    layers = _reorder_layers(topic, intent)

    # Generate query variants to cover different angles of the topic
    queries = await _expand_queries(topic, n=3)

    db_url = os.environ["DATABASE_URL"]
    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await register_vector_async(conn)

            # Embed queries with both models (mxbai for most layers, bge-m3 for github)
            try:
                mxbai_embs = await asyncio.gather(
                    *[get_embedding(q, model=DEFAULT_MODEL) for q in queries]
                )
            except Exception as e:
                return f"[Embedding unavailable: {e}]"

            bge_embs = None
            try:
                bge_embs = await asyncio.gather(
                    *[get_embedding(q, model="bge-m3") for q in queries]
                )
            except Exception:
                pass  # github layer will fall back to mxbai if bge-m3 unavailable

            # Build layer tasks based on intent-reordered layers
            layer_tasks = []
            for label, sources, budget, _ in layers:
                layer_model = SOURCE_MODEL_MAP.get(sources[0], DEFAULT_MODEL)
                embs = bge_embs if layer_model == "bge-m3" and bge_embs else mxbai_embs
                days = news_days if label == "Current Events" else None
                uid = user_id if label == "Your Notes" else None
                layer_tasks.append(
                    _search_layer(
                        conn,
                        embs,
                        queries,
                        sources,
                        budget,
                        days,
                        user_id=uid,
                    )
                )
            layer_results = await asyncio.gather(*layer_tasks)
    except Exception as e:
        return f"[Database error: {e}]"

    # Build output — skip empty layers
    sections = []
    total_results = 0
    for (label, sources, budget, _), rows in zip(layers, layer_results):
        if not rows:
            continue
        source_tag = " · ".join(sources)
        header = f"## {label}  ({source_tag})"
        if label == "Current Events":
            header += f" · last {news_days} days"
        sections.append(header + "\n\n" + _format_layer(label, rows))
        total_results += len(rows)

    if not sections:
        return f"No results found for '{topic}' across any knowledge source."

    preamble = (
        f"**{topic}** — {total_results} results across "
        f"{len(sections)} knowledge horizon(s) · "
        f"queries: {' | '.join(queries)}\n"
    )
    return preamble + "\n\n---\n\n" + "\n\n---\n\n".join(sections)
