"""LangGraph node factories: agent_node and dynamic_llm_node."""

import json
import logging
import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from graph_guardrails import _invoke_with_fallback, _prune_messages
from graph_llm import _get_llm
from graph_prompts import BASE_PROMPT
from graph_state import (
    _analysis_requested,
    _analysis_tool_used,
    _extract_fail_fast,
    _latest_user_text,
    _response_calls_analysis_tool,
    _task_intents_from_messages,
    _task_tier_from_messages,
    _tool_call_args_for_tool_message,
    _tool_args_signature,
)
from graph_tools import (
    ANALYSIS_TOOL_NAMES,
    SAME_TOOL_REPEAT_LIMIT,
    SAME_TOOL_REPEAT_LIMITS,
    TOOL_CALL_BUDGETS,
    _select_tools_for_request,
)

logger = logging.getLogger(__name__)


def make_agent_node(db_url: str):
    def agent_node(state):
        llm = _get_llm(state.get("model_override"))
        tier = state.get("task_tier") or _task_tier_from_messages(state["messages"])
        task_intent = state.get("task_intent")
        selected_tools = _select_tools_for_request(state["messages"], tier, task_intent)
        llm_with_tools = llm.bind_tools(selected_tools)

        # Expose current model to tools via env var (for execution logging)
        from config import get_settings

        settings = get_settings()
        resolved_model = state.get("model_override") or ""
        if not resolved_model:
            resolved_model = settings.resolve_model(settings.default_llm)
        os.environ["AGENT_CURRENT_MODEL"] = resolved_model
        os.environ["AGENT_USER_REQUEST"] = _latest_user_text(state["messages"])
        os.environ["AGENT_BOUND_TOOLS"] = ",".join(
            getattr(tool_obj, "name", str(tool_obj)) for tool_obj in selected_tools
        )

        memory_ctx = state.get("memory_context", "")
        prompt = BASE_PROMPT
        if memory_ctx:
            prompt = prompt + "\n\n" + memory_ctx

        # ── Message Pruning ───────────────────────────────────────────────────
        state["messages"] = _prune_messages(state["messages"])

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
            if isinstance(msg, __import__("langchain_core.messages", fromlist=["ToolMessage"]).ToolMessage):
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
            response = _invoke_with_fallback(llm_with_tools, messages, tools=selected_tools, tier=tier)
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
        response = _invoke_with_fallback(llm_with_tools, messages, tools=selected_tools, tier=tier)

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
                tier=tier,
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
    from config import get_settings, TIER_ALIASES

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

        classifier_model = (
            settings.resolve_model("classifier")
            or settings.gpu_llm_model
            or settings.resolve_model("fast")
        )
        if not classifier_model:
            raise RuntimeError("No classifier model configured. Set CLASSIFIER_MODEL or FAST_MODEL in .env.")

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
            import httpx

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

    def dynamic_llm_node(state):
        from graph_state import AgentState

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
