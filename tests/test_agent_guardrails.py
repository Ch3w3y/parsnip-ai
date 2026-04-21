"""Tests for agent guardrail policy helpers."""

import json
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))


def test_analysis_request_detection_requires_execution_intent():
    from graph import _analysis_requested

    assert _analysis_requested([HumanMessage(content="Generate a plot of this CSV")])
    assert not _analysis_requested(
        [HumanMessage(content="Explain when a regression plot is useful")]
    )


def test_tool_args_signature_uses_matching_tool_call_id():
    from graph import _tool_args_signature, _tool_call_args_for_tool_message

    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "web_search",
                    "args": {"query": "alpha", "max_results": 3},
                    "id": "call-a",
                },
                {
                    "name": "web_search",
                    "args": {"query": "beta", "max_results": 3},
                    "id": "call-b",
                },
            ],
        ),
        ToolMessage(content="result", name="web_search", tool_call_id="call-b"),
    ]

    args = _tool_call_args_for_tool_message(messages, messages[-1])
    assert args == {"query": "beta", "max_results": 3}
    assert _tool_args_signature(args) == '{"max_results": 3, "query": "beta"}'


def test_tool_pack_selection_binds_relevant_general_tools():
    from graph import _select_tools_for_request

    tools = _select_tools_for_request(
        [HumanMessage(content="Research the latest papers on graph neural networks")],
        tier="mid",
    )
    names = {tool.name for tool in tools}

    assert "adaptive_search" in names
    assert "arxiv_search" in names
    assert "execute_python_script" not in names


def test_tool_pack_selection_keeps_analysis_and_github_broad():
    from graph import _select_tools_for_request

    tools = _select_tools_for_request(
        [
            HumanMessage(
                content="Analyze this GitHub repo and generate a dashboard of open issues"
            )
        ],
        tier="high",
        task_intent="analysis",
    )
    names = {tool.name for tool in tools}

    assert "execute_python_script" in names
    assert "github_list_issues" in names
    assert "generate_knowledge_graph" in names


@pytest.mark.asyncio
async def test_preflight_feedback_is_recoverable_by_default(monkeypatch):
    from tools import analysis_server

    monkeypatch.setattr(analysis_server, "DATABASE_URL", "postgresql://example")
    monkeypatch.setenv("AGENT_USER_REQUEST", "Use World Bank data for GDP analysis")

    result = await analysis_server._preflight_required_identifiers(
        code="print('placeholder')"
    )
    payload = json.loads(result)

    assert payload["status"] == "needs_decision"
    assert payload["hard_stop"] is False
    assert "error_type" not in payload
    assert payload["missing"] == ["world_bank_data"]


@pytest.mark.asyncio
async def test_preflight_feedback_hard_stops_when_user_requires_exact(monkeypatch):
    from tools import analysis_server

    monkeypatch.setattr(analysis_server, "DATABASE_URL", "postgresql://example")
    monkeypatch.setenv(
        "AGENT_USER_REQUEST",
        "You must use exactly World Bank data with no fallback",
    )

    result = await analysis_server._preflight_required_identifiers(
        code="print('placeholder')"
    )
    payload = json.loads(result)

    assert payload["status"] == "error"
    assert payload["hard_stop"] is True
    assert payload["error_type"] == "fail_fast_missing_requirements"
