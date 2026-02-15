"""
Tests for MCP tools that invoke OpenSCAD as a subprocess.

Covers: check_openscad, export_model, validate_scad, analyze_model, get_libraries.
"""

import os
import subprocess
import struct
import pytest
from pathlib import Path
from unittest.mock import patch, Mock, AsyncMock, MagicMock

from openscad_mcp.server import (
    check_openscad,
    export_model,
    validate_scad,
    analyze_model,
    get_libraries,
    SUPPORTED_EXPORT_FORMATS,
)
from openscad_mcp.utils.config import Config, CacheConfig, SecurityConfig, set_config


# ---------------------------------------------------------------------------
# Unwrap MCP tool functions
# ---------------------------------------------------------------------------

check_openscad_fn = check_openscad.fn if hasattr(check_openscad, "fn") else check_openscad
export_model_fn = export_model.fn if hasattr(export_model, "fn") else export_model
validate_scad_fn = validate_scad.fn if hasattr(validate_scad, "fn") else validate_scad
analyze_model_fn = analyze_model.fn if hasattr(analyze_model, "fn") else analyze_model
get_libraries_fn = get_libraries.fn if hasattr(get_libraries, "fn") else get_libraries


# ============================================================================
# TestCheckOpenscad
# ============================================================================


class TestCheckOpenscad:
    """Tests for the check_openscad MCP tool."""

    async def test_found(self, configured_env):
        """When OpenSCAD is found, installed should be True with a version string."""
        with patch(
            "openscad_mcp.server.subprocess.run",
            return_value=Mock(
                returncode=0,
                stdout="OpenSCAD version 2021.01",
                stderr="",
            ),
        ):
            result = await check_openscad_fn()

        assert result["installed"] is True
        assert "2021.01" in result["version"]
        assert result["path"] == "/usr/bin/openscad"

    async def test_not_found(self, configured_env, monkeypatch):
        """When OpenSCAD is not found, installed should be False."""
        monkeypatch.setattr(
            "openscad_mcp.server.find_openscad", lambda: None
        )

        result = await check_openscad_fn()

        assert result["installed"] is False
        assert result["version"] is None
        assert result["path"] is None

    async def test_version_from_stderr(self, configured_env):
        """Version should be extracted from stderr when stdout is empty."""
        with patch(
            "openscad_mcp.server.subprocess.run",
            return_value=Mock(
                returncode=0,
                stdout="",
                stderr="OpenSCAD version 2023.05",
            ),
        ):
            result = await check_openscad_fn()

        assert result["installed"] is True
        assert "2023.05" in result["version"]

    async def test_exception_handling(self, configured_env):
        """When subprocess.run raises an exception, version should be 'Unknown'."""
        with patch(
            "openscad_mcp.server.subprocess.run",
            side_effect=Exception("Permission denied"),
        ):
            result = await check_openscad_fn()

        assert result["installed"] is True
        assert result["version"] == "Unknown"

    async def test_searched_paths(self, configured_env, monkeypatch):
        """With include_paths=True and OpenSCAD not found, searched_paths should be returned."""
        monkeypatch.setattr(
            "openscad_mcp.server.find_openscad", lambda: None
        )

        result = await check_openscad_fn(include_paths=True)

        assert result["installed"] is False
        assert result["searched_paths"] is not None
        assert isinstance(result["searched_paths"], list)
        assert len(result["searched_paths"]) > 0

    async def test_ctx_logging(self, configured_env, mock_context):
        """When ctx is provided, ctx.info should be called."""
        with patch(
            "openscad_mcp.server.subprocess.run",
            return_value=Mock(
                returncode=0,
                stdout="OpenSCAD version 2021.01",
                stderr="",
            ),
        ):
            result = await check_openscad_fn(ctx=mock_context)

        assert result["installed"] is True
        assert mock_context.info.called


# ============================================================================
# TestExportModel
# ============================================================================


