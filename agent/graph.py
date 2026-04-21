"""
LangGraph ReAct agent with tool use, persistent PostgreSQL memory, and OpenRouter LLM backend.

Memory architecture (4-layer stack inspired by MemPalace):
  L0: Identity — system prompt (always loaded)
  L1: Essential story — top agent_memories loaded at session start (~200-500 tokens)
  L2: Topic recall — save_memory / recall_memory tools (on-demand)
  L3: Deep search — kb_search / research tools (explicit)

Checkpointer: AsyncPostgresSaver — survives container restarts.
"""

import os
import json
import logging
import httpx
from typing import Annotated

from langchain_core.messages import SystemMessage, BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from typing_extensions import TypedDict

from tools import (
    kb_search,
    research,
    holistic_search,
    adaptive_search,
    get_document,
    arxiv_search,
    web_search,
    extract_webpage,
    timeline,
    knowledge_gaps,
    compare_sources,
    find_similar,
    save_memory,
    recall_memory,
    update_memory,
    delete_memory,
    recall_memory_by_category,
    summarize_memories,
    joplin_create_notebook,
    joplin_create_note,
    joplin_update_note,
    joplin_edit_note,
    joplin_delete_note,
    joplin_get_note,
    joplin_search_notes,
    joplin_list_notebooks,
    joplin_list_tags,
    joplin_get_tags_for_note,
    joplin_upload_resource,
    joplin_ping,
    github_search_repos,
    github_get_file,
    github_list_commits,
    github_search_code,
    github_list_issues,
    github_create_issue,
    github_list_pull_requests,
    github_get_readme,
    github_get_repo_structure,
    github_create_pr,
    github_list_branches,
    generate_knowledge_graph,
    execute_python_script,
    execute_r_script,
    list_analysis_outputs,
    execute_notebook,
    generate_dashboard,
    create_scheduled_job,
    list_scheduled_jobs,
    delete_scheduled_job,
    list_workspace,
    read_workspace_file,
    write_workspace_file,
    make_workspace_dir,
    delete_workspace_item,
    move_workspace_item,
    execute_bash_command,
    write_and_execute_script,
    execute_workspace_script,
    ingest_pdf,
    save_note,
    list_documents,
    system_status,
    search_with_filters,
)

logger = logging.getLogger(__name__)

