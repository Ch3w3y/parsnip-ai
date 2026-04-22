# Architecture Visuals

This document describes the main runtime paths in `parsnip-ai`: user requests, retrieval, ingestion, model routing, analysis execution, and backup. The diagrams are intended for operators and contributors who need to understand service boundaries before changing deployment or data flow behavior.

## 1. Runtime Request Path

A chat request enters through OpenWebUI, passes through the pipeline adapter, and is handled by the Agent API. The agent decides whether to answer from memory, retrieve from the knowledge base, call external tools, execute analysis code, or publish a result to Joplin.

```mermaid
flowchart LR
    User[User browser] --> OWUI[OpenWebUI<br/>:3000]
    OWUI --> Pipe[Pipelines adapter<br/>:9099]
    Pipe --> Agent[Agent API<br/>FastAPI :8000]

    subgraph AgentCore[Agent runtime]
        Agent --> Graph[LangGraph orchestration]
        Graph --> Tools[Tool router]
        Graph --> Checkpoints[Conversation checkpoints]
        Graph --> Memory[Long-term memory]
    end

    subgraph Tooling[Tool targets]
        Tools --> KB[Hybrid KB retrieval]
        Tools --> Analysis[Analysis server<br/>Python, R, notebooks]
        Tools --> Joplin[Joplin MCP<br/>notes and resources]
        Tools --> Search[SearXNG / web extraction]
        Tools --> GitHub[GitHub tools]
    end

    Checkpoints --> PG[(PostgreSQL)]
    Memory --> PG
    KB --> PG
    Joplin --> JoplinServer[Joplin Server]
```

Key boundary: OpenWebUI is the user interface, but the Agent API owns orchestration, memory, retrieval, and tool execution.

## 2. Durable Storage Layout

PostgreSQL is the main system of record for the agent. Joplin has a separate database because it is an application with its own schema and sync semantics. Analysis outputs are files, not chat messages, and are served through the analysis service.

```mermaid
flowchart TB
    subgraph Postgres[PostgreSQL container]
        KBTable[knowledge_chunks<br/>content, metadata, vectors]
        Jobs[ingestion_jobs<br/>status and resume points]
        Memories[agent_memories<br/>long-term memory]
        CheckpointTables[LangGraph checkpoint tables]
        Structured[forex_rates<br/>world_bank_data]
        JoplinDB[(joplin database)]
    end

    subgraph Volumes[Docker volumes and mounts]
        PGData[pgdata<br/>Postgres data directory]
        IngestData[ingestion/data<br/>raw dumps and landing files]
        AnalysisOutput[analysis_output<br/>charts, CSVs, notebooks]
        OWUIData[owui_data<br/>OpenWebUI state]
    end

    Agent[Agent API] --> KBTable
    Agent --> Memories
    Agent --> CheckpointTables
    Scheduler[Scheduler] --> Jobs
    Scheduler --> KBTable
    Analysis[Analysis server] --> Structured
    Analysis --> AnalysisOutput
    Joplin[Joplin Server] --> JoplinDB
    Postgres --> PGData
```

Operational note: database volumes must remain on block storage. Object storage is used for backup artifacts, not as a live database filesystem.

## 3. Ingestion and Embedding Flow

Ingestion jobs fetch source data, preserve enough raw or structured input to make the process reproducible, then create chunks and embeddings. The same table is used across text sources, with source-specific metadata kept in JSONB.

```mermaid
sequenceDiagram
    participant Scheduler as Scheduler
    participant Source as Source API or dump
    participant Raw as Raw landing data
    participant Chunker as Cleaner and chunker
    participant Embed as Ollama embeddings
    participant DB as PostgreSQL knowledge_chunks
    participant Jobs as ingestion_jobs

    Scheduler->>Jobs: create running job
    Scheduler->>Source: fetch source records
    Source-->>Raw: persist raw payload or structured rows
    Raw->>Chunker: normalize text and metadata
    Chunker->>Embed: batch embedding request
    Embed-->>Chunker: vector list
    Chunker->>DB: upsert source, source_id, chunk_index
    Scheduler->>Jobs: update processed count
    Scheduler->>Jobs: mark done or failed
```

The stable identity for a text chunk is `(source, source_id, chunk_index)`. For Wikipedia, `source_id` is the article title and `chunk_index` is the chunk number within that article.

## 4. Retrieval Paths

The agent can use multiple retrieval tools depending on the prompt. Simple questions may use a direct KB search; broader research prompts can combine vector search, full-text search, time filtering, source comparison, and document reconstruction.

