# Testing Guide

This document describes the test suite for `parsnip-ai`.

## Quick Start

```bash
# Run all tests (verbose output)
pytest

# Run all tests silently (CI style)
pytest -q

# Run only tests in tests/ directory
pytest tests/

# Run only unit tests (exclude integration and slow)
pytest -m "not integration and not slow"

# Run only integration tests
pytest -m integration

# Run with coverage
pytest --cov=. --cov-report=term-missing
```

## Test Categories

### Unit Tests (`tests/test_*.py`)

Unit tests verify isolated components and business logic. They:

- Run without external services (database, APIs)
- Mock external dependencies explicitly
- Execute quickly (typically <1s each)

**Example location:** `tests/test_agent_guardrails.py`, `tests/test_joplin_pg.py`

### Integration Tests (`tests/integration/`)

Integration tests verify interactions between components and external systems. They:

- May require services running (PostgreSQL, FastAPI, etc.)
- Mark tests with `@pytest.mark.integration`
- Can be skipped during local development with `-m "not integration"`

**Example location:** `tests/integration/test_joplin_api_integration.py`, `tests/integration/test_registry_integration.py`

### Running Test Categories

```bash
# Unit tests only (no external dependencies)
pytest -m "not integration and not slow"

# Integration tests
pytest -m integration

# Slow tests (LLM calls, real external services)
pytest -m slow
```

## Coverage

```bash
# Run with coverage report
pytest --cov=. --cov-report=term-missing

# Generate HTML coverage report
pytest --cov=. --cov-report=html

# Run specific test with coverage
pytest tests/test_agent_guardrails.py --cov=graph --cov-report=term-missing
```

## Docker-Based Integration Testing

The project includes Docker Compose support for running integration tests against a full stack:

```bash
# Start the stack with test services
docker compose up -d --build

# Run integration tests against running services
pytest -m integration -v

# Run against specific service URLs (e.g., in CI)
AGENT_URL=http://localhost:8000 \
ANALYSIS_URL=http://localhost:8095 \
JOPLIN_MCP_URL=http://localhost:8090 \
pytest -m integration
```

## Adding New Tests

### Testing Pattern: Mocking Database Connections

Use `mock_db_connection` fixture or patch `get_pool` for database tests:

```python
@pytest.fixture(autouse=True)
def _mock_pool_and_owner():
    """Patch get_pool and _get_owner_id so no real DB is touched."""
    with (
        patch(f"{MOD}.get_pool") as mock_get_pool,
        patch(f"{MOD}.ensure_joplin_pool", new_callable=AsyncMock),
        patch(f"{MOD}._get_owner_id", new_callable=AsyncMock, return_value="owner123"),
    ):
        # Setup mock connection
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.connection = MagicMock(return_value=ctx)
        mock_get_pool.return_value = mock_pool
        yield {"pool": mock_pool, "conn": mock_conn}
```

### Testing Pattern: Mocking LLM Calls

Use `mock_ollama` or `mock_httpx_client` fixtures for LLM/HTTP testing:

```python
@pytest.fixture
def mock_ollama():
    """Mock Ollama embedding endpoint."""
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post.return_value = MockResponse()
        yield mock_client
```

### Testing LangChain Tools

LangChain tools are decorated with `@tool` and have a `.name` attribute:

```python
from langchain_core.tools import BaseTool

def test_joplin_pg_functions_are_langchain_tools():
    from tools.joplin_pg import joplin_create_note
    
    assert isinstance(joplin_create_note, BaseTool)
    assert joplin_create_note.name == "joplin_create_note"
```

### Testing FastAPI Endpoints

Use FastAPI's `TestClient` for endpoint testing:

```python
from fastapi.testclient import TestClient

def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
```

## CI Workflow Overview

The CI is defined in `.github/workflows/build-and-publish.yml`:

### Test Job

Runs static and unit tests:

```yaml
- name: Run static / unit tests
  run: pytest -m "not integration and not slow"
```

**Environment variables set:**
- `DATABASE_URL`: postgresql://agent:test@localhost:5432/agent_kb
- `ANALYSIS_URL`: http://localhost:8095
- `JOPLIN_MCP_URL`: http://localhost:8090

### Build and Push Job

Builds Docker images for four services:

- **agent**: Main LangGraph orchestration service (amd64, arm64)
- **analysis**: Python/R/notebook execution service (amd64 only)
- **joplin-mcp**: Joplin integration service (amd64, arm64)
- **scheduler**: Scheduled job runner (amd64, arm64)

Images are pushed to GitHub Container Registry (`ghcr.io`) with:
- Semantic version tags (`v1.2.3`)
- Branch tags (main)
- Pull request tags
- SHA-based tags

### Release Job

Creates GitHub releases when tags matching `v*` are pushed:
- Auto-generates release notes from commits
- Uses `softprops/action-gh-release@v2`

## Test Suite Statistics

**Total tests:** 365

**Test breakdown:**

| Location | Count | Category |
|----------|-------|----------|
| `tests/test_agent_guardrails.py` | 7 | Unit |
| `tests/test_analysis_server.py` | 4 | Unit |
| `tests/test_circuit_breaker.py` | 2 | Unit |
| `tests/test_contracts.py` | 9 | Unit + Integration |
| `tests/test_db_pool.py` | 4 | Unit |
| `tests/test_embed_routing.py` | 3 | Unit |
| `tests/test_imports.py` | 1 | Unit |
| `tests/test_ingestion_utils.py` | 1 | Unit |
| `tests/test_joplin_hitl.py` | 2 | Unit |
| `tests/test_joplin_pg.py` | 23 | Unit |
| `tests/test_joplin_tools_unified.py` | 4 | Unit |
| `tests/test_memory_pool.py` | 3 | Unit |
| `tests/test_openai_compat.py` | 2 | Unit |
| `tests/test_pipeline.py` | 5 | Unit |
| `tests/test_pipeline_joplin.py` | 2 | Unit |
| `tests/test_registry.py` | 3 | Unit |
| `tests/test_scheduler_registry.py` | 3 | Unit |
| `tests/test_stuck_jobs.py` | 4 | Unit |
| `tests/test_workspace_tools.py` | 8 | Unit |
| `tests/integration/test_joplin_api_integration.py` | 19 | Integration |
| `tests/integration/test_registry_integration.py` | 13 | Integration |

## Running Specific Tests

```bash
# Run a single test file
pytest tests/test_agent_guardrails.py

# Run a specific test class
pytest tests/test_agent_guardrails.py::test_model_aliases_resolve_from_environment

# Run tests matching a pattern
pytest -k "joplin"

# Run with verbose output and show print statements
pytest -v -s

# Run withpdb for debugging
pytest --pdb
```
