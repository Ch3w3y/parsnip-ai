# Parsnip Subagent Architecture — Phase 1: Subagent-Ready Refactoring

> Status: **Planning**  
> Prerequisite: Architecture improvements branch merged  
> Estimated effort: 2-3 weeks  

## Goal

Refactor the existing single-agent loop into a parameterizable subagent factory WITHOUT changing user-facing behavior. This makes Phase 2 (actual subagent dispatch) a plug-in operation rather than a rearchitecting.

## Current State (from oracle analysis)

Parsnip runs a ReAct agent in a single LangGraph `StateGraph`:

```
classify ──► agent ──► tools ──► agent ──► tools ──► ... (loop)
```

**Bottlenecks this enables solving:**
- Every tool-calling round uses the same expensive model (Kimi/GLM)
- No parallelism — research + analysis must be sequential
- Context window saturates at 25 tool calls (12K chars truncated per tool, 25-msg limit)
- Single generalist prompt for all domains

## What Already Exists (subagent primitives)

| Primitive | Current Location | Subagent Role |
|-----------|-----------------|---------------|
| `classify` node | `graph_nodes.py:242` | **Subagent router** — already classifies `task_intent` into domains |
| 7 tool packs | `graph_tools.py:164` | **Subagent tool sets** — each pack IS a subagent's tools |
| 3-tier model routing | `graph_llm.py`, `config.py` | **Per-subagent model** — orchestrator=high, subagents=mid |
| `TOOL_CALL_BUDGETS` | `graph_tools.py:182` | **Per-subagent budgets** |
| Circuit breaker | `graph_guardrails.py` | **Per-subagent guardrails** |
| `AsyncPostgresSaver` | `graph.py:85` | **Subgraph checkpointing** |
| `openai_compat_base_url` | `config.py:59` | **Ollama Cloud subagent backend** |

## Phase 1 Tasks

### S1: Extract Domain Prompts

**What**: Split `BASE_PROMPT` (graph_prompts.py) into domain-specific system prompts.

**Files**:
- `agent/graph_prompts.py` — Currently contains one 78-line generalist prompt
- Create `agent/prompts/research.py`, `agent/prompts/analysis.py`, `agent/prompts/github.py`, `agent/prompts/notes.py`, `agent/prompts/memory.py`, `agent/prompts/core.py`

**Each prompt**:
- Starts with a domain-specific identity ("You are a research specialist.")
- Lists available tools for that domain
- Includes domain-specific guidelines (e.g., "Always cite sources" for research)
- Maintains the overall Parsnip personality

**Must NOT**: Change user-facing behavior. The existing `BASE_PROMPT` should remain as the orchestrator/system prompt.

### S2: Parameterize `make_agent_node`

**What**: Refactor `make_agent_node` in `graph_nodes.py` to accept `(prompt, tools, model_tier, budget)` instead of closing over module-level constants.

**Current**: `make_agent_node(db_url)` closes over `BASE_PROMPT`, the full 66-tool set, and uses the classification output to select tools.

**Target**: `make_subagent_node(system_prompt, tool_pack, model_tier, budget, db_url)` creates a single-purpose agent node that can be used as a subgraph.

**Files**:
- `agent/graph_nodes.py` — Refactor `make_agent_node` → `make_subagent_node`
- Keep `make_agent_node` as a thin wrapper that calls `make_subagent_node` with current behavior (backward compat)

**Must NOT**: Change the existing graph topology. The current `classify → agent → tools` loop should work identically.

### S3: Add Planner Node

**What**: Add a `plan` node between `classify` and the agent that decides: direct response, single subagent, or multi-subagent.

**New node**: `plan_node(state: AgentState) -> AgentState`
- Takes `task_tier` and `task_intent` from classification
- Returns `plan: list[SubagentPlan]` in state
- `SubagentPlan = {"subagent": str, "task": str, "model_tier": str, "budget": int}`

**For now**: The planner ALWAYS returns a single plan that mirrors current behavior (one agent, full tool set). This is a no-op architecturally but establishes the state extension.

**Files**:
- `agent/graph_nodes.py` — Add `plan_node`
- `agent/graph_state.py` — Extend `AgentState` with `plan: list[SubagentPlan]` and `subagent_results: list[dict]`

