#!/usr/bin/env python3
"""
Curated Demo Runner — Hybrid RAG Showcase
Executes pre-built scenarios that demonstrate web+KB synthesis capabilities.

Usage:
  python scripts/run_demo.py              # Run all demos
  python scripts/run_demo.py --list       # List available scenarios
  python scripts/run_demo.py --scenario space_exploration
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import requests

PIPELINE_URL = "http://localhost:9099/v1/chat/completions"
API_KEY = "owui-pipeline-key"
JOPLIN_MCP_URL = "http://localhost:8090"
OUTPUT_DIR = "/tmp/demos"

SCENARIOS = {
    "space_exploration": {
        "title": "Space Exploration: Current and Historical",
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
    "quantum_computing": {
        "title": "Quantum Computing: Latest Breakthroughs & Fundamentals",
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
    "climate_policy": {
        "title": "Climate Policy 2025-2026: Web & KB Synthesis",
        "prompt": (
            "Search the web for recent climate policy developments in 2025-2026 "
            "(e.g., COP30, national carbon targets, or green energy legislation). "
            "Then search the knowledge base for Wikipedia articles on climate change science, "
            "greenhouse gas effects, and renewable energy technologies. "
            "Produce a policy analysis that grounds current political developments in scientific fact. "
            "Save to Joplin as 'Climate Policy 2025-2026: Web & KB Synthesis' in 'LLM Generated - Research Outputs'."
        ),
    },
    "ai_trends": {
        "title": "AI/ML Trends 2025-2026: State of the Field",
        "prompt": (
            "Search the web for the latest AI/ML developments in 2025-2026 "
            "(e.g., new model releases, benchmark results, or regulatory news). "
            "Then search the knowledge base for Wikipedia articles on artificial intelligence history, "
            "neural networks, and machine learning fundamentals. "
            "Create a trend report that contextualizes current breakthroughs within the field's evolution. "
            "Save to Joplin as 'AI/ML Trends 2025-2026: State of the Field' in 'LLM Generated - Research Outputs'."
        ),
    },
    "geopolitics": {
        "title": "Geopolitical Risk Assessment: Current + Historical",
        "prompt": (
            "Search the web for current geopolitical developments in 2025-2026 "
            "(e.g., conflicts, alliances, trade agreements, or sanctions). "
            "Then search the knowledge base for Wikipedia articles on related historical contexts, "
            "treaties, and regional histories. "
            "Produce a risk assessment that grounds current events in historical patterns. "
            "Save to Joplin as 'Geopolitical Risk Assessment: Current + Historical' in 'LLM Generated - Research Outputs'."
        ),
    },
}


def send_prompt(prompt: str) -> str:
    r = requests.post(
        PIPELINE_URL,
        json={"model": "research_agent", "messages": [{"role": "user", "content": prompt}], "stream": False},
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        timeout=300,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def run_scenario(key: str, scenario: dict):
    print(f"\n{'='*70}")
    print(f"DEMO: {scenario['title']}")
    print(f"{'='*70}")
    print(f"Prompt: {scenario['prompt'][:120]}...")

    start = time.time()
    content = send_prompt(scenario["prompt"])
    elapsed = time.time() - start

    # Metrics
    has_joplin = "joplin://" in content
    has_web = any(w in content.lower() for w in ["http://", "https://", "source:", "web search", "searched"])
    has_kb = any(w in content.lower() for w in ["knowledge base", "wikipedia", "kb search", "corpus"])
    word_count = len(content.split())

    print(f"\nResults:")
    print(f"  Time:          {elapsed:.1f}s")
    print(f"  Words:         {word_count}")
    print(f"  Joplin link:   {'Yes' if has_joplin else 'No'}")
    print(f"  Web refs:      {'Yes' if has_web else 'No'}")
    print(f"  KB refs:       {'Yes' if has_kb else 'No'}")

    # Save
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"demo_{key}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "scenario": key,
            "title": scenario["title"],
            "prompt": scenario["prompt"],
            "content": content,
            "elapsed_seconds": round(elapsed, 2),
            "metrics": {
                "word_count": word_count,
                "has_joplin_link": has_joplin,
                "has_web_refs": has_web,
                "has_kb_refs": has_kb,
            },
            "timestamp": datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)
    print(f"  Saved to:      {path}")

    return content


def main():
    parser = argparse.ArgumentParser(description="Run Hybrid RAG demo scenarios")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), help="Run a specific scenario")
    parser.add_argument("--list", action="store_true", help="List available scenarios")
    args = parser.parse_args()

    if args.list:
        print("Available demo scenarios:")
        for key, s in SCENARIOS.items():
            print(f"  {key:<20} — {s['title']}")
        return

    if args.scenario:
        run_scenario(args.scenario, SCENARIOS[args.scenario])
    else:
        print(f"[{datetime.now().isoformat()}] Running all {len(SCENARIOS)} demo scenarios...")
        for i, (key, scenario) in enumerate(SCENARIOS.items(), 1):
            run_scenario(key, scenario)
            if i < len(SCENARIOS):
                print(f"\n  Waiting 60s before next demo...")
                time.sleep(60)
        print(f"\n[{datetime.now().isoformat()}] All demos complete. Outputs in {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