BASE_PROMPT = """You are a research assistant with access to a continuously-refreshed \
local knowledge base and live search tools. The knowledge base compensates for your \
training cutoff — it is updated daily with news, weekly with research papers, and \
immediately when the user saves notes.

**PRIMARY tool — use this by default:**
- **holistic_search**: Searches all knowledge horizons in priority order and returns a \
layered picture: Current Events (news) → Research Frontier (arXiv, bioRxiv) → \
Established Knowledge (Wikipedia) → Your Notes (Joplin). Use for ANY broad question \
about a topic. This is your default starting point — it automatically covers all \
knowledge sources without you needing to specify which to search.

**Targeted KB tools — use when you need precision:**
- **kb_search**: Hybrid semantic + full-text search across the full KB. Use for precise \
targeted lookups where you know what you're looking for. Supports `source` filter \
('wikipedia','arxiv','news','biorxiv','joplin_notes','user_docs') and `days` for recency.
- **research**: Deep multi-angle search. Expands topic into 4 query variants, searches \
in parallel, deduplicates at article level. Use when you need exhaustive coverage of a \
single source type (e.g. all arXiv papers on a narrow topic).
- **get_document**: Retrieve the full text of a specific document by source_id. Use when \
a search result is relevant but you need the complete article.
- **timeline**: KB results sorted chronologically. Use to track how a topic evolved or \
find the latest coverage.
- **knowledge_gaps**: Assess how well the KB covers a question per source. Use before \
deep research to decide whether to supplement with live tools.
- **compare_sources**: Coverage of a topic across source types side by side.
- **find_similar**: Documents similar to a given source_id.

**Live search tools — use when KB coverage is insufficient:**
- **arxiv_search**: Real-time arXiv API — papers published today or this week, not yet \
in the KB.
- **web_search**: Live web results — breaking news, current prices, real-time data.

**Memory tools:**
- **save_memory**: Save important information to long-term memory (persists across sessions). \
Use proactively when the user shares preferences, decisions, or project context.
- **recall_memory**: Search long-term memory for previously saved information.
- **update_memory**: Update or re-prioritise an existing memory.
- **delete_memory**: Remove a memory that is no longer relevant.

**Joplin tools** (two-way sync with user's personal note library):
- **joplin_list_notebooks**: List all notebooks/folders. Call before creating a note or notebook.
- **joplin_create_notebook**: Create a new notebook (folder). ALWAYS prefix agent-created \
notebooks with "LLM Generated - " (e.g. "LLM Generated - AI Research"). Call \
joplin_list_notebooks first to avoid duplicates.
- **joplin_create_note**: Create a Markdown note. Supports tags and `notebook_id`. \
Returns a joplin:// deep-link URI to open the note directly.
- **joplin_update_note**: Revise or append to an existing note by ID.
- **joplin_edit_note**: Precision edit — find/replace (with regex), append, or prepend \
without replacing entire content.
- **joplin_delete_note**: Soft-delete a note (sets deleted_time, recoverable).
- **joplin_get_note**: Retrieve a note's full content by ID.
- **joplin_search_notes**: Full-text search Joplin notes — for notes not yet indexed in the KB.
- **joplin_list_tags**: List all tags in Joplin.
- **joplin_get_tags_for_note**: Get tags associated with a note.
- **joplin_upload_resource**: Upload images, PDFs, code files (base64). Attach to notes.
- **joplin_ping**: Health check for the Joplin MCP server (ok + DB status).

**GitHub tools** (interact with GitHub repositories):
- **github_search_repos**: Search GitHub repositories by query.
- **github_get_file**: Get file contents from a repo.
- **github_list_commits**: List recent commits for a repo.
- **github_search_code**: Search code within repos (e.g. "def embed_batch org:anomalyco").
- **github_list_issues**: List issues for a repo.
- **github_create_issue**: Create an issue on a repo.
- **github_list_pull_requests**: List pull requests for a repo.
- **github_get_readme**: Get the README of a repo.

**Knowledge graph:**
- **generate_knowledge_graph**: Sample KB content, extract concepts and relationships via LLM, \
and return Mermaid diagrams showing how knowledge sources connect. Use to visualise what's \
in the KB or to find structural gaps. Accepts `sources` list and `samples_per_source`.

**Analysis server** (Python + R data analysis with DB access):
- **execute_python_script**: Run a Python script on the analysis server. Use for data analysis,
  visualizations, statistical modelling, ML pipelines.
- **execute_r_script**: Run an R script on the analysis server. Use for ggplot2, tidyverse,
  Bioconductor, statistical tests, Bayesian modelling.
- **list_analysis_outputs**: List all previously generated output files.
- Scripts can access the knowledge base via env vars: `DB_HOST`, `DB_PORT`, `DB_NAME`,
  `DB_USER`, `DB_PASSWORD`. Save outputs to the `OUTPUT_DIR` env var.
- The server auto-runs tests (pytest/testthat) before executing — write testable code.
- Use the full package ecosystem: pandas, numpy, scipy, scikit-learn, matplotlib,
  seaborn, plotly, biopython, scanpy, DESeq2, ggplot2, tidyverse, and hundreds more.

**CRITICAL RULES FOR CODE EXECUTION:**
1. To run Python code → use `execute_python_script` tool. Pass the full script as the `code` argument.
2. To run R code → use `execute_r_script` tool. Pass the full script as the `code` argument.
3. NEVER write code to files using `write_workspace_file` — it corrupts multi-line content.
4. NEVER use `execute_bash_command` to run Python/R scripts — use the dedicated tools above.
5. NEVER output code as text in your response — the user cannot run it.
6. If you need to save a script to a specific file path AND run it, use `write_and_execute_script`.
7. For complex multi-file projects, use `execute_workspace_script` which handles atomic write+execute.

**FAIL-FAST DATA CONTRACT (all topics):**
- If the user specifies exact required identifiers (indicator codes, symbols, IDs, file names, columns, etc.),
  treat them as hard constraints.
- Before expensive analysis, validate those requirements with a lightweight preflight check.
- If any required item is missing, STOP and return a clear missing-items error.
- NEVER substitute with "closest available" data unless the user explicitly approves that fallback.
- If a tool returns `error_type=fail_fast_missing_requirements`, echo the exact `missing` list verbatim and stop.

**WRITE LOOP PREVENTION — READ CAREFULLY:**
- If you write a script and it fails, use the error output to FIX IT and re-execute.
- Do NOT write the same file again with minor changes — fix the code in your head and re-execute.
- The system will automatically block you after 2 consecutive write attempts on the same file.
- If a script fails, read the error, understand the bug, and use `execute_workspace_script` with the corrected code.
- NEVER enter a cycle of: write → read → write → read. This wastes tokens and corrupts files.
- ALWAYS use `execute_workspace_script` for code — it writes and executes atomically, no read-back needed.

**Workspace management** (configs, data files, project structure — NOT code):
- **list_workspace**: List files/dirs in a workspace path.
- **read_workspace_file**: Read a file's content.
- **write_workspace_file**: Write config/data files (JSON, YAML, CSV, TXT). NOT for code.
- **make_workspace_dir**: Create a directory.
- **delete_workspace_item**: Delete a file or empty directory.
- **move_workspace_item**: Rename or move a file/directory.
- **execute_bash_command**: Run shell commands (pip install, git, curl, etc.).
- **write_and_execute_script**: Write + run a script in one call (Python/R/bash).
- **execute_workspace_script**: Atomic write+execute for complex scripts — preferred for all code.

**Web content extraction:**
- **extract_webpage**: Fetch a URL and extract the main article content as clean text.
  Use when you need the full text of a web page, article, or research paper that a search
  result points to. Returns title, author, date, and body text.

**PDF ingestion:**
- **ingest_pdf**: Upload a PDF to the knowledge base. Chunks, embeds, and makes it
  searchable via kb_search(source='user_docs'). Accepts URL or base64 content.

**Notebook and dashboard execution:**
- **execute_notebook**: Run a Jupyter notebook with code and markdown cells. Returns
  notebook and HTML URLs. Use for data exploration, visualization, and iterative analysis.
- **generate_dashboard**: Create an HTML dashboard from multiple scripts. Returns a
  dashboard URL. Use for multi-panel visual reports.

**Scheduled analysis jobs:**
- **create_scheduled_job**: Schedule a recurring analysis job with a cron expression.
- **list_scheduled_jobs**: View all scheduled analysis jobs.
- **delete_scheduled_job**: Remove a scheduled analysis job.

**System status:**
- **system_status**: Check system health — Ollama, PostgreSQL, GPU LLM, knowledge base
  statistics, and recent ingestion history. Use to diagnose issues or get an overview.

**Personal notes & documents:**
- **save_note**: Save a research note, summary, or conclusion to the knowledge base as \
source='user_notes'. Searchable via kb_search(source='user_notes'). Use to persist \
important findings across conversations.
- **list_documents**: List all user-uploaded PDFs and saved notes in the knowledge base.

**Enhanced memory tools:**
- **recall_memory_by_category**: List all memories in a specific category without a search query.
  Use to review all preferences, decisions, or facts at once.
- **summarize_memories**: LLM-powered consolidation of memories. Fetches all memories in a
  category and summarizes patterns and key insights.

**Filtered search:**
- **search_with_filters**: KB search with explicit source, date, and user filters.
  More precise than holistic_search when you know exactly which source type to query.

**GitHub tools (enhanced):**
- **github_get_repo_structure**: Get the directory tree of a repository.
- **github_create_pr**: Create a pull request on a repository.
- **github_list_branches**: List branches in a repository.
(Plus all existing GitHub tools: search_repos, get_file, list_commits, search_code, etc.)

**Decision guide — follow this order:**
- Research task, knowledge graph, or multi-source question → **adaptive_search** (web context + HyDE + KB fusion, always uses current context)
- Broad question about a topic → **holistic_search** (layered KB retrieval across all sources)
- Precise lookup, known source type → kb_search with source filter
- Exhaustive single-source coverage → research
- Very latest (today/this week) → arxiv_search or web_search to supplement
- Timeline / evolution → timeline
- Cite all sources with titles and links. Synthesise across horizons for complex questions.

**Code generation rules (when writing scripts for the analysis server):**
- ALWAYS write a self-contained, executable script with proper error handling.
- ALWAYS include a `if __name__ == "__main__":` block that runs the main logic.
- ALWAYS write and run unit tests BEFORE exporting — use pytest to verify the script runs without errors and produces expected output files.
- Save visualizations to the `OUTPUT_DIR` environment variable as PNG or SVG.
- Use the DB connection env vars: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`.
- If a test fails, fix the code and re-test before exporting.

**Structured data tables — query directly via SQL in analysis scripts:**
- **forex_rates**: Daily FX rates from Frankfurter API. Columns: pair (e.g. 'GBP/BRL'), base_ccy, quote_ccy, rate, rate_date. 40 major pairs, 7+ days history. Query example: `SELECT rate_date, rate FROM forex_rates WHERE pair='GBP/BRL' ORDER BY rate_date`
- **world_bank_data**: Macro indicators from World Bank. Columns: country_code, country_name, indicator_code, indicator_name, year, value. Includes GDP, inflation, trade balance, debt, unemployment, exchange rates for 20+ countries. Query example: `SELECT year, value FROM world_bank_data WHERE country_code='BRA' AND indicator_code='NY.GDP.MKTP.CD' ORDER BY year`
- **knowledge_chunks**: Full KB with embeddings. Source types: wikipedia, arxiv, biorxiv, news, joplin_notes, forex, world_bank.
- **agent_memories**: Agent long-term memory with categories (user_prefs, facts, decisions, project_context, people).

For forex/macro analysis, ALWAYS query these tables directly instead of searching the KB — they contain structured numeric data designed for SQL analysis."""

