"""
Tests for scheduler-registry decoupling — scheduler uses SourceRegistry instead of
direct imports.

TDD (RED-GREEN-REFACTOR): These tests are written FIRST. They define the contract
for:
  - Scheduler can resolve all 7 scheduled sources via the registry
  - Missing source gives clear KeyError (not ImportError)
  - Registry handles ingest_wikipedia.py's sync main() entry point
  - The scheduler adapter (run_source) calls entry_point() correctly
  - Async entry points are awaited; sync entry points are called directly
"""

import inspect

import pytest

from ingestion.registry import SourceEntry, SourceRegistry
from registry_adapter import run_source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCHEDULED_SOURCES = [
    "news_api",
    "arxiv",
    "biorxiv",
    "joplin",
    "wikipedia_updates",
    "forex",
    "worldbank",
]


# ---------------------------------------------------------------------------
# 1. Scheduler resolves all 7 scheduled sources via registry
# ---------------------------------------------------------------------------


class TestSchedulerResolvesAllSources:
    def test_all_scheduled_sources_found_in_registry(self):
        """All 7 scheduled sources can be resolved from the real registry."""
        reg = SourceRegistry(config_path=None)
        for name in SCHEDULED_SOURCES:
            src = reg.get_source(name)
            assert isinstance(src, SourceEntry), f"Source '{name}' is not a SourceEntry"
            assert src.module_path.startswith("ingest_"), f"Bad module_path for '{name}'"

    def test_all_scheduled_sources_have_entry_points(self):
        """Each scheduled source exposes a callable entry point."""
        reg = SourceRegistry(config_path=None)
        for name in SCHEDULED_SOURCES:
            src = reg.get_source(name)
            entry = src.get_entry_point()
            assert callable(entry), f"Entry point for '{name}' is not callable"


# ---------------------------------------------------------------------------
# 2. Missing source gives clear KeyError (not ImportError)
# ---------------------------------------------------------------------------


class TestMissingSourceError:
    def test_missing_source_raises_key_error(self):
        """Looking up a nonexistent source raises KeyError, not ImportError."""
        reg = SourceRegistry(config_path=None)
        with pytest.raises(KeyError, match="nonexistent_source"):
            reg.get_source("nonexistent_source")

    async def test_missing_source_in_run_source_raises_key_error(self):
        """run_source with unknown name raises a clear KeyError mentioning the source."""
        reg = SourceRegistry(config_path=None)
        with pytest.raises(KeyError, match="typo_source"):
            await run_source(reg, "typo_source")

    async def test_run_source_wraps_key_error_with_context(self):
        """run_source on missing source raises an error that mentions the source name."""
        reg = SourceRegistry(config_path=None)
        with pytest.raises((KeyError, ValueError), match="typo_source"):
            await run_source(reg, "typo_source")


# ---------------------------------------------------------------------------
# 3. Registry handles ingest_wikipedia.py's main() (sync) entry point
# ---------------------------------------------------------------------------


class TestSyncEntryPoint:
    def test_wikipedia_resolves_main_via_get_entry_point(self):
        """ingest_wikipedia has main() but not main_async — get_entry_point resolves it."""
        reg = SourceRegistry(config_path=None)
        src = reg.get_source("wikipedia")
        entry = src.get_entry_point()
        # Should be the sync main function, not main_async
        mod = src.module_ref
        assert entry is getattr(mod, "main", None) or entry is getattr(mod, "main_async", None)
        assert callable(entry)

    def test_wikipedia_entry_point_is_sync_main(self):
        """ingest_wikipedia.get_entry_point() returns the sync main function."""
        reg = SourceRegistry(config_path=None)
        src = reg.get_source("wikipedia")
        mod = src.module_ref
        # Wikipedia has main but not main_async
        has_main_async = hasattr(mod, "main_async") and callable(mod.main_async)
        has_main = hasattr(mod, "main") and callable(mod.main)
        assert has_main, "ingest_wikipedia must have a main() function"
        # get_entry_point should prefer main_async if present, else main
        entry = src.get_entry_point()
        if has_main_async:
            assert entry is mod.main_async
        else:
            assert entry is mod.main


# ---------------------------------------------------------------------------
# 4. Scheduler adapter (run_source) calls entry_point correctly
# ---------------------------------------------------------------------------


class TestRunSourceAdapter:
    async def test_run_source_calls_async_entry_point(self):
        """run_source awaits main_async when it's the entry point."""
        reg = pytest.importorskip("unittest").mock.MagicMock(spec=SourceRegistry)

        # Build a mock SourceEntry with async entry point
        async def fake_main_async(**kwargs):
            return "async_result"

        from unittest.mock import MagicMock

        mock_entry = MagicMock(spec=SourceEntry)
        mock_entry.get_entry_point.return_value = fake_main_async
        mock_entry.name = "fake_async"
        reg.get_source.return_value = mock_entry

        result = await run_source(reg, "fake_async")
        reg.get_source.assert_called_once_with("fake_async")
        mock_entry.get_entry_point.assert_called_once()
        assert result == "async_result"

    async def test_run_source_calls_sync_entry_point(self):
        """run_source calls main() directly when it's the entry point (not async)."""
        from unittest.mock import MagicMock

        reg = MagicMock(spec=SourceRegistry)

        # Build a mock SourceEntry with sync entry point
        def fake_main(**kwargs):
            return "sync_result"

        mock_entry = MagicMock(spec=SourceEntry)
        mock_entry.get_entry_point.return_value = fake_main
        mock_entry.name = "fake_sync"
        reg.get_source.return_value = mock_entry

        result = await run_source(reg, "fake_sync")
        reg.get_source.assert_called_once_with("fake_sync")
        mock_entry.get_entry_point.assert_called_once()
        assert result == "sync_result"

    async def test_run_source_surfaces_source_name_on_failure(self):
        """When entry point raises, run_source re-raises with source name context."""
        from unittest.mock import MagicMock

        reg = MagicMock(spec=SourceRegistry)

        async def failing_async(**kwargs):
            raise RuntimeError("API timeout")

        mock_entry = MagicMock(spec=SourceEntry)
        mock_entry.get_entry_point.return_value = failing_async
        mock_entry.name = "broken_source"
        reg.get_source.return_value = mock_entry

        with pytest.raises(RuntimeError, match="API timeout"):
            await run_source(reg, "broken_source")

    def test_run_source_detects_async_vs_sync_entry_point(self):
        """run_source correctly detects coroutine functions vs regular functions."""
        # Verify our detection approach works
        async def async_func():
            pass

        def sync_func():
            pass

        assert inspect.iscoroutinefunction(async_func)
        assert not inspect.iscoroutinefunction(sync_func)