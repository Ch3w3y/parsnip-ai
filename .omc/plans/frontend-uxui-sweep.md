# Parsnip Frontend UX/UI Sweep — Comprehensive Wiring & Affordance Audit

> Plan status: **DRAFT — awaiting user approval**
> Created: 2026-04-24
> Scope: `frontend/src/**` + the streaming contract at `agent/main.py` `/v1/chat/completions`
> Phasing: P1 (critical bugs) → P2 (data-freshness audit) → P3 (metadata/observability polish)
> Streaming fidelity target (user-chosen): **per-token incremental render + tool-call badges**

---

## 1. Requirements Summary

The frontend today has three felt pain points and a long tail of wiring gaps:

- **Felt by the user (from screenshot/report):**
  1. Thread list does not reliably reflect reality — new threads, renames, and switches leave stale state.
  2. Agent responses show no sign of life during long operations — the UI is silent from prompt-submit to final answer, which users interpret as "frozen".
  3. No metadata accompanies responses — no model, no timing, no tool-call trace, no token counts.

- **Found by audit (full list in §7 matrix):** 10 component-level and 6 store-level gaps covering missing loading states, missing error surfaces, filter-change-without-refetch, optimistic-id reconciliation, and unused assistant-ui adapters.

**The goal** is a UI where every async surface gives unambiguous feedback (in-progress / success / error / empty), every mutation triggers refreshes of dependent views, and every long-running agent operation is legible in real time.

---

## 2. Guiding Principles

1. **Nothing is silent.** Every async call surfaces pending/success/error state within 150 ms.
2. **Data stays fresh.** Mutations invalidate their dependents; filter changes trigger refetches; UI never shows stale data unless explicitly labelled "stale" with refresh affordance.
3. **Use what's already there.** The backend already emits typed events (`token`, `tool_start`, `tool_end`, `done`) and the stores already have `subscribeWithSelector` middleware — we consume what exists before building new plumbing.
4. **Progressive reveal over big-bang commits.** Phase 1 ships the three felt bugs (highest user impact, ~1 day). Phase 2 ships a consistent loading/error/empty pattern across all panels. Phase 3 adds metadata and observability polish.
5. **Accessibility baseline.** Every interactive control has an accessible name and a focus-visible ring; loading states use `aria-live="polite"`; destructive actions have confirmations or undo.

---

## 3. Acceptance Criteria

All criteria are testable either manually (with the feature in a browser) or programmatically.

### P1 — Critical (must pass before marking P1 done)

- [ ] **AC-P1-1 (thread refresh):** After sending the first message of a new conversation, the thread list shows the real thread ID (not `pending-*`) within 500 ms of the stream's `X-Thread-ID` response header arriving, not after stream completion. Verified by opening devtools, sending a long-running prompt, and watching the sidebar.
- [ ] **AC-P1-2 (no duplicate pending):** The `pending-*` optimistic thread row is replaced by the real-id row — not left alongside it. Verified by counting thread rows before vs. after a first message completes.
- [ ] **AC-P1-3 (streaming text):** Once the first SSE `delta.content` chunk arrives, text is visible in the assistant message bubble and updates incrementally as more chunks arrive. Zero-content interval after first chunk must be < 1 frame (16 ms) on localhost. Verified with a 10 s+ prompt.
- [ ] **AC-P1-4 (in-progress indicator):** While `isStreaming === true`, the assistant message bubble shows a visible animated indicator (pulsing cursor, typing dots, or shimmer) — even when zero text has arrived yet. Verified by sending a prompt that triggers a long tool call before any token emits.
- [ ] **AC-P1-5 (tool-call badge):** Every `delta.tool_calls[]` event renders an inline badge in the assistant message, with: tool name, a pulsing/spinner state while running, the truncated input args on hover/click, and a final "Xs" timing label when closed. Verified by sending "search my kb for parsnip" and observing `kb_search` badge appear, spin, and settle.
- [ ] **AC-P1-6 (metadata strip):** After an assistant response completes, a subtle footer strip under the bubble shows: model id (from `done.model_id`), total time, tool count. Verified by the presence of the strip after any response completes.

### P2 — Data-freshness (must pass before marking P2 done)