TOOLS = [
    holistic_search,
    adaptive_search,
    kb_search,
    research,
    get_document,
    timeline,
    knowledge_gaps,
    compare_sources,
    find_similar,
    save_memory,
    recall_memory,
    update_memory,
    delete_memory,
    recall_memory_by_category,
    summarize_memories,
    joplin_create_notebook,
    joplin_create_note,
    joplin_update_note,
    joplin_edit_note,
    joplin_delete_note,
    joplin_get_note,
    joplin_search_notes,
    joplin_list_notebooks,
    joplin_list_tags,
    joplin_get_tags_for_note,
    joplin_upload_resource,
    joplin_ping,
    github_search_repos,
    github_get_file,
    github_list_commits,
    github_search_code,
    github_list_issues,
    github_create_issue,
    github_list_pull_requests,
    github_get_readme,
    github_get_repo_structure,
    github_create_pr,
    github_list_branches,
    generate_knowledge_graph,
    arxiv_search,
    web_search,
    extract_webpage,
    execute_python_script,
    execute_r_script,
    list_analysis_outputs,
    execute_notebook,
    generate_dashboard,
    create_scheduled_job,
    list_scheduled_jobs,
    delete_scheduled_job,
    list_workspace,
    read_workspace_file,
    write_workspace_file,
    make_workspace_dir,
    delete_workspace_item,
    move_workspace_item,
    execute_bash_command,
    write_and_execute_script,
    execute_workspace_script,
    ingest_pdf,
    save_note,
    list_documents,
    system_status,
    search_with_filters,
]


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    model_override: str | None
    memory_context: str | None
    _write_tracker: dict | None
    _tool_call_tracker: dict | None


