# Routing Configuration

The agent uses a multi-tier routing pipeline to classify query complexity, select
LLM tiers, and route knowledge-base searches to the right sources. All weights
and thresholds are defined in `ROUTING_CONFIG` (`agent/tools/router.py`) for
tuning.

## ROUTING_CONFIG Structure

### Complexity Thresholds

Queries are scored 0.0–1.0 and mapped to tiers:

| Score Range | Tier | Behaviour |
|-------------|------|-----------|
| 0.0 – 0.3 | **low** | Web search only (3 results), no HyDE, 1 KB layer, budget 3 |
| 0.3 – 0.6 | **mid** | Web search (5 results) + HyDE + 3 KB layers, budget 5 |
| 0.6 – 1.0 | **high** | Full pipeline: web (8 results) + HyDE + 5 KB layers, budget 8 |

### Complexity Scoring Weights

Weights are applied additively; raw score is capped at 1.0. They do not need to sum to 1.0.

| Signal | Weight | Description |
|--------|--------|-------------|
| `length` | 0.15 | Longer queries tend to be more complex (normalised to 200 chars) |
| `multi_question` | 0.25 | Multiple `?` marks (normalised to 3) |
| `technical_terms` | 0.20 | Mixed-case or underscore terms (normalised to 5) |
| `comparison` | 0.15 | Keywords: versus, vs, compare, difference, better, worse |
| `synthesis` | 0.15 | Keywords: explain, analyze, summarize, deep dive, comprehensive, thorough, detailed |
| `temporal` | 0.10 | Time-bounded: latest, recent, today, this week/month/year, breaking |

### Search Depth Per Tier

| Parameter | low | mid | high |
|-----------|-----|-----|------|
| `web_results` | 3 | 5 | 8 |
| `hyde` | false | true | true |
| `kb_layers` | 1 | 3 | 5 |
| `kb_budget` | 3 | 5 | 8 |
| `query_expansion` | 1 | 2 | 4 |

## SOURCE_MODEL_MAP — Embedding Model per Source

Different sources use different embedding models based on content type:

| Source | Embedding Model | Dimensions | Rationale |
|--------|-----------------|------------|-----------|
| `github` | `bge-m3` | 1024 | Code-optimised, multilingual; better on source code and technical docs |
| All others | `mxbai-embed-large` | 1024 | General-purpose, default; strong on natural language across domains |

Both models output 1024-dim vectors, fitting the `VECTOR(1024)` column without
schema changes. The `embedding_model` column in `knowledge_chunks` tracks which
model produced each chunk's embedding.

## Pattern Checklist for Adding New Sources

When adding a new ingestion source:

1. **Choose embed model** — `mxbai-embed-large` for natural text, `bge-m3` for code-heavy content. Both must output 1024 dims.
2. **Add to `SOURCE_MODEL_MAP`** — only needed if using `bge-m3` (or any model other than the default). Keys not in the map fall back to `DEFAULT_MODEL` (`mxbai-embed-large`).
3. **Verify existing chunks aren't affected** — changing a source's embed model only affects newly ingested chunks. Existing chunks retain their original `embedding_model` value. For source-wide re-embedding, re-run ingestion with `--from-raw` after updating the model.

## detect_intent() → intent_layers → Source Ordering

`detect_intent()` classifies the query into one of four intents using keyword
regex matching, then `holistic_search` reorders KB source layers accordingly:

| Intent | Trigger | Sources (exact order from `intent_layers`) |
|--------|---------|-------------------------------------------|
| `code` | ≥2 code keywords, or code > research && code > temporal | github → wikipedia → joplin_notes |
| `research` | ≥2 research keywords | arxiv → biorxiv → wikipedia → news |
| `current` | ≥1 temporal keyword | news → wikipedia |
| `general` | default (no dominant intent) | wikipedia → github → joplin_notes → arxiv → news |

Each source is queried independently with its own budget (from `layer_budgets`).
Layers with no relevant results are silently omitted.

### Layer Budgets

| Source | Default Budget | Notes |
|--------|---------------|-------|
| `github` | 6 | Higher for code-centric queries |
| `arxiv` | 4 | |
| `biorxiv` | 4 | |
| `wikipedia` | 3 | |
| `news` | 4 | |
| `joplin_notes` | 2 | Personal notes are typically narrow |

Budgets are applied from `layer_budgets` per source. The `intent_layers` dict defines the exact source list per intent; no reordering is performed beyond this.

### Intent Keyword Regexes

| Intent | Example matches |
|--------|----------------|
| `code` | implement, function, api, python, fastapi, repo, github, framework, deploy, docker, open source |
| `research` | paper, study, neural network, transformer, fine-tune, benchmark, arxiv, biorxiv |
| `temporal` | latest, recent, today, this week, 2025, 2026, 2027, breaking, just announced |

## Embed Model Mismatch Bug (Fixed)

Previously, `holistic_search` generated embeddings using only the default model
(`mxbai-embed-large`) for all layers, including the GitHub layer which stores
`bge-m3` embeddings. This caused poor retrieval on code queries because cosine
similarity between mismatched embedding spaces is near-random.

**Fix**: `holistic_search` now generates embeddings for both models up front and
selects the appropriate set per layer based on `SOURCE_MODEL_MAP`. If `bge-m3`
is unavailable, the GitHub layer falls back to `mxbai-embed-large` with reduced
relevance.

## Reference Tables

### ROUTING_CONFIG Full Reference

```python
ROUTING_CONFIG = {
    "thresholds": {
        "simple": 0.3,
        "moderate": 0.6,
    },
    "weights": {
        "length": 0.15,
        "multi_question": 0.25,
        "technical_terms": 0.20,
        "comparison": 0.15,
        "synthesis": 0.15,
        "temporal": 0.10,
    },
    "search_depth": {
        "low":  {"web_results": 3, "hyde": False, "kb_layers": 1, "kb_budget": 3, "query_expansion": 1},
        "mid":  {"web_results": 5, "hyde": True,  "kb_layers": 3, "kb_budget": 5, "query_expansion": 2},
        "high": {"web_results": 8, "hyde": True,  "kb_layers": 5, "kb_budget": 8, "query_expansion": 4},
    },
    "intent_layers": {
        "code":     ["github", "wikipedia", "joplin_notes"],
        "research": ["arxiv", "biorxiv", "wikipedia", "news"],
        "current":  ["news", "wikipedia"],
        "general":  ["wikipedia", "github", "joplin_notes", "arxiv", "news"],
    },
    "layer_budgets": {
        "github": 6,
        "arxiv": 4,
        "biorxiv": 4,
        "wikipedia": 3,
        "news": 4,
        "joplin_notes": 2,
    },
}
```

### SOURCE_MODEL_MAP

```python
SOURCE_MODEL_MAP = {
    "github": "bge-m3",
}
DEFAULT_MODEL = "mxbai-embed-large"
```