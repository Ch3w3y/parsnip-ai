# Architectural Visualizations

## 1. High-Level System Topology
This diagram shows the request flow from the user through the agentic layers to the supporting services.

```mermaid
graph TD
    User((User)) -->|Browser| OWUI[OpenWebUI :3000]
    OWUI -->|SSE Stream| Pipe[Research Pipeline :9099]
    Pipe -->|Chat/Sync| Agent[Agent API :8000]
    
    subgraph "Agentic Core (LangGraph)"
        Agent --> Graph[Tool Orchestration Graph]
        Graph --> Memory[PostgreSQL Memory L1-L4]
    end

    subgraph "Support Services"
        Graph --> Analysis[Analysis Server :8095]
        Graph --> Joplin[Joplin MCP :8090]
        Graph --> Search[SearXNG Meta-Search :8080]
        Graph --> Vector[pgvector Retrieval]
    end

    subgraph "LLM Routing (Hybrid Ollama)"
        Graph -->|Low/Mid Complexity| LocalOllama[Local GPU Ollama]
        Graph -->|High Complexity| CloudOllama[Ollama Cloud Subscription]
    end
```

## 2. Ingestion Pipeline Pattern
The "Fetch-First" pattern ensures that raw data is never lost and embeddings can be re-run without re-hitting external APIs.

```mermaid
sequenceDiagram
    participant Source as External Source (API/Wiki/Joplin)
    participant Raw as Raw Landing Zone (.jsonl.gz)
    participant Embed as Ollama Embeddings
    participant DB as PostgreSQL (pgvector)

    Source->>Raw: 1. Fetch & Persist Raw Payload
    Raw->>Embed: 2. Load, Clean & Chunk
    Embed->>Embed: 3. Generate 1024-dim Vectors
    Embed->>DB: 4. Bulk Upsert (Knowledge Chunks)
    DB->>DB: 5. Rebuild DiskANN Index
```

## 3. Hybrid Model Routing Logic
The agent autonomously determines where to route tasks based on complexity tiers defined in `config.py`.

```mermaid
flowchart LR
    Prompt[User Prompt] --> Class[Classifier: qwen2.5-3b]
    Class --> Tier{Complexity?}
    
    Tier -->|Low/Mid| Local[Local GPU Ollama]
    Local --> Result
    
    Tier -->|High| Cloud[Ollama Cloud Subscription]
    Cloud -->|Kimi-k2.6| Result
    
    subgraph "Model Fleet"
        Local -->|Fast| gemma4
        Local -->|Smart| qwen3.5
        Cloud -->|Reasoning| kimi2.6
    end
```

## 4. Cost Control & Guardrails
Visualizing the loop prevention and context pruning logic that maintains stability.

```mermaid
stateDiagram-v2
    [*] --> Request
    Request --> CheckBudget: Tool Call?
    
    state CheckBudget {
        [*] --> CountCalls
        CountCalls --> BudgetOK: count < 25
        CountCalls --> Terminate: count >= 25
    }
    
    BudgetOK --> LoopDetect: Same Args?
    state LoopDetect {
        [*] --> CheckRepeat
        CheckRepeat --> Execute: < 2 repeats
        CheckRepeat --> Block: >= 2 repeats
    }
    
    Execute --> PruneContext: Large Output?
    PruneContext --> [*]: Truncate > 12k chars
```