- [ ] **AC-P2-1 (filter reactivity):** In MemoryBrowser and NotesBrowser, changing any filter (category, importance, notebook, search) triggers a refetch within the component's debounce window (150 ms for dropdowns, 300 ms for typed search). Currently filters only update client-side selector output.
- [ ] **AC-P2-2 (mutation → list refresh):** Every create/update/delete mutation in note-store, memory-store, and the notes HTTP layer invalidates the list view it affects. No stale-count badges, no silent disappearances.
- [ ] **AC-P2-3 (cross-store invalidation):** Creating/deleting a note updates the parent notebook's `note_count` in the sidebar within 500 ms (via store subscription, not polling).
- [ ] **AC-P2-4 (outputs panel auto-refresh):** When the agent emits an `execute_python_script` or `execute_r_script` tool result, OutputsPanel refreshes within 1 s of tool_end. Currently only mounts-once.
- [ ] **AC-P2-5 (error surfacing):** Every fetch failure surfaces a user-visible error with: error message, the URL that failed (truncated), a "retry" button, and `aria-live="polite"`. Test by toggling the backend off and clicking through panels.
- [ ] **AC-P2-6 (empty states):** Every list view has an explicit empty state with actionable copy ("No notebooks — create one?", "No threads — start a conversation"). No blank panes.
- [ ] **AC-P2-7 (loading skeletons):** Every list view shows a shape-matching skeleton (3+ rows) on first load, not a bare "Loading..." string. Skeletons appear within 1 animation frame of request start.

### P3 — Metadata & Observability (must pass before marking P3 done)

- [ ] **AC-P3-1 (per-tool detail):** Clicking/expanding a tool badge reveals: full args (pretty-printed JSON), full output (collapsible), timing, and tool description. Verified on kb_search, web_search, execute_python_script.
- [ ] **AC-P3-2 (response hover):** Hovering a completed assistant bubble reveals: copy, regenerate, and "inspect" (opens a side panel with full metadata: prompt tokens, completion tokens, raw SSE event log). Verified by inspecting a response and seeing event log count match devtools Network panel.
- [ ] **AC-P3-3 (stall detection):** If no SSE chunk arrives for 15 s mid-stream, the in-progress indicator changes to a "still working — Nm elapsed" mode with an optional cancel button. Verified by injecting a 20 s sleep into a tool.
- [ ] **AC-P3-4 (keyboard parity):** Every clickable affordance added in P1/P2/P3 is reachable via Tab, triggerable via Enter/Space, and shows a visible focus ring. Verified via keyboard-only walkthrough.

---

## 4. Phase 1 — Critical Fixes (est. 6–10 h)

### 4.1  Fix incremental streaming (`frontend/src/app/providers.tsx:95-121`)

**Problem:** `fullText` accumulates tokens locally and only calls `appendMessage("assistant", fullText)` once, after `while(true)` completes. assistant-ui sees one giant message delivered at the end.

**Fix approach:**

1. Add a new store action `beginAssistantMessage()` that pushes an empty `{role: "assistant", content: ""}` entry and remembers its index.
2. Add `updateAssistantMessage(delta: string)` that mutates the last assistant message's content by concatenation, preserving identity so React can re-render efficiently.
3. Add `appendToolCall(index: number, tool: { name: string; args?: any; status: "running"|"done"|"error"; durationMs?: number })` that attaches to the last assistant message.
4. In the SSE loop, for every decoded chunk:
   - If `delta.content` → `updateAssistantMessage(delta.content)` **immediately**.
   - If `delta.tool_calls[]` → for each tool-call delta, either create or update a running entry on the message.
   - Catch the synthetic `done` chunk to finalize metadata (model_id, latency).
5. Remove the `fullText` variable entirely.

**Why not send one append per delta?** Zustand's shallow compare would re-render the whole thread on every token. Use an internal `StringBuilder`-style mutation that only bumps a `version` counter, and have the MessagePrimitive render via a selector keyed on both `content` and `version`.

**Files to touch:**
- `frontend/src/app/providers.tsx` — replace SSE handler
- `frontend/src/stores/thread-store.ts` — add actions, extend `SerializedMessage` with optional `toolCalls` and `meta`

### 4.2  Streaming indicator in AssistantMessage (`frontend/src/components/assistant-ui/thread.tsx:58-70`)

**Problem:** Component renders content only. `isRunning` is in scope via the runtime but ignored.