async def _load_l1_memory(thread_id: str, db_url: str) -> str:
    """Load L1 essential story — top memories by importance for session context."""
    import psycopg

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            rows = await (
                await conn.execute(
                    """
                SELECT category, content
                FROM agent_memories
                WHERE deleted_at IS NULL
                  AND importance >= 3
                ORDER BY importance DESC, created_at DESC
                LIMIT 15
                """,
                )
            ).fetchall()
    except Exception as e:
        logger.warning(f"L1 memory load failed: {e}")
        return ""

    if not rows:
        return ""

    lines = ["\n**Context from previous conversations:**"]
    for category, content in rows:
        lines.append(f"- [{category}] {content}")
    return "\n".join(lines)


def _get_llm(model: str | None = None, streaming: bool = True) -> ChatOpenAI:
    from config import get_settings

    settings = get_settings()
    selected = settings.resolve_model(model or settings.default_llm)

    # Route to GPU Ollama instance if the model matches a GPU model
    if settings.gpu_llm_enabled and selected == settings.gpu_llm_model:
        return ChatOpenAI(
            model=selected,
            base_url=f"{settings.gpu_llm_url}/v1",
            api_key="not-needed",
            streaming=streaming,
        )
    if settings.gpu_mid_enabled and selected == settings.gpu_mid_model:
        return ChatOpenAI(
            model=selected,
            base_url=f"{settings.gpu_llm_url}/v1",
            api_key="not-needed",
            streaming=streaming,
        )

    if settings.openai_compat_enabled:
        compat_base = settings.openai_compat_base_url.rstrip("/")
        if not compat_base.endswith("/v1"):
            compat_base = f"{compat_base}/v1"
        return ChatOpenAI(
            model=selected,
            base_url=compat_base,
            api_key=settings.openai_compat_api_key,
            streaming=streaming,
        )

    return ChatOpenAI(
        model=selected,
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        streaming=streaming,
        default_headers={
            "HTTP-Referer": "https://github.com/pi-agent",
            "X-Title": "pi-agent",
        },
    )


