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
import threading
import time
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

BASE_PROMPT = """You are a personal assistant who operates as an expert data
scientist, analyst, engineer, and statistician. You combine rigorous technical
methodology with practical execution — you don't just describe analyses, you
run them. You treat the user's requests as collaborative projects where your
role is to deliver precise, reproducible, and well-documented results.

Persona and voice:
- Professional but approachable. Speak clearly, avoid unnecessary jargon, but
  never dumb down technical content when precision matters.
- You are a practitioner, not a commentator. When the user asks for analysis,
  your default is to execute it, not to explain how it could be done.
- You take ownership of quality: validate data, check assumptions, report
  uncertainty, and flag limitations honestly.
- You maintain continuity across sessions through memory and structured notes.

Technical operating principles:
- Prefer direct answers for simple questions. Use tools when freshness, private
  knowledge, files, code execution, or source evidence would materially improve
  the answer.
- HARD RULE — Knowledge Base First: whenever the user asks for research,
  analysis, summaries, visualizations, word clouds, trend reports, thematic
  exploration, or any investigation of "what we know about X", you MUST query
  the knowledge base BEFORE producing the deliverable. This applies even when
  the user does NOT explicitly mention "knowledge base", "existing data", or
  "stored documents". Your default assumption is: if the request involves
  themes, topics, categories, patterns, or corpus-level insights, the primary
  source is the local knowledge base (5.9 M+ chunks, 725 k+ Wikipedia articles).
  Call `kb_search`, `research`, `adaptive_search`, or `holistic_search` as the
  FIRST step, feed ONLY the returned real text into downstream analysis, and
  cite the actual sources. If the KB returns no results, report that honestly.
  NEVER synthesize, hallucinate, or use your parametric knowledge as a
  substitute for real KB data when the user expects an evidence-based answer.
- For broad or uncertain research, start with `adaptive_search` or
  `holistic_search`. Use `kb_search`, `search_with_filters`, `timeline`,
  `get_document`, `compare_sources`, and `find_similar` when they fit the shape
  of the question.
- Use `web_search`, `extract_webpage`, and `arxiv_search` for current or
  source-specific material that may not be in the KB.
- Use memory tools when the user asks about remembered context or shares stable
  preferences, decisions, project facts, or other information that should
  persist across sessions.
- Use Joplin tools for note workflows. Check existing notebooks when useful;
  agent-created notebook names should be easy to distinguish from user-created
  notebooks.
- Use GitHub tools for repository discovery, code reading, issues, branches,
  PRs, and repository structure.
- Use analysis/workspace tools when the user needs actual computation,
  generated files, charts, notebooks, dashboards, scheduled jobs, or runnable
  scripts. `execute_python_script` and `execute_r_script` run one-off scripts;
  `write_and_execute_script` and `execute_workspace_script` are better when the
  script should be saved or iterated in the workspace.
- Structured tables are available for numeric analysis: `forex_rates`,
  `world_bank_data`, `knowledge_chunks`, and `agent_memories`. Prefer SQL
  against structured tables when the task needs numeric precision.
- If a tool returns structured preflight feedback such as `needs_decision`,
  reason over the options. Ask the user only when the next step genuinely
  depends on their preference; otherwise revise the plan, choose a defensible
  fallback, or explain the limitation.

Safety and execution boundaries:
- Treat explicitly exact user requirements as hard constraints. Do not silently
  substitute required identifiers, files, countries, symbols, or columns when the
  user says they are mandatory or forbids fallback.
- Do not perform destructive actions unless the user requested them or the
  action is clearly part of the current task.
- If code execution fails, use the error output to fix the next attempt. Avoid
  repeating identical tool calls or rewrites without a new hypothesis.
- Cite source titles and links for research claims when tools provide them.

Joplin execution rule:
- When the user explicitly asks you to save, store, or create a note in Joplin,
  you MUST call the appropriate Joplin tool (`joplin_create_note`,
  `joplin_update_note`, etc.) as a real tool call. A text-only response claiming
  the note was saved is NOT sufficient. After creating the note, return the
  note title and a `joplin://` deep-link in your final answer.
"""

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

CORE_TOOLS = [
    adaptive_search,
    holistic_search,
    kb_search,
    web_search,
    extract_webpage,
    get_document,
    save_memory,
    recall_memory,
    system_status,
]

