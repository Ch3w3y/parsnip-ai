import os
import psycopg
from psycopg import sql
from langchain_core.tools import tool


CATEGORIES = ["user_prefs", "facts", "decisions", "project_context", "people"]


@tool
async def save_memory(
    content: str, category: str = "facts", importance: int = 3
) -> str:
    """Save a piece of information to long-term memory.

    Use this to remember important facts, user preferences, decisions, project
    context, or people mentioned during conversations. Memories persist across
    sessions and are loaded at the start of new conversations.

    Args:
        content: The information to remember (be specific and concise)
        category: One of: 'user_prefs', 'facts', 'decisions', 'project_context', 'people'
        importance: 1-5, where 5 is critical identity/context, 1 is nice-to-know
    """
    if category not in CATEGORIES:
        return (
            f"[Invalid category '{category}'. Must be one of: {', '.join(CATEGORIES)}]"
        )

    importance = max(1, min(5, int(importance)))
    db_url = os.environ["DATABASE_URL"]

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            await conn.execute(
                """
                INSERT INTO agent_memories (category, content, importance)
                VALUES (%s, %s, %s)
                """,
                (category, content, importance),
            )
            await conn.commit()
    except Exception as e:
        return f"[Memory save failed: {e}]"

    return f"Saved to memory ({category}, importance {importance}/5): {content}"


@tool
async def recall_memory(
    query: str = "", category: str | None = None, limit: int = 10
) -> str:
    """Search long-term memories for previously saved information.

    Use this to recall user preferences, past decisions, project context, or
    facts from earlier conversations. Searches by full-text relevance and
    importance score.

    Args:
        query: What to search for (leave empty to list all memories)
        category: Optional filter — 'user_prefs', 'facts', 'decisions', 'project_context', 'people'
        limit: Max results (default 10)
    """
    limit = min(int(limit), 50)
    db_url = os.environ["DATABASE_URL"]

    conditions = ["deleted_at IS NULL"]
    params: list = []

    if category:
        if category not in CATEGORIES:
            return f"[Invalid category '{category}'. Must be one of: {', '.join(CATEGORIES)}]"
        conditions.append("category = %s")
        params.append(category)

    if query:
        conditions.append(
            "to_tsvector('english', content) @@ plainto_tsquery('english', %s)"
        )
        params.append(query)

    where_clause = " AND ".join(conditions)
    params.append(limit)

    q = f"""
        SELECT id, category, content, importance, created_at
        FROM agent_memories
        WHERE {where_clause}
        ORDER BY importance DESC, created_at DESC
        LIMIT %s
    """

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            rows = await (await conn.execute(q, params)).fetchall()
    except Exception as e:
        return f"[Memory recall failed: {e}]"

    if not rows:
        scope = f" in {category}" if category else ""
        return f"No memories found{scope}."

    parts = []
    for mem_id, cat, content, importance, created_at in rows:
        icon = {5: "★", 4: "●", 3: "○", 2: "·", 1: "·"}.get(importance, "·")
        date_str = created_at.strftime("%Y-%m-%d") if created_at else "unknown"
        parts.append(f"[{icon}] [{cat}] {content}\n  saved {date_str} (id={mem_id})")

    header = f"**Memories — {len(rows)} results**\n"
    return header + "\n\n".join(parts)


@tool
async def update_memory(
    memory_id: int, content: str | None = None, importance: int | None = None
) -> str:
    """Update an existing memory's content or importance score.

    Args:
        memory_id: The ID of the memory to update (from recall_memory results)
        content: New content (optional — leave unchanged if not provided)
        importance: New importance 1-5 (optional)
    """
    db_url = os.environ["DATABASE_URL"]
    updates = []
    params: list = []

    if content is not None:
        updates.append("content = %s")
        params.append(content)
    if importance is not None:
        updates.append("importance = %s")
        params.append(max(1, min(5, int(importance))))
    updates.append("updated_at = NOW()")
    params.append(memory_id)

    q = f"UPDATE agent_memories SET {', '.join(updates)} WHERE id = %s AND deleted_at IS NULL"

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            result = await conn.execute(q, params)
            await conn.commit()
            if result.rowcount == 0:
                return f"[Memory {memory_id} not found or already deleted.]"
    except Exception as e:
        return f"[Memory update failed: {e}]"

    return f"Memory {memory_id} updated."


