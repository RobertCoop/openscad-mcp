"""
Tests for helper functions in openscad_mcp.server.

Covers:
- find_openscad() -- locating the OpenSCAD binary
- Render cache helpers (_compute_render_cache_key, _check_cache,
  _save_to_cache, _evict_cache_if_needed)
- _parse_openscad_stderr() -- categorising OpenSCAD stderr output
- _parse_stl_vertices() -- parsing ASCII and binary STL files
- render_scad_to_png() -- command construction, error handling, security
"""

import base64
import struct
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, Mock

import pytest

from openscad_mcp.server import (
    find_openscad,
    _compute_render_cache_key,
    _check_cache,
    _save_to_cache,
    _evict_cache_if_needed,
    _parse_openscad_stderr,
    _parse_stl_vertices,
    render_scad_to_png,
)
from openscad_mcp.utils.config import (
    Config,
    CacheConfig,
    SecurityConfig,
    set_config,
    get_config,
)


# ============================================================================
# TestFindOpenscad
# ============================================================================


class TestFindOpenscad:
    """Tests for finding the OpenSCAD executable on the system."""

    def test_find_openscad_configured_path(self, tmp_path):
        """Return the configured path when it exists on disk."""
        fake_bin = tmp_path / "openscad"
        fake_bin.touch()

        cfg = Config(
            openscad_path=str(fake_bin),
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
        )
        set_config(cfg)

        result = find_openscad()
        assert result == str(fake_bin)

    def test_find_openscad_from_subprocess(self, tmp_path, monkeypatch):
        """Fall back to subprocess discovery when no configured path."""
        cfg = Config(
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
        )
        set_config(cfg)

        def mock_run(cmd, **kwargs):
            if cmd[0] == "openscad":
                m = Mock()
                m.returncode = 0
                return m
            raise FileNotFoundError

        monkeypatch.setattr("subprocess.run", mock_run)
        assert find_openscad() == "openscad"

    def test_find_openscad_from_common_paths(self, tmp_path, monkeypatch):
        """Fall back to well-known paths when subprocess discovery fails."""
        cfg = Config(
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
        )
        set_config(cfg)

        # All subprocess candidates fail
        monkeypatch.setattr(
            "subprocess.run", Mock(side_effect=FileNotFoundError)
        )

        # Only allow /usr/local/bin/openscad to exist among common paths
        original_exists = Path.exists
        allowed = "/usr/local/bin/openscad"

        common_paths = [
            "/usr/bin/openscad",
            "/usr/local/bin/openscad",
            "/snap/bin/openscad",
            "/var/lib/flatpak/exports/bin/org.openscad.OpenSCAD",
            "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
            "C:\\Program Files\\OpenSCAD\\openscad.exe",
            "C:\\Program Files (x86)\\OpenSCAD\\openscad.exe",
        ]

        def patched_exists(self):
            s = str(self)
            if s in common_paths:
                return s == allowed
            return original_exists(self)

        monkeypatch.setattr(Path, "exists", patched_exists)

        assert find_openscad() == "/usr/local/bin/openscad"

    def test_find_openscad_not_found(self, tmp_path, monkeypatch):
        """Return None when OpenSCAD cannot be located anywhere."""
        cfg = Config(
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
        )
        set_config(cfg)

        monkeypatch.setattr(
            "subprocess.run", Mock(side_effect=FileNotFoundError)
        )

        original_exists = Path.exists

        def patched_exists(self):
            # Block all common paths
            common = [
                "/usr/bin/openscad",
                "/usr/local/bin/openscad",
                "/snap/bin/openscad",
                "/var/lib/flatpak/exports/bin/org.openscad.OpenSCAD",
                "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
                "C:\\Program Files\\OpenSCAD\\openscad.exe",
                "C:\\Program Files (x86)\\OpenSCAD\\openscad.exe",
            ]
            if str(self) in common:
                return False
            return original_exists(self)

        monkeypatch.setattr(Path, "exists", patched_exists)

        assert find_openscad() is None

    def test_find_openscad_configured_path_missing(self, tmp_path, monkeypatch):
        """Return None when the configured path does not exist on disk."""
        cfg = Config(
            openscad_path="/nonexistent/openscad",
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
        )
        set_config(cfg)

        monkeypatch.setattr(
            "subprocess.run", Mock(side_effect=FileNotFoundError)
        )

        original_exists = Path.exists

        def patched_exists(self):
            # Block configured path and all common paths
            if str(self) in (
                "/nonexistent/openscad",
                "/usr/bin/openscad",
                "/usr/local/bin/openscad",
                "/snap/bin/openscad",
                "/var/lib/flatpak/exports/bin/org.openscad.OpenSCAD",
                "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
                "C:\\Program Files\\OpenSCAD\\openscad.exe",
                "C:\\Program Files (x86)\\OpenSCAD\\openscad.exe",
            ):
                return False
            return original_exists(self)

        monkeypatch.setattr(Path, "exists", patched_exists)

        assert find_openscad() is None


