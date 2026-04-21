"""
knowledge_graph — Extract concepts and relationships from KB content,
build a knowledge graph, and output as Mermaid diagram + Joplin note.

The agent can call this repeatedly to visualize the knowledge base as it grows.
"""

import asyncio
import json
import os
import psycopg
from pgvector.psycopg import register_vector_async
from langchain_core.tools import tool

from .llm_client import llm_call


async def _sample_chunks(conn, source: str, limit: int = 30) -> list[dict]:
    rows = await (
        await conn.execute(
            """
        SELECT source_id, content, metadata
        FROM knowledge_chunks
        WHERE source = %s AND embedding IS NOT NULL
        ORDER BY RANDOM()
        LIMIT %s
        """,
            (source, limit),
        )
    ).fetchall()
    return [{"source_id": r[0], "content": r[1][:1000], "metadata": r[2]} for r in rows]


async def _extract_concepts(chunks: list[dict], source: str) -> str:
    content_snippets = "\n---\n".join(
        f"[{c['source_id']}]\n{c['content'][:500]}" for c in chunks[:15]
    )

    prompt = (
        f"Extract the key concepts, entities, and their relationships from this "
        f"{source} knowledge base sample. Return a JSON array of relationships:\n\n"
        f"Content:\n{content_snippets}\n\n"
        f"Return ONLY a JSON array of objects with fields:\n"
        f'  "source": concept name (string)\n'
        f'  "target": related concept (string)\n'
        f'  "label": relationship type (string, e.g. "is_a", "related_to", "part_of", "causes")\n\n'
        f"Extract 15-30 relationships. Focus on domain-specific knowledge, "
        f"not generic terms. Use short concept names (1-3 words)."
    )

    result = await llm_call(
        messages=[{"role": "user", "content": prompt}],
        tier="simple",
        max_tokens=2000,
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    if isinstance(result, dict):
        return json.dumps(result)
    return result if isinstance(result, str) else ""


def _build_mermaid(relations: list[dict], title: str) -> str:
    if not relations:
        return f"No relationships extracted for {title}."

    def sanitize(name: str) -> str:
        return "".join(c if c.isalnum() else "_" for c in name)[:30]

    nodes = set()
    edges = []
    for rel in relations:
        src = sanitize(rel.get("source", ""))
        tgt = sanitize(rel.get("target", ""))
        label = rel.get("label", "related_to")
        if src and tgt:
            nodes.add(src)
            nodes.add(tgt)
            edges.append((src, label, tgt))

    lines = [f"graph TD", f"    subgraph {title}", ""]
    for node in sorted(nodes):
        lines.append(f"    {node}[{node.replace('_', ' ')}]")
    lines.append("")
    for src, label, tgt in edges:
        lines.append(f"    {src} -- {label} --> {tgt}")
    lines.append("    end")

    return "\n".join(lines)


def _extract_relationships(result) -> list[dict]:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return result.get("relationships", []) if "relationships" in result else [result]
    if isinstance(result, str):
        try:
            data = json.loads(result)
            if isinstance(data, list):
                return data
            return data.get("relationships", []) if isinstance(data, dict) else []
        except json.JSONDecodeError:
            return []
    return []


@tool
async def generate_knowledge_graph(
    sources: list[str] | None = None,
    samples_per_source: int = 30,
) -> str:
    """Generate a knowledge graph from the KB by extracting concepts and relationships.

    Samples content from each source, uses the LLM to identify key concepts and
    their relationships, then outputs a Mermaid diagram showing the knowledge structure.

    Args:
        sources: List of sources to include. None = all sources.
                 Options: wikipedia, news, github, arxiv, biorxiv, joplin_notes
        samples_per_source: Number of chunks to sample per source (default 30)
    """
    db_url = os.environ["DATABASE_URL"]

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await register_vector_async(conn)
            if sources:
                source_rows = await (
                    await conn.execute(
                        "SELECT DISTINCT source FROM knowledge_chunks WHERE source = ANY(%s)",
                        (sources,),
                    )
                ).fetchall()
            else:
                source_rows = await (
                    await conn.execute(
                        "SELECT DISTINCT source FROM knowledge_chunks ORDER BY source",
                    )
                ).fetchall()
            available_sources = [r[0] for r in source_rows]
    except Exception as e:
        return f"[Database error: {e}]"

    if not available_sources:
        return "No knowledge sources found in the database."

    all_mermaids = []
    all_relations = {}
    total_extracted = 0

    for source in available_sources:
        try:
            async with await psycopg.AsyncConnection.connect(db_url) as conn:
                await register_vector_async(conn)
                chunks = await _sample_chunks(conn, source, samples_per_source)
        except Exception:
            continue

        if not chunks:
            continue

        raw_json = await _extract_concepts(chunks, source)
        if not raw_json:
            continue

        try:
            data = json.loads(raw_json)
            relations = _extract_relationships(data)
        except json.JSONDecodeError:
            continue

        if not relations:
            continue

        mermaid = _build_mermaid(relations, source.replace("_", " ").title())
        all_mermaids.append(mermaid)
        all_relations[source] = relations
        total_extracted += len(relations)

    if not all_mermaids:
        return "No relationships could be extracted from the knowledge base."

    parts = [
        f"**Knowledge Graph** — {total_extracted} relationships extracted "
        f"from {len(available_sources)} source(s)\n\n"
    ]

    for mermaid in all_mermaids:
        parts.append(f"```mermaid\n{mermaid}\n```")

    if len(available_sources) > 1:
        cross_prompt = (
            "Based on these knowledge domains, identify 5-10 cross-domain relationships:\n\n"
            + "\n".join(
                f"- {src}: {len(rels)} relationships"
                for src, rels in all_relations.items()
            )
            + "\n\nReturn JSON array with source, target, label fields."
        )

        cross_result = await llm_call(
            messages=[{"role": "user", "content": cross_prompt}],
            tier="simple",
            max_tokens=1000,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        cross_rels = _extract_relationships(cross_result)
        if cross_rels:
            cross_mermaid = _build_mermaid(cross_rels, "Cross-Domain")
            parts.append(f"```mermaid\n{cross_mermaid}\n```")

    return "\n\n".join(parts)