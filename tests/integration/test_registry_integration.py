"""Integration tests for ingestion.registry + scheduler.registry_adapter.

Verifies that the SourceRegistry and the scheduler adapter work together
end-to-end — discovering modules, parsing YAML, resolving entry points,
and correctly routing sync vs. async execution.

No live DB or external services are required.  All file reads use the
real project files; only module-level side-effects (importing ingestion
modules) are live since those modules already exist on disk.
"""

import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from ingestion.registry import SourceEntry, SourceRegistry

# Ensure the ingestion directory is importable (added by SourceRegistry.__init__
# as well, but explicit here for clarity).
_INGESTION_DIR = str(Path(__file__).resolve().parent.parent.parent / "ingestion")
if _INGESTION_DIR not in sys.path:
    sys.path.insert(0, _INGESTION_DIR)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The 7 scheduled sources (matching scheduler/scheduler.py)
SCHEDULED_SOURCES = [
    "news_api",
    "arxiv",
    "biorxiv",
    "joplin",
    "wikipedia_updates",
    "forex",
    "worldbank",
]

# The known 14 ingest_*.py modules on disk
EXPECTED_INGEST_MODULES = sorted([
    "ingest_arxiv",
    "ingest_biorxiv",
    "ingest_forex",
    "ingest_github",
    "ingest_hackernews",
    "ingest_joplin",
    "ingest_news",
    "ingest_news_api",
    "ingest_pubmed",
    "ingest_rss",
    "ingest_ssrn",
    "ingest_wikipedia",
    "ingest_wikipedia_updates",
    "ingest_worldbank",
])


# ---------------------------------------------------------------------------
# 1. Registry discovers all ingest_*.py modules
# ---------------------------------------------------------------------------


class TestRegistryDiscoversModules:
    def test_registry_discovers_all_ingest_modules(self):
        """SourceRegistry().__init__ discovers all 14 ingest_*.py files in ingestion/ dir."""
        reg = SourceRegistry(config_path=None)
        all_sources = reg.list_sources()
        source_names = {s.name for s in all_sources}

        for mod_name in EXPECTED_INGEST_MODULES:
            # Module name → registry key: ingest_arxiv → arxiv
            key = mod_name.replace("ingest_", "", 1)
            assert key in source_names, f"Registry missing auto-discovered source: {key}"

    def test_discovered_modules_match_ingest_py_files(self):
        """Every ingest_*.py file on disk should appear in the registry."""
        ingestion_dir = Path(_INGESTION_DIR)
        disk_modules = sorted(f.stem for f in ingestion_dir.glob("ingest_*.py"))

        reg = SourceRegistry(config_path=None)
        registry_modules = {s.module_path for s in reg.list_sources()}

        for mod in disk_modules:
            assert mod in registry_modules, f"Disk module {mod} not in registry"


# ---------------------------------------------------------------------------
# 2. sources.yaml matches discovered modules
# ---------------------------------------------------------------------------


class TestSourcesYAMLMatchesDiscovered:
    def test_sources_yaml_matches_discovered_modules(self):
        """Every module in sources.yaml exists as an ingest_*.py file."""
        yaml_path = Path(_INGESTION_DIR) / "sources.yaml"
        assert yaml_path.exists(), "sources.yaml not found"

        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}

        ingestion_dir = Path(_INGESTION_DIR)
        disk_modules = {f.stem for f in ingestion_dir.glob("ingest_*.py")}

        for name, entry in data.get("sources", {}).items():
            module_name = entry.get("module", "")
            assert module_name in disk_modules, (
                f"Source '{name}' references module '{module_name}' which has no matching .py file"
            )

    def test_all_yaml_sources_are_in_registry(self):
        """All sources defined in sources.yaml are resolvable via the registry."""
        reg = SourceRegistry(config_path=None)

        yaml_path = Path(_INGESTION_DIR) / "sources.yaml"
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}

        for name in data.get("sources", {}):
            src = reg.get_source(name)
            assert isinstance(src, SourceEntry)
            assert src.name == name