class TestExportModel:
    """Tests for the export_model MCP tool."""

    async def test_stl_export_success(
        self, configured_env, mock_subprocess_success
    ):
        """STL export with scad_content should succeed and produce an output file."""
        mock_run = mock_subprocess_success()
        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await export_model_fn(scad_content="cube(10);")

        assert result["success"] is True
        assert result["format"] == "stl"
        assert Path(result["output_path"]).exists()
        assert result["file_size_bytes"] > 0

    @pytest.mark.parametrize("fmt", sorted(SUPPORTED_EXPORT_FORMATS))
    async def test_all_formats(
        self, configured_env, mock_subprocess_success, fmt
    ):
        """All supported export formats should succeed."""
        mock_run = mock_subprocess_success()
        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await export_model_fn(
                scad_content="cube(10);", output_format=fmt
            )

        assert result["success"] is True
        assert result["format"] == fmt

    async def test_unsupported_format(self, configured_env):
        """An unsupported format should return an error."""
        result = await export_model_fn(
            scad_content="cube(10);", output_format="obj"
        )

        assert result["success"] is False
        assert "unsupported" in result["error"].lower() or "Unsupported" in result["error"]

    async def test_custom_output_path(
        self, configured_env, mock_subprocess_success
    ):
        """A custom output_path should be respected."""
        tmp_path = configured_env[0]
        custom_path = str(tmp_path / "custom_output.stl")

        mock_run = mock_subprocess_success()
        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await export_model_fn(
                scad_content="cube(10);", output_path=custom_path
            )

        assert result["success"] is True
        assert result["output_path"] == custom_path
        assert Path(custom_path).exists()

    async def test_both_inputs_error(self, configured_env):
        """Providing both scad_content and scad_file should return an error."""
        result = await export_model_fn(
            scad_content="cube(10);", scad_file="/some/file.scad"
        )

        assert result["success"] is False
        assert "exactly one" in result["error"].lower() or "Exactly one" in result["error"]

    async def test_no_inputs_error(self, configured_env):
        """Providing neither scad_content nor scad_file should return an error."""
        result = await export_model_fn()

        assert result["success"] is False
        assert "exactly one" in result["error"].lower() or "Exactly one" in result["error"]

    async def test_file_not_found(self, configured_env):
        """A nonexistent scad_file should return a file-not-found error."""
        result = await export_model_fn(scad_file="/nonexistent/model.scad")

        assert result["success"] is False
        assert "not found" in result["error"].lower() or "not within allowed" in result["error"].lower()

    async def test_subprocess_failure(self, configured_env):
        """When OpenSCAD returns a nonzero exit code, export should fail."""
        def mock_run(cmd, **kwargs):
            result = Mock()
            result.returncode = 1
            result.stderr = "ERROR: Parse error in cube()"
            result.stdout = ""
            # Still create the output so the function doesn't fail on missing file first
            return result

        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await export_model_fn(scad_content="cube(")

        assert result["success"] is False
        assert "failed" in result["error"].lower() or "ERROR" in result["error"]

    async def test_no_output_file(self, configured_env):
        """When OpenSCAD returns 0 but creates no output file, an error should be reported."""
        def mock_run_no_output(cmd, **kwargs):
            result = Mock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""
            return result

        with patch(
            "openscad_mcp.server.subprocess.run",
            side_effect=mock_run_no_output,
        ):
            result = await export_model_fn(scad_content="cube(10);")

        assert result["success"] is False
        assert "did not produce" in result["error"].lower() or "output" in result["error"].lower()

    async def test_timeout(self, configured_env):
        """When OpenSCAD times out, a timeout error should be reported."""
        def mock_run_timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=300)

        with patch(
            "openscad_mcp.server.subprocess.run",
            side_effect=mock_run_timeout,
        ):
            result = await export_model_fn(scad_content="cube(10);")

        assert result["success"] is False
        assert "timed out" in result["error"].lower() or "timeout" in result["error"].lower()

    async def test_security_allowed_paths(self, configured_env):
        """scad_file outside allowed_paths should be rejected."""
        tmp_path, cfg = configured_env
        cfg.security.allowed_paths = ["/allowed"]
        set_config(cfg)

        result = await export_model_fn(scad_file="/forbidden/model.scad")

        assert result["success"] is False
        assert "allowed paths" in result["error"].lower() or "not within" in result["error"].lower()

    async def test_security_content_size(self, configured_env, monkeypatch):
        """Content exceeding max_file_size_mb should be rejected."""
        tmp_path, _ = configured_env
        cfg = Config(
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
            security=SecurityConfig(max_file_size_mb=1, allowed_paths=None),
        )
        set_config(cfg)
        monkeypatch.setattr(
            "openscad_mcp.server.find_openscad", lambda: "/usr/bin/openscad"
        )

        large_content = "x" * (2 * 1024 * 1024)  # 2 MB
        result = await export_model_fn(scad_content=large_content)

        assert result["success"] is False
        assert "size" in result["error"].lower() or "exceeds" in result["error"].lower()

    async def test_security_variable_name(self, configured_env):
        """Variable names with invalid characters should be rejected."""
        result = await export_model_fn(
            scad_content="cube(10);",
            variables={"bad;name": 1},
        )

        assert result["success"] is False
        assert "variable name" in result["error"].lower() or "Invalid" in result["error"]

    async def test_temp_cleanup(self, configured_env, mock_subprocess_success):
        """The temporary input .scad file should be cleaned up after export."""
        tmp_path = configured_env[0]
        mock_run = mock_subprocess_success()

        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await export_model_fn(scad_content="cube(10);")

        assert result["success"] is True

        # No leftover input_*.scad files in temp_dir
        leftover = list(tmp_path.glob("input_*.scad"))
        assert len(leftover) == 0, f"Temp input files not cleaned up: {leftover}"