```mermaid
flowchart LR
    Prompt[User request] --> Router[Tool selection]

    Router --> Vector[Vector search<br/>embedding distance]
    Router --> Text[Full-text search<br/>Postgres FTS]
    Router --> Filters[Metadata filters<br/>source, time, user]
    Router --> Timeline[Timeline retrieval<br/>published_at ordering]
    Router --> Document[Document reconstruction<br/>all chunks by source_id]

    Vector --> Rank[Rerank / merge]
    Text --> Rank
    Filters --> Rank
    Timeline --> Rank
    Document --> Context[Grounded context]
    Rank --> Context
    Context --> Answer[Model response with tool evidence]
```

Retrieval tools should preserve source identifiers in their output so the response can be traced back to the underlying records.

## 5. Model Routing and Fallbacks

The agent resolves stable aliases such as `fast`, `smart`, and `reasoning` into concrete provider IDs from `.env`. Local models are useful for private or low-latency work; cloud or OpenAI-compatible models can be used for larger synthesis tasks when explicitly configured.

```mermaid
flowchart TB
    Request[Agent needs model call] --> Alias[Alias requested<br/>fast, smart, reasoning, graph, classifier]
    Alias --> Env[Resolve from .env<br/>FAST_MODEL, SMART_MODEL, REASONING_MODEL]
    Env --> Backend{Configured backend?}

    Backend -->|OLLAMA_BASE_URL / GPU_LLM_URL| Local[Local Ollama or GPU endpoint]
    Backend -->|OLLAMA_CLOUD_URL| Cloud[Hosted Ollama-compatible API]
    Backend -->|LLM_PROVIDER=openrouter| OpenRouter[OpenRouter]
    Backend -->|LLM_PROVIDER=openai_compat| Compat[OpenAI-compatible endpoint]

    Local --> Invoke[Invoke model]
    Cloud --> Invoke
    OpenRouter --> Invoke
    Compat --> Invoke

    Invoke --> Success{Succeeded?}
    Success -->|yes| Response[Return model output]
    Success -->|rate limit or provider failure| Cascade[Try next configured model in alias chain]
    Cascade --> Response
```

Fallback behavior should stay explicit. A model failure should not silently route sensitive workloads to an external provider unless that provider is configured.

## 6. Analysis Execution and Artifact Handling

Analysis code runs outside the agent process. The agent sends scripts or notebooks to the analysis server, which executes them, captures logs and files, and returns links or summaries to the agent.

```mermaid
sequenceDiagram
    participant Agent as Agent API
    participant Analysis as Analysis Server
    participant Runtime as Python/R runtime
    participant Output as analysis_output volume
    participant GCS as Optional object storage
    participant Joplin as Joplin tools

    Agent->>Analysis: submit script or notebook
    Analysis->>Runtime: execute in controlled workspace
    Runtime-->>Output: write charts, CSVs, notebooks
    Analysis-->>Agent: stdout, stderr, file manifest
    Analysis->>GCS: archive artifacts when configured
    Agent->>Joplin: publish report or attach resources
```

Generated artifacts are operational data. They should be backed up or retained according to the same policy as notebook outputs and user documents.

## 7. Backup and Recovery Flow

Backups are snapshot artifacts. They are not replacements for the live database volume.

```mermaid
flowchart LR
    Scheduler[Scheduler] --> KBBackup[backup_kb.py]
    Scheduler --> ConfigBackup[backup_config.py]

    KBBackup --> PG[(PostgreSQL)]
    KBBackup --> Parquet[Parquet exports<br/>KB, memories, Joplin metadata]

    ConfigBackup --> Project[Project config and selected code]
    ConfigBackup --> Tarball[Compressed tarball]

    Parquet --> LocalBackup[/Local backup dir/]
    Tarball --> LocalBackup
    LocalBackup --> GCS[(Optional GCS bucket)]
```

Recovery should be tested from backup artifacts before relying on them for production operations.

## 8. Guardrails

Runtime guardrails protect the operator from runaway loops, oversized context, missing required data, and unsafe analysis assumptions.

```mermaid
stateDiagram-v2
    [*] --> Request
    Request --> Route
    Route --> ToolBudget

    ToolBudget --> ExecuteTool: under call limit
    ToolBudget --> Stop: call limit exceeded

    ExecuteTool --> RepeatCheck
    RepeatCheck --> Stop: repeated same tool arguments
    RepeatCheck --> ValidateData: new tool call

    ValidateData --> ExecuteAnalysis: required data present
    ValidateData --> AskOrFail: required data missing

    ExecuteAnalysis --> PruneContext
    PruneContext --> Respond
    AskOrFail --> Respond
    Stop --> Respond
    Respond --> [*]
```
