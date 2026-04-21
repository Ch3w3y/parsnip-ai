import httpx
import xml.etree.ElementTree as ET
from langchain_core.tools import tool

ARXIV_API = "https://export.arxiv.org/api/query"
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


@tool
async def arxiv_search(query: str, max_results: int = 6) -> str:
    """Search arXiv for scientific preprints and papers.

    Use this for novel research, recent scientific findings, and academic topics.
    Returns paper titles, authors, abstracts, and links.

    Args:
        query: Search terms (supports field prefixes: ti:, au:, abs:, cat:)
        max_results: Number of papers to return (default 6, max 15)
    """
    max_results = min(int(max_results), 15)

    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(ARXIV_API, params=params)
            r.raise_for_status()
        except Exception as e:
            return f"[arXiv API error: {e}]"

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        return f"[arXiv parse error: {e}]"

    entries = root.findall("atom:entry", NS)
    if not entries:
        return "No arXiv results found."

    parts = []
    for entry in entries:
        title = (entry.findtext("atom:title", "", NS) or "").replace("\n", " ").strip()
        abstract = (entry.findtext("atom:summary", "", NS) or "").replace("\n", " ").strip()
        published = (entry.findtext("atom:published", "", NS) or "")[:10]
        link = entry.findtext("atom:id", "", NS) or ""
        authors = [
            a.findtext("atom:name", "", NS)
            for a in entry.findall("atom:author", NS)
        ]
        author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")

        # Truncate long abstracts
        if len(abstract) > 600:
            abstract = abstract[:600] + "…"

        parts.append(
            f"**{title}** ({published})\n"
            f"Authors: {author_str}\n"
            f"Link: {link}\n"
            f"{abstract}"
        )

    return "\n\n---\n\n".join(parts)
