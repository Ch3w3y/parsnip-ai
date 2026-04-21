# Hybrid RAG Capabilities Showcase

This document demonstrates the agent's unique ability to **synthesize real-time web data with curated knowledge base (KB) grounding** — a capability we call **Hybrid RAG**.

## What is Hybrid RAG?

Traditional RAG systems retrieve from a static corpus. Our agent goes further:

1. **Live Web Search** — pulls current news, papers, and developments (2025-2026)
2. **KB Grounding** — anchors findings in Wikipedia-curated historical context (5.9M+ chunks, 725k+ articles)
3. **Cross-Source Synthesis** — connects temporal dots: *what's happening now* + *why it matters historically*
4. **Provenance Tracking** — every claim tagged with `[Source: Web Search]` or `[Source: KB Topic]`

---

## Demo 1: Space Exploration — Current and Historical

### Prompt
> Search the web for latest space exploration news (2025-2026), then search the KB for Apollo/NASA history. Synthesize a report connecting current developments to historical context.

### Output Excerpt

# Space Exploration: Current and Historical

## 1. Introduction
Space exploration has entered a new era, transitioning from the Cold War-driven "Space Race" to a collaborative and commercialized frontier.

## 2. Historical Context: The Apollo Era and NASA's Origins
*   **The Space Race:** Ignited by the Soviet Union's launch of *Sputnik 1* in 1957, the U.S. responded by creating NASA in 1958 **[Source: Apollo 11 KB]**
*   **Apollo Program:** Dedicated by President Kennedy, successfully landed 12 astronauts on the Moon between 1969 and 1972 **[Source: Apollo Program KB]**

## 3. The New Frontier: 2025–2026

### NASA's Artemis Program
*   **Current Status:** Artemis 1 tested uncrewed SLS/Orion; crewed missions face spacesuit/HLS delays **[Source: Web Search]**
*   **Next Steps:** Commercial lander demos scheduled for 2027 **[Source: NASA Moon to Mars]**

### SpaceX Starship
*   **Starship Progress:** Iterating on lunar/Martian lander design
*   **Vertical Integration:** In February 2026, SpaceX integrated xAI to accelerate AI-driven rocketry **[Source: SpaceX Updates]**

## 4. Synthesis
The historical focus was on national prestige; the current focus is on sustainability. Artemis utilizes Apollo knowledge to establish long-term lunar presence, facilitating Mars missions **[Source: NASA News]**.

## 5. References
- **KB:** Wikipedia articles on "Apollo 11", "Apollo Program"
- **Web:** SpaceX Official Updates (2026), NASA Artemis Reports, Space.com

---

## Why This Matters

| Capability | Traditional RAG | Hybrid RAG (Ours) |
|------------|---------------|-------------------|
| Data freshness | Stale (last ingestion) | Real-time web + KB |
| Historical depth | Corpus-only | Wikipedia + current news |
| Source provenance | Implicit | Explicit `[Source: X]` tags |
| Cross-domain links | Manual | Auto-generated |
| Output format | Plain text | Markdown reports with Joplin export |

## Running the Demos

```bash
# Run all curated demo prompts
python scripts/run_demo.py

# Run a specific demo scenario
python scripts/run_demo.py --scenario space_exploration
```

## Demo Scenarios Available

| # | Scenario | Web Query | KB Query | Output |
|---|----------|-----------|----------|--------|
| 1 | Space Exploration | Artemis/Starship 2025-2026 | Apollo Program, NASA history | Synthesis report |
| 2 | Quantum Computing | IBM/Google breakthroughs 2025-2026 | Quantum mechanics, algorithms | Analysis + chart |
| 3 | Climate Policy | COP30, carbon targets 2025-2026 | Climate science, renewable energy | Policy brief |
| 4 | AI/ML Trends | Latest models, benchmarks 2025-2026 | AI history, neural networks | Trend report |
| 5 | Geopolitics | Current conflicts, alliances | Historical context, treaties | Risk assessment |

## Architecture

```
User Prompt
    |
    v
[Agent Classifier] -> tier=high, intent=research
    |
    v
[Web Search Tool]  -> current articles, news, papers
    |
    v
[KB Search Tool]   -> Wikipedia grounding, historical context
    |
    v
[Analysis Tool]    -> charts, stats, word clouds (optional)
    |
    v
[Synthesis Node]   -> cross-source markdown report
    |
    v
[Joplin Export]    -> persistent note with joplin:// link
```

## Tags for Marketing

- `#hybrid-rag` — our core differentiator
- `#real-time-research` — web + KB, not just corpus
- `#provenance-tracking` — every claim sourced
- `#cross-domain-synthesis` — connecting temporal + topical boundaries
