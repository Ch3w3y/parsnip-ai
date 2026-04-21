"""
joplin_mcp — Tools for interacting with Joplin Server via the MCP server.

Provides: create_notebook, create_note, update_note, edit_note, delete_note,
get_note, search_notes, list_notebooks, list_tags, get_tags_for_note,
upload_resource, ping.

The MCP server exposes a REST proxy at POST /tools/{tool_name} for direct tool calls.
"""

import os
import json
import httpx
from langchain_core.tools import tool

MCP_URL = os.environ.get("JOPLIN_MCP_URL", "http://localhost:8090")


async def _mcp_call(tool_name: str, arguments: dict) -> str:
    """Call a tool on the Joplin MCP server via its REST proxy endpoint."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{MCP_URL}/tools/{tool_name}",
            json={"tool": tool_name, "arguments": arguments},
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            return f"Error: {data['error']}"
        return data.get("result", str(data))


@tool
async def joplin_create_notebook(
    title: str,
    parent_notebook_id: str = "",
) -> str:
    """Create a new Joplin notebook (folder).

    Always prefix agent-created notebooks with "LLM Generated - " so they are
    visually distinct from the user's own notebooks.

    Args:
        title: Notebook name — prefix with "LLM Generated - " for agent-created notebooks
        parent_notebook_id: Parent notebook ID for nested notebooks (optional)
    """
    args: dict = {"title": title}
    if parent_notebook_id:
        args["parent_notebook_id"] = parent_notebook_id
    return await _mcp_call("joplin_create_notebook", args)


@tool
async def joplin_create_note(
    title: str,
    content: str,
    notebook_id: str = "",
    tags: list[str] | None = None,
) -> str:
    """Create a note in Joplin Server.

    Args:
        title: Note title
        content: Markdown body
        notebook_id: Parent folder ID (from joplin_list_notebooks)
        tags: Optional list of tags
    """
    args = {"title": title, "content": content}
    if notebook_id:
        args["notebook_id"] = notebook_id
    if tags:
        args["tags"] = tags
    return await _mcp_call("joplin_create_note", args)


@tool
async def joplin_update_note(
    note_id: str,
    title: str = "",
    content: str = "",
    append: bool = False,
) -> str:
    """Update an existing Joplin note.

    Args:
        note_id: Note ID to update
        title: New title (optional)
        content: New content (optional)
        append: If true, append to existing content instead of replacing
    """
    args = {"note_id": note_id}
    if title:
        args["title"] = title
    if content:
        args["content"] = content
    args["append"] = append
    return await _mcp_call("joplin_update_note", args)


@tool
async def joplin_get_note(note_id: str) -> str:
    """Retrieve a Joplin note by ID.

    Args:
        note_id: Note ID
    """
    return await _mcp_call("joplin_get_note", {"note_id": note_id})


@tool
async def joplin_search_notes(query: str, limit: int = 10) -> str:
    """Search Joplin notes by keyword.

    Args:
        query: Search query
        limit: Max results (default 10)
    """
    return await _mcp_call("joplin_search_notes", {"query": query, "limit": limit})


@tool
async def joplin_list_notebooks() -> str:
    """List all Joplin notebooks (folders)."""
    return await _mcp_call("joplin_list_notebooks", {})


@tool
async def joplin_upload_resource(
    filename: str,
    content_b64: str,
    note_id: str = "",
    mime_type: str = "",
) -> str:
    """Upload a file (image, PDF, code) to Joplin and optionally attach to a note.

    Args:
        filename: Filename
        content_b64: Base64-encoded file content
        note_id: Note ID to attach resource to (optional)
        mime_type: MIME type (auto-detected if not provided)
    """
    args = {"filename": filename, "content_b64": content_b64}
    if note_id:
        args["note_id"] = note_id
    if mime_type:
        args["mime_type"] = mime_type
    return await _mcp_call("joplin_upload_resource", args)


@tool
async def joplin_edit_note(
    note_id: str,
    find: str = "",
    replace: str = "",
    append: str = "",
    prepend: str = "",
    regex: bool = False,
) -> str:
    """Precision edit a Joplin note: find/replace, append, or prepend content.

    Args:
        note_id: Note ID to edit
        find: Text to find (or regex pattern if regex=True)
        replace: Replacement text for find/replace
        append: Text to append to the end of the note body
        prepend: Text to prepend to the beginning of the note body
        regex: Treat find as a regex pattern (default False)
    """
    args: dict = {"note_id": note_id}
    if find:
        args["find"] = find
    if replace:
        args["replace"] = replace
    if append:
        args["append"] = append
    if prepend:
        args["prepend"] = prepend
    if regex:
        args["regex"] = regex
    return await _mcp_call("joplin_edit_note", args)


@tool
async def joplin_delete_note(note_id: str) -> str:
    """Soft-delete a Joplin note by setting its deleted_time.

    Args:
        note_id: Note ID to delete
    """
    return await _mcp_call("joplin_delete_note", {"note_id": note_id})


@tool
async def joplin_list_tags() -> str:
    """List all tags in Joplin Server."""
    return await _mcp_call("joplin_list_tags", {})


@tool
async def joplin_get_tags_for_note(note_id: str) -> str:
    """Get all tags associated with a Joplin note.

    Args:
        note_id: Note ID
    """
    return await _mcp_call("joplin_get_tags_for_note", {"note_id": note_id})


@tool
async def joplin_ping() -> str:
    """Health check — returns 'ok' and PostgreSQL connection status for the Joplin MCP server."""
    return await _mcp_call("joplin_ping", {})
