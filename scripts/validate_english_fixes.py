#!/usr/bin/env python3
"""Quick validation of English-only pipeline fixes."""
import json, os, time
from datetime import datetime
import requests

URL = "http://localhost:9099/v1/chat/completions"
KEY = "owui-pipeline-key"

PROMPTS = [
    {
        "num": 1,
        "prompt": "Search the knowledge base for topics related to artificial intelligence and machine learning, then generate a word cloud visualization from the key terms found. Save the resulting word cloud image to the Joplin notebook 'LLM Generated - Research Outputs' with title 'AI/ML Word Cloud'.",
    },
    {
        "num": 8,
        "prompt": "Execute a multi-step research task: 1) Search the knowledge base for climate change and global warming articles, 2) Extract key statistics and trends, 3) Run a Python script to create a line chart showing temperature anomalies over time, 4) Synthesize everything into a comprehensive markdown report with the chart embedded, 5) Save the final report to Joplin as 'Climate Change Comprehensive Report' in 'LLM Generated - Research Outputs'.",
    },
    {
        "num": 10,
        "prompt": "Synthesize knowledge across two domains: search the knowledge base for quantum computing and artificial intelligence articles, identify conceptual overlaps and differences, generate a knowledge graph visualization showing the relationships between key concepts, and save the synthesis report with the graph to Joplin as 'Quantum AI Cross-Domain Synthesis' in 'LLM Generated - Research Outputs'.",
    },
]

def send(prompt):
    r = requests.post(URL, json={"model":"research_agent","messages":[{"role":"user","content":prompt}],"stream":False}, headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"}, timeout=300)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

def get_joplin_notes():
    try:
        r = requests.post("http://localhost:8090/tools/joplin_search_notes", json={"tool":"joplin_search_notes","arguments":{"query":"","limit":50}}, timeout=10)
        return r.json().get("result","")
    except Exception as e:
        return str(e)

print(f"[{datetime.now().isoformat()}] Starting 3-prompt English validation\n")
for item in PROMPTS:
    print(f"Prompt {item['num']}: {item['prompt'][:80]}...")
    start = time.time()
    content = send(item["prompt"])
    elapsed = time.time() - start
    print(f"  -> {elapsed:.1f}s | Response length: {len(content)}")
    if "joplin://" in content:
        print(f"  -> Contains Joplin link")
    else:
        print(f"  -> NO Joplin link in response")
    with open(f"/tmp/validation_{item['num']}.json","w") as f:
        json.dump({"prompt":item["prompt"],"content":content,"elapsed":elapsed}, f)
    print(f"  -> Saved to /tmp/validation_{item['num']}.json\n")
    if item['num'] != 10:
        print("  Waiting 60s...")
        time.sleep(60)

print(f"[{datetime.now().isoformat()}] Validation complete. Joplin notes:\n")
print(get_joplin_notes())
