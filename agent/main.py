"""
FastAPI entry point for the pi-agent LangGraph research agent.

Endpoints:
  POST /chat          — streaming SSE chat
  POST /chat/sync     — non-streaming (for testing)
  POST /v1/chat/completions — OpenAI-compatible streaming chat (for assistant-ui)
  GET  /v1/models          — OpenAI-compatible model list
  GET  /health        — liveness probe
  GET  /threads/{id}  — fetch thread message history
  GET  /stats         — knowledge base stats
  GET  /models        — list available OpenRouter models
  POST /sessions/save — save a research session
  GET  /sessions      — list saved sessions
  GET  /sessions/{id} — get a saved session
  GET  /sessions/{id}/export — export session as markdown
"""

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import psycopg
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from pydantic import BaseModel

from tools.db_pool import init_pool, get_pool, close_all
from tools.pdf_ingest import ingest_pdf

from admin_routes import admin_router
from config import get_settings
from graph import build_graph, _load_l1_memory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

agent = None
_pool = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, _pool
    settings = get_settings()
    logger.info("Building LangGraph agent…")
    agent = await build_graph(settings.database_url)
    _pool = getattr(agent, "_db_pool", None)
    logger.info("Agent ready.")

    # Named connection pool registry (agent_kb for tools, joplin in Task 6)
    await init_pool("agent_kb", settings.database_url)
    logger.info("Named pool 'agent_kb' initialised.")

    yield

    logger.info("Shutting down.")
    await close_all()
    pool = _pool
    if pool:
        try:
            await pool.close()
            logger.info("Database connection pool closed.")
        except Exception as e:
            logger.warning(f"Error closing pool: {e}")


app = FastAPI(title="pi-agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    model: str | None = None  # override LLM per-request


class ChatResponse(BaseModel):
    thread_id: str
    content: str
    model_id: str = ""


class OpenAIChatMessage(BaseModel):
    role: str
    content: str


class OpenAIChatRequest(BaseModel):
    model: str = "parsnip-agent"
    messages: list[OpenAIChatMessage]
    stream: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    thread_id: str | None = None


class SessionSave(BaseModel):
    thread_id: str
    title: str = ""
    tools_used: list[str] = []


class NoteCreate(BaseModel):
    title: str
    content: str
    notebook_id: str | None = None
    tags: list[str] = []


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    append: bool = False
    tags: list[str] | None = None


class NotebookCreate(BaseModel):
    title: str
    parent_id: str | None = None


class HitlPublish(BaseModel):
    reviewed_content: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _serialize_message(msg) -> dict:
    if isinstance(msg, HumanMessage):
        return {"role": "user", "content": msg.content}
    if isinstance(msg, AIMessage):
        return {"role": "assistant", "content": msg.content}
    if isinstance(msg, ToolMessage):
        return {"role": "tool", "name": msg.name, "content": msg.content}
    return {"role": "unknown", "content": str(msg)}


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok", "agent_ready": agent is not None}


@app.post("/chat")
async def chat_stream(req: ChatRequest):
    """Server-Sent Events streaming chat endpoint."""
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    settings = get_settings()
    memory_ctx = await _load_l1_memory(thread_id, settings.database_url)

    # Restore conversation history from checkpointer
    existing_state = await agent.aget_state(config)
    existing_messages = (
        list(existing_state.values.get("messages", []))
        if existing_state and existing_state.values
        else []
    )

    state = {
        "messages": existing_messages + [HumanMessage(content=req.message)],
        "model_override": req.model,
        "memory_context": memory_ctx,
    }

    async def event_stream():
        try:
            async for event in agent.astream_events(state, config={**config, "recursion_limit": 50}, version="v2"):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if chunk and chunk.content:
                        yield f"data: {json.dumps({'type': 'token', 'content': chunk.content, 'thread_id': thread_id})}\n\n"

                elif kind == "on_tool_start":
                    tool_name = event.get("name", "")
                    tool_input = event["data"].get("input", {})
                    yield f"data: {json.dumps({'type': 'tool_start', 'tool': tool_name, 'input': tool_input})}\n\n"

                elif kind == "on_tool_end":
                    tool_name = event.get("name", "")
                    yield f"data: {json.dumps({'type': 'tool_end', 'tool': tool_name})}\n\n"

        except Exception as e:
            logger.exception("Stream error")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        finally:
            resolved_model = settings.resolve_model(req.model or settings.default_llm)
            yield f"data: {json.dumps({'type': 'done', 'thread_id': thread_id, 'model_id': resolved_model})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "X-Thread-ID": thread_id,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── OpenAI-compatible endpoints (for assistant-ui / Vercel AI SDK) ─────────


@app.get("/v1/models")
async def openai_list_models():
    """OpenAI-compatible /v1/models endpoint."""
    return {
        "object": "list",
        "data": [
            {
                "id": "parsnip-agent",
                "object": "model",
                "owned_by": "parsnip",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def openai_chat_completions(req: OpenAIChatRequest):
    """OpenAI-compatible /v1/chat/completions endpoint.

    Converts internal SSE events to OpenAI streaming format so that
    Vercel AI SDK's `useChat` hook can consume them directly.
    """
    # Extract the last user message
    user_message = ""
    for msg in reversed(req.messages):
        if msg.role == "user":
            user_message = msg.content
            break

    if not user_message:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "No user message found in messages array", "type": "invalid_request_error"}},
        )

    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    chatcmpl_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"

    settings = get_settings()
    memory_ctx = await _load_l1_memory(thread_id, settings.database_url)

    # Restore conversation history from checkpointer
    existing_state = await agent.aget_state(config)
    existing_messages = (
        list(existing_state.values.get("messages", []))
        if existing_state and existing_state.values
        else []
    )

    if existing_messages:
        history_messages = list(existing_messages)
        history_messages.append(HumanMessage(content=user_message))
    else:
        history_messages = []
        for msg in req.messages[:-1]:
            if msg.role == "user":
                history_messages.append(HumanMessage(content=msg.content))
            elif msg.role == "assistant":
                history_messages.append(AIMessage(content=msg.content))
        history_messages.append(HumanMessage(content=user_message))

    state = {
        "messages": history_messages,
        "model_override": None,
        "memory_context": memory_ctx,
    }

    async def openai_event_stream():
        tool_call_index = 0
        resolved_model = "parsnip-agent"

        try:
            async for event in agent.astream_events(
                state, config={**config, "recursion_limit": 50}, version="v2"
            ):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if chunk and chunk.content:
                        openai_chunk = {
                            "id": chatcmpl_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": resolved_model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant", "content": chunk.content},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(openai_chunk)}\n\n"

                elif kind == "on_tool_start":
                    tool_name = event.get("name", "")
                    tool_input = event["data"].get("input", {})
                    openai_chunk = {
                        "id": chatcmpl_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": resolved_model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "tool_calls": [
                                        {
                                            "index": tool_call_index,
                                            "id": f"call_{uuid.uuid4().hex[:24]}",
                                            "type": "function",
                                            "function": {
                                                "name": tool_name,
                                                "arguments": json.dumps(tool_input) if isinstance(tool_input, dict) else str(tool_input),
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": None,
                            }
                        ],
                    }
                    tool_call_index += 1
                    yield f"data: {json.dumps(openai_chunk)}\n\n"

                elif kind == "on_tool_end":
                    # Tool end event — no content to add in OpenAI format
                    pass

        except Exception as e:
            logger.exception("OpenAI compat stream error")
            error_chunk = {
                "id": chatcmpl_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": resolved_model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": f"\n[Error: {str(e)}]"},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
        finally:
            resolved_model = settings.resolve_model(settings.default_llm)
            # Send final chunk with finish_reason
            final_chunk = {
                "id": chatcmpl_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": resolved_model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
            yield f"data: {json.dumps(final_chunk)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        openai_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Thread-ID": thread_id,
        },
    )


