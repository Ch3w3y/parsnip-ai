#!/usr/bin/env python3
"""
Web Search + KB Grounding Test
Strategy: Web search for a current topic, then KB search for Wikipedia grounding.
Validates hybrid RAG: real-time + historical context synthesis.
"""

import json, os, time
from datetime import datetime
import requests

URL = "http://localhost:9099/v1/chat/completions"
KEY = "owui-pipeline-key"
JOPLIN_MCP_URL = "http://localhost:8090"

PROMPTS = [
    {
        "num": 11,
        "name": "Web→KB Current Events",
        "prompt": (
            "First, do a web search for the latest news about space exploration in 2025-2026 "
            "(e.g., Artemis missions, SpaceX Starship, or Mars rover updates). "
            "Then search the knowledge base for Wikipedia articles about the history of space exploration, "
            "NASA, or related space agencies to provide historical grounding. "
            "Synthesize a report that connects the latest developments with the historical context, "
            "citing both web sources and KB sources. Save the report to Joplin as "
            "'Space Exploration: Current and Historical' in 'LLM Generated - Research Outputs'."
        ),
    },
    {
        "num": 12,
        "name": "Web→KB Tech Trends",
        "prompt": (
            "Search the web for the latest developments in quantum computing in 2025-2026 "
            "(e.g., IBM Quantum, Google Willow, or error correction breakthroughs). "
            "Then search the knowledge base for Wikipedia articles about quantum mechanics, "
            "quantum computing fundamentals, and quantum algorithms. "
            "Write a grounded analysis that explains the significance of the latest breakthroughs "
            "using the foundational knowledge from the KB. Save to Joplin as "
            "'Quantum Computing: Latest Breakthroughs & Fundamentals' in 'LLM Generated - Research Outputs'."
        ),
    },
    {
        "num": 13,
        "name": "News→KB Policy Analysis",
        "prompt": (
            "Search the web for recent climate policy developments in 2025-2026 "
            "(e.g., COP30, national carbon targets, or green energy legislation). "
            "Then search the knowledge base for Wikipedia articles on climate change science, "
            "greenhouse gas effects, and renewable energy technologies. "
            "Produce a policy analysis that grounds current political developments in scientific fact. "
            "Save to Joplin as 'Climate Policy 2025-2026: Web & KB Synthesis' in 'LLM Generated - Research Outputs'."
        ),
    },
]

def send(prompt, chat_id):
    r = requests.post(URL, json={
        "model":"research_agent",
        "messages":[{"role":"user","content":prompt}],
        "stream":False,
        "chat_id": chat_id,
        "metadata": {"chat_id": chat_id},
    }, headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"}, timeout=300)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

def get_joplin_notes():
    try:
        r = requests.post(f"{JOPLIN_MCP_URL}/tools/joplin_search_notes",
            json={"tool":"joplin_search_notes","arguments":{"query":"","limit":50}}, timeout=10)
        return r.json().get("result","")
    except Exception as e:
        return str(e)

print(f"[{datetime.now().isoformat()}] Starting Web+KB Grounding Tests\n")
for item in PROMPTS:
    print(f"Prompt {item['num']} — {item['name']}")
    start = time.time()
    chat_id = f"web-kb-test-{item['num']}-{int(start)}"
    content = send(item["prompt"], chat_id)
    elapsed = time.time() - start
    print(f"  -> {elapsed:.1f}s | Response length: {len(content)}")
    has_joplin = "joplin://" in content
    has_web = any(w in content.lower() for w in ["http://","https://","source:","web search","searched"])
    has_kb = any(w in content.lower() for w in ["knowledge base","wikipedia","kb search","corpus"])
    print(f"  -> Joplin link: {has_joplin} | Web refs: {has_web} | KB refs: {has_kb}")
    with open(f"/tmp/web_kb_test_{item['num']}.json","w") as f:
        json.dump({"prompt":item["prompt"],"content":content,"elapsed":elapsed}, f)
    print(f"  -> Saved to /tmp/web_kb_test_{item['num']}.json\n")
    if item['num'] != 13:
        print("  Waiting 60s...")
        time.sleep(60)

print(f"[{datetime.now().isoformat()}] Web+KB tests complete. New Joplin notes:\n")
print(get_joplin_notes())
