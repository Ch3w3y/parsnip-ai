# Ingestion Review — Issues & Problems

## Active Issues

## Resolved Issues

## Patterns Learned
- Landing zone pattern: fetch → save_raw(jsonl.gz) → iter_raw → embed → upsert
- Entry point convention: `main_async()` preferred, `main()` fallback with `argparse`
- Conflict strategies: `skip` for immutable (papers), `update` for mutable (notes, wikipedia)
- `sources.yaml` is the single source of truth for schedules + conflict + embedding model
- `SourceRegistry` auto-discovers scripts not declared in YAML
13. ingest_wikipedia.py has sync `def main()` (not async), contrary to audit report claim
14. Only 11/14 scripts use `save_raw` (landing zone): joplin, news, wikipedia_updates, wikipedia do NOT
