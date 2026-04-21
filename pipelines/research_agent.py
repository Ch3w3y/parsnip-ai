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
        SHOW_TOOL_CALLS: bool = True
        SHOW_TOOL_OUTPUTS: bool = False
        REQUEST_TIMEOUT: int = 300
        DEBUG: bool = False
        AUTO_SAVE_SESSIONS: bool = False

    def __init__(self):
        self.name = "Research Agent"
        self.valves = self.Valves(
            AGENT_URL=os.environ.get("AGENT_URL", "http://localhost:8000"),
        )
        self._last_thread_id = None
        self._tools_used = []

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
                        yield event.get("content", "")

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
            return content if isinstance(content, str) else str(content)
        except Exception as e:
            return f"*Pipeline error: {e}*"
