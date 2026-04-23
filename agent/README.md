# Agent Runtime

FastAPI + LangGraph service on port `8000`. The agent owns tool orchestration,
retrieval, long-term memory, conversation checkpoints, model invocation, and
streaming responses back to the OpenWebUI pipeline adapter.

## Architecture

```text
graph.py (orchestrator)
  -> graph_nodes.py
       -> agent node      dynamic tool selection + guardrails
       -> classify node   complexity-based tier routing
  -> graph_llm.py         model alias resolution + multi-provider client
  -> graph_tools.py       tool pack definitions + request filtering
  -> graph_guardrails.py  circuit breaker, cascade fallback, message pruning
  -> graph_state.py       agent state, message helpers, memory loader
  -> graph_prompts.py     system identity / BASE_PROMPT
  -> tools/              LangChain tool implementations
```

The runtime uses OpenAI-compatible clients where possible. Provider-specific
model IDs are not hardcoded in graph code; they are resolved from `.env` through
`agent/config.py`.

## Configuration Contract

Model routing uses stable aliases:

- `FAST_MODEL`
- `SMART_MODEL`
- `REASONING_MODEL`
- `GRAPH_MODEL`
- `CLASSIFIER_MODEL`

`DEFAULT_LLM` and `RESEARCH_LLM` can point at aliases such as `smart` and
`reasoning`. Each alias can be a comma-separated fallback chain.

Supported provider modes are documented in `../docs/CONFIGURATION.md`.

## State and Memory

| Layer | Storage | Purpose |
|-------|---------|---------|
| Conversation checkpoints | PostgreSQL checkpoint tables | Resume in-flight sessions across restarts. |
| Long-term memory | `agent_memories` | Durable user/project facts. |
| Knowledge retrieval | `knowledge_chunks` | Vector and full-text search over ingested sources. |
| Structured datasets | Source-specific tables | Direct data access for forex and World Bank data. |

The FastAPI lifespan context opens the async PostgreSQL connection pool used by
LangGraph checkpointing.

## Tools

The agent exposes **68 tools** across 10 functional groups. All tools are
registered in [tools/\_\_init\_\_.py](tools/__init__.py), wired into tool packs
in [graph_tools.py](graph_tools.py), and the orchestrator loads them in
[graph.py](graph.py).

### Retrieval

Tools for searching and retrieving content from the knowledge base.

| Tool | Purpose | Source |
|------|---------|--------|
| [`holistic_search`](tools/holistic_search.py) | Layered knowledge retrieval across current events, research, established knowledge, personal notes, and code sources with intent-based reordering. | |
| [`adaptive_search`](tools/adaptive_search.py) | Complexity-aware retrieval that routes through web-first + HyDE + KB fusion based on query tier. | |
| [`kb_search`](tools/kb_search.py) | Targeted hybrid vector + full-text search (RRF) against a specific KB source. | |
| [`research`](tools/research.py) | Multi-query research that expands a topic into variants, runs parallel KB searches, and synthesizes results. | |
| [`get_document`](tools/get_document.py) | Reconstruct full document text from a source_id returned by search tools. | |
| [`timeline`](tools/timeline.py) | Chronological retrieval of KB content using published timestamps. | |
| [`search_with_filters`](tools/filtered_search.py) | KB search with explicit source, date, and user filters for precise scoping. | |
| [`arxiv_search`](tools/arxiv.py) | Search arXiv for scientific preprints and papers. | |
| [`web_search`](tools/web.py) | Live web search via SearXNG / Tavily / Brave with automatic backend selection. | |
| [`extract_webpage`](tools/web.py) | Fetch and extract clean text content from a URL. | |

### Knowledge Analysis

Tools for analyzing KB coverage, comparing sources, and finding related content.

