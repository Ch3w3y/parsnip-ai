## 1. TASK
Switch agent prompts from `kb_search` to `holistic_search`. The user's frontend UI plan requires the agent to use `holistic_search` as the primary search tool.

## 2. EXPECTED OUTCOME
- `agent/graph_tools.py`: Remove `kb_search` from `CORE_TOOLS` (around line 88) and `SAME_TOOL_REPEAT_LIMITS` (around line 209). Remove `kb_search` import.
- `agent/graph_prompts.py`: Update `BASE_PROMPT` to list `holistic_search` first instead of `kb_search`. Change line 30 from listing `kb_search` first to listing `holistic_search` first (or remove `kb_search` from first-call list). Update line 36 similarly.
- `agent/tools/__init__.py`: Remove `from .kb_search import kb_search` and remove `"kb_search"` from `__all__`.

## 3. REQUIRED TOOLS
- Read tool for file contents
- Edit tool for Python files
- grep to verify changes

## 4. MUST DO
- Keep `holistic_search` as the primary search tool in prompts
- Do NOT delete the `kb_search.py` tool file itself (keep for backward compatibility)
- After making changes, verify no syntax errors with `python -m py_compile graph_tools.py graph_prompts.py tools/__init__.py`

## 5. MUST NOT DO
- Do NOT modify any other files
- Do NOT change the `/kb-search` REST endpoint in `main.py`
- Do NOT delete `kb_search.py`
- Do NOT modify graph_tools.py beyond removing the kb_search references
- Do NOT touch the 13 reference locations in other tool docstrings (notes_pg.py, timeline.py, etc.) — out of scope

## 6. CONTEXT
The exploration found:
- `agent/graph_tools.py` line 9: `kb_search` import; line 88: `kb_search` in `CORE_TOOLS`; line 209: `"kb_search": 3` in `SAME_TOOL_REPEAT_LIMITS`
- `agent/graph_prompts.py` line 30: `BASE_PROMPT` mentions `kb_search` first in KB-first rule
- `agent/tools/__init__.py` line 1: `from .kb_search import kb_search`; line 53: `"kb_search"` in `__all__`
- `holistic_search` tool already exists and is imported in `graph_tools.py`

File paths:
- `/home/daryn/parsnip/agent/graph_tools.py`
- `/home/daryn/parsnip/agent/graph_prompts.py`
- `/home/daryn/parsnip/agent/tools/__init__.py`