# ============================================================================
# TestEvictCacheIfNeeded
# ============================================================================


class TestEvictCacheIfNeeded:
    """Tests for cache eviction logic."""

    def test_evict_noop_under_limit(self, configured_env_with_cache):
        """Small cache files well under the limit are not removed."""
        _tmp_path, _cfg, cache_dir = configured_env_with_cache

        # Create a few small files (total << 100 MB)
        for i in range(3):
            (cache_dir / f"file{i}.png").write_bytes(b"x" * 100)

        _evict_cache_if_needed()

        # All files still present
        assert len(list(cache_dir.glob("*.png"))) == 3

    def test_evict_removes_oldest(self, configured_env_with_cache):
        """Oldest cache files are removed first when over the limit."""
        _tmp_path, cfg, cache_dir = configured_env_with_cache

        # Bypass validator to set a tiny limit
        cfg.cache = CacheConfig.model_construct(
            enabled=True,
            directory=cache_dir,
            max_size_mb=0,
            ttl_hours=24,
        )
        set_config(cfg)

        # Create files with different mtimes
        old_file = cache_dir / "old.png"
        old_file.write_bytes(b"A" * 100)
        import os
        os.utime(old_file, (time.time() - 3600, time.time() - 3600))

        new_file = cache_dir / "new.png"
        new_file.write_bytes(b"B" * 100)

        _evict_cache_if_needed()

        # The oldest file should be removed
        assert not old_file.exists()

    def test_evict_noop_cache_disabled(self, configured_env):
        """Eviction is a no-op when caching is disabled."""
        _tmp_path, _cfg = configured_env
        # Should not raise; no eviction happens
        _evict_cache_if_needed()

    def test_evict_noop_no_cache_dir(self, configured_env_with_cache):
        """Eviction handles a missing cache directory gracefully."""
        _tmp_path, _cfg, cache_dir = configured_env_with_cache

        # Remove the directory entirely
        import shutil
        shutil.rmtree(cache_dir)

        # Should not raise
        _evict_cache_if_needed()

    def test_evict_handles_oserror_on_unlink(self, configured_env_with_cache):
        """OSError during file removal does not crash eviction."""
        _tmp_path, cfg, cache_dir = configured_env_with_cache

        cfg.cache = CacheConfig.model_construct(
            enabled=True,
            directory=cache_dir,
            max_size_mb=0,
            ttl_hours=24,
        )
        set_config(cfg)

        (cache_dir / "locked.png").write_bytes(b"Z" * 100)

        with patch.object(Path, "unlink", side_effect=OSError("perm denied")):
            # Should not raise
            _evict_cache_if_needed()

    def test_evict_skips_stat_errors(self, configured_env_with_cache):
        """OSError during stat is skipped without crashing."""
        _tmp_path, cfg, cache_dir = configured_env_with_cache

        (cache_dir / "bad.png").write_bytes(b"Z" * 50)

        original_stat = Path.stat

        def broken_stat(self, **kwargs):
            if self.name == "bad.png":
                raise OSError("stat failed")
            return original_stat(self, **kwargs)

        with patch.object(Path, "stat", broken_stat):
            _evict_cache_if_needed()


# ============================================================================
# TestParseOpenscadStderr
# ============================================================================


class TestParseOpenscadStderr:
    """Tests for parsing OpenSCAD's stderr output."""

    def test_empty_input(self):
        """Empty string returns all empty lists."""
        result = _parse_openscad_stderr("")
        assert result == {
            "errors": [],
            "warnings": [],
            "echo_output": [],
            "deprecated": [],
        }

    def test_errors(self):
        """ERROR: lines are captured in the errors list."""
        result = _parse_openscad_stderr("ERROR: some message\n")
        assert "ERROR: some message" in result["errors"]

    def test_warnings(self):
        """WARNING: lines are captured in the warnings list."""
        result = _parse_openscad_stderr("WARNING: some warning\n")
        assert "WARNING: some warning" in result["warnings"]

    def test_echo(self):
        """ECHO: lines have the prefix stripped and value in echo_output."""
        result = _parse_openscad_stderr("ECHO: hello\n")
        assert "hello" in result["echo_output"]

    def test_deprecated(self):
        """DEPRECATED: lines are captured in the deprecated list."""
        result = _parse_openscad_stderr("DEPRECATED: old function\n")
        assert "DEPRECATED: old function" in result["deprecated"]

    def test_mixed_output(self):
        """Multiple categories are split correctly."""
        stderr = (
            "ECHO: value1\n"
            "WARNING: watch out\n"
            "ERROR: broken\n"
            "DEPRECATED: ancient api\n"
            "normal line\n"
        )
        result = _parse_openscad_stderr(stderr)
        assert len(result["echo_output"]) == 1
        assert len(result["warnings"]) == 1
        assert len(result["errors"]) == 1
        assert len(result["deprecated"]) == 1

    def test_error_without_colon(self):
        """Lines starting with ERROR (no colon) still end up in errors."""
        result = _parse_openscad_stderr("ERROR something went wrong\n")
        assert "ERROR something went wrong" in result["errors"]


