"""
Tests for project management MCP tools: _extract_scad_dependencies,
get_project_files, and clear_cache.

Covers dependency parsing, recursive file discovery, security validation,
cache clearing, and error handling.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, Mock, AsyncMock

from openscad_mcp.server import (
    _extract_scad_dependencies,
    get_project_files,
    clear_cache,
)
from openscad_mcp.utils.config import Config, CacheConfig, SecurityConfig, set_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unwrap(tool):
    """Return the underlying async function from a FastMCP FunctionTool."""
    return tool.fn if hasattr(tool, "fn") else tool


# ============================================================================
# TestExtractScadDependencies
# ============================================================================


class TestExtractScadDependencies:
    """Tests for the _extract_scad_dependencies helper."""

    def test_include_statement(self, tmp_path):
        """Parses a simple include statement."""
        f = tmp_path / "main.scad"
        f.write_text("include <lib/utils.scad>\ncube(10);\n")
        deps = _extract_scad_dependencies(f)
        assert deps == ["lib/utils.scad"]

    def test_use_statement(self, tmp_path):
        """Parses a use statement with trailing semicolon."""
        f = tmp_path / "main.scad"
        f.write_text("use <lib/shapes.scad>;\ncube(10);\n")
        deps = _extract_scad_dependencies(f)
        assert deps == ["lib/shapes.scad"]

    def test_commented_include_ignored(self, tmp_path):
        """Line-commented include statements are not extracted."""
        f = tmp_path / "main.scad"
        f.write_text("// include <lib/commented.scad>\ncube(10);\n")
        deps = _extract_scad_dependencies(f)
        assert deps == []

    def test_no_dependencies(self, tmp_path):
        """File without include/use returns empty list."""
        f = tmp_path / "main.scad"
        f.write_text("cube(10);\n")
        deps = _extract_scad_dependencies(f)
        assert deps == []

    def test_oserror_safety(self, tmp_path):
        """Non-existent file returns empty list without raising."""
        deps = _extract_scad_dependencies(tmp_path / "nonexistent.scad")
        assert deps == []

    def test_semicolons_handled(self, tmp_path):
        """Trailing semicolons in include/use are allowed by regex."""
        f = tmp_path / "main.scad"
        f.write_text("use <lib/foo.scad>;\n")
        deps = _extract_scad_dependencies(f)
        assert deps == ["lib/foo.scad"]

    def test_whitespace_around_path(self, tmp_path):
        """Whitespace around the path inside angle brackets is stripped."""
        f = tmp_path / "main.scad"
        f.write_text("include < lib/spaced.scad >\n")
        deps = _extract_scad_dependencies(f)
        assert deps == ["lib/spaced.scad"]

    def test_multiple_deps(self, tmp_path):
        """Multiple include and use statements are all extracted."""
        f = tmp_path / "main.scad"
        f.write_text(
            "include <lib/utils.scad>\n"
            "use <lib/shapes.scad>;\n"
            "cube(10);\n"
        )
        deps = _extract_scad_dependencies(f)
        assert deps == ["lib/utils.scad", "lib/shapes.scad"]


# ============================================================================
# TestGetProjectFiles
# ============================================================================


class TestGetProjectFiles:
    """Tests for the get_project_files MCP tool."""

    @pytest.fixture(autouse=True)
    def _setup(self, configured_env):
        self.tmp_path, self.cfg = configured_env
        self.fn = _unwrap(get_project_files)

    async def test_recursive_find(self):
        """Finds .scad files in nested directory structures."""
        project = self.tmp_path / "project"
        project.mkdir()
        (project / "main.scad").write_text("cube(10);")
        sub = project / "lib"
        sub.mkdir()
        (sub / "utils.scad").write_text("module u() {}")

        result = await self.fn(project_dir=str(project))

        assert result["success"] is True
        names = [f["name"] for f in result["files"]]
        assert "main.scad" in names
        assert "utils.scad" in names
        assert len(result["files"]) == 2

    async def test_dependency_extraction(self):
        """Dependencies are populated for files with include/use."""
        project = self.tmp_path / "project"
        project.mkdir()
        (project / "main.scad").write_text(
            "include <lib/utils.scad>\ncube(10);\n"
        )

        result = await self.fn(project_dir=str(project))

        assert result["success"] is True
        assert len(result["dependencies"]) > 0
        assert "lib/utils.scad" in result["dependencies"]["main.scad"]

    async def test_nonexistent_dir(self):
        """Non-existent directory returns error."""
        result = await self.fn(
            project_dir=str(self.tmp_path / "nonexistent")
        )

        assert result["success"] is False
        assert "not found" in result["error"]

    async def test_not_a_directory(self):
        """Passing a file path returns error about not a directory."""
        f = self.tmp_path / "file.scad"
        f.write_text("cube(1);")

        result = await self.fn(project_dir=str(f))

        assert result["success"] is False
        assert "not a directory" in result["error"]

    async def test_security_validation(self):
        """Directory outside allowed_paths is rejected."""
        cfg = Config(
            temp_dir=self.tmp_path,
            cache=CacheConfig(enabled=False, directory=self.tmp_path / "cache"),
            security=SecurityConfig(allowed_paths=[str(self.tmp_path / "allowed")]),
        )
        set_config(cfg)

        outside = self.tmp_path / "outside"
        outside.mkdir()
        (outside / "model.scad").write_text("cube(1);")

        result = await self.fn(project_dir=str(outside))

        assert result["success"] is False
        assert "allowed paths" in result["error"]

    async def test_empty_dir(self):
        """Empty directory returns empty lists."""
        project = self.tmp_path / "empty_project"
        project.mkdir()

        result = await self.fn(project_dir=str(project))

        assert result["success"] is True
        assert result["files"] == []
        assert result["dependencies"] == {}


# ============================================================================
# TestClearCache
# ============================================================================


class TestClearCache:
    """Tests for the clear_cache MCP tool."""

    @pytest.fixture(autouse=True)
    def _setup(self, configured_env_with_cache):
        self.tmp_path, self.cfg, self.cache_dir = configured_env_with_cache
        self.fn = _unwrap(clear_cache)

    async def test_clears_pngs(self):
        """PNG files in cache directory are deleted."""
        (self.cache_dir / "abc123.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        (self.cache_dir / "def456.png").write_bytes(b"\x89PNG" + b"\x00" * 200)

        result = await self.fn()

        assert result["success"] is True
        assert result["cleared_files"] == 2
        assert result["freed_bytes"] > 0
        assert not list(self.cache_dir.glob("*.png"))

    async def test_nonexistent_dir_noop(self):
        """Non-existent cache directory returns zero counts."""
        import shutil
        shutil.rmtree(self.cache_dir)

        result = await self.fn()

        assert result["success"] is True
        assert result["cleared_files"] == 0
        assert result["freed_bytes"] == 0

    async def test_ignores_non_png(self):
        """Non-PNG files in cache directory are not deleted."""
        txt = self.cache_dir / "notes.txt"
        txt.write_text("keep me")

        result = await self.fn()

        assert result["success"] is True
        assert result["cleared_files"] == 0
        assert txt.exists()

    async def test_oserror_handling(self):
        """OSError during unlink is logged but doesn't crash."""
        png = self.cache_dir / "bad.png"
        png.write_bytes(b"\x89PNG" + b"\x00" * 50)

        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            result = await self.fn()

        # Should still return success (errors are logged, not raised)
        assert result["success"] is True
        # No files were actually cleared because unlink raised
        assert result["cleared_files"] == 0

    async def test_ctx_logging(self, mock_context):
        """Context info method is called during cache clear."""
        result = await self.fn(ctx=mock_context)

        assert result["success"] is True
        mock_context.info.assert_called()
