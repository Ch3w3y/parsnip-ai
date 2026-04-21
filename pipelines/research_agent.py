"""
OpenWebUI Pipeline — Research Agent connector.

Routes all queries through the LangGraph agent (RAG + tools) via SSE streaming.
Features:
  - Conversation threading via chat_id
  - Tool call visibility (toggleable)
  - Streaming tokens for real-time responses
  - Error handling with user-friendly messages
  - Debug mode for troubleshooting
  - Saved session tracking with metadata
"""

import json
import os
from typing import Iterator

import requests
from pydantic import BaseModel


class Pipeline:
    class Valves(BaseModel):
        AGENT_URL: str = "http://localhost:8000"
        JOPLIN_MCP_URL: str = "http://localhost:8090"
        SHOW_TOOL_CALLS: bool = True
        SHOW_TOOL_OUTPUTS: bool = False
        REQUEST_TIMEOUT: int = 300
        DEBUG: bool = False
        AUTO_SAVE_SESSIONS: bool = False

    def __init__(self):
        self.name = "Research Agent"
        self.valves = self.Valves(
            AGENT_URL=os.environ.get("AGENT_URL", "http://localhost:8000"),
            JOPLIN_MCP_URL=os.environ.get("JOPLIN_MCP_URL", "http://localhost:8090"),
        )
        self._last_thread_id = None
        self._tools_used = []

    def _fetch_joplin_note(self, note_id: str) -> str | None:
        """Fetch note content from Joplin MCP bridge."""
        try:
            r = requests.post(
                f"{self.valves.JOPLIN_MCP_URL}/tools/joplin_get_note",
                json={"tool": "joplin_get_note", "arguments": {"note_id": note_id}},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            result = data.get("result", "")
            # Parse markdown title/content from MCP response
            if "##" in result:
                lines = result.split("\n")
                content_lines = []
                for line in lines:
                    if line.startswith("## "):
                        continue
                    if line.startswith("`") and line.endswith("`"):
                        continue
                    content_lines.append(line)
                return "\n".join(content_lines).strip()
            return result
        except Exception:
            return None

    def _enrich_with_joplin(self, content: str) -> str:
        """If response contains a joplin:// link, fetch and prepend note content."""
        import re
        match = re.search(r"joplin://x-callback-url/openNote\?id=([a-f0-9]+)", content)
        if not match:
            return content
        note_id = match.group(1)
        note_content = self._fetch_joplin_note(note_id)
        if note_content:
            return f"{note_content}\n\n---\n\n*{content}*"
        return content

    def _stream_response(
        self,
        user_message: str,
        body: dict,
    ) -> Iterator[str]:
        chat_id = body.get("metadata", {}).get("chat_id") or body.get("chat_id", "")
        thread_id = chat_id or self._last_thread_id or "default"
        self._last_thread_id = thread_id

        if self.valves.DEBUG:
            msg_count = len(body.get("messages", []))
            yield f"\n\n*[Debug: thread_id={thread_id}, chat_id={chat_id}, messages_in_context={msg_count}]*\n\n"

        payload = {
            "message": user_message,
            "thread_id": thread_id,
        }

        accumulated = []

        try:
            with requests.post(
                f"{self.valves.AGENT_URL}/chat",
                json=payload,
                stream=True,
                timeout=self.valves.REQUEST_TIMEOUT,
            ) as response:
                response.raise_for_status()

                for raw_line in response.iter_lines():
                    if not raw_line:
                        continue
                    line = (
                        raw_line.decode("utf-8")
                        if isinstance(raw_line, bytes)
                        else raw_line
                    )
                    if not line.startswith("data: "):
                        continue

                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    if event_type == "token":
                        token = event.get("content", "")
                        accumulated.append(token)
                        yield token

                    elif event_type == "tool_start" and self.valves.SHOW_TOOL_CALLS:
                        tool = event.get("tool", "")
                        if tool not in self._tools_used:
                            self._tools_used.append(tool)
                        tool_input = event.get("input", {})
                        query = ""
                        if isinstance(tool_input, dict):
                            query = tool_input.get(
                                "query",
                                tool_input.get("question", tool_input.get("code", "")),
                            )
                        if len(query) > 100:
                            query = query[:100] + "…"
                        yield f"\n\n> *Using **{tool}**{': ' + query if query else ''}…*\n\n"

                    elif event_type == "tool_end" and self.valves.SHOW_TOOL_OUTPUTS:
                        tool = event.get("tool", "")
                        yield f"\n\n> *✓ {tool} complete*\n\n"

                    elif event_type == "error":
                        yield f"\n\n*Error: {event.get('content', 'Unknown error')}*"

                    elif event_type == "done":
                        break

            # Post-stream: enrich with Joplin content if a deep-link was generated
            full_text = "".join(accumulated)
            enriched = self._enrich_with_joplin(full_text)
            if enriched != full_text:
                # Yield the appended content separator and note body
                yield f"\n\n---\n\n{enriched}"

            if self.valves.AUTO_SAVE_SESSIONS and self._tools_used:
                try:
                    requests.post(
                        f"{self.valves.AGENT_URL}/sessions/save",
                        json={
                            "thread_id": thread_id,
                            "title": user_message[:100],
                            "tools_used": self._tools_used,
                        },
                        timeout=10,
                    )
                except Exception:
                    pass

        except requests.exceptions.ConnectionError:
            yield (
                f"\n\n*Agent backend unreachable at {self.valves.AGENT_URL}. "
                "Is the agent container running?*"
            )
        except requests.exceptions.Timeout:
            yield "\n\n*Request timed out. The agent may be processing a complex query.*"
        except Exception as e:
            yield f"\n\n*Pipeline error: {e}*"

    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: list[dict],
        body: dict,
    ):
        if body.get("stream", False):
            return self._stream_response(user_message=user_message, body=body)

        chat_id = body.get("metadata", {}).get("chat_id") or body.get("chat_id", "")
        thread_id = chat_id or self._last_thread_id or "default"
        self._last_thread_id = thread_id

        try:
            response = requests.post(
                f"{self.valves.AGENT_URL}/chat/sync",
                json={"message": user_message, "thread_id": thread_id},
                timeout=self.valves.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("content", "")
            content = content if isinstance(content, str) else str(content)
            # Enrich with Joplin note content if a deep-link is present
            content = self._enrich_with_joplin(content)
            return content
        except Exception as e:
            return f"*Pipeline error: {e}*"