| Tool | Purpose | Source |
|------|---------|--------|
| [`knowledge_gaps`](tools/knowledge_gaps.py) | Assess how well the KB covers a question and identify missing sources. | |
| [`compare_sources`](tools/compare_sources.py) | Compare how different KB sources (Wikipedia, arXiv, news, etc.) cover the same topic. | |
| [`find_similar`](tools/find_similar.py) | Find documents semantically similar to a known document using stored embeddings. | |
| [`generate_knowledge_graph`](tools/knowledge_graph.py) | Extract concepts and relationships from KB content and output a Mermaid diagram. | |

### Memory

Tools for persisting and recalling user facts, preferences, and decisions across sessions.

| Tool | Purpose | Source |
|------|---------|--------|
| [`save_memory`](tools/memory.py) | Save a fact, preference, decision, project context, or person to long-term memory. | |
| [`recall_memory`](tools/memory.py) | Search long-term memories by query and category. | |
| [`update_memory`](tools/memory.py) | Update the content or importance of an existing memory. | |
| [`delete_memory`](tools/memory.py) | Soft-delete a memory so it no longer appears in recall results. | |
| [`recall_memory_by_category`](tools/memory.py) | List all memories in a specific category without needing a search query. | |
| [`summarize_memories`](tools/memory.py) | Consolidate and summarize memories using LLM analysis for a high-level overview. | |

### Joplin Notebook Operations

Direct PostgreSQL access to Joplin Server for creating, reading, updating, and deleting notebooks, notes, tags, and resources.

| Tool | Purpose | Source |
|------|---------|--------|
| [`joplin_create_notebook`](tools/joplin_pg.py) | Create a new Joplin notebook (folder). | |
| [`joplin_create_note`](tools/joplin_pg.py) | Create a new note with title and Markdown body in a notebook. | |
| [`joplin_update_note`](tools/joplin_pg.py) | Update an existing note's title or content. | |
| [`joplin_edit_note`](tools/joplin_pg.py) | Edit a note by appending content to its body. | |
| [`joplin_delete_note`](tools/joplin_pg.py) | Delete a note by ID. | |
| [`joplin_get_note`](tools/joplin_pg.py) | Retrieve a note's title, body, and metadata by ID. | |
| [`joplin_search_notes`](tools/joplin_pg.py) | Full-text search across all Joplin notes. | |
| [`joplin_list_notebooks`](tools/joplin_pg.py) | List all Joplin notebooks with note counts. | |
| [`joplin_list_tags`](tools/joplin_pg.py) | List all tags in Joplin. | |
| [`joplin_get_tags_for_note`](tools/joplin_pg.py) | Get all tags attached to a specific note. | |
| [`joplin_upload_resource`](tools/joplin_pg.py) | Upload a binary resource (image, attachment) to a Joplin note. | |
| [`joplin_ping`](tools/joplin_pg.py) | Health check — verify Joplin database connectivity. | |

### Joplin HITL Workflow

Human-in-the-Loop workflow for generated notes. Tracks LLM versions so user edits can be detected, reviewed, and published.

| Tool | Purpose | Source |
|------|---------|--------|
| [`generate_note`](tools/joplin_hitl.py) | Create a note via Joplin and register it in the HITL session table for edit tracking. | |
| [`detect_edits`](tools/joplin_hitl.py) | Compare the current note content hash against the stored LLM hash to detect user edits. | |
| [`review_edited_note`](tools/joplin_hitl.py) | Produce a unified diff between the LLM's original content and the user's current version. | |
| [`publish_review`](tools/joplin_hitl.py) | Update the note with reviewed content and increment the HITL cycle count. | |

**HITL workflow:**

```text
generate_note  →  detect_edits  →  review_edited_note  →  publish_review
     │                  │                   │                     │
  Create note      Check if user      Show diff of LLM vs    Push reviewed
  + track hash     edited content      user edits             version back
```

### GitHub

Tools for repository discovery, code reading, issue tracking, and pull requests via the GitHub REST API.

