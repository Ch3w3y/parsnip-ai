# Extending Parsnip

## Ingestion Extension Pattern

Use the same four-stage pattern for new source types:
1. Fetch source records.
2. Save raw landing payload.
3. Transform/chunk/map metadata.
4. Embed/upsert into target table(s).

Recommended locations:
- `ingestion/ingest_<source>.py`
- scheduler registration in `scheduler/scheduler.py`

## Domain/Org Data Onboarding

Common extension routes:
- API ingestion (REST/GraphQL/vendor feeds)
- File ingestion (PDF/Markdown/Notes)
- Joplin-synced private notebooks
- Database/warehouse extraction (including ODBC/cloud mirrors)

## Tooling Extension

Add new agent capabilities by:
1. Implementing tool module in `agent/tools/`.
2. Registering tool in `agent/tools/__init__.py`.
3. Wiring into `agent/graph_tools.py` and `agent/graph_prompts.py` prompt contracts.

## Structured Data Support

For numeric/analytics workflows:
- Prefer dedicated structured tables over unstructured chunk search.
- Keep source identifiers stable.
- Add preflight validation for required identifiers before expensive analysis runs.
