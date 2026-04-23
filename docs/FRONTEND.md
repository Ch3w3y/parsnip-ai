# Frontend (assistant-ui / Next.js)

The Parsnip frontend is a Next.js application powered by [`@assistant-ui/react`](https://www.assistant-ui.com/) (v0.12.x). It provides the primary browser-based chat interface for interacting with the Parsnip agent.

- **Default port**: `3001` (mapped from container port `3000`)
- **Framework**: Next.js 14 with App Router, React 18, TypeScript, Tailwind CSS 3
- **UI library**: `@assistant-ui/react` 0.12.x (primitives-based)

OpenWebUI on port `3000` remains available as a legacy option (see [Legacy: OpenWebUI](#legacy-openwebui)).

---

## Project Structure

```
frontend/
├── Dockerfile                  # Multi-stage build (deps → build → standalone runner)
├── next.config.js              # API proxy rewrite to agent
├── tailwind.config.ts          # Custom parsnip color palette
├── package.json                # Scripts and dependencies
└── src/
    ├── app/
    │   ├── layout.tsx          # Root layout — metadata, dark theme, Inter font
    │   ├── page.tsx            # Main page — assembles providers, header, thread, tool UIs
    │   ├── providers.tsx       # ParsnipRuntimeProvider — SSE adapter + AssistantRuntimeProvider
    │   └── globals.css         # Tailwind directives, CSS variables, custom animations
    └── components/
        ├── Header.tsx               # Top bar with logo, brand name, connection status
        ├── WelcomeScreen.tsx        # Empty-state screen with suggestion chips
        ├── ApprovalUI.tsx           # Approval/reject UI with auto-approve countdown
        ├── assistant-ui/
        │   └── thread.tsx           # Thread primitive assembly (messages + composer)
        └── tools/
            ├── ToolUIs.tsx          # All makeAssistantToolUI registrations + ToolUIRegistry
            └── ToolBoundary.tsx     # Error boundary wrapping tool UI components
```

---

## Key Components

### `page.tsx` — Main Entry

The root page composes the full UI tree:

```tsx
<ParsnipRuntimeProvider>
  <ToolUIRegistry />       {/* Registers all tool UI components */}
  <Header />
  <div className="accent-line" />
  <main>
    <ToolBoundary>          {/* Catches errors in tool rendering */}
      <Thread />            {/* Message thread + composer */}
    </ToolBoundary>
  </main>
</ParsnipRuntimeProvider>
```

Key detail: `<ToolUIRegistry />` must render inside `<AssistantRuntimeProvider>` (provided by `ParsnipRuntimeProvider`) because `makeAssistantToolUI` components require the runtime context.

### `providers.tsx` — ParsnipRuntimeProvider

This is the bridge between the frontend and the agent API. It implements the `ChatModelAdapter` interface from `@assistant-ui/react` to stream responses via SSE:

- Converts assistant-ui message format to OpenAI-compatible `{ role, content }` pairs.
- POSTs to `/api/chat/completions` (proxied to the agent — see [API Proxy](#api-proxy-nextrewrite)).
- Parses SSE `data:` lines from the OpenAI chat completion chunk format.
- Yields incremental text content as it arrives.
- Uses `useLocalRuntime` to create the runtime (no server-side assistant-ui runtime).

### `thread.tsx` — Thread Component

Built from assistant-ui 0.12.x primitives (not the pre-built `Thread` component, which is not exported in this version):

- `ThreadPrimitive.Root` — container with empty-state handling.
- `ThreadPrimitive.Empty` — renders `WelcomeScreen` when no messages exist.
- `ThreadPrimitive.Viewport` — scrollable message area with `UserMessage` and `AssistantMessage` components.
- `ComposerPrimitive` — input field and send button at the bottom.

### `Header.tsx`

Top navigation bar showing the Parsnip logo, brand name with gradient text, tagline, and a green "Connected" status indicator with pulse animation.

### `WelcomeScreen.tsx`

Displayed when the thread is empty. Shows the Parsnip brand mark and four suggestion chips using `ThreadPrimitive.Suggestion`:

| Label | Prompt |
|---|---|
| Research papers | Research the latest machine learning papers from arxiv |
| Search knowledge base | Search my knowledge base for climate change data |
| Create a note | Create a Joplin note summarizing our discussion |
| Analyze data | Analyze the latest forex rates and identify trends |

Clicking a chip sends the prompt immediately (`send` prop).

### `ApprovalUI.tsx`

A reusable approval prompt for dangerous or sensitive agent actions. Features:

- **Countdown ring**: SVG circular progress indicator showing seconds remaining.
- **Auto-approve**: Configurable `autoApproveSeconds` (default 30s). When the timer expires, the action auto-approves.
- **Approve / Reject buttons**: User can explicitly approve or reject at any time.
- Props: `description`, `onApprove`, `onReject`, `autoApproveSeconds`.

### `ToolUIs.tsx` — Tool UI Registry

The largest component file. Maps every Parsnip agent tool to a React rendering component using `makeAssistantToolUI`. See [Custom Tool UI Architecture](#custom-tool-ui-architecture) for details.

### `ToolBoundary.tsx`

A React `ErrorBoundary` wrapper that catches rendering errors in tool UI components and displays a fallback error card instead of crashing the entire thread.

---

## Custom Tool UI Architecture

### How `makeAssistantToolUI` Works

`makeAssistantToolUI` is a function from `@assistant-ui/react` that creates a React component bound to a specific tool name. When the agent calls a tool during a conversation, assistant-ui matches the tool name and renders the corresponding component.

Signature:

```tsx
const MyToolUI = makeAssistantToolUI<ToolArgs, ResultType>({
  toolName: "my_tool",
  render: ({ args, status, result }) => {
    // args:    the tool call arguments
    // status:  { type: "running" | "error" | "complete" }
    // result:  the tool result (when available)
    return <div>...</div>;
  },
});
```

The returned component **must** be rendered inside the `<AssistantRuntimeProvider>` tree.

### Render Functions by Category

`ToolUIs.tsx` defines six shared render functions. Multiple tool names sharing the same visual style reuse one render function:

| Render Function | Visual Style | Color Accent | Tools |
|---|---|---|---|
| `renderSearchTool` | 🔍 Search card with query preview | Parsnip teal | `kb_search`, `holistic_search`, `adaptive_search`, `research`, `search_with_filters`, `find_similar`, `web_search`, `arxiv_search` |
| `renderJoplinTool` | 📓 Joplin card with `joplin://` deep-links | Indigo | `joplin_search_notes`, `joplin_get_note`, `joplin_create_note`, `joplin_update_note`, `joplin_edit_note`, `joplin_delete_note`, `joplin_list_notebooks`, `joplin_create_notebook`, `joplin_list_tags`, `joplin_get_tags_for_note`, `joplin_upload_resource`, `joplin_ping`, `save_note`, `list_documents` |
| `renderMemoryTool` | 🧠 Memory card with importance dots | Parsnip teal | `save_memory`, `recall_memory`, `update_memory`, `delete_memory`, `recall_memory_by_category`, `summarize_memories` |
| `renderAnalysisTool` | ▶ Analysis card with collapsible code view | Parsnip blue | `execute_python_script`, `execute_r_script`, `execute_notebook`, `generate_dashboard`, `list_analysis_outputs`, `create_scheduled_job`, `list_scheduled_jobs`, `delete_scheduled_job` |
| `renderWorkspaceTool` | ⌨/📁 Workspace card (terminal vs file icon) | Warning (amber) | `list_workspace`, `read_workspace_file`, `write_workspace_file`, `make_workspace_dir`, `delete_workspace_item`, `move_workspace_item`, `execute_bash_command`, `write_and_execute_script`, `execute_workspace_script` |
| `renderGitHubTool` | GitHub icon SVG card | Purple | `github_search_repos`, `github_get_file`, `github_list_commits`, `github_search_code`, `github_list_issues`, `github_create_issue`, `github_list_pull_requests`, `github_get_readme`, `github_get_repo_structure`, `github_create_pr`, `github_list_branches` |
| `renderGenericTool` | ⚙ Generic card | Muted | `extract_webpage`, `timeline`, `knowledge_gaps`, `compare_sources`, `get_document`, `ingest_pdf`, `generate_knowledge_graph`, `system_status` |

### Adding a New Tool UI

1. In `ToolUIs.tsx`, either reuse an existing render function or define a new one following the three-state pattern (`running` → shimmer, `error` → error card, `complete` → result card).
2. Create a `makeAssistantToolUI` component:
   ```tsx
   const MyNewTool = makeAssistantToolUI<ToolArgs, string>({
     toolName: "my_new_tool",
     render: renderGenericTool,  // or your custom function
   });
   ```
3. Add the component to the `ToolUIRegistry` return value.

### Tool Card CSS

All tool UIs share the `.tool-card` base class defined in `globals.css`:

```css
.tool-card {
  background-color: var(--parsnip-navy-700);
  border: 1px solid var(--parsnip-border);
  border-radius: 10px;
  padding: 14px 16px;
  margin: 6px 0;
  transition: border-color 0.2s;
}
.tool-card:hover { border-color: var(--parsnip-teal); }
```

---

## API Proxy (Next.js Rewrite)

The frontend does **not** call the agent API directly from the browser. Instead, Next.js rewrites all `/api/*` requests to the agent's `/v1/*` endpoint:

```js
// next.config.js
async rewrites() {
  return [{
    source: "/api/:path*",
    destination: `${process.env.AGENT_INTERNAL_URL || process.env.NEXT_PUBLIC_AGENT_URL || "http://localhost:8000"}/v1/:path*`,
  }];
}
```

This means:
- Browser requests go to `/api/chat/completions`
- Next.js rewrites them to `http://<agent>/v1/chat/completions`
- The `AGENT_INTERNAL_URL` env var is used first (container-to-container in Docker), falling back to `NEXT_PUBLIC_AGENT_URL` (direct browser access in dev).

---

## Environment Variables

| Variable | Scope | Purpose |
|---|---|---|
| `NEXT_PUBLIC_AGENT_URL` | Browser + SSR | Agent API URL reachable from the browser. Default: `http://localhost:8000`. Used as fallback when `AGENT_INTERNAL_URL` is not set. |
| `AGENT_INTERNAL_URL` | SSR only (Docker) | Container-to-container URL for the agent API. In Docker: `http://pi_agent_backend:8000`. Takes priority over `NEXT_PUBLIC_AGENT_URL` in the rewrite rule. |

In `docker-compose.yml`, both are set:
```yaml
environment:
  - NEXT_PUBLIC_AGENT_URL=http://localhost:8000
  - AGENT_INTERNAL_URL=http://pi_agent_backend:8000
```

The `NEXT_PUBLIC_` prefix makes the variable available in client-side JavaScript, but the actual proxying is done server-side by Next.js rewrites. The browser never calls `AGENT_INTERNAL_URL` directly.

---

## Styling

The frontend uses a custom dark color palette defined in both `tailwind.config.ts` and `globals.css` CSS variables:

| Token | Value | Usage |
|---|---|---|
| `navy-950` / `--parsnip-navy` | `#0b0f1a` | Page background |
| `navy-900` | `#111827` | Header, surfaces |
| `navy-800` | `#1a2332` | Card backgrounds, input fields |
| `navy-700` | `#243044` | Tool cards |
| `navy-600` / `--parsnip-border` | `#2d3b4f` | Borders, dividers |
| `parsnip-teal` | `#23c0a8` | Primary accent, brand color |
| `parsnip-blue` | `#2f6cff` | Secondary accent |
| `parsnip-text` | `#f5f7ff` | Primary text |
| `parsnip-muted` | `#9fb3c8` | Secondary/muted text |
| `parsnip-error` | `#ef4444` | Error states |
| `parsnip-warning` | `#f59e0b` | Warning/approval states |

Custom animations: `.shimmer` (loading placeholder), `.pulse-dot` (status indicator), `.gradient-text` (brand gradient), `.accent-line` (teal-to-blue divider).

---

## Running the Frontend

### Development Mode (Local)

```bash
cd frontend
npm install
npm run dev        # Starts Next.js dev server on http://localhost:3001
```

The dev server proxies API requests to `NEXT_PUBLIC_AGENT_URL` (default `http://localhost:8000`). The agent API must be running separately — either locally or via Docker.

### Docker Compose

```bash
# From project root
docker compose up -d --build
```

The frontend service in `docker-compose.yml`:

- Builds from `frontend/Dockerfile` (multi-stage: deps → build → standalone runner)
- Maps host port `3001` to container port `3000`
- Sets `NEXT_PUBLIC_AGENT_URL=http://localhost:8000` (for browser access)
- Sets `AGENT_INTERNAL_URL=http://pi_agent_backend:8000` (for server-side proxy)
- Depends on the `agent` service being healthy

The Dockerfile uses `output: "standalone"` in Next.js config to produce a minimal production server (`node server.js`).

### Accessing the UI

- **assistant-ui frontend**: `http://localhost:3001`
- **Agent API docs**: `http://localhost:8000/docs`

---

## Legacy: OpenWebUI

OpenWebUI on port `3000` is maintained for backward compatibility during the transition to assistant-ui. It connects to the agent via the Pipelines adapter service on port `9099`.

To switch between UIs:

| UI | URL | Notes |
|---|---|---|
| **assistant-ui** (primary) | `http://localhost:3001` | Next.js + custom tool UIs |
| **OpenWebUI** (legacy) | `http://localhost:3000` | Uses Pipelines adapter at `:9099` |

The OpenWebUI service does not have custom tool UIs — tool output appears as raw text. The Pipelines adapter translates OpenWebUI's OpenAI API calls to the Parsnip agent's `/v1/chat/completions` endpoint.

If you want to run only the legacy UI, you can comment out the `frontend` service in `docker-compose.yml`. Conversely, to disable OpenWebUI, comment out both `openwebui` and `pipelines` services.

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `next` | 14.2.21 | App Router, SSR, API rewrites |
| `react` | 18.3.1 | UI framework |
| `react-dom` | 18.3.1 | DOM rendering |
| `@assistant-ui/react` | 0.12.25 | Chat primitives (`ThreadPrimitive`, `ComposerPrimitive`, `makeAssistantToolUI`) |
| `tailwindcss` | 3.4.15 | Utility-first CSS |
| `typescript` | 5.6.3 | Type safety |
| `react-error-boundary` | — | Used by `ToolBoundary` |

Note: `@assistant-ui/react` 0.12.x does not export a pre-built `Thread` component. The `Thread` in `thread.tsx` is assembled from primitives (`ThreadPrimitive.Root`, `ThreadPrimitive.Empty`, `ThreadPrimitive.Viewport`, `ThreadPrimitive.Messages`, `ComposerPrimitive`).