| Tool | Purpose | Source |
|------|---------|--------|
| [`github_search_repos`](tools/github.py) | Search GitHub repositories by query. | |
| [`github_get_file`](tools/github.py) | Fetch file or directory contents from a repository. | |
| [`github_list_commits`](tools/github.py) | List recent commits for a repository or branch. | |
| [`github_search_code`](tools/github.py) | Search code within GitHub repositories. | |
| [`github_list_issues`](tools/github.py) | List open or closed issues for a repository. | |
| [`github_create_issue`](tools/github.py) | Create a new issue on a repository. | |
| [`github_list_pull_requests`](tools/github.py) | List pull requests for a repository. | |
| [`github_get_readme`](tools/github.py) | Fetch and decode a repository's README. | |
| [`github_get_repo_structure`](tools/github.py) | Get the directory tree structure of a repository. | |
| [`github_create_pr`](tools/github.py) | Create a pull request on a repository. | |
| [`github_list_branches`](tools/github.py) | List branches in a repository. | |

### Workspace & Analysis

File system operations and code execution on the analysis server. Workspace tools manage files and run commands; analysis tools execute Python/R scripts and manage scheduled jobs.

| Tool | Purpose | Source |
|------|---------|--------|
| [`list_workspace`](tools/workspace.py) | List files and directories in a workspace path. | |
| [`read_workspace_file`](tools/workspace.py) | Read file content from the workspace. | |
| [`write_workspace_file`](tools/workspace.py) | Write content to a workspace file, creating directories as needed. | |
| [`make_workspace_dir`](tools/workspace.py) | Create a directory in the workspace. | |
| [`delete_workspace_item`](tools/workspace.py) | Delete a file or empty directory. | |
| [`move_workspace_item`](tools/workspace.py) | Move or rename a file or directory. | |
| [`execute_bash_command`](tools/workspace.py) | Run an arbitrary bash command on the analysis server with DB env vars set. | |
| [`write_and_execute_script`](tools/workspace.py) | Write a script to the workspace and execute it atomically (with optional tests). | |
| [`execute_workspace_script`](tools/workspace.py) | Atomic write+execute for script iteration — no tests, fastest loop for fixing errors. | |
| [`execute_python_script`](tools/analysis_server.py) | Execute a Python script on the analysis server with optional Joplin note saving. | |
| [`execute_r_script`](tools/analysis_server.py) | Execute an R script on the analysis server with optional Joplin note saving. | |
| [`list_analysis_outputs`](tools/analysis_server.py) | List all generated output files from previous analysis runs. | |
| [`execute_notebook`](tools/analysis_server.py) | Execute a Jupyter notebook with code and markdown cells. | |
| [`generate_dashboard`](tools/analysis_server.py) | Generate an HTML dashboard from multiple analysis scripts. | |
| [`create_scheduled_job`](tools/analysis_server.py) | Create a cron-scheduled analysis job. | |
| [`list_scheduled_jobs`](tools/analysis_server.py) | List all scheduled analysis jobs. | |
| [`delete_scheduled_job`](tools/analysis_server.py) | Delete a scheduled analysis job by ID. | |

### Personal Notes & PDF Ingestion

Tools for saving user notes directly to the KB and uploading PDF documents.

| Tool | Purpose | Source |
|------|---------|--------|
| [`save_note`](tools/notes.py) | Save a research note or summary to the KB as `user_notes` source, immediately searchable. | |
| [`list_documents`](tools/notes.py) | List all user-uploaded PDFs and saved notes with chunk counts and upload dates. | |
| [`ingest_pdf`](tools/ingest.py) | Upload a PDF document to the KB for search and retrieval (by URL or base64 content). | |

### System

| Tool | Purpose | Source |
|------|---------|--------|
| [`system_status`](tools/system.py) | Check health and status of all system components (Ollama, PostgreSQL, KB stats, memory count, GCS). | |

## Tool Packs

Tools are grouped into packs that are activated based on the classified task
intent. The full set is defined in [graph_tools.py](graph_tools.py):

