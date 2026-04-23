# Task 13: Frontend Documentation — Evidence

## Files Created
- `docs/FRONTEND.md` — comprehensive frontend documentation

## Sections Covered
- [x] Project structure (`frontend/src/app/`, `frontend/src/components/`)
- [x] Key components: `ToolUIs.tsx`, `ToolBoundary.tsx`, `ApprovalUI.tsx`, `WelcomeScreen.tsx`, `Header.tsx`, `thread.tsx`, `providers.tsx`, `page.tsx`, `layout.tsx`
- [x] How the frontend connects to agent API (`NEXT_PUBLIC_AGENT_URL`, `AGENT_INTERNAL_URL`, Next.js rewrite proxy)
- [x] Environment variables specific to the frontend (`NEXT_PUBLIC_AGENT_URL`, `AGENT_INTERNAL_URL`)
- [x] Custom tool UI architecture (`makeAssistantToolUI` — six render functions, 50+ tool registrations)
- [x] How to run frontend in dev mode (`npm run dev`) vs Docker (`docker compose up`)
- [x] OpenWebUI as legacy option (port 3000, Pipelines adapter)

## Verification: Component Names Match Actual Files
All component file names in the documentation match files on disk:

| Documented Name | Actual File | Verified |
|---|---|---|
| `Header.tsx` | `frontend/src/components/Header.tsx` | ✅ |
| `WelcomeScreen.tsx` | `frontend/src/components/WelcomeScreen.tsx` | ✅ |
| `ApprovalUI.tsx` | `frontend/src/components/ApprovalUI.tsx` | ✅ |
| `ToolUIs.tsx` | `frontend/src/components/tools/ToolUIs.tsx` | ✅ |
| `ToolBoundary.tsx` | `frontend/src/components/tools/ToolBoundary.tsx` | ✅ |
| `thread.tsx` | `frontend/src/components/assistant-ui/thread.tsx` | ✅ |
| `page.tsx` | `frontend/src/app/page.tsx` | ✅ |
| `providers.tsx` | `frontend/src/app/providers.tsx` | ✅ |
| `layout.tsx` | `frontend/src/app/layout.tsx` | ✅ |

## Key Insights
- assistant-ui 0.12.x does NOT export a pre-built `Thread` component — it's built from primitives in `thread.tsx`
- `makeAssistantToolUI` returns components that MUST render inside `AssistantRuntimeProvider`
- The `ToolUIRegistry` component renders ALL registered tool UIs as siblings
- API proxying goes through Next.js rewrites, not direct browser → agent calls
- Docker setup maps port 3001→3000 (container), with `AGENT_INTERNAL_URL` for container-to-container routing
- Tool card categories: Search (8 tools), Joplin (14 tools), Memory (6 tools), Analysis (8 tools), Workspace (9 tools), GitHub (11 tools), Generic (8 tools)