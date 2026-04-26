#!/usr/bin/env python3
# /// script
# dependencies = ["httpx>=0.27", "psycopg[binary]>=3.1", "pgvector>=0.3", "python-dotenv>=1.0"]
# ///
"""
Incrementally sync Joplin Server notes into the knowledge base as source='joplin_notes'.

Each note is stored with a joplin://x-callback-url/openNote?id=<id> deep-link URI
in metadata.url — all agent tools (kb_search, research, timeline, etc.) cite this
URI automatically alongside the note title.

Usage:
    uv run ingest_joplin.py          # incremental — only notes updated since last run
    uv run ingest_joplin.py --full   # full re-sync (re-embeds everything)

Requires (in .env):
    JOPLIN_SERVER_URL      (default http://localhost:22300)
    JOPLIN_ADMIN_EMAIL
    JOPLIN_ADMIN_PASSWORD
"""

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from urllib.parse import quote

import httpx
import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector_async

from utils import cleanup_orphan_chunks, compute_content_hash, write_to_dlq

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

JOPLIN_URL = os.environ.get("JOPLIN_SERVER_URL", "http://localhost:22300")
JOPLIN_EMAIL = os.environ.get("JOPLIN_ADMIN_EMAIL", "")
JOPLIN_PASS = os.environ.get("JOPLIN_ADMIN_PASSWORD", "")
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
DB_URL = os.environ["DATABASE_URL"]

CHUNK_WORDS = 300
OVERLAP_WORDS = 40
EMBED_BATCH = 32


# ── Text utilities ─────────────────────────────────────────────────────────────


def _chunk_text(text: str) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i : i + CHUNK_WORDS]))
        i += CHUNK_WORDS - OVERLAP_WORDS
    return chunks


def _parse_joplin_content(raw: bytes) -> tuple[str, str, str]:
    """
    Parse Joplin serialized note → (title, parent_id, body).

    Format: [body text]\n\nid: ...\nparent_id: ...\ntitle: ...\n...\ntype_: 1
    We locate the metadata block by finding 'type_:' at the end and walking
    backward through contiguous key-value lines.
    """
    text = raw.decode("utf-8", errors="replace")
    lines = text.rstrip("\n").split("\n")

    # Find the type_ line (always last in metadata block)
    type_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if re.match(r"^type_: \d+$", lines[i].strip()):
            type_idx = i
            break

    if type_idx is None:
        return "", "", text.strip()

    # Walk upward through contiguous key: value lines
    meta_start = type_idx
    while meta_start > 0 and re.match(r"^[a-zA-Z_]+: ", lines[meta_start - 1]):
        meta_start -= 1

    # Body is everything before metadata, minus trailing blank lines
    body_lines = lines[:meta_start]
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()

    meta: dict[str, str] = {}
    for line in lines[meta_start:]:
        if ": " in line:
            k, _, v = line.partition(": ")
            meta[k.strip()] = v.strip()

    return meta.get("title", ""), meta.get("parent_id", ""), "\n".join(body_lines)


# ── Ollama embedding ───────────────────────────────────────────────────────────


