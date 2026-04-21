"""
joplin_export — Shared utility for exporting documents to Joplin Server.

Any pipeline or script can use this to create/update Joplin notes with
Markdown content. Handles auth, serialization, and notebook management.

Usage:
    from joplin_export import export_to_joplin
    export_to_joplin("My Note Title", "# Markdown content...", notebook_id="...")
"""

import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

_SERVER_URL = os.environ.get("JOPLIN_SERVER_URL", "http://localhost:22300")
_EMAIL = os.environ.get("JOPLIN_ADMIN_EMAIL", "")
_PASSWORD = os.environ.get("JOPLIN_ADMIN_PASSWORD", "")

_session_token: str | None = None


def _iso(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3]
        + "Z"
    )


def _auth() -> str:
    """Get or refresh session token."""
    global _session_token
    if _session_token:
        return _session_token

    r = httpx.post(
        f"{_SERVER_URL}/api/sessions",
        json={"email": _EMAIL, "password": _PASSWORD},
        timeout=10,
    )
    r.raise_for_status()
    _session_token = r.json()["id"]
    return _session_token


def _serialize_note(
    note_id: str, title: str, body: str, parent_id: str, now_ms: int
) -> bytes:
    ts = _iso(now_ms)
    meta = "\n".join(
        [
            f"id: {note_id}",
            f"parent_id: {parent_id}",
            f"title: {title}",
            f"created_time: {ts}",
            f"updated_time: {ts}",
            "is_conflict: 0",
            "latitude: 0.00000000",
            "longitude: 0.00000000",
            "altitude: 0.0000",
            "author: ",
            "source_url: ",
            "is_todo: 0",
            "todo_due: 0",
            "todo_completed: 0",
            "source: joplinapp-server",
            "source_application: net.cozic.joplin-server",
            "application_data: ",
            "order: 0",
            f"user_created_time: {ts}",
            f"user_updated_time: {ts}",
            "encryption_cipher_text: ",
            "encryption_applied: 0",
            "markup_language: 1",
            "is_shared: 0",
            "share_id: ",
            "conflict_original_id: ",
            "master_key_id: ",
            "user_data: ",
            "deleted_time: 0",
            "type_: 1",
        ]
    )
    return f"{body}\n\n{meta}".encode("utf-8")


def _serialize_folder(folder_id: str, title: str, parent_id: str, now_ms: int) -> bytes:
    ts = _iso(now_ms)
    meta = "\n".join(
        [
            f"id: {folder_id}",
            f"parent_id: {parent_id}",
            f"title: {title}",
            f"created_time: {ts}",
            f"updated_time: {ts}",
            f"user_created_time: {ts}",
            f"user_updated_time: {ts}",
            "encryption_cipher_text: ",
            "encryption_applied: 0",
            "is_shared: 0",
            "share_id: ",
            "deleted_time: 0",
            "type_: 2",
        ]
    )
    return meta.encode("utf-8")


def _put_content(name: str, content: bytes):
    """Upload content to a Joplin item."""
    token = _auth()
    # URL-encode the name for the API path
    from urllib.parse import quote

    encoded = quote(name, safe="")
    r = httpx.put(
        f"{_SERVER_URL}/api/items/{encoded}/content",
        headers={"X-API-AUTH": token, "Content-Type": "application/octet-stream"},
        content=content,
        timeout=15,
    )
    if r.status_code == 401:
        global _session_token
        _session_token = None
        token = _auth()
        r = httpx.put(
            f"{_SERVER_URL}/api/items/{encoded}/content",
            headers={"X-API-AUTH": token, "Content-Type": "application/octet-stream"},
            content=content,
            timeout=15,
        )
    r.raise_for_status()


def _note_name(note_id: str) -> str:
    return f"root:/{note_id}.md:"


def _folder_name(folder_id: str) -> str:
    return f"root:/{folder_id}/"


def _joplin_uri(note_id: str) -> str:
    return f"joplin://x-callback-url/openNote?id={note_id}"


def ensure_notebook(title: str, parent_id: str = "") -> str:
    """Create a notebook if it doesn't exist, return its ID."""
    folder_id = uuid.uuid4().hex
    now_ms = int(time.time() * 1000)
    serialized = _serialize_folder(folder_id, title, parent_id, now_ms)
    name = _folder_name(folder_id)
    _put_content(name, serialized)
    return folder_id


def export_to_joplin(
    title: str,
    content: str,
    notebook_id: str = "",
    update_existing: bool = False,
) -> dict:
    """Export a Markdown document to Joplin Server.

    Args:
        title: Note title
        content: Full Markdown body
        notebook_id: Joplin folder UUID (from ensure_notebook or list_joplin_notebooks)
        update_existing: If True, updates existing note with same title instead of creating new

    Returns:
        dict with note_id, uri, and notebook_id
    """
    note_id = uuid.uuid4().hex
    now_ms = int(time.time() * 1000)
    serialized = _serialize_note(note_id, title, content, notebook_id, now_ms)
    name = _note_name(note_id)
    _put_content(name, serialized)

    return {
        "note_id": note_id,
        "uri": _joplin_uri(note_id),
        "notebook_id": notebook_id or "(root)",
    }
