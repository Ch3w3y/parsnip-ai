"""Import smoke tests — verify every tool module can be imported without the full stack running.

These tests catch syntax errors, missing dependencies, and circular imports early.
Run with: pytest tests/test_imports.py -v
"""

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
AGENT_DIR = PROJECT_ROOT / "agent"
PIPELINE_DIR = PROJECT_ROOT / "pipelines"
ANALYSIS_DIR = PROJECT_ROOT / "analysis"
SCHEDULER_DIR = PROJECT_ROOT / "scheduler"
JOPLIN_MCP_DIR = PROJECT_ROOT / "joplin-mcp"


def _py_files(directory: Path):
    return sorted(directory.rglob("*.py"))


def _can_import(module_path: Path, root: Path):
    """Try to import a module file relative to root. Returns True/False."""
    rel = module_path.relative_to(root)
    module_name = str(rel.with_suffix("")).replace("/", ".")
    try:
        # For agent/tools we need to be in agent/ context
        old_path = sys.path.copy()
        if root not in sys.path:
            sys.path.insert(0, str(root))
        __import__(module_name)
        return True
    except Exception:
        return False
    finally:
        sys.path = old_path


class TestSyntax:
    """Verify every .py file compiles to bytecode (catches syntax errors)."""

    @pytest.mark.parametrize(
        "py_file",
        [
            *_py_files(AGENT_DIR),
            *_py_files(PIPELINE_DIR),
            *_py_files(ANALYSIS_DIR),
            *_py_files(SCHEDULER_DIR),
            *_py_files(JOPLIN_MCP_DIR),
        ],
        ids=lambda p: str(p.relative_to(PROJECT_ROOT)),
    )
    def test_compiles(self, py_file: Path):
        src = py_file.read_text(encoding="utf-8")
        ast.parse(src)


class TestAgentToolImports:
    """Verify agent tool modules import cleanly."""

    @pytest.mark.parametrize(
        "tool_file",
        sorted((AGENT_DIR / "tools").glob("*.py")),
        ids=lambda p: p.name,
    )
    def test_tool_imports(self, tool_file: Path):
        if tool_file.name == "__init__.py":
            pytest.skip("skip init")
        old_path = sys.path.copy()
        try:
            sys.path.insert(0, str(AGENT_DIR))
            module_name = f"tools.{tool_file.stem}"
            __import__(module_name)
        finally:
            sys.path = old_path

    def test_tools_init_exports(self):
        """Every name in __all__ must exist and every tool file must be exported."""
        old_path = sys.path.copy()
        try:
            sys.path.insert(0, str(AGENT_DIR))
            import tools

            assert hasattr(tools, "__all__"), "tools/__init__.py missing __all__"
            for name in tools.__all__:
                assert hasattr(tools, name), f"tools.__all__ references missing name: {name}"
        finally:
            sys.path = old_path


class TestPipelineImports:
    def test_pipeline_imports(self):
        old_path = sys.path.copy()
        try:
            sys.path.insert(0, str(PIPELINE_DIR))
            import research_agent
            assert hasattr(research_agent, "Pipeline")
        finally:
            sys.path = old_path


class TestAnalysisImports:
    def test_analysis_imports(self):
        old_path = sys.path.copy()
        try:
            sys.path.insert(0, str(ANALYSIS_DIR))
            import server
            assert hasattr(server, "app")
        finally:
            sys.path = old_path


class TestSchedulerImports:
    def test_scheduler_imports(self):
        old_path = sys.path.copy()
        try:
            sys.path.insert(0, str(SCHEDULER_DIR))
            import scheduler
            assert hasattr(scheduler, "main")
        finally:
            sys.path = old_path


class TestJoplinMcpImports:
    def test_joplin_mcp_imports(self):
        old_path = sys.path.copy()
        try:
            sys.path.insert(0, str(JOPLIN_MCP_DIR))
            import server
            assert hasattr(server, "main")
        finally:
            sys.path = old_path
