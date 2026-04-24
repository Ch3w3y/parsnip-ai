# Phase 2 Handoff — Data-Freshness Across Panels

> **Pick up from:** Phase 1 is complete and deployed. The agent's streaming UX is fixed. This phase fixes the **wiring gaps** where UI panels show stale data, don't refetch on filter changes, or silently fail.
> **Parent plan:** `.omc/plans/frontend-uxui-sweep.md` (§5)
> **Prerequisite reading:** §3 (AC-P2-1 through AC-P2-7), §7 (component/store matrix), §8 (risks R4, R7, R8)
> **Estimated effort:** 6–8 hours
> **Scope guardrail:** Only the six fixes below. Do NOT start P3 metadata/observability work.

---

## Context you need before touching any file

1. **Architecture:** Parsnip's frontend uses Zustand stores (`frontend/src/stores/*.ts`) backed by REST through a Next.js proxy route (`frontend/src/app/api/agent/[...path]/route.ts`) into the FastAPI agent (`agent/main.py`). The streaming chat takes a separate route at `/api/chat/completions`.

2. **Deployment reality** (from project memory `project_backend_no_bindmount.md`): Neither `pi_agent_backend` nor `pi_agent_frontend` has a bind mount for `/app`. Host edits do NOT reach the container. Workflow:
   - Backend changes: `docker cp <file> pi_agent_backend:/app/... && docker restart pi_agent_backend`
   - Frontend changes: `cd frontend && npm run build && docker cp .next/standalone/. pi_agent_frontend:/app/ && docker exec pi_agent_frontend rm -rf /app/.next/static && docker exec pi_agent_frontend mkdir -p /app/.next/static && docker cp .next/static/. pi_agent_frontend:/app/.next/static/ && docker restart pi_agent_frontend`

3. **Existing patterns** — conform to these:
   - `note-store.ts` is the gold standard for store design (immutable updates, optimistic delete with rollback, `subscribeWithSelector` middleware available).
   - `memory-store.ts` also has `subscribeWithSelector` but the subscription hooks are never wired — this phase wires them.
   - Zustand middleware `subscribeWithSelector` enables `store.subscribe(selector, listener, { equalityFn, fireImmediately })`. Use this for filter-change refetching.
   - All new shared UI components go under `frontend/src/components/ui/`.

4. **What NOT to change:**
   - Don't touch any backend code in Phase 2 (`agent/**`). Backend changes are queued for Phase 3.
   - Don't modify the assistant-ui thread.tsx — it's finished.
   - Don't introduce React Query, SWR, or any new data-fetching library. Zustand + fetch is the house style; stay consistent.
   - Don't re-home stores or rename files.

---

## The six fixes, in recommended implementation order

### 2.1 — Uniform Loading/Error/Empty primitives (do this FIRST)

Create three shared components so every subsequent fix can consume them. This is the foundation.

**Files to create:**

- `frontend/src/components/ui/LoadingSkeleton.tsx`
  - Prop: `variant: "list" | "card" | "inline"`, `rows?: number` (default 3)
  - Renders animated shimmer blocks using Tailwind `animate-pulse` on `bg-navy-700/40` blocks
  - Each row is ~16px tall with varying widths (90%/75%/82%/68% randomly but deterministically via index) so it doesn't look fake-uniform
  - Must render first paint within one animation frame — zero data dependencies, just CSS

- `frontend/src/components/ui/ErrorBanner.tsx`
  - Props: `message: string`, `onRetry?: () => void`, `detail?: string`
  - Red-tinted bar: `bg-red-900/30 border-red-700/50 text-red-200`, padding `p-3`, rounded `rounded-md`
  - Icon (use inline SVG — no new icon library), message, optional detail in smaller dim text, optional "Retry" button on the right
  - `role="alert"` and `aria-live="assertive"` on the banner root

- `frontend/src/components/ui/EmptyState.tsx`
  - Props: `icon?: ReactNode`, `title: string`, `description?: string`, `cta?: { label: string; onClick: () => void }`
  - Centered flex column with generous padding, muted colors, optional CTA button styled like the existing parsnip-teal buttons
  - Keep it short — 2–3 text nodes max

**Apply to these components, replacing the current ad-hoc "Loading..." text / red text / bare divs:**

