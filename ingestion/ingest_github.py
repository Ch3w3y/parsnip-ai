#!/usr/bin/env python3
"""
GitHub repository ingestion: fetch source files, chunk at logical boundaries,
and embed into pgvector using bge-m3 (code-optimized, 1024 dims).

Usage:
    python ingest_github.py                                          # default repos from .env
    python ingest_github.py --repos owner/repo1 owner/repo2          # explicit list
    python ingest_github.py --max-files 500                          # limit files per repo
    python ingest_github.py --from-raw                               # replay from latest raw file
    python ingest_github.py --from-raw path/to/file.jsonl.gz         # replay from specific file
"""

import argparse
import asyncio
import base64
import logging
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

from utils import (
    chunk_text,
    embed_batch,
    bulk_upsert_chunks,
    cleanup_orphan_chunks,
    get_db_connection,
    create_job,
    finish_job,
    update_job_progress,
    save_raw,
    iter_raw,
    latest_raw,
)

load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
EMBED_MODEL = os.environ.get("GITHUB_EMBED_MODEL", "bge-m3")
BATCH_SIZE = 32
MAX_FILE_SIZE_KB = 200  # skip files larger than this

# Default repos to ingest — override via --repos or GITHUB_REPOS env var
DEFAULT_REPOS = [
    "langchain-ai/langgraph",
    "anthropics/anthropic-cookbook",
    "microsoft/autogen",
]

# Language-specific function/class boundary patterns
FUNC_PATTERNS = {
    "python": re.compile(r"^(async\s+)?(def|class)\s+\w+", re.MULTILINE),
    "javascript": re.compile(
        r"^(export\s+)?(async\s+)?(function|class)\s+\w+", re.MULTILINE
    ),
    "typescript": re.compile(
        r"^(export\s+)?(async\s+)?(function|class|interface|type)\s+\w+", re.MULTILINE
    ),
    "go": re.compile(r"^func\s+(\(\w+\s+\*\w+\)\s+)?\w+", re.MULTILINE),
    "rust": re.compile(
        r"^(pub\s+)?(async\s+)?(fn|struct|enum|impl|trait)\s+\w*", re.MULTILINE
    ),
    "java": re.compile(
        r"^(public|private|protected)?\s*(static\s+)?(class|interface|void|int|String|boolean|double|float|long|byte|char|short)\s+\w+",
        re.MULTILINE,
    ),
    "c": re.compile(
        r"^(static\s+)?(void|int|char|float|double|long|short|unsigned|signed|struct)\s+\*?\w+\s*\(",
        re.MULTILINE,
    ),
    "cpp": re.compile(
        r"^(static\s+)?(void|int|char|float|double|long|short|unsigned|signed|class|struct)\s+[\*~]?\w+",
        re.MULTILINE,
    ),
}

# File extensions to skip
SKIP_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".bmp",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".exe",
    ".bin",
    ".so",
    ".dll",
    ".dylib",
    ".pyc",
    ".pyo",
    ".pyd",
    ".lock",
    ".sum",
}

# File extensions to always treat as text (single chunk regardless of size)
DOC_EXTENSIONS = {
    ".md",
    ".txt",
    ".rst",
    ".adoc",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".cfg",
    ".ini",
    ".conf",
    ".html",
    ".css",
}


def detect_language(filename: str) -> str:
    """Detect language from file extension."""
    ext = Path(filename).suffix.lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".hpp": "cpp",
    }
    return mapping.get(ext, "text")


def should_skip_file(filename: str) -> bool:
    """Check if a file should be skipped."""
    ext = Path(filename).suffix.lower()
    basename = Path(filename).name.lower()

    if ext in SKIP_EXTENSIONS:
        return True

    if basename in {
        "package-lock.json",
        "yarn.lock",
        "poetry.lock",
        "go.sum",
        "go.mod",
    }:
        return True

    if basename.startswith("."):
        return True

    return False


def chunk_code(
    content: str, language: str, max_words: int = 300, overlap_words: int = 40
) -> list[str]:
    """Chunk code at function/class boundaries, falling back to word chunks."""
    words = content.split()
    if len(words) <= max_words:
        return [content]

    pattern = FUNC_PATTERNS.get(language)
    if not pattern:
        return chunk_text(content, max_words, overlap_words)

    # Find boundary positions
    boundaries = [m.start() for m in pattern.finditer(content)]
    if not boundaries:
        return chunk_text(content, max_words, overlap_words)

    chunks = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(content)
        chunk = content[start:end].strip()
        if not chunk:
            continue
        chunk_words = chunk.split()
        if len(chunk_words) > max_words * 2:
            sub_chunks = chunk_text(chunk, max_words, overlap_words)
            chunks.extend(sub_chunks)
        else:
            chunks.append(chunk)

    if not chunks:
        return chunk_text(content, max_words, overlap_words)

    return chunks


def make_chunk_content(file_path: str, language: str, chunk_body: str) -> str:
    """Prefix chunk with file context for LLM readability."""
    return f"File: {file_path} ({language})\n\n{chunk_body}"