**Fix approach:**

1. Use `useMessage` / `MessagePrimitive.If` from assistant-ui (available on 0.7+) to conditionally render a "streaming" affordance *inside* the bubble when `status.type === "running"`.
2. If that primitive isn't available in the installed version, subscribe to `useThreadStore((s) => s.isStreaming)` and render the indicator only on the last assistant message when streaming is true and `content === ""`.
3. Indicator design: three-dot pulsing loader (Tailwind `animate-pulse`) followed by `▋` blinking cursor once first token lands. Replace cursor with nothing when `isStreaming` flips false.

**Files to touch:**
- `frontend/src/components/assistant-ui/thread.tsx` — extend `AssistantMessage`, add new `StreamingIndicator` subcomponent

### 4.3  Thread list reconciliation (`frontend/src/app/providers.tsx:56-88`, `frontend/src/components/ThreadList.tsx`)

**Problem:** Optimistic `pending-{Date.now()}` thread is inserted but never removed when the real id arrives. `loadThreads()` fires in `finally` after the entire stream, not when the id is known.

**Fix approach:**

1. Change optimistic-thread creation to use a sentinel `__optimistic__` id (single slot, not timestamp-unique), so subsequent optimistic creates replace previous ones.
2. On `X-Thread-ID` header arrival (available before first token emits), perform: `removeOptimistic()` + `upsertThread({ id: realId, ... })` + call `loadThreads()` *immediately* in parallel with continuing the stream.
3. Extend thread-store with `removeOptimistic()` action.
4. Subscribe ThreadList to store updates (Zustand already does this); nothing to change in ThreadList.tsx itself — but verify the component re-renders when `threads` mutates (it should; if not, add explicit `useShallow`).

**Files to touch:**
- `frontend/src/app/providers.tsx` — header-arrival handler
- `frontend/src/stores/thread-store.ts` — `removeOptimistic`

### 4.4  Tool-call badges in message (`frontend/src/components/tools/ToolUIs.tsx`, new `ToolCallBadge.tsx`)

**Problem:** Today `delta.tool_calls[]` is discarded. There's no in-message rendering of "agent is searching KB…".

**Fix approach:**

1. Extend `SerializedMessage` with `toolCalls?: ToolCall[]` where `ToolCall = { name, args, status: "running"|"done"|"error", startedAt, endedAt?, output?, error? }`.
2. In `AssistantMessage`, render toolCalls in order between the text and the end of the bubble: each is a `<ToolCallBadge>` with:
   - Pulsing teal dot while `status === "running"`
   - Solid teal checkmark when `status === "done"`
   - Red X when `status === "error"`
   - Click expands to show args (pretty JSON) and output (first 400 chars + "…")
3. When the stream's SSE delta carries a `tool_call` at index N with a `function.name`, create or update the entry. When a `tool_call_id` arrives with a `finish_reason: "tool_calls"`, mark done.

**Files to touch:**
- `frontend/src/components/tools/ToolCallBadge.tsx` — NEW
- `frontend/src/components/assistant-ui/thread.tsx` — wire into AssistantMessage
- `frontend/src/app/providers.tsx` — parse tool_call deltas
- `frontend/src/stores/thread-store.ts` — extend types and actions

### 4.5  Response metadata footer

**Problem:** Model id, total latency, and tool count are all known server-side (`done.model_id`) but never shown.

**Fix approach:**

1. Track `startedAt` when `onNew` fires; compute `elapsedMs` when `done` SSE chunk arrives or stream ends.
2. Attach `meta: { modelId, elapsedMs, toolCount, promptTokens?, completionTokens? }` to the assistant message when stream closes.
3. Render a footer strip inside the assistant bubble — small, dim, right-aligned: `claude-sonnet-4-6 · 4.2s · 2 tools`.
4. Token counts: the backend's OpenAI-compat `/v1/chat/completions` response doesn't include usage today — file follow-up (§8) to add `usage` on the final chunk. Until then, show only what's available.

**Files to touch:**
- `frontend/src/stores/thread-store.ts` — extend `SerializedMessage` with `meta`
- `frontend/src/components/assistant-ui/thread.tsx` — new `MessageFooter` subcomponent
- `frontend/src/app/providers.tsx` — wire elapsedMs + parse final chunk