# ============================================================================
# TestValidateScad
# ============================================================================


class TestValidateScad:
    """Tests for the validate_scad MCP tool."""

    async def test_valid_code(self, configured_env, mock_subprocess_success):
        """Valid SCAD code should return valid=True with no errors."""
        mock_run = mock_subprocess_success()
        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await validate_scad_fn(scad_content="cube(10);")

        assert result["success"] is True
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    async def test_errors(self, configured_env):
        """When OpenSCAD reports errors, valid should be False and errors populated."""
        def mock_run(cmd, **kwargs):
            result = Mock()
            result.returncode = 1
            result.stderr = "ERROR: syntax error in line 1\nERROR: missing semicolon"
            result.stdout = ""
            return result

        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await validate_scad_fn(scad_content="cube(")

        assert result["success"] is True
        assert result["valid"] is False
        assert len(result["errors"]) >= 1

    async def test_warnings_and_echo(self, configured_env):
        """Warnings and ECHO output should be captured and categorized."""
        def mock_run(cmd, **kwargs):
            result = Mock()
            result.returncode = 0
            result.stderr = (
                "WARNING: Undefined variable 'x'\n"
                "ECHO: hello world\n"
            )
            result.stdout = ""
            return result

        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await validate_scad_fn(scad_content="echo(\"hello\");")

        assert result["success"] is True
        assert result["valid"] is True
        assert len(result["warnings"]) >= 1
        assert len(result["echo_output"]) >= 1
        assert "hello world" in result["echo_output"][0]

    async def test_deprecated(self, configured_env):
        """DEPRECATED messages should be captured."""
        def mock_run(cmd, **kwargs):
            result = Mock()
            result.returncode = 0
            result.stderr = "DEPRECATED: old function usage"
            result.stdout = ""
            return result

        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await validate_scad_fn(scad_content="cube(10);")

        assert result["success"] is True
        assert len(result["deprecated"]) >= 1
        assert "old" in result["deprecated"][0].lower()

    async def test_both_inputs_error(self, configured_env):
        """Providing both scad_content and scad_file should return an error."""
        result = await validate_scad_fn(
            scad_content="cube(10);", scad_file="/some/file.scad"
        )

        assert result["success"] is False
        assert "exactly one" in result["error"].lower() or "Exactly one" in result["error"]

    async def test_no_inputs_error(self, configured_env):
        """Providing neither scad_content nor scad_file should return an error."""
        result = await validate_scad_fn()

        assert result["success"] is False
        assert "exactly one" in result["error"].lower() or "Exactly one" in result["error"]

    async def test_file_not_found(self, configured_env):
        """A nonexistent scad_file should return a file-not-found error."""
        result = await validate_scad_fn(scad_file="/nonexistent/model.scad")

        assert result["success"] is False
        assert "not found" in result["error"].lower() or "not within" in result["error"].lower()

    async def test_timeout(self, configured_env):
        """When OpenSCAD times out during validation, a timeout error should be reported."""
        def mock_run_timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=300)

        with patch(
            "openscad_mcp.server.subprocess.run",
            side_effect=mock_run_timeout,
        ):
            result = await validate_scad_fn(scad_content="cube(10);")

        assert result["success"] is False
        assert "timed out" in result["error"].lower() or "timeout" in result["error"].lower()

    async def test_security_allowed_paths(self, configured_env):
        """scad_file outside allowed_paths should be rejected."""
        tmp_path, cfg = configured_env
        cfg.security.allowed_paths = ["/allowed"]
        set_config(cfg)

        result = await validate_scad_fn(scad_file="/forbidden/model.scad")

        assert result["success"] is False
        assert "allowed paths" in result["error"].lower() or "not within" in result["error"].lower()

    async def test_dev_null_output(self, configured_env, mock_subprocess_success):
        """The validate command should use /dev/null (or NUL on Windows) as output."""
        captured_cmds = []

        def capturing_mock(cmd, **kwargs):
            captured_cmds.append(cmd)
            result = Mock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""
            return result

        with patch(
            "openscad_mcp.server.subprocess.run",
            side_effect=capturing_mock,
        ):
            await validate_scad_fn(scad_content="cube(10);")

        assert len(captured_cmds) == 1
        cmd = captured_cmds[0]
        assert "-o" in cmd
        o_idx = cmd.index("-o")
        output_target = cmd[o_idx + 1]
        assert output_target in ("/dev/null", "NUL")

    async def test_hardwarnings_flag(self, configured_env, mock_subprocess_success):
        """The validate command should include --hardwarnings flag."""
        captured_cmds = []

        def capturing_mock(cmd, **kwargs):
            captured_cmds.append(cmd)
            result = Mock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""
            return result

        with patch(
            "openscad_mcp.server.subprocess.run",
            side_effect=capturing_mock,
        ):
            await validate_scad_fn(scad_content="cube(10);")

        assert len(captured_cmds) == 1
        assert "--hardwarnings" in captured_cmds[0]