@tool
async def delete_memory(memory_id: int) -> str:
    """Soft-delete a memory so it no longer appears in recall or session context.

    Args:
        memory_id: The ID of the memory to delete (from recall_memory results)
    """
    db_url = os.environ["DATABASE_URL"]

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            result = await conn.execute(
                "UPDATE agent_memories SET deleted_at = NOW() WHERE id = %s AND deleted_at IS NULL",
                (memory_id,),
            )
            await conn.commit()
            if result.rowcount == 0:
                return f"[Memory {memory_id} not found or already deleted.]"
    except Exception as e:
        return f"[Memory delete failed: {e}]"

    return f"Memory {memory_id} deleted."


@tool
async def recall_memory_by_category(category: str, limit: int = 20) -> str:
    """List all memories in a specific category without requiring a search query.

    Use this to review all user preferences, decisions, facts, or project context
    at once. Unlike recall_memory, this does not need a query string — it returns
    everything in the chosen category.

    Args:
        category: One of: 'user_prefs', 'facts', 'decisions', 'project_context', 'people'
        limit: Max results (default 20)
    """
    if category not in CATEGORIES:
        return f"[Invalid category '{category}'. Must be one of: {', '.join(CATEGORIES)}]"

    limit = min(int(limit), 100)
    db_url = os.environ["DATABASE_URL"]

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            rows = await (await conn.execute(
                """
                SELECT id, content, importance, created_at
                FROM agent_memories
                WHERE deleted_at IS NULL AND category = %s
                ORDER BY importance DESC, created_at DESC
                LIMIT %s
                """,
                (category, limit),
            )).fetchall()
    except Exception as e:
        return f"[Memory recall failed: {e}]"

    if not rows:
        return f"No memories in category '{category}'."

    parts = [f"**Memories in '{category}' — {len(rows)} results**\n"]
    for mem_id, content, importance, created_at in rows:
        icon = {5: "★", 4: "●", 3: "○", 2: "·", 1: "·"}.get(importance, "·")
        date_str = created_at.strftime("%Y-%m-%d") if created_at else "unknown"
        parts.append(f"[{icon}] {content}\n  id={mem_id} | saved {date_str}")

    return "\n\n".join(parts)


@tool
async def summarize_memories(category: str = "", max_insights: int = 10) -> str:
    """Consolidate and summarize memories using LLM analysis.

    Fetches all memories in a category (or all categories) and uses a fast LLM
    to identify patterns, key insights, and redundancies. Returns a concise
    consolidation rather than raw memory entries.

    Use this when you need a high-level overview of what the agent knows about
    the user, rather than individual memory entries.

    Args:
        category: Optional category to focus on ('user_prefs', 'facts',
                  'decisions', 'project_context', 'people'). Leave empty for all.
        max_insights: Maximum number of key insights to return (default 10)
    """
    db_url = os.environ["DATABASE_URL"]
    max_insights = min(int(max_insights), 30)

    conditions = ["deleted_at IS NULL"]
    params: list = []
    if category:
        if category not in CATEGORIES:
            return f"[Invalid category '{category}'. Must be one of: {', '.join(CATEGORIES)}]"
        conditions.append("category = %s")
        params.append(category)

    where = " AND ".join(conditions)
    params.append(50)

    try:
        async with await psycopg.AsyncConnection.connect(db_url) as conn:
            rows = await (await conn.execute(
                f"""
                SELECT id, category, content, importance, created_at
                FROM agent_memories
                WHERE {where}
                ORDER BY importance DESC, created_at DESC
                LIMIT %s
                """,
                params,
            )).fetchall()
    except Exception as e:
        return f"[Memory recall failed: {e}]"

    if not rows:
        scope = f" in '{category}'" if category else ""
        return f"No memories found{scope}."

    memories_text = "\n".join(
        f"[{cat}][{importance}] {content} (id={mem_id}, {created_at.strftime('%Y-%m-%d') if created_at else 'unknown'})"
        for mem_id, cat, content, importance, created_at in rows
    )

    from .llm_client import llm_call

    prompt = (
        f"Analyze these {len(rows)} memories and extract up to {max_insights} key insights.\n\n"
        f"Each line is: [category][importance] content (id, date)\n\n"
        f"Memories:\n{memories_text}\n\n"
        f"Return a structured summary:\n"
        f"1. **Key Insights** (most important patterns and facts)\n"
        f"2. **Redundancies** (memories that overlap and could be consolidated)\n"
        f"3. **Gaps** (areas where more information would be valuable)\n\n"
        f"Keep each insight to 1-2 sentences. Focus on actionable information."
    )

    result = await llm_call(
        messages=[{"role": "user", "content": prompt}],
        tier="simple",
        max_tokens=800,
        temperature=0.3,
    )

    if isinstance(result, str) and result:
        scope = f" in '{category}'" if category else ""
        return f"**Memory Consolidation{scope}** ({len(rows)} memories analyzed)\n\n{result}"

    return f"[Summarization failed for {len(rows)} memories]"