RESEARCH_TOOLS = [
    research,
    timeline,
    knowledge_gaps,
    compare_sources,
    find_similar,
    arxiv_search,
    search_with_filters,
    generate_knowledge_graph,
    ingest_pdf,
    save_note,
    list_documents,
]

ANALYSIS_TOOLS = [
    execute_python_script,
    execute_r_script,
    list_analysis_outputs,
    execute_notebook,
    generate_dashboard,
    create_scheduled_job,
    list_scheduled_jobs,
    delete_scheduled_job,
]

WORKSPACE_TOOLS = [
    list_workspace,
    read_workspace_file,
    write_workspace_file,
    make_workspace_dir,
    delete_workspace_item,
    move_workspace_item,
    execute_bash_command,
    write_and_execute_script,
    execute_workspace_script,
]

GITHUB_TOOLS = [
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
]

NOTE_TOOLS = [
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
    save_note,
    list_documents,
]

MEMORY_TOOLS = [
    save_memory,
    recall_memory,
    update_memory,
    delete_memory,
    recall_memory_by_category,
    summarize_memories,
]

TOOL_PACKS = {
    "core": CORE_TOOLS,
    "research": CORE_TOOLS + RESEARCH_TOOLS,
    "analysis": CORE_TOOLS + RESEARCH_TOOLS + ANALYSIS_TOOLS + WORKSPACE_TOOLS,
    "workspace": CORE_TOOLS + WORKSPACE_TOOLS + ANALYSIS_TOOLS,
    "github": CORE_TOOLS + GITHUB_TOOLS + WORKSPACE_TOOLS,
    "notes": CORE_TOOLS + NOTE_TOOLS,
    "memory": CORE_TOOLS + MEMORY_TOOLS,
    "system": [system_status],
}


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    model_override: str | None
    task_tier: str | None
    task_intent: str | None
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


# ── Circuit breaker for OpenRouter rate-limit / quota exhaustion ───────────
# When OpenRouter returns 403 (key limit), 429 (rate limit), or 402 (payment),
# we automatically fallback to GPU Ollama for a cooldown period.

_OPENROUTER_TRIPPED = False
_OPENROUTER_TRIPPED_AT: float | None = None
_OPENROUTER_LOCK = threading.Lock()
_OPENROUTER_COOLDOWN_SECONDS = 300  # 5 minutes


def _trip_circuit():
    global _OPENROUTER_TRIPPED, _OPENROUTER_TRIPPED_AT
    with _OPENROUTER_LOCK:
        _OPENROUTER_TRIPPED = True
        _OPENROUTER_TRIPPED_AT = time.time()
    logger.warning("OpenRouter circuit breaker TRIPPED — falling back to GPU Ollama")


def _reset_circuit():
    global _OPENROUTER_TRIPPED, _OPENROUTER_TRIPPED_AT
    with _OPENROUTER_LOCK:
        _OPENROUTER_TRIPPED = False
        _OPENROUTER_TRIPPED_AT = None
    logger.info("OpenRouter circuit breaker RESET")


def _circuit_is_open() -> bool:
    """Check if circuit is tripped and cooldown has not expired."""
    global _OPENROUTER_TRIPPED, _OPENROUTER_TRIPPED_AT
    with _OPENROUTER_LOCK:
        if not _OPENROUTER_TRIPPED:
            return False
        if _OPENROUTER_TRIPPED_AT is None:
            return False
        elapsed = time.time() - _OPENROUTER_TRIPPED_AT
        if elapsed >= _OPENROUTER_COOLDOWN_SECONDS:
            # Auto-reset after cooldown
            _OPENROUTER_TRIPPED = False
            _OPENROUTER_TRIPPED_AT = None
            logger.info("OpenRouter circuit breaker auto-RESET after cooldown")
            return False
        return True


def _is_rate_limit_error(e: Exception) -> bool:
    """Detect OpenRouter rate-limit / quota errors from langchain/openai exceptions."""
    msg = str(e).lower()
    codes = ["403", "429", "402", "key limit exceeded", "rate limit", "quota",
             "insufficient_quota", "payment_required", "limit exceeded"]
    return any(c in msg for c in codes)


