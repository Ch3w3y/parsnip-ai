"""
Tests for ingestion.registry — SourceRegistry + sources.yaml plugin system.

TDD: These tests are written FIRST. They define the contract for:
  - YAML parsing of sources.yaml
  - Required field validation (name, module, schedule, conflict_strategy)
  - Auto-discovery of ingest_*.py files not declared in YAML
  - Missing module detection
  - Duplicate name detection
  - Enabled/disabled filtering
  - Flexible entry point: main_async or main
  - Programmatic registration
"""

import importlib
import textwrap
from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from ingestion.registry import SourceEntry, SourceRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

YAML_DIR = Path(__file__).resolve().parent.parent / "ingestion"


def _write_yaml(tmp_path: Path, content: dict) -> Path:
    """Write a dict as YAML to a temp file and return the path."""
    p = tmp_path / "sources.yaml"
    p.write_text(yaml.dump(content, default_flow_style=False))
    return p


def _valid_sources() -> dict:
    """Return a minimal valid sources dict for testing."""
    return {
        "sources": {
            "arxiv": {
                "module": "ingest_arxiv",
                "schedule": "0 3 * * 1",
                "conflict": "skip",
                "enabled": True,
            },
        }
    }


# ---------------------------------------------------------------------------
# 1. YAML parsing
# ---------------------------------------------------------------------------


class TestYAMLParsing:
    def test_loads_sources_from_yaml_file(self, tmp_path):
        """Registry can parse a valid sources.yaml file."""
        cfg = _write_yaml(tmp_path, _valid_sources())
        reg = SourceRegistry(config_path=str(cfg))
        assert "arxiv" in [s.name for s in reg.list_sources()]

    def test_loads_default_sources_yaml_when_no_path(self):
        """Registry falls back to ingestion/sources.yaml when config_path is None."""
        reg = SourceRegistry(config_path=None)
        # Should not raise, and should contain sources from the shipped YAML
        all_sources = reg.list_sources()
        assert len(all_sources) >= 14  # at least the 14 official sources

    def test_empty_sources_key_yields_no_yaml_entries(self, tmp_path):
        """An empty sources dict is valid — auto-discovery still runs."""
        cfg = _write_yaml(tmp_path, {"sources": {}})
        reg = SourceRegistry(config_path=str(cfg))
        # No YAML sources, but auto-discovery should still find ingest_*.py files
        discovered = [s for s in reg.list_sources() if s.module_path.startswith("ingest_")]
        assert len(discovered) >= 14


# ---------------------------------------------------------------------------
# 2. Required field validation
# ---------------------------------------------------------------------------


class TestRequiredFields:
    def test_missing_module_raises(self, tmp_path):
        """A source entry without 'module' should raise ValueError."""
        cfg = _write_yaml(
            tmp_path,
            {"sources": {"bad": {"schedule": "0 0 * * *", "conflict": "skip"}}},
        )
        with pytest.raises(ValueError, match="module"):
            SourceRegistry(config_path=str(cfg))

    def test_missing_conflict_raises(self, tmp_path):
        """A source entry without 'conflict' should raise ValueError."""
        cfg = _write_yaml(
            tmp_path,
            {"sources": {"bad": {"module": "ingest_arxiv", "schedule": "0 0 * * *"}}},
        )
        with pytest.raises(ValueError, match="conflict"):
            SourceRegistry(config_path=str(cfg))

    def test_missing_schedule_is_allowed_for_manual_sources(self, tmp_path):
        """Sources without a schedule (manual-only) are valid — schedule defaults to None."""
        cfg = _write_yaml(
            tmp_path,
            {"sources": {"manual_src": {"module": "ingest_github", "conflict": "update"}}},
        )
        reg = SourceRegistry(config_path=str(cfg))
        src = reg.get_source("manual_src")
        assert src.schedule is None


# ---------------------------------------------------------------------------
# 3. Auto-discovery of ingest_*.py files
# ---------------------------------------------------------------------------


class TestAutoDiscovery:
    def test_discovers_14_ingest_scripts(self):
        """Auto-discovery should find all 14 ingest_*.py files."""
        reg = SourceRegistry(config_path=None)
        all_sources = reg.list_sources()
        ingest_names = [s.name for s in all_sources]
        # The 14 known scripts
        expected = [
            "arxiv", "biorxiv", "forex", "github", "hackernews",
            "joplin", "news_api", "news", "pubmed", "rss",
            "ssrn", "wikipedia", "wikipedia_updates", "worldbank",
        ]
        for name in expected:
            assert name in ingest_names, f"Missing auto-discovered source: {name}"

    def test_yaml_entries_take_precedence_over_discovery(self, tmp_path):
        """If a source is in YAML, those settings win; auto-discovery doesn't overwrite."""
        cfg = _write_yaml(
            tmp_path,
            {
                "sources": {
                    "arxiv": {
                        "module": "ingest_arxiv",
                        "schedule": "0 5 * * 2",  # different from default
                        "conflict": "update",  # different from default
                        "enabled": True,
                    }
                }
            },
        )
        reg = SourceRegistry(config_path=str(cfg))
        src = reg.get_source("arxiv")
        assert src.schedule == "0 5 * * 2"
        assert src.conflict_strategy == "update"


# ---------------------------------------------------------------------------
# 4. Missing module detection
# ---------------------------------------------------------------------------


