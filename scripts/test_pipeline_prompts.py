import requests
import json
import uuid
import time
import sys

PIPELINE_URL = "http://localhost:9099/chat/completions"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Bearer owui-pipeline-key"
}

def send_prompt(prompt, chat_id=None):
    if chat_id is None:
        chat_id = str(uuid.uuid4())
    
    payload = {
        "model": "research_agent",
        "messages": [{"role": "user", "content": prompt}],
        "metadata": {"chat_id": chat_id},
        "stream": False
    }
    
    print(f"\n--- Sending Prompt to Chat ID: {chat_id} ---\n{prompt}\n")
    start_time = time.time()
    try:
        response = requests.post(PIPELINE_URL, headers=HEADERS, json=payload, timeout=600)
        response.raise_for_status()
        end_time = time.time()
        result = response.json()
        content = result['choices'][0]['message']['content']
        print(f"\n--- Response (Time: {end_time - start_time:.2f}s) ---\n{content}\n")
        return content, chat_id
    except Exception as e:
        print(f"Error: {e}")
        return None, chat_id

# Test Case 1: Complex Research + Web + KB + Analysis
prompt1 = """Analyze the correlation between the recent performance of the Australian Dollar (AUD) against the Japanese Yen (JPY) and geopolitical tensions in East Asia over the last 90 days.
1. Use `search_web` to find the latest news on geopolitical tensions in the South China Sea or Taiwan Strait.
2. Use `kb_search` to retrieve historical AUD/JPY rates from the 'forex' source and background on the Yen's safe-haven status.
3. Use the `analysis_server` (Python) to create a time-series plot of AUD/JPY rates (from the KB) and overlay markers for significant geopolitical events found via web search.
4. Generate a summary report in English, concluding with a brief executive summary in Japanese and German."""

# Test Case 2: Structured Data Analysis + Heatmap
prompt2 = """Compare economic indicators for the 'BRICS' countries using World Bank data from the knowledge base.
1. Use `kb_search` (source: 'world_bank') to find indicators like GDP growth, Inflation, and Debt-to-GDP for Brazil, Russia, India, China, and South Africa.
2. Use `execute_python_script` to create a heatmap showing these indicators across the 5 countries for the most recent year available in the structured `world_bank_data` table.
3. Analyze which country shows the most stable growth-to-inflation ratio.
4. Save the results to a new Joplin notebook named 'BRICS Economic Analysis'."""

# Test Case 3: Cross-domain + Multi-language + Geospatial (Simulated)
# Note: Geospatial is tricky without a specific map tool, but the analysis server can use geopandas/matplotlib for mapping.
prompt3 = """Investigate the impact of climate change on agriculture in Southeast Asia, specifically focusing on rice production.
1. Use `kb_search` to find relevant Wikipedia or arXiv papers on 'climate impact on rice Southeast Asia'.
2. Use `search_web` for any very recent (2025/2026) reports on crop yields in Vietnam and Thailand.
3. Use `execute_r_script` to perform a time-series statistical analysis of yield projections vs temperature increases if data is available in the KB or provided via search.
4. Create a visualization (e.g., a regional map or bar chart) showing projected yield changes by 2030.
5. Provide the report in English, but include a 'Methodology' section in French."""

if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_num = int(sys.argv[1])
        if test_num == 1:
            send_prompt(prompt1)
        elif test_num == 2:
            send_prompt(prompt2)
        elif test_num == 3:
            send_prompt(prompt3)
    else:
        # Run a sequence
        _, cid1 = send_prompt(prompt1)
        # Give it a moment between runs
        time.sleep(5)
        _, cid2 = send_prompt(prompt2)
