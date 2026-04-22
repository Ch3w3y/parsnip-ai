"""Tool pack definitions and dynamic tool selection for the agent."""

from tools import (
    adaptive_search,
    holistic_search,
    kb_search,
    research,
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

# ── Tool lists ──────────────────────────────────────────────────────────────

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

# ── Guardrail constants ─────────────────────────────────────────────────────

TOOL_CALL_BUDGETS = {
    "low": 5,
    "mid": 12,
    "high": 25,
}
SAME_TOOL_REPEAT_LIMIT = 2  # repeated same tool with identical args
SAME_TOOL_REPEAT_LIMITS = {
    # Analysis tools often need iterative refinement/fixing across a few runs.
    "execute_r_script": 5,
    "execute_python_script": 5,
    "execute_workspace_script": 5,
    "write_and_execute_script": 5,
    "web_search": 3,
    "kb_search": 3,
    "holistic_search": 3,
    "adaptive_search": 3,
}

ANALYSIS_TOOL_NAMES = {
    "execute_r_script",
    "execute_python_script",
    "execute_notebook",
    "generate_dashboard",
    "write_and_execute_script",
    "execute_workspace_script",
}

# ── Tool selection logic ────────────────────────────────────────────────────

from graph_state import (
    _dedupe_tools,
    _task_intents_from_messages,
    _task_tier_from_messages,
)
from langchain_core.messages import BaseMessage


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
