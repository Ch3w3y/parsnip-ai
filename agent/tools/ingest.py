"""
PDF ingestion tool — upload PDFs to the knowledge base.
Calls pdf_ingest.ingest_pdf directly (no HTTP roundtrip).
"""

import base64

import httpx
from langchain_core.tools import tool

from .pdf_ingest import ingest_pdf as _ingest_pdf_direct


@tool
async def ingest_pdf(url: str = "", content_b64: str = "", filename: str = "document.pdf",
                     title: str = "") -> str:
    """Upload a PDF document to the knowledge base for search and retrieval.

    The PDF is chunked, embedded, and stored as source='user_docs'. After indexing,
    it becomes searchable via kb_search(source='user_docs') or search_with_filters.

    Provide either a URL to download the PDF from, or base64-encoded PDF content.

    Args:
        url: URL to download the PDF from (alternative to content_b64)
        content_b64: Base64-encoded PDF content (alternative to url)
        filename: Original filename (used for source_id and metadata)
        title: Optional title override (defaults to filename)
    """
    if not url and not content_b64:
        return "[Must provide either url or content_b64]"

    if url and not content_b64:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            try:
                r = await client.get(url)
                r.raise_for_status()
                pdf_bytes = r.content
            except Exception as e:
                return f"[Failed to download PDF from {url}: {e}]"
    elif content_b64:
        try:
            pdf_bytes = base64.b64decode(content_b64)
        except Exception as e:
            return f"[Failed to decode base64 content: {e}]"
    else:
        return "[Must provide either url or content_b64]"

    if len(pdf_bytes) > 50 * 1024 * 1024:
        return f"[PDF too large: {len(pdf_bytes) / (1024*1024):.1f}MB (max 50MB)]"

    try:
        result = await _ingest_pdf_direct(filename, pdf_bytes)
        chunks = result.get("chunks", 0)
        source_id = result.get("source_id", "")
        return f"PDF ingested successfully: {chunks} chunks indexed.\nSource ID: {source_id}\nSearchable via: kb_search(source='user_docs', query='...')"
    except ValueError as e:
        return f"[Ingestion error: {e}]"
    except RuntimeError as e:
        return f"[Ingestion error: {e}]"
    except Exception as e:
        return f"[Ingestion error: {e}]"