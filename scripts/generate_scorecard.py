#!/usr/bin/env python3
"""
Scorecard generator for the 10-prompt pipeline test.
Reads the saved JSONs, checks Joplin + analysis outputs, and produces a scorecard.
"""

import json
import os
import requests
from datetime import datetime

OUTPUT_DIR = "/tmp"
JOPLIN_MCP_URL = "http://localhost:8090"

EXPECTED_NOTES = {
    1: "AI/ML Word Cloud",
    2: "Países Hispanohablantes",
    3: "Chronologie Révolution Française",
    4: "Deutsche Städte Vergleich",
    5: "日本史要約レポート",
    6: "Demographic Statistics Analysis",
    7: "Classificação Geográfica América do Sul",
    8: "Climate Change Comprehensive Report",
    9: "한국 주요 도시 인구 그래프",
    10: "Quantum AI Cross-Domain Synthesis",
}


def get_joplin_notes() -> list[dict]:
    """Fetch all Joplin notes via MCP search."""
    try:
        r = requests.post(
            f"{JOPLIN_MCP_URL}/tools/joplin_search_notes",
            json={"tool": "joplin_search_notes", "arguments": {"query": "", "limit": 100}},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        result_text = data.get("result", "")
        # Parse the markdown format: ## Title\n`id`\ncontent...\n---\n
        notes = []
        blocks = result_text.split("\n\n---\n")
        for block in blocks:
            lines = block.strip().split("\n")
            title = ""
            note_id = ""
            for line in lines:
                # Only the FIRST ## line is the note title; body subheadings are ignored
                if line.startswith("## ") and not title:
                    title = line[3:].strip()
                elif line.startswith("`") and line.endswith("`"):
                    note_id = line.strip("`")
            if title:
                notes.append({"title": title, "id": note_id})
        return notes
    except Exception as e:
        print(f"Error fetching Joplin notes: {e}")
        return []


def score_prompt(num: int, response_data: dict, joplin_notes: list[dict], joplin_before: list[dict]) -> dict:
    """Score a single prompt against the rubric.

    joplin_notes: current state of all notes
    joplin_before: notes that existed BEFORE this test run started
    """
    content = response_data.get("response", {}).get("content", "")
    error = response_data.get("response", {}).get("error", "")

    # Criteria:
    # [JOP] Joplin note created in "Research Outputs" during THIS run
    # [LANG] Response in correct language, non-empty, not an error payload
    # [QUAL] Output is professional, well-structured
    # [TOOLS] KB searched first, then analysis/Joplin chain
    # [VIS] (if applicable) PNG image pulled and dimensions verified

    scores = {}
    details = {}

    # 1. Joplin output — only count notes newly created in this run
    expected_title = EXPECTED_NOTES.get(num, "")
    before_titles = {n["title"].lower() for n in joplin_before}
    newly_created = [n for n in joplin_notes
                     if expected_title.lower() in n["title"].lower()
                     and n["title"].lower() not in before_titles]
    scores["joplin"] = 1 if newly_created else 0
    details["joplin"] = (
        f"Expected: '{expected_title}', "
        f"Newly created: {[n['title'] for n in newly_created]}, "
        f"Pre-existing matches: {[n['title'] for n in joplin_notes if expected_title.lower() in n['title'].lower() and n['title'].lower() in before_titles]}"
    )

    # 2. Language — validate English too; reject empty/error payloads
    lang = response_data.get("lang", "English")
    is_empty_or_error = not content or content.strip() == "" or "*Pipeline error*" in content or "*Error:*" in content
    if is_empty_or_error:
        lang_ok = False
    elif lang == "English":
        # English: require non-empty, coherent response with basic structure
        lang_ok = len(content.split()) >= 5 and not content.startswith("*")
    elif lang == "Spanish":
        lang_ok = any(w in content.lower() for w in ["país", "español", "tabla", "datos"])
    elif lang == "French":
        lang_ok = any(w in content.lower() for w in ["révolution", "française", "chronologie"])
    elif lang == "German":
        lang_ok = any(w in content.lower() for w in ["städte", "deutschland", "vergleich"])
    elif lang == "Japanese":
        lang_ok = any(w in content for w in ["日本", "歴史", "時代"])
    elif lang == "Portuguese":
        lang_ok = any(w in content.lower() for w in ["países", "américa", "sul", "classificação"])
    elif lang == "Korean":
        lang_ok = any(w in content for w in ["한국", "도시", "인구"])
    else:
        lang_ok = not is_empty_or_error
    scores["language"] = 1 if lang_ok else 0
    details["language"] = "Valid response in correct language" if lang_ok else "Empty, error payload, or language mismatch"
    
    # 3. Quality (has markdown structure, tables, or sections)
    quality_ok = any(marker in content for marker in ["#", "|", "---", "##", "###"])
    scores["quality"] = 1 if quality_ok else 0
    details["quality"] = "Has structured markdown" if quality_ok else "Plain text only"
    
    # 4. Tool usage (mentions KB, search, analysis, etc.)
    tool_indicators = ["knowledge base", "base de connaissances", "base de conocimientos", "conhecimento", "知識", "지식"]
    tools_ok = any(t.lower() in content.lower() for t in tool_indicators) or "search" in content.lower()
    scores["tools"] = 1 if tools_ok else 0
    details["tools"] = "KB/analysis referenced" if tools_ok else "No tool usage mentioned"
    
    # 5. Visual (for applicable prompts)
    vis_styles = ["Word Cloud", "Bar Chart", "Statistics", "Multi-Step Chain", "Cross-Domain Synthesis"]
    style = response_data.get("style", "")
    if style in vis_styles:
        # Check for image references or analysis output mentions
        vis_ok = any(marker in content for marker in [".png", "word cloud", "chart", "graph", "visualization", "gráfico"])
        scores["visual"] = 1 if vis_ok else 0
        details["visual"] = "Visual output referenced" if vis_ok else "No visual referenced"
    else:
        scores["visual"] = 0  # N/A
        details["visual"] = "N/A (no visual required)"
    
    # Total out of 5 (or 4 if visual N/A, but let's keep consistent)
    total = sum(scores.values())
    max_possible = 5 if style in vis_styles else 4
    
    return {
        "num": num,
        "lang": lang,
        "style": style,
        "complexity": response_data.get("complexity", ""),
        "scores": scores,
        "details": details,
        "total": total,
        "max": max_possible,
        "elapsed": response_data.get("elapsed_seconds", 0),
        "error": error,
    }


def generate_scorecard():
    joplin_before = get_joplin_notes()
    print(f"Joplin notes BEFORE test run: {len(joplin_before)}")
    for n in joplin_before:
        print(f"  - {n['title']}")
    print()

    joplin_after = get_joplin_notes()
    print(f"Joplin notes AFTER test run: {len(joplin_after)}")
    for n in joplin_after:
        print(f"  - {n['title']}")
    print()

    results = []
    for num in range(1, 11):
        path = os.path.join(OUTPUT_DIR, f"research_pipeline_test_{num:02d}.json")
        if not os.path.exists(path):
            print(f"Missing result file for prompt {num}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        score = score_prompt(num, data, joplin_after, joplin_before)
        results.append(score)
    
    # Print scorecard
    print("=" * 80)
    print("10-PROMPT PIPELINE E2E TEST SCORECARD")
    print("=" * 80)
    print(f"{'#':<4} {'Lang':<12} {'Style':<25} {'Complexity':<10} {'JOP':>4} {'LANG':>4} {'QUAL':>4} {'TOOLS':>5} {'VIS':>4} {'Total':>6} {'Time':>6}")
    print("-" * 80)
    
    grand_total = 0
    grand_max = 0
    for r in results:
        print(f"{r['num']:<4} {r['lang']:<12} {r['style']:<25} {r['complexity']:<10} "
              f"{r['scores']['joplin']:>4} {r['scores']['language']:>4} {r['scores']['quality']:>4} "
              f"{r['scores']['tools']:>5} {r['scores']['visual']:>4} {r['total']}/{r['max']:>3} {r['elapsed']:>5.1f}s")
        grand_total += r['total']
        grand_max += r['max']
    
    print("-" * 80)
    print(f"{'GRAND TOTAL':<4} {'':<12} {'':<25} {'':<10} {'':>4} {'':>4} {'':>4} {'':>5} {'':>4} {grand_total}/{grand_max:>3}")
    print(f"\nPass rate: {grand_total}/{grand_max} = {grand_total/grand_max*100:.1f}%")
    
    # Per-prompt details
    print("\n" + "=" * 80)
    print("DETAILED NOTES")
    print("=" * 80)
    for r in results:
        print(f"\nPrompt {r['num']} ({r['lang']} - {r['style']}):")
        for k, v in r['details'].items():
            print(f"  [{k}] {v}")
        if r['error']:
            print(f"  [ERROR] {r['error']}")
    
    # Save scorecard
    scorecard_path = os.path.join(OUTPUT_DIR, "research_pipeline_scorecard.json")
    with open(scorecard_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "results": results,
            "grand_total": grand_total,
            "grand_max": grand_max,
            "pass_rate": grand_total / grand_max,
            "joplin_notes": joplin_notes,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nScorecard saved to {scorecard_path}")


if __name__ == "__main__":
    generate_scorecard()
