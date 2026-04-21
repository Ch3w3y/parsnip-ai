import os
import httpx
from langchain_core.tools import tool

# Search backend priority when SEARCH_BACKEND=auto: searxng → tavily → brave
_BACKEND_AUTO_ORDER = ["searxng", "tavily", "brave"]


@tool
async def web_search(query: str, max_results: int = 5) -> str:
    """Search the live web for current information, news, and recent events.

    Use this when the topic requires up-to-date information not covered by
    Wikipedia or arXiv (e.g. recent events, product releases, current prices).

    Backends tried in priority order: SearXNG (self-hosted) → Tavily → Brave.
    Override with SEARCH_BACKEND env var: searxng | tavily | brave | auto.

    Args:
        query: Search query
        max_results: Number of results (default 5)
    """
    max_results = min(int(max_results), 10)

    backend = os.environ.get("SEARCH_BACKEND", "auto").lower()
    searxng_url = os.environ.get("SEARXNG_URL", "http://localhost:8080")
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    brave_key = os.environ.get("BRAVE_API_KEY", "")

    if backend == "searxng":
        return await _searxng_search(query, max_results, searxng_url)
    elif backend == "tavily":
        if not tavily_key:
            return "[Web search unavailable: SEARCH_BACKEND=tavily but TAVILY_API_KEY not set]"
        return await _tavily_search(query, max_results, tavily_key)
    elif backend == "brave":
        if not brave_key:
            return "[Web search unavailable: SEARCH_BACKEND=brave but BRAVE_API_KEY not set]"
        return await _brave_search(query, max_results, brave_key)
    else:
        # auto: try each backend in priority order, skip if unavailable
        result = await _searxng_search(query, max_results, searxng_url)
        if not result.startswith("[SearXNG"):
            return result
        if tavily_key:
            return await _tavily_search(query, max_results, tavily_key)
        if brave_key:
            return await _brave_search(query, max_results, brave_key)
        return (
            "[Web search unavailable: SearXNG unreachable and no API keys set. "
            "SearXNG runs on port 8080 — check: docker compose ps searxng. "
            "Or set TAVILY_API_KEY / BRAVE_API_KEY in .env.]"
        )


async def _searxng_search(query: str, max_results: int, base_url: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(
                f"{base_url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "language": "en",
                },
            )
            r.raise_for_status()
        except Exception as e:
            return f"[SearXNG error: {e}]"

    data = r.json()
    results = data.get("results", [])[:max_results]
    if not results:
        return "No web results found."

    parts = []
    for result in results:
        title = result.get("title", "")
        url = result.get("url", "")
        content = result.get("content", "")
        if len(content) > 500:
            content = content[:500] + "…"
        parts.append(f"**{title}**\n{url}\n{content}")

    return "\n\n---\n\n".join(parts)


async def _tavily_search(query: str, max_results: int, api_key: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "include_answer": True,
                    "search_depth": "basic",
                },
            )
            r.raise_for_status()
        except Exception as e:
            return f"[Tavily error: {e}]"

    data = r.json()
    parts = []

    if data.get("answer"):
        parts.append(f"**Summary:** {data['answer']}\n")

    for result in data.get("results", []):
        title = result.get("title", "")
        url = result.get("url", "")
        content = result.get("content", "")
        if len(content) > 500:
            content = content[:500] + "…"
        parts.append(f"**{title}**\n{url}\n{content}")

    return "\n\n---\n\n".join(parts) if parts else "No results."


async def _brave_search(query: str, max_results: int, api_key: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
            )
            r.raise_for_status()
        except Exception as e:
            return f"[Brave search error: {e}]"

    data = r.json()
    results = data.get("web", {}).get("results", [])
    if not results:
        return "No web results found."

    parts = []
    for result in results:
        title = result.get("title", "")
        url = result.get("url", "")
        description = result.get("description", "")
        parts.append(f"**{title}**\n{url}\n{description}")

    return "\n\n---\n\n".join(parts)


@tool
async def extract_webpage(url: str) -> str:
    """Fetch a URL and extract the main article content as clean text.

    Handles news articles, blog posts, research papers, and documentation pages.
    Returns the title, body text, and metadata (author, date, description).
    Use this when you need the full text of a web page that a search result points to.

    Args:
        url: The URL to fetch and extract content from
    """
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; pi-agent/1.0)"})
            r.raise_for_status()
        except Exception as e:
            return f"[Failed to fetch {url}: {e}]"

    try:
        import trafilatura
        import json as _json

        html = r.text
        if not html:
            return f"[Empty response from {url}]"

        result = trafilatura.extract(
            html,
            url=url,
            include_tables=True,
            include_links=True,
            favor_precision=True,
        )
        if not result:
            return f"[Could not extract article content from {url}. The page may be dynamically rendered or require JavaScript.]"

        metadata = trafilatura.extract(html, url=url, output_format="json")
        meta = _json.loads(metadata) if metadata else {}

        header = f"# {meta.get('title', 'Untitled')}\n"
        if meta.get("author"):
            header += f"By {meta['author']}\n"
        if meta.get("date"):
            header += f"Published: {meta['date']}\n"
        if meta.get("description"):
            header += f"\n> {meta['description']}\n"
        header += f"\nSource: {url}\n"

        content = result
        if len(content) > 8000:
            content = content[:8000] + "\n\n[... content truncated ...]"

        return f"{header}\n---\n\n{content}"

    except ImportError:
        return f"[trafilatura not installed. Install with: pip install trafilatura]"
    except Exception as e:
        return f"[Content extraction failed for {url}: {e}]"