| Component | Current state | Replace with |
|-----------|---------------|--------------|
| `ThreadList.tsx:63-72` | Plain text loading + empty | `<LoadingSkeleton rows={5}/>`, `<EmptyState title="No threads yet" cta={{label: "Start a conversation", onClick: switchToNewThread}}/>` |
| `NotesBrowser.tsx` | Check current state | Same pattern |
| `MemoryBrowser.tsx` | Plain text loading | Same pattern |
| `KBStatsPanel.tsx` | Plain text loading + text error | All three primitives |
| `KBSearchPanel.tsx` | Plain text loading, no error/empty | All three primitives |
| `OutputsPanel.tsx` | Plain text loading + text error | All three primitives |
| `NoteEditor.tsx` | Nothing (silent load) | Add `<LoadingSkeleton variant="card"/>` when `isLoadingNote` is true (added by fix 2.5) |

**Acceptance:** AC-P2-5, AC-P2-6, AC-P2-7 from the master plan.

### 2.2 — Filter-change reactivity (memory + note stores)

Both stores already import `subscribeWithSelector` middleware. Wire it up inside each store module with a self-subscription, or subscribe from the consuming component. Pick ONE approach and apply consistently — I recommend the component approach because it makes debounce timing explicit.

**Implementation pattern (component approach):**

```tsx
// In MemoryBrowser.tsx
useEffect(() => {
  loadMemories();
}, [categoryFilter, importanceFilter, loadMemories]);

// For search (300ms debounce):
useEffect(() => {
  const t = setTimeout(() => loadMemories(), 300);
  return () => clearTimeout(t);
}, [searchQuery, loadMemories]);
```

**Files to edit:**

- `frontend/src/components/MemoryBrowser.tsx` — add useEffect for category/importance (instant) and search (300ms debounce). Currently only runs on mount (line ~52-54).
- `frontend/src/components/NotesBrowser.tsx` — add useEffect for `notebookFilter` and debounced `searchQuery`. The store's `loadNotes(notebookId?, search?)` already accepts the filters; pass them through.
- `frontend/src/stores/note-store.ts` — you may need to expand `loadNotes` to also include the current `searchQuery` in the URL. Verify it's already wired; if search currently only filters client-side in `selectNotes` (line 246-263), add `?search=` to the query string so the backend does the real filtering.

