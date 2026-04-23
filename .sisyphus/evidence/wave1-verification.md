# Wave 1 Evidence - Verification Results

## Task 1: Port Fix
- **Status**: PASS
- **Changed**: ARCHITECTURE.md line 11: `Next.js :3000` → `Next.js :3001`
- **Verified**: 
  - ARCHITECTURE_VISUALS.md line 14: already correct (`assistant-ui :3001`)
  - DEPLOYMENT.md line 30: `OpenWebUI :3000` preserved (legacy)
  - ARCHITECTURE_VISUALS.md line 17: `OpenWebUI :3000` preserved (legacy)
  - No other `assistant-ui :3000` references found in docs/

## Task 2: Ingestion Sources
- **Status**: PASS (with note)
- **Changed**: ingestion/README.md: Added 4 sources (hackernews, pubmed, rss, ssrn)
- **Discovery**: sources.yaml has 14 sources, not 13 (wikipedia and wikipedia_updates are separate sources)
- **Verified**: 
  - Table now has 14 rows matching YAML count
  - Sorting: 7 scheduled + 7 manual-only
  - Conflict strategy rationale covers all sources

## Task 3: Docs Index
- **Status**: PASS
- **Changed**: docs/README.md: Added ROUTING.md link
- **Verified**: All 7 .md files in docs/ are now listed in the index

## Task 4: Routing Verification
- **Status**: PASS
- **Changed**: docs/ROUTING.md: Updated intent_layers to match router.py exactly
- **Verified**: 
  - code: ["github", "wikipedia", "joplin_notes"] ✓
  - research: ["arxiv", "biorxiv", "wikipedia", "news"] ✓
  - current: ["news", "wikipedia"] ✓
  - general: ["wikipedia", "github", "joplin_notes", "arxiv", "news"] ✓
  - layer_budgets: github=6, arxiv=4, biorxiv=4, wikipedia=3, news=4, joplin_notes=2 ✓

## Task 5: Configuration Updates
- **Status**: PASS
- **Changed**: docs/CONFIGURATION.md:
  - Marked JOPLIN_MCP_URL as deprecated
  - Added PARSNIP_CIRCUIT_BREAKER_PATH section
  - Added GUARDRAIL_MODE section with strict/balanced/lenient descriptions
  - Added cross-reference to ROUTING.md for SOURCE_MODEL_MAP
- **Verified**: All env vars from .env.example are either documented or explicitly marked deprecated