async def github_request(
    client: httpx.AsyncClient, url: str, params: dict | None = None
) -> httpx.Response:
    """Make authenticated GitHub API request with rate limit handling."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    for attempt in range(3):
        r = await client.get(url, params=params, headers=headers)
        if r.status_code == 403 and "rate limit" in r.text.lower():
            reset = int(r.headers.get("X-RateLimit-Reset", 0))
            wait = max(reset - int(r.headers.get("Date", "0")), 60)
            logger.warning(f"GitHub rate limit hit, waiting {wait}s")
            await asyncio.sleep(wait)
            continue
        r.raise_for_status()
        return r

    raise RuntimeError("GitHub API request failed after retries")


async def fetch_top_repos(count: int = 1000) -> list[str]:
    """Fetch the most starred repos via GitHub Search API.

    GitHub Search API returns max 1000 results (100 pages × 10 per page).
    Uses authenticated requests for higher rate limits (5000/hr vs 60/hr).
    """
    if count > 1000:
        logger.warning("GitHub Search API caps at 1000 results, limiting to 1000")
        count = min(count, 1000)

    repos = []
    page = 1
    per_page = 10
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    logger.info(f"Fetching top {count} most-starred repos from GitHub Search API...")

    async with httpx.AsyncClient(timeout=30) as client:
        while len(repos) < count and page <= 100:
            try:
                r = await client.get(
                    f"{GITHUB_API}/search/repositories",
                    params={
                        "q": "stars:>1",
                        "sort": "stars",
                        "order": "desc",
                        "per_page": per_page,
                        "page": page,
                    },
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()

                items = data.get("items", [])
                if not items:
                    break

                for item in items:
                    full_name = item.get("full_name", "")
                    stars = item.get("stargazers_count", 0)
                    if full_name:
                        repos.append(full_name)
                        if len(repos) >= count:
                            break

                total_count = data.get("total_count", 0)
                logger.info(
                    f"  Page {page}: fetched {len(items)} repos "
                    f"({len(repos)}/{count} total, {total_count:,} available)"
                )

                # Polite delay between pages
                await asyncio.sleep(3)
                page += 1

            except Exception as e:
                logger.error(f"Failed to fetch page {page}: {e}")
                break

    logger.info(f"Found {len(repos)} top repos")
    return repos


async def fetch_repo_files(repo: str, max_files: int) -> list[dict]:
    """Fetch file tree and contents for a GitHub repo. Returns list of file records."""
    owner, name = repo.split("/", 1)
    files = []

    async with httpx.AsyncClient(timeout=60) as client:
        # Get default branch
        r = await github_request(client, f"{GITHUB_API}/repos/{owner}/{name}")
        default_branch = r.json().get("default_branch", "main")
        stars = r.json().get("stargazers_count", 0)

        # Get tree
        r = await github_request(
            client,
            f"{GITHUB_API}/repos/{owner}/{name}/git/trees/{default_branch}",
            params={"recursive": "1"},
        )
        tree = r.json().get("tree", [])

        # Filter to files we want
        candidates = []
        for item in tree:
            if item["type"] != "blob":
                continue
            path = item["path"]
            if should_skip_file(path):
                continue
            if item.get("size", 0) > MAX_FILE_SIZE_KB * 1024:
                continue
            candidates.append({"path": path, "size": item["size"]})

        if len(candidates) > max_files:
            # Prioritize: docs first, then source files, then others
            def priority(f):
                ext = Path(f["path"]).suffix.lower()
                if ext in DOC_EXTENSIONS:
                    return 0
                if ext in {".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp"}:
                    return 1
                return 2

            candidates.sort(key=priority)
            candidates = candidates[:max_files]

        # Fetch file contents
        logger.info(f"  Fetching {len(candidates)} files from {repo}...")
        for i, candidate in enumerate(candidates):
            path = candidate["path"]
            try:
                r = await github_request(
                    client,
                    f"{GITHUB_API}/repos/{owner}/{name}/contents/{path}",
                    params={"ref": default_branch},
                )
                data = r.json()
                content_b64 = data.get("content", "")
                if content_b64:
                    content = base64.b64decode(content_b64).decode(
                        "utf-8", errors="replace"
                    )
                else:
                    content = ""

                # Remove NUL bytes — PostgreSQL text columns reject them
                content = content.replace("\x00", "")

                if not content.strip():
                    continue

                files.append(
                    {
                        "repo": repo,
                        "path": path,
                        "language": detect_language(path),
                        "content": content,
                        "stars": stars,
                        "branch": default_branch,
                        "url": f"https://github.com/{repo}/blob/{default_branch}/{path}",
                    }
                )
            except Exception as e:
                logger.warning(f"  Failed to fetch {path}: {e}")

            # Polite delay to avoid rate limiting
            if (i + 1) % 10 == 0:
                await asyncio.sleep(2)

    logger.info(f"  {repo}: fetched {len(files)} files ({stars} stars)")
    return files


async def fetch_all_repos(repos: list[str], max_files: int) -> list[dict]:
    """Phase 1: Fetch all repos — pure API, no DB or embedding."""
    all_files = []
    for repo in repos:
        logger.info(f"Fetching GitHub repo: {repo}")
        try:
            files = await fetch_repo_files(repo, max_files)
            all_files.extend(files)
        except Exception as e:
            logger.error(f"Failed to fetch {repo}: {e}")
    return all_files


async def process_files(files: list[dict], conn, job_id: int) -> int:
    """Phase 2: Chunk, embed, and upsert files."""
    rows = []
    total = 0
    source_chunk_counts: dict[str, int] = {}

    async def flush():
        nonlocal total
        if not rows:
            return
        texts = [r[3] for r in rows]  # content field
        embeddings = await embed_batch(texts, model=EMBED_MODEL)
        if embeddings is None:
            logger.error("Embedding failed, skipping batch.")
            rows.clear()
            return
        await bulk_upsert_chunks(conn, rows, on_conflict="update")
        total += len(rows)

        for sid, count in source_chunk_counts.items():
            await cleanup_orphan_chunks(conn, "github", sid, count)
        source_chunk_counts.clear()

        rows.clear()

    for file_rec in files:
        repo = file_rec["repo"]
        path = file_rec["path"]
        language = file_rec["language"]
        content = file_rec["content"].replace("\x00", "")
        stars = file_rec["stars"]
        url = file_rec["url"]

        ext = Path(path).suffix.lower()
        if ext in DOC_EXTENSIONS:
            chunks = chunk_text(content, 300, 40)
        else:
            chunks = chunk_code(content, language, 300, 40)

        for chunk_idx, chunk_body in enumerate(chunks):
            chunk_content = make_chunk_content(path, language, chunk_body)
            source_id = f"{repo}/{path}"
            metadata = {
                "language": language,
                "repo": repo,
                "file_path": path,
                "stars": stars,
                "url": url,
                "total_chunks": len(chunks),
                "file_type": "doc" if ext in DOC_EXTENSIONS else "source",
            }
            rows.append(
                (
                    "github",
                    source_id,
                    chunk_idx,
                    chunk_content,
                    metadata,
                    None,  # embedding placeholder
                    EMBED_MODEL,
                )
            )

            if len(rows) >= BATCH_SIZE:
                await flush()

        source_chunk_counts[f"{repo}/{path}"] = len(chunks)

    await flush()

    await update_job_progress(conn, job_id, len(files))
    return total


async def main_async(repos: list[str], max_files: int, from_raw: Path | None):
    conn = None
    job_id = None
    try:
        if from_raw:
            logger.info(f"Loading from raw file: {from_raw}")
            files = list(iter_raw(from_raw))
        else:
            files = await fetch_all_repos(repos, max_files)
            save_raw(files, "github")

        logger.info(f"Processing {len(files)} GitHub files…")
        conn = await get_db_connection()
        job_id = await create_job(conn, "github", len(files))
        await conn.commit()

        total = await process_files(files, conn, job_id)
        await finish_job(conn, job_id, "done")
        await conn.commit()
        conn = None  # prevent finally from closing again
        logger.info(f"GitHub ingestion complete: {total} chunks from {len(files)} files")
    except Exception as exc:
        logger.error(f"github ingestion failed: {exc}", exc_info=True)
        if conn is not None and job_id is not None:
            try:
                await finish_job(conn, job_id, "failed", error_message=str(exc)[:500])
                await conn.commit()
            except Exception as finish_exc:
                logger.error(f"Failed to mark job as failed: {finish_exc}")
        raise
    finally:
        if conn is not None:
            try:
                await conn.rollback()
            except Exception:
                pass
            try:
                await conn.close()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="Ingest GitHub repos into pgvector")
    parser.add_argument(
        "--repos",
        nargs="+",
        default=None,
        help="GitHub repos to ingest (owner/repo format)",
    )
    parser.add_argument(
        "--top-repos",
        type=int,
        default=0,
        help="Fetch N most-starred repos via GitHub Search API (max 1000)",
    )
    parser.add_argument("--max-files", type=int, default=200, help="Max files per repo")
    parser.add_argument(
        "--from-raw",
        metavar="PATH",
        default=None,
        help="Replay from a saved JSONL.gz file instead of hitting the API.",
    )
    args = parser.parse_args()

    repos = args.repos
    if repos:
        pass  # use explicit repos
    elif args.top_repos > 0:
        repos = asyncio.run(fetch_top_repos(args.top_repos))
        if not repos:
            logger.error("Failed to fetch top repos")
            return
    else:
        env_repos = os.environ.get("GITHUB_REPOS", "")
        if env_repos:
            repos = env_repos.split()
        else:
            repos = DEFAULT_REPOS

    raw_path = None
    if args.from_raw is not None:
        raw_path = Path(args.from_raw) if args.from_raw else latest_raw("github")
        if not raw_path or not raw_path.exists():
            logger.error(f"Raw file not found: {raw_path}")
            return

    asyncio.run(main_async(repos, args.max_files, raw_path))


if __name__ == "__main__":
    main()
