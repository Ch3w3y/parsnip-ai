"""
LangGraph ReAct agent orchestration.

The heavy implementation lives in submodules:
  graph_state      — agent state TypedDict and message helpers
  graph_prompts    — system prompt / identity
  graph_tools      — tool pack definitions and selection logic
  graph_guardrails — circuit breaker, fallback cascade, message pruning
  graph_llm        — LLM client construction
  graph_nodes      — agent node and dynamic LLM node factories

This module provides the public surface: build_graph and backward-compatible
re-exports so other code can continue importing from graph directly.
"""

# Backward-compatible re-exports for consumers that import from graph directly
from graph_state import (  # noqa: F401
    _analysis_requested,
    _analysis_tool_used,
    _extract_fail_fast,
    _latest_user_text,
    _response_calls_analysis_tool,
    _task_intents_from_messages,
    _task_tier_from_messages,
    _tool_args_signature,
    _tool_call_args_for_tool_message,
    AgentState,
    _load_l1_memory,
)
from graph_tools import (  # noqa: F401
    _dedupe_tools,
    _select_tools_for_request,
    ANALYSIS_TOOL_NAMES,
    CORE_TOOLS,
    RESEARCH_TOOLS,
    ANALYSIS_TOOLS,
    WORKSPACE_TOOLS,
    GITHUB_TOOLS,
    NOTE_TOOLS,
    MEMORY_TOOLS,
    TOOL_PACKS,
    TOOL_CALL_BUDGETS,
    SAME_TOOL_REPEAT_LIMIT,
    SAME_TOOL_REPEAT_LIMITS,
)
from graph_guardrails import (  # noqa: F401
    _invoke_with_fallback,
    _prune_messages,
    _circuit_is_open,
    _trip_circuit,
    _reset_circuit,
    _is_rate_limit_error,
    _get_cascading_fallbacks,
    _try_gpu_fallback,
)
from graph_llm import _get_llm  # noqa: F401

# Heavy imports deferred until build_graph is actually used.


async def build_graph(db_url: str):
    """Build and compile the LangGraph agent with persistent PostgreSQL memory
    and dynamic LLM routing (complexity → model tier)."""
    import logging

    from langgraph.graph import StateGraph
    from langgraph.prebuilt import ToolNode, tools_condition
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool
    from psycopg.rows import dict_row

    from graph_nodes import make_agent_node, make_dynamic_llm_node
    from tools import TOOLS

    logger = logging.getLogger(__name__)

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
