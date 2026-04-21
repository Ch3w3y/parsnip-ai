# Contributing

## Development Flow

1. Create a feature branch.
2. Keep changes scoped and tested.
3. Run local checks before PR.
4. Open PR with behavior summary, risk notes, and test evidence.

## Expectations

- Do not commit secrets, `.env`, keys, or credentials.
- Preserve existing architecture and tool contracts unless explicitly changing them.
- Prefer incremental, reviewable changes over broad refactors.

## Suggested Checks

```bash
docker compose config
pytest -q
```
