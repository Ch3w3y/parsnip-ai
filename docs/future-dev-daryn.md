# Strategic Development Plan: parsnip-ai 2.0 (The Unified Stack)

**Date:** April 22, 2026  
**Status:** Research & Implementation Roadmap  
**Target:** Daryn (Project Lead)

---

## 1. Vision: "Industrial Backend, Integrated Experience"
The current architecture is powerful because it is **decoupled**. We should not sacrifice this for a "monolithic" OpenWebUI (OWUI) plugin. Instead, we will implement a **Deep Integration Layer** where OWUI becomes a rich "HUD" (Heads-Up Display) for the backend services.

## 2. Track 1: The "Parsnip HUD" (OWUI Actions & Filters)
Instead of typing queries to check status, we will build native OWUI **Actions** (buttons) and **Filters** (auto-injectors).

### **A. Real-time Ingestion Monitor**
*   **The Problem:** You have to run `curl` or check logs to see Wikipedia ingestion progress.
*   **The Idea:** An OWUI **Function (Filter)** that polls the `/stats` endpoint of the agent and displays a "Knowledge Base Status" badge in the sidebar or at the top of every research session.
*   **Complexity:** *Moderate.* Requires an SSE (Server-Sent Events) bridge from the Scheduler to OWUI.

### **B. Artifact Control Action**
*   **The Idea:** A button at the bottom of a research response that lets you:
    *   `[Sync to Joplin]` (Manual override)
    *   `[Clear Analysis Cache]` (Wipes the current thread's `/app/output` files)
    *   `[Open in Analysis Lab]` (Deep link to a dedicated Jupyter/R-Studio instance pointing at the same volume)

## 3. Track 2: Unified Memory Bridge (The "Global Brain")
Currently, OWUI has its own "Memories" and the agent has its "L1-L4 Memory." These are siloed.

### **Implementation Plan:**
*   **Memory Sync Pipe:** Build an OWUI **Function** that hooks into the `on_user_message` event. It extracts OWUI’s "User Memories" (e.g., "I am a Data Scientist," "I prefer Dutch for summaries") and pushes them into the agent's **L1 (Story)** memory layer at the start of every session.
*   **Result:** The agent knows your preferences across *all* frontends, but the "source of truth" for user profile data lives in OWUI.

## 4. Track 3: Analysis Visualization 2.0 (The "Artifact Gallery")
The `output://` placeholder system is a good start, but we can do better.

### **A. Native Markdown Dashboards**
*   **The Idea:** Instead of just links, use an OWUI **Filter** to intercept `output://` tags and replace them with high-fidelity, interactive components.
*   **Tech:** Leverage OWUI’s support for **React components in Markdown**. The Analysis Server would return small JSON datasets which the frontend renders as interactive Plotly/ECharts widgets directly in the chat bubbles.

### **B. The Git-Replay Viewer**
*   **The Idea:** Since every analysis script is committed to git in the background, build a "History" tab in the chat that lets you view the evolution of the code used to generate a specific chart.

## 5. Track 4: Tool-Level Granularity (Hybrid Dispatch)
Right now, the agent is an "all or nothing" pipeline.

### **Implementation Plan:**
*   **Standalone Tools:** Expose individual agent tools (`kb_search`, `arxiv`, `analysis`) as standalone **OpenWebUI Tools**.
*   **The Benefit:** You could use a "light" model (like `gemma4`) for 90% of a conversation using standalone tools, and then "invoke" the full **Research Agent Pipeline** only when you need the 100k-token Kimi 2.6 synthesis. This would be the ultimate cost-control strategy.

## 6. Engineering Requirements & Tech Stack
*   **OWUI Scripting:** High use of the **OpenWebUI Functions API** (Python).
*   **Shared Volume Management:** Hardening the `analysis_output` volume to support concurrent access from the Analysis Server and a potential "Output Browser" service.
*   **API Evolution:** Transitioning the Agent API from `sync/async` chat to a full **WebSocket** implementation to support live multi-tool progress bars in the UI.

---

## 7. Immediate "Quick Wins" (Post-Sleep)
1.  **Ingestion Progress Badge:** A simple OWUI Function that puts a progress bar in the UI for the Wikipedia bulk ingest.
2.  **Joplin Deep-Link Formatter:** A Filter that turns `joplin://` links into nice clickable buttons with the Joplin icon.
3.  **Model Identity Filter:** A Filter that forces the agent to report *exactly* which model (Local vs Cloud) it is currently using for the specific response chunk.

---

**"The stack is the engine; the UI should be the cockpit."**