---

## 5. Phase 2 — Data-Freshness Audit (est. 6–8 h)

### 5.1  Filter-change reactivity

Wire every filter setter in memory-store and note-store to invalidate + refetch via `useEffect` subscriptions inside the component (or internal `zustand/middleware` `subscribe`). Debounce search inputs (300 ms) to avoid firehose.

**Files:**
- `frontend/src/components/MemoryBrowser.tsx:52-54` — add `useEffect(() => { loadMemories() }, [categoryFilter, importanceFilter])`
- `frontend/src/components/NotesBrowser.tsx:93-95` — add `useEffect` for `notebookFilter` + debounced `searchQuery`
- Consider moving this into the store via `subscribeWithSelector(state, (filters) => loadX())` — keeps components clean.

### 5.2  Cross-store invalidation

When a note is created/deleted, the notebook's `note_count` is stale. Fix:

- Have `createNote`/`deleteNote` call `get()` from note-store and additionally `useNoteStore.getState().loadNotebooks()` — or better, have note-store expose an event via `subscribeWithSelector` and have notebook-aware components subscribe.
- Similarly for memories: deleting a memory doesn't update memory counts elsewhere (currently nowhere, but preemptive).

**Files:**
- `frontend/src/stores/note-store.ts:131-148, 181-204` — append `loadNotebooks()` after success
- (No other cross-store dependencies identified today; document in §7 matrix.)

### 5.3  OutputsPanel auto-refresh

**Approach:** Hook into the thread-store. When a tool_end arrives with `tool` in `{"execute_python_script","execute_r_script","execute_notebook","generate_dashboard"}`, dispatch a store event that OutputsPanel subscribes to and refetches.

**Files:**
- `frontend/src/stores/thread-store.ts` — add `lastAnalysisToolAt: number | null`, bump when applicable tool_end parsed
- `frontend/src/components/OutputsPanel.tsx:102-104` — `useEffect` on `lastAnalysisToolAt`

### 5.4  Uniform loading/error/empty pattern

Introduce three tiny shared components so every panel behaves consistently:

- `<LoadingSkeleton variant="list" rows={5}/>` — animated shimmer blocks
- `<ErrorBanner message={...} onRetry={...}/>` — red bar with retry
- `<EmptyState icon={...} title={...} cta={...}/>` — neutral, with primary action

Apply across: ThreadList, NotesBrowser, NoteEditor, MemoryBrowser, KBStatsPanel, KBSearchPanel, OutputsPanel, AnalysisOutput. Matrix in §7 marks current state per component.

**Files (new):**
- `frontend/src/components/ui/LoadingSkeleton.tsx`
- `frontend/src/components/ui/ErrorBanner.tsx`
- `frontend/src/components/ui/EmptyState.tsx`

### 5.5  note-store `loadNote` loading flag

**Problem:** `loadNote` doesn't flip `isLoading` — navigating to a note is silent (note-store.ts:103-116).

**Fix:** Add a `isLoadingNote` flag (distinct from list loading), flip around the fetch, and consume it in NoteEditor with a skeleton.

### 5.6  Memory-store delete-in-place

**Problem:** `deleteMemory` sets `isLoading: true` on the entire list (memory-store.ts:68-84), which hides the whole panel during a single-row delete.

**Fix:** Track per-row pending state (`deletingIds: Set<number>`) and only flip that single row to a spinning "deleting…" state. On success, remove from list. On failure, show inline error on the row.

---

## 6. Phase 3 — Metadata & Observability Polish (est. 5–7 h)

### 6.1  Tool-call detail drawer

Clicking a ToolCallBadge opens a side drawer (right panel) with: tool name, description (pulled from tool registry endpoint), full args JSON tree, full output (code-highlighted), run timing chart if multiple invocations happened. Uses the existing panel infrastructure.

### 6.2  Response hover actions

On hover over a completed assistant bubble:
- **Copy** — copies text-only content to clipboard
- **Regenerate** — resubmits the last user message with same thread_id
- **Inspect** — opens a drawer showing raw SSE events captured during that response

### 6.3  Stall detection + cancel

Add a 15 s "no new chunk" watchdog. On trigger, change the streaming indicator to "still working — 18s elapsed" and show a cancel button. Cancel calls `abort()` on the fetch and surfaces a "response cancelled" marker in the message.