@app.post("/chat/sync", response_model=ChatResponse)
async def chat_sync(req: ChatRequest):
    """Non-streaming chat for testing/debugging."""
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    settings = get_settings()
    memory_ctx = await _load_l1_memory(thread_id, settings.database_url)

    # Restore conversation history from checkpointer
    existing_state = await agent.aget_state(config)
    existing_messages = (
        list(existing_state.values.get("messages", []))
        if existing_state and existing_state.values
        else []
    )

    state = {
        "messages": existing_messages + [HumanMessage(content=req.message)],
        "model_override": req.model,
        "memory_context": memory_ctx,
    }

    result = await agent.ainvoke(state, config={**config, "recursion_limit": 50})
    last = result["messages"][-1]
    
    # Resolve the model ID for logging/UI
    resolved_model = settings.resolve_model(req.model or settings.default_llm)
    
    return ChatResponse(thread_id=thread_id, content=last.content, model_id=resolved_model)


@app.get("/threads/{thread_id}")
async def get_thread(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    try:
        state = await agent.aget_state(config)
        if not state or not state.values:
            raise HTTPException(status_code=404, detail="Thread not found")
        messages = [_serialize_message(m) for m in state.values.get("messages", [])]

        title = ""
        for m in state.values.get("messages", []):
            if isinstance(m, HumanMessage):
                title = m.content[:80]
                break

        settings = get_settings()
        try:
            async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
                await conn.execute(
                    "INSERT INTO thread_metadata (thread_id, title, message_count, updated_at) "
                    "VALUES (%s, %s, %s, NOW()) "
                    "ON CONFLICT (thread_id) DO UPDATE SET title = %s, message_count = %s, updated_at = NOW()",
                    (thread_id, title, len(messages), title, len(messages)),
                )
                await conn.commit()
        except Exception:
            pass

        return {"thread_id": thread_id, "messages": messages}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/threads")
async def list_threads(limit: int = 50, offset: int = 0):
    """List conversation threads — fast SQL-only query with cached titles."""
    settings = get_settings()
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            cur = await conn.execute(
                "SELECT c.thread_id, tm.title, tm.message_count, tm.created_at "
                "FROM (SELECT DISTINCT ON (thread_id) thread_id, checkpoint_id "
                "      FROM checkpoints ORDER BY thread_id, checkpoint_id DESC "
                "      LIMIT %s OFFSET %s) c "
                "LEFT JOIN thread_metadata tm ON c.thread_id = tm.thread_id "
                "ORDER BY tm.created_at DESC NULLS LAST, c.checkpoint_id DESC",
                (limit, offset),
            )
            rows = await cur.fetchall()

            cur2 = await conn.execute(
                "SELECT COUNT(DISTINCT thread_id) FROM checkpoints"
            )
            total_row = await cur2.fetchone()
            total = total_row[0] if total_row else 0

            threads = []
            for r in rows:
                threads.append({
                    "id": r[0],
                    "title": r[1] or "",
                    "message_count": r[2] or 0,
                    "created_at": str(r[3]) if r[3] else None,
                })

            return {"threads": threads, "total": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/memories")
async def list_memories(
    category: str = "",
    min_importance: int = 0,
    limit: int = 50,
    search: str = "",
):
    """List long-term memories, optionally filtered by category, importance, and content search."""
    settings = get_settings()
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            conditions = ["deleted_at IS NULL"]
            params: list = []
            if category:
                conditions.append("category = %s")
                params.append(category)
            if min_importance > 0:
                conditions.append("importance >= %s")
                params.append(min_importance)
            if search:
                escaped_search = (
                    search.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                conditions.append("content ILIKE %s ESCAPE E'\\\\'")
                params.append(f"%{escaped_search}%")
            params.append(limit)

            where = " AND ".join(conditions)
            cur = await conn.execute(
                f"SELECT id, category, content, importance, created_at, updated_at "
                f"FROM agent_memories WHERE {where} ORDER BY importance DESC, created_at DESC LIMIT %s",
                params,
            )
            rows = await cur.fetchall()

            return {
                "memories": [
                    {
                        "id": r[0],
                        "category": r[1],
                        "content": r[2],
                        "importance": r[3],
                        "created_at": str(r[4]),
                        "updated_at": str(r[5]) if r[5] else None,
                    }
                    for r in rows
                ],
                "total": len(rows),
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/memories/{memory_id}")
async def delete_memory_endpoint(memory_id: int):
    """Soft-delete a memory by ID."""
    settings = get_settings()
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            result = await conn.execute(
                "UPDATE agent_memories SET deleted_at = NOW() WHERE id = %s AND deleted_at IS NULL",
                (memory_id,),
            )
            await conn.commit()
            if result.rowcount == 0:
                raise HTTPException(
                    status_code=404, detail="Memory not found or already deleted."
                )
            return {"status": "ok", "message": f"Memory {memory_id} deleted."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
async def get_stats():
    """Return knowledge base statistics."""
    settings = get_settings()
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            cur = await conn.execute(
                """
                SELECT source, COUNT(*) as chunks, COUNT(DISTINCT source_id) as articles
                FROM knowledge_chunks
                GROUP BY source
                ORDER BY chunks DESC
                """
            )
            rows = await cur.fetchall()
            cur2 = await conn.execute(
                "SELECT source, status, processed, total, started_at, finished_at "
                "FROM ingestion_jobs ORDER BY id DESC LIMIT 10"
            )
            jobs = await cur2.fetchall()

        return {
            "knowledge_base": [
                {"source": r[0], "chunks": r[1], "articles": r[2]} for r in rows
            ],
            "ingestion_jobs": [
                {
                    "source": j[0],
                    "status": j[1],
                    "processed": j[2],
                    "total": j[3],
                    "started_at": str(j[4]),
                    "finished_at": str(j[5]),
                }
                for j in jobs
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ingestion/status")
async def get_ingestion_status():
    """Return the overall ingestion and migration health snapshot.

    Covers:
      - Wikipedia source_id schema migration (remaining rows, PID, readiness)
      - Wikipedia bulk dump seed (running state, chunks/articles in KB, last job)
      - Recent ingestion jobs across all sources
      - Scheduled next-run hints for recurring sources
    """
    from ingestion_status import get_ingestion_overview, to_dict

    try:
        overview = await get_ingestion_overview()
        return to_dict(overview)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ingestion/migration")
async def get_migration_detail():
    """Deep-dive on the Wikipedia source_id migration only."""
    from ingestion_status import get_migration_status

    try:
        m = await get_migration_status()
        return {
            "running": m.running,
            "pid": m.pid,
            "rows_remaining": m.rows_remaining,
            "anomalous_rows": m.anomalous_rows,
            "ready_for_ingestion": m.ready_for_ingestion,
            "last_log_tail": m.last_log_tail,
            "note": (
                "If rows_remaining > 0 and running is False, the migration exited early. "
                "Restart with: nohup python scripts/migrate_wiki_source_ids.py --batch-size 50000 ... &"
            ),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ingestion/wikipedia")
async def get_wikipedia_detail():
    """Deep-dive on the Wikipedia bulk seed and incremental update state."""
    from ingestion_status import get_wikipedia_bulk_status

    try:
        b = await get_wikipedia_bulk_status()
        return {
            "bulk_ingest_running": b.running,
            "bulk_ingest_pid": b.pid,
            "chunks_in_kb": b.chunks_in_kb,
            "articles_in_kb": b.articles_in_kb,
            "last_job_status": b.last_job_status,
            "last_job_processed": b.last_job_processed,
            "resume_safe": (not b.running) and (b.last_job_processed or 0) > 0,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/kb-search")
async def kb_search_endpoint(q: str, source: str | None = None, days: int | None = None, limit: int = 8):
    """Search the knowledge base via hybrid semantic + full-text RRF.

    Returns JSON array of {source_id, chunk_index, content, metadata, source, score}.
    """
    limit = min(limit, 20)
    from tools.embed import get_embedding
    from pgvector.psycopg import register_vector_async

    embed_model = "bge-m3" if source == "github" else "mxbai-embed-large"
    try:
        embedding = await get_embedding(q, model=embed_model)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Embedding unavailable: {e}")

    settings = get_settings()

    where_parts = []
    if source:
        where_parts.append(f"source = '{source.replace(chr(39), '')}'")
    if days:
        where_parts.append(f"created_at >= NOW() - {int(days)} * INTERVAL '1 day'")
    where_clause = ("AND " + " AND ".join(where_parts)) if where_parts else ""

    query_sql = f"""
        WITH vector_results AS (
            SELECT source_id, chunk_index, content, metadata, source,
                   ROW_NUMBER() OVER (ORDER BY embedding <=> %s::vector) AS rank
            FROM knowledge_chunks
            WHERE embedding IS NOT NULL
            {where_clause}
            LIMIT 60
        ),
        fts_results AS (
            SELECT source_id, chunk_index, content, metadata, source,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank(to_tsvector('english', content),
                                        plainto_tsquery('english', %s)) DESC
                   ) AS rank
            FROM knowledge_chunks
            WHERE to_tsvector('english', content) @@ plainto_tsquery('english', %s)
            {where_clause}
            LIMIT 60
        ),
        combined AS (
            SELECT
                COALESCE(v.source_id, f.source_id)     AS source_id,
                COALESCE(v.chunk_index, f.chunk_index)  AS chunk_index,
                COALESCE(v.content,    f.content)       AS content,
                COALESCE(v.metadata,   f.metadata)      AS metadata,
                COALESCE(v.source,     f.source)        AS source,
                COALESCE(1.0 / (60 + v.rank), 0.0)
                    + COALESCE(1.0 / (60 + f.rank), 0.0) AS rrf_score
            FROM vector_results v
            FULL OUTER JOIN fts_results f USING (source_id, chunk_index)
        )
        SELECT source_id, chunk_index, content, metadata, source, rrf_score
        FROM combined
        ORDER BY rrf_score DESC
        LIMIT %s
    """

    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            await register_vector_async(conn)
            cur = await conn.execute(query_sql, [embedding, q, q, limit])
            rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    if not rows:
        return []

    return [
        {
            "source_id": r[0],
            "chunk_index": r[1],
            "content": r[2][:500] if r[2] else "",
            "metadata": r[3],
            "source": r[4],
            "score": round(float(r[5]), 4),
        }
        for r in rows
    ]


@app.post("/upload/pdf")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF document to the personal knowledge base.

    The document is chunked, embedded via Ollama, and stored as source='user_docs'.
    Re-uploading the same file is idempotent (matched by content hash).
    The agent can then search it via: kb_search(query, source='user_docs')
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    file_bytes = await file.read()
    if len(file_bytes) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(status_code=413, detail="PDF too large (max 50MB).")

    try:
        result = await ingest_pdf(file.filename, file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {
        "status": "ok",
        "message": f"Ingested '{result['filename']}': {result['chunks']} chunks from {result['pages']} pages.",
        **result,
    }


@app.post("/sessions/save")
async def save_session(req: SessionSave):
    """Save a research session to agent_memories with metadata."""
    settings = get_settings()
    config = {"configurable": {"thread_id": req.thread_id}}
    try:
        state = await agent.aget_state(config)
        if not state or not state.values:
            raise HTTPException(status_code=404, detail="Thread not found")

        messages = state.values.get("messages", [])
        message_count = len(
            [m for m in messages if isinstance(m, (HumanMessage, AIMessage))]
        )

        tools_used = req.tools_used or []
        for m in messages:
            if isinstance(m, ToolMessage) and m.name:
                if m.name not in tools_used:
                    tools_used.append(m.name)

        content = json.dumps(
            {
                "thread_id": req.thread_id,
                "title": req.title,
                "message_count": message_count,
                "tools_used": tools_used,
                "messages": [_serialize_message(m) for m in messages],
            }
        )

        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            await conn.execute(
                "INSERT INTO agent_memories (category, content, importance, metadata) "
                "VALUES (%s, %s, %s, %s)",
                (
                    "research_session",
                    content,
                    3,
                    psycopg.types.json.Jsonb(
                        {
                            "title": req.title,
                            "thread_id": req.thread_id,
                            "message_count": message_count,
                            "tools_used": tools_used,
                        }
                    ),
                ),
            )
            await conn.commit()

        return {
            "status": "saved",
            "thread_id": req.thread_id,
            "title": req.title,
            "message_count": message_count,
            "tools_used": tools_used,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions")
async def list_sessions(limit: int = 50):
    """List saved research sessions."""
    settings = get_settings()
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            cur = await conn.execute(
                "SELECT id, content, metadata, created_at "
                "FROM agent_memories "
                "WHERE category = 'research_session' AND deleted_at IS NULL "
                "ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()

            sessions = []
            for row in rows:
                meta = row[2] or {}
                sessions.append(
                    {
                        "id": row[0],
                        "title": meta.get("title", "Untitled"),
                        "thread_id": meta.get("thread_id", ""),
                        "message_count": meta.get("message_count", 0),
                        "tools_used": meta.get("tools_used", []),
                        "created_at": str(row[3]),
                    }
                )

            return {"sessions": sessions, "total": len(sessions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions/{session_id}")
async def get_session(session_id: int):
    """Get a saved session with full conversation."""
    settings = get_settings()
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            cur = await conn.execute(
                "SELECT id, content, metadata, created_at "
                "FROM agent_memories WHERE id = %s AND category = 'research_session'",
                (session_id,),
            )
            row = await cur.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Session not found")

            data = json.loads(row[1])
            return {
                "id": row[0],
                "title": (row[2] or {}).get("title", "Untitled"),
                "thread_id": data.get("thread_id", ""),
                "message_count": data.get("message_count", 0),
                "tools_used": data.get("tools_used", []),
                "created_at": str(row[3]),
                "messages": data.get("messages", []),
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions/{session_id}/export")
async def export_session(session_id: int):
    """Export a session as markdown."""
    settings = get_settings()
    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            cur = await conn.execute(
                "SELECT content, metadata, created_at "
                "FROM agent_memories WHERE id = %s AND category = 'research_session'",
                (session_id,),
            )
            row = await cur.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Session not found")

            data = json.loads(row[0])
            meta = row[1] or {}
            title = meta.get("title", "Untitled")
            created = str(row[2])[:10]

            md_parts = [
                f"# {title}",
                f"\n*Session: {data.get('thread_id', '')} | Created: {created}*",
                f"\n*Tools used: {', '.join(meta.get('tools_used', []))}*\n",
            ]

            for msg in data.get("messages", []):
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user":
                    md_parts.append(f"\n## User\n\n{content}\n")
                elif role == "assistant":
                    md_parts.append(f"\n## Assistant\n\n{content}\n")
                elif role == "tool":
                    md_parts.append(f"\n> *Tool: {msg.get('name', '')}*\n")

            return {"markdown": "\n".join(md_parts), "title": title}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/models")
async def list_models(filter: str = ""):
    """List available OpenRouter models, optionally filtered by substring.

    Examples:
      GET /models               — all models
      GET /models?filter=gemini — only Google Gemini models
      GET /models?filter=claude — only Anthropic Claude models
      GET /models?filter=embed  — only embedding models
    """
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            )
            r.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"OpenRouter API error: {e}")

    models = r.json().get("data", [])
    if filter:
        models = [m for m in models if filter.lower() in m["id"].lower()]

    return {
        "count": len(models),
        "current_defaults": {
            "default_llm": settings.default_llm,
            "research_llm": settings.research_llm,
            "embed_model": settings.embed_model,
        },
        "models": [
            {
                "id": m["id"],
                "name": m.get("name", ""),
                "context_length": m.get("context_length"),
                "pricing": m.get("pricing", {}),
            }
            for m in sorted(models, key=lambda x: x["id"])
        ],
    }


# ── Notes CRUD & HITL endpoints ─────────────────────────────────────────────────


@app.get("/notes")
async def list_notes(
    notebook_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
) -> dict:
    """List notes with optional notebook filter and full-text search."""
    pool = get_pool("agent_kb")

    conditions = ["n.deleted_at IS NULL"]
    params: list = []

    if notebook_id:
        try:
            params.append(uuid.UUID(notebook_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid notebook_id UUID")
        conditions.append("n.notebook_id = %s")

    if search:
        ilike_pat = f"%{search}%"
        params.append(ilike_pat)
        conditions.append(
            "(n.title ILIKE %s OR n.content ILIKE %s "
            "OR to_tsvector('english', n.content) @@ plainto_tsquery('english', %s))"
        )
        params.append(ilike_pat)
        params.append(search)

    where = " AND ".join(conditions)

    async with pool.connection() as conn:
        cur = await conn.execute(
            f"SELECT COUNT(*) AS total FROM notes n WHERE {where}",
            tuple(params),
        )
        total_row = await cur.fetchone()
        total = total_row["total"] if total_row else 0

        cur2 = await conn.execute(
            f"""
            SELECT n.id, n.title, n.notebook_id, n.created_at, n.updated_at,
                   nb.title AS notebook_title,
                   COALESCE(
                       json_agg(DISTINCT t.name) FILTER (WHERE t.name IS NOT NULL),
                       '[]'
                   ) AS tags
            FROM notes n
            LEFT JOIN notebooks nb ON nb.id = n.notebook_id
            LEFT JOIN note_tags nt ON nt.note_id = n.id
            LEFT JOIN tags t ON t.id = nt.tag_id
            WHERE {where}
            GROUP BY n.id, nb.title
            ORDER BY n.updated_at DESC
            LIMIT %s OFFSET %s
            """,
            tuple([*params, limit, offset]),
        )
        rows = await cur2.fetchall()

    notes = [
        {
            "id": str(row["id"]),
            "title": row["title"],
            "notebook_id": str(row["notebook_id"]) if row["notebook_id"] else None,
            "notebook_title": row["notebook_title"],
            "tags": row["tags"] if row["tags"] else [],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]

    return {"notes": notes, "total": total}


@app.get("/notes/{note_id}")
async def get_note(note_id: str) -> dict:
    """Get a single note with full content, tags, and resources."""
    pool = get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid note_id UUID")

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT n.id, n.title, n.content, n.notebook_id,
                   n.created_at, n.updated_at,
                   nb.title AS notebook_title
            FROM notes n
            LEFT JOIN notebooks nb ON nb.id = n.notebook_id
            WHERE n.id = %s AND n.deleted_at IS NULL
            """,
            (nid,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Note not found")

        cur2 = await conn.execute(
            """
            SELECT t.id, t.name
            FROM note_tags nt
            JOIN tags t ON t.id = nt.tag_id
            WHERE nt.note_id = %s
            ORDER BY t.name
            """,
            (nid,),
        )
        tag_rows = await cur2.fetchall()

        cur3 = await conn.execute(
            """
            SELECT id, filename, mime_type, size
            FROM note_resources
            WHERE note_id = %s
            ORDER BY created_at
            """,
            (nid,),
        )
        res_rows = await cur3.fetchall()

    return {
        "id": str(row["id"]),
        "title": row["title"],
        "content": row["content"],
        "notebook_id": str(row["notebook_id"]) if row["notebook_id"] else None,
        "notebook_title": row["notebook_title"],
        "tags": [{"id": str(t["id"]), "name": t["name"]} for t in tag_rows],
        "resources": [
            {
                "id": str(r["id"]),
                "filename": r["filename"],
                "mime_type": r["mime_type"],
                "size": r["size"],
            }
            for r in res_rows
        ],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


@app.post("/notes", status_code=201)
async def create_note(req: NoteCreate) -> dict:
    """Create a new note with optional tags and auto-index into knowledge_chunks."""
    pool = get_pool("agent_kb")
    note_id = uuid.uuid4()

    nb_uuid: uuid.UUID | None = None
    if req.notebook_id:
        try:
            nb_uuid = uuid.UUID(req.notebook_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid notebook_id UUID")

    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO notes (id, title, content, notebook_id)
            VALUES (%s, %s, %s, %s)
            """,
            (note_id, req.title, req.content, nb_uuid),
        )

        for tag_name in req.tags:
            tag_cur = await conn.execute(
                "SELECT id FROM tags WHERE name = %s",
                (tag_name,),
            )
            tag_row = await tag_cur.fetchone()
            if tag_row:
                tag_id = tag_row["id"]
            else:
                tag_id = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO tags (id, name) VALUES (%s, %s)",
                    (tag_id, tag_name),
                )
            await conn.execute(
                "INSERT INTO note_tags (note_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (note_id, tag_id),
            )

        nb_title: str | None = None
        if nb_uuid:
            nb_cur = await conn.execute(
                "SELECT title FROM notebooks WHERE id = %s",
                (nb_uuid,),
            )
            nb_row = await nb_cur.fetchone()
            nb_title = nb_row["title"] if nb_row else None

    # Auto-index into knowledge_chunks (best-effort)
    try:
        from pgvector.psycopg import register_vector_async
        from psycopg.types.json import Jsonb
        from tools.embed import get_embedding

        combined = f"{req.title}\n\n{req.content}"
        source_id = f"{note_id}::chunk_0"
        meta: dict = {"note_id": str(note_id), "title": req.title}
        if req.notebook_id:
            meta["notebook_id"] = req.notebook_id

        try:
            embedding = await get_embedding(combined)
        except Exception as exc:
            logger.warning("Embedding failed for new note %s: %s", note_id, exc)
        else:
            async with pool.connection() as conn:
                await register_vector_async(conn)
                await conn.execute(
                    """
                    INSERT INTO knowledge_chunks
                        (source, source_id, chunk_index, content, metadata, embedding)
                    VALUES (%s, %s, 0, %s, %s, %s)
                    ON CONFLICT (source, source_id, chunk_index)
                    DO UPDATE SET
                        content    = EXCLUDED.content,
                        metadata   = EXCLUDED.metadata,
                        embedding  = EXCLUDED.embedding,
                        updated_at = NOW()
                    """,
                    ("user_notes", source_id, combined, Jsonb(meta), embedding),
                )
    except Exception as exc:
        logger.warning("KB index upsert failed for new note %s: %s", note_id, exc)

    return {
        "id": str(note_id),
        "title": req.title,
        "content": req.content,
        "notebook_id": req.notebook_id,
        "notebook_title": nb_title,
        "tags": req.tags,
        "created_at": None,
        "updated_at": None,
    }


@app.put("/notes/{note_id}")
async def update_note(note_id: str, req: NoteUpdate) -> dict:
    """Update a note's title/content/tags and re-index into knowledge_chunks."""
    pool = get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid note_id UUID")

    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT title, content, notebook_id FROM notes WHERE id = %s AND deleted_at IS NULL",
            (nid,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Note not found")

        new_title = req.title if req.title is not None else row["title"]
        existing_body = row["content"]
        if req.content is not None:
            new_body = (existing_body + "\n\n" + req.content) if req.append else req.content
        else:
            new_body = existing_body

        await conn.execute(
            """
            UPDATE notes SET title = %s, content = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (new_title, new_body, nid),
        )

        if req.tags is not None:
            await conn.execute(
                "DELETE FROM note_tags WHERE note_id = %s",
                (nid,),
            )
            for tag_name in req.tags:
                tag_cur = await conn.execute(
                    "SELECT id FROM tags WHERE name = %s",
                    (tag_name,),
                )
                tag_row = await tag_cur.fetchone()
                if tag_row:
                    tag_id = tag_row["id"]
                else:
                    tag_id = uuid.uuid4()
                    await conn.execute(
                        "INSERT INTO tags (id, name) VALUES (%s, %s)",
                        (tag_id, tag_name),
                    )
                await conn.execute(
                    "INSERT INTO note_tags (note_id, tag_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (nid, tag_id),
                )

        nb_title: str | None = None
        if row["notebook_id"]:
            nb_cur = await conn.execute(
                "SELECT title FROM notebooks WHERE id = %s",
                (row["notebook_id"],),
            )
            nb_row = await nb_cur.fetchone()
            nb_title = nb_row["title"] if nb_row else None

        tag_cur2 = await conn.execute(
            """
            SELECT t.id, t.name
            FROM note_tags nt
            JOIN tags t ON t.id = nt.tag_id
            WHERE nt.note_id = %s
            ORDER BY t.name
            """,
            (nid,),
        )
        tag_rows = await tag_cur2.fetchall()

    # Re-index into knowledge_chunks
    if req.content is not None or req.title is not None:
        try:
            from pgvector.psycopg import register_vector_async
            from psycopg.types.json import Jsonb
            from tools.embed import get_embedding

            combined = f"{new_title}\n\n{new_body}"
            source_id = f"{note_id}::chunk_0"
            meta: dict = {"note_id": note_id, "title": new_title}
            if row["notebook_id"]:
                meta["notebook_id"] = str(row["notebook_id"])

            try:
                embedding = await get_embedding(combined)
            except Exception as exc:
                logger.warning("Embedding failed for note %s: %s", note_id, exc)
            else:
                async with pool.connection() as conn:
                    await register_vector_async(conn)
                    await conn.execute(
                        """
                        INSERT INTO knowledge_chunks
                            (source, source_id, chunk_index, content, metadata, embedding)
                        VALUES (%s, %s, 0, %s, %s, %s)
                        ON CONFLICT (source, source_id, chunk_index)
                        DO UPDATE SET
                            content    = EXCLUDED.content,
                            metadata   = EXCLUDED.metadata,
                            embedding  = EXCLUDED.embedding,
                            updated_at = NOW()
                        """,
                        ("user_notes", source_id, combined, Jsonb(meta), embedding),
                    )
        except Exception as exc:
            logger.warning("KB index upsert failed for note %s: %s", note_id, exc)

    return {
        "id": note_id,
        "title": new_title,
        "content": new_body,
        "notebook_id": str(row["notebook_id"]) if row["notebook_id"] else None,
        "notebook_title": nb_title,
        "tags": [{"id": str(t["id"]), "name": t["name"]} for t in tag_rows],
        "created_at": None,
        "updated_at": None,
    }


@app.delete("/notes/{note_id}")
async def delete_note(note_id: str) -> dict:
    """Soft-delete a note by setting deleted_at."""
    pool = get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid note_id UUID")

    async with pool.connection() as conn:
        del_cur = await conn.execute(
            "SELECT id FROM notes WHERE id = %s AND deleted_at IS NULL",
            (nid,),
        )
        row = await del_cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Note not found or already deleted")

        await conn.execute(
            "UPDATE notes SET deleted_at = NOW(), updated_at = NOW() WHERE id = %s",
            (nid,),
        )

        like_pattern = f"{note_id}::chunk_%"
        await conn.execute(
            """
            UPDATE knowledge_chunks
            SET updated_at = NOW()
            WHERE source = 'user_notes' AND source_id LIKE %s
            """,
            (like_pattern,),
        )

    return {"deleted": True, "note_id": note_id}


@app.get("/notebooks")
async def list_notebooks() -> dict:
    """List all notebooks with note counts."""
    pool = get_pool("agent_kb")

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT n.id, n.title, n.parent_id, n.created_at, n.updated_at,
                   (SELECT COUNT(*) FROM notes WHERE notebook_id = n.id AND deleted_at IS NULL) AS note_count
            FROM notebooks n
            ORDER BY n.title
            """
        )
        rows = await cur.fetchall()

    notebooks = [
        {
            "id": str(row["id"]),
            "title": row["title"],
            "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
            "note_count": row["note_count"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]

    return {"notebooks": notebooks}


@app.post("/notebooks", status_code=201)
async def create_notebook(req: NotebookCreate) -> dict:
    """Create a new notebook."""
    pool = get_pool("agent_kb")
    nb_id = uuid.uuid4()

    parent_uuid: uuid.UUID | None = None
    if req.parent_id:
        try:
            parent_uuid = uuid.UUID(req.parent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid parent_id UUID")

    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO notebooks (id, title, parent_id)
            VALUES (%s, %s, %s)
            """,
            (nb_id, req.title, parent_uuid),
        )

    return {
        "id": str(nb_id),
        "title": req.title,
        "parent_id": req.parent_id,
        "note_count": 0,
    }


@app.get("/tags")
async def list_tags() -> dict:
    """List all tags with note counts."""
    pool = get_pool("agent_kb")

    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT t.id, t.name,
                   (SELECT COUNT(*) FROM note_tags WHERE tag_id = t.id) AS note_count
            FROM tags t
            ORDER BY t.name
            """
        )
        rows = await cur.fetchall()

    tags = [
        {
            "id": str(row["id"]),
            "name": row["name"],
            "note_count": row["note_count"],
        }
        for row in rows
    ]

    return {"tags": tags}


@app.post("/notes/{note_id}/resources", status_code=201)
async def upload_resource(note_id: str, file: UploadFile = File(...)) -> dict:
    """Upload a file attachment to a note."""
    pool = get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid note_id UUID")

    async with pool.connection() as conn:
        note_cur = await conn.execute(
            "SELECT id FROM notes WHERE id = %s AND deleted_at IS NULL",
            (nid,),
        )
        note = await note_cur.fetchone()
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")

    resource_id = uuid.uuid4()
    raw = await file.read()
    filename = file.filename or "untitled"
    mime_type = file.content_type or "application/octet-stream"

    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO note_resources (id, note_id, filename, mime_type, content, size)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (resource_id, nid, filename, mime_type, raw, len(raw)),
        )

    return {
        "id": str(resource_id),
        "filename": filename,
        "mime_type": mime_type,
        "size": len(raw),
    }


@app.get("/notes/{note_id}/hitl")
async def get_hitl_session(note_id: str) -> dict:
    """Get HITL session for a note."""
    pool = get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid note_id UUID")

    async with pool.connection() as conn:
        note_cur = await conn.execute(
            "SELECT id FROM notes WHERE id = %s AND deleted_at IS NULL",
            (nid,),
        )
        note = await note_cur.fetchone()
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")

        sess_cur = await conn.execute(
            "SELECT * FROM hitl_sessions WHERE note_id = %s ORDER BY id DESC LIMIT 1",
            (nid,),
        )
        session = await sess_cur.fetchone()

    if not session:
        raise HTTPException(status_code=404, detail="No HITL session found for this note")

    return {
        "id": session["id"],
        "note_id": str(session["note_id"]),
        "last_llm_content": session["last_llm_content"],
        "last_llm_hash": session["last_llm_hash"],
        "cycle_count": session["cycle_count"],
        "status": session["status"],
        "created_at": session["created_at"].isoformat() if session["created_at"] else None,
        "updated_at": session["updated_at"].isoformat() if session["updated_at"] else None,
    }


@app.post("/notes/{note_id}/hitl/publish")
async def publish_hitl_review(note_id: str, req: HitlPublish) -> dict:
    """Publish reviewed content for a HITL-tracked note."""
    pool = get_pool("agent_kb")
    try:
        nid = uuid.UUID(note_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid note_id UUID")

    async with pool.connection() as conn:
        note_cur = await conn.execute(
            "SELECT id, title FROM notes WHERE id = %s AND deleted_at IS NULL",
            (nid,),
        )
        note = await note_cur.fetchone()
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")

        sess_cur = await conn.execute(
            "SELECT * FROM hitl_sessions WHERE note_id = %s ORDER BY id DESC LIMIT 1",
            (nid,),
        )
        session = await sess_cur.fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="No HITL session found for this note")

        await conn.execute(
            """
            UPDATE notes SET content = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (req.reviewed_content, nid),
        )

        import hashlib

        new_hash = hashlib.sha256(req.reviewed_content.encode()).hexdigest()[:16]
        new_cycle = session["cycle_count"] + 1

        await conn.execute(
            """
            UPDATE hitl_sessions
            SET last_llm_content = %s, last_llm_hash = %s,
                cycle_count = %s, status = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (req.reviewed_content, new_hash, new_cycle, "published", session["id"]),
        )

    # Re-index into knowledge_chunks
    try:
        from pgvector.psycopg import register_vector_async
        from psycopg.types.json import Jsonb
        from tools.embed import get_embedding

        title = note["title"]
        combined = f"{title}\n\n{req.reviewed_content}"
        source_id = f"{note_id}::chunk_0"
        meta: dict = {"note_id": note_id, "title": title}

        try:
            embedding = await get_embedding(combined)
        except Exception as exc:
            logger.warning("Embedding failed for published note %s: %s", note_id, exc)
        else:
            async with pool.connection() as conn:
                await register_vector_async(conn)
                await conn.execute(
                    """
                    INSERT INTO knowledge_chunks
                        (source, source_id, chunk_index, content, metadata, embedding)
                    VALUES (%s, %s, 0, %s, %s, %s)
                    ON CONFLICT (source, source_id, chunk_index)
                    DO UPDATE SET
                        content    = EXCLUDED.content,
                        metadata   = EXCLUDED.metadata,
                        embedding  = EXCLUDED.embedding,
                        updated_at = NOW()
                    """,
                    ("user_notes", source_id, combined, Jsonb(meta), embedding),
                )
    except Exception as exc:
        logger.warning("KB index upsert failed for published note %s: %s", note_id, exc)

    return {
        "note_id": note_id,
        "status": "published",
        "cycle_count": new_cycle,
        "content_hash": new_hash,
    }


app.include_router(admin_router)
