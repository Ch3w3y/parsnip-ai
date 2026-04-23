# Contributing to parsnip-ai

This guide covers the essential steps and requirements for contributing to parsnip-ai. Follow these conventions to keep contributions reviewable and maintainable.

## Branch Naming

Name branches to reflect the change type and scope:

```
feature/<short-description>
fix/<short-description>
refactor/<short-description>
docs/<short-description>
test/<short-description>
```

Examples: `feature/joplin-sync`, `fix/embedding-timeout`, `docs/api-reference`

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:** `feat`, `fix`, `chore`, `docs`, `style`, `refactor`, `perf`, `test`

**Examples:**

```
feat(agent): add hybrid search with pgvector

- Implement similarity search with metadata filters
- Add fallback to full-text search when embeddings unavailable

Closes #42
```

```
fix(scheduler): handle rate limit on news API

- Add exponential backoff for 429 responses
- Log rate limit headers for debugging
```

## Code Style

### Python

The project uses Ruff for linting and Black for formatting. Configuration lives in `pyproject.toml`.

Run linting:

```bash
ruff check .
```

Ruff checks are enforced in CI. Your PR must pass with no errors.

### TypeScript

Frontend code follows standard Next.js/TypeScript conventions. Ensure no TypeScript errors before submitting.

## Testing

Run the test suite before opening a PR:

```bash
pytest -q
```

For unit tests only (faster, no external services):

```bash
pytest -m "not integration and not slow"
```

Tests are configured in `pytest.ini`. New features should include tests covering the core behavior.

## Docker Compose Validation

Always validate the compose stack before committing:

```bash
docker compose config
```

This command must exit with code 0. It verifies YAML syntax and service configuration.

## PR Template

When opening a pull request, include the following in your PR description:

### Summary

One or two sentences describing what this PR does and why.

### Changes

Bulleted list of the main changes:

- Files modified or added
- Key implementation decisions
- Any breaking changes

### Validation

Checklist of validation steps:

- [ ] `docker compose config` passes
- [ ] `ruff check .` passes
- [ ] `pytest -q` passes
- [ ] No secrets or credentials in code
- [ ] Relevant tests added or updated

### Risks

Note any potential risks, edge cases, or areas that need extra review attention.

## Documentation

Update documentation when:

- Changing public API endpoints or request/response schemas
- Adding or modifying configuration options in `.env`
- Changing architecture or service dependencies
- Adding new services or removing existing ones

Update the relevant docs in the `docs/` directory and reference them in your PR.

## What Not to Commit

Never commit:

- `.env` files or any secrets
- API keys, passwords, or credentials
- Database dumps or backups
- Generated files that can be rebuilt

These are already in `.gitignore`. If they appear in your commit, remove them before pushing.

## Review Process

1. Create a feature branch from `main`
2. Make incremental, reviewable commits
3. Run local checks: `docker compose config`, `ruff check .`, `pytest -q`
4. Open a PR with the template filled out
5. Address review feedback
6. Squash or rebase commits if requested before merge