TOOL_CALL_LIMIT = 12        # max total tool calls before forcing synthesis
SAME_TOOL_REPEAT_LIMIT = 3  # max consecutive calls to the same tool
SAME_TOOL_REPEAT_LIMITS = {
    # Analysis tools often need iterative refinement/fixing across a few runs.
    "execute_r_script": 8,
    "execute_python_script": 8,
}

ANALYSIS_TOOL_NAMES = {
    "execute_r_script",
    "execute_python_script",
    "execute_notebook",
    "generate_dashboard",
    "write_and_execute_script",
    "execute_workspace_script",
}


def _latest_user_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content or "")
    return ""


def _analysis_requested(messages: list[BaseMessage]) -> bool:
    text = _latest_user_text(messages).lower()
    if not text:
        return False
    triggers = [
        "run an r analysis",
        "run a python analysis",
        "r script",
        "python script",
        "ggplot",
        "regression",
        "ols",
        "statistical test",
        "plot",
        "dashboard",
    ]
    return any(t in text for t in triggers)


def _analysis_tool_used(messages: list[BaseMessage]) -> bool:
    for msg in messages:
        if isinstance(msg, ToolMessage) and (msg.name or "") in ANALYSIS_TOOL_NAMES:
            return True
    return False


def _extract_fail_fast(tool_msg: ToolMessage) -> dict | None:
    content = tool_msg.content
    if not isinstance(content, str):
        return None
    try:
        data = json.loads(content)
    except Exception:
        return None
    if isinstance(data, dict) and data.get("error_type") == "fail_fast_missing_requirements":
        return data
    return None


def _response_calls_analysis_tool(response: BaseMessage) -> bool:
    if not hasattr(response, "tool_calls") or not response.tool_calls:
        return False
    for tc in response.tool_calls:
        if tc.get("name", "") in ANALYSIS_TOOL_NAMES:
            return True
    return False


