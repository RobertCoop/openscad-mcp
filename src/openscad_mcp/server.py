"""
Main FastMCP server implementation for OpenSCAD rendering.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from fastmcp import Context, FastMCP
from fastmcp.utilities.types import Image as MCPImage
from PIL import Image as PILImage

from .diagnostics import (
    DiagnosticRecord,
    Diagnostics,
    extract_source_dependencies,
    image_token_estimate,
    parse_deps_file,
    parse_openscad_output,
    unresolved_includes,
)
from .utils.config import get_config, get_render_semaphore

logger = logging.getLogger(__name__)


# Initialize the FastMCP server
mcp = FastMCP("OpenSCAD MCP Server")


# ============================================================================
# OpenSCAD binary discovery and capabilities
# ============================================================================

# Executable names to look up on PATH, most specific first. The nightly
# package is deliberately named so it co-installs with the 2021.01 release.
_OPENSCAD_NAMES = ["openscad-nightly", "openscad", "OpenSCAD", "openscad.exe"]

# Fixed locations checked after PATH. Nightly / snapshot layouts included.
_OPENSCAD_COMMON_PATHS = [
    "/usr/bin/openscad-nightly",
    "/usr/local/bin/openscad-nightly",
    "/snap/bin/openscad-nightly",
    "/usr/bin/openscad",
    "/usr/local/bin/openscad",
    "/snap/bin/openscad",
    "/var/lib/flatpak/exports/bin/org.openscad.OpenSCAD",
    "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
    "/Applications/OpenSCAD-nightly.app/Contents/MacOS/OpenSCAD",
    "C:\\Program Files\\OpenSCAD\\openscad.exe",
    "C:\\Program Files\\OpenSCAD (Nightly)\\openscad.exe",
    "C:\\Program Files (x86)\\OpenSCAD\\openscad.exe",
]

_VERSION_RE = re.compile(r"OpenSCAD version (\S+)")

# Memoised discovery. OpenSCAD is probed once per configured path; every
# render used to re-exec ``openscad --version`` before even checking the
# cache.
_openscad_cache: Dict[str, Optional[str]] = {}
_capability_cache: Dict[str, Dict[str, Any]] = {}


def _reset_openscad_cache() -> None:
    """Forget discovered binaries and capability records (tests, config reload)."""
    _openscad_cache.clear()
    _capability_cache.clear()


def _probe_version(path: str) -> Optional[str]:
    """Return the version string printed by ``openscad --version``, or None.

    2021.01 prints it on stderr; newer builds print on stdout. Both are read.
    """
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        # FileNotFoundError, PermissionError, TimeoutExpired, or anything a
        # broken shim raises: the candidate is simply not usable.
        return None
    text = f"{result.stdout or ''}\n{result.stderr or ''}"
    m = _VERSION_RE.search(text)
    if m:
        return m.group(1)
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else None


def _version_tuple(version: Optional[str]) -> Tuple[int, ...]:
    """Sortable tuple from strings like ``2021.01`` or ``2025.08.17``."""
    if not version:
        return (0,)
    parts: List[int] = []
    for piece in re.split(r"[.\-]", version):
        m = re.match(r"\d+", piece)
        if not m:
            break
        parts.append(int(m.group(0)))
    return tuple(parts) if parts else (0,)


def find_openscad() -> Optional[str]:
    """Locate the OpenSCAD executable.

    Order: the configured ``openscad_path`` (or ``OPENSCAD_PATH``), then
    every executable name on PATH and every known install location, both
    stable and nightly. When more than one candidate is found the newest
    version wins. The result is memoised per configured path.
    """
    config = get_config()
    configured = config.openscad_path or ""
    if configured in _openscad_cache:
        return _openscad_cache[configured]

    found: Optional[str] = None
    if configured and Path(configured).exists():
        found = configured
    else:
        # Every candidate is probed by executing it: names via PATH lookup by
        # the OS, fixed locations only if present. Among the ones that run,
        # the newest version wins; ties keep list order.
        probed: List[Tuple[str, Optional[str]]] = []
        for name in _OPENSCAD_NAMES:
            probed.append((name, _probe_version(name)))
        for common in _OPENSCAD_COMMON_PATHS:
            if Path(common).exists():
                probed.append((common, _probe_version(common)))

        best: Optional[Tuple[Tuple[int, ...], int, str]] = None
        for idx, (cand, version) in enumerate(probed):
            if version is None:
                continue
            _capability_cache.setdefault(cand, {})["version"] = version
            key = (_version_tuple(version), -idx, cand)
            if best is None or key[:2] > best[:2]:
                best = key
        if best is not None:
            found = best[2]
        else:
            # Nothing executed. A fixed path that exists but could not be
            # probed (permissions, sandbox) is still the best guess.
            existing = [c for c, _ in probed if c.startswith(("/", "C:"))]
            found = existing[0] if existing else None

    _openscad_cache[configured] = found
    return found


def get_openscad_capabilities(path: Optional[str] = None) -> Dict[str, Any]:
    """Return a cached capability record for the OpenSCAD binary.

    The record is probed once per binary path and reused by every tool, so
    version-dependent behaviour (nightly-only flags, removed formats) can be
    decided without re-executing OpenSCAD.
    """
    if path is None:
        path = find_openscad()
    if not path:
        return {"installed": False}
    cached = _capability_cache.get(path)
    if cached and cached.get("probed"):
        return cached

    version = (cached or {}).get("version") or _probe_version(path)
    vt = _version_tuple(version)
    is_snapshot = bool(version) and (len(vt) >= 3 or "git" in (version or ""))
    record: Dict[str, Any] = {
        "installed": True,
        "path": str(path),
        "version": version,
        "version_tuple": list(vt),
        "is_snapshot": is_snapshot,
        # Feature gates by version. 2021.01 is the stable floor.
        "has_manifold_backend": vt >= (2024, 9),
        "has_summary_json": vt >= (2022,),
        "has_egl_headless": vt >= (2023, 9),
        "amf_export": vt < (2026,),
        "probed": True,
    }
    _capability_cache[path] = record
    return record


# ============================================================================
# Subprocess execution
# ============================================================================

_memory_limit_checked: Dict[str, bool] = {}


def _wrap_with_memory_limit(cmd: List[str]) -> List[str]:
    """Prefix *cmd* with a POSIX shell that applies RLIMIT_AS, then execs.

    ``preexec_fn`` is avoided deliberately: this is a multi-threaded async
    server and running Python between fork and exec is a documented
    segfault source. ``exec`` replaces the shell, so the direct child that
    ``subprocess`` kills on timeout is OpenSCAD itself.
    """
    config = get_config()
    limit_mb = config.security.max_memory_mb
    if limit_mb <= 0 or os.name != "posix":
        return cmd
    sh = shutil.which("sh")
    if not sh:
        return cmd
    limit_kb = int(limit_mb) * 1024
    key = str(limit_kb)
    if key not in _memory_limit_checked:
        try:
            probe = subprocess.run(
                [sh, "-c", f"ulimit -v {limit_kb}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            _memory_limit_checked[key] = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _memory_limit_checked[key] = False
        if not _memory_limit_checked[key]:
            logger.warning(
                "Could not apply memory limit of %d MB to OpenSCAD subprocesses "
                "(ulimit -v unsupported here); running without a ceiling",
                limit_mb,
            )
    if not _memory_limit_checked[key]:
        return cmd
    return [sh, "-c", f'ulimit -v {limit_kb} 2>/dev/null; exec "$@"', "openscad-mcp", *cmd]


def _run_openscad(
    cmd: List[str],
    include_paths: Optional[List[str]] = None,
    label: str = "rendering",
) -> subprocess.CompletedProcess:
    """Run one OpenSCAD command with the configured limits.

    Applies the render timeout, the memory ceiling, ``OPENSCADPATH`` for
    include paths, and a fresh session so terminal signals never reach the
    child. On timeout the partial stderr is kept in the error so the model
    sees what OpenSCAD reported before it was killed.
    """
    config = get_config()
    full_cmd = _wrap_with_memory_limit(cmd)
    try:
        return subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=config.rendering.timeout_seconds,
            env=_openscad_env(include_paths),
            stdin=subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stderr
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        tail = ""
        if partial:
            diag = parse_openscad_output(partial, None)
            lines = diag.errors + diag.warnings
            if not lines:
                lines = [ln for ln in partial.splitlines() if ln.strip()][-5:]
            if lines:
                tail = " Output before timeout: " + " | ".join(lines[-5:])
        raise RuntimeError(
            f"OpenSCAD {label} timed out after {config.rendering.timeout_seconds} seconds.{tail}"
        ) from exc


def _openscad_env(
    include_paths: Optional[List[str]] = None,
) -> Optional[Dict[str, str]]:
    """
    Build the environment for an OpenSCAD subprocess, honouring include paths.

    OpenSCAD has no include-path command line flag. Library and include
    search paths come from the OPENSCADPATH environment variable, which is
    os.pathsep-separated. Anything already set in the environment is kept and
    searched after the caller's paths, so configuring OPENSCADPATH globally
    still works.

    Returns None when there is nothing to add, so the subprocess simply
    inherits the parent environment.
    """
    if not include_paths:
        return None
    env = os.environ.copy()
    paths = [str(p) for p in include_paths]
    existing = env.get("OPENSCADPATH", "")
    if existing:
        paths.append(existing)
    env["OPENSCADPATH"] = os.pathsep.join(paths)
    return env


def _format_variables(variables: Optional[Dict[str, Any]]) -> List[str]:
    """Turn a variables dict into ``-D name=value`` argv pairs."""
    args: List[str] = []
    if not variables:
        return args
    for key, value in variables.items():
        if isinstance(value, str):
            val_str = f'"{value}"'
        elif isinstance(value, bool):
            val_str = "true" if value else "false"
        else:
            val_str = str(value)
        args.extend(["-D", f"{key}={val_str}"])
    return args


# ============================================================================
# Security helpers
# ============================================================================


def _is_within(resolved: Union[str, Path], allowed_root: Union[str, Path]) -> bool:
    """
    Report whether *resolved* lies inside *allowed_root*.

    Containment is decided with Path.is_relative_to rather than by comparing
    path strings with startswith. A string prefix test counts any sibling
    whose name merely begins with the allowed root as being inside it, so
    permitting /srv/project would also permit /srv/project-secrets and
    /srv/projects -- meaning a configured sandbox does not actually hold.

    Both sides are resolved first, so symlinks and ".." segments cannot be
    used to step outside the root either.
    """
    try:
        return Path(resolved).resolve().is_relative_to(
            Path(allowed_root).resolve()
        )
    except (OSError, ValueError):
        # An unresolvable or malformed root can never contain anything.
        return False


def _library_search_paths() -> List[Path]:
    """Standard OpenSCAD library directories for this platform plus OPENSCADPATH."""
    search_paths: List[Path] = []
    system = platform.system()
    home = Path.home()
    if system == "Linux":
        search_paths.extend([
            home / ".local" / "share" / "OpenSCAD" / "libraries",
            Path("/usr/share/openscad/libraries"),
            Path("/usr/share/openscad-nightly/libraries"),
            Path("/usr/local/share/openscad/libraries"),
        ])
    elif system == "Darwin":
        search_paths.extend([
            home / "Documents" / "OpenSCAD" / "libraries",
            home / "Library" / "Application Support" / "OpenSCAD" / "libraries",
        ])
    elif system == "Windows":
        search_paths.extend([
            home / "Documents" / "OpenSCAD" / "libraries",
        ])
    openscad_env = os.environ.get("OPENSCADPATH")
    if openscad_env:
        for p in openscad_env.split(os.pathsep):
            if p.strip():
                env_path = Path(p.strip())
                if env_path not in search_paths:
                    search_paths.append(env_path)
    return search_paths


def _check_allowed_path(path: Union[str, Path], what: str) -> None:
    """Raise ValueError unless *path* is inside a configured allowed root."""
    config = get_config()
    if not config.security.allowed_paths:
        return
    resolved = Path(path).resolve()
    if not any(_is_within(resolved, ap) for ap in config.security.allowed_paths):
        raise ValueError(
            f"{what} '{path}' is not within allowed paths: {config.security.allowed_paths}"
        )


def _validate_include_paths(include_paths: Optional[List[str]]) -> None:
    """Validate every caller-supplied include directory against allowed_paths."""
    if not include_paths:
        return
    for inc_path in include_paths:
        _check_allowed_path(inc_path, "Include path")


def _validate_source_size(scad_content: Optional[str]) -> None:
    if not scad_content:
        return
    config = get_config()
    max_bytes = config.security.max_file_size_mb * 1024 * 1024
    if len(scad_content) > max_bytes:
        raise ValueError(
            f"SCAD content size ({len(scad_content)} bytes) exceeds maximum allowed size "
            f"({config.security.max_file_size_mb} MB / {max_bytes} bytes)"
        )


def _validate_variable_names(variables: Optional[Dict[str, Any]]) -> None:
    if not variables:
        return
    for key in variables:
        if not VARIABLE_NAME_RE.match(key):
            raise ValueError(
                f"Invalid variable name '{key}': must match {VARIABLE_NAME_RE.pattern}"
            )


def _check_dependency_closure(
    deps: List[str],
    scad_path: Path,
    include_paths: Optional[List[str]] = None,
) -> None:
    """Enforce ``allowed_paths`` on every file OpenSCAD actually read.

    ``allowed_paths`` used to apply only to the ``scad_file`` argument. A
    script can still reach any readable file through ``include <...>``,
    ``use <...>``, ``import()`` and ``surface()``, and return its contents
    through echo output or as geometry. The ``-d`` dependency list is the
    resolved closure of those reads, so it is checked here after the run and
    the output is withheld when any file lies outside the sandbox.

    Allowed roots: ``allowed_paths``, the standard library directories,
    caller ``include_paths`` (already validated), and the server temp dir.
    """
    config = get_config()
    if not config.security.allowed_paths:
        return
    roots: List[Path] = [Path(p) for p in config.security.allowed_paths]
    roots.extend(_library_search_paths())
    roots.extend(Path(p) for p in (include_paths or []))
    roots.append(Path(config.temp_dir))
    scad_dir = scad_path.parent
    offenders: List[str] = []
    for dep in deps:
        dep_path = Path(dep)
        if not dep_path.is_absolute():
            dep_path = scad_dir / dep_path
        if any(_is_within(dep_path, root) for root in roots):
            continue
        offenders.append(str(dep_path))
    if offenders:
        raise ValueError(
            "The model reads files outside allowed paths and its output has been "
            f"withheld: {offenders[:5]}. Allowed roots: {config.security.allowed_paths}"
        )


# ============================================================================
# Render Cache Helpers
# ============================================================================


def _hash_field(hasher: "hashlib._Hash", value: Any) -> None:
    """Feed one length-prefixed field so adjacent fields can never merge."""
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    hasher.update(len(data).to_bytes(8, "big"))
    hasher.update(data)


def _compute_render_cache_key(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    camera_position: Optional[List[float]] = None,
    camera_target: Optional[List[float]] = None,
    camera_up: Optional[List[float]] = None,
    image_size: Optional[List[int]] = None,
    color_scheme: str = "Cornfield",
    variables: Optional[Dict[str, Any]] = None,
    auto_center: bool = False,
    include_paths: Optional[List[str]] = None,
    binary_identity: Optional[str] = None,
) -> str:
    """Compute a SHA-256 cache key from all rendering parameters.

    When *scad_file* is provided (instead of inline content), the file's
    contents are read and hashed so that changes to the file on disk
    correctly invalidate the cache entry. Files pulled in through
    ``include``/``use``/``import``/``surface`` are not part of the key;
    they are validated on lookup through the cache manifest instead.

    Args:
        scad_content: Inline OpenSCAD source code.
        scad_file: Path to an OpenSCAD file.
        camera_position: Camera eye position [x, y, z].
        camera_target: Camera look-at point [x, y, z].
        camera_up: Camera up vector [x, y, z].
        image_size: Output image dimensions [width, height].
        color_scheme: OpenSCAD colour scheme name.
        variables: OpenSCAD ``-D`` variables.
        auto_center: Whether auto-centre / view-all is enabled.
        include_paths: Extra include directories.
        binary_identity: Path and version of the OpenSCAD binary.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    hasher = hashlib.sha256()

    # Hash the actual SCAD source
    if scad_content:
        _hash_field(hasher, scad_content.encode("utf-8"))
    elif scad_file:
        try:
            _hash_field(hasher, Path(scad_file).read_bytes())
        except OSError:
            # If we cannot read the file fall back to hashing the path
            _hash_field(hasher, scad_file.encode("utf-8"))
    else:
        _hash_field(hasher, b"")

    for value in (
        camera_position,
        camera_target,
        camera_up,
        image_size,
        color_scheme,
        variables or {},
        bool(auto_center),
        include_paths or [],
        binary_identity or "",
    ):
        _hash_field(hasher, value)

    return hasher.hexdigest()


