#!/usr/bin/env python3
"""
10-Prompt E2E Test Suite via OpenWebUI Pipeline (port 9099)
Batches 1 prompt every 60 seconds through the research_agent pipeline.
Logs responses and verifies Joplin + analysis outputs.
"""

import json
import os
import sys
import time
from datetime import datetime

import requests

PIPELINE_URL = "http://localhost:9099/v1/chat/completions"
API_KEY = "owui-pipeline-key"
JOPLIN_MCP_URL = "http://localhost:8090"
OUTPUT_DIR = "/tmp"

PROMPTS = [
    {
        "num": 1,
        "lang": "English",
        "style": "Word Cloud",
        "complexity": "Easy",
        "prompt": "Search the knowledge base for topics related to artificial intelligence and machine learning, then generate a word cloud visualization from the key terms found. Save the resulting word cloud image to the Joplin notebook 'LLM Generated - Research Outputs' with title 'AI/ML Word Cloud'.",
    },
    {
        "num": 2,
        "lang": "Spanish",
        "style": "Data Table",
        "complexity": "Easy",
        "prompt": "Crea una tabla de datos en formato markdown con información sobre los principales países hispanohablantes: nombre, capital, población aproximada y idioma oficial. Guarda esta tabla como una nota en Joplin con el título 'Países Hispanohablantes' en el cuaderno 'LLM Generated - Research Outputs'.",
    },
    {
        "num": 3,
        "lang": "French",
        "style": "Timeline",
        "complexity": "Medium",
        "prompt": "Recherche dans la base de connaissances les informations sur la Révolution française (1789-1799) et crée une chronologie détaillée des événements majeurs. Sauvegarde cette chronologie dans Joplin sous le titre 'Chronologie Révolution Française' dans le carnet 'LLM Generated - Research Outputs'.",
    },
    {
        "num": 4,
        "lang": "German",
        "style": "Comparison Table",
        "complexity": "Medium",
        "prompt": "Erstelle eine Vergleichstabelle in Markdown mit den fünf größten Städten Deutschlands: Stadtname, Einwohnerzahl, Bundesland und ein bekanntes Wahrzeichen. Speichere die Tabelle als Joplin-Notiz mit dem Titel 'Deutsche Städte Vergleich' im Notizbuch 'LLM Generated - Research Outputs'.",
    },
    {
        "num": 5,
        "lang": "Japanese",
        "style": "Summary Report",
        "complexity": "Medium",
        "prompt": "知識ベースから日本の歴史に関する情報を検索し、主要な時代（古代、中世、近世、近代、現代）を含む要約レポートを作成してください。レポートをJoplinのノート「日本史要約レポート」として、ノートブック「LLM Generated - Research Outputs」に保存してください。",
    },
    {
        "num": 6,
        "lang": "English",
        "style": "Statistics",
        "complexity": "Medium",
        "prompt": "Search the knowledge base for world population or demographic data, then run a Python statistical analysis script to calculate basic statistics (mean, median, std dev) by continent or region. Generate a bar chart of the results and save both the analysis and chart to Joplin as 'Demographic Statistics Analysis' in 'LLM Generated - Research Outputs'.",
    },
    {
        "num": 7,
        "lang": "Portuguese",
        "style": "Geographic Classification",
        "complexity": "Medium",
        "prompt": "Pesquise na base de conhecimento sobre os países da América do Sul e crie uma classificação geográfica em formato de tabela: país, capital, área territorial, população e bioma principal. Salve como nota no Joplin com o título 'Classificação Geográfica América do Sul' no caderno 'LLM Generated - Research Outputs'.",
    },
    {
        "num": 8,
        "lang": "English",
        "style": "Multi-Step Chain",
        "complexity": "Hard",
        "prompt": "Execute a multi-step research task: 1) Search the knowledge base for climate change and global warming articles, 2) Extract key statistics and trends, 3) Run a Python script to create a line chart showing temperature anomalies over time, 4) Synthesize everything into a comprehensive markdown report with the chart embedded, 5) Save the final report to Joplin as 'Climate Change Comprehensive Report' in 'LLM Generated - Research Outputs'.",
    },
    {
        "num": 9,
        "lang": "Korean",
        "style": "Bar Chart",
        "complexity": "Medium",
        "prompt": "지식 기반에서 한국 관련 주제를 검색하고, 주요 도시의 인구 데이터를 수집하여 막대 그래프를 생성하세요. 그래프를 Joplin 노트 '한국 주요 도시 인구 그래프'로 노트북 'LLM Generated - Research Outputs'에 저장하세요.",
    },
    {
        "num": 10,
        "lang": "English",
        "style": "Cross-Domain Synthesis",
        "complexity": "Hard",
        "prompt": "Synthesize knowledge across two domains: search the knowledge base for quantum computing and artificial intelligence articles, identify conceptual overlaps and differences, generate a knowledge graph visualization showing the relationships between key concepts, and save the synthesis report with the graph to Joplin as 'Quantum AI Cross-Domain Synthesis' in 'LLM Generated - Research Outputs'.",
    },
]