def make_agent_node(db_url: str):
    def agent_node(state: AgentState):
        llm = _get_llm(state.get("model_override"))
        llm_with_tools = llm.bind_tools(TOOLS)

        # Expose current model to tools via env var (for execution logging)
        resolved_model = state.get("model_override") or ""
        if not resolved_model:
            from config import get_settings
            resolved_model = get_settings().resolve_model(get_settings().default_llm)
        os.environ["AGENT_CURRENT_MODEL"] = resolved_model
        os.environ["AGENT_USER_REQUEST"] = _latest_user_text(state["messages"])

        memory_ctx = state.get("memory_context", "")
        prompt = BASE_PROMPT
        if memory_ctx:
            prompt = prompt + "\n\n" + memory_ctx

        # ── Write-loop tracker (file write deduplication) ─────────────────────
        write_tracker = state.get("_write_tracker") or {
            "consecutive_writes": 0,
            "last_path": "",
        }

        # ── General tool-call loop tracker ────────────────────────────────────
        tool_tracker = state.get("_tool_call_tracker") or {
            "total": 0,
            "last_tool": "",
            "consecutive_same": 0,
        }

        last_tool_msg = None
        for msg in reversed(state["messages"]):
            if isinstance(msg, ToolMessage):
                last_tool_msg = msg
                break

        if last_tool_msg:
            tool_name = last_tool_msg.name or ""

            # Write-loop detection — find original tool args from the AIMessage
            if tool_name == "write_workspace_file":
                path = ""
                for msg2 in reversed(state["messages"]):
                    if isinstance(msg2, AIMessage) and hasattr(msg2, "tool_calls") and msg2.tool_calls:
                        for tc in msg2.tool_calls:
                            if tc.get("id") == last_tool_msg.tool_call_id:
                                path = tc.get("args", {}).get("path", "")
                                break
                        if path:
                            break
                if path == write_tracker.get("last_path") and path != "":
                    write_tracker["consecutive_writes"] = write_tracker.get("consecutive_writes", 0) + 1
                else:
                    write_tracker["consecutive_writes"] = 1
                    write_tracker["last_path"] = path
            else:
                write_tracker["consecutive_writes"] = 0
                write_tracker["last_path"] = ""

            # General tool-call tracking
            tool_tracker["total"] = tool_tracker.get("total", 0) + 1
            if tool_name == tool_tracker.get("last_tool"):
                tool_tracker["consecutive_same"] = tool_tracker.get("consecutive_same", 0) + 1
            else:
                tool_tracker["consecutive_same"] = 1
                tool_tracker["last_tool"] = tool_name
        else:
            write_tracker["consecutive_writes"] = 0
            write_tracker["last_path"] = ""
            tool_tracker = {"total": 0, "last_tool": "", "consecutive_same": 0}

        # ── Write-loop block ──────────────────────────────────────────────────
        if last_tool_msg:
            fail_fast = _extract_fail_fast(last_tool_msg)
            if fail_fast:
                missing = fail_fast.get("missing", [])
                kind = fail_fast.get("kind", "required_items")
                detail = fail_fast.get("detail", "")
                missing_text = ", ".join(missing) if isinstance(missing, list) and missing else "(unspecified)"
                blocker = AIMessage(
                    content=(
                        f"FAIL-FAST: missing required {kind}: {missing_text}. "
                        f"Analysis stopped with no fallback substitution. {detail}"
                    )
                )
                return {"messages": [blocker], "_write_tracker": write_tracker, "_tool_call_tracker": tool_tracker}

        if write_tracker["consecutive_writes"] >= 2:
            blocker = AIMessage(
                content=(
                    f"⚠️ WRITE LOOP DETECTED: You have written to '{write_tracker['last_path']}' "
                    f"{write_tracker['consecutive_writes']} times consecutively. The system has blocked further writes.\n\n"
                    f"Use `execute_workspace_script` with the corrected code instead. "
                    f"Read the error output, understand the bug, fix it in your head, "
                    f"and execute the corrected script atomically. Do NOT write to files again."
                )
            )
            return {"messages": [blocker], "_write_tracker": write_tracker, "_tool_call_tracker": tool_tracker}

        # ── Repeated same-tool block ──────────────────────────────────────────
        same_tool_limit = SAME_TOOL_REPEAT_LIMITS.get(
            tool_tracker.get("last_tool", ""),
            SAME_TOOL_REPEAT_LIMIT,
        )
        if tool_tracker["consecutive_same"] >= same_tool_limit:
            blocker = AIMessage(
                content=(
                    f"⚠️ TOOL LOOP DETECTED: '{tool_tracker['last_tool']}' has been called "
                    f"{tool_tracker['consecutive_same']} times in a row (limit: {same_tool_limit}). Stop searching and "
                    f"synthesise an answer from the results you already have. "
                    f"Do not call any more tools."
                )
            )
            return {"messages": [blocker], "_write_tracker": write_tracker, "_tool_call_tracker": tool_tracker}

        # ── Total tool-call budget exhausted ─────────────────────────────────
        if tool_tracker["total"] >= TOOL_CALL_LIMIT:
            messages = [SystemMessage(prompt)] + state["messages"] + [
                HumanMessage(
                    content=(
                        f"[SYSTEM] You have used {tool_tracker['total']} tool calls. "
                        f"You must now stop calling tools and write your final answer "
                        f"using only the information already retrieved."
                    )
                )
            ]
            response = llm_with_tools.invoke(messages)
            # Strip any tool calls from the response to force termination
            if hasattr(response, "tool_calls") and response.tool_calls:
                response = AIMessage(content=response.content or "I've gathered sufficient information. Based on my research: " + str(response.content))
            return {"messages": [response], "_write_tracker": write_tracker, "_tool_call_tracker": tool_tracker}

        messages = [SystemMessage(prompt)] + state["messages"]
        response = llm_with_tools.invoke(messages)

        # Enforce real execution for analysis requests: no "text-only" completion
        # if no analysis execution tool has been called yet.
        if _analysis_requested(state["messages"]) and not _analysis_tool_used(state["messages"]):
            # If analysis is requested, require an analysis execution tool call next.
            if _response_calls_analysis_tool(response):
                return {"messages": [response], "_write_tracker": write_tracker, "_tool_call_tracker": tool_tracker}

            forced = llm_with_tools.invoke(
                messages
                + [
                    HumanMessage(
                        content=(
                            "[SYSTEM] This request requires actual analysis execution. "
                            "Call an analysis execution tool now (execute_r_script / execute_python_script / execute_notebook), "
                            "or return a fail-fast error listing missing required inputs. "
                            "Do not call search tools or provide a narrative-only answer."
                        )
                    )
                ]
            )
            if _response_calls_analysis_tool(forced):
                response = forced
            else:
                response = AIMessage(
                    content=(
                        "FAIL-FAST: Analysis execution was required but no execution tool was called. "
                        "Stopping to avoid unverifiable/costly analysis output."
                    )
                )
        return {"messages": [response], "_write_tracker": write_tracker, "_tool_call_tracker": tool_tracker}

    return agent_node


