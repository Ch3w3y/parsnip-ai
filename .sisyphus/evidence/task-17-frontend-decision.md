# Frontend Decision: OpenWebUI vs assistant-ui

## Recommendation: Replace OpenWebUI with assistant-ui

### Why

| Criterion | OpenWebUI | assistant-ui |
|-----------|-----------|-------------|
| **Tech stack** | Python (Svelte frontend) | TypeScript/React (100%) |
| **Custom tool UIs** | Python Pipelines only — no per-tool React rendering | `makeAssistantToolUI` — each tool gets its own React component with loading/success/error states |
| **HITL approvals** | Not built-in | Built-in — approve/reject memory modifications, file writes before execution |
| **Weight** | Heavy — full Python stack, Redis, 9 vector DBs, built-in RAG (redundant with Parsnip) | Ultra-light — it's a component library you embed in your own app |
| **Frontend customizability** | Svelte — hard to customize without forking | React — `makeAssistantToolUI` + Radix primitives, customize every pixel |
| **Backend coupling** | Brings its own user management, RAG, vector DB | None — just `useChat({ api: "http://localhost:8000/v1/chat/completions" })` |
| **Ops burden** | 2 containers (openwebui + pipelines), Redis | 1 container (Next.js static build) or serve via Caddy |
| **Joplin integration** | Pipeline enrichment (Python-only, duplicated agent's work) | Custom React tool UI with Joplin deep-links, edit-in-place |
| **Memory rendering** | Pipeline model — appears as chat model choice | Dedicated memory panel component with category/importance badges |
| **KB search rendering** | Same pipeline model | Expandable source-cards with similarity scores, content previews |
| **Stars/activity** | 133k★, active | 9.6k★, active (pushed today) |
| **Migration effort** | N/A (current) | ~2-3 days: scaffold Next.js + assistant-ui, wire to agent API, build 5-6 tool UI components |

### Decision: GO — Replace OpenWebUI with assistant-ui

**Rationale**:
1. Parsnip's agent IS the backend. OpenWebUI's built-in RAG, vector DBs, and user management are all redundant weight.
2. Per-tool custom React rendering is the killer feature — Joplin notes, KB search results, memory chips, analysis code blocks each get purpose-built UIs.
3. HITL approval buttons for memory modifications and file writes match the Joplin HITL workflow (Task 6).
4. TypeScript/React aligns with long-term extensibility — the frontend can grow independently.
5. The pipeline middleware approach (Tasks 9/10) was a workaround for OpenWebUI's limitations. With assistant-ui, we get direct, rich integration.

### Path Forward
- **Tasks 9/10** (OpenWebUI pipelines) → **CANCELLED**
- **Tasks 18/19** (assistant-ui setup + tool UIs) → **ACTIVATED**
- OpenWebUI remains in docker-compose.yml temporarily (parallel operation) until assistant-ui is stable
- Agent needs a thin `/v1/chat/completions` OpenAI-compatible endpoint wrapper (currently has `/chat` with custom SSE format)

### Risks
- Need to ensure agent's SSE format is compatible with assistant-ui's `useChat` hook (Vercel AI SDK format)
- assistant-ui is a component library, not an app — need to build the Next.js shell (~30 min with `npx assistant-ui init`)
- If tool calls aren't in OpenAI function_calling format, need to normalize agent output