**Must NOT**: Change the routing logic. The existing conditional edges should work identically.

### S4: Extend AgentState

**What**: Add fields for subagent orchestration to `AgentState`.

**New fields**:
```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    thread_id: str
    task_tier: str
    task_intent: str
    plan: list[dict]  # NEW: [{"subagent": "research", "task": "...", "model_tier": "mid", "budget": 15}]
    subagent_results: list[dict]  # NEW: [{"subagent": "research", "output": "...", "status": "success"}]
```

**Files**:
- `agent/graph_state.py` — Add `plan` and `subagent_results` fields (default empty lists)

### S5: Subagent Result Summarization

**What**: Create a utility that compresses subagent conversation history into a structured summary for the orchestrator.

This is critical for Phase 2 — if we pass full message history between subagents, we lose the token savings. Instead:
- Extract the final assistant message from each subagent
- Extract tool call results that produced key findings
- Compress into a structured summary: `{"subagent": "research", "findings": [...], "key_sources": [...], "output_summary": "..."}`

**Files**:
- `agent/graph_utils.py` (new) — `summarize_subagent_result(messages: list, max_chars: int = 4000) -> dict`

**Must NOT**: Be called yet. This is infrastructure for Phase 2.

### S6: Test Coverage for Refactored Nodes

**What**: Write tests for:
- `make_subagent_node` with different prompts/tools/budgets
- `plan_node` returns correct plan structure
- Domain prompts are valid and contain expected tool references
- Existing graph behavior unchanged after refactoring

**Files**:
- `tests/test_subagent_factory.py` (new)

## What Phase 2 Looks Like (after Phase 1)

```
classify ──► plan ──► research ──► synthesize ──► respond
                     ──► analysis ──► synthesize
                     ──► (direct) ──► respond
```

- `classify`: Unchanged
- `plan`: NEW — decides which subagents to invoke
- `research`: `make_subagent_node(RESEARCH_PROMPT, RESEARCH_TOOLS, tier="mid", budget=15)`
- `analysis`: `make_subagent_node(ANALYSIS_PROMPT, ANALYSIS_TOOLS + WORKSPACE_TOOLS, tier="high", budget=20)`
- `synthesize`: Orchestrator (high-tier model) merges subagent summaries into final response

## Model Selection Strategy

| Role | Model | Reasoning |
|------|-------|-----------|
| Orchestrator (plan + synthesize) | glm-5.1 / Kimi K2.6 | Needs strong reasoning for planning and synthesis |
| Research subagent | Ollama mid-tier (mistral:7b, etc.) | Many tool calls, simple pattern matching, cheap |
| Analysis subagent | OpenRouter mid or glm-5.1 | Needs reliable Python execution, structured output |
| GitHub subagent | Ollama fast-tier | Narrow tool set, template-matching queries |
| Notes/Memory subagent | Ollama fast-tier | Simple CRUD operations, minimal reasoning needed |

## Cost Projection

| Scenario | Current (single agent) | Phase 2 (subagents) | Saving |
|----------|------------------------|---------------------|--------|
| Simple question | 2 calls × high-tier | 2 calls × high-tier | 0% |
| Research task (25 tools) | 25 calls × high-tier | 1 plan + 15 calls × mid + 1 synthesize × high | ~55% |
| Research + analysis | 25 calls × high-tier | 1 plan + 15×mid + 10×mid + 1×high | ~60% |
| Complex multi-intent | 25 calls × high-tier | 1 plan + 20×mid + 1×high | ~65% |

## Success Criteria for Phase 1

- [ ] All 364 existing tests still pass
- [ ] `make_subagent_node(prompts, tools, tier, budget)` creates a working agent subgraph
- [ ] Domain prompts extracted into separate files
- [ ] `plan_node` returns valid plan structure (even if it's always single-agent)
- [ ] `AgentState` has `plan` and `subagent_results` fields
- [ ] No change in user-facing behavior (same responses, same streaming format)
- [ ] `summarize_subagent_result()` utility exists and is unit-tested
- [ ] Graph topology is unchanged — same `classify → agent → tools` loop