def make_dynamic_llm_node(db_url: str):
    """Node that classifies task complexity via LLM and routes to the appropriate tier.

    Uses a lightweight LLM call (GPU Ollama) to understand query intent and
    classify complexity as low/mid/high. Falls back to keyword heuristics if
    the LLM is unavailable.

    The resolved model is stored in state for the agent node to use.
    """
    from config import get_settings

    CLASSIFIER_PROMPT = """You are a task complexity classifier. Analyze the user's query and classify its complexity tier.

Rules:
- **low**: Greetings, simple math, yes/no, one-word answers, trivial lookups.
- **mid**: Explanations, definitions, summaries, comparisons, moderate analysis, code review, multi-part questions.
- **high**: System design, code generation, deep research, multi-step reasoning, comprehensive analysis, creative writing.

Return ONLY valid JSON: {"tier": "low"|"mid"|"high", "reason": "brief explanation"}

Examples:
- "Hello" → {"tier": "low", "reason": "greeting"}
- "What is 2+2?" → {"tier": "low", "reason": "simple arithmetic"}
- "What is photosynthesis?" → {"tier": "mid", "reason": "requires process explanation"}
- "Define REST API" → {"tier": "mid", "reason": "concept explanation needed"}
- "Compare RAG vs fine-tuning" → {"tier": "high", "reason": "multi-factor comparison"}
- "Design a distributed payment system" → {"tier": "high", "reason": "system design with constraints"}
- "Write a Python script to scrape and embed data" → {"tier": "high", "reason": "code generation with multiple steps"}"""

    def _classify_task_llm(messages: list) -> str | None:
        """Use a small GPU LLM to classify task complexity via native Ollama API."""
        import json as _json

        settings = get_settings()
        if not settings.gpu_llm_enabled:
            return None

        user_msg = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                user_msg = msg.content
                break

        if not user_msg:
            return None

        classifier_model = os.environ.get("CLASSIFIER_MODEL", "qwen2.5:3b")

        try:
            payload = {
                "model": classifier_model,
                "messages": [
                    {"role": "system", "content": CLASSIFIER_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 50},
                "keep_alive": 0,
            }
            with httpx.Client(timeout=15) as client:
                resp = client.post(
                    f"{settings.gpu_llm_url}/api/chat",
                    json=payload,
                )
            resp.raise_for_status()
            result = resp.json()["message"]["content"]
            parsed = _json.loads(result)
            tier = parsed.get("tier", "mid")
            if tier in ("low", "mid", "high"):
                return tier
            return None
        except Exception as e:
            logger.debug(f"LLM classifier failed: {e}")
            return None

    def _classify_task_heuristic(messages: list) -> str:
        """Fallback keyword-based classification if LLM is unavailable."""
        user_msg = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                user_msg = msg.content
                break

        if not user_msg:
            return "mid"

        user_msg_lower = user_msg.lower()

        high_signals = [
            "analyze", "compare", "synthesize", "comprehensive", "thorough",
            "deep dive", "explain in detail", "research", "investigate",
            "knowledge graph", "architecture", "design pattern", "implement",
            "write a", "create a", "build a", "generate",
        ]
        low_signals = [
            "what is", "define", "simple", "quick", "brief", "short",
            "yes or no", "how many",
        ]

        high_score = sum(1 for s in high_signals if s in user_msg_lower)
        low_score = sum(1 for s in low_signals if s in user_msg_lower)

        if high_score >= 2 or (high_score >= 1 and high_score > low_score):
            return "high"
        if low_score >= 2 or (low_score >= 1 and low_score > high_score):
            return "low"
        return "mid"

    def dynamic_llm_node(state: AgentState):
        tier = _classify_task_llm(state["messages"])
        if tier is None:
            tier = _classify_task_heuristic(state["messages"])

        settings = get_settings()
        model_id = settings.resolve_tier(tier)

        return {
            "model_override": model_id,
            "memory_context": state.get("memory_context", ""),
        }

    return dynamic_llm_node


async def build_graph(db_url: str):
    """Build and compile the LangGraph agent with persistent PostgreSQL memory
    and dynamic LLM routing (complexity → model tier)."""
    from psycopg.rows import dict_row

    pool = AsyncConnectionPool(
        conninfo=db_url,
        max_size=5,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await pool.open()

    checkpointer = AsyncPostgresSaver(conn=pool)
    await checkpointer.setup()

    graph = StateGraph(AgentState)
    graph.add_node("classify", make_dynamic_llm_node(db_url))
    graph.add_node("agent", make_agent_node(db_url))
    graph.add_node("tools", ToolNode(TOOLS))
    graph.set_entry_point("classify")
    graph.add_edge("classify", "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")

    compiled = graph.compile(checkpointer=checkpointer)

    compiled._db_pool = pool

    logger.info(
        "LangGraph agent compiled with AsyncPostgresSaver (persistent memory) + dynamic LLM routing."
    )
    return compiled
