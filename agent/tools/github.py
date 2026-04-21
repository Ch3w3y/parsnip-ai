"""
github — Tools for interacting with the GitHub REST API.

Uses httpx.AsyncClient with GITHUB_TOKEN for authentication.
"""

import os
from typing import Optional

import httpx
from langchain_core.tools import tool

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_API = "https://api.github.com"


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


async def _github_get(path: str, params: dict | None = None) -> dict | list:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{GITHUB_API}{path}", headers=_headers(), params=params)
        r.raise_for_status()
        return r.json()


async def _github_post(path: str, json_body: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{GITHUB_API}{path}", headers=_headers(), json=json_body)
        r.raise_for_status()
        return r.json()


@tool
async def github_search_repos(
    query: str,
    sort: str = "stars",
    limit: int = 5,
) -> str:
    """Search GitHub repositories.

    Args:
        query: Search query (e.g. "vector database python")
        sort: Sort by stars, forks, or updated (default "stars")
        limit: Max results (default 5)
    """
    data = await _github_get("/search/repositories", {
        "q": query,
        "sort": sort,
        "order": "desc",
        "per_page": min(limit, 100),
    })
    items = data.get("items", [])
    if not items:
        return f"No repositories found for: {query}"
    parts = []
    for r in items[:limit]:
        parts.append(
            f"- **{r['full_name']}** ({'⭐' if sort == 'stars' else '📊'} {r.get('stargazers_count', 0)})\n"
            f"  {r.get('description', 'No description')}\n"
            f"  {r.get('html_url', '')}"
        )
    return f"Found {data.get('total_count', 0)} repos (showing {min(limit, len(items))}):\n\n" + "\n\n".join(parts)


@tool
async def github_get_file(
    owner: str,
    repo: str,
    path: str,
    branch: str = "main",
) -> str:
    """Get file contents from a GitHub repository.

    Args:
        owner: Repository owner
        repo: Repository name
        path: File path within the repository
        branch: Branch name (default "main")
    """
    data = await _github_get(f"/repos/{owner}/{repo}/contents/{path}", {"ref": branch})
    if isinstance(data, list):
        entries = []
        for item in data:
            icon = "📁" if item["type"] == "dir" else "📄"
            entries.append(f"{icon} {item['name']} ({item['type']})")
        return f"Contents of {path} in {owner}/{repo} ({branch}):\n" + "\n".join(entries)
    import base64
    content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return f"**{data.get('path', path)}** ({data.get('size', '?')} bytes):\n\n{content}"


@tool
async def github_list_commits(
    owner: str,
    repo: str,
    branch: str = "",
    limit: int = 10,
) -> str:
    """List recent commits for a GitHub repository.

    Args:
        owner: Repository owner
        repo: Repository name
        branch: Branch name (optional, default branch used if empty)
        limit: Max commits to return (default 10)
    """
    params: dict = {"per_page": min(limit, 100)}
    if branch:
        params["sha"] = branch
    data = await _github_get(f"/repos/{owner}/{repo}/commits", params)
    if not isinstance(data, list) or not data:
        return f"No commits found for {owner}/{repo}"
    parts = []
    for c in data[:limit]:
        sha = c.get("sha", "")[:7]
        msg = c.get("commit", {}).get("message", "").split("\n")[0]
        author = c.get("commit", {}).get("author", {}).get("name", "unknown")
        date = c.get("commit", {}).get("author", {}).get("date", "")[:10]
        parts.append(f"- `{sha}` {msg} — {author} ({date})")
    return f"Commits for {owner}/{repo}:\n" + "\n".join(parts)


@tool
async def github_search_code(
    query: str,
    limit: int = 5,
) -> str:
    """Search code within GitHub repositories.

    Args:
        query: Search query (e.g. "def embed_batch org:anomalyco")
        limit: Max results (default 5)
    """
    data = await _github_get("/search/code", {
        "q": query,
        "per_page": min(limit, 100),
    })
    items = data.get("items", [])
    if not items:
        return f"No code results found for: {query}"
    parts = []
    for r in items[:limit]:
        parts.append(
            f"- **{r['repository']['full_name']}** `{r.get('path', '')}`\n"
            f"  {r.get('html_url', '')}"
        )
    return f"Found {data.get('total_count', 0)} code results (showing {min(limit, len(items))}):\n\n" + "\n\n".join(parts)


@tool
async def github_list_issues(
    owner: str,
    repo: str,
    state: str = "open",
    limit: int = 10,
) -> str:
    """List issues for a GitHub repository.

    Args:
        owner: Repository owner
        repo: Repository name
        state: Filter by state — open, closed, or all (default "open")
        limit: Max issues to return (default 10)
    """
    data = await _github_get(f"/repos/{owner}/{repo}/issues", {
        "state": state,
        "per_page": min(limit, 100),
        "sort": "updated",
        "direction": "desc",
    })
    if not data:
        return f"No {state} issues found for {owner}/{repo}"
    parts = []
    for issue in data[:limit]:
        num = issue.get("number", "?")
        title = issue.get("title", "Untitled")
        labels = ", ".join(l["name"] for l in issue.get("labels", []))
        label_str = f" [{labels}]" if labels else ""
        user = issue.get("user", {}).get("login", "unknown")
        parts.append(f"- #{num} {title}{label_str} — @{user}")
    return f"Issues for {owner}/{repo} ({state}):\n" + "\n".join(parts)


@tool
async def github_create_issue(
    owner: str,
    repo: str,
    title: str,
    body: str = "",
    labels: list[str] | None = None,
) -> str:
    """Create an issue on a GitHub repository.

    Args:
        owner: Repository owner
        repo: Repository name
        title: Issue title
        body: Issue body (optional)
        labels: List of label names (optional)
    """
    payload: dict = {"title": title}
    if body:
        payload["body"] = body
    if labels:
        payload["labels"] = labels
    data = await _github_post(f"/repos/{owner}/{repo}/issues", payload)
    num = data.get("number", "?")
    url = data.get("html_url", "")
    return f"Issue #{num} created: {url}"


@tool
async def github_list_pull_requests(
    owner: str,
    repo: str,
    state: str = "open",
    limit: int = 10,
) -> str:
    """List pull requests for a GitHub repository.

    Args:
        owner: Repository owner
        repo: Repository name
        state: Filter by state — open, closed, or all (default "open")
        limit: Max PRs to return (default 10)
    """
    data = await _github_get(f"/repos/{owner}/{repo}/pulls", {
        "state": state,
        "per_page": min(limit, 100),
        "sort": "updated",
        "direction": "desc",
    })
    if not data:
        return f"No {state} pull requests found for {owner}/{repo}"
    parts = []
    for pr in data[:limit]:
        num = pr.get("number", "?")
        title = pr.get("title", "Untitled")
        user = pr.get("user", {}).get("login", "unknown")
        parts.append(f"- #{num} {title} — @{user}")
    return f"Pull requests for {owner}/{repo} ({state}):\n" + "\n".join(parts)


@tool
async def github_get_readme(
    owner: str,
    repo: str,
    branch: str = "",
) -> str:
    """Get the README of a GitHub repository.

    Args:
        owner: Repository owner
        repo: Repository name
        branch: Branch name (optional, default branch used if empty)
    """
    params: dict = {}
    if branch:
        params["ref"] = branch
    try:
        data = await _github_get(f"/repos/{owner}/{repo}/readme", params)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"No README found for {owner}/{repo}"
        raise
    import base64
    content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return f"**{data.get('path', 'README')}** ({owner}/{repo}):\n\n{content}"


@tool
async def github_get_repo_structure(
    owner: str,
    repo: str,
    branch: str = "",
    limit: int = 100,
) -> str:
    """Get the directory tree structure of a GitHub repository.

    Useful for understanding a project's layout before diving into specific files.

    Args:
        owner: Repository owner
        repo: Repository name
        branch: Branch name (optional, default branch used if empty)
        limit: Max entries to return (default 100, max 500)
    """
    ref = branch if branch else ""
    params = {"recursive": "1"}
    if ref:
        params["ref"] = ref
    try:
        data = await _github_get(f"/repos/{owner}/{repo}/git/trees/HEAD", params)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Repository {owner}/{repo} not found or no tree available"
        raise

    tree = data.get("tree", [])
    if not tree:
        return f"No tree data for {owner}/{repo}"

    truncated = data.get("truncated", False)
    entries = tree[:min(int(limit), 500)]

    dirs = []
    files = []
    for entry in entries:
        path = entry.get("path", "")
        entry_type = "📁" if entry.get("type") == "tree" else "📄"
        if entry.get("type") == "tree":
            dirs.append(f"  {entry_type} {path}/")
        else:
            size = entry.get("size", 0)
            size_str = f" ({size // 1024}KB)" if size > 1024 else ""
            files.append(f"  {entry_type} {path}{size_str}")

    header = f"**{owner}/{repo}** directory structure"
    if ref:
        header += f" (branch: {ref})"
    if truncated:
        header += f" (truncated, showing {len(entries)} of {len(tree)} entries)"

    return f"{header}:\n\n" + "\n".join(dirs + files)


@tool
async def github_create_pr(
    owner: str,
    repo: str,
    title: str,
    body: str = "",
    head: str = "",
    base: str = "main",
) -> str:
    """Create a pull request on a GitHub repository.

    Args:
        owner: Repository owner
        repo: Repository name
        title: PR title
        body: PR description (Markdown supported)
        head: The name of the branch where your changes are implemented
        base: The name of the branch you want the changes pulled into (default "main")
    """
    if not GITHUB_TOKEN:
        return "[GitHub token not configured. Set GITHUB_TOKEN in .env]"
    if not head:
        return "[Must specify the 'head' branch with your changes]"

    try:
        result = await _github_post(
            f"/repos/{owner}/{repo}/pulls",
            json_body={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )
    except httpx.HTTPStatusError as e:
        detail = e.response.text if e.response else str(e)
        return f"[Failed to create PR: {e.response.status_code} {detail}]"

    pr_number = result.get("number", "?")
    pr_url = result.get("html_url", "")
    state = result.get("state", "open")
    return f"Pull request #{pr_number} created: **{title}**\nState: {state}\nURL: {pr_url}"


@tool
async def github_list_branches(
    owner: str,
    repo: str,
    limit: int = 20,
) -> str:
    """List branches in a GitHub repository.

    Args:
        owner: Repository owner
        repo: Repository name
        limit: Max branches to return (default 20)
    """
    try:
        repo_data = await _github_get(f"/repos/{owner}/{repo}")
        default_branch = repo_data.get("default_branch", "main")
    except Exception:
        default_branch = "main"

    try:
        branches = await _github_get(f"/repos/{owner}/{repo}/branches", {"per_page": min(int(limit), 100)})
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Repository {owner}/{repo} not found"
        raise

    if not branches:
        return f"No branches found for {owner}/{repo}"

    lines = [f"**Branches for {owner}/{repo}** ({len(branches)}):\n"]
    for b in branches:
        name = b.get("name", "?")
        default = " (default)" if name == default_branch else ""
        lines.append(f"  - `{name}`{default}")

    return "\n".join(lines)