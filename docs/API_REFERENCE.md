# Agent API Reference

The pi-agent FastAPI service runs on port `8000` with OpenAI-compatible endpoints for seamless integration with AI SDKs and frontends.

## Endpoints

### Chat & Completion

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/chat/completions` | OpenAI-compatible streaming chat (SSE) |
| `POST` | `/chat` | Native streaming chat (SSE) |
| `POST` | `/chat/sync` | Non-streaming chat for testing |
| `GET` | `/v1/models` | OpenAI-compatible model list |

### Ingestion & Knowledge Base

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/stats` | Knowledge base statistics |
| `GET` | `/ingestion/status` | Ingestion job overview |
| `GET` | `/ingestion/migration` | Wikipedia source_id migration status |
| `GET` | `/ingestion/wikipedia` | Wikipedia bulk ingestion detail |
| `POST` | `/upload/pdf` | Upload and embed a PDF |

### Sessions & Threads

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/threads/{thread_id}` | Conversation history |
| `POST` | `/sessions/save` | Save a research session |
| `GET` | `/sessions` | List saved sessions |
| `GET` | `/sessions/{session_id}` | Get saved session details |
| `GET` | `/sessions/{session_id}/export` | Export session as markdown |

### Other

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/models` | List available OpenRouter models |
| `GET` | `/memories` | List long-term memories |
| `DELETE` | `/memories/{memory_id}` | Soft-delete a memory |

---

## Request/Response Formats

### `/v1/chat/completions` (OpenAI-compatible)

**Request:**
```json
{
  "model": "parsnip-agent",
  "messages": [
    {"role": "user", "content": "What is the capital of France?"}
  ],
  "stream": true,
  "temperature": 0.7,
  "max_tokens": 1000
}
```

**Response (SSE stream):**
```
data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1713888000,"model":"parsnip-agent","choices":[{"index":0,"delta":{"role":"assistant","content":"The"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1713888000,"model":"parsnip-agent","choices":[{"index":0,"delta":{"content":" capital"},"finish_reason":null}]}

data: {"id":"chatcmpl-abc123","object":"chat.completion.chunk","created":1713888000,"model":"parsnip-agent","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### `/chat` (native streaming)

**Request:**
```json
{
  "message": "What is the capital of France?",
  "thread_id": "thread_123",
  "model": null
}
```

**Response (SSE stream):**
```
data: {"type":"token","content":"The","thread_id":"thread_123"}

data: {"type":"tool_start","tool":"web_search","input":{"query":"capital of France"}}

data: {"type":"tool_end","tool":"web_search"}

data: {"type":"token","content":" capital","thread_id":"thread_123"}

data: {"type":"done","thread_id":"thread_123","model_id":"smart"}
```

### `/health`

**Response:**
```json
{"status":"ok","agent_ready":true}
```

### `/stats`

**Response:**
```json
{
  "knowledge_base": [
    {"source":"wikipedia","chunks":12345,"articles":1200},
    {"source":"arxiv","chunks":5678,"articles":450}
  ],
  "ingestion_jobs": [
    {"source":"wikipedia","status":"completed","processed":10000,"total":10000,"started_at":"2025-04-23","finished_at":"2025-04-23"}
  ]
}
```

### `/ingestion/status`

Returns full ingestion ecosystem status (Wikipedia migration, bulk ingest, jobs overview).

### `/upload/pdf`

**Request:**
```
multipart/form-data: file=<PDF file>
```

**Response:**
```json
{
  "status":"ok",
  "message":"Ingested 'paper.pdf': 42 chunks from 8 pages.",
  "filename":"paper.pdf",
  "chunks":42,
  "pages":8
}
```

---

## Testing with `curl`

### 1. OpenAI-compatible streaming chat
```bash
curl -sS http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "parsnip-agent",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }' | grep -v "^data: \[DONE\]$"
```

### 2. Non-streaming sync chat
```bash
curl -sS http://localhost:8000/chat/sync \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is 2+2?",
    "thread_id": "test-thread"
  }'
```

### 3. Health check
```bash
curl -sS http://localhost:8000/health
```

### 4. Knowledge base stats
```bash
curl -sS http://localhost:8000/stats
```

### 5. Upload a PDF
```bash
curl -sS http://localhost:8000/upload/pdf \
  -F "file=@/path/to/document.pdf"
```

---

## Authentication

None. The agent runs local-first. Authentication defaults to `local-first` and can be optionally configured via `.env` if needed.

---

## Auto-generated Docs

Visit `http://localhost:8000/docs` for interactive OpenAPI/Swagger documentation with Try-it-out support.

---

*Last updated: 2026-04-23*