def send_prompt_via_pipeline(prompt: str, thread_id: str) -> dict:
    """Send a prompt through the OpenWebUI pipeline (port 9099).

    Uses a unique chat_id per prompt to isolate threads and prevent
    context contamination between test runs.
    """
    payload = {
        "model": "research_agent",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "chat_id": thread_id,
        "metadata": {"chat_id": thread_id},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }
    try:
        r = requests.post(PIPELINE_URL, json=payload, headers=headers, timeout=300)
        r.raise_for_status()
        data = r.json()
        # Extract content from OpenAI-format response
        content = ""
        if "choices" in data and len(data["choices"]) > 0:
            choice = data["choices"][0]
            if "message" in choice and "content" in choice["message"]:
                content = choice["message"]["content"]
            elif "text" in choice:
                content = choice["text"]
            else:
                content = str(choice)
        else:
            content = str(data)
        return {"raw": data, "content": content}
    except requests.exceptions.ConnectionError as e:
        return {"error": f"Connection error: {e}"}
    except requests.exceptions.Timeout as e:
        return {"error": f"Timeout: {e}"}
    except Exception as e:
        return {"error": str(e)}


def check_joplin_notes() -> list:
    """Check for notes in the Research Outputs notebook.

    Parses the markdown format returned by joplin_search_notes:
      ## Note Title
      `note_id`
      content...
      ---
    """
    try:
        r = requests.post(
            f"{JOPLIN_MCP_URL}/tools/joplin_search_notes",
            json={"tool": "joplin_search_notes", "arguments": {"query": "", "limit": 50}},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        result_text = data.get("result", "")
        notes = []
        blocks = result_text.split("\n\n---\n")
        for block in blocks:
            for line in block.strip().split("\n"):
                if line.startswith("## "):
                    notes.append(line[3:].strip())
                    break  # only first ## per block is the note title
        return notes
    except Exception as e:
        return [f"Error checking Joplin: {e}"]


def run_test():
    results = []
    joplin_before = set(check_joplin_notes())
    
    print(f"[{datetime.now().isoformat()}] Starting 10-prompt E2E test via PIPELINE (port 9099)")
    print(f"[{datetime.now().isoformat()}] Joplin notes before: {len(joplin_before)}")
    
    for item in PROMPTS:
        num = item["num"]
        print(f"\n{'='*60}")
        print(f"[{datetime.now().isoformat()}] Prompt {num}/10 | {item['lang']} | {item['style']} | {item['complexity']}")
        print(f"Prompt: {item['prompt'][:100]}...")
        
        start_time = time.time()
        thread_id = f"research-test-{num}-{int(start_time)}"
        response = send_prompt_via_pipeline(item["prompt"], thread_id)
        elapsed = time.time() - start_time
        
        result = {
            "num": num,
            "lang": item["lang"],
            "style": item["style"],
            "complexity": item["complexity"],
            "prompt": item["prompt"],
            "elapsed_seconds": round(elapsed, 2),
            "response": response,
            "timestamp": datetime.now().isoformat(),
        }
        
        # Save individual result
        out_path = os.path.join(OUTPUT_DIR, f"research_pipeline_test_{num:02d}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[{datetime.now().isoformat()}] Response saved to {out_path} ({elapsed:.1f}s)")
        
        # Check Joplin for new notes
        joplin_after = set(check_joplin_notes())
        new_notes = joplin_after - joplin_before
        joplin_before = joplin_after
        result["joplin_new_notes"] = list(new_notes)
        if new_notes:
            print(f"[{datetime.now().isoformat()}] New Joplin notes: {new_notes}")
        else:
            print(f"[{datetime.now().isoformat()}] No new Joplin notes detected")
        
        results.append(result)
        
        # Wait 60 seconds before next prompt (unless last)
        if num < 10:
            wait = 60
            print(f"[{datetime.now().isoformat()}] Waiting {wait}s before next prompt...")
            time.sleep(wait)
    
    # Final summary
    summary = {
        "total_prompts": len(results),
        "total_elapsed_seconds": sum(r["elapsed_seconds"] for r in results),
        "prompts": results,
        "final_joplin_notes": list(joplin_before),
    }
    
    summary_path = os.path.join(OUTPUT_DIR, "research_pipeline_test_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print(f"[{datetime.now().isoformat()}] Pipeline test suite complete!")
    print(f"Summary saved to {summary_path}")
    print(f"Total time: {summary['total_elapsed_seconds']:.1f}s")
    print(f"Final Joplin note count: {len(summary['final_joplin_notes'])}")
    
    return summary


if __name__ == "__main__":
    run_test()
