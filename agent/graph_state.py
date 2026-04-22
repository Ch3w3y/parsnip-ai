"""LangGraph agent state definition and message utility helpers."""

from typing import Annotated

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    model_override: str | None
    task_tier: str | None
    task_intent: str | None
    memory_context: str | None
    _write_tracker: dict | None
    _tool_call_tracker: dict | None


# ── Message utility helpers ──────────────────────────────────────────────────


def _latest_user_text(messages: list[BaseMessage]) -> str:
    """Extract text from the most recent human message."""
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
    _ANALYSIS_TOOL_NAMES = {
        "execute_r_script",
        "execute_python_script",
        "execute_notebook",
        "generate_dashboard",
        "write_and_execute_script",
        "execute_workspace_script",
    }
    for msg in messages:
        if isinstance(msg, ToolMessage) and (msg.name or "") in _ANALYSIS_TOOL_NAMES:
            return True
    return False


def _extract_fail_fast(tool_msg: ToolMessage) -> dict | None:
    import json

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
    import json

    try:
        return json.dumps(args, sort_keys=True, default=str)
    except Exception:
        return str(args)


def _response_calls_analysis_tool(response: BaseMessage) -> bool:
    if not hasattr(response, "tool_calls") or not response.tool_calls:
        return False
    for tc in response.tool_calls:
        if tc.get("name", "") in {"execute_r_script", "execute_python_script", "execute_notebook", "generate_dashboard", "write_and_execute_script", "execute_workspace_script"}:
            return True
    return False


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


# ── L1 memory loader ──────────────────────────────────────────────────────────

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
        logger = __import__("logging").getLogger(__name__)
        logger.warning(f"L1 memory load failed: {e}")
        return ""

    if not rows:
        return ""

    lines = ["\n**Context from previous conversations:**"]
    for category, content in rows:
        lines.append(f"- [{category}] {content}")
    return "\n".join(lines)
