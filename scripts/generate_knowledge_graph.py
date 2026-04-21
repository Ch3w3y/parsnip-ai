#!/usr/bin/env python3
"""
Generate a knowledge graph from the pi-agent knowledge base.

Samples content from each source, extracts concepts and relationships via LLM,
and outputs a Mermaid diagram. Designed to be called repeatedly as the KB grows.

Usage:
    uv run python generate_knowledge_graph.py
    uv run python generate_knowledge_graph.py --sources wikipedia github
    uv run python generate_knowledge_graph.py --samples 50
    uv run python generate_knowledge_graph.py --output graph.md
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector_async

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
LLM_MODEL = os.environ.get("DEFAULT_LLM", "google/gemini-2.0-flash-001")


async def sample_chunks(conn, source: str, limit: int = 30) -> list[dict]:
    """Sample representative chunks from a source."""
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


async def extract_concepts(chunks: list[dict], source: str) -> list[dict]:
    """Use LLM to extract concepts and relationships from sampled content."""
    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set, skipping concept extraction")
        return []

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
        f'  "label": relationship type (string, e.g. "is_a", "related_to", "part_of", "causes", "used_by", "implements")\n\n'
        f"Extract 15-30 relationships. Focus on domain-specific knowledge, "
        f"not generic terms. Use short concept names (1-3 words)."
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2000,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
            )
            r.raise_for_status()
            data = r.json()["choices"][0]["message"]["content"].strip()
            result = json.loads(data)
            if isinstance(result, list):
                return result
            return result.get("relationships", [])
    except Exception as e:
        logger.error(f"Failed to extract concepts for {source}: {e}")
        return []


def sanitize(name: str) -> str:
    """Sanitize node names for Mermaid."""
    return "".join(c if c.isalnum() else "_" for c in name)[:30]


def build_mermaid(relations: list[dict], title: str) -> str:
    """Build a Mermaid graph from extracted relationships."""
    if not relations:
        return ""

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

    lines = [f"graph TD", f"    subgraph {title.replace(' ', '_')}", ""]
    for node in sorted(nodes):
        lines.append(f"    {node}[{node.replace('_', ' ')}]")
    lines.append("")
    for src, label, tgt in edges:
        lines.append(f"    {src} -- {label} --> {tgt}")
    lines.append("    end")

    return "\n".join(lines)


async def extract_cross_domain(all_relations: dict[str, list[dict]]) -> list[dict]:
    """Extract cross-domain relationships between knowledge sources."""
    if not OPENROUTER_API_KEY or len(all_relations) < 2:
        return []

    summary = "\n".join(
        f"- {src}: {[r.get('source', '') for r in rels[:5]]}"
        for src, rels in all_relations.items()
    )

    prompt = (
        f"Based on these knowledge domains, identify 5-10 cross-domain relationships:\n\n"
        f"{summary}\n\n"
        f"Return ONLY a JSON array of objects with fields:\n"
        f'  "source": concept from one domain\n'
        f'  "target": concept from another domain\n'
        f'  "label": relationship type\n'
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1000,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
            )
            r.raise_for_status()
            data = json.loads(r.json()["choices"][0]["message"]["content"].strip())
            if isinstance(data, list):
                return data
            return data.get("relationships", [])
    except Exception as e:
        logger.error(f"Failed to extract cross-domain relationships: {e}")
        return []


async def main(sources: list[str] | None, samples: int, output: str | None):
    conn = await psycopg.AsyncConnection.connect(DATABASE_URL)
    await register_vector_async(conn)

    # Get available sources
    if sources:
        rows = await (
            await conn.execute(
                "SELECT DISTINCT source FROM knowledge_chunks WHERE source = ANY(%s) ORDER BY source",
                (sources,),
            )
        ).fetchall()
    else:
        rows = await (
            await conn.execute(
                "SELECT DISTINCT source FROM knowledge_chunks ORDER BY source",
            )
        ).fetchall()

    available = [r[0] for r in rows]
    if not available:
        logger.error("No knowledge sources found")
        await conn.close()
        return

    logger.info(f"Generating knowledge graph for: {', '.join(available)}")

    all_mermaids = []
    all_relations = {}
    total_extracted = 0

    for source in available:
        logger.info(f"Sampling {source}...")
        chunks = await sample_chunks(conn, source, samples)
        if not chunks:
            continue

        logger.info(f"Extracting concepts from {source}...")
        relations = await extract_concepts(chunks, source)
        if not relations:
            continue

        mermaid = build_mermaid(relations, source.replace("_", " ").title())
        if mermaid:
            all_mermaids.append((source, mermaid))
        all_relations[source] = relations
        total_extracted += len(relations)
        logger.info(f"  {source}: {len(relations)} relationships extracted")

    # Cross-domain
    logger.info("Extracting cross-domain relationships...")
    cross_rels = await extract_cross_domain(all_relations)
    cross_mermaid = build_mermaid(cross_rels, "Cross-Domain") if cross_rels else ""

    await conn.close()

    # Build output
    parts = [
        f"# Knowledge Graph\n",
        f"*Generated: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"*{total_extracted} relationships from {len(available)} sources*\n",
    ]

    for source, mermaid in all_mermaids:
        parts.append(f"\n## {source.replace('_', ' ').title()}\n")
        parts.append(f"```mermaid\n{mermaid}\n```")

    if cross_mermaid:
        parts.append(f"\n## Cross-Domain Relationships\n")
        parts.append(f"```mermaid\n{cross_mermaid}\n```")

    output_text = "\n".join(parts)

    if output:
        Path(output).write_text(output_text, encoding="utf-8")
        logger.info(f"Written to {output}")
    else:
        print(output_text)

    # Always export to Joplin
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from joplin_export import export_to_joplin, ensure_notebook

        notebook_id = ensure_notebook("LLM Generated - Knowledge Graphs")
        result = export_to_joplin(
            f"Knowledge Graph — {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
            output_text,
            notebook_id=notebook_id,
        )
        logger.info(f"Exported to Joplin: {result['uri']}")
    except Exception as e:
        logger.warning(f"Failed to export to Joplin: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate knowledge graph from KB")
    parser.add_argument("--sources", nargs="+", default=None, help="Sources to include")
    parser.add_argument("--samples", type=int, default=30, help="Samples per source")
    parser.add_argument("--output", type=str, default=None, help="Output file path")
    args = parser.parse_args()

    asyncio.run(main(args.sources, args.samples, args.output))