# ============================================================================
# TestParseStlVertices
# ============================================================================


class TestParseStlVertices:
    """Tests for STL vertex parsing (ASCII and binary)."""

    def test_ascii_stl(self, tmp_path, ascii_stl_content):
        """Parse vertices from a valid ASCII STL file."""
        stl_file = tmp_path / "test.stl"
        stl_file.write_text(ascii_stl_content)

        vertices = _parse_stl_vertices(stl_file)
        assert len(vertices) == 6  # 2 facets x 3 vertices

    def test_binary_stl(self, tmp_path, binary_stl_content):
        """Parse vertices from a valid binary STL file."""
        stl_file = tmp_path / "test.stl"
        stl_file.write_bytes(binary_stl_content)

        vertices = _parse_stl_vertices(stl_file)
        assert len(vertices) == 3  # 1 triangle x 3 vertices

    def test_empty_ascii_stl(self, tmp_path):
        """Empty ASCII STL returns no vertices."""
        stl_file = tmp_path / "empty.stl"
        stl_file.write_text("solid empty\nendsolid empty\n")

        vertices = _parse_stl_vertices(stl_file)
        assert vertices == []

    def test_malformed_vertex_line(self, tmp_path):
        """Vertex lines with bad data are silently skipped."""
        content = (
            "solid test\n"
            "  facet normal 0 0 1\n"
            "    outer loop\n"
            "      vertex bad data here\n"
            "      vertex 10 0 0\n"
            "      vertex 10 10 5\n"
            "    endloop\n"
            "  endfacet\n"
            "endsolid test\n"
        )
        stl_file = tmp_path / "bad.stl"
        stl_file.write_text(content)

        vertices = _parse_stl_vertices(stl_file)
        # "vertex bad data here" has 4 parts after split but float() fails
        # so it is skipped; two valid vertices remain
        assert len(vertices) == 2

    def test_binary_stl_too_short(self, tmp_path):
        """Binary STL with incomplete triangle count raises ValueError."""
        # 80-byte header + only 2 bytes (incomplete count)
        stl_file = tmp_path / "short.stl"
        stl_file.write_bytes(b"\x00" * 80 + b"\x01\x00")

        with pytest.raises(ValueError, match="too short"):
            _parse_stl_vertices(stl_file)

    def test_binary_stl_truncated_vertex(self, tmp_path):
        """Binary STL with truncated vertex data raises ValueError."""
        header = b"\x00" * 80
        count = struct.pack("<I", 1)
        normal = struct.pack("<fff", 0.0, 0.0, 1.0)
        # Only one vertex instead of three
        v1 = struct.pack("<fff", 1.0, 2.0, 3.0)
        # Missing v2 and v3 -- truncated
        stl_file = tmp_path / "trunc.stl"
        stl_file.write_bytes(header + count + normal + v1)

        with pytest.raises(ValueError, match="unexpected end"):
            _parse_stl_vertices(stl_file)

    def test_ascii_stl_known_bbox(self, tmp_path, ascii_stl_content):
        """Vertices from the fixture match the known bounding box."""
        stl_file = tmp_path / "bbox.stl"
        stl_file.write_text(ascii_stl_content)

        vertices = _parse_stl_vertices(stl_file)

        xs = [v[0] for v in vertices]
        ys = [v[1] for v in vertices]
        zs = [v[2] for v in vertices]

        assert min(xs) == pytest.approx(0.0)
        assert max(xs) == pytest.approx(10.0)
        assert min(ys) == pytest.approx(0.0)
        assert max(ys) == pytest.approx(10.0)
        assert min(zs) == pytest.approx(0.0)
        assert max(zs) == pytest.approx(5.0)


# ============================================================================
# TestCacheKeyAndGaps
# ============================================================================


