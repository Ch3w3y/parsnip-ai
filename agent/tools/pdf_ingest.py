import hashlib
import io
import os
import logging

import httpx
import psycopg
from pgvector.psycopg import register_vector_async
from pypdf import PdfReader

logger = logging.getLogger(__name__)

CHUNK_WORDS = 400
OVERLAP_WORDS = 50
EMBED_BATCH = 32


def _chunk_text(text: str) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i: i + CHUNK_WORDS]))
        i += CHUNK_WORDS - OVERLAP_WORDS
    return chunks


async def _embed_batch(texts: list[str]) -> list[list[float]] | None:
    url = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    model = os.environ.get("EMBED_MODEL", "mxbai-embed-large")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{url}/api/embed", json={"model": model, "input": texts})
            r.raise_for_status()
            return r.json()["embeddings"]
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        return None


async def ingest_pdf(filename: str, file_bytes: bytes) -> dict:
    """
    Extract text from a PDF, chunk, embed, and store in knowledge_chunks.
    Returns a summary dict with chunk count and source_id.
    """
    # Stable source_id: filename + content hash (idempotent re-upload)
    content_hash = hashlib.sha256(file_bytes).hexdigest()[:12]
    source_id = f"{filename}::{content_hash}"

    # Extract text
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages_text = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages_text.append(text)
        full_text = "\n\n".join(pages_text)
    except Exception as e:
        raise ValueError(f"PDF extraction failed: {e}")

    if len(full_text.strip()) < 100:
        raise ValueError("PDF appears to contain no extractable text (may be scanned/image-only).")

    chunks = _chunk_text(full_text)
    if not chunks:
        raise ValueError("No content chunks produced from PDF.")

    metadata = {"filename": filename, "pages": len(reader.pages), "source_id": source_id}

    # Embed in batches
    all_embeddings: list[list[float]] = []
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i: i + EMBED_BATCH]
        embs = await _embed_batch(batch)
        if embs is None:
            raise RuntimeError("Embedding service unavailable during PDF ingestion.")
        all_embeddings.extend(embs)

    # Upsert into DB
    db_url = os.environ["DATABASE_URL"]
    inserted = 0
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await register_vector_async(conn)
        for idx, (chunk, emb) in enumerate(zip(chunks, all_embeddings)):
            async with conn.transaction():
                result = await conn.execute(
                    """
                    INSERT INTO knowledge_chunks
                        (source, source_id, chunk_index, content, metadata, embedding, embedding_model)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source, source_id, chunk_index)
                    DO UPDATE SET
                        content         = EXCLUDED.content,
                        embedding       = EXCLUDED.embedding,
                        embedding_model = EXCLUDED.embedding_model,
                        metadata        = EXCLUDED.metadata,
                        updated_at      = NOW()
                    """,
                    ("user_docs", source_id, idx, chunk,
                     psycopg.types.json.Jsonb(metadata), emb, "mxbai-embed-large"),
                )
                if result.rowcount > 0:
                    inserted += 1
        await conn.commit()

    return {
        "source_id": source_id,
        "filename": filename,
        "pages": len(reader.pages),
        "chunks": len(chunks),
        "inserted": inserted,
    }