# ============================================================================
# TestAnalyzeModel
# ============================================================================


class TestAnalyzeModel:
    """Tests for the analyze_model MCP tool."""

    async def test_ascii_stl_analysis(
        self, configured_env, mock_subprocess_success
    ):
        """Analysis of an ASCII STL should return bounding box, dimensions, and triangle count."""
        mock_run = mock_subprocess_success()
        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await analyze_model_fn(scad_content="cube(10);")

        assert result["success"] is True
        assert "bounding_box" in result
        assert "dimensions" in result
        assert "triangle_count" in result
        assert result["triangle_count"] >= 1

    async def test_binary_stl_analysis(
        self, configured_env, binary_stl_content
    ):
        """Analysis of a binary STL should return correct vertex data."""
        def mock_run(cmd, **kwargs):
            if "-o" in cmd:
                idx = cmd.index("-o")
                out_path = Path(cmd[idx + 1])
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(binary_stl_content)
            result = Mock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""
            return result

        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await analyze_model_fn(scad_content="cube(10);")

        assert result["success"] is True
        assert result["triangle_count"] == 1
        # Vertices are (0,0,0), (10,0,0), (10,10,5)
        assert result["bounding_box"]["min"] == [0.0, 0.0, 0.0]
        assert result["bounding_box"]["max"] == [10.0, 10.0, 5.0]

    async def test_empty_model(self, configured_env):
        """An STL with no vertices should return an error about an empty model."""
        def mock_run(cmd, **kwargs):
            if "-o" in cmd:
                idx = cmd.index("-o")
                out_path = Path(cmd[idx + 1])
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text("solid empty\nendsolid empty\n")
            result = Mock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""
            return result

        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await analyze_model_fn(scad_content="// empty model")

        assert result["success"] is False
        assert "empty" in result["error"].lower()

    async def test_subprocess_failure(self, configured_env):
        """When OpenSCAD returns a nonzero exit code, analysis should fail."""
        def mock_run(cmd, **kwargs):
            result = Mock()
            result.returncode = 1
            result.stderr = "ERROR: compile failed"
            result.stdout = ""
            return result

        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await analyze_model_fn(scad_content="cube(")

        assert result["success"] is False
        assert "failed" in result["error"].lower() or "ERROR" in result["error"]

    async def test_both_inputs_error(self, configured_env):
        """Providing both scad_content and scad_file should return an error."""
        result = await analyze_model_fn(
            scad_content="cube(10);", scad_file="/some/file.scad"
        )

        assert result["success"] is False
        assert "exactly one" in result["error"].lower() or "Exactly one" in result["error"]

    async def test_file_not_found(self, configured_env):
        """A nonexistent scad_file should return a file-not-found error."""
        result = await analyze_model_fn(scad_file="/nonexistent/model.scad")

        assert result["success"] is False
        assert "not found" in result["error"].lower() or "not within" in result["error"].lower()

    async def test_temp_stl_cleanup(
        self, configured_env, mock_subprocess_success
    ):
        """After successful analysis, no leftover .stl files should remain in temp dir."""
        tmp_path = configured_env[0]
        mock_run = mock_subprocess_success()

        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await analyze_model_fn(scad_content="cube(10);")

        assert result["success"] is True
        leftover_stl = list(tmp_path.glob("analyze_*.stl"))
        assert len(leftover_stl) == 0, f"Temp STL files not cleaned up: {leftover_stl}"

    async def test_dimensions_correct(
        self, configured_env, mock_subprocess_success
    ):
        """Dimensions should match the known mock STL vertices (0,0,0)-(10,10,5)."""
        mock_run = mock_subprocess_success()
        with patch("openscad_mcp.server.subprocess.run", side_effect=mock_run):
            result = await analyze_model_fn(scad_content="cube(10);")

        assert result["success"] is True
        dims = result["dimensions"]
        assert dims["width"] == pytest.approx(10.0)
        assert dims["height"] == pytest.approx(10.0)
        assert dims["depth"] == pytest.approx(5.0)


