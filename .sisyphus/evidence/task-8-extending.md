# Task 8: Rewrite EXTENDING.md — Evidence

## Summary
Rewrote `docs/EXTENDING.md` from 37 lines to a 365-line comprehensive extension guide covering all extension patterns in the Parsnip stack.

## File Modified
- `docs/EXTENDING.md` (37 → 365 lines)

## Sections Delivered

| Section | Lines | Key Content |
|---------|-------|-------------|
| Ingestion Extension | 39 | Four-stage pipeline (fetch → save_raw → process → upsert), `--from-raw` replay, sources.yaml schema table, conflict strategies |
| SourceRegistry Pattern | 24 | Auto-discovery mechanism, YAML-first + filesystem scan, `register_source()` programmatic API, two-thing minimum to add a source |
| Routing System Integration | 49 | `ROUTING_CONFIG` intent_layers + layer_budgets, `SOURCE_MODEL_MAP` for non-default embedders, kb_search docstring + holistic_search layer integration, cross-ref to ROUTING.md |
| Tool Extension | 35 | Four-file pattern (tool file → __init__.py → graph_tools.py → graph_prompts.py), tool pack strategy table, `_select_tools_for_request()` reference |
| Frontend Extension | 14 | `makeAssistantToolUI` pattern, render function signature, ToolUIRegistry component, `renderGenericTool` fallback, cross-ref to FRONTEND.md |
| Structured Data Extension | 18 | `forex_rates` pattern reference, upsert function template, `records_to_chunks()` dual-write strategy, preflight validation guidance |
| Connection Pool Extension | 32 | `init_pool()` / `get_pool()` / `close_all()` API, startup/shutdown lifecycle, named pool strategy |
| Complete Example | 150 | Hypothetical "SEC filings" source end-to-end: ingestion script, sources.yaml entry, ROUTING_CONFIG update, docstring updates, documentation, verification checklist |

## Verification
- Every section ≥ 5 lines (minimum 14, maximum 150)
- Every section maps to real files/commands in the repo
- Cross-references used instead of duplicating content (ingestion/README.md, ROUTING.md, FRONTEND.md)
- sources.yaml schema reference points to ingestion/README.md
- Concrete example walks through all 5 integration steps with runnable code
- Source files verified: registry.py (254 lines), router.py (292 lines), graph_tools.py (242 lines), graph_prompts.py (78 lines), __init__.py (144 lines), db_pool.py (118 lines), ToolUIs.tsx (416 lines), ingest_arxiv.py (268 lines), ingest_forex.py (362 lines)