class TestCacheKeyAndGaps:
    """Tests for cache key computation, cache miss, and round-trip."""

    def test_deterministic_key(self):
        """Same parameters always produce the same cache key."""
        kwargs = dict(
            scad_content="cube(10);",
            color_scheme="Cornfield",
        )
        assert _compute_render_cache_key(**kwargs) == _compute_render_cache_key(**kwargs)

    def test_different_content_different_key(self):
        """Different SCAD content produces a different key."""
        key1 = _compute_render_cache_key(scad_content="cube(10);")
        key2 = _compute_render_cache_key(scad_content="sphere(5);")
        assert key1 != key2

    def test_scad_file_hashing(self, tmp_path):
        """Cache key changes when the on-disk file content changes."""
        scad = tmp_path / "model.scad"
        scad.write_text("cube(10);")
        key1 = _compute_render_cache_key(scad_file=str(scad))

        scad.write_text("sphere(5);")
        key2 = _compute_render_cache_key(scad_file=str(scad))

        assert key1 != key2

    def test_scad_file_unreadable_fallback(self):
        """Unreadable scad_file falls back to hashing the path string."""
        key = _compute_render_cache_key(scad_file="/nonexistent/model.scad")
        # Should succeed without raising
        assert isinstance(key, str) and len(key) == 64

    def test_check_cache_miss(self, configured_env_with_cache):
        """_check_cache returns None for a key that is not cached."""
        result = _check_cache("deadbeef" * 8)
        assert result is None

    def test_save_and_check_cache_roundtrip(self, configured_env_with_cache):
        """Saving then checking the same key returns the original data."""
        _tmp_path, _cfg, _cache_dir = configured_env_with_cache

        image_bytes = b"fake-png-data-for-cache"
        cache_key = "abc123" * 10 + "abcd"  # 64 hex chars

        _save_to_cache(cache_key, image_bytes)
        result = _check_cache(cache_key)

        assert result is not None
        assert base64.b64decode(result) == image_bytes


# ============================================================================
# TestRenderScadToPngGaps
# ============================================================================


class TestRenderScadToPngGaps:
    """Tests for render_scad_to_png edge cases and security."""

    def test_include_paths_flags(self, configured_env, mock_subprocess_success):
        """Include paths produce the correct -I flags in the command."""
        _tmp_path, _cfg = configured_env
        captured_cmd = []

        def spy_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return mock_subprocess_success()(cmd, **kwargs)

        with patch("subprocess.run", side_effect=spy_run):
            render_scad_to_png(
                scad_content="cube(1);",
                include_paths=["/path/a", "/path/b"],
            )

        assert "-I" in captured_cmd
        idx_a = captured_cmd.index("-I")
        assert captured_cmd[idx_a + 1] == "/path/a"
        # Find the second -I
        idx_b = captured_cmd.index("-I", idx_a + 1)
        assert captured_cmd[idx_b + 1] == "/path/b"

    def test_timeout_error(self, configured_env):
        """subprocess.TimeoutExpired is converted to RuntimeError."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("openscad", 300),
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                render_scad_to_png(scad_content="cube(1);")

    def test_no_output_error(self, configured_env):
        """RuntimeError when OpenSCAD succeeds but produces no output file."""
        def mock_run(cmd, **kwargs):
            # Return success but do NOT create the output file
            m = Mock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch("subprocess.run", side_effect=mock_run):
            with pytest.raises(RuntimeError, match="did not produce output"):
                render_scad_to_png(scad_content="cube(1);")

    def test_no_input_error(self, configured_env):
        """ValueError when neither scad_content nor scad_file is provided."""
        with pytest.raises(ValueError, match="Either scad_content or scad_file"):
            render_scad_to_png()

    def test_subprocess_failure(self, configured_env):
        """Non-zero return code surfaces the stderr message."""
        def mock_run(cmd, **kwargs):
            m = Mock()
            m.returncode = 1
            m.stderr = "Parse error in line 1"
            return m

        with patch("subprocess.run", side_effect=mock_run):
            with pytest.raises(RuntimeError, match="Parse error in line 1"):
                render_scad_to_png(scad_content="invalid{{{")

    def test_security_scad_file_not_allowed(self, tmp_path, monkeypatch):
        """scad_file outside allowed_paths raises ValueError."""
        cfg = Config(
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
            security=SecurityConfig(allowed_paths=["/allowed"]),
        )
        set_config(cfg)
        monkeypatch.setattr(
            "openscad_mcp.server.find_openscad", lambda: "/usr/bin/openscad"
        )

        with pytest.raises(ValueError, match="not within allowed paths"):
            render_scad_to_png(scad_file="/other/file.scad")

    def test_security_include_paths_not_allowed(self, tmp_path, monkeypatch):
        """Include paths outside allowed_paths raise ValueError."""
        cfg = Config(
            temp_dir=tmp_path,
            cache=CacheConfig(enabled=False, directory=tmp_path / "cache"),
            security=SecurityConfig(allowed_paths=["/allowed"]),
        )
        set_config(cfg)
        monkeypatch.setattr(
            "openscad_mcp.server.find_openscad", lambda: "/usr/bin/openscad"
        )

        with pytest.raises(ValueError, match="not within allowed paths"):
            render_scad_to_png(
                scad_content="cube(1);",
                include_paths=["/other"],
            )