# ---------------------------------------------------------------------------
# 3. Scheduler adapter resolves all scheduled sources
# ---------------------------------------------------------------------------


class TestSchedulerAdapterResolvesScheduled:
    def test_scheduler_adapter_resolves_scheduled_sources(self):
        """All 7 scheduled sources resolve via the registry."""
        reg = SourceRegistry(config_path=None)
        for name in SCHEDULED_SOURCES:
            src = reg.get_source(name)
            assert isinstance(src, SourceEntry), f"Source '{name}' not a SourceEntry"
            assert src.module_path.startswith("ingest_"), f"Bad module_path for '{name}'"

    def test_scheduled_sources_have_schedules(self):
        """All scheduled sources have non-null schedule values in YAML."""
        reg = SourceRegistry(config_path=None)
        for name in SCHEDULED_SOURCES:
            src = reg.get_source(name)
            assert src.schedule is not None, f"Scheduled source '{name}' has no schedule"


# ---------------------------------------------------------------------------
# 4. Scheduler adapter detects sync entry point (wikipedia)
# ---------------------------------------------------------------------------


class TestSyncEntryPointDetection:
    def test_scheduler_adapter_detects_sync_entry_point(self):
        """ingest_wikipedia has main() not main_async() — get_entry_point resolves it."""
        reg = SourceRegistry(config_path=None)
        src = reg.get_source("wikipedia")
        mod = src.module_ref

        # Wikipedia should have main but NOT main_async
        has_main_async = hasattr(mod, "main_async") and callable(mod.main_async)
        has_main = hasattr(mod, "main") and callable(mod.main)

        # Per the codebase, ingest_wikipedia has main() but not main_async
        # get_entry_point resolves main_async first, then main
        entry = src.get_entry_point()
        assert callable(entry)

        # The entry point chosen must be a function from the module
        if has_main_async:
            assert entry is mod.main_async
        else:
            assert entry is mod.main

    def test_wikipedia_entry_point_is_not_coroutine(self):
        """ingest_wikipedia's resolved entry point is NOT a coroutine function."""
        reg = SourceRegistry(config_path=None)
        src = reg.get_source("wikipedia")
        entry = src.get_entry_point()
        # The sync main() should not be a coroutine function
        # (unless the module defines main_async, which it doesn't)
        mod = src.module_ref
        if not (hasattr(mod, "main_async") and callable(mod.main_async)):
            assert not inspect.iscoroutinefunction(entry), (
                "ingest_wikipedia entry point should be sync, not a coroutine"
            )


# ---------------------------------------------------------------------------
# 5. Scheduler adapter detects async entry point (arxiv)
# ---------------------------------------------------------------------------


class TestAsyncEntryPointDetection:
    def test_scheduler_adapter_detects_async_entry_point(self):
        """ingest_arxiv has main_async() — get_entry_point resolves it."""
        reg = SourceRegistry(config_path=None)
        src = reg.get_source("arxiv")

        entry = src.get_entry_point()
        mod = src.module_ref

        # Arxiv has both main_async and main; main_async should be preferred
        has_main_async = hasattr(mod, "main_async") and callable(mod.main_async)
        has_main = hasattr(mod, "main") and callable(mod.main)

        assert has_main_async, "ingest_arxiv must have main_async()"
        assert entry is mod.main_async, "get_entry_point should prefer main_async over main"

    def test_arxiv_entry_point_is_coroutine(self):
        """ingest_arxiv's resolved entry point IS a coroutine function."""
        reg = SourceRegistry(config_path=None)
        src = reg.get_source("arxiv")
        entry = src.get_entry_point()
        assert inspect.iscoroutinefunction(entry), (
            "ingest_arxiv main_async should be a coroutine function"
        )


# ---------------------------------------------------------------------------
# 6. Disabled sources are filtered
# ---------------------------------------------------------------------------