def _manifest_path(cache_key: str) -> Path:
    return get_config().cache.directory / f"{cache_key}.json"


def _file_fingerprint(path: Path, with_hash: bool = True) -> Optional[Dict[str, Any]]:
    try:
        st = path.stat()
    except OSError:
        return None
    entry: Dict[str, Any] = {
        "path": str(path),
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }
    if with_hash:
        try:
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None
    return entry


def _build_cache_manifest(
    deps: List[str],
    scad_dir: Path,
    missing_includes: List[str],
    diagnostics: Diagnostics,
    exclude: Optional[List[Path]] = None,
) -> Dict[str, Any]:
    """Record every file the render depended on, with size/mtime/sha256.

    *exclude* lists files already covered by the cache key (the top-level
    source, which for inline content is a temp file that will not exist at
    lookup time).
    """
    excluded = {p.resolve() for p in (exclude or [])}
    entries: List[Dict[str, Any]] = []
    for dep in deps:
        p = Path(dep)
        if not p.is_absolute():
            p = scad_dir / p
        try:
            if p.resolve() in excluded:
                continue
        except OSError:
            continue
        fp = _file_fingerprint(p)
        if fp is not None:
            entries.append(fp)
    return {
        "version": 1,
        "dependencies": entries,
        "unresolved_includes": missing_includes,
        "scad_dir": str(scad_dir),
        "diagnostics": diagnostics.to_dict(include_records=True),
        "statistics": diagnostics.statistics,
    }


def _manifest_is_current(
    manifest: Dict[str, Any], include_paths: Optional[List[str]]
) -> bool:
    """True if every recorded dependency is unchanged and no missing include appeared."""
    for entry in manifest.get("dependencies", []):
        p = Path(entry["path"])
        fresh = _file_fingerprint(p, with_hash=False)
        if fresh is None:
            return False
        if fresh["size"] == entry.get("size") and fresh["mtime_ns"] == entry.get("mtime_ns"):
            continue
        # Stat drift: fall back to content comparison
        fresh = _file_fingerprint(p, with_hash=True)
        if fresh is None or fresh.get("sha256") != entry.get("sha256"):
            return False

    # Negative dependencies: includes that could not be opened at render
    # time. If one exists now, the cached image was built without it.
    search_dirs: List[Path] = [Path(manifest.get("scad_dir", "."))]
    search_dirs.extend(Path(p) for p in (include_paths or []))
    search_dirs.extend(_library_search_paths())
    for name in manifest.get("unresolved_includes", []):
        for d in search_dirs:
            if (d / name).exists():
                return False
    return True


