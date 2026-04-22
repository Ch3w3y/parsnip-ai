"""
title: Ingestion Status HUD
author: parsnip-ai
author_url: https://github.com/Ch3w3y/parsnip-ai
description: Adds a real-time ingestion status badge to the bottom of every assistant message.
version: 0.1.0
"""

import requests
import json
from typing import Optional

class Action:
    def __init__(self):
        # Configuration - adjust to match your network topology
        self.agent_url = "http://localhost:8000"

    def action(self, body: dict, __user__: Optional[dict] = None, __event_emitter__: Optional[callable] = None) -> dict:
        """
        Polls the agent /stats endpoint and appends a status badge to the message.
        """
        try:
            # 1. Fetch stats from the parsnip agent
            response = requests.get(f"{self.agent_url}/stats", timeout=2)
            if response.status_code != 200:
                return body
            
            stats = response.json()
            kb = stats.get("knowledge_base", [])
            jobs = stats.get("ingestion_jobs", [])
            
            # 2. Extract Wikipedia specific progress
            wiki_kb = next((s for s in kb if s["source"] == "wikipedia"), {})
            wiki_job = next((j for j in jobs if j["source"] == "wikipedia" and j["status"] == "running"), None)
            
            chunks = wiki_kb.get("chunks", 0)
            status_line = f"📚 **KB Chunks:** {chunks:,}"
            
            if wiki_job:
                processed = wiki_job.get("processed", 0)
                total = wiki_job.get("total", "??")
                status_line += f" | ⚙️ **Ingesting:** {processed}/{total} articles..."

            # 3. Create a clean HUD to append
            hud = f"\n\n---\n<small>{status_line}</small>"
            
            # 4. Modify the last message in the body to include the HUD
            if "messages" in body and len(body["messages"]) > 0:
                body["messages"][-1]["content"] += hud

            return body

        except Exception as e:
            # Silent fail to avoid disrupting the chat
            print(f"Ingestion HUD Error: {e}")
            return body