**Gotchas:**
- Risk R4 from the master plan: watch for infinite refetch loops. Always include `loadX` in the dependency array (it's stable from Zustand).
- Do NOT add `subscribeWithSelector` subscriptions inside the store modules themselves — that's the pattern that easily loops and is hard to debug.

**Acceptance:** AC-P2-1.

### 2.3 — Mutation → list refresh (note-store cleanup)

`note-store.ts` already re-fetches on mutations (lines 140, 172, 198, 202). Audit to confirm, and extend to cover gaps:

- After `createNote` succeeds, also call `loadNotebooks()` — the created note incremented the target notebook's `note_count`.
- After `deleteNote` succeeds, also call `loadNotebooks()` — the deleted note decremented it.
- After `updateNote`, if `notebook_id` was in the updates, call `loadNotebooks()` (note moved between notebooks).

**Files to edit:**
- `frontend/src/stores/note-store.ts` — lines 131-148 (createNote), 151-179 (updateNote), 181-204 (deleteNote)

**Acceptance:** AC-P2-2, AC-P2-3.

### 2.4 — OutputsPanel auto-refresh on analysis tool-end

The agent emits tool_call events that we now parse in `providers.tsx`. Extend the thread-store to track the last time an analysis-producing tool ran, and have OutputsPanel subscribe to that marker.

**Implementation:**

1. In `frontend/src/stores/thread-store.ts`, add:
   ```ts
   lastAnalysisToolAt: number | null;  // default null
   bumpAnalysisTool: () => void;       // sets to Date.now()
   ```

2. In `frontend/src/app/providers.tsx`, inside the SSE tool_calls loop, after `appendToolCall`, check if the tool name is in `{"execute_python_script","execute_r_script","execute_notebook","generate_dashboard"}` and call `bumpAnalysisTool()`. But actually better: bump on tool *completion* rather than start. Since we detect end implicitly via `markRunningToolCallsDone()`, one simpler approach is to bump on any completed tool with a matching name, which happens when the next tool_call arrives OR at stream end. Call `bumpAnalysisTool()` right after `markRunningToolCallsDone()` if any just-closed tool had an analysis-producing name.

3. In `frontend/src/components/OutputsPanel.tsx`, add:
   ```ts
   const lastAnalysisToolAt = useThreadStore((s) => s.lastAnalysisToolAt);
   useEffect(() => {
     if (lastAnalysisToolAt !== null) {
       loadOutputs(); // existing function in the panel
     }
   }, [lastAnalysisToolAt]);
   ```

**Acceptance:** AC-P2-4.

### 2.5 — note-store `loadNote` needs a loading flag

`loadNote` doesn't flip any loading flag. Navigating to a note is silent.

**Files to edit:** `frontend/src/stores/note-store.ts`
- Add `isLoadingNote: boolean` to state (distinct from the list's `isLoading`)
- Flip it around the fetch in `loadNote` (lines 103-116)
- Export a selector `selectIsLoadingNote` for consumers

**Files to edit:** `frontend/src/components/NoteEditor.tsx`
- Consume `isLoadingNote`; when true, render `<LoadingSkeleton variant="card"/>` instead of the editor

**Acceptance:** AC-P2-5 (error path), AC-P2-7 (loading path).

### 2.6 — memory-store per-row delete state

`deleteMemory` sets `isLoading: true` on the whole list, which blanks the entire panel while a single row is being deleted. Fix by tracking per-row state.

**Files to edit:** `frontend/src/stores/memory-store.ts`
- Add `deletingIds: Set<number>` to state (or `Record<number, boolean>` if Set serialization causes issues — it won't with Zustand in memory)
- `deleteMemory`: add id to set around the fetch, remove on completion, surface inline error per-row on failure
- Stop flipping global `isLoading` in `deleteMemory`

**Files to edit:** `frontend/src/components/MemoryBrowser.tsx`
- Per-row: when this row's id is in `deletingIds`, show a small spinner inline on the delete button and disable it

**Acceptance:** AC-P2-2 (delete correctness without UI disruption).

---

## Verification Checklist (before declaring Phase 2 done)

Automated:
- [ ] `cd frontend && npx tsc --noEmit` — zero TypeScript errors
- [ ] `cd frontend && npm run build` — clean build
- [ ] Docker: deploy new build via the three-step cp/restart procedure above

Manual (requires running UI at http://localhost:3001):
- [ ] Open NotesBrowser, change notebook filter → list refetches within 150 ms (Network tab shows new `/api/agent/notes?notebook_id=...` request)
- [ ] Type in NotesBrowser search → debounced refetch 300 ms after keystroke stop
- [ ] Same for MemoryBrowser with category and importance filters
- [ ] Create a note in notebook A → sidebar count for A increments within 1 s
- [ ] Delete a note → sidebar count decrements, note removed from list
- [ ] Send an agent prompt that triggers `execute_python_script` → OutputsPanel refreshes within 1 s of tool completion
- [ ] Click a note → editor shows a skeleton then populates (not a blank pane)
- [ ] Delete a memory → only that row shows a spinner; rest of the panel remains interactive
- [ ] Toggle backend off (`docker stop pi_agent_backend`), click through every panel → each shows the `ErrorBanner` with a Retry button; clicking Retry re-attempts
- [ ] Every empty list view shows an EmptyState with a CTA
- [ ] Keyboard: Tab through the UI, every new button is reachable with visible focus ring

---

## When Phase 2 is done

1. Update master plan `.omc/plans/frontend-uxui-sweep.md` with a "Phase 2 completed" stamp at the bottom.
2. Save a project memory summarizing what was fixed (see `/home/daryn/.claude/projects/-home-daryn-parsnip/memory/` for format).
3. Do NOT auto-start Phase 3. Ask the user whether they want to proceed, then consult `phase-3-handoff.md`.

## Common traps

- **The OutputsPanel component currently loads on mount only.** Don't rewrite its data-fetching logic — just add the `useEffect` subscription hook. The existing mount-fetch and manual-refresh-button logic stays.
- **Server-side search** (fix 2.2) may return different results than the current client-side filter in `selectNotes`. That's intentional and correct — the backend is authoritative. But if you see fewer hits than expected, check the backend's `list_notes` endpoint in `agent/main.py` around line 1010 — the ILIKE `search` param uses `%{search}%` on title and content.
- **Don't confuse `isLoading` with `isLoadingNote`** in note-store after fix 2.5 — the list uses the former, the editor the latter.
- **psycopg3 gotcha** (in case you touch backend at all): see project memory `project_psycopg3_style.md`. Parameters are `%s`, never `$1`.