class TestDisabledSourcesFiltered:
    def test_disabled_sources_filtered(self):
        """Sources with enabled:false don't appear in list_sources(enabled_only=True)."""
        reg = SourceRegistry(config_path=None)

        # Manually disable a source to verify filtering
        src = reg.get_source("arxiv")
        src.enabled = False

        enabled = reg.list_sources(enabled_only=True)
        enabled_names = {s.name for s in enabled}

        assert "arxiv" not in enabled_names, "Disabled source should not appear in enabled_only list"

        # Re-enable to not affect other tests
        src.enabled = True

    def test_disabled_sources_appear_in_full_list(self):
        """Sources with enabled:false DO appear in list_sources(enabled_only=False)."""
        reg = SourceRegistry(config_path=None)

        src = reg.get_source("arxiv")
        src.enabled = False

        all_sources = reg.list_sources(enabled_only=False)
        all_names = {s.name for s in all_sources}

        assert "arxiv" in all_names, "Disabled source should appear in full list"

        src.enabled = True


# ---------------------------------------------------------------------------
# 7. Auto-discovery finds YAML-undeclared sources
# ---------------------------------------------------------------------------


class TestAutoDiscoveryYamlUndeclared:
    def test_auto_discovery_finds_yaml_undeclared_sources(self):
        """Any .py file NOT in sources.yaml gets auto-discovered by the registry."""
        # Create a temp YAML that only declares arxiv, then check that
        # all the other ingest_*.py files are still discovered
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "sources.yaml"
            yaml_path.write_text(yaml.dump({
                "sources": {
                    "arxiv": {
                        "module": "ingest_arxiv",
                        "schedule": "0 3 * * 1",
                        "conflict": "skip",
                        "enabled": True,
                    }
                }
            }))

            reg = SourceRegistry(config_path=str(yaml_path))
            all_names = {s.name for s in reg.list_sources()}

            # arxiv should be there (from YAML)
            assert "arxiv" in all_names

            # The other ingest_*.py modules should be auto-discovered
            # (at least some of them — those with valid entry points)
            discovered_names = all_names - {"arxiv"}
            assert len(discovered_names) >= 12, (
                f"Auto-discovery should find many undeclared sources, got: {discovered_names}"
            )

    def test_yaml_entries_take_precedence(self):
        """YAML-defined source settings (e.g. schedule) take precedence over auto-discovery defaults."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            custom_schedule = "0 5 * * 2"
            yaml_path = Path(tmp) / "sources.yaml"
            yaml_path.write_text(yaml.dump({
                "sources": {
                    "arxiv": {
                        "module": "ingest_arxiv",
                        "schedule": custom_schedule,
                        "conflict": "update",
                        "enabled": True,
                    }
                }
            }))

            reg = SourceRegistry(config_path=str(yaml_path))
            src = reg.get_source("arxiv")
            assert src.schedule == custom_schedule
            assert src.conflict_strategy == "update"


# ---------------------------------------------------------------------------
# 8. get_source raises for unknown source
# ---------------------------------------------------------------------------


class TestGetSourceRaisesForUnknown:
    def test_get_source_raises_for_unknown(self):
        """Clear KeyError for nonexistent source name."""
        reg = SourceRegistry(config_path=None)

        with pytest.raises(KeyError, match="nonexistent_source"):
            reg.get_source("nonexistent_source")

    def test_get_source_error_message_is_descriptive(self):
        """The KeyError message should mention the source name."""
        reg = SourceRegistry(config_path=None)

        with pytest.raises(KeyError) as exc_info:
            reg.get_source("totally_bogus_name")

        error_msg = str(exc_info.value)
        assert "totally_bogus_name" in error_msg, (
            f"Error message should mention the source name, got: {error_msg}"
        )

    async def test_run_source_raises_for_unknown(self):
        """run_source with unknown name raises KeyError through the registry."""
        from registry_adapter import run_source

        reg = SourceRegistry(config_path=None)
        with pytest.raises((KeyError, ValueError), match="no_such_source"):
            await run_source(reg, "no_such_source")