class TestMissingModuleDetection:
    def test_nonexistent_module_raises(self, tmp_path):
        """A YAML entry referencing a nonexistent module should raise."""
        cfg = _write_yaml(
            tmp_path,
            {"sources": {"ghost": {"module": "ingest_nonexistent", "conflict": "skip"}}},
        )
        with pytest.raises(ValueError, match="ingest_nonexistent"):
            SourceRegistry(config_path=str(cfg))

    def test_module_without_entry_point_raises(self, tmp_path):
        """A module that exists but has neither main_async nor main should raise."""
        # Create a fake module file with no main entry point
        fake_mod = tmp_path / "ingest_fake_noentry.py"
        fake_mod.write_text("# no main functions here\nx = 1\n")
        cfg = _write_yaml(
            tmp_path,
            {"sources": {"fake_n": {"module": "ingest_fake_noentry", "conflict": "skip"}}},
        )
        with pytest.raises(ValueError, match="entry point"):
            SourceRegistry(
                config_path=str(cfg),
                extra_ingestion_dirs=[str(tmp_path)],
            )


# ---------------------------------------------------------------------------
# 5. Duplicate name detection
# ---------------------------------------------------------------------------


class TestDuplicateNameDetection:
    def test_duplicate_name_in_yaml_raises(self, tmp_path):
        """Duplicate source names in YAML should raise ValueError."""
        # YAML maps can't technically have duplicate keys, but
        # programmatic registration can create duplicates.
        cfg = _write_yaml(tmp_path, _valid_sources())
        reg = SourceRegistry(config_path=str(cfg))
        with pytest.raises(ValueError, match="[Dd]uplicate"):
            reg.register_source({"name": "arxiv", "module": "ingest_arxiv", "conflict": "skip"})


# ---------------------------------------------------------------------------
# 6. Enabled/disabled filtering
# ---------------------------------------------------------------------------


class TestEnabledFiltering:
    def test_list_sources_enabled_only(self, tmp_path):
        """list_sources(enabled_only=True) should exclude disabled sources."""
        cfg = _write_yaml(
            tmp_path,
            {
                "sources": {
                    "arxiv": {
                        "module": "ingest_arxiv",
                        "schedule": "0 3 * * 1",
                        "conflict": "skip",
                        "enabled": True,
                    },
                    "rss": {
                        "module": "ingest_rss",
                        "schedule": None,
                        "conflict": "update",
                        "enabled": False,
                    },
                }
            },
        )
        reg = SourceRegistry(config_path=str(cfg))
        enabled = reg.list_sources(enabled_only=True)
        names = [s.name for s in enabled]
        assert "arxiv" in names
        assert "rss" not in names

    def test_list_sources_all_includes_disabled(self, tmp_path):
        """list_sources(enabled_only=False) should include disabled sources."""
        cfg = _write_yaml(
            tmp_path,
            {
                "sources": {
                    "arxiv": {
                        "module": "ingest_arxiv",
                        "schedule": "0 3 * * 1",
                        "conflict": "skip",
                        "enabled": True,
                    },
                    "rss": {
                        "module": "ingest_rss",
                        "schedule": None,
                        "conflict": "update",
                        "enabled": False,
                    },
                }
            },
        )
        reg = SourceRegistry(config_path=str(cfg))
        all_sources = reg.list_sources(enabled_only=False)
        names = [s.name for s in all_sources]
        assert "arxiv" in names
        assert "rss" in names


# ---------------------------------------------------------------------------
# 7. Flexible entry point (main_async / main)
# ---------------------------------------------------------------------------


class TestEntryPoint:
    def test_wikipedia_has_main_not_main_async(self):
        """ingest_wikipedia.py has main() but not main_async() — should still resolve."""
        reg = SourceRegistry(config_path=None)
        src = reg.get_source("wikipedia")
        assert src.module_ref is not None
        assert callable(getattr(src.module_ref, "main", None))

    def test_arxiv_has_main_async(self):
        """ingest_arxiv.py has main_async — should resolve to that."""
        reg = SourceRegistry(config_path=None)
        src = reg.get_source("arxiv")
        assert src.module_ref is not None
        assert callable(getattr(src.module_ref, "main_async", None))


# ---------------------------------------------------------------------------
# 8. SourceEntry dataclass
# ---------------------------------------------------------------------------


class TestSourceEntryDataclass:
    def test_has_required_fields(self):
        """SourceEntry dataclass has all required fields."""
        field_names = {f.name for f in fields(SourceEntry)}
        required = {
            "name", "module_path", "schedule", "conflict_strategy",
            "pipeline", "enabled", "_module_ref",
        }
        assert required.issubset(field_names), f"Missing fields: {required - field_names}"

    def test_get_source_returns_source_entry(self):
        """get_source returns a SourceEntry instance."""
        reg = SourceRegistry(config_path=None)
        src = reg.get_source("arxiv")
        assert isinstance(src, SourceEntry)
        assert src.name == "arxiv"
        assert src.module_path == "ingest_arxiv"


# ---------------------------------------------------------------------------
# 9. Programmatic registration
# ---------------------------------------------------------------------------


class TestProgrammaticRegistration:
    def test_register_source_programmatically(self, tmp_path):
        """register_source allows adding a source at runtime."""
        cfg = _write_yaml(tmp_path, {"sources": {}})
        reg = SourceRegistry(config_path=str(cfg))
        reg.register_source({
            "name": "custom_source",
            "module": "ingest_arxiv",
            "conflict": "skip",
            "schedule": "0 0 * * *",
            "enabled": True,
        })
        src = reg.get_source("custom_source")
        assert src.name == "custom_source"

    def test_register_invalid_source_raises(self, tmp_path):
        """register_source with missing required fields raises."""
        cfg = _write_yaml(tmp_path, {"sources": {}})
        reg = SourceRegistry(config_path=str(cfg))
        with pytest.raises(ValueError):
            reg.register_source({"name": "bad"})  # missing module, conflict