def _check_cache(
    cache_key: str, include_paths: Optional[List[str]] = None
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return ``(base64 PNG, manifest)`` on a validated hit, else None.

    A hit requires the PNG, a manifest, an unexpired TTL, and every
    dependency recorded in the manifest to be unchanged.
    """
    config = get_config()
    if not config.cache.enabled:
        return None

    cache_file = config.cache.directory / f"{cache_key}.png"
    manifest_file = _manifest_path(cache_key)
    if not cache_file.exists():
        return None

    # Check TTL
    age_hours = (time.time() - cache_file.stat().st_mtime) / 3600.0
    if age_hours > config.cache.ttl_hours:
        _remove_cache_entry(cache_key)
        return None

    if not manifest_file.exists():
        # Pre-manifest entry: cannot be validated, so treat as a miss.
        _remove_cache_entry(cache_key)
        return None
    try:
        manifest = json.loads(manifest_file.read_text())
    except (OSError, ValueError):
        _remove_cache_entry(cache_key)
        return None

    if not _manifest_is_current(manifest, include_paths):
        _remove_cache_entry(cache_key)
        return None

    try:
        image_data = cache_file.read_bytes()
        return base64.b64encode(image_data).decode("utf-8"), manifest
    except OSError:
        return None


def _remove_cache_entry(cache_key: str) -> None:
    config = get_config()
    for suffix in (".png", ".json"):
        try:
            (config.cache.directory / f"{cache_key}{suffix}").unlink()
        except OSError:
            pass


def _save_to_cache(
    cache_key: str, image_data: bytes, manifest: Optional[Dict[str, Any]] = None
) -> None:
    """Save raw PNG bytes plus the dependency manifest, evicting if needed.

    Args:
        cache_key: Hex digest returned by ``_compute_render_cache_key``.
        image_data: Raw PNG image bytes (not base64).
        manifest: Dependency manifest from ``_build_cache_manifest``.
    """
    config = get_config()
    if not config.cache.enabled:
        return

    config.cache.ensure_cache_directory()
    cache_file = config.cache.directory / f"{cache_key}.png"

    try:
        _manifest_path(cache_key).write_text(json.dumps(manifest or {"version": 1, "dependencies": []}))
        cache_file.write_bytes(image_data)
    except OSError as exc:
        logger.warning("Failed to write render cache entry: %s", exc)
        _remove_cache_entry(cache_key)
        return

    # Evict oldest files if the cache exceeds the size limit
    _evict_cache_if_needed()


def _evict_cache_if_needed() -> None:
    """Delete the oldest cache entries until total size is within limits."""
    config = get_config()
    if not config.cache.enabled:
        return

    cache_dir = config.cache.directory
    if not cache_dir.exists():
        return

    max_bytes = config.cache.max_size_mb * 1024 * 1024

    # Collect all cache files with their stats
    cache_files: List[Tuple[Path, float, int]] = []
    total_size = 0
    for f in list(cache_dir.glob("*.png")) + list(cache_dir.glob("*.json")):
        try:
            stat = f.stat()
            cache_files.append((f, stat.st_mtime, stat.st_size))
            total_size += stat.st_size
        except OSError:
            continue

    if total_size <= max_bytes:
        return

    # Sort oldest first (ascending mtime)
    cache_files.sort(key=lambda t: t[1])

    for file_path, _mtime, file_size in cache_files:
        if total_size <= max_bytes:
            break
        try:
            file_path.unlink()
            total_size -= file_size
            sibling = file_path.with_suffix(".json" if file_path.suffix == ".png" else ".png")
            if sibling.exists():
                total_size -= sibling.stat().st_size
                sibling.unlink()
        except OSError:
            continue


# ============================================================================
# Rendering
# ============================================================================


@dataclass
class RenderResult:
    """Outcome of one PNG render: the image plus everything OpenSCAD said."""

    image_b64: str
    diagnostics: Optional[Diagnostics] = None
    cached: bool = False
    dependencies: List[str] = field(default_factory=list)
    unresolved_includes: List[str] = field(default_factory=list)
    cache_key: Optional[str] = None
    image_size: Optional[List[int]] = None

    def metadata(self) -> Dict[str, Any]:
        """JSON-safe summary for tool responses."""
        d: Dict[str, Any] = {"cached": self.cached}
        if self.diagnostics is not None:
            d.update(self.diagnostics.to_dict(include_records=False))
        if self.unresolved_includes:
            d["unresolved_includes"] = self.unresolved_includes
        if self.image_size:
            d["image_size"] = list(self.image_size)
            d["image_tokens"] = image_token_estimate(*self.image_size)
        return d


def _as_render_result(value: Any) -> RenderResult:
    """Accept either a RenderResult or a bare base64 string (older callers/mocks)."""
    if isinstance(value, RenderResult):
        return value
    return RenderResult(image_b64=str(value))


def _clamp_image_size(image_size: List[int]) -> List[int]:
    """Clamp to configured maxima, preserving aspect ratio."""
    config = get_config()
    max_w = config.rendering.max_image_width
    max_h = config.rendering.max_image_height
    w, h = int(image_size[0]), int(image_size[1])
    if w <= 0 or h <= 0:
        return [max(w, 1), max(h, 1)]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        w = max(1, int(w * scale))
        h = max(1, int(h * scale))
    return [w, h]


def render_scad_to_png(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    camera_position: Optional[List[float]] = None,
    camera_target: Optional[List[float]] = None,
    camera_up: Optional[List[float]] = None,
    image_size: Optional[List[int]] = None,
    color_scheme: str = "Cornfield",
    variables: Optional[Dict[str, Any]] = None,
    auto_center: bool = False,
    include_paths: Optional[List[str]] = None,
) -> RenderResult:
    """
    Render OpenSCAD code or file to PNG.

    Returns a :class:`RenderResult` holding the base64 PNG and the parsed
    OpenSCAD diagnostics. The image is always returned when OpenSCAD wrote
    one, even when the diagnostics contain errors: on 2021.01 a failed
    ``assert()`` or an unknown module exits 0 with a blank picture, and the
    picture together with the error is the useful signal.

    Supports render caching (controlled via ``config.cache``), validated
    against every file the render read, and multi-file projects via
    ``include_paths``.

    Raises:
        RuntimeError: OpenSCAD is missing, exited non-zero without producing
            an image, or timed out.
        ValueError: A security validation failed.
    """
    if camera_position is None:
        camera_position = [70, 70, 70]
    if camera_target is None:
        camera_target = [0, 0, 0]
    if camera_up is None:
        camera_up = [0, 0, 1]
    if image_size is None:
        image_size = [800, 600]
    image_size = _clamp_image_size(list(image_size))

    openscad_cmd = find_openscad()
    if not openscad_cmd:
        raise RuntimeError("OpenSCAD not found. Please install OpenSCAD first.")

    config = get_config()

    # Security validations
    if scad_file:
        _check_allowed_path(scad_file, "File path")
    _validate_source_size(scad_content)
    _validate_variable_names(variables)
    _validate_include_paths(include_paths)

    capabilities = get_openscad_capabilities(openscad_cmd)
    binary_identity = f"{openscad_cmd}|{capabilities.get('version')}"

    # --- Cache: check for a validated cached render ---
    cache_key = _compute_render_cache_key(
        scad_content=scad_content,
        scad_file=scad_file,
        camera_position=camera_position,
        camera_target=camera_target,
        camera_up=camera_up,
        image_size=image_size,
        color_scheme=color_scheme,
        variables=variables,
        auto_center=auto_center,
        include_paths=include_paths,
        binary_identity=binary_identity,
    )
    cached = _check_cache(cache_key, include_paths)
    if cached is not None:
        image_b64, manifest = cached
        logger.debug("Render cache hit for key %s", cache_key[:12])
        diag_dict = manifest.get("diagnostics") or {}
        diag = Diagnostics(returncode=0)
        # Rebuild the record list so cached diagnostics are not lost.
        for rec in diag_dict.get("records", []):
            diag.records.append(
                DiagnosticRecord(
                    severity=rec.get("severity", "WARNING"),
                    message=rec.get("message", ""),
                    file=rec.get("file"),
                    line=rec.get("line"),
                    trace=list(rec.get("trace", [])),
                )
            )
        diag.echo_output = list(diag_dict.get("echo_output", []))
        diag.echo_truncated = bool(diag_dict.get("echo_truncated", False))
        diag.statistics = dict(manifest.get("statistics") or {})
        return RenderResult(
            image_b64=image_b64,
            diagnostics=diag,
            cached=True,
            dependencies=[e["path"] for e in manifest.get("dependencies", [])],
            unresolved_includes=list(manifest.get("unresolved_includes", [])),
            cache_key=cache_key,
            image_size=image_size,
        )

    # Ensure temp directory exists
    temp_dir_path = Path(config.temp_dir)
    temp_dir_path.mkdir(parents=True, exist_ok=True)

    # Create temporary files
    with tempfile.TemporaryDirectory(dir=config.temp_dir) as temp_dir:
        temp_path = Path(temp_dir)

        # Handle input source
        inline_path: Optional[str] = None
        if scad_content:
            scad_path = temp_path / "input.scad"
            scad_path.write_text(scad_content)
            inline_path = str(scad_path)
        elif scad_file:
            scad_path = Path(scad_file)
            if not scad_path.exists():
                raise FileNotFoundError(f"SCAD file not found: {scad_file}")
        else:
            raise ValueError("Either scad_content or scad_file must be provided")

        # Output paths
        output_path = temp_path / "output.png"
        deps_path = temp_path / "deps.make"

        # Build OpenSCAD command
        cmd = [openscad_cmd]
        if config.rendering.hard_warnings:
            cmd.append("--hardwarnings")
        cmd += [
            "-o", str(output_path),
            "-d", str(deps_path),
            "--imgsize", f"{image_size[0]},{image_size[1]}",
            "--colorscheme", color_scheme,
        ]

        # Add camera parameters (eye + center, 6-value format)
        camera_str = (
            f"--camera="
            f"{camera_position[0]},{camera_position[1]},{camera_position[2]},"
            f"{camera_target[0]},{camera_target[1]},{camera_target[2]}"
        )
        cmd.append(camera_str)

        if auto_center:
            cmd.append("--autocenter")
            cmd.append("--viewall")

        cmd.extend(_format_variables(variables))

        # Add the SCAD file
        cmd.append(str(scad_path))

        render_start = time.time()
        result = _run_openscad(cmd, include_paths, label="rendering")

        diag = parse_openscad_output(result.stderr or "", result.returncode, inline_path)

        # Dependency closure: what OpenSCAD actually read
        deps: List[str] = []
        if deps_path.exists():
            try:
                deps = parse_deps_file(deps_path.read_text(errors="replace"))
            except OSError:
                deps = []
        missing = unresolved_includes(diag)

        # Security: withhold output that depended on files outside the sandbox
        _check_dependency_closure(deps, scad_path, include_paths)

        if result.returncode != 0 or not output_path.exists():
            detail = "; ".join(diag.errors) if diag.errors else (result.stderr or "").strip()
            if not output_path.exists() and result.returncode == 0:
                detail = detail or "OpenSCAD did not produce output file"
            raise RuntimeError(f"OpenSCAD rendering failed: {detail}")

        # Read the image
        with open(output_path, "rb") as f:
            image_data = f.read()

        # --- Cache: save the rendered image with its manifest ---
        # Do not cache a render whose inputs were modified while it ran: the
        # image may reflect either version of the file.
        scad_dir = scad_path.parent
        manifest = _build_cache_manifest(deps, scad_dir, missing, diag, exclude=[scad_path])
        race = any(
            (e.get("mtime_ns", 0) / 1e9) >= render_start
            for e in manifest["dependencies"]
        )
        if not race:
            _save_to_cache(cache_key, image_data, manifest)

        return RenderResult(
            image_b64=base64.b64encode(image_data).decode("utf-8"),
            diagnostics=diag,
            cached=False,
            dependencies=[e["path"] for e in manifest["dependencies"]],
            unresolved_includes=missing,
            cache_key=cache_key,
            image_size=image_size,
        )


# ============================================================================
# MCP Tools
# ============================================================================


def parse_camera_param(param: Union[str, List[float], Dict[str, float], None], default: List[float]) -> List[float]:
    """
    Parse camera parameters from various input formats.
    
    Accepts:
    - List of floats: [x, y, z]
    - JSON string: "[x, y, z]" or '{"x": x, "y": y, "z": z}'
    - Dict: {"x": x, "y": y, "z": z}
    - None: returns default
    """
    if param is None:
        return default
    
    # If it's already a list, return it
    if isinstance(param, list):
        if len(param) == 3:
            return [float(v) for v in param]
        else:
            raise ValueError(f"Expected 3 values for camera parameter, got {len(param)}")
    
    # If it's a dict with x, y, z keys
    if isinstance(param, dict):
        if "x" in param and "y" in param and "z" in param:
            return [float(param["x"]), float(param["y"]), float(param["z"])]
        else:
            raise ValueError(f"Dict must have x, y, z keys, got {param.keys()}")
    
    # If it's a string, try to parse as JSON
    if isinstance(param, str):
        try:
            parsed = json.loads(param.strip())
            if isinstance(parsed, list) and len(parsed) == 3:
                return [float(v) for v in parsed]
            elif isinstance(parsed, dict) and all(k in parsed for k in ["x", "y", "z"]):
                return [float(parsed["x"]), float(parsed["y"]), float(parsed["z"])]
            else:
                raise ValueError(f"Parsed value must be a list of 3 numbers or dict with x,y,z keys")
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Cannot parse '{param}' as camera parameter: {e}")
    
    raise ValueError(f"Unexpected type for camera parameter: {type(param)}")


def parse_list_param(param: Union[str, List[Any], None], default: List[Any]) -> List[Any]:
    """
    Parse flexible list parameters from various input formats.
    
    Handles:
    - JSON arrays: '["front", "top"]'
    - CSV strings: "front,top"
    - Python lists: ["front", "top"]
    - None: returns default
    
    Args:
        param: Input parameter in various formats
        default: Default value if param is None
    
    Returns:
        Parsed list
    """
    if param is None:
        return default
    
    # Already a list
    if isinstance(param, list):
        return param
    
    # String input - try various formats
    if isinstance(param, str):
        param = param.strip()

        # Empty or whitespace-only string returns default
        if not param:
            return default

        # Try JSON parsing first
        if param.startswith('['):
            try:
                parsed = json.loads(param)
                if isinstance(parsed, list):
                    return parsed
                else:
                    raise ValueError(f"JSON parsed to {type(parsed)}, expected list")
            except json.JSONDecodeError:
                pass
        
        # Try CSV format
        if ',' in param:
            return [item.strip() for item in param.split(',') if item.strip()]
        
        # Single value
        return [param]
    
    raise ValueError(f"Cannot parse list from type {type(param)}")


def parse_dict_param(param: Union[str, Dict[str, Any], None], default: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse flexible dict parameters from various input formats.
    
    Handles:
    - JSON objects: '{"x": 10, "y": 20}'
    - Key=value strings: "x=10,y=20"
    - Python dicts: {"x": 10}
    - None: returns default
    
    Args:
        param: Input parameter in various formats
        default: Default value if param is None
    
    Returns:
        Parsed dictionary
    """
    if param is None:
        return default
    
    # Already a dict
    if isinstance(param, dict):
        return param
    
    # String input - try various formats
    if isinstance(param, str):
        param = param.strip()

        # Empty or whitespace-only string returns default
        if not param:
            return default

        # Try JSON parsing first
        if param.startswith('{'):
            try:
                parsed = json.loads(param)
                if isinstance(parsed, dict):
                    return parsed
                else:
                    raise ValueError(f"JSON parsed to {type(parsed)}, expected dict")
            except json.JSONDecodeError:
                pass
        
        # Try key=value format
        if '=' in param:
            result = {}
            pairs = param.split(',')
            for pair in pairs:
                pair = pair.strip()
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Try to parse the value as number or boolean
                    try:
                        # Try integer first
                        if '.' not in value:
                            result[key] = int(value)
                        else:
                            result[key] = float(value)
                    except ValueError:
                        # Check for boolean
                        if value.lower() == 'true':
                            result[key] = True
                        elif value.lower() == 'false':
                            result[key] = False
                        else:
                            # Keep as string
                            result[key] = value
            return result
    
    raise ValueError(f"Cannot parse dict from type {type(param)}")


def parse_image_size_param(param: Union[List[int], str, tuple, None], default: List[int]) -> List[int]:
    """
    Parse flexible image size parameters from various input formats.
    
    Handles:
    - List format: [800, 600]
    - String format: "800x600" or "800,600"
    - Tuple format: (800, 600)
    - None: returns default
    
    Args:
        param: Input parameter in various formats
        default: Default value if param is None
    
    Returns:
        List of two integers [width, height]
    """
    if param is None:
        return default
    
    # Already a list
    if isinstance(param, list):
        if len(param) == 2:
            return [int(param[0]), int(param[1])]
        else:
            raise ValueError(f"Image size must have 2 values, got {len(param)}")
    
    # Tuple format
    if isinstance(param, tuple):
        if len(param) == 2:
            return [int(param[0]), int(param[1])]
        else:
            raise ValueError(f"Image size must have 2 values, got {len(param)}")
    
    # String format
    if isinstance(param, str):
        param = param.strip()
        
        # Try JSON format first (handles "[1200, 900]")
        if param.startswith('['):
            try:
                parsed = json.loads(param)
                if isinstance(parsed, list) and len(parsed) == 2:
                    return [int(parsed[0]), int(parsed[1])]
            except (json.JSONDecodeError, ValueError):
                pass
        
        # Try "800x600" format
        if 'x' in param:
            parts = param.split('x')
            if len(parts) == 2:
                return [int(parts[0].strip()), int(parts[1].strip())]
        
        # Try "800,600" format (only if not JSON-like)
        if ',' in param and not param.startswith('['):
            parts = param.split(',')
            if len(parts) == 2:
                return [int(parts[0].strip()), int(parts[1].strip())]
    
    raise ValueError(f"Cannot parse image size from {param}")

def estimate_response_size(data: Any) -> int:
    """
    Estimate the token size of response data.
    
    Uses a rough approximation of 4 characters per token, which is a 
    conservative estimate for base64-encoded data and JSON structures.
    
    Args:
        data: Any JSON-serializable data structure
        
    Returns:
        Estimated size in tokens
    """
    json_str = json.dumps(data)
    # Approximate: 4 characters per token (conservative for base64)
    return len(json_str) // 4


def save_image_to_file(base64_data: str, filename: str, output_dir: Path) -> str:
    """
    Save base64 image to file and return path.
    
    Decodes base64 image data and saves it to a file in the specified directory.
    Creates the directory if it doesn't exist.
    
    Args:
        base64_data: Base64-encoded image data
        filename: Name for the saved file
        output_dir: Directory to save the file in
        
    Returns:
        String path to the saved file
        
    Raises:
        ValueError: If base64 decoding fails
        OSError: If file writing fails
    """
    try:
        # Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / filename
        
        # Decode and save
        image_data = base64.b64decode(base64_data)
        with open(file_path, 'wb') as f:
            f.write(image_data)
        
        return str(file_path)
    except Exception as e:
        raise ValueError(f"Failed to save image to file: {e}")


def compress_base64_image(base64_data: str, quality: int = 85, optimize: bool = True) -> str:
    """
    Compress base64 image to reduce size.
    
    Uses PIL/Pillow to decode, compress, and re-encode the image.
    Maintains PNG format but applies compression and optimization.
    
    Args:
        base64_data: Base64-encoded PNG image
        quality: Compression quality (1-100, ignored for PNG optimize)
        optimize: Whether to apply PNG optimization
        
    Returns:
        Compressed base64-encoded image
        
    Raises:
        ValueError: If image processing fails
    """
    import io
    
    try:
        # Decode base64 to image
        image_data = base64.b64decode(base64_data)
        image = PILImage.open(io.BytesIO(image_data))
        
        # Compress using PNG optimization
        buffer = io.BytesIO()
        # For PNG, quality parameter doesn't apply, but optimize does
        # We use compress_level for finer control
        save_kwargs = {
            'format': 'PNG',
            'optimize': optimize,
            'compress_level': 9 if quality < 50 else (6 if quality < 85 else 3)
        }
        image.save(buffer, **save_kwargs)
        
        # Re-encode to base64
        buffer.seek(0)
        compressed_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return compressed_data
    except Exception as e:
        raise ValueError(f"Failed to compress image: {e}")


def manage_response_size(
    images: Union[Dict[str, str], List[Dict[str, Any]]], 
    output_format: str = "auto",
    max_size: int = 25000, 
    output_dir: Optional[Path] = None,
    ctx: Optional[Any] = None
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Manage response size for multiple images.
    
    Intelligently handles large image responses by either compressing them,
    saving to files, or keeping as base64 based on size constraints.
    
    Args:
        images: Dictionary of name->base64 or list of image dicts with base64 data
        output_format: "auto" | "base64" | "file_path" | "compressed"
        max_size: Maximum response size in tokens (approx 4 chars per token)
        output_dir: Directory to save images when using file_path format
        ctx: Optional context for logging
        
    Returns:
        Modified images dictionary or list with optimized responses
    """
    config = get_config()
    
    # Set default output directory if not provided
    if output_dir is None:
        output_dir = Path(config.temp_dir) / "renders"
    
    # Handle both dict and list inputs
    is_dict = isinstance(images, dict)
    
    if is_dict:
        working_images = [(k, v) for k, v in images.items()]
    else:
        working_images = [(f"image_{i}", img.get("data", img)) for i, img in enumerate(images)]
    
    # Determine output format if auto
    if output_format == "auto":
        # Estimate current size
        current_size = estimate_response_size(images)
        
        if ctx:
            logger.info(f"Estimated response size: {current_size} tokens")
        
        if current_size > max_size:
            # Try compression first
            test_compressed = {}
            for name, data in working_images[:1]:  # Test with first image
                try:
                    compressed = compress_base64_image(data)
                    compression_ratio = len(compressed) / len(data)
                    # If we can achieve >30% reduction, use compression
                    if compression_ratio < 0.7:
                        output_format = "compressed"
                        break
                except Exception:
                    pass
            
            # If compression isn't enough, use file paths
            if output_format == "auto":
                output_format = "file_path"
        else:
            output_format = "base64"
        
        if ctx:
            logger.info(f"Selected output format: {output_format}")
    
    # Process images based on format
    result = {}
    
    for name, base64_data in working_images:
        if output_format == "file_path":
            # Save to file and return path
            filename = f"{name}_{uuid.uuid4().hex[:8]}.png"
            file_path = save_image_to_file(base64_data, filename, output_dir)
            result[name] = {
                "type": "file_path",
                "path": file_path,
                "mime_type": "image/png"
            }
            
        elif output_format == "compressed":
            # Compress and return base64
            try:
                compressed_data = compress_base64_image(base64_data)
                result[name] = {
                    "type": "base64_compressed", 
                    "data": compressed_data,
                    "mime_type": "image/png",
                    "compression_ratio": len(compressed_data) / len(base64_data)
                }
            except Exception as e:
                # Fallback to original if compression fails
                if ctx:
                    logger.warning(f"Compression failed for {name}: {e}")
                result[name] = {
                    "type": "base64",
                    "data": base64_data,
                    "mime_type": "image/png"
                }
                
        else:  # base64 format
            result[name] = {
                "type": "base64",
                "data": base64_data,
                "mime_type": "image/png"
            }
    
    # Return in original format
    if is_dict:
        # For backwards compatibility, if all are base64, return simple dict
        if all(v["type"] == "base64" for v in result.values()):
            return {k: v["data"] for k, v in result.items()}
        return result
    else:
        return list(result.values())



# View presets for common perspectives with distance=200
VIEW_PRESETS = {
    "front": ([0, -200, 0], [0, 0, 0], [0, 0, 1]),
    "back": ([0, 200, 0], [0, 0, 0], [0, 0, 1]),
    "left": ([-200, 0, 0], [0, 0, 0], [0, 0, 1]),
    "right": ([200, 0, 0], [0, 0, 0], [0, 0, 1]),
    "top": ([0, 0, 200], [0, 0, 0], [0, 1, 0]),
    "bottom": ([0, 0, -200], [0, 0, 0], [0, -1, 0]),
    "isometric": ([200, 200, 200], [0, 0, 0], [0, 0, 1]),
    "dimetric": ([200, 100, 200], [0, 0, 0], [0, 0, 1]),
}

# Views rendered by render_perspectives when none are requested.
DEFAULT_PERSPECTIVE_VIEWS = ("front", "top", "isometric")

# Quality presets mapping to OpenSCAD resolution variables
# OpenSCAD variable names, including its special variables. A leading $
# marks a special variable: $fn/$fa/$fs control tessellation, $t drives
# animation, $vpr/$vpt/$vpd the viewport. Rejecting them made the tool's own
# QUALITY_PRESETS unusable, since "draft" and "high" set $fn, $fa and $fs.
#
# Allowing $ is safe here: the name is handed to OpenSCAD as a single argv
# element ("-D", "name=value") with no shell in between, so $ is an ordinary
# character rather than an expansion. The rest of the name stays constrained.
VARIABLE_NAME_RE = re.compile(r'^\$?[a-zA-Z_][a-zA-Z0-9_]*$')

QUALITY_PRESETS = {
    "draft": {"$fn": 8, "$fa": 12, "$fs": 2},
    "normal": {},  # OpenSCAD defaults
    "high": {"$fn": 64, "$fa": 2, "$fs": 0.5},
}


@mcp.tool(output_schema=None)
async def render_single(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    view: Optional[str] = None,
    camera_position: Union[str, List[float], Dict[str, float], None] = None,
    camera_target: Union[str, List[float], Dict[str, float], None] = None,
    camera_up: Union[str, List[float], Dict[str, float], None] = None,
    image_size: Union[str, List[int], tuple, None] = None,
    color_scheme: str = "Cornfield",
    variables: Optional[Dict[str, Any]] = None,
    auto_center: bool = False,
    quality: Optional[str] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
):
    """
    Render a single view from OpenSCAD code or file.

    The image is returned together with OpenSCAD's diagnostics. Check the
    "errors" and "warnings" in the metadata: OpenSCAD exits 0 for a failed
    assert() or an unknown module and draws a blank scene, so the picture
    alone does not mean success.

    Args:
        scad_content: OpenSCAD code to render (mutually exclusive with scad_file)
        scad_file: Path to OpenSCAD file (mutually exclusive with scad_content)
        view: Predefined view name ("front", "back", "left", "right", "top", "bottom", "isometric", "dimetric")
        camera_position: Camera position - accepts [x,y,z] list, JSON string "[x,y,z]", or dict {"x":x,"y":y,"z":z} (default: [70, 70, 70])
        camera_target: Camera look-at point - accepts [x,y,z] list, JSON string, or dict (default: [0, 0, 0])
        camera_up: Camera up vector - accepts [x,y,z] list, JSON string, or dict (default: [0, 0, 1])
        image_size: Image dimensions - accepts [width, height] list, JSON string "[width, height]", "widthxheight", or tuple (default: [800, 600]; clamped to the configured maximum)
        color_scheme: OpenSCAD color scheme (default: "Cornfield")
        variables: Variables to pass to OpenSCAD
        auto_center: Fit the model in frame. Enabled automatically when no view and no explicit camera are given.
        quality: Quality preset - "draft" (fast, low detail), "normal" (OpenSCAD defaults), or "high" (slow, high detail). User-provided variables override quality preset values.
        include_paths: Additional include paths for OpenSCAD via the OPENSCADPATH environment variable, enabling multi-file project support
        ctx: MCP context for logging

    Returns:
        List containing the rendered PNG image and a JSON metadata block with
        success, errors, warnings, echo_output, hints, cached and image_tokens
    """
    if ctx:
        await ctx.info("Starting OpenSCAD render...")
    
    # Validate input
    if bool(scad_content) == bool(scad_file):
        raise ValueError("Exactly one of scad_content or scad_file must be provided")

    # With no view and no explicit camera the fixed default eye at
    # [70,70,70] leaves small parts as a thumbnail (a 2x3x1 mm cube filled
    # 0.2% of the frame). Fit the model unless the caller placed the camera.
    explicit_camera = camera_position is not None or camera_target is not None
    if not view and not explicit_camera and not auto_center:
        auto_center = True

    # If view keyword is provided, use preset camera settings
    if view:
        if view not in VIEW_PRESETS:
            raise ValueError(f"Invalid view name '{view}'. Must be one of: {', '.join(VIEW_PRESETS.keys())}")
        
        # Get preset camera settings
        preset_pos, preset_target, preset_up = VIEW_PRESETS[view]
        
        # Override camera parameters with preset values
        camera_position = list(preset_pos)
        camera_target = list(preset_target)
        camera_up = list(preset_up)
        
        # Auto-center is typically enabled for standard views
        if not auto_center:
            auto_center = True
            
        if ctx:
            await ctx.info(f"Using preset view '{view}' with camera position {camera_position}")
    else:
        # Parse camera parameters with proper defaults
        camera_position = parse_camera_param(camera_position, [70, 70, 70])
        camera_target = parse_camera_param(camera_target, [0, 0, 0])
        camera_up = parse_camera_param(camera_up, [0, 0, 1])
    
    # Parse image size with flexible formats
    image_size = parse_image_size_param(image_size, [800, 600])
    
    # Parse variables with flexible formats
    variables = parse_dict_param(variables, {})

    # Apply quality preset variables (user-provided variables take precedence)
    if quality:
        if quality not in QUALITY_PRESETS:
            raise ValueError(
                f"Invalid quality preset '{quality}'. "
                f"Must be one of: {', '.join(QUALITY_PRESETS.keys())}"
            )
        quality_vars = QUALITY_PRESETS[quality]
        if quality_vars:
            merged = dict(quality_vars)
            merged.update(variables)
            variables = merged

    try:
        # Run rendering in executor to avoid blocking the event loop
        async with get_render_semaphore():
            raw = await asyncio.get_running_loop().run_in_executor(
                None,
                render_scad_to_png,
                scad_content,
                scad_file,
                camera_position,
                camera_target,
                camera_up,
                image_size,
                color_scheme,
                variables,
                auto_center,
                include_paths,
            )
        result = _as_render_result(raw)
        meta = result.metadata()
        success = not meta.get("errors")

        if ctx:
            if success:
                await ctx.info("Rendering completed" + (" (cached)" if result.cached else ""))
            else:
                await ctx.warning(
                    f"Render produced {len(meta['errors'])} error(s); image returned with diagnostics"
                )

        # Return as MCPImage so FastMCP sends proper ImageContent to clients.
        # The image is returned even when diagnostics contain errors: the
        # picture plus the error is the useful signal.
        image_bytes = base64.b64decode(result.image_b64)
        meta.update({"success": success, "operation_id": str(uuid.uuid4())})
        return [
            MCPImage(data=image_bytes, format="png"),
            json.dumps(meta),
        ]

    except Exception as e:
        if ctx:
            await ctx.error(f"Rendering failed: {str(e)}")
        return [
            json.dumps({
                "success": False,
                "error": str(e),
                "operation_id": str(uuid.uuid4()),
            })
        ]


@mcp.tool(output_schema=None)
async def render_perspectives(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    views: Optional[List[str]] = None,
    image_size: Optional[str] = None,
    color_scheme: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    quality: Optional[str] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
):
    """
    Render multiple standard views of an OpenSCAD model in a single call.

    Renders the model from several predefined camera perspectives in parallel,
    returning all images at once. Useful for generating a comprehensive visual
    overview of a 3D model.

    Args:
        scad_content: OpenSCAD code to render (mutually exclusive with scad_file)
        scad_file: Path to OpenSCAD file (mutually exclusive with scad_content)
        views: List of view names to render. Valid names: "front", "back", "left",
            "right", "top", "bottom", "isometric", "dimetric". Default: "front",
            "top", "isometric" (each image costs roughly 640 vision tokens at
            800x600, so ask for more views only when they answer a question).
        image_size: Image dimensions - accepts "widthxheight", "width,height",
            "[width, height]", or [width, height] list (default: [800, 600])
        color_scheme: OpenSCAD color scheme (default: "Cornfield")
        variables: Variables to pass to OpenSCAD via -D flags
        quality: Quality preset - "draft" (fast, low detail), "normal" (OpenSCAD
            defaults), or "high" (slow, high detail). User-provided variables
            override quality preset values.
        include_paths: Additional include paths for OpenSCAD via the
            OPENSCADPATH environment variable, enabling multi-file
            project support
        ctx: MCP context for logging

    Returns:
        List of rendered PNG images and metadata
    """
    try:
        # Validate input
        if bool(scad_content) == bool(scad_file):
            raise ValueError(
                "Exactly one of scad_content or scad_file must be provided"
            )

        # Determine which views to render. Three views by default: the old
        # seven-view default cost ~4500 vision tokens per call.
        default_views = list(DEFAULT_PERSPECTIVE_VIEWS)
        if views is None:
            views = default_views
        else:
            # Parse views if provided as string
            views = parse_list_param(views, default_views)

        # Validate view names
        invalid_views = [v for v in views if v not in VIEW_PRESETS]
        if invalid_views:
            raise ValueError(
                f"Invalid view name(s): {', '.join(invalid_views)}. "
                f"Must be one of: {', '.join(VIEW_PRESETS.keys())}"
            )

        # Parse image size
        parsed_image_size = parse_image_size_param(image_size, [800, 600])

        # Parse variables
        parsed_variables = parse_dict_param(variables, {})

        # Apply quality preset variables (user-provided variables take precedence)
        if quality:
            if quality not in QUALITY_PRESETS:
                raise ValueError(
                    f"Invalid quality preset '{quality}'. "
                    f"Must be one of: {', '.join(QUALITY_PRESETS.keys())}"
                )
            quality_vars = QUALITY_PRESETS[quality]
            if quality_vars:
                merged = dict(quality_vars)
                merged.update(parsed_variables)
                parsed_variables = merged

        # Use provided color scheme or default
        resolved_color_scheme = color_scheme or "Cornfield"

        if ctx:
            await ctx.info(
                f"Rendering {len(views)} perspective(s): {', '.join(views)}"
            )

        # Define render function for a single view
        def _render_view(view_name: str) -> Tuple[str, Any]:
            """Render a single view, returning (view_name, result_or_error)."""
            preset_pos, preset_target, preset_up = VIEW_PRESETS[view_name]
            try:
                rendered = _as_render_result(render_scad_to_png(
                    scad_content=scad_content,
                    scad_file=scad_file,
                    camera_position=list(preset_pos),
                    camera_target=list(preset_target),
                    camera_up=list(preset_up),
                    image_size=parsed_image_size,
                    color_scheme=resolved_color_scheme,
                    variables=parsed_variables,
                    auto_center=True,
                    include_paths=include_paths,
                ))
                return (view_name, {"success": True, "result": rendered})
            except Exception as e:
                return (view_name, {"success": False, "error": str(e)})

        # Render all views in parallel, bounded by the configured concurrency
        loop = asyncio.get_running_loop()
        semaphore = get_render_semaphore()

        async def _guarded(view_name: str) -> Tuple[str, Any]:
            async with semaphore:
                return await loop.run_in_executor(None, _render_view, view_name)

        results = await asyncio.gather(*[_guarded(v) for v in views])

        # Collect successful renders and errors. Diagnostics are identical
        # across views of the same source, so report them once.
        response_items: list = []
        errors = {}
        success_count = 0
        diagnostics_meta: Dict[str, Any] = {}
        for view_name, result in results:
            if result["success"]:
                rendered = result["result"]
                image_bytes = base64.b64decode(rendered.image_b64)
                response_items.append(f"View: {view_name}")
                response_items.append(MCPImage(data=image_bytes, format="png"))
                success_count += 1
                if not diagnostics_meta:
                    diagnostics_meta = rendered.metadata()
            else:
                errors[view_name] = result["error"]

        if ctx:
            error_count = len(errors)
            msg = f"Rendered {success_count}/{len(views)} view(s) successfully"
            if error_count > 0:
                msg += f" ({error_count} failed)"
            await ctx.info(msg)

        # Add metadata summary
        summary: Dict[str, Any] = {
            "success": len(errors) == 0 and not diagnostics_meta.get("errors"),
            "count": success_count,
            "views": [v for v, r in results if r["success"]],
            "failed_views": errors if errors else None,
        }
        summary.update(diagnostics_meta)
        if success_count and diagnostics_meta.get("image_tokens"):
            summary["image_tokens"] = diagnostics_meta["image_tokens"] * success_count
        response_items.append(json.dumps(summary))

        return response_items

    except Exception as e:
        if ctx:
            await ctx.error(f"Render perspectives failed: {str(e)}")
        return [
            json.dumps({
                "success": False,
                "error": str(e),
            })
        ]


@mcp.tool
async def check_openscad(
    include_paths: bool = False,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Verify OpenSCAD installation and return version info.
    
    Args:
        include_paths: Include searched paths in response
        ctx: MCP context for logging
    
    Returns:
        Dict with OpenSCAD installation information
    """
    if ctx:
        await ctx.info("Checking OpenSCAD installation...")
    
    openscad_path = find_openscad()
    
    if not openscad_path:
        searched = list(_OPENSCAD_NAMES) + list(_OPENSCAD_COMMON_PATHS)
        return {
            "installed": False,
            "version": None,
            "path": None,
            "searched_paths": searched if include_paths else None,
            "message": (
                "OpenSCAD not found. Install the 2021.01 release or a dev snapshot "
                "from https://openscad.org/downloads.html, or set OPENSCAD_PATH."
            ),
        }

    record = get_openscad_capabilities(openscad_path)
    version = record.get("version") or "Unknown"
    
    if ctx:
        await ctx.info(f"Found OpenSCAD {version} at {openscad_path}")
    
    response: Dict[str, Any] = {
        "installed": True,
        "version": version,
        "path": str(openscad_path),
        "is_snapshot": record.get("is_snapshot", False),
        "capabilities": {
            k: record[k]
            for k in ("has_manifold_backend", "has_summary_json", "has_egl_headless", "amf_export")
            if k in record
        },
        "supported_export_formats": sorted(_supported_export_formats(record)),
        "message": f"OpenSCAD {version} is installed at {openscad_path}",
    }
    if not record.get("is_snapshot") and _version_tuple(version) <= (2021, 1):
        response["upgrade_hint"] = (
            "OpenSCAD 2021.01 is the last stable release; daily dev snapshots "
            "(Manifold backend, headless EGL rendering) are published at "
            "https://openscad.org/downloads.html and install alongside it as "
            "'openscad-nightly'."
        )
    if include_paths:
        response["library_paths"] = [str(p) for p in _library_search_paths() if p.exists()]
    return response


# ============================================================================
# Export Tool
# ============================================================================


# Formats OpenSCAD 2021.01 accepts for -o. dxf/svg/pdf need a 2D model;
# csg is the evaluated CSG tree (no geometry evaluation); nef3 is CGAL's
# native solid. AMF is removed in dev snapshots after 2025.
SUPPORTED_EXPORT_FORMATS = {"stl", "3mf", "amf", "off", "dxf", "svg", "csg", "nef3", "pdf"}

# Mesh formats where the CGAL statistics banner is printed and manifoldness
# can be judged.
_MESH_EXPORT_FORMATS = {"stl", "3mf", "amf", "off", "nef3"}


def _supported_export_formats(capabilities: Optional[Dict[str, Any]] = None) -> set:
    """Export formats for the detected binary (AMF only where it still exists)."""
    formats = set(SUPPORTED_EXPORT_FORMATS)
    if capabilities and capabilities.get("installed") and not capabilities.get("amf_export", True):
        formats.discard("amf")
    return formats


@dataclass
class EvalResult:
    """Outcome of one non-image OpenSCAD run (export, analyze, validate)."""

    returncode: int
    diagnostics: Diagnostics
    dependencies: List[str]
    output_path: Optional[Path]


def _evaluate_scad(
    scad_content: Optional[str],
    scad_file: Optional[str],
    output_target: str,
    export_format: Optional[str],
    variables: Optional[Dict[str, Any]],
    include_paths: Optional[List[str]],
    label: str,
    prefix: str,
) -> EvalResult:
    """Shared OpenSCAD invocation for export, analysis and validation.

    Performs every security check, writes inline content to a temp file,
    records the dependency closure with ``-d``, enforces ``allowed_paths``
    on that closure, and parses stderr into diagnostics. Callers decide what
    the exit code and diagnostics mean for their tool.
    """
    config = get_config()
    if scad_file:
        _check_allowed_path(scad_file, "File path")
    _validate_source_size(scad_content)
    _validate_variable_names(variables)
    _validate_include_paths(include_paths)

    openscad_cmd = find_openscad()
    if not openscad_cmd:
        raise RuntimeError("OpenSCAD not found. Please install OpenSCAD first.")

    temp_dir_path = Path(config.temp_dir)
    temp_dir_path.mkdir(parents=True, exist_ok=True)

    inline_path: Optional[str] = None
    cleanup: List[Path] = []
    if scad_content:
        tmp_input = temp_dir_path / f"{prefix}_{uuid.uuid4().hex[:8]}.scad"
        tmp_input.write_text(scad_content)
        scad_input_path = tmp_input
        inline_path = str(tmp_input)
        cleanup.append(tmp_input)
    else:
        scad_input_path = Path(scad_file or "")
        if not scad_input_path.exists():
            raise FileNotFoundError(f"SCAD file not found: {scad_file}")

    deps_path = temp_dir_path / f"{prefix}_{uuid.uuid4().hex[:8]}.d"
    cleanup.append(deps_path)

    cmd = [openscad_cmd]
    if config.rendering.hard_warnings:
        cmd.append("--hardwarnings")
    if export_format:
        cmd.append(f"--export-format={export_format}")
    cmd += ["-o", output_target, "-d", str(deps_path)]
    cmd.extend(_format_variables(variables))
    cmd.append(str(scad_input_path))

    try:
        result = _run_openscad(cmd, include_paths, label=label)
        diag = parse_openscad_output(result.stderr or "", result.returncode, inline_path)
        deps: List[str] = []
        if deps_path.exists():
            try:
                deps = parse_deps_file(deps_path.read_text(errors="replace"))
            except OSError:
                deps = []
        try:
            _check_dependency_closure(deps, scad_input_path, include_paths)
        except ValueError:
            # Withhold everything derived from the run, including any file.
            out = Path(output_target)
            if output_target not in ("/dev/null", "NUL") and out.exists():
                try:
                    out.unlink()
                except OSError:
                    pass
            raise
        out_path: Optional[Path] = None
        if output_target not in ("/dev/null", "NUL"):
            candidate = Path(output_target)
            out_path = candidate if candidate.exists() else None
        return EvalResult(result.returncode, diag, deps, out_path)
    finally:
        for f in cleanup:
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass


@mcp.tool()
async def export_model(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    output_format: str = "stl",
    output_path: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Export OpenSCAD code or file to a mesh, 2D, or CSG format.

    Mesh exports (stl, 3mf, amf, off, nef3) also return a "mesh_health"
    block from OpenSCAD's CGAL statistics: "manifold" is true, false, or
    null when OpenSCAD did not perform the check. A non-manifold result
    usually means parts touch along an edge or face; overlap them slightly.

    Args:
        scad_content: OpenSCAD code to export (mutually exclusive with scad_file)
        scad_file: Path to OpenSCAD file (mutually exclusive with scad_content)
        output_format: "stl", "3mf", "amf", "off", "nef3" (3D), "dxf", "svg",
            "pdf" (2D), or "csg" (evaluated CSG tree). Default "stl".
        output_path: Path to write the exported file. If not specified, a temp
            directory is used.
        variables: Variables to pass to OpenSCAD via -D flags
        include_paths: Additional include paths for OpenSCAD via the
            OPENSCADPATH environment variable
        ctx: MCP context for logging

    Returns:
        Dict with success status, output_path, format, file_size_bytes,
        mesh_health (mesh formats), warnings, errors, and hints
    """
    try:
        # Validate exactly one input source
        if bool(scad_content) == bool(scad_file):
            raise ValueError(
                "Exactly one of scad_content or scad_file must be provided"
            )

        # Validate output format against what the detected binary supports
        fmt = output_format.lower()
        supported = _supported_export_formats(get_openscad_capabilities())
        if fmt not in supported:
            raise ValueError(
                f"Unsupported format '{output_format}'. "
                f"Must be one of: {', '.join(sorted(supported))}"
            )

        config = get_config()
        temp_dir_path = Path(config.temp_dir)
        temp_dir_path.mkdir(parents=True, exist_ok=True)

        # Determine output file path
        if output_path:
            final_output = Path(output_path)
            _check_allowed_path(final_output.parent, "Output directory")
            final_output.parent.mkdir(parents=True, exist_ok=True)
        else:
            export_dir = temp_dir_path / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            final_output = export_dir / f"export_{uuid.uuid4().hex[:8]}.{fmt}"

        if ctx:
            await ctx.info(f"Exporting to {fmt}...")

        async with get_render_semaphore():
            ev = await asyncio.get_running_loop().run_in_executor(
                None,
                _evaluate_scad,
                scad_content,
                scad_file,
                str(final_output),
                None,
                variables,
                include_paths,
                "export",
                "input",
            )

        diag = ev.diagnostics
        if ev.returncode != 0 or ev.output_path is None:
            detail = "; ".join(diag.errors) if diag.errors else "OpenSCAD did not produce output file"
            response: Dict[str, Any] = {
                "success": False,
                "error": f"OpenSCAD export failed: {detail}",
                "format": fmt,
            }
            response.update(diag.to_dict(include_records=False))
            if diag.empty_output:
                response["empty_output"] = True
            return response

        file_size = ev.output_path.stat().st_size

        if ctx:
            await ctx.info(
                f"Export complete: {ev.output_path} ({file_size} bytes)"
            )

        response = {
            "success": diag.ok,
            "output_path": str(ev.output_path),
            "format": fmt,
            "file_size_bytes": file_size,
        }
        if fmt in _MESH_EXPORT_FORMATS:
            response["mesh_health"] = diag.mesh_health()
        response.update(diag.to_dict(include_records=False))
        return response

    except Exception as e:
        if ctx:
            await ctx.error(f"Export failed: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


# ============================================================================
# Model Management Tools
# ============================================================================


def _validate_model_name(name: str) -> str:
    """
    Validate and normalize a model file name.

    Ensures the name contains only safe characters (alphanumeric, hyphens,
    underscores, dots) and ends with .scad.

    Args:
        name: The model file name to validate

    Returns:
        The validated and normalized name (with .scad extension)

    Raises:
        ValueError: If the name contains invalid characters or path traversal
    """
    # Reject path traversal
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(
            f"Invalid model name '{name}': must not contain path separators "
            f"or '..'"
        )

    # Strip .scad extension for validation, then re-add
    base = name.removesuffix(".scad")

    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]*$', base):
        raise ValueError(
            f"Invalid model name '{name}': must start with alphanumeric and "
            f"contain only alphanumeric, hyphens, and underscores"
        )

    if not name.endswith(".scad"):
        name = name + ".scad"

    return name


def _resolve_workspace(workspace: Optional[str] = None) -> Path:
    """
    Resolve the workspace directory path.

    Uses the provided workspace path or defaults to the configured temp_dir
    models subdirectory. Creates the directory if it does not exist.

    Args:
        workspace: Optional workspace directory path

    Returns:
        Resolved Path to the workspace directory

    Raises:
        ValueError: If the workspace path contains path traversal sequences
    """
    config = get_config()

    if workspace:
        if ".." in workspace:
            raise ValueError(
                "Workspace path must not contain '..'"
            )
        ws = Path(workspace).resolve()
    else:
        ws = Path(config.temp_dir) / "models"

    ws.mkdir(parents=True, exist_ok=True)
    return ws


@mcp.tool()
async def create_model(
    name: str,
    content: str,
    workspace: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Create a new OpenSCAD model file.

    Args:
        name: File name for the model (alphanumeric, hyphens, underscores;
            .scad extension added automatically if missing)
        content: OpenSCAD source code for the model
        workspace: Directory to save the model in. Defaults to the configured
            temp_dir/models directory.
        ctx: MCP context for logging

    Returns:
        Dict with success status, path, and name of the created file
    """
    try:
        name = _validate_model_name(name)
        ws = _resolve_workspace(workspace)
        file_path = ws / name

        if file_path.exists():
            raise ValueError(
                f"Model '{name}' already exists at {file_path}. "
                f"Use update_model to modify it."
            )

        file_path.write_text(content)

        if ctx:
            await ctx.info(f"Created model: {file_path}")

        return {
            "success": True,
            "path": str(file_path),
            "name": name,
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Failed to create model: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def get_model(
    name: str,
    workspace: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Read an OpenSCAD model file and return its contents.

    Args:
        name: File name of the model to read
        workspace: Directory containing the model. Defaults to the configured
            temp_dir/models directory.
        ctx: MCP context for logging

    Returns:
        Dict with success status, name, content, path, and size_bytes
    """
    try:
        name = _validate_model_name(name)
        ws = _resolve_workspace(workspace)
        file_path = ws / name

        if not file_path.exists():
            raise FileNotFoundError(
                f"Model '{name}' not found at {file_path}"
            )

        content = file_path.read_text()
        size = file_path.stat().st_size

        if ctx:
            await ctx.info(f"Read model: {file_path} ({size} bytes)")

        return {
            "success": True,
            "name": name,
            "content": content,
            "path": str(file_path),
            "size_bytes": size,
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Failed to read model: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def update_model(
    name: str,
    content: str,
    workspace: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Update an existing OpenSCAD model file with new content.

    The file must already exist. Use create_model to create new files.

    Args:
        name: File name of the model to update
        content: New OpenSCAD source code for the model
        workspace: Directory containing the model. Defaults to the configured
            temp_dir/models directory.
        ctx: MCP context for logging

    Returns:
        Dict with success status, path, and name of the updated file
    """
    try:
        name = _validate_model_name(name)
        ws = _resolve_workspace(workspace)
        file_path = ws / name

        if not file_path.exists():
            raise FileNotFoundError(
                f"Model '{name}' not found at {file_path}. "
                f"Use create_model to create it first."
            )

        file_path.write_text(content)

        if ctx:
            await ctx.info(f"Updated model: {file_path}")

        return {
            "success": True,
            "path": str(file_path),
            "name": name,
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Failed to update model: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def list_models(
    workspace: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    List all OpenSCAD model files in the workspace directory.

    Args:
        workspace: Directory to list models from. Defaults to the configured
            temp_dir/models directory.
        ctx: MCP context for logging

    Returns:
        Dict with success status, list of models (name, path, size_bytes,
        modified), and count
    """
    try:
        ws = _resolve_workspace(workspace)
        models = []

        for scad_file in sorted(ws.glob("*.scad")):
            stat = scad_file.stat()
            models.append({
                "name": scad_file.name,
                "path": str(scad_file),
                "size_bytes": stat.st_size,
                "modified": stat.st_mtime,
            })

        if ctx:
            await ctx.info(
                f"Found {len(models)} model(s) in {ws}"
            )

        return {
            "success": True,
            "models": models,
            "count": len(models),
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Failed to list models: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def delete_model(
    name: str,
    workspace: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Delete an OpenSCAD model file from the workspace.

    The file must exist. Returns the path of the deleted file.

    Args:
        name: File name of the model to delete
        workspace: Directory containing the model. Defaults to the configured
            temp_dir/models directory.
        ctx: MCP context for logging

    Returns:
        Dict with success status, name, and deleted_path
    """
    try:
        name = _validate_model_name(name)
        ws = _resolve_workspace(workspace)
        file_path = ws / name

        if not file_path.exists():
            raise FileNotFoundError(
                f"Model '{name}' not found at {file_path}"
            )

        deleted_path = str(file_path)
        file_path.unlink()

        if ctx:
            await ctx.info(f"Deleted model: {deleted_path}")

        return {
            "success": True,
            "name": name,
            "deleted_path": deleted_path,
        }

    except Exception as e:
        if ctx:
            await ctx.error(f"Failed to delete model: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


# ============================================================================
# Validation, Analysis, Libraries, and Comparison Tools
# ============================================================================


def _parse_openscad_stderr(
    stderr: str, returncode: Optional[int] = None
) -> Dict[str, List[str]]:
    """
    Parse OpenSCAD stderr output into categorized message lists.

    Thin compatibility wrapper over :func:`parse_openscad_output`. Returns
    the classic dict with "errors", "warnings", "echo_output" and
    "deprecated" lists; callers that need locations, call stacks, hints or
    CGAL statistics should use the Diagnostics object directly.
    """
    diag = parse_openscad_output(stderr, returncode)
    return {
        "errors": diag.errors,
        "warnings": diag.warnings,
        "echo_output": diag.echo_output,
        "deprecated": diag.deprecated,
    }


def _parse_stl_vertices(stl_path: Path) -> List[List[float]]:
    """
    Parse vertex coordinates from an STL file (ASCII or binary).

    Detects the STL format automatically and extracts all vertex
    coordinates. For binary STL, reads the 80-byte header and
    triangle count, then iterates facets. For ASCII STL, uses
    regex matching on vertex lines.

    Args:
        stl_path: Path to the STL file to parse

    Returns:
        List of [x, y, z] vertex coordinate lists

    Raises:
        ValueError: If the STL file cannot be parsed
    """
    with open(stl_path, "rb") as f:
        header = f.read(80)

    # Detect ASCII vs binary: ASCII STL starts with "solid"
    is_ascii = header[:5] == b"solid"

    vertices = []

    if is_ascii:
        text = stl_path.read_text(errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("vertex"):
                parts = stripped.split()
                if len(parts) == 4:
                    try:
                        vertices.append([
                            float(parts[1]),
                            float(parts[2]),
                            float(parts[3]),
                        ])
                    except ValueError:
                        continue
    else:
        # Binary STL format:
        # 80 bytes header, 4 bytes triangle count,
        # then per triangle: 12 bytes normal + 3x12 bytes vertices
        # + 2 bytes attribute
        with open(stl_path, "rb") as f:
            f.read(80)  # skip header
            count_data = f.read(4)
            if len(count_data) < 4:
                raise ValueError("Invalid binary STL: too short")
            tri_count = struct.unpack("<I", count_data)[0]

            for _ in range(tri_count):
                # Skip normal vector (3 floats = 12 bytes)
                f.read(12)
                # Read 3 vertices (each 3 floats = 12 bytes)
                for _ in range(3):
                    vdata = f.read(12)
                    if len(vdata) < 12:
                        raise ValueError(
                            "Invalid binary STL: unexpected end of file"
                        )
                    x, y, z = struct.unpack("<fff", vdata)
                    vertices.append([x, y, z])
                # Skip attribute byte count (2 bytes)
                f.read(2)

    return vertices


@mcp.tool()
async def validate_scad(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Syntax-check and evaluate OpenSCAD code without rendering geometry.

    Runs OpenSCAD with CSG output directed to /dev/null (NUL on Windows)
    so it only parses and evaluates the code. Much faster than a full
    render. Returns categorized ECHO, WARNING, ERROR, and DEPRECATED
    messages with file/line locations and repair hints. "valid" is false
    whenever an ERROR was reported, regardless of OpenSCAD's exit code.

    Args:
        scad_content: OpenSCAD code to validate (mutually exclusive
            with scad_file)
        scad_file: Path to OpenSCAD file to validate (mutually
            exclusive with scad_content)
        variables: Variables to pass to OpenSCAD via -D flags
        include_paths: Additional include paths for OpenSCAD via the
            OPENSCADPATH environment variable
        ctx: MCP context for logging

    Returns:
        Dict with success status, valid flag, errors, warnings,
        echo_output, deprecated, hints, and unresolved_includes
    """
    try:
        # Validate exactly one input source
        if bool(scad_content) == bool(scad_file):
            raise ValueError(
                "Exactly one of scad_content or scad_file "
                "must be provided"
            )

        # Output to /dev/null (NUL on Windows). csg rather than a mesh
        # format: validation only needs the script parsed and evaluated,
        # and asking for a mesh forces full CGAL geometry evaluation
        # instead -- 19s versus 80ms on a real model here. A mesh format
        # also fails outright on valid input that produces no 3D solid.
        null_output = (
            "NUL" if platform.system() == "Windows"
            else "/dev/null"
        )

        if ctx:
            await ctx.info("Validating OpenSCAD code...")

        async with get_render_semaphore():
            ev = await asyncio.get_running_loop().run_in_executor(
                None,
                _evaluate_scad,
                scad_content,
                scad_file,
                null_output,
                "csg",
                variables,
                include_paths,
                "validation",
                "validate",
            )

        diag = ev.diagnostics
        is_valid = diag.ok

        if ctx:
            status = "valid" if is_valid else "invalid"
            await ctx.info(
                f"Validation complete: {status} "
                f"({len(diag.errors)} error(s), "
                f"{len(diag.warnings)} warning(s))"
            )

        response: Dict[str, Any] = {"success": True, "valid": is_valid}
        response.update(diag.to_dict(include_records=True))
        missing = unresolved_includes(diag)
        if missing:
            response["unresolved_includes"] = missing
        return response

    except Exception as e:
        if ctx:
            await ctx.error(f"Validation failed: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def analyze_model(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Extract geometric information from an OpenSCAD model.

    Exports the model to a temporary STL file, then parses vertex
    data to compute bounding box, dimensions, center point, and
    triangle count. Also returns "mesh_health" from OpenSCAD's CGAL
    statistics ("manifold" true/false/null) and any warnings or errors
    OpenSCAD reported, which the numbers alone would hide.

    Args:
        scad_content: OpenSCAD code to analyze (mutually exclusive
            with scad_file)
        scad_file: Path to OpenSCAD file to analyze (mutually
            exclusive with scad_content)
        variables: Variables to pass to OpenSCAD via -D flags
        include_paths: Additional include paths for OpenSCAD via the
            OPENSCADPATH environment variable
        ctx: MCP context for logging

    Returns:
        Dict with success status, bounding_box (min/max xyz),
        dimensions (width/height/depth), center point, triangle_count,
        mesh_health, warnings, errors, and hints
    """
    try:
        # Validate exactly one input source
        if bool(scad_content) == bool(scad_file):
            raise ValueError(
                "Exactly one of scad_content or scad_file "
                "must be provided"
            )

        config = get_config()
        temp_dir_path = Path(config.temp_dir)
        temp_dir_path.mkdir(parents=True, exist_ok=True)
        stl_output = temp_dir_path / f"analyze_{uuid.uuid4().hex[:8]}.stl"

        if ctx:
            await ctx.info("Analyzing model geometry...")

        try:
            async with get_render_semaphore():
                ev = await asyncio.get_running_loop().run_in_executor(
                    None,
                    _evaluate_scad,
                    scad_content,
                    scad_file,
                    str(stl_output),
                    None,
                    variables,
                    include_paths,
                    "export",
                    "analyze",
                )

            diag = ev.diagnostics
            if ev.returncode != 0 or ev.output_path is None:
                detail = "; ".join(diag.errors) if diag.errors else (
                    "OpenSCAD did not produce STL output file"
                )
                response: Dict[str, Any] = {
                    "success": False,
                    "error": f"OpenSCAD export failed: {detail}",
                }
                response.update(diag.to_dict(include_records=False))
                if diag.empty_output:
                    response["empty_output"] = True
                return response

            vertices = _parse_stl_vertices(ev.output_path)
        finally:
            # Always clean up temp STL
            if stl_output.exists():
                stl_output.unlink()

        if not vertices:
            raise ValueError(
                "No vertices found in exported STL. "
                "The model may be empty."
            )

        # Calculate bounding box
        xs = [v[0] for v in vertices]
        ys = [v[1] for v in vertices]
        zs = [v[2] for v in vertices]

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)

        width = max_x - min_x
        height = max_y - min_y
        depth = max_z - min_z

        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        center_z = (min_z + max_z) / 2.0

        # Triangle count = vertices / 3 (each triangle has 3 verts)
        triangle_count = len(vertices) // 3

        if ctx:
            await ctx.info(
                f"Analysis complete: {triangle_count} triangles, "
                f"dimensions {width:.2f} x {height:.2f} x "
                f"{depth:.2f}"
            )

        response = {
            "success": diag.ok,
            "bounding_box": {
                "min": [min_x, min_y, min_z],
                "max": [max_x, max_y, max_z],
            },
            "dimensions": {
                "width": width,
                "height": height,
                "depth": depth,
            },
            "center": [center_x, center_y, center_z],
            "triangle_count": triangle_count,
            "mesh_health": diag.mesh_health(),
        }
        response.update(diag.to_dict(include_records=False))
        return response

    except Exception as e:
        if ctx:
            await ctx.error(f"Analysis failed: {str(e)}")
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool()
async def get_libraries(
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Discover installed OpenSCAD libraries on the system.

    Searches standard OpenSCAD library paths for the current
    platform, plus the OPENSCADPATH environment variable. For each
    found library directory, lists subdirectories as libraries and
    reports file counts, README presence, and main entry files.

    This is a read-only operation that does not require OpenSCAD
    to be installed.

    Args:
        ctx: MCP context for logging

    Returns:
        Dict with success status, library_paths searched, and
        libraries list with name, path, file_count, has_readme,
        and main_files for each library
    """
    try:
        # Determine library search paths based on platform
        search_paths = _library_search_paths()

        if ctx:
            await ctx.info(
                f"Searching {len(search_paths)} library path(s)..."
            )

        # Scan each path for libraries
        found_paths = []
        libraries = []

        for lib_dir in search_paths:
            if not lib_dir.exists() or not lib_dir.is_dir():
                continue

            found_paths.append(str(lib_dir))

            # Each subdirectory is potentially a library
            for entry in sorted(lib_dir.iterdir()):
                if not entry.is_dir():
                    # Also check for top-level .scad files
                    continue

                # Count .scad files in the library
                scad_files = list(entry.rglob("*.scad"))
                file_count = len(scad_files)

                # Check for README files
                readme_names = [
                    "README", "README.md", "README.txt",
                    "readme.md", "readme.txt",
                ]
                has_readme = any(
                    (entry / rn).exists() for rn in readme_names
                )

                # Identify main entry files
                main_file_candidates = [
                    "std.scad", "main.scad", "lib.scad",
                    f"{entry.name}.scad",
                ]
                main_files = [
                    mf for mf in main_file_candidates
                    if (entry / mf).exists()
                ]

                libraries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "file_count": file_count,
                    "has_readme": has_readme,
                    "main_files": main_files,
                })

        if ctx:
            await ctx.info(
                f"Found {len(libraries)} library(ies) in "
                f"{len(found_paths)} path(s)"
            )

        return {
            "success": True,
            "library_paths": found_paths,
            "libraries": libraries,
        }

    except Exception as e:
        if ctx:
            await ctx.error(
                f"Library discovery failed: {str(e)}"
            )
        return {
            "success": False,
            "error": str(e),
        }


@mcp.tool(output_schema=None)
async def compare_renders(
    scad_content_before: Optional[str] = None,
    scad_content_after: Optional[str] = None,
    scad_file: Optional[str] = None,
    variables_before: Optional[Dict[str, Any]] = None,
    variables_after: Optional[Dict[str, Any]] = None,
    view: Optional[str] = "isometric",
    image_size: Optional[str] = None,
    quality: Optional[str] = "draft",
    ctx: Optional[Context] = None,
):
    """
    Render two versions of a model for visual comparison.

    Supports two modes:
    1. Two different SCAD contents: provide scad_content_before and
       scad_content_after.
    2. Same file with different variables: provide scad_file with
       variables_before and variables_after.

    Both versions are rendered in parallel for efficiency. Uses
    the existing render_scad_to_png helper and QUALITY_PRESETS.

    Args:
        scad_content_before: OpenSCAD code for the "before" version
        scad_content_after: OpenSCAD code for the "after" version
        scad_file: Path to OpenSCAD file (used with variable diffs)
        variables_before: Variables for the "before" render
        variables_after: Variables for the "after" render
        view: View preset name (default: "isometric"). Valid names:
            "front", "back", "left", "right", "top", "bottom",
            "isometric", "dimetric"
        image_size: Image dimensions - accepts "widthxheight",
            "width,height", "[width, height]", or [width, height]
            list (default: [800, 600])
        quality: Quality preset - "draft", "normal", or "high"
            (default: "draft")
        ctx: MCP context for logging

    Returns:
        List with before/after images and metadata
    """
    try:
        # Validate input combinations
        has_both_contents = (
            scad_content_before is not None
            and scad_content_after is not None
        )
        has_file_with_vars = (
            scad_file is not None
            and variables_before is not None
            and variables_after is not None
        )

        if not has_both_contents and not has_file_with_vars:
            raise ValueError(
                "Provide either (scad_content_before + "
                "scad_content_after) or (scad_file + "
                "variables_before + variables_after)"
            )

        # Validate view preset
        if view and view not in VIEW_PRESETS:
            raise ValueError(
                f"Invalid view name '{view}'. "
                f"Must be one of: "
                f"{', '.join(VIEW_PRESETS.keys())}"
            )

        # Validate quality preset
        if quality and quality not in QUALITY_PRESETS:
            raise ValueError(
                f"Invalid quality preset '{quality}'. "
                f"Must be one of: "
                f"{', '.join(QUALITY_PRESETS.keys())}"
            )

        # Parse image size
        parsed_image_size = parse_image_size_param(
            image_size, [800, 600]
        )

        # Get camera settings from view preset
        if view:
            preset_pos, preset_target, preset_up = (
                VIEW_PRESETS[view]
            )
            cam_pos = list(preset_pos)
            cam_target = list(preset_target)
            cam_up = list(preset_up)
        else:
            cam_pos = [200, 200, 200]
            cam_target = [0, 0, 0]
            cam_up = [0, 0, 1]

        # Build quality variables
        quality_vars = {}
        if quality:
            quality_vars = dict(QUALITY_PRESETS.get(quality, {}))

        # Prepare before/after render parameters
        if has_both_contents:
            before_content = scad_content_before
            after_content = scad_content_after
            before_file = None
            after_file = None
            before_vars = dict(quality_vars)
            after_vars = dict(quality_vars)
            if variables_before:
                before_vars.update(variables_before)
            if variables_after:
                after_vars.update(variables_after)
        else:
            before_content = None
            after_content = None
            before_file = scad_file
            after_file = scad_file
            before_vars = dict(quality_vars)
            before_vars.update(variables_before)
            after_vars = dict(quality_vars)
            after_vars.update(variables_after)

        if ctx:
            await ctx.info(
                "Rendering before and after versions in parallel..."
            )

        # Define render functions for before and after
        def _render_before():
            return render_scad_to_png(
                scad_content=before_content,
                scad_file=before_file,
                camera_position=cam_pos,
                camera_target=cam_target,
                camera_up=cam_up,
                image_size=parsed_image_size,
                color_scheme="Cornfield",
                variables=before_vars if before_vars else None,
                auto_center=True,
            )

        def _render_after():
            return render_scad_to_png(
                scad_content=after_content,
                scad_file=after_file,
                camera_position=cam_pos,
                camera_target=cam_target,
                camera_up=cam_up,
                image_size=parsed_image_size,
                color_scheme="Cornfield",
                variables=after_vars if after_vars else None,
                auto_center=True,
            )

        # Render both in parallel, bounded by the configured concurrency
        loop = asyncio.get_running_loop()
        semaphore = get_render_semaphore()

        async def _guarded(fn):
            async with semaphore:
                return await loop.run_in_executor(None, fn)

        before_raw, after_raw = await asyncio.gather(
            _guarded(_render_before), _guarded(_render_after)
        )
        before_res = _as_render_result(before_raw)
        after_res = _as_render_result(after_raw)

        if ctx:
            await ctx.info("Comparison renders completed")

        before_bytes = base64.b64decode(before_res.image_b64)
        after_bytes = base64.b64decode(after_res.image_b64)

        before_meta = before_res.metadata()
        after_meta = after_res.metadata()
        return [
            "Before:",
            MCPImage(data=before_bytes, format="png"),
            "After:",
            MCPImage(data=after_bytes, format="png"),
            json.dumps({
                "success": not before_meta.get("errors") and not after_meta.get("errors"),
                "view": view or "isometric",
                "quality": quality or "draft",
                "before": before_meta,
                "after": after_meta,
            }),
        ]

    except Exception as e:
        if ctx:
            await ctx.error(
                f"Comparison render failed: {str(e)}"
            )
        return [
            json.dumps({
                "success": False,
                "error": str(e),
            })
        ]


# ============================================================================
# Cache Management Tools
# ============================================================================


@mcp.tool()
async def clear_cache(
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Delete all cached render files and report freed space.

    Removes every cached image and its dependency manifest from the
    configured cache directory.
    Does nothing (and still reports success) when the cache is disabled
    or the directory does not exist.

    Args:
        ctx: MCP context for logging

    Returns:
        Dict with success status, cleared_files count, and freed_bytes
    """
    config = get_config()
    cache_dir = config.cache.directory

    if not cache_dir.exists():
        if ctx:
            await ctx.info("Cache directory does not exist; nothing to clear")
        return {
            "success": True,
            "cleared_files": 0,
            "freed_bytes": 0,
        }

    cleared = 0
    freed = 0
    for f in list(cache_dir.glob("*.png")) + list(cache_dir.glob("*.json")):
        try:
            size = f.stat().st_size
            f.unlink()
            if f.suffix == ".png":
                cleared += 1
            freed += size
        except OSError as exc:
            logger.warning("Failed to delete cache file %s: %s", f, exc)

    if ctx:
        await ctx.info(
            f"Cleared {cleared} cached file(s), freed {freed} bytes"
        )

    return {
        "success": True,
        "cleared_files": cleared,
        "freed_bytes": freed,
    }


# ============================================================================
# Multi-file Project Tools
# ============================================================================


def _extract_scad_dependencies(file_path: Path) -> List[str]:
    """Return the file references written in an OpenSCAD source file.

    Finds ``include <...>`` / ``use <...>`` anywhere in the file (with
    trailing comments, several per line) plus ``import("...")`` and
    ``surface(file="...")``. Comments are stripped first.

    Args:
        file_path: Path to the ``.scad`` file to parse.

    Returns:
        List of dependency path strings as written in the source.
    """
    try:
        text = file_path.read_text(errors="replace")
    except OSError:
        return []
    return extract_source_dependencies(text)


@mcp.tool()
async def get_project_files(
    project_dir: str,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    List all OpenSCAD files in a project directory and map their dependencies.

    Recursively finds every ``.scad`` file under *project_dir*, parses
    each file for ``include`` and ``use`` statements, and returns a
    structured overview of the project's file tree and dependency graph.

    Args:
        project_dir: Root directory of the OpenSCAD project. Validated
            against ``security.allowed_paths`` when configured.
        ctx: MCP context for logging

    Returns:
        Dict with success status, files list (each with name, path,
        size_bytes, modified), and dependencies mapping (relative path
        to list of dependency strings).
    """
    try:
        config = get_config()
        resolved_dir = Path(project_dir).resolve()

        # Security: validate against allowed_paths
        if config.security.allowed_paths:
            if not any(
                _is_within(resolved_dir, ap)
                for ap in config.security.allowed_paths
            ):
                raise ValueError(
                    f"Project directory '{project_dir}' is not within "
                    f"allowed paths: {config.security.allowed_paths}"
                )

        if not resolved_dir.exists():
            raise FileNotFoundError(
                f"Project directory not found: {project_dir}"
            )
        if not resolved_dir.is_dir():
            raise ValueError(
                f"Path is not a directory: {project_dir}"
            )

        files_info: List[Dict[str, Any]] = []
        dependencies: Dict[str, List[str]] = {}

        for scad_file in sorted(resolved_dir.rglob("*.scad")):
            try:
                stat = scad_file.stat()
            except OSError:
                continue

            rel = str(scad_file.relative_to(resolved_dir))
            files_info.append({
                "name": scad_file.name,
                "path": str(scad_file),
                "relative_path": rel,
                "size_bytes": stat.st_size,
                "modified": stat.st_mtime,
            })

            deps = _extract_scad_dependencies(scad_file)
            if deps:
                dependencies[rel] = deps

        if ctx:
            await ctx.info(
                f"Found {len(files_info)} .scad file(s) in {project_dir}"
            )

        return {
            "success": True,
            "files": files_info,
            "dependencies": dependencies,
        }

    except Exception as e:
        if ctx:
            await ctx.error(
                f"Failed to scan project files: {str(e)}"
            )
        return {
            "success": False,
            "error": str(e),
        }


# ============================================================================
# MCP Resources
# ============================================================================


@mcp.resource("resource://server/info")
async def get_server_info() -> Dict[str, Any]:
    """Get server configuration and capabilities."""
    config = get_config()
    # check_openscad is a FastMCP FunctionTool once decorated; call the
    # wrapped function. Calling the tool object raised TypeError on every
    # read of this resource.
    check_fn = getattr(check_openscad, "fn", check_openscad)
    openscad_info = await check_fn()

    return {
        "version": config.server.version,
        "openscad_version": openscad_info.get("version"),
        "openscad_path": openscad_info.get("path"),
        "openscad_capabilities": openscad_info.get("capabilities"),
        "max_concurrent_renders": config.rendering.max_concurrent,
        "cache_enabled": config.cache.enabled,
        "allowed_paths": config.security.allowed_paths,
        "path_validation_enabled": bool(config.security.allowed_paths),
        "supported_formats": ["png"] + sorted(
            _supported_export_formats(get_openscad_capabilities())
        ),
    }


# ============================================================================
# Main Entry Point
# ============================================================================


def main():
    """Main entry point for the server."""
    import sys
    
    config = get_config()

    # Check for OpenSCAD on startup. Never print to stdout: on the stdio
    # transport that is the JSON-RPC channel.
    if not find_openscad():
        logger.warning(
            "OpenSCAD not found. Install it from https://openscad.org/downloads.html "
            "or set OPENSCAD_PATH."
        )

    if not config.security.allowed_paths:
        logger.warning(
            "security.allowed_paths is not set: no path validation is performed and "
            "scripts may read any file the server can. Set MCP_ALLOWED_PATHS or "
            "security.allowed_paths to confine reads to project directories."
        )
    
    if config.server.transport == "stdio":
        mcp.run()
    else:
        # For HTTP/SSE transport
        mcp.run(
            transport=config.server.transport.value,
            host=config.server.host,
            port=config.server.port,
        )


if __name__ == "__main__":
    main()