| Pack | Tools | Purpose |
|------|-------|---------|
| `core` | `adaptive_search`, `holistic_search`, `kb_search`, `web_search`, `extract_webpage`, `get_document`, `save_memory`, `recall_memory`, `system_status` | Always available baseline set. |
| `research` | `research`, `timeline`, `knowledge_gaps`, `compare_sources`, `find_similar`, `arxiv_search`, `search_with_filters`, `generate_knowledge_graph`, `ingest_pdf`, `save_note`, `list_documents` | Deep investigation and synthesis. |
| `analysis` | `execute_python_script`, `execute_r_script`, `list_analysis_outputs`, `execute_notebook`, `generate_dashboard`, `create_scheduled_job`, `list_scheduled_jobs`, `delete_scheduled_job` | Code execution and dashboards. |
| `workspace` | `list_workspace`, `read_workspace_file`, `write_workspace_file`, `make_workspace_dir`, `delete_workspace_item`, `move_workspace_item`, `execute_bash_command`, `write_and_execute_script`, `execute_workspace_script` | File management and script iteration. |
| `github` | `github_search_repos`, `github_get_file`, `github_list_commits`, `github_search_code`, `github_list_issues`, `github_create_issue`, `github_list_pull_requests`, `github_get_readme`, `github_get_repo_structure`, `github_create_pr`, `github_list_branches` | Repository discovery and code reading. |
| `notes` | All 12 Joplin PG tools + `save_note` + `list_documents` | Full notebook workflow. |
| `memory` | `save_memory`, `recall_memory`, `update_memory`, `delete_memory`, `recall_memory_by_category`, `summarize_memories` | Long-term recall. |
| `system` | `system_status` | Health check. |

Composite packs stack on `core`:

```text
core          → core tools
research      → core + research
analysis      → core + research + analysis + workspace
workspace     → core + workspace + analysis
github        → core + github + workspace
notes         → core + notes
memory        → core + memory
```

High-complexity requests automatically receive research tools regardless of
the primary intent pack.

## Routing Behavior

`agent/tools/router.py` classifies complex prompts by score, tier, and intent:

```text
User prompt -> classify_complexity() -> ComplexityResult(score, tier, intent)
```

The tier controls model selection and search depth. Intent controls preferred
knowledge layers, for example code-oriented prompts bias toward GitHub and
research prompts bias toward arXiv/bioRxiv before broader sources.

Thresholds, layer budgets, and intent/source mappings are in
`agent/tools/router.py`. Concrete model choices remain in `.env`.

## Adding a New Tool

1. Create `agent/tools/<name>.py` with a `@tool` decorated async function.
2. Import and export it in [tools/\_\_init\_\_.py](tools/__init__.py).
3. Add it to the appropriate tool list in [graph_tools.py](graph_tools.py):
   - Append to an existing pack list (`CORE_TOOLS`, `RESEARCH_TOOLS`,
     `ANALYSIS_TOOLS`, `WORKSPACE_TOOLS`, `GITHUB_TOOLS`, `NOTE_TOOLS`,
     `MEMORY_TOOLS`), or create a new list.
   - If creating a new pack, add it to `TOOL_PACKS` and update `_select_tools_for_request`.
4. Add concise guidance to `BASE_PROMPT` in [graph_prompts.py](graph_prompts.py)
   so the model knows when to use the tool.
5. Add tests for behavior that affects routing, persistence, or user-visible
   output.

## Adding a New Knowledge Source

If the new source is searchable through the knowledge base:

1. Add or update the ingestion pipeline under `ingestion/`.
2. Keep the `source` column value stable and unique.
3. Register source-specific embedding behavior in [tools/kb_search.py](tools/kb_search.py)
   if it does not use the default text embedder.
4. Update router source mappings and holistic-search layers where the new source
   should appear.
5. Document the source in `../ingestion/README.md`.