def _invoke_with_fallback(llm, messages, tools: list | None = None):
    """Invoke LLM; on OpenRouter rate-limit, rebuild with GPU and retry once.

    Preserves tool bindings on fallback by re-calling bind_tools(...) when
    the original LLM was already tool-bound.
    """
    from config import get_settings

    settings = get_settings()
    gpu_model = settings.gpu_llm_model

    # If circuit is open (tripped + within cooldown), skip straight to fallback
    if _circuit_is_open() and gpu_model:
        fallback_llm = ChatOpenAI(
            model=gpu_model,
            base_url=f"{settings.gpu_llm_url}/v1",
            api_key="not-needed",
            streaming=getattr(llm, "streaming", True),
        )
        if tools:
            fallback_llm = fallback_llm.bind_tools(tools)
        return fallback_llm.invoke(messages)

    try:
        return llm.invoke(messages)
    except Exception as e:
        if _is_rate_limit_error(e) and settings.gpu_llm_enabled:
            _trip_circuit()
            logger.warning(f"OpenRouter blocked ({e}). Retrying with GPU model {gpu_model} ...")
            fallback_llm = ChatOpenAI(
                model=gpu_model,
                base_url=f"{settings.gpu_llm_url}/v1",
                api_key="not-needed",
                streaming=getattr(llm, "streaming", True),
            )
            if tools:
                fallback_llm = fallback_llm.bind_tools(tools)
            try:
                result = fallback_llm.invoke(messages)
                logger.info(f"GPU fallback succeeded with {gpu_model}")
                return result
            except Exception as e2:
                logger.error(f"GPU fallback also failed: {e2}")
                raise e2 from e
        raise


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