### 6.4  Keyboard + a11y pass

Walk every new affordance with a screen reader and keyboard:
- Focus rings visible
- `aria-live="polite"` on streaming text
- `aria-busy` on loading containers
- `aria-label` on every icon-only button (refresh, new thread, etc. — some already exist, audit rest)

### 6.5  Backend: add `usage` to final SSE chunk

Open follow-up in `agent/main.py` to include `usage: { prompt_tokens, completion_tokens, total_tokens }` on the final OpenAI-compat chunk before `[DONE]`. Blocks AC-P3-2 partially; all other P3 items are frontend-only.

---

## 7. Component × Store Audit Matrix (complete)

### 7.1  Component matrix

Legend: ✅ present / ⚠️ partial / ❌ missing

| Component | Data source | Refresh trigger | Loading | Error | Empty | Phase |
|-----------|-------------|-----------------|---------|-------|-------|-------|
| `ThreadList.tsx` | `thread-store` | Manual button + mount | ⚠️ "Loading..." text | ❌ | ✅ | P1.3, P2.4 |
| `AssistantMessage` (thread.tsx) | stream | SSE chunks | ❌ | ❌ | n/a | P1.2, P1.4, P1.5 |
| `UserMessage` (thread.tsx) | store | synchronous | n/a | n/a | n/a | — |
| `AppShell.tsx` | panel-store | UI | n/a | n/a | n/a | — |
| `Header.tsx` | panel-store | UI | n/a | n/a | n/a | — |
| `LeftSidebar.tsx` | panel-store | UI tab | n/a | n/a | n/a | — |
| `RightSidebar.tsx` | panel-store | UI tab | n/a | n/a | n/a | — |
| `NotesBrowser.tsx` | `note-store` | Mount + notebook change | ⚠️ text | ⚠️ red text | ⚠️ text | P2.1, P2.4 |
| `NoteEditor.tsx` | `note-store` | `setCurrentNoteId` | ❌ | ❌ | ❌ | P2.5, P2.4 |
| `NoteToolbar.tsx` | `note-store` | user action | n/a | n/a | n/a | P2.5 |
| `MemoryBrowser.tsx` | `memory-store` | Mount only | ⚠️ text | ✅ banner | ⚠️ text | P2.1, P2.4, P2.6 |
| `KBSearchPanel.tsx` | `kb-store` | Manual refresh | ⚠️ text | ❌ | ⚠️ text | P2.4 |
| `KBStatsPanel.tsx` | `kb-store` | Mount only | ⚠️ text | ⚠️ text | ⚠️ text | P2.4 |
| `OutputsPanel.tsx` | local | Mount + manual | ⚠️ text | ⚠️ text | ⚠️ text | P2.3, P2.4 |
| `AnalysisOutput.tsx` | props | render | n/a | n/a | n/a | P3.1 (detail view) |
| `MarkdownRenderer.tsx` | props | render | n/a | n/a | n/a | — |
| `ApprovalUI.tsx` | props + timer | countdown | ✅ ring | n/a | n/a | — |
| `WelcomeScreen.tsx` | static | — | n/a | n/a | n/a | — |
| `ToolUIs.tsx` (existing) | assistant msg | SSE | ✅ shimmer | ✅ card | n/a | P3.1 (extend) |
| `ToolBoundary.tsx` | error | React boundary | n/a | ✅ | n/a | — |

### 7.2  Store matrix

| Store | Loading | Error | Cross-store hooks | Filter reactivity | Optimistic | Phase |
|-------|---------|-------|-------------------|-------------------|------------|-------|
| `thread-store` | ✅ `isLoading` | ❌ | ❌ | n/a | ⚠️ pending-id leak | P1.3, P2.5 |
| `note-store` | ⚠️ list only | ✅ | ❌ | ❌ | ✅ | P2.1, P2.2, P2.5 |
| `memory-store` | ⚠️ coarse | ✅ | ❌ | ❌ | ❌ | P2.1, P2.6 |
| `kb-store` | ✅ split | ✅ | ❌ | n/a | n/a | P2.4 |
| `panel-store` | n/a | n/a | n/a | n/a | n/a | — |

---