async def _embed_batch(http: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
    r = await http.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": texts, "truncate": True},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["embeddings"]


# ── Joplin Server API ──────────────────────────────────────────────────────────


async def _joplin_auth(http: httpx.AsyncClient) -> tuple[str, str]:
    """Authenticate and return (session_token, user_id)."""
    r = await http.post(
        f"{JOPLIN_URL}/api/sessions",
        json={"email": JOPLIN_EMAIL, "password": JOPLIN_PASS},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return data["id"], str(data["user_id"])


async def _fetch_folders(
    http: httpx.AsyncClient, token: str, user_id: str
) -> dict[str, str]:
    """Return {folder_id: title} for all folders."""
    folders: dict[str, str] = {}
    cursor = None
    while True:
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = await http.get(
            f"{JOPLIN_URL}/api/items/root/children",
            headers={"X-API-AUTH": token},
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            name: str = item.get("name", "")
            # Folder names: [folder_id]/
            m = re.match(r"^([0-9a-f]+)/$", name)
            if not m:
                continue
            folder_id = m.group(1)
            try:
                rc = await http.get(
                    f"{JOPLIN_URL}/api/items/{quote(name, safe='')}/content",
                    headers={"X-API-AUTH": token},
                    timeout=10,
                )
                if rc.status_code == 200:
                    title, _, _ = _parse_joplin_content(rc.content)
                    if title:
                        folders[folder_id] = title
            except Exception:
                pass
        cursor = data.get("cursor")
        if not data.get("has_more"):
            break
    return folders


async def _fetch_note_items(
    http: httpx.AsyncClient, token: str, user_id: str, since_ms: int
) -> list[dict]:
    """Fetch all note items (root:/*.md:) updated after since_ms."""
    items, cursor = [], None
    while True:
        params: dict = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = await http.get(
            f"{JOPLIN_URL}/api/items/root/children",
            headers={"X-API-AUTH": token},
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        for item in data.get("items", []):
            name: str = item.get("name", "")
            if re.match(r"^[0-9a-f]+\.md$", name):
                items.append(item)
        cursor = data.get("cursor")
        if not data.get("has_more"):
            break

    if since_ms:
        items = [i for i in items if i.get("updated_time", 0) > since_ms]

    return items


# ── DB helpers ─────────────────────────────────────────────────────────────────


async def _get_last_sync_ms(conn) -> int:
    row = await (
        await conn.execute(
            """
        SELECT (metadata->>'last_sync_ms')::bigint
        FROM   ingestion_jobs
        WHERE  source = 'joplin_notes' AND status = 'done'
        ORDER  BY finished_at DESC
        LIMIT  1
        """,
        )
    ).fetchone()
    return int(row[0]) if (row and row[0]) else 0


# ── Main ───────────────────────────────────────────────────────────────────────


async def main_async(full: bool = False, user_id_override: str | None = None):
    if not JOPLIN_EMAIL or not JOPLIN_PASS:
        logger.error("JOPLIN_ADMIN_EMAIL and JOPLIN_ADMIN_PASSWORD must be set in .env")
        sys.exit(1)

    async with httpx.AsyncClient() as http:
        try:
            token, user_id = await _joplin_auth(http)
        except Exception as e:
            logger.error(f"Joplin Server auth failed ({JOPLIN_URL}): {e}")
            logger.error(
                "Is the joplin-server container running? Check: docker compose ps"
            )
            sys.exit(1)

        # user_id for KB tagging: explicit override → Joplin user_id → None (org-wide)
        kb_user_id = user_id_override or user_id or None
        logger.info(f"Authenticated to Joplin Server (joplin_user={user_id}, kb_user_id={kb_user_id})")

        folders = await _fetch_folders(http, token, user_id)
        logger.info(f"Found {len(folders)} notebook(s): {list(folders.values())}")

        async with await psycopg.AsyncConnection.connect(DB_URL) as conn:
            await register_vector_async(conn)

            since_ms = 0 if full else await _get_last_sync_ms(conn)
            sync_start_ms = int(time.time() * 1000)

            note_items = await _fetch_note_items(http, token, user_id, since_ms)
            logger.info(
                f"{'Full sync' if not since_ms else 'Incremental sync'} — "
                f"{len(note_items)} note(s) to process"
            )

            if not note_items:
                logger.info("Nothing to sync.")
                return

            job_row = await (
                await conn.execute(
                    "INSERT INTO ingestion_jobs (source, status, total, started_at) "
                    "VALUES ('joplin_notes', 'running', %s, NOW()) RETURNING id",
                    (len(note_items),),
                )
            ).fetchone()
            job_id = job_row[0]
            await conn.commit()

            processed = 0
            for item in note_items:
                name: str = item.get("name", "")
                updated_ms: int = item.get("updated_time", 0)

                # Extract note ID from name: note_id.md
                match = re.match(r"^([0-9a-f]+)\.md$", name)
                if not match:
                    continue
                note_id = match.group(1)

                # Fetch note content
                try:
                    r = await http.get(
                        f"{JOPLIN_URL}/api/items/{quote(name, safe='')}/content",
                        headers={"X-API-AUTH": token},
                        timeout=15,
                    )
                    if r.status_code != 200:
                        continue
                    raw_content = r.content
                except Exception as e:
                    logger.warning(f"Could not fetch note {note_id}: {e}")
                    try:
                        await write_to_dlq(conn, source="joplin_notes", source_id=note_id,
                                           content=None, metadata={"note_id": note_id}, error=e)
                        await conn.commit()
                    except Exception:
                        pass
                    continue

                title, parent_id, body = _parse_joplin_content(raw_content)
                if not body.strip():
                    continue

                notebook_name = folders.get(parent_id, "")
                chunks = _chunk_text(f"{title}\n\n{body}" if title else body)
                if not chunks:
                    continue

                metadata = {
                    "title": title or f"Note {note_id[:8]}",
                    "note_id": note_id,
                    "notebook_id": parent_id,
                    "notebook": notebook_name,
                    "url": f"joplin://x-callback-url/openNote?id={note_id}",
                    "updated_time_ms": updated_ms,
                }

                all_embeddings: list[list[float]] = []
                for i in range(0, len(chunks), EMBED_BATCH):
                    embs = await _embed_batch(http, chunks[i : i + EMBED_BATCH])
                    all_embeddings.extend(embs)

                async with conn.transaction():
                    for idx, (chunk, emb) in enumerate(zip(chunks, all_embeddings)):
                        await conn.execute(
                            """
                            INSERT INTO knowledge_chunks
                                (source, source_id, chunk_index, content, content_hash, metadata, embedding, embedding_model, user_id)
                            VALUES ('joplin_notes', %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (source, source_id, chunk_index) DO UPDATE SET
                                content         = EXCLUDED.content,
                                content_hash    = EXCLUDED.content_hash,
                                embedding       = EXCLUDED.embedding,
                                embedding_model = EXCLUDED.embedding_model,
                                metadata        = EXCLUDED.metadata,
                                user_id         = EXCLUDED.user_id,
                                updated_at      = NOW()
                            """,
                            (
                                note_id,
                                idx,
                                chunk,
                                compute_content_hash(chunk),
                                psycopg.types.json.Jsonb(metadata),
                                emb,
                                "mxbai-embed-large",
                                kb_user_id,
                            ),
                        )

                await cleanup_orphan_chunks(conn, "joplin_notes", note_id, len(chunks))

                processed += 1
                if processed % 25 == 0:
                    logger.info(f"  {processed}/{len(note_items)} notes synced")
                    await conn.execute(
                        "UPDATE ingestion_jobs SET processed = %s WHERE id = %s",
                        (processed, job_id),
                    )
                    await conn.commit()

            await conn.execute(
                """
                UPDATE ingestion_jobs
                SET status      = 'done',
                    processed   = %s,
                    finished_at = NOW(),
                    metadata    = jsonb_build_object('last_sync_ms', %s::text)
                WHERE id = %s
                """,
                (processed, sync_start_ms, job_id),
            )
            await conn.commit()

    logger.info(f"Joplin sync complete — {processed} note(s) ingested.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Sync Joplin Server notes into the knowledge base."
    )
    parser.add_argument("--full", action="store_true", help="Re-sync all notes")
    parser.add_argument(
        "--user-id", metavar="ID", default=None,
        help="Override the user_id written to knowledge_chunks (default: Joplin user_id from auth).",
    )
    args = parser.parse_args()
    asyncio.run(main_async(full=args.full, user_id_override=args.user_id))