TOOL_CALL_BUDGETS = {
    "low": 10,
    "mid": 20,
    "high": 45,
}
SAME_TOOL_REPEAT_LIMIT = 5  # repeated same tool with identical args
SAME_TOOL_REPEAT_LIMITS = {
    # Analysis tools often need iterative refinement/fixing across a few runs.
    "execute_r_script": 10,
    "execute_python_script": 10,
    "execute_workspace_script": 10,
    "write_and_execute_script": 10,
    "web_search": 7,
    "kb_search": 7,
    "holistic_search": 7,
    "adaptive_search": 7,
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
        "execute an r analysis",
        "execute a python analysis",
        "actually run",
        "r script",
        "python script",
        "run the script",
        "generate a chart",
        "generate a plot",
        "create a dashboard",
        "produce a dashboard",
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


def _task_tier_from_messages(messages: list[BaseMessage]) -> str:
    text = _latest_user_text(messages).lower()
    if not text:
        return "mid"

    high_signals = [
        "comprehensive",
        "deep",
        "multi-step",
        "investigate",
        "research",
        "analyze",
        "analysis",
        "implement",
        "build",
        "design",
        "architecture",
        "compare",
        "synthesize",
    ]
    low_signals = [
        "quick",
        "brief",
        "simple",
        "what is",
        "define",
        "yes or no",
    ]

    high_score = sum(1 for signal in high_signals if signal in text)
    low_score = sum(1 for signal in low_signals if signal in text)
    if high_score >= 2 or len(text) > 400:
        return "high"
    if low_score > high_score and len(text) < 160:
        return "low"
    return "mid"


def _task_intents_from_messages(messages: list[BaseMessage]) -> list[str]:
    text = _latest_user_text(messages).lower()
    if not text:
        return ["research"]

    intents: list[str] = []
    if any(
        signal in text
        for signal in (
            "python",
            " r ",
            " r script",
            "script",
            "notebook",
            "dashboard",
            "chart",
            "plot",
            "statistical",
            "regression",
            "sql",
            "dataframe",
            "csv",
            "world bank",
            "forex",
            "analysis",
            "analyze",
        )
    ):
        intents.append("analysis")
    if any(
        signal in text
        for signal in (
            "github",
            "repo",
            "repository",
            "pull request",
            " pr ",
            "issue",
            "commit",
            "branch",
            "readme",
            "code search",
        )
    ):
        intents.append("github")
    if any(
        signal in text
        for signal in (
            "workspace",
            "file",
            "directory",
            "folder",
            "bash",
            "shell",
            "install",
            "run command",
        )
    ):
        intents.append("workspace")
    if any(
        signal in text
        for signal in ("joplin", "note", "notebook", "tag", "document")
    ):
        intents.append("notes")
    if any(
        signal in text
        for signal in (
            "remember",
            "memory",
            "preference",
            "decision",
            "recall",
            "what do you know about me",
        )
    ):
        intents.append("memory")
    if any(signal in text for signal in ("status", "health", "diagnose", "broken")):
        intents.append("system")
    if any(
        signal in text
        for signal in (
            "research",
            "latest",
            "current",
            "paper",
            "article",
            "compare",
            "timeline",
            "source",
            "citation",
            "cite",
            "summarize",
            "explain",
        )
    ):
        intents.append("research")

    return intents or ["research"]


def _dedupe_tools(tools: list) -> list:
    seen: set[str] = set()
    deduped = []
    for tool_obj in tools:
        name = getattr(tool_obj, "name", str(tool_obj))
        if name not in seen:
            seen.add(name)
            deduped.append(tool_obj)
    return deduped


def _select_tools_for_request(
    messages: list[BaseMessage],
    tier: str = "mid",
    task_intent: str | None = None,
) -> list:
    heuristic_intents = _task_intents_from_messages(messages)
    if task_intent in (None, "", "general", "current"):
        intents = heuristic_intents
    else:
        intents = [task_intent, *heuristic_intents]
    if task_intent == "code" and "github" not in intents:
        intents.append("github")
    if task_intent == "research" and "research" not in intents:
        intents.append("research")

    selected = list(CORE_TOOLS)
    for intent in intents:
        selected.extend(TOOL_PACKS.get(intent, []))

    # High-complexity requests benefit from research context even when the first
    # intent classifier picks a narrower operational pack.
    if tier == "high" and "research" not in intents:
        selected.extend(RESEARCH_TOOLS)

    return _dedupe_tools(selected)


def _tool_call_args_for_tool_message(
    messages: list[BaseMessage], tool_msg: ToolMessage
) -> dict:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("id") == tool_msg.tool_call_id:
                    args = tc.get("args", {})
                    return args if isinstance(args, dict) else {"value": args}
    return {}


def _tool_args_signature(args: dict) -> str:
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except Exception:
        return str(args)


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
        tier = state.get("task_tier") or _task_tier_from_messages(state["messages"])
        task_intent = state.get("task_intent")
        selected_tools = _select_tools_for_request(state["messages"], tier, task_intent)
        llm_with_tools = llm.bind_tools(selected_tools)

        # Expose current model to tools via env var (for execution logging)
        resolved_model = state.get("model_override") or ""
        if not resolved_model:
            from config import get_settings
            resolved_model = get_settings().resolve_model(get_settings().default_llm)
        os.environ["AGENT_CURRENT_MODEL"] = resolved_model
        os.environ["AGENT_USER_REQUEST"] = _latest_user_text(state["messages"])
        os.environ["AGENT_BOUND_TOOLS"] = ",".join(
            getattr(tool_obj, "name", str(tool_obj)) for tool_obj in selected_tools
        )

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
            "last_args": "",
            "consecutive_same": 0,
        }

        last_tool_msg = None
        for msg in reversed(state["messages"]):
            if isinstance(msg, ToolMessage):
                last_tool_msg = msg
                break

        if last_tool_msg:
            tool_name = last_tool_msg.name or ""
            tool_args = _tool_call_args_for_tool_message(state["messages"], last_tool_msg)
            tool_args_sig = _tool_args_signature(tool_args)

            # Write-loop detection — find original tool args from the AIMessage
            if tool_name == "write_workspace_file":
                path = tool_args.get("path", "")
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
            if (
                tool_name == tool_tracker.get("last_tool")
                and tool_args_sig == tool_tracker.get("last_args")
            ):
                tool_tracker["consecutive_same"] = tool_tracker.get("consecutive_same", 0) + 1
            else:
                tool_tracker["consecutive_same"] = 1
                tool_tracker["last_tool"] = tool_name
                tool_tracker["last_args"] = tool_args_sig
        else:
            write_tracker["consecutive_writes"] = 0
            write_tracker["last_path"] = ""
            tool_tracker = {
                "total": 0,
                "last_tool": "",
                "last_args": "",
                "consecutive_same": 0,
            }

        # ── Write-loop block ──────────────────────────────────────────────────
        if last_tool_msg:
            fail_fast = _extract_fail_fast(last_tool_msg)
            if fail_fast and fail_fast.get("hard_stop") is True:
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
        tool_call_limit = TOOL_CALL_BUDGETS.get(tier, TOOL_CALL_BUDGETS["mid"])
        if tool_tracker["consecutive_same"] >= same_tool_limit:
            blocker = AIMessage(
                content=(
                    f"⚠️ TOOL LOOP DETECTED: '{tool_tracker['last_tool']}' has been called "
                    f"{tool_tracker['consecutive_same']} times with the same arguments "
                    f"(limit: {same_tool_limit}). Stop repeating that call and synthesize "
                    f"from the results already available, or explain what information is missing."
                )
            )
            return {"messages": [blocker], "_write_tracker": write_tracker, "_tool_call_tracker": tool_tracker}

        # ── Total tool-call budget exhausted ─────────────────────────────────
        if tool_tracker["total"] >= tool_call_limit:
            messages = [SystemMessage(prompt)] + state["messages"] + [
                HumanMessage(
                    content=(
                        f"[SYSTEM] You have used {tool_tracker['total']} tool calls. "
                        f"The adaptive budget for this {tier} task is {tool_call_limit}. "
                        f"Stop calling tools and write the best final answer from the "
                        f"information already retrieved."
                    )
                )
            ]
            response = _invoke_with_fallback(llm_with_tools, messages, tools=selected_tools)
            # Strip any tool calls from the response to force termination
            if hasattr(response, "tool_calls") and response.tool_calls:
                response = AIMessage(content=response.content or "I've gathered sufficient information. Based on my research: " + str(response.content))
            return {"messages": [response], "_write_tracker": write_tracker, "_tool_call_tracker": tool_tracker}

        guardrail_notice = ""
        if tool_tracker["consecutive_same"] == same_tool_limit - 1:
            guardrail_notice = (
                f"[SYSTEM] Guardrail notice: the last tool has been called "
                f"{tool_tracker['consecutive_same']} times with identical arguments. "
                f"If you call it again unchanged, the loop guard will stop execution. "
                f"Change strategy or synthesize if you have enough information."
            )
        elif tool_tracker["total"] >= max(tool_call_limit - 3, 1):
            guardrail_notice = (
                f"[SYSTEM] Guardrail notice: {tool_tracker['total']} tool calls used "
                f"out of the adaptive {tool_call_limit}-call budget for this {tier} task. "
                f"Use additional tools only if they materially change the answer."
            )

        messages = [SystemMessage(prompt)] + state["messages"]
        if guardrail_notice:
            messages.append(HumanMessage(content=guardrail_notice))
        response = _invoke_with_fallback(llm_with_tools, messages, tools=selected_tools)

        # Enforce real execution for analysis requests: no "text-only" completion
        # if no analysis execution tool has been called yet.
        if _analysis_requested(state["messages"]) and not _analysis_tool_used(state["messages"]):
            # If analysis is requested, require an analysis execution tool call next.
            if _response_calls_analysis_tool(response):
                return {"messages": [response], "_write_tracker": write_tracker, "_tool_call_tracker": tool_tracker}

            forced = _invoke_with_fallback(
                llm_with_tools,
                messages
                + [
                    HumanMessage(
                        content=(
                            "[SYSTEM] This request requires actual analysis execution. "
                            "Call an analysis execution tool now (execute_r_script / execute_python_script / execute_notebook), "
                            "or ask for the specific missing inputs needed to run it. "
                            "Do not call search tools or provide a narrative-only answer."
                        )
                    )
                ],
                tools=selected_tools,
            )
            if _response_calls_analysis_tool(forced):
                response = forced
            else:
                response = AIMessage(
                    content=(
                        "I need a runnable analysis step for this request, but I do not have enough "
                        "specific input to execute it safely. Please provide the missing data, file, "
                        "or analysis target."
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
        task_intent = _task_intents_from_messages(state["messages"])[0]

        return {
            "model_override": model_id,
            "task_tier": tier,
            "task_intent": task_intent,
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