## 8. Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|------------|--------|------------|
| R1 | Incremental append triggers whole-message re-render per token (perf) | Med | Med | Use single-instance mutation + version counter; memoize `MarkdownRenderer` on `content+version`; benchmark on 4k-token response. |
| R2 | assistant-ui `ExternalStoreAdapter` in installed version doesn't expose a per-message "running" status | Low | Med | Fallback: subscribe directly to `useThreadStore.isStreaming` in the AssistantMessage component; identify last message by index. |
| R3 | Parsing `delta.tool_calls[]` incrementally (OpenAI streams fragments of tool names and args one piece at a time) | High | Med | Reuse the backend's already-fragmented format (main.py:336-354) which sends full name + full args on one chunk; if that changes, add a small accumulator keyed by `tool_call_index`. |
| R4 | `subscribeWithSelector` patterns introduce infinite refetch loops | Med | High | Use `useEffect` with explicit dependency arrays rather than in-store subscriptions for cross-store effects; keep in-store subs limited to filter→list within same store. |
| R5 | Token counts not available until backend change (§6.5) | High | Low | Ship P3 metadata strip without token counts; add them when backend lands. |
| R6 | Tests for streaming behavior are flaky | Med | Low | Write Vitest component tests that feed a mocked ReadableStream with hand-crafted chunks; assert DOM after each chunk with `findByText`. |
| R7 | Thread-list reconciliation race: optimistic row inserted, then `loadThreads()` returns stale list that *also* lacks the new row | Med | Med | In `loadThreads`, merge incoming list with any still-pending optimistic rows; only remove optimistic once a matching real id appears. |
| R8 | Phase 2 cross-store invalidation touches too many files and regresses | Med | Med | Land P2 behind `NEXT_PUBLIC_UX_SWEEP_V2` env flag for one day, bake, then remove flag. |

---

## 9. Verification Plan

For each phase, run the following before marking done:

### Manual (all phases)

- [ ] Open devtools Network tab; for each affected panel: observe request timing, response status, and re-render behavior.
- [ ] Keyboard-only walkthrough: Tab through the UI; every button must be reachable and have a visible focus ring.
- [ ] Screen reader spot check (macOS VoiceOver or NVDA): list views announce row counts; streaming text is announced politely.
- [ ] Offline test: toggle backend off; every panel shows an error banner with retry.
- [ ] Slow-network test: in devtools, throttle to "Slow 3G"; loading skeletons appear immediately, text streams visibly.

### Automated (P1+)

- [ ] New Vitest test: `thread-store.test.ts` exercising `beginAssistantMessage` + `updateAssistantMessage` + `appendToolCall`.
- [ ] New Vitest test: `providers.test.tsx` with a mocked `ReadableStream` producing canonical chunks; assert DOM is incrementally populated and tool badges render.
- [ ] `ruff check agent/` — backend unchanged in P1/P2; sanity check only.
- [ ] `npm run build` inside `frontend/` — TypeScript + Next.js build clean.

### Regression

- [ ] Send a 2-message conversation: no memory leaks in devtools Memory profile; store `messagesByThread` contains both messages.
- [ ] Switch between 3 existing threads: cached messages appear instantly; no flashes of empty state.
- [ ] Delete a note: list updates; notebook sidebar count decrements; if delete fails, row reappears with error.
- [ ] Change note search: results update within 400 ms (300 ms debounce + 100 ms request).

---

## 10. Out of Scope (this plan)

- **Design refresh / visual rebrand.** Colors, typography, and spacing stay as-is; only affordances change.
- **Mobile / responsive.** Layout fixes for narrow viewports are deferred.
- **Authentication UI.** No auth surfaces are audited.
- **Offline-first / PWA.** No service worker work.
- **i18n.** Copy changes are in English only.

---

## 11. Execution Order & Handoff

Recommended execution mode: `/oh-my-claudecode:team` for Phase 1 (parallel across thread-store, providers.tsx, thread.tsx, and a new ToolCallBadge file — 4 near-independent lanes), then `/oh-my-claudecode:ralph` for P2 and P3 (more sequential, each panel touches specific files).

Exit criteria from plan into execution:
1. User approves this plan.
2. Each phase's acceptance criteria are copied as TaskCreate entries before any code changes.
3. After P1, pause and let the user hit the UI end-to-end before starting P2.