# ============================================================================
# TestGetLibraries
# ============================================================================


class TestGetLibraries:
    """Tests for the get_libraries MCP tool."""

    async def test_no_libraries(self, configured_env, tmp_path, monkeypatch):
        """When no library directories exist, libraries list should be empty."""
        monkeypatch.delenv("OPENSCADPATH", raising=False)

        # Point Path.home() to a temp dir so platform-specific paths don't exist
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        # Also ensure /usr/share/openscad/libraries etc. are not found
        # by patching platform.system to return a platform with no real libs
        monkeypatch.setattr("openscad_mcp.server.platform.system", lambda: "FreeBSD")

        result = await get_libraries_fn()

        assert result["success"] is True
        assert result["libraries"] == []

    async def test_library_discovery(
        self, configured_env, tmp_path, monkeypatch
    ):
        """Libraries in a directory pointed to by OPENSCADPATH should be discovered."""
        monkeypatch.delenv("OPENSCADPATH", raising=False)

        lib_base = tmp_path / "libs"
        lib_base.mkdir()
        lib_dir = lib_base / "BOSL2"
        lib_dir.mkdir()
        (lib_dir / "std.scad").write_text("// BOSL2 standard library")
        (lib_dir / "shapes.scad").write_text("// shapes module")

        monkeypatch.setenv("OPENSCADPATH", str(lib_base))

        result = await get_libraries_fn()

        assert result["success"] is True
        assert len(result["libraries"]) >= 1
        names = [lib["name"] for lib in result["libraries"]]
        assert "BOSL2" in names

    async def test_openscadpath_env(
        self, configured_env, tmp_path, monkeypatch
    ):
        """OPENSCADPATH environment variable should be searched for libraries."""
        monkeypatch.delenv("OPENSCADPATH", raising=False)

        lib_base = tmp_path / "env_libs"
        lib_base.mkdir()
        my_lib = lib_base / "mylib"
        my_lib.mkdir()
        (my_lib / "utils.scad").write_text("// utils")

        monkeypatch.setenv("OPENSCADPATH", str(lib_base))

        result = await get_libraries_fn()

        assert result["success"] is True
        assert str(lib_base) in result["library_paths"]
        names = [lib["name"] for lib in result["libraries"]]
        assert "mylib" in names

    async def test_readme_detection(
        self, configured_env, tmp_path, monkeypatch
    ):
        """Libraries with a README.md should have has_readme=True."""
        monkeypatch.delenv("OPENSCADPATH", raising=False)

        lib_base = tmp_path / "readme_libs"
        lib_base.mkdir()
        lib_dir = lib_base / "documented_lib"
        lib_dir.mkdir()
        (lib_dir / "main.scad").write_text("// main")
        (lib_dir / "README.md").write_text("# Documented Library")

        monkeypatch.setenv("OPENSCADPATH", str(lib_base))

        result = await get_libraries_fn()

        assert result["success"] is True
        lib = next(
            l for l in result["libraries"] if l["name"] == "documented_lib"
        )
        assert lib["has_readme"] is True

    async def test_main_file_detection(
        self, configured_env, tmp_path, monkeypatch
    ):
        """Libraries with std.scad should report it in main_files."""
        monkeypatch.delenv("OPENSCADPATH", raising=False)

        lib_base = tmp_path / "main_libs"
        lib_base.mkdir()
        lib_dir = lib_base / "stdlib"
        lib_dir.mkdir()
        (lib_dir / "std.scad").write_text("// standard entry")
        (lib_dir / "helpers.scad").write_text("// helpers")

        monkeypatch.setenv("OPENSCADPATH", str(lib_base))

        result = await get_libraries_fn()

        assert result["success"] is True
        lib = next(
            l for l in result["libraries"] if l["name"] == "stdlib"
        )
        assert "std.scad" in lib["main_files"]
