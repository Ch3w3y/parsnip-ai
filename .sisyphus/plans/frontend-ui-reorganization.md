# Frontend UI Reorganization + KB Search Fix

## TL;DR
- **Agent prompt**: Switch from `kb_search` to `holistic_search` for better formatted KB results
- **UI reorganization**: Notebook editor swaps with chat panel on click; KB search becomes a header widget strip; markdown raw/rendered toggle; outputs organized by thread
- **All changes** are frontend-only (no backend/deployment changes)

## TODOs

### Wave 1 — KB Search + Header Widgets (Foundation)
- [x] **T1: Switch agent prompt to holistic_search**
- [x] **T2: Add KB widget to header center strip**
- [x] **T3: Refactor LeftSidebar** (remove KB from cycling)
- [x] **T4: Panel store enhancement** (add `centerView` state)

### Wave 2 — Notebook Swap + Markdown Toggle (Core)
- [x] **T5: AppShell center panel refactor** (conditional rendering: thread/notebook/welcome)
- [x] **T6: NoteEditor markdown toggle** (raw ↔ rendered)
- [x] **T7: Notebook → center swap trigger** (NotesBrowser click handler)
- [x] **T8: Back navigation** (NoteEditor back button)

### Wave 3 — Thread-Based Organization
- [x] **T9: OutputsPanel thread filtering**
- [x] **T10: NotesBrowser thread filter**
- [x] **T11: Thread context propagation**

## Final Verification Wave
- [x] **F1: Code Review** — All changes reviewed for correctness
- [x] **F2: Build + Type Check** — `npm run build` passes
- [x] **F3: Manual QA** — All scenarios tested
- [x] **F4: Regression Check** — No breaking changes
