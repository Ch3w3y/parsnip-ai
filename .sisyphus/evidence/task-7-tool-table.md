# Task 7: Tool Table — Evidence

## Verification

- **`__init__.py` exports**: 68 tools
- **README tool count**: 68 tools ✓
- **Source files**: 22 tool files (excluding `__init__.py`, `db_pool.py`, `embed.py`, `llm_client.py`, `router.py`, `pdf_ingest.py`, `joplin_mcp.py`)
- **graph_tools.py `TOOLS` list**: Deduplicated union of all pack lists, verified matches `__init__.py`

## Tool Groups and Counts

| Group | Count | Source File(s) |
|-------|-------|----------------|
| Retrieval | 10 | holistic_search.py, adaptive_search.py, kb_search.py, research.py, get_document.py, timeline.py, filtered_search.py, arxiv.py, web.py |
| Knowledge Analysis | 4 | knowledge_gaps.py, compare_sources.py, find_similar.py, knowledge_graph.py |
| Memory | 6 | memory.py |
| Joplin PG | 12 | joplin_pg.py |
| Joplin HITL | 4 | joplin_hitl.py |
| GitHub | 11 | github.py |
| Workspace & Analysis | 17 | workspace.py (9) + analysis_server.py (8) |
| Personal Notes & PDF | 3 | notes.py (2) + ingest.py (1) |
| System | 1 | system.py |
| **Total** | **68** | |

## Cross-reference with graph_tools.py Packs

- `CORE_TOOLS` (8): adaptive_search, holistic_search, kb_search, web_search, extract_webpage, get_document, save_memory, recall_memory, system_status
- `RESEARCH_TOOLS` (10): research, timeline, knowledge_gaps, compare_sources, find_similar, arxiv_search, search_with_filters, generate_knowledge_graph, ingest_pdf, save_note, list_documents
- `ANALYSIS_TOOLS` (8): execute_python_script, execute_r_script, list_analysis_outputs, execute_notebook, generate_dashboard, create_scheduled_job, list_scheduled_jobs, delete_scheduled_job
- `WORKSPACE_TOOLS` (9): list_workspace, read_workspace_file, write_workspace_file, make_workspace_dir, delete_workspace_item, move_workspace_item, execute_bash_command, write_and_execute_script, execute_workspace_script
- `GITHUB_TOOLS` (11): all 11 github_* tools
- `NOTE_TOOLS` (14): all 12 joplin_pg tools + save_note + list_documents
- `MEMORY_TOOLS` (6): all 6 memory tools

## Files Modified

- `agent/README.md` — Complete rewrite with all 68 tools cataloged in 10 groups

## Changes from Previous README

- Expanded tool table from 6 rows to 68 rows across 10 categories
- Added Tool Packs section mapping to graph_tools.py definitions
- Added HITL workflow diagram for joplin_hitl.py
- Updated "Adding a New Tool" section to reference graph_tools.py + graph_prompts.py wiring
- Added source file links for all tools