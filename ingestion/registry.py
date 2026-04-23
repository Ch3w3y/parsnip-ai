"""
Ingestion plugin registry — declarative source management for parsnip.

Loads source definitions from sources.yaml, auto-discovers ingest_*.py files
not declared in YAML, validates module existence and entry points, and provides
lookup/filtering services for the scheduler and other consumers.

Usage:
    from ingestion.registry import SourceRegistry
    reg = SourceRegistry()
    for src in reg.list_sources(enabled_only=True):
        print(src.name, src.schedule)
"""

from __future__ import annotations

import importlib
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Default ingestion directory (sibling of this file)
_INGESTION_DIR = Path(__file__).resolve().parent
_DEFAULT_YAML = _INGESTION_DIR / "sources.yaml"


def _ensure_on_sys_path(directory: Path) -> None:
    """Add a directory to sys.path if not already present."""
    dir_str = str(directory)
    if dir_str not in sys.path:
        sys.path.insert(0, dir_str)


@dataclass
class SourceEntry:
    """A single ingestion source definition."""

    name: str
    module_path: str
    schedule: str | None = None
    conflict_strategy: str = "skip"
    pipeline: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    _module_ref: Any = field(default=None, repr=False)

    @property
    def module_ref(self) -> Any:
        """Lazily resolve and return the imported module object."""
        if self._module_ref is None:
            self._module_ref = _import_module(self.module_path)
        return self._module_ref

    def get_entry_point(self) -> Any:
        """Return main_async if available, else main, else raise."""
        mod = self.module_ref
        entry = getattr(mod, "main_async", None) or getattr(mod, "main", None)
        if entry is None:
            raise ValueError(
                f"Module {self.module_path} has no main_async or main entry point"
            )
        return entry


def _import_module(module_name: str) -> Any:
    """Import a module by dotted name, raising ValueError on failure."""
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(
            f"Cannot import module '{module_name}': {exc}"
        ) from exc


def _validate_entry_point(module_name: str) -> None:
    """Verify a module has main_async or main; raise ValueError if not."""
    try:
        mod = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(
            f"Module '{module_name}' does not exist or cannot be imported: {exc}"
        ) from exc
    has_async = hasattr(mod, "main_async")
    has_sync = hasattr(mod, "main")
    if not has_async and not has_sync:
        raise ValueError(
            f"Module '{module_name}' has no entry point (main_async or main)"
        )


def _source_name_from_module(module_name: str) -> str:
    """Derive a registry key from a module name: ingest_arxiv -> arxiv."""
    prefix = "ingest_"
    if module_name.startswith(prefix):
        return module_name[len(prefix):]
    return module_name


class SourceRegistry:
    """
    Declarative plugin registry for ingestion sources.

    Reads sources.yaml for explicit definitions, auto-discovers ingest_*.py
    files not declared in YAML, validates module existence and entry points,
    and provides lookup/filtering.
    """

    def __init__(
        self,
        config_path: str | None = None,
        extra_ingestion_dirs: list[str] | None = None,
    ):
        """
        Initialize the registry.

        Args:
            config_path: Path to sources.yaml. None = use ingestion/sources.yaml.
            extra_ingestion_dirs: Additional directories to scan for ingest_*.py
                                 auto-discovery (used in tests).
        """
        self._sources: dict[str, SourceEntry] = {}
        self._extra_ingestion_dirs = extra_ingestion_dirs or []

        # Ensure ingestion directory is importable
        _ensure_on_sys_path(_INGESTION_DIR)
        # Ensure extra directories are importable and discoverable
        for d in self._extra_ingestion_dirs:
            _ensure_on_sys_path(Path(d))

        yaml_path = Path(config_path) if config_path else _DEFAULT_YAML
        yaml_entries: dict[str, dict] = {}

        # --- Load YAML ---
        if yaml_path.exists():
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}
            yaml_entries = data.get("sources", {})
        else:
            logger.warning(f"sources.yaml not found at {yaml_path}")

        # --- Validate YAML entries ---
        for name, entry in yaml_entries.items():
            self._validate_yaml_entry(name, entry)

        # --- Register YAML entries (validated) ---
        for name, entry in yaml_entries.items():
            se = SourceEntry(
                name=name,
                module_path=entry["module"],
                schedule=entry.get("schedule"),
                conflict_strategy=entry.get("conflict", "skip"),
                pipeline=entry.get("pipeline", {}),
                enabled=entry.get("enabled", True),
            )
            self._sources[name] = se

        # --- Auto-discover ingest_*.py not declared in YAML ---
        declared_modules = {e["module"] for e in yaml_entries.values() if "module" in e}
        discovered = self._discover_ingest_modules()
        for mod_name in discovered:
            if mod_name not in declared_modules:
                name = _source_name_from_module(mod_name)
                if name not in self._sources:
                    try:
                        _validate_entry_point(mod_name)
                        self._sources[name] = SourceEntry(
                            name=name,
                            module_path=mod_name,
                            schedule=None,
                            conflict_strategy="skip",
                            pipeline={},
                            enabled=True,
                        )
                        logger.info(f"Auto-discovered source: {name} ({mod_name})")
                    except ValueError:
                        logger.warning(
                            f"Skipping auto-discovered module {mod_name}: no valid entry point"
                        )

    def _validate_yaml_entry(self, name: str, entry: dict) -> None:
        """Raise ValueError if a YAML entry is missing required fields or has bad refs."""
        if "module" not in entry:
            raise ValueError(
                f"Source '{name}' is missing required field 'module'"
            )
        if "conflict" not in entry:
            raise ValueError(
                f"Source '{name}' is missing required field 'conflict'"
            )
        # Validate the module can be found and has an entry point
        _validate_entry_point(entry["module"])

    def _discover_ingest_modules(self) -> list[str]:
        """Scan ingestion directories for ingest_*.py files and return module names."""
        dirs_to_scan = [_INGESTION_DIR] + [
            Path(d) for d in self._extra_ingestion_dirs
        ]
        modules: set[str] = set()
        for dir_path in dirs_to_scan:
            if not dir_path.is_dir():
                continue
            for py_file in dir_path.glob("ingest_*.py"):
                mod_name = py_file.stem  # e.g. "ingest_arxiv"
                modules.add(mod_name)
        return sorted(modules)

    def get_source(self, name: str) -> SourceEntry:
        """Return a validated SourceEntry by name, or raise KeyError."""
        if name not in self._sources:
            raise KeyError(f"Source '{name}' not found in registry")
        return self._sources[name]

    def list_sources(self, enabled_only: bool = False) -> list[SourceEntry]:
        """Return all sources, optionally filtered to enabled only."""
        sources = list(self._sources.values())
        if enabled_only:
            sources = [s for s in sources if s.enabled]
        return sorted(sources, key=lambda s: s.name)

    def register_source(self, entry: dict) -> None:
        """
        Programmatically register a source at runtime.

        Args:
            entry: dict with at least 'name', 'module', 'conflict'.
                   Optional: 'schedule', 'enabled', 'pipeline'.
        """
        if "name" not in entry:
            raise ValueError("Programmatic registration requires 'name' field")
        if "module" not in entry:
            raise ValueError("Programmatic registration requires 'module' field")
        if "conflict" not in entry:
            raise ValueError("Programmatic registration requires 'conflict' field")

        name = entry["name"]
        if name in self._sources:
            raise ValueError(f"Duplicate source name: '{name}'")

        module_path = entry["module"]
        _validate_entry_point(module_path)

        self._sources[name] = SourceEntry(
            name=name,
            module_path=module_path,
            schedule=entry.get("schedule"),
            conflict_strategy=entry["conflict"],
            pipeline=entry.get("pipeline", {}),
            enabled=entry.get("enabled", True),
        )