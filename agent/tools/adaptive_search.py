"""
adaptive_search — Multi-Tier Routing with Web-First + HyDE + KB Fusion

Pipeline:
  1. User prompt → complexity classifier (small model or heuristic)
  2. Complexity tier determines LLM model + search depth
  3. Web search ALWAYS first (SearXNG, low cost, fast)
  4. HyDE generates hypothetical document from web context (if tier >= mid)
  5. KB search expands based on intent + tier depth
  6. Full answer assembled with all context sources

This treats the KB as ground truth, complemented by timely web context
and HyDE-guided retrieval — pseudo-elastic search that adapts to query complexity.
"""

import asyncio
import os
import psycopg
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool

from .embed import get_embedding
from .research import _rrf_search
from .llm_client import llm_call
from .router import (
    classify_complexity,
    ROUTING_CONFIG,
    SOURCE_MODEL_MAP,
    DEFAULT_MODEL,
)
from .holistic_search import _search_layer, _format_layer
from .web import _searxng_search


async def _web_search(query: str, max_results: int = 5) -> str:
    """Quick web search via SearXNG."""
    url = os.environ.get("SEARXNG_URL", "http://localhost:8080")
    return await _searxng_search(query, max_results, url)


async def _generate_hypothetical(
    topic: str, web_context: str, intent: str, model: str
) -> str:
    """Generate a hypothetical answer/document for HyDE using the tier's LLM."""
    intent_context = {
        "code": "Write a detailed technical explanation with code examples, "
        "implementation patterns, and architecture details.",
        "research": "Write a detailed academic-style summary covering methodology, "
        "findings, and technical details.",
        "current": "Write a timely summary incorporating the latest developments and context.",
        "general": "Write a comprehensive, factual summary covering all aspects of the topic.",
    }

    prompt = (
        f'Based on the following web context about "{topic}", write a detailed '
        f"hypothetical document that would be an ideal knowledge base entry for this topic.\n\n"
        f"Web context:\n{web_context[:2000]}\n\n"
        f"{intent_context.get(intent, intent_context['general'])}\n\n"
        f"Write 2-4 paragraphs. Be specific and technical. Include key terms, "
        f"names, dates, and concepts that would match relevant documents."
    )

    result = await llm_call(
        messages=[{"role": "user", "content": prompt}],
        tier="simple",
        max_tokens=500,
        temperature=0.3,
    )

    return result if isinstance(result, str) and result else topic


async def _search_kb_layers(
    query: str,
    intent: str,
    depth: dict,
    model: str = DEFAULT_MODEL,
) -> list[tuple]:
    """Search KB layers based on intent and search depth config."""
    db_url = os.environ["DATABASE_URL"]
    layers = ROUTING_CONFIG["intent_layers"].get(
        intent, ROUTING_CONFIG["intent_layers"]["general"]
    )
    budgets = ROUTING_CONFIG["layer_budgets"]

    # Limit layers to configured depth
    layers = layers[: depth["kb_layers"]]

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await register_vector_async(conn)

            # Embed with appropriate model
            emb_model = model
            try:
                emb = await get_embedding(query, model=emb_model)
            except Exception:
                try:
                    emb = await get_embedding(query, model=DEFAULT_MODEL)
                except Exception:
                    return []

            all_results = []
            for source in layers:
                budget = budgets.get(source, depth["kb_budget"])
                try:
                    src_model = SOURCE_MODEL_MAP.get(source, emb_model)
                    if src_model != emb_model:
                        try:
                            src_emb = await get_embedding(query, model=src_model)
                        except Exception:
                            src_emb = emb
                    else:
                        src_emb = emb

                    rows = await _rrf_search(
                        conn, src_emb, query, [source], None, candidate_limit=budget
                    )
                    all_results.extend(rows)
                except Exception:
                    continue

            # Deduplicate at article level
            best = {}
            for row in all_results:
                source_id, chunk_idx, content, metadata, src, score = row
                key = (src, source_id)
                if key not in best or score > best[key][5]:
                    best[key] = row

            return sorted(best.values(), key=lambda r: r[5], reverse=True)[
                : depth["kb_budget"]
            ]
    except Exception:
        return []


@tool
async def adaptive_search(topic: str) -> str:
    """Adaptive multi-tier search with web-first retrieval and HyDE fusion.

    This is the smartest search tool. The pipeline:
    1. Classifies query complexity (simple/mid/complex) using a small model
    2. Always runs web search first for current context
    3. For mid/complex queries: generates a hypothetical document (HyDE) from web context
    4. Searches KB layers based on intent (code→GitHub, research→arXiv, etc.)
    5. Fuses all results with source attribution

    Use for any query where you want both current web context and KB ground truth.
    The complexity classifier automatically adjusts search depth and LLM tier.
    """
    # Phase 1: Classify complexity
    classification = await classify_complexity(topic)
    tier = classification.tier
    intent = classification.intent
    depth = classification.search_params
    llm_model = ROUTING_CONFIG["llm_tiers"][tier]

    # Phase 2: Web search ALWAYS first
    web_context = ""
    try:
        web_context = await _web_search(topic, max_results=depth["web_results"])
        web_available = not web_context.startswith("[")
    except Exception:
        web_available = False

    # Phase 3: HyDE (mid/high tiers only)
    hyde_kb_results = []
    used_hyde = False
    if depth["hyde"] and web_available:
        hypothetical = await _generate_hypothetical(
            topic, web_context, intent, llm_model
        )
        if hypothetical != topic:
            hyde_kb_results = await _search_kb_layers(
                hypothetical, intent, depth, model=DEFAULT_MODEL
            )
            used_hyde = True

    # Phase 4: Direct KB search (all tiers)
    kb_results = await _search_kb_layers(topic, intent, depth)

    # Phase 5: Assemble output
    sections = []
    total = 0

    # Web results
    if web_available and web_context:
        sections.append("## Web Context\n\n" + web_context)
        total += 1

    # HyDE results
    if hyde_kb_results:
        parts = []
        for source_id, chunk_idx, content, metadata, src, score in hyde_kb_results:
            meta = metadata or {}
            url = meta.get("url", "")
            title = meta.get("title", source_id)
            citation = f"[{src}] **{title}**" + (f" (<{url}>)" if url else "")
            parts.append(f"{citation}\n{content.strip()}")
        sections.append(
            "## HyDE Results  (web-grounded KB retrieval)\n\n" + "\n\n".join(parts)
        )
        total += len(hyde_kb_results)

    # Direct KB results
    if kb_results:
        parts = []
        for source_id, chunk_idx, content, metadata, src, score in kb_results:
            meta = metadata or {}
            url = meta.get("url", "")
            title = meta.get("title", source_id)
            citation = f"[{src}] **{title}**" + (f" (<{url}>)" if url else "")
            parts.append(f"{citation}\n{content.strip()}")
        sections.append("## Knowledge Base\n\n" + "\n\n".join(parts))
        total += len(kb_results)

    if not sections:
        return f"No results found for '{topic}'."

    preamble = (
        f"**{topic}**\n"
        f"Complexity: {tier} (score {classification.score}) · "
        f"Intent: {intent} · "
        f"HyDE: {'yes' if used_hyde else 'no'}\n"
        f"*{classification.reasoning}*\n"
    )

    return preamble + "\n\n---\n\n" + "\n\n---\n\n".join(sections)
