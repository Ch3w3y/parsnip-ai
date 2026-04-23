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

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import psycopg
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from pydantic import BaseModel

from tools.db_pool import init_pool, close_all
from tools.pdf_ingest import ingest_pdf

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


class SessionSave(BaseModel):
    thread_id: str
    title: str = ""
    tools_used: list[str] = []


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

    thread_id = str(uuid.uuid4())
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

    # Convert OpenAI messages to LangChain messages for context
    history_messages = list(existing_messages)
    for msg in req.messages[:-1]:  # All except the last (which is the current user message)
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
    """Return full message history for a conversation thread."""
    config = {"configurable": {"thread_id": thread_id}}
    try:
        state = await agent.aget_state(config)
        if not state or not state.values:
            raise HTTPException(status_code=404, detail="Thread not found")
        messages = [_serialize_message(m) for m in state.values.get("messages", [])]
        return {"thread_id": thread_id, "messages": messages}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/memories")
async def list_memories(category: str = "", min_importance: int = 0, limit: int = 50):
    """List long-term memories, optionally filtered by category and importance."""
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
            params.append(limit)

            where = " AND ".join(conditions)
            rows = await (
                await conn.execute(
                    f"SELECT id, category, content, importance, created_at, updated_at "
                    f"FROM agent_memories WHERE {where} ORDER BY importance DESC, created_at DESC LIMIT %s",
                    params,
                )
            ).fetchall()

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
            rows = await (
                await conn.execute(
                    """
                    SELECT source, COUNT(*) as chunks, COUNT(DISTINCT source_id) as articles
                    FROM knowledge_chunks
                    GROUP BY source
                    ORDER BY chunks DESC
                    """
                )
            ).fetchall()
            jobs = await (
                await conn.execute(
                    "SELECT source, status, processed, total, started_at, finished_at "
                    "FROM ingestion_jobs ORDER BY id DESC LIMIT 10"
                )
            ).fetchall()

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
            rows = await (
                await conn.execute(
                    "SELECT id, content, metadata, created_at "
                    "FROM agent_memories "
                    "WHERE category = 'research_session' AND deleted_at IS NULL "
                    "ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
            ).fetchall()

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
            row = await (
                await conn.execute(
                    "SELECT id, content, metadata, created_at "
                    "FROM agent_memories WHERE id = %s AND category = 'research_session'",
                    (session_id,),
                )
            ).fetchone()

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
            row = await (
                await conn.execute(
                    "SELECT content, metadata, created_at "
                    "FROM agent_memories WHERE id = %s AND category = 'research_session'",
                    (session_id,),
                )
            ).fetchone()

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
