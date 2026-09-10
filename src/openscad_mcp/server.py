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
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from fastmcp import Context, FastMCP
from fastmcp.utilities.types import Image as MCPImage
from PIL import Image as PILImage

from .assembly import (  # noqa: F401
    Assembly,
    AssemblyError,
    Part,
    assembly_digest,
    load_check_file,
    parse_assembly,
    parse_parts,
)
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


_FALLBACK_INSTRUCTIONS = (
    "OpenSCAD design server. Units are millimetres, Z is up, right-handed. "
    "Workflow: validate(mode=syntax) -> measure(mode=model) -> render(grounded=true) "
    "with 1-3 views -> iterate by changing a variable and re-measuring -> export_model. "
    "Trust numbers from measure over pictures; check errors/warnings/hints in every "
    "response, since OpenSCAD exits 0 on failed asserts and unknown modules. "
    "reference(topic=fits|fasteners|inserts|joints|dfm) has sourced clearances."
)


def _server_instructions() -> str:
    """Conventions brief sent to clients as MCP server instructions."""
    try:
        from .reference import conventions_brief

        return conventions_brief()
    except Exception:  # pragma: no cover - reference data optional at import
        return _FALLBACK_INSTRUCTIONS


# Initialize the FastMCP server
mcp = FastMCP("OpenSCAD MCP Server", instructions=_server_instructions())


def _tool_fn(tool: Any) -> Any:
    """Return the plain coroutine behind a registered tool.

    fastmcp 2.x wraps decorated functions in a FunctionTool (callable via
    ``.fn``); fastmcp 4.x returns the function itself. Internal callers use
    this so both resolve.
    """
    return getattr(tool, "fn", tool)


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
    if _is_within(resolved, config.temp_dir):
        return  # the server's own temp dir (wrapper programs, inline content)
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
    """Delete the oldest cache entries until total size is within limits.

    An entry is every file sharing one key: ``<key>.png`` + ``<key>.json`` for
    renders at the top level, and ``parts/<key>.stl`` + ``.json`` + ``.csg``
    for per-part meshes. Entries are evicted whole, oldest first, so a mesh
    never outlives its manifest and the size cap covers the parts cache too.
    """
    config = get_config()
    if not config.cache.enabled:
        return

    cache_dir = config.cache.directory
    if not cache_dir.exists():
        return

    max_bytes = config.cache.max_size_mb * 1024 * 1024

    # Group files by (directory, stem); each group is one cache entry.
    entries: Dict[Tuple[Path, str], List[Tuple[Path, int]]] = {}
    newest: Dict[Tuple[Path, str], float] = {}
    total_size = 0
    candidates = list(cache_dir.glob("*.png")) + list(cache_dir.glob("*.json"))
    parts_dir = cache_dir / "parts"
    if parts_dir.is_dir():
        candidates += [f for f in parts_dir.iterdir() if f.suffix in (".stl", ".json", ".csg")]
    for f in candidates:
        try:
            stat = f.stat()
        except OSError:
            continue
        key = (f.parent, f.stem)
        entries.setdefault(key, []).append((f, stat.st_size))
        newest[key] = max(newest.get(key, 0.0), stat.st_mtime)
        total_size += stat.st_size

    if total_size <= max_bytes:
        return

    # Oldest entry first (by its most recently touched file)
    for key in sorted(entries, key=lambda k: newest[k]):
        if total_size <= max_bytes:
            break
        for file_path, file_size in entries[key]:
            try:
                file_path.unlink()
                total_size -= file_size
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
    projection: Optional[str] = None,
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
    if projection:
        if projection not in ("o", "p", "ortho", "perspective", "orthogonal"):
            raise ValueError("projection must be 'o' (orthographic) or 'p' (perspective)")
        projection = "o" if projection.startswith("o") else "p"
        binary_identity += f"|projection={projection}"

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

        if projection:
            cmd.append(f"--projection={projection}")

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
                raise ValueError("Parsed value must be a list of 3 numbers or dict with x,y,z keys")
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Cannot parse '{param}' as camera parameter: {e}") from e
    
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
        raise ValueError(f"Failed to save image to file: {e}") from e


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
        raise ValueError(f"Failed to compress image: {e}") from e


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
            for _name, data in working_images[:1]:  # Test with first image
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
            "success": True,
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
        "success": True,
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
    parts: Optional[List[Dict[str, Any]]] = None,
    quality: Any = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Export OpenSCAD code or file to a mesh, 2D, or CSG format. With
    parts=[{name, code, place?}] every part is exported separately (in its
    assembly position) and bundled into one 3MF with named objects, or into
    a directory of STLs when output_format="stl"; a manifest is returned.

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
        if parts:
            return await _export_parts_bundle(
                scad_content, scad_file, output_format, output_path, variables,
                include_paths, parts, quality, ctx,
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
        _check_allowed_path(ws, "Workspace")
    else:
        ws = Path(config.temp_dir) / "models"

    ws.mkdir(parents=True, exist_ok=True)
    return ws


@mcp.tool()
async def model(
    action: str,
    name: Optional[str] = None,
    content: Optional[str] = None,
    workspace: Optional[str] = None,
    template: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Manage .scad files in a workspace: action = "create" | "get" | "update" |
    "list" | "delete". name is the file name (".scad" added); content is the
    source for create/update. Every response carries an "etag" (content
    hash) so a later update can be checked against the version last read.
    template="part:<id>" with action="create" writes a purchased-part module
    from the parts catalog (see reference(topic="parts")) instead of content.
    workspace defaults to the server temp models directory; when
    allowed_paths is configured the workspace must lie inside it.
    """
    action = (action or "").lower().strip()
    try:
        if action not in ("create", "get", "update", "list", "delete"):
            raise ValueError("action must be one of: create, get, update, list, delete")
        ws = _resolve_workspace(workspace)

        if action == "list":
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
                await ctx.info(f"Found {len(models)} model(s) in {ws}")
            return {"success": True, "workspace": str(ws), "models": models, "count": len(models)}

        if not name:
            raise ValueError(f"action '{action}' needs a name")
        name = _validate_model_name(name)
        file_path = ws / name

        if action == "get":
            if not file_path.exists():
                raise FileNotFoundError(f"Model '{name}' not found at {file_path}")
            text = file_path.read_text()
            return {
                "success": True,
                "name": name,
                "content": text,
                "path": str(file_path),
                "size_bytes": file_path.stat().st_size,
                "etag": _etag(text),
            }

        if action == "delete":
            if not file_path.exists():
                raise FileNotFoundError(f"Model '{name}' not found at {file_path}")
            file_path.unlink()
            if ctx:
                await ctx.info(f"Deleted model: {file_path}")
            return {"success": True, "name": name, "deleted_path": str(file_path)}

        # create / update
        extra: Dict[str, Any] = {}
        if template:
            if action != "create":
                raise ValueError("template is only valid with action='create'")
            if not template.startswith("part:"):
                raise ValueError("template must look like 'part:<catalog id>'")
            from . import parts_catalog

            part_id = template.split(":", 1)[1]
            entry = parts_catalog.lookup_part(part_id, detailed=False)
            if entry is None:
                raise ValueError(f"unknown catalog part '{part_id}'; see reference(topic='parts')")
            content = parts_catalog.part_scad_source(entry["id"])
            extra = {
                "template": entry["id"],
                "modules": entry.get("modules") or entry.get("module"),
                "anchors": entry.get("anchors"),
                "envelope_mm": entry.get("envelope_mm"),
                "verify": entry.get("verify"),
            }
        if content is None:
            raise ValueError(f"action '{action}' needs content")
        if action == "create" and file_path.exists():
            raise ValueError(
                f"Model '{name}' already exists at {file_path}; use action='update'"
            )
        if action == "update" and not file_path.exists():
            raise FileNotFoundError(
                f"Model '{name}' not found at {file_path}; use action='create'"
            )
        file_path.write_text(content)
        if ctx:
            await ctx.info(f"{action.title()}d model: {file_path}")
        response = {"success": True, "path": str(file_path), "name": name, "etag": _etag(content)}
        response.update(extra)
        return response
    except Exception as e:
        if ctx:
            await ctx.error(f"model {action} failed: {e}")
        return {"success": False, "action": action, "error": str(e)}


def _etag(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


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
    parts_dir = cache_dir / "parts"
    part_files = (
        list(parts_dir.glob("*.stl")) + list(parts_dir.glob("*.json")) + list(parts_dir.glob("*.csg"))
        if parts_dir.exists()
        else []
    )
    for f in list(cache_dir.glob("*.png")) + list(cache_dir.glob("*.json")) + part_files:
        try:
            size = f.stat().st_size
            f.unlink()
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
    mode: str = "files",
    symbol: Optional[str] = None,
    direction: str = "downstream",
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    mode="files": every .scad under project_dir with size/mtime and the
    include/use/import/surface references of each. mode="trace": the
    constant dependency graph for symbol= (lexical, file-scope constants
    only): its definition, what depends on it (direction="downstream") or
    what it depends on ("upstream"), by depth, and the part files that use
    them. project_dir must be inside allowed_paths when configured.
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

        if (mode or "files").lower() == "trace":
            from . import analysis

            if not symbol:
                raise ValueError("mode=trace needs symbol=<constant name>")
            files = sorted(resolved_dir.rglob("*.scad"))
            trace = await asyncio.get_running_loop().run_in_executor(
                None, lambda: analysis.trace_symbol(symbol, files, direction=direction)
            )
            trace.update({"success": True, "mode": "trace", "scope": "lexical, file-scope constants only"})
            return trace

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
# Geometry helpers shared by render / measure / validate
# ============================================================================

# Views rendered by render(mode="views") when none are requested.
DEFAULT_RENDER_VIEWS = ("isometric",)

# Palette for per-part colouring (camera.PALETTE when available).
_FALLBACK_PALETTE = [
    "#E69F00", "#56B4E9", "#009E73", "#F0E442", "#0072B2",
    "#D55E00", "#CC79A7", "#999999", "#000000", "#8B4513",
]


def _palette() -> List[str]:
    try:
        from .camera import PALETTE

        return list(PALETTE)
    except Exception:  # pragma: no cover - camera module optional at import time
        return list(_FALLBACK_PALETTE)


class _ModelSource:
    """Resolve (scad_content | scad_file) to a file path that wrappers can include.

    Inline content is written to the temp dir for the lifetime of the
    context manager; a file path is validated against ``allowed_paths``.
    """

    def __init__(self, scad_content: Optional[str], scad_file: Optional[str], prefix: str):
        if bool(scad_content) == bool(scad_file):
            raise ValueError("Exactly one of scad_content or scad_file must be provided")
        self.scad_content = scad_content
        self.scad_file = scad_file
        self.prefix = prefix
        self.path: Optional[Path] = None
        self.text: str = ""
        self.wrapper_dir: Optional[Path] = None
        self.model_dir: Optional[Path] = None
        self.display_name: str = "<inline>"
        self._temp: Optional[Path] = None
        self._cleanup: List[Path] = []

    def __enter__(self) -> "_ModelSource":
        config = get_config()
        temp_dir_path = Path(config.temp_dir)
        temp_dir_path.mkdir(parents=True, exist_ok=True)
        if self.scad_content:
            _validate_source_size(self.scad_content)
            self._temp = temp_dir_path / f"{self.prefix}_{uuid.uuid4().hex[:8]}.scad"
            self._temp.write_text(self.scad_content)
            self.path = self._temp
            self.text = self.scad_content
            self.wrapper_dir = temp_dir_path
        else:
            _check_allowed_path(self.scad_file or "", "File path")
            self.path = Path(self.scad_file or "")
            if not self.path.exists():
                raise FileNotFoundError(f"SCAD file not found: {self.scad_file}")
            # Wrapper programs live in the server temp dir (never write into
            # the user's project). Relative include/use lines resolve through
            # OPENSCADPATH, which gets the model's directory; relative
            # import()/surface() paths are rewritten to absolute ones.
            from .wrappers import absolutize_file_refs

            self.model_dir = self.path.parent
            self.text = absolutize_file_refs(
                self.path.read_text(errors="replace"), self.model_dir
            )
            self.wrapper_dir = temp_dir_path
            self.display_name = self.path.name
        return self

    def include_paths_for_wrapper(self, include_paths: Optional[List[str]]) -> List[str]:
        """Caller include paths plus the model's own directory."""
        paths = [str(p) for p in (include_paths or [])]
        if self.model_dir is not None and str(self.model_dir) not in paths:
            paths.append(str(self.model_dir))
        return paths

    def __exit__(self, *exc: Any) -> None:
        for f in [self._temp, *self._cleanup]:
            if f is None:
                continue
            try:
                f.unlink()
            except OSError:
                pass

    def wrapper_file(self, wrapped: Any) -> Path:
        """Write a wrapper program to the temp dir and register it for cleanup."""
        target_dir = self.wrapper_dir or Path(get_config().temp_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"wrapper-{self.prefix}-{uuid.uuid4().hex[:8]}.scad"
        path.write_text(wrapped.text)
        self._cleanup.append(path)
        return path

    @property
    def inline_path(self) -> Optional[str]:
        return str(self._temp) if self._temp is not None else None


def _rebase_diagnostics(diag: Diagnostics, wrapper_path: Path, wrapped: Any, display: str) -> None:
    """Point wrapper-file diagnostics back at the model's own file and lines.

    Also drops the "X was assigned ... but was overwritten" warnings that the
    wrapper's own variable injection provokes for the injected names.
    """
    wname = wrapper_path.name
    injected = set(getattr(wrapped, "injected", []) or [])
    if injected:
        diag.records = [
            rec
            for rec in diag.records
            if not (
                "was overwritten" in rec.message
                and any(rec.message.startswith(f"{name} was assigned") for name in injected)
            )
        ]
    for rec in diag.records:
        if rec.file and (rec.file.endswith(wname) or rec.file == "<inline>"):
            rec.file = display
            if rec.line is not None:
                rec.line = wrapped.rebase_line(rec.line)
        rec.trace = [t.replace(str(wrapper_path), display).replace(wname, display) for t in rec.trace]


_measure_cache: Dict[str, Tuple[Any, Diagnostics]] = {}
_MEASURE_CACHE_MAX = 32


def _static_dependency_fingerprint(
    text: str, base_dir: Path, include_paths: Optional[List[str]], limit: int = 400
) -> List[str]:
    """Stat every file reachable through include/use/import/surface references.

    A static approximation of the ``-d`` closure, good enough to invalidate
    cached measurements when a constants file or library changes. Each entry
    is ``path|size|mtime_ns``; unresolvable references are skipped.
    """
    search_roots: List[Path] = [base_dir]
    search_roots.extend(Path(p) for p in (include_paths or []))
    search_roots.extend(_library_search_paths())
    seen: Dict[str, str] = {}
    queue: List[Tuple[str, Path]] = [(text, base_dir)]
    while queue and len(seen) < limit:
        current_text, current_dir = queue.pop()
        for ref in extract_source_dependencies(current_text):
            candidates = [current_dir / ref] + [root / ref for root in search_roots]
            for cand in candidates:
                try:
                    if not cand.is_file():
                        continue
                    key = str(cand.resolve())
                    if key in seen:
                        break
                    st = cand.stat()
                    seen[key] = f"{key}|{st.st_size}|{st.st_mtime_ns}"
                    if cand.suffix.lower() == ".scad":
                        queue.append((cand.read_text(errors="replace"), cand.parent))
                except OSError:
                    continue
                break
    return sorted(seen.values())


def _measure_cache_key(source: _ModelSource, variables, include_paths, extra: str = "") -> str:
    hasher = hashlib.sha256()
    base_dir = source.model_dir or source.wrapper_dir or Path(get_config().temp_dir)
    if source.scad_content:
        _hash_field(hasher, source.scad_content.encode())
    else:
        try:
            st = Path(source.scad_file or "").stat()
            _hash_field(hasher, f"{source.scad_file}|{st.st_size}|{st.st_mtime_ns}")
        except OSError:
            _hash_field(hasher, str(source.scad_file))
    # Files the model pulls in: a constants file or library edit must miss.
    _hash_field(hasher, _static_dependency_fingerprint(source.text, base_dir, include_paths))
    _hash_field(hasher, variables or {})
    _hash_field(hasher, include_paths or [])
    _hash_field(hasher, extra)
    _hash_field(hasher, f"{find_openscad()}|{get_openscad_capabilities().get('version')}")
    return hasher.hexdigest()


def _analyze_mesh_export(
    wrapper_content: Optional[str],
    scad_file: Optional[str],
    variables: Optional[Dict[str, Any]],
    include_paths: Optional[List[str]],
    prefix: str,
    apply_variables: bool,
) -> Tuple[Any, Diagnostics, Optional[Path]]:
    """Export to STL (or SVG for 2D models) and analyse the geometry.

    Returns ``(stats, diagnostics, svg_path_or_None)``. ``stats`` is a
    ``mesh.MeshStats`` for 3D models or a ``mesh.Polygon2DStats`` for 2D
    models. Raises RuntimeError when OpenSCAD produced no geometry.
    """
    from . import mesh as meshlib

    config = get_config()
    temp_dir_path = Path(config.temp_dir)
    temp_dir_path.mkdir(parents=True, exist_ok=True)
    stl_output = temp_dir_path / f"{prefix}_{uuid.uuid4().hex[:8]}.stl"
    try:
        ev = _evaluate_scad(
            wrapper_content, scad_file, str(stl_output), None,
            variables if apply_variables else None, include_paths, "export", prefix,
        )
        diag = ev.diagnostics
        if ev.output_path is not None and ev.output_path.stat().st_size > 0:
            return meshlib.analyze_stl(ev.output_path), diag, None
    finally:
        if stl_output.exists():
            stl_output.unlink()

    # No geometry at all: OpenSCAD prints "Current top level object is empty"
    # and exits 1 without a file. That is a legitimate answer (an empty
    # intersection, a difference that removed everything), not a failure.
    if diag.empty_output:
        diag.records = [
            r for r in diag.records if not r.message.startswith("OpenSCAD exited with status")
        ]
        diag.returncode = 0
        return meshlib.analyze_triangles([]), diag, None

    # A 2D model. Try a 2D export before giving up.
    is_2d = any("2D" in w for w in diag.warnings) or diag.returncode != 0
    if is_2d:
        svg_output = temp_dir_path / f"{prefix}_{uuid.uuid4().hex[:8]}.svg"
        try:
            ev2 = _evaluate_scad(
                wrapper_content, scad_file, str(svg_output), None,
                variables if apply_variables else None, include_paths, "export", prefix,
            )
            if ev2.output_path is not None:
                polys = meshlib.load_svg_polygons(ev2.output_path)
                return meshlib.analyze_polygons(polys), ev2.diagnostics, None
        finally:
            if svg_output.exists():
                svg_output.unlink()
    detail = "; ".join(diag.errors) if diag.errors else "OpenSCAD produced no geometry"
    if diag.empty_output:
        detail = "the model evaluates to no geometry (Current top level object is empty)"
    raise RuntimeError(f"Measurement failed: {detail}")


def _measure_source(
    source: _ModelSource,
    variables: Optional[Dict[str, Any]],
    include_paths: Optional[List[str]],
    part_code: Optional[str] = None,
) -> Tuple[Any, Diagnostics]:
    """Measure the whole model, or one part of it, with an in-process cache."""
    from .wrappers import part_wrapper

    key = _measure_cache_key(source, variables, include_paths, extra=part_code or "")
    cached = _measure_cache.get(key)
    if cached is not None:
        return cached
    if part_code is not None:
        wrapped = part_wrapper(source.text, part_code, variables)
        wpath = source.wrapper_file(wrapped)
        stats, diag, _ = _analyze_mesh_export(
            None, str(wpath), variables, source.include_paths_for_wrapper(include_paths),
            "part", apply_variables=False,
        )
        _rebase_diagnostics(diag, wpath, wrapped, source.display_name)
    elif source.scad_content:
        stats, diag, _ = _analyze_mesh_export(
            source.scad_content, None, variables, include_paths, "measure", apply_variables=True
        )
    else:
        stats, diag, _ = _analyze_mesh_export(
            None, source.scad_file, variables, include_paths, "measure", apply_variables=True
        )
    if len(_measure_cache) >= _MEASURE_CACHE_MAX:
        _measure_cache.pop(next(iter(_measure_cache)))
    _measure_cache[key] = (stats, diag)
    return stats, diag


def _section_polygons(
    source: _ModelSource,
    axis: str,
    offset: float,
    variables: Optional[Dict[str, Any]],
    include_paths: Optional[List[str]],
) -> Tuple[List[List[Tuple[float, float]]], Diagnostics]:
    """Cut the model and return the section contours in model coordinates."""
    from . import mesh as meshlib
    from .wrappers import section_wrapper

    config = get_config()
    temp_dir_path = Path(config.temp_dir)
    temp_dir_path.mkdir(parents=True, exist_ok=True)
    svg_output = temp_dir_path / f"section_{uuid.uuid4().hex[:8]}.svg"
    wrapped = section_wrapper(source.text, axis, offset, variables)
    wpath = source.wrapper_file(wrapped)
    try:
        ev = _evaluate_scad(
            None, str(wpath), str(svg_output), None, None,
            source.include_paths_for_wrapper(include_paths), "export", "section",
        )
        _rebase_diagnostics(ev.diagnostics, wpath, wrapped, source.display_name)
        if ev.output_path is None:
            # A plane that misses the solid: WARNING: Projection() failed, exit 1
            return [], ev.diagnostics
        return meshlib.load_svg_polygons(ev.output_path), ev.diagnostics
    finally:
        if svg_output.exists():
            svg_output.unlink()


def _draw_section_png(
    polys: List[List[Tuple[float, float]]],
    image_size: List[int],
    axes_labels: Tuple[str, str],
    title: str,
) -> Tuple[bytes, float, Tuple[float, float, float, float]]:
    """Rasterise section polygons with Pillow. Returns (png, mm_per_px, bbox)."""

    from PIL import ImageDraw

    w, h = int(image_size[0]), int(image_size[1])
    img = PILImage.new("RGBA", (w, h), (255, 255, 229, 255))
    draw = ImageDraw.Draw(img)
    if not polys:
        return _png_bytes(img.convert("RGB")), 0.0, (0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    margin = 0.1
    mm_per_px = max(span_x / (w * (1 - 2 * margin)), span_y / (h * (1 - 2 * margin)))
    cx, cy = (min_x + max_x) / 2, (min_y + max_y) / 2

    def to_px(p: Tuple[float, float]) -> Tuple[float, float]:
        return (w / 2 + (p[0] - cx) / mm_per_px, h / 2 - (p[1] - cy) / mm_per_px)

    # Even-odd fill: draw every polygon, alternating fill on nesting is
    # approximated by drawing outer polygons first (by |area| desc) and holes
    # (negative signed area) in the background colour.
    def signed_area(poly):
        return sum(
            poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
            for i in range(len(poly))
        ) / 2

    ordered = sorted(polys, key=lambda p: -abs(signed_area(p)))
    for poly in ordered:
        pts = [to_px(p) for p in poly]
        fill = (255, 255, 229, 255) if signed_area(poly) < 0 else (86, 180, 233, 255)
        draw.polygon(pts, fill=fill, outline=(0, 0, 0, 255))
    try:
        from . import camera as cam

        font = cam._load_font(16)
        cam._draw_scale_bar(draw, font, mm_per_px, (w, h), 12)
    except Exception:  # pragma: no cover - annotation is best effort
        font = None
    draw.text(
        (8, 8),
        f"{title}  ({axes_labels[0]} right, {axes_labels[1]} up)",
        fill=(0, 0, 0, 255),
        font=font,
    )
    return _png_bytes(img.convert("RGB")), mm_per_px, (min_x, min_y, max_x, max_y)


def _png_bytes(img: "PILImage.Image") -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _bbox_of(stats: Any) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    bmin = getattr(stats, "bbox_min", None)
    bmax = getattr(stats, "bbox_max", None)
    if bmin is None or bmax is None or len(bmin) != 3:
        return None
    return tuple(bmin), tuple(bmax)  # type: ignore[return-value]


def _apply_quality(variables: Optional[Dict[str, Any]], quality: Any) -> Dict[str, Any]:
    """Merge a quality preset, integer $fn, or {fn, fa, fs} under caller variables."""
    parsed = parse_dict_param(variables, {})
    if quality is not None and quality != "":
        merged = _quality_to_variables(quality)
        merged.update(parsed)
        parsed = merged
    return parsed


def _validate_views(views: Any, default: Tuple[str, ...]) -> List[str]:
    parsed = list(default) if views is None else parse_list_param(views, list(default))
    invalid = [v for v in parsed if v not in VIEW_PRESETS]
    if invalid:
        raise ValueError(
            f"Invalid view name(s): {', '.join(invalid)}. "
            f"Must be one of: {', '.join(VIEW_PRESETS.keys())}"
        )
    return parsed


def _parse_parts(parts: Any) -> List[Dict[str, str]]:
    """Accept [{"name","code"}], {"name": "code"}, or JSON text of either."""
    if isinstance(parts, str):
        try:
            parts = json.loads(parts)
        except json.JSONDecodeError as exc:
            raise ValueError("parts must be a list of {name, code} objects") from exc
    if isinstance(parts, dict):
        parts = [{"name": k, "code": v} for k, v in parts.items()]
    if not isinstance(parts, list) or not parts:
        raise ValueError("parts must be a non-empty list of {name, code} objects")
    out: List[Dict[str, str]] = []
    for item in parts:
        if isinstance(item, str):
            out.append({"name": item.rstrip("();").strip(), "code": item})
            continue
        if not isinstance(item, dict) or "code" not in item:
            raise ValueError("each part needs a 'code' statement, e.g. {'name':'lid','code':'lid();'}")
        name = str(item.get("name") or item["code"].rstrip("();").strip())
        out.append({"name": name, "code": str(item["code"])})
    return out


def _render_one_view(
    source_content: Optional[str],
    source_file: Optional[str],
    view: Optional[str],
    camera_position: Optional[List[float]],
    camera_target: Optional[List[float]],
    camera_up: Optional[List[float]],
    image_size: List[int],
    color_scheme: str,
    variables: Dict[str, Any],
    include_paths: Optional[List[str]],
    grounded_bbox: Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]],
    annotate: bool,
    framed_bbox: Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = None,
    callouts: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bytes, str, Dict[str, Any]]:
    """Render one image and build its spatial digest. Returns (png, digest, meta).

    ``framed_bbox`` (look-at) frames the camera on a sub-box while
    ``grounded_bbox`` is the whole model; both appear in the digest so the
    crop is explicit. ``callouts`` are labelled 3D points projected with the
    same camera model (grounded renders only).
    """
    from . import camera as cam

    if view:
        preset_pos, preset_target, preset_up = VIEW_PRESETS[view]
        eye, center, up = list(preset_pos), list(preset_target), list(preset_up)
    else:
        eye = camera_position or [70, 70, 70]
        center = camera_target or [0, 0, 0]
        up = camera_up or [0, 0, 1]

    grounded = grounded_bbox is not None
    if grounded:
        bmin, bmax = grounded_bbox  # type: ignore[misc]
        fmin, fmax = framed_bbox if framed_bbox is not None else (bmin, bmax)
        direction = [eye[i] - center[i] for i in range(3)]
        ortho = cam.fit_camera(fmin, fmax, tuple(direction), tuple(up), (image_size[0], image_size[1]))
        eye, center = list(ortho.eye), list(ortho.center)
        result = _as_render_result(render_scad_to_png(
            scad_content=source_content, scad_file=source_file,
            camera_position=eye, camera_target=center, camera_up=up,
            image_size=image_size, color_scheme=color_scheme, variables=variables,
            auto_center=False, include_paths=include_paths, projection="o",
        ))
        png = base64.b64decode(result.image_b64)
        if annotate:
            png = cam.annotate(png, ortho, bbox_min=fmin, bbox_max=fmax, label=view)
        if callouts:
            png = _draw_callouts(png, ortho, callouts)
        digest = cam.spatial_digest(ortho, bbox_min=bmin, bbox_max=bmax, view_name=view, grounded=True)
        if framed_bbox is not None:
            digest += (
                f"\nframed (look_at): [{fmin[0]:.3g},{fmin[1]:.3g},{fmin[2]:.3g}].."
                f"[{fmax[0]:.3g},{fmax[1]:.3g},{fmax[2]:.3g}] of the model bbox above"
            )
        if callouts:
            digest += "\ncallouts: " + "; ".join(
                f"{c.get('label')} @ {c.get('at')}" for c in callouts[:8]
            )
    else:
        result = _as_render_result(render_scad_to_png(
            scad_content=source_content, scad_file=source_file,
            camera_position=eye, camera_target=center, camera_up=up,
            image_size=image_size, color_scheme=color_scheme, variables=variables,
            auto_center=True, include_paths=include_paths,
        ))
        png = base64.b64decode(result.image_b64)
        ortho = cam.OrthoCamera(tuple(eye), tuple(center), tuple(up), (image_size[0], image_size[1]))
        digest = cam.spatial_digest(
            ortho, projection="perspective", view_name=view, grounded=False
        )
    return png, digest, result.metadata()


def _coerce_section_offset(value: Any) -> Any:
    """Numbers stay numbers; strings are validated OpenSCAD expressions."""
    if isinstance(value, bool):
        raise ValueError("section_offset must be a number or an expression string")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    from .analysis import validate_expression

    return validate_expression(text)


def _resolved_section_offset(diag: Diagnostics, offset: Any) -> Optional[float]:
    if not isinstance(offset, str):
        return float(offset)
    from .wrappers import SECTION_MARKER, parse_echo_values

    for line in diag.echo_output:
        if SECTION_MARKER in line:
            try:
                vals = parse_echo_values(line)
            except ValueError:
                continue
            if len(vals) >= 2 and isinstance(vals[1], (int, float)) and not isinstance(vals[1], bool):
                return float(vals[1])
    return None


def _draw_callouts(png: bytes, ortho: Any, callouts: List[Dict[str, Any]]) -> bytes:
    """Label up to 8 world points on a grounded render (no overlap resolution)."""
    import io

    from PIL import ImageDraw

    from . import camera as cam

    img = PILImage.open(io.BytesIO(png)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    try:
        font = cam._load_font(16)
    except Exception:  # pragma: no cover
        font = None
    w, h = img.size
    used: List[Tuple[int, int, int, int]] = []
    for i, c in enumerate(callouts[:8]):
        at = c.get("at")
        label = str(c.get("label", f"#{i + 1}"))
        if not (isinstance(at, (list, tuple)) and len(at) == 3):
            continue
        x, y = ortho.project(tuple(float(v) for v in at))
        x, y = int(round(x)), int(round(y))
        if not (0 <= x < w and 0 <= y < h):
            continue
        draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(255, 0, 0, 255), outline=(0, 0, 0, 255))
        # Try four offsets to avoid piling labels on one another.
        for dx, dy in ((14, -18), (14, 10), (-90, -18), (-90, 10)):
            tx, ty = x + dx, y + dy
            box = draw.textbbox((tx, ty), label, font=font) if font else (tx, ty, tx + 8 * len(label), ty + 16)
            rect = (box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2)
            if all(rect[2] < u[0] or rect[0] > u[2] or rect[3] < u[1] or rect[1] > u[3] for u in used):
                used.append(rect)
                draw.rectangle(rect, fill=(248, 248, 248, 235), outline=(24, 24, 24, 255))
                draw.line((x, y, tx, ty), fill=(0, 0, 0, 255), width=1)
                draw.text((tx, ty), label, fill=(17, 17, 17, 255), font=font)
                break
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _resolve_look_at(
    look_at: Any,
    part_bboxes: Dict[str, Tuple[Tuple[float, float, float], Tuple[float, float, float]]],
    pad_mm: float = 2.0,
) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    """A part name, a point, or {min,max} -> the box to frame."""
    if look_at is None:
        return None
    if isinstance(look_at, str):
        if look_at in part_bboxes:
            bmin, bmax = part_bboxes[look_at]
        else:
            try:
                look_at = json.loads(look_at)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"look_at '{look_at}' is not a part name here; parts: {list(part_bboxes)}"
                ) from exc
            return _resolve_look_at(look_at, part_bboxes, pad_mm)
    elif isinstance(look_at, dict):
        bmin, bmax = tuple(look_at["min"]), tuple(look_at["max"])
    elif isinstance(look_at, (list, tuple)) and len(look_at) == 3 and all(isinstance(v, (int, float)) for v in look_at):
        c = tuple(float(v) for v in look_at)
        bmin, bmax = tuple(v - 5 for v in c), tuple(v + 5 for v in c)
    else:
        raise ValueError("look_at must be a part name, [x,y,z], or {min:[..], max:[..]}")
    return (
        tuple(float(v) - pad_mm for v in bmin),  # type: ignore[return-value]
        tuple(float(v) + pad_mm for v in bmax),
    )


def _parts_render_body(asm: Assembly, colors: Dict[str, str], isolate: Optional[str]) -> str:
    """``!union(){ color(c) { placement { code } } ... }`` with ghosts translucent."""
    lines = []
    for p in asm.parts:
        stmt = asm.part_statement(p, explode=True)
        color = p.color or colors.get(p.name, "#999999")
        ghost = p.ghost or (isolate is not None and p.name != isolate)
        if ghost:
            lines.append(f'%color("{color}", 0.3) {{ {stmt} }}')
        else:
            lines.append(f'color("{color}") {{ {stmt} }}')
    return "!union() {\n    " + "\n    ".join(lines) + "\n}\n"


# ============================================================================
# render
# ============================================================================


@mcp.tool(output_schema=None)
async def render(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    mode: str = "views",
    views: Optional[List[str]] = None,
    camera_position: Union[str, List[float], Dict[str, float], None] = None,
    camera_target: Union[str, List[float], Dict[str, float], None] = None,
    camera_up: Union[str, List[float], Dict[str, float], None] = None,
    image_size: Union[str, List[int], None] = None,
    color_scheme: str = "Cornfield",
    variables: Optional[Dict[str, Any]] = None,
    quality: Any = None,
    include_paths: Optional[List[str]] = None,
    grounded: bool = False,
    annotate: bool = False,
    section_axis: str = "z",
    section_offset: Any = 0.0,
    parts: Optional[List[Dict[str, Any]]] = None,
    isolate: Optional[str] = None,
    look_at: Any = None,
    callouts: Optional[List[Dict[str, Any]]] = None,
    variables_after: Optional[Dict[str, Any]] = None,
    scad_content_after: Optional[str] = None,
    ctx: Optional[Context] = None,
):
    """
    Images of a model, each preceded by a text digest (view direction,
    camera, scale, bbox) and followed by metadata with errors/warnings/
    hints. Check those: OpenSCAD draws a blank scene and exits 0 on a
    failed assert or unknown module.
    mode "views": one image per view (default ["isometric"]; front back
    left right top bottom isometric dimetric) or a custom camera_position.
    "section": exact cut at section_axis=section_offset (mm, or an
    expression in the model's scope). "parts": parts=[{name, code, place?,
    color?, ghost?, explode?}] in stable colours; isolate=name ghosts the
    rest. "compare": before/after via variables_after or scad_content_after.
    grounded=true gives an orthographic view with an exact mm/px scale;
    annotate=true adds scale bar, axis triad and bbox size; look_at=part |
    [x,y,z] | {min,max} frames that box; callouts=[{label, at}] label
    points. Auto-fit views have no absolute scale. quality: draft|normal|
    high. image_size default 800x600 (~640 tokens per image).
    """
    if ctx:
        await ctx.info(f"render mode={mode}")
    try:
        if bool(scad_content) == bool(scad_file):
            raise ValueError("Exactly one of scad_content or scad_file must be provided")
        mode = (mode or "views").lower()
        if mode not in ("views", "section", "parts", "compare"):
            raise ValueError("mode must be one of: views, section, parts, compare")
        parsed_size = _clamp_image_size(parse_image_size_param(image_size, [800, 600]))
        parsed_vars = _apply_quality(variables, quality)
        if annotate and not grounded and mode == "views":
            grounded = True
        loop = asyncio.get_running_loop()
        semaphore = get_render_semaphore()
        items: List[Any] = []
        meta: Dict[str, Any] = {"mode": mode, "image_size": parsed_size}

        if mode == "views":
            custom_camera = views is None and (
                camera_position is not None or camera_target is not None
            )
            view_list: List[Optional[str]] = (
                [None] if custom_camera else list(_validate_views(views, DEFAULT_RENDER_VIEWS))
            )
            cam_pos = parse_camera_param(camera_position, [70, 70, 70]) if custom_camera else None
            cam_tgt = parse_camera_param(camera_target, [0, 0, 0]) if custom_camera else None
            cam_up = parse_camera_param(camera_up, [0, 0, 1]) if custom_camera else None
            bbox = None
            if grounded:
                with _ModelSource(scad_content, scad_file, "ground") as src:
                    async with semaphore:
                        stats, _diag = await loop.run_in_executor(
                            None, _measure_source, src, parsed_vars, include_paths
                        )
                bbox = _bbox_of(stats)
                if bbox is None:
                    raise ValueError("grounded rendering needs a 3D model with a bounding box")

            async def _one(v: Optional[str]):
                async with semaphore:
                    return await loop.run_in_executor(
                        None, _render_one_view, scad_content, scad_file, v,
                        cam_pos, cam_tgt, cam_up, parsed_size, color_scheme, parsed_vars,
                        include_paths, bbox, annotate,
                    )

            framed = _resolve_look_at(look_at, {}, 2.0) if (look_at is not None) else None
            if framed is not None and bbox is None:
                raise ValueError("look_at needs grounded=true")
            if callouts and bbox is None:
                raise ValueError("callouts need grounded=true")

            async def _one(v: Optional[str]):  # noqa: F811 - replaces the plain renderer above
                async with semaphore:
                    return await loop.run_in_executor(
                        None, _render_one_view, scad_content, scad_file, v,
                        cam_pos, cam_tgt, cam_up, parsed_size, color_scheme, parsed_vars,
                        include_paths, bbox, annotate, framed, callouts,
                    )

            results = await asyncio.gather(*[_one(v) for v in view_list], return_exceptions=True)
            failed: Dict[str, str] = {}
            for v, res in zip(view_list, results, strict=False):
                name = v or "custom"
                if isinstance(res, Exception):
                    failed[name] = str(res)
                    continue
                png, digest, m = res
                items.append(f"View: {name}\n{digest}")
                items.append(MCPImage(data=png, format="png"))
                if "errors" not in meta:
                    meta.update({k: m[k] for k in ("errors", "warnings", "hints", "echo_output", "cached") if k in m})
            meta["views"] = [v or "custom" for v, r in zip(view_list, results, strict=False) if not isinstance(r, Exception)]
            if failed:
                meta["failed_views"] = failed
            if bbox is not None:
                meta["bbox"] = {"min": list(bbox[0]), "max": list(bbox[1])}
            meta["image_tokens"] = image_token_estimate(*parsed_size) * len(meta["views"])
            meta["success"] = not failed and not meta.get("errors")

        elif mode == "section":
            from .wrappers import section_in_plane_axes

            offset_value = _coerce_section_offset(section_offset)
            with _ModelSource(scad_content, scad_file, "section") as src:
                async with semaphore:
                    polys, diag = await loop.run_in_executor(
                        None, _section_polygons, src, section_axis, offset_value,
                        parsed_vars, include_paths,
                    )
            axes_labels = section_in_plane_axes(section_axis)
            resolved = _resolved_section_offset(diag, offset_value)
            if isinstance(offset_value, str) and resolved is None:
                raise ValueError(
                    f"section_offset expression {offset_value!r} did not evaluate to a number"
                )
            meta["section_offset"] = resolved
            title = f"section {section_axis.lower()} = {resolved} mm"
            png, mm_per_px, sbbox = _draw_section_png(polys, parsed_size, axes_labels, title)
            meta.update(diag.to_dict(include_records=False))
            if not polys:
                digest = (
                    f"{title}: the cut plane misses the model (no section geometry). "
                    "Try a different offset (measure(mode=model) gives the bbox). If the "
                    "file only instantiates geometry under `if ($preview)`, nothing exists "
                    "at export time: pass the guard variable (e.g. variables={'make_stl': true})."
                )
                meta["empty_section"] = True
            else:
                digest = (
                    f"{title} | in-plane axes: {axes_labels[0]} right, {axes_labels[1]} up | units mm\n"
                    f"scale: {mm_per_px:.4g} mm/px | section bbox "
                    f"[{sbbox[0]:.3g},{sbbox[1]:.3g}]..[{sbbox[2]:.3g},{sbbox[3]:.3g}] | "
                    f"{len(polys)} contour(s)"
                )
            items.append(digest)
            items.append(MCPImage(data=png, format="png"))
            meta["contours"] = len(polys)
            meta["image_tokens"] = image_token_estimate(*parsed_size)
            meta["success"] = not diag.errors

        elif mode == "parts":
            from .analysis import assign_colors
            from .wrappers import build_wrapper

            part_list = parse_parts(parts)
            asm = Assembly(parts=part_list)
            colors = assign_colors([p.name for p in part_list])
            for p in part_list:
                if p.color:
                    colors[p.name] = p.color
            with _ModelSource(scad_content, scad_file, "parts") as src:
                wrapped = build_wrapper(
                    src.text, parsed_vars, extra_body=_parts_render_body(asm, colors, isolate)
                )
                wpath = src.wrapper_file(wrapped)
                view_list2 = list(_validate_views(views, DEFAULT_RENDER_VIEWS))
                bbox = None
                part_bboxes: Dict[str, Any] = {}
                if grounded or look_at is not None or callouts:
                    exported = await _export_parts(src, asm, parsed_vars, include_paths, ctx)
                    for n, e in exported.items():
                        if e.empty:
                            continue
                        m = _load_mesh(e)
                        part_bboxes[n] = (tuple(m.bbox_min), tuple(m.bbox_max))
                    if part_bboxes:
                        bbox = (
                            tuple(min(b[0][i] for b in part_bboxes.values()) for i in range(3)),
                            tuple(max(b[1][i] for b in part_bboxes.values()) for i in range(3)),
                        )
                framed = _resolve_look_at(look_at, part_bboxes, 2.0) if look_at is not None else None
                wrapper_paths = src.include_paths_for_wrapper(include_paths)

                async def _one_part_view(v: str):
                    async with semaphore:
                        return await loop.run_in_executor(
                            None, _render_one_view, None, str(wpath), v, None, None, None,
                            parsed_size, color_scheme, {}, wrapper_paths, bbox, annotate,
                            framed, callouts,
                        )

                results = await asyncio.gather(*[_one_part_view(v) for v in view_list2])
            legend = " | ".join(
                f"{p.name}={colors[p.name]}"
                + (" (ghost)" if p.ghost or (isolate and p.name != isolate) else "")
                for p in part_list
            )
            for v, (png, digest, m) in zip(view_list2, results, strict=False):
                items.append(f"View: {v}\nparts: {legend}\n{digest}")
                items.append(MCPImage(data=png, format="png"))
                if "errors" not in meta:
                    meta.update({k: m[k] for k in ("errors", "warnings", "hints") if k in m})
            meta["parts"] = [
                {"name": p.name, "color": colors[p.name],
                 **({"ghost": True} if p.ghost or (isolate and p.name != isolate) else {}),
                 **({"bbox": [list(part_bboxes[p.name][0]), list(part_bboxes[p.name][1])]} if p.name in part_bboxes else {})}
                for p in part_list
            ]
            meta["frame"] = "assembly"
            meta["image_tokens"] = image_token_estimate(*parsed_size) * len(view_list2)
            meta["success"] = not meta.get("errors")

        else:  # compare
            if variables_after is None and scad_content_after is None:
                raise ValueError("compare needs variables_after or scad_content_after")
            after_vars = dict(parsed_vars)
            if variables_after:
                after_vars.update(parse_dict_param(variables_after, {}))
            view_name = list(_validate_views(views, DEFAULT_RENDER_VIEWS))[0]

            async def _side(content: Optional[str], file: Optional[str], vars_: Dict[str, Any]):
                async with semaphore:
                    return await loop.run_in_executor(
                        None, _render_one_view, content, file, view_name, None, None, None,
                        parsed_size, color_scheme, vars_, include_paths, None, False,
                    )

            before, after = await asyncio.gather(
                _side(scad_content, scad_file, parsed_vars),
                _side(scad_content_after or scad_content, None if scad_content_after else scad_file, after_vars),
            )
            for label, (png, digest, m) in (("Before", before), ("After", after)):
                items.append(f"{label}: view {view_name}\n{digest}")
                items.append(MCPImage(data=png, format="png"))
                meta[label.lower()] = {k: m[k] for k in ("errors", "warnings", "hints") if k in m}
            meta["view"] = view_name
            meta["image_tokens"] = image_token_estimate(*parsed_size) * 2
            meta["success"] = not meta["before"].get("errors") and not meta["after"].get("errors")

        items.append(json.dumps(meta))
        return items
    except Exception as e:
        if ctx:
            await ctx.error(f"render failed: {e}")
        return [json.dumps({"success": False, "mode": mode, "error": str(e)})]


# ============================================================================
# measure
# ============================================================================


def _stats_dict(stats: Any, detailed: bool) -> Dict[str, Any]:
    if hasattr(stats, "to_dict"):
        try:
            return stats.to_dict(detailed=detailed)
        except TypeError:
            return stats.to_dict()
    return dict(stats)


@mcp.tool()
async def measure(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    mode: str = "model",
    variables: Optional[Dict[str, Any]] = None,
    include_paths: Optional[List[str]] = None,
    parts: Optional[List[Dict[str, Any]]] = None,
    section_axis: str = "z",
    section_offset: Any = 0.0,
    material: Optional[str] = None,
    density_g_cm3: Optional[float] = None,
    mesh: Optional[str] = None,
    part: Optional[str] = None,
    points: Optional[List[Any]] = None,
    rays: Optional[List[Any]] = None,
    polyline: Optional[List[List[float]]] = None,
    orientation: Any = None,
    about_axis: Optional[List[List[float]]] = None,
    nozzle_mm: float = 0.4,
    layer_height_mm: Optional[float] = None,
    quality: Any = None,
    response_format: str = "concise",
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Exact numbers from a model's geometry (mm). Prefer this over judging a
    picture. parts=[{name, code, place?, material?, mass_g?}] names
    assembly parts; coordinates are then in the assembly frame.
    mode "model": bbox, volume, area, solid/cavity counts, watertight,
    mesh_health (2D: area/perimeter). "parts": per part plus assembly bbox.
    "section": cut contours at section_axis=section_offset (mm or an
    expression). "mass": grams for material/density; with parts= and
    about_axis=[[point],[dir]] the assembly mass, centre of mass and
    inertia about the axis (mass_g overrides purchased parts). "probe":
    points -> solid|air and which part, rays=[[ox,oy,oz,dx,dy,dz]] ->
    crossings, polyline -> line of sight and blocker. "features": holes
    from the CSG tree: axis, diameter, depth, undersize at $fn, fit names.
    "printability": overhang patches with unsupported reach, thickness vs
    nozzle, islands (layer_height_mm), support estimate; facts only.
    "orientation": candidate orientations, no winner. "anchors": BOSL2
    anchors of part="module();". mesh=existing STL/SVG. quality: draft|
    normal|high or $fn. response_format: concise|detailed.
    """
    from . import mesh as meshlib

    detailed = (response_format or "concise").lower() == "detailed"
    mode = (mode or "model").lower()
    try:
        extended = ("probe", "features", "printability", "orientation", "anchors")
        if mode not in ("model", "parts", "section", "mass") + extended:
            raise ValueError(
                "mode must be one of: model, parts, section, mass, probe, features, "
                "printability, orientation, anchors"
            )
        parsed_vars = parse_dict_param(variables, {})
        _validate_variable_names(parsed_vars)
        _validate_include_paths(include_paths)
        if mode in extended or (mode == "mass" and (parts or about_axis) and not mesh):
            if bool(scad_content) == bool(scad_file):
                raise ValueError("Exactly one of scad_content or scad_file must be provided")
            return await _measure_extended(
                mode, scad_content, scad_file, parsed_vars, include_paths, parts, part,
                points, rays, polyline, orientation, about_axis, material, density_g_cm3,
                nozzle_mm, layer_height_mm, quality, detailed, ctx,
            )
        if quality is not None:
            parsed_vars.update(_quality_to_variables(quality))
        loop = asyncio.get_running_loop()
        semaphore = get_render_semaphore()
        result: Dict[str, Any] = {"success": True, "mode": mode, "units": "mm"}

        if mesh:
            if scad_content or scad_file:
                raise ValueError("give either mesh or scad_content/scad_file, not both")
            _check_allowed_path(mesh, "Mesh path")
            mesh_path = Path(mesh)
            if not mesh_path.exists():
                raise FileNotFoundError(f"Mesh file not found: {mesh}")
            if mesh_path.suffix.lower() == ".svg":
                stats = meshlib.analyze_polygons(meshlib.load_svg_polygons(mesh_path))
            else:
                stats = await loop.run_in_executor(None, meshlib.analyze_stl, mesh_path)
            result["source"] = str(mesh_path)
            result.update(_stats_dict(stats, detailed))
            if mode == "mass":
                result["mass"] = _mass_block(stats, material, density_g_cm3)
            return result

        if bool(scad_content) == bool(scad_file):
            raise ValueError("Exactly one of scad_content, scad_file or mesh must be provided")

        with _ModelSource(scad_content, scad_file, "measure") as src:
            if mode in ("model", "mass"):
                async with semaphore:
                    stats, diag = await loop.run_in_executor(
                        None, _measure_source, src, parsed_vars, include_paths
                    )
                result.update(_stats_dict(stats, detailed))
                if getattr(stats, "triangle_count", 1) == 0:
                    result["empty"] = True
                    result["note"] = "the model evaluates to no geometry"
                result["mesh_health"] = diag.mesh_health()
                result.update(diag.to_dict(include_records=False))
                if mode == "mass":
                    result["mass"] = _mass_block(stats, material, density_g_cm3)
                result["success"] = not diag.errors

            elif mode == "parts":
                # The same part grammar as check/render/export, so `place`,
                # frames and ghosts mean the same thing everywhere; each
                # part is measured in its assembly position.
                asm_parts = Assembly(parts=parse_parts(parts))
                part_list = [
                    {"name": p.name, "code": asm_parts.part_statement(p), "display_code": p.code}
                    for p in asm_parts.parts
                ]

                async def _one(p: Dict[str, str]):
                    async with semaphore:
                        return await loop.run_in_executor(
                            None, _measure_source, src, parsed_vars, include_paths, p["code"]
                        )

                outcomes = await asyncio.gather(*[_one(p) for p in part_list], return_exceptions=True)
                per_part: List[Dict[str, Any]] = []
                boxes: List[Tuple[str, Tuple[float, ...], Tuple[float, ...]]] = []
                errors: List[str] = []
                for p, out in zip(part_list, outcomes, strict=False):
                    if isinstance(out, Exception):
                        per_part.append({"name": p["name"], "error": str(out)})
                        errors.append(f"{p['name']}: {out}")
                        continue
                    stats, diag = out
                    entry = {"name": p["name"], "code": p.get("display_code", p["code"])}
                    if p["code"] != p.get("display_code"):
                        entry["placed"] = True
                    entry.update(_stats_dict(stats, detailed=False))
                    entry["mesh_health"] = diag.mesh_health()
                    if diag.errors:
                        entry["errors"] = diag.errors
                        errors.extend(diag.errors)
                    per_part.append(entry)
                    bb = _bbox_of(stats)
                    if bb:
                        boxes.append((p["name"], bb[0], bb[1]))
                result["parts"] = per_part
                result["frame"] = "assembly"
                if boxes:
                    amin = [min(b[1][i] for b in boxes) for i in range(3)]
                    amax = [max(b[2][i] for b in boxes) for i in range(3)]
                    result["assembly_bbox"] = {
                        "min": amin, "max": amax,
                        "size": [round(amax[i] - amin[i], 4) for i in range(3)],
                    }
                    overlaps = []
                    for i in range(len(boxes)):
                        for j in range(i + 1, len(boxes)):
                            a, b = boxes[i], boxes[j]
                            if all(a[1][k] < b[2][k] and b[1][k] < a[2][k] for k in range(3)):
                                overlaps.append([a[0], b[0]])
                    result["bbox_overlaps"] = overlaps
                    result["note"] = (
                        "bbox_overlaps lists part pairs whose bounding boxes intersect; "
                        "that is not an interference test."
                    )
                if errors:
                    result["errors"] = errors
                    result["success"] = False

            else:  # section
                from .wrappers import section_in_plane_axes

                offset_value = _coerce_section_offset(section_offset)
                async with semaphore:
                    polys, diag = await loop.run_in_executor(
                        None, _section_polygons, src, section_axis, offset_value,
                        parsed_vars, include_paths,
                    )
                axes_labels = section_in_plane_axes(section_axis)
                result["plane"] = {"axis": section_axis.lower(), "offset": _resolved_section_offset(diag, offset_value)}
                result["in_plane_axes"] = {"x": axes_labels[0], "y": axes_labels[1]}
                if not polys:
                    result["empty_section"] = True
                    result["contours"] = []
                    result["note"] = (
                        "the cut plane misses the model, or the file instantiates geometry "
                        "only under `if ($preview)` (pass the guard variable via variables)"
                    )
                else:
                    pstats = meshlib.analyze_polygons(polys)
                    result.update(_stats_dict(pstats, detailed))
                    cap = 400 if detailed else 60
                    result["contours"] = [
                        [[round(x, 4), round(y, 4)] for x, y in poly[:cap]]
                        + ([["...", f"{len(poly) - cap} more"]] if len(poly) > cap else [])
                        for poly in polys
                    ]
                result.update(diag.to_dict(include_records=False))
                result["success"] = not diag.errors
        return result
    except Exception as e:
        if ctx:
            await ctx.error(f"measure failed: {e}")
        return {"success": False, "mode": mode, "error": str(e)}


def _mass_block(stats: Any, material: Optional[str], density: Optional[float]) -> Dict[str, Any]:
    from . import mesh as meshlib

    volume = getattr(stats, "volume", None)
    if volume is None:
        raise ValueError("mass needs a 3D model with a volume")
    if density is None:
        if not material:
            raise ValueError("mass needs material or density_g_cm3")
        key = next((k for k in meshlib.MATERIAL_DENSITIES if k.lower() == material.lower()), None)
        if key is None:
            raise ValueError(
                f"unknown material '{material}'; known: {', '.join(meshlib.MATERIAL_DENSITIES)}"
            )
        density = meshlib.MATERIAL_DENSITIES[key]
        material = key
    grams = meshlib.mass_from_volume(volume, density)
    return {
        "material": material,
        "density_g_cm3": density,
        "grams": float(f"{grams:.4g}"),
        "note": "solid infill; real prints weigh less with sparse infill",
    }


# ============================================================================
# validate
# ============================================================================


@mcp.tool()
async def validate(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    mode: str = "syntax",
    variables: Optional[Dict[str, Any]] = None,
    include_paths: Optional[List[str]] = None,
    predicates: Optional[List[str]] = None,
    sweep: Optional[Dict[str, Any]] = None,
    autofix: bool = False,
    orientation: Any = None,
    profile: Optional[Dict[str, Any]] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Check a model. "valid" is false whenever an ERROR was reported, whatever
    OpenSCAD's exit code was.
    mode:
      "syntax": parse and evaluate without geometry (fast): errors,
        warnings, echo_output, hints, unresolved_includes with locations.
      "geometry": export the mesh and report findings: not watertight,
        non-manifold, several solids, cavities, empty; with the numbers.
      "predicates": predicates=["W > 10", ...] evaluated in the model's own
        scope; each must be true. sweep={variable, values:[..]} re-runs
        them per value and reports the first failure and the crossing.
      "includes": every include/use/import/surface reference with its
        resolved path, plus the BOSL2 lint: a module from a use<>d file
        placed by attach()/position() is silently put at CENTER; findings
        carry a fix plan, applied to the file when autofix=true and safe.
      "printability": rules from reference(topic=dfm) over
        measure(mode=printability) facts in orientation=; profile=
        {overhang_deg, nozzle_mm, max_unsupported_reach_mm, min_wall_mm}
        overrides thresholds. Findings carry magnitude and location.
    """
    mode = (mode or "syntax").lower()
    try:
        if bool(scad_content) == bool(scad_file):
            raise ValueError("Exactly one of scad_content or scad_file must be provided")
        if mode not in ("syntax", "geometry", "predicates", "includes", "printability"):
            raise ValueError("mode must be one of: syntax, geometry, predicates, includes, printability")
        if mode == "printability":
            return await _validate_printability(
                scad_content, scad_file, variables, include_paths, orientation, profile or {}, ctx
            )
        parsed_vars = parse_dict_param(variables, {})
        _validate_variable_names(parsed_vars)
        null_output = "NUL" if platform.system() == "Windows" else "/dev/null"
        loop = asyncio.get_running_loop()
        semaphore = get_render_semaphore()
        if ctx:
            await ctx.info(f"validate mode={mode}")

        if mode == "syntax":
            async with semaphore:
                ev = await loop.run_in_executor(
                    None, _evaluate_scad, scad_content, scad_file, null_output, "csg",
                    parsed_vars, include_paths, "validation", "validate",
                )
            diag = ev.diagnostics
            response: Dict[str, Any] = {"success": True, "mode": mode, "valid": diag.ok}
            response.update(diag.to_dict(include_records=True))
            missing = unresolved_includes(diag)
            if missing:
                response["unresolved_includes"] = missing
            return response

        if mode == "geometry":
            with _ModelSource(scad_content, scad_file, "validate") as src:
                async with semaphore:
                    stats, diag = await loop.run_in_executor(
                        None, _measure_source, src, parsed_vars, include_paths
                    )
            findings: List[Dict[str, Any]] = []
            health = diag.mesh_health()
            if health.get("manifold") is False:
                findings.append({"code": "non_manifold", "detail": health.get("issue", "")})
            if getattr(stats, "is_watertight", True) is False:
                findings.append({
                    "code": "open_edges",
                    "detail": f"{getattr(stats, 'open_edge_count', 0)} open edge(s); the mesh is not closed",
                })
            if getattr(stats, "non_manifold_edge_count", 0):
                findings.append({
                    "code": "non_manifold_edges",
                    "detail": f"{stats.non_manifold_edge_count} edge(s) shared by more than two faces",
                })
            if getattr(stats, "solid_count", 1) > 1:
                findings.append({
                    "code": "multiple_solids",
                    "detail": f"{stats.solid_count} separate solids; intended for a single part?",
                })
            if getattr(stats, "cavity_count", 0):
                findings.append({
                    "code": "cavities",
                    "detail": f"{stats.cavity_count} enclosed cavity(ies); unprintable trapped volume unless intended",
                })
            if getattr(stats, "degenerate_triangle_count", 0):
                findings.append({
                    "code": "degenerate_triangles",
                    "detail": f"{stats.degenerate_triangle_count} zero-area triangle(s)",
                })
            for err in diag.errors:
                findings.append({"code": "openscad_error", "detail": err})
            response = {
                "success": True,
                "mode": mode,
                "valid": not findings,
                "findings": findings,
                "mesh_health": health,
                "summary": _stats_dict(stats, detailed=False),
            }
            response.update(diag.to_dict(include_records=False))
            return response

        if mode == "predicates":
            from .wrappers import collect_eval_results, eval_wrapper

            exprs = parse_list_param(predicates, [])
            if not exprs:
                raise ValueError("predicates must be a non-empty list of OpenSCAD boolean expressions")
            with _ModelSource(scad_content, scad_file, "pred") as src:
                wrapped = eval_wrapper(src.text, [str(e) for e in exprs], parsed_vars)
                wpath = src.wrapper_file(wrapped)
                async with semaphore:
                    ev = await loop.run_in_executor(
                        None, _evaluate_scad, None, str(wpath), null_output, "csg",
                        None, src.include_paths_for_wrapper(include_paths), "validation", "predicates",
                    )
                _rebase_diagnostics(ev.diagnostics, wpath, wrapped, src.display_name)
            diag = ev.diagnostics
            evaluated = collect_eval_results(diag.echo_output, len(exprs))
            results = []
            for expr, r in zip(exprs, evaluated, strict=False):
                passed = r.get("evaluated") and r.get("value") is True
                results.append({
                    "predicate": expr,
                    "pass": bool(passed),
                    "value": r.get("value"),
                    "type": r.get("type", "undef"),
                })
            response = {
                "success": True,
                "mode": mode,
                "valid": all(r["pass"] for r in results) and not diag.errors,
                "results": results,
            }
            response.update(diag.to_dict(include_records=False))
            if sweep:
                response["sweep"] = await _predicate_sweep(
                    scad_content, scad_file, parsed_vars, include_paths, exprs, sweep
                )
            return response

        # includes
        with _ModelSource(scad_content, scad_file, "inc") as src:
            text = src.scad_content if src.scad_content else (src.path or Path()).read_text(errors="replace")
            references = extract_source_dependencies(text)
            async with semaphore:
                ev = await loop.run_in_executor(
                    None, _evaluate_scad, scad_content, scad_file, null_output, "csg",
                    parsed_vars, include_paths, "validation", "includes",
                )
        diag = ev.diagnostics
        missing = set(unresolved_includes(diag))
        resolved: List[Dict[str, Any]] = []
        model_dir = (src.path or Path()).parent
        dep_resolved = {}
        for d in ev.dependencies:
            try:
                dep_resolved[str(Path(d).resolve())] = d
            except OSError:
                continue
        for ref in references:
            # A reference like ../../config/x.scad is recorded by OpenSCAD as
            # a normalised absolute path, so compare resolved paths first and
            # fall back to a suffix match for OPENSCADPATH-resolved libraries.
            match = None
            try:
                match = dep_resolved.get(str((model_dir / ref).resolve()))
            except OSError:
                match = None
            if match is None:
                norm_ref = ref.replace("\\", "/").lstrip("./")
                match = next(
                    (d for d in ev.dependencies if d.replace("\\", "/").endswith(norm_ref)),
                    None,
                )
            # OpenSCAD records import()/surface() targets it *tried* to read,
            # so existence must be checked separately.
            exists = match is not None and Path(match).exists()
            resolved.append({
                "reference": ref,
                "resolved_path": match if exists else None,
                "found": exists and ref not in missing,
            })
        lint: List[Dict[str, Any]] = []
        if src.path is not None and src.scad_content is None:
            try:
                from . import analysis

                lint = [
                    dict(f, severity=str(f.get("severity", "warning")).lower())
                    for f in analysis.lint_use_shadowing(
                        src.path, include_paths=include_paths, library_paths=_library_search_paths()
                    )
                ]
                if autofix and lint:
                    lint = _apply_lint_fixes(src.path, lint)
            except Exception as exc:  # the lint is advisory
                lint = [{"code": "lint_error", "severity": "warning", "message": str(exc)}]
        response = {
            "success": True,
            "mode": mode,
            "valid": all(r["found"] for r in resolved) and not diag.errors
            and not any(f.get("severity") == "error" and not f.get("fixed") for f in lint),
            "references": resolved,
            "lint": lint,
            "files_read": [d for d in ev.dependencies if not d.endswith(Path(src.path or "").name)],
            "search_paths": [str(p) for p in ([Path(p) for p in (include_paths or [])] + _library_search_paths()) if Path(p).exists()],
        }
        response.update(diag.to_dict(include_records=False))
        return response
    except Exception as e:
        if ctx:
            await ctx.error(f"validate failed: {e}")
        return {"success": False, "mode": mode, "error": str(e)}


# ============================================================================
# scad_eval
# ============================================================================


@mcp.tool()
async def scad_eval(
    expressions: List[str],
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    include_paths: Optional[List[str]] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Evaluate OpenSCAD expressions and return typed values (number, vector,
    string, bool, range, undef). With scad_content/scad_file the
    expressions run in that model's variable and function scope, so
    "wall*2 + clearance" or "len(hole_positions)" work; without a model
    they run standalone. Numbers carry OpenSCAD's 6 significant digits.
    No geometry is evaluated.
    """
    from .wrappers import collect_eval_results, eval_wrapper

    try:
        exprs = [str(e) for e in parse_list_param(expressions, [])]
        if not exprs:
            raise ValueError("expressions must be a non-empty list")
        if scad_content and scad_file:
            raise ValueError("give at most one of scad_content or scad_file")
        parsed_vars = parse_dict_param(variables, {})
        _validate_variable_names(parsed_vars)
        null_output = "NUL" if platform.system() == "Windows" else "/dev/null"
        loop = asyncio.get_running_loop()
        with _ModelSource(scad_content or "// standalone\n" if not scad_file else None, scad_file, "eval") as src:
            wrapped = eval_wrapper(src.text, exprs, parsed_vars)
            wpath = src.wrapper_file(wrapped)
            async with get_render_semaphore():
                ev = await loop.run_in_executor(
                    None, _evaluate_scad, None, str(wpath), null_output, "csg",
                    None, src.include_paths_for_wrapper(include_paths), "evaluation", "eval",
                )
            _rebase_diagnostics(ev.diagnostics, wpath, wrapped, src.display_name)
        diag = ev.diagnostics
        results = collect_eval_results(diag.echo_output, len(exprs))
        for expr, r in zip(exprs, results, strict=False):
            r["expression"] = expr
        other_echo = [e for e in diag.echo_output if "__OPENSCAD_MCP_EVAL__" not in e]
        response: Dict[str, Any] = {
            "success": not diag.errors,
            "results": results,
            "errors": diag.errors,
            "warnings": diag.warnings,
        }
        if other_echo:
            response["echo_output"] = other_echo
        hints = diag.hints()
        if hints:
            response["hints"] = hints
        return response
    except Exception as e:
        if ctx:
            await ctx.error(f"scad_eval failed: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# reference
# ============================================================================


@mcp.tool()
async def reference(
    topic: str = "conventions",
    query: Optional[str] = None,
    detailed: bool = False,
    diameter_mm: Optional[float] = None,
    shaft_mm: Optional[float] = None,
    bore_mm: Optional[float] = None,
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Sourced engineering data for 3D-printed parts, each entry with a
    confidence label (standard / consensus / calibrate). topics: fits
    (clearances per side and diametral, $slop), fasteners (metric screws,
    clearance and tap holes), inserts (heat-set), bearings, magnets,
    joints (dovetail, snap, press, hinge; BOSL2 module names), parts
    (purchased-part catalog: envelope, mount pattern, shaft, mass, a BOSL2
    module with named anchors and a clearance mask; write it with
    model(action=create, template="part:<id>")), conventions, cheatsheet,
    dfm (FDM design rules), materials. query filters entries.
    topic="fits" with diameter_mm=3.3 names what that hole is (top 3
    with deltas); with shaft_mm and bore_mm it names the fit class.
    """
    from . import reference as ref

    try:
        if topic == "list":
            return {"success": True, "topics": ref.list_topics()}
        if topic == "fits" and diameter_mm is not None:
            return {"success": True, "topic": "fits", "diameter_mm": diameter_mm,
                    "matches": ref.fit_for_diameter(float(diameter_mm))}
        if topic == "fits" and shaft_mm is not None and bore_mm is not None:
            data = ref.fit_class(float(shaft_mm), float(bore_mm))
            data.update({"success": True, "topic": "fits"})
            return data
        data = ref.lookup(topic, query, detailed=detailed)
        data["success"] = True
        return data
    except Exception as e:
        if ctx:
            await ctx.error(f"reference failed: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# Assembly export service (per-part meshes, never unioned)
# ============================================================================



@dataclass
class ExportedPart:
    name: str
    stl_path: Path
    cached: bool
    diagnostics: Diagnostics
    key: str
    empty: bool = False


def _parts_cache_dir() -> Path:
    d = get_config().cache.directory / "parts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _quality_to_variables(quality: Any) -> Dict[str, Any]:
    """Accept quality as preset name, fn integer, or {fn, fa, fs}."""
    if quality is None:
        return {}
    if isinstance(quality, (int, float)) and not isinstance(quality, bool):
        return {"$fn": int(quality)}
    if isinstance(quality, str):
        if quality.isdigit():
            return {"$fn": int(quality)}
        if quality in QUALITY_PRESETS:
            return dict(QUALITY_PRESETS[quality])
        raise ValueError(
            f"Invalid quality preset '{quality}': must be draft, normal, high, an integer $fn, "
            "or {fn, fa, fs}"
        )
    if isinstance(quality, dict):
        return {f"${k}": v for k, v in quality.items() if k in ("fn", "fa", "fs")}
    raise ValueError("quality must be draft|normal|high, an integer, or {fn, fa, fs}")


def _part_cache_key(
    source: _ModelSource, asm: Assembly, part: Part, variables: Dict[str, Any],
    include_paths: Optional[List[str]],
) -> str:
    hasher = hashlib.sha256()
    _hash_field(hasher, source.text.encode())
    base_dir = source.model_dir or source.wrapper_dir or Path(get_config().temp_dir)
    _hash_field(hasher, _static_dependency_fingerprint(source.text, base_dir, include_paths))
    _hash_field(hasher, asm.cache_material(part))
    _hash_field(hasher, variables)
    _hash_field(hasher, include_paths or [])
    _hash_field(hasher, f"{find_openscad()}|{get_openscad_capabilities().get('version')}")
    return hasher.hexdigest()


def _export_part_sync(
    source: _ModelSource, asm: Assembly, part: Part, variables: Dict[str, Any],
    include_paths: Optional[List[str]],
) -> ExportedPart:
    """Export one placed part to the on-disk mesh cache (or reuse it)."""
    from .wrappers import build_wrapper

    config = get_config()
    key = _part_cache_key(source, asm, part, variables, include_paths)
    cache_dir = _parts_cache_dir()
    stl = cache_dir / f"{key}.stl"
    manifest = cache_dir / f"{key}.json"
    if config.cache.enabled and stl.exists() and manifest.exists():
        try:
            data = json.loads(manifest.read_text())
            diag = Diagnostics(returncode=0)
            for rec in data.get("records", []):
                diag.records.append(DiagnosticRecord(
                    severity=rec.get("severity", "WARNING"), message=rec.get("message", ""),
                    file=rec.get("file"), line=rec.get("line"), trace=list(rec.get("trace", [])),
                ))
            diag.statistics = dict(data.get("statistics") or {})
            return ExportedPart(part.name, stl, True, diag, key, empty=bool(data.get("empty")))
        except (OSError, ValueError):
            pass

    wrapped = build_wrapper(source.text, variables, extra_body=asm.part_body(part))
    wpath = source.wrapper_file(wrapped)
    tmp_stl = Path(config.temp_dir) / f"part_{part.name}_{uuid.uuid4().hex[:8]}.stl"
    ev = _evaluate_scad(
        None, str(wpath), str(tmp_stl), None, None,
        source.include_paths_for_wrapper(include_paths), "export", f"part-{part.name}",
    )
    _rebase_diagnostics(ev.diagnostics, wpath, wrapped, source.display_name)
    diag = ev.diagnostics
    empty = False
    if ev.output_path is None:
        if diag.empty_output:
            empty = True
            diag.records = [r for r in diag.records if not r.message.startswith("OpenSCAD exited")]
            diag.returncode = 0
            tmp_stl.write_text("solid empty\nendsolid empty\n")
        else:
            detail = "; ".join(diag.errors) if diag.errors else "OpenSCAD produced no output"
            raise RuntimeError(f"part '{part.name}': export failed: {detail}")
    try:
        tmp_stl.replace(stl)
    except OSError:
        shutil.copyfile(tmp_stl, stl)
        tmp_stl.unlink(missing_ok=True)
    manifest.write_text(json.dumps({
        "part": part.name, "records": [r.to_dict() for r in diag.records],
        "statistics": diag.statistics, "empty": empty,
    }))
    if config.cache.enabled:
        _evict_cache_if_needed()
    return ExportedPart(part.name, stl, False, diag, key, empty=empty)


async def _export_parts(
    source: _ModelSource, asm: Assembly, variables: Dict[str, Any],
    include_paths: Optional[List[str]], ctx: Optional[Context] = None,
) -> Dict[str, ExportedPart]:
    """Export every part in parallel under the render semaphore."""
    loop = asyncio.get_running_loop()
    semaphore = get_render_semaphore()

    async def _one(part: Part) -> ExportedPart:
        async with semaphore:
            return await loop.run_in_executor(
                None, _export_part_sync, source, asm, part, variables, include_paths
            )

    results = await asyncio.gather(*[_one(p) for p in asm.parts], return_exceptions=True)
    out: Dict[str, ExportedPart] = {}
    errors: List[str] = []
    for part, res in zip(asm.parts, results, strict=False):
        if isinstance(res, Exception):
            errors.append(str(res))
        else:
            out[part.name] = res
    if errors:
        raise RuntimeError("; ".join(errors))
    if ctx:
        hits = sum(1 for r in out.values() if r.cached)
        await ctx.info(f"exported {len(out)} part(s), {hits} from cache")
    return out


_mesh_cache: Dict[str, Tuple[float, Any]] = {}
_MESH_CACHE_MAX = 64


def _load_mesh(exported: ExportedPart) -> Any:
    """Load (and BVH-cache) a part mesh; keyed on the STL path and mtime."""
    from . import geom

    try:
        mtime = exported.stl_path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    cached = _mesh_cache.get(str(exported.stl_path))
    if cached is not None and cached[0] == mtime:
        return cached[1]
    mesh = geom.Mesh.from_stl(exported.stl_path, name=exported.name)
    if len(_mesh_cache) >= _MESH_CACHE_MAX:
        _mesh_cache.pop(next(iter(_mesh_cache)))
    _mesh_cache[str(exported.stl_path)] = (mtime, mesh)
    return mesh


def _resolve_check_inputs(
    scad_content: Optional[str], scad_file: Optional[str], check_file: Optional[str],
    parts: Any, frames: Any, quality: Any, variables: Any, checks: Any,
) -> Tuple[Assembly, Optional[str], Optional[str], Dict[str, Any]]:
    """Build the Assembly from either a check file or inline arguments."""
    parsed_vars = parse_dict_param(variables, {})
    _validate_variable_names(parsed_vars)
    asm: Assembly
    if check_file:
        _check_allowed_path(check_file, "Check file")
        cf = Path(check_file)
        if not cf.exists():
            raise FileNotFoundError(f"check file not found: {check_file}")
        asm = load_check_file(cf.read_text(), scad_file=scad_file)
        if asm.scad_file and not Path(asm.scad_file).is_absolute():
            asm.scad_file = str((cf.parent / asm.scad_file).resolve())
        scad_file = scad_file or asm.scad_file
        if parts:
            asm.parts = parse_parts(parts)
        if checks:
            asm.checks = list(checks)
    else:
        if not parts:
            raise ValueError("parts=[{name, code, place?}] is required unless check_file is given")
        asm = parse_assembly({
            "parts": parts, "frames": frames or {}, "quality": {}, "checks": checks or [],
        })
    if quality is not None:
        qv = _quality_to_variables(quality)
        for k, v in qv.items():
            asm.quality[k.lstrip("$")] = v
    all_vars = dict(asm.variables)
    all_vars.update(parsed_vars)
    all_vars.update(asm.quality_variables())
    if not scad_content and not scad_file:
        raise ValueError("scad_content, scad_file, or a check file with a 'model:' key is required")
    return asm, scad_content, scad_file, all_vars


def _eval_in_model_scope(
    src: "_ModelSource",
    exprs: List[str],
    all_vars: Dict[str, Any],
    include_paths: Optional[List[str]],
    tag: str,
) -> List[Dict[str, Any]]:
    """Evaluate SCAD expressions in the model's scope; one CSG-mode run, no CGAL."""
    from .wrappers import build_wrapper, collect_eval_results

    wrapped = build_wrapper(src.text, all_vars, extra_body="\n".join(
        f'echo("__OPENSCAD_MCP_EVAL__", {i}, ({e}));' for i, e in enumerate(exprs)
    ))
    wpath = src.wrapper_file(wrapped)
    null_output = "NUL" if platform.system() == "Windows" else "/dev/null"
    ev = _evaluate_scad(None, str(wpath), null_output, "csg", None,
                        src.include_paths_for_wrapper(include_paths), "validation", tag)
    return collect_eval_results(ev.diagnostics.echo_output, len(exprs))


def _fn_of(all_vars: Dict[str, Any]) -> Optional[int]:
    v = all_vars.get("$fn")
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _curved_radius(features_by_part: Dict[str, Any], names: Sequence[str]) -> Optional[float]:
    best: Optional[float] = None
    for n in names:
        fs = features_by_part.get(n)
        if not fs:
            continue
        for f in getattr(fs, "features", []):
            r = float(getattr(f, "nominal_d_mm", 0.0)) / 2.0
            if r > 0 and (best is None or r > best):
                best = r
    return best


def _csg_dump_sync(
    source: _ModelSource, asm: Assembly, part: Part, variables: Dict[str, Any],
    include_paths: Optional[List[str]],
) -> str:
    """The evaluated CSG tree of one placed part (assembly frame)."""
    from .wrappers import build_wrapper

    config = get_config()
    # Cached next to the part mesh under the same key: the dump depends on
    # exactly the same inputs as the mesh (source closure, placement,
    # variables, $fn, binary).
    key = _part_cache_key(source, asm, part, variables, include_paths)
    cached = _parts_cache_dir() / f"{key}.csg"
    if config.cache.enabled and cached.exists():
        try:
            return cached.read_text(errors="replace")
        except OSError:
            pass
    wrapped = build_wrapper(source.text, variables, extra_body=asm.part_body(part))
    wpath = source.wrapper_file(wrapped)
    out = Path(config.temp_dir) / f"csg_{part.name}_{uuid.uuid4().hex[:8]}.csg"
    try:
        ev = _evaluate_scad(
            None, str(wpath), str(out), None, None,
            source.include_paths_for_wrapper(include_paths), "export", f"csg-{part.name}",
        )
        if ev.output_path is None:
            return ""
        text = ev.output_path.read_text(errors="replace")
        if config.cache.enabled:
            try:
                cached.write_text(text)
            except OSError:
                pass
            _evict_cache_if_needed()
        return text
    finally:
        if out.exists():
            out.unlink()


async def _features_for_parts(
    source: _ModelSource, asm: Assembly, variables: Dict[str, Any],
    include_paths: Optional[List[str]], names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    from . import csgfeatures

    loop = asyncio.get_running_loop()
    semaphore = get_render_semaphore()
    targets = [p for p in asm.parts if names is None or p.name in names]

    async def _one(part: Part):
        async with semaphore:
            text = await loop.run_in_executor(
                None, _csg_dump_sync, source, asm, part, variables, include_paths
            )
        return part.name, csgfeatures.extract_features(text) if text else None

    results = await asyncio.gather(*[_one(p) for p in targets])
    return {n: fs for n, fs in results if fs is not None}


# ============================================================================
# check
# ============================================================================


@mcp.tool()
async def check(
    scad_content: Optional[str] = None,
    scad_file: Optional[str] = None,
    check_file: Optional[str] = None,
    mode: str = "interference",
    parts: Optional[List[Dict[str, Any]]] = None,
    frames: Optional[Dict[str, Any]] = None,
    pairs: Any = "all",
    tolerance_mm: float = 0.0,
    min_mm: Optional[float] = None,
    kind: Optional[str] = None,
    moving: Optional[str] = None,
    axis: Optional[List[float]] = None,
    center: Optional[List[float]] = None,
    vector: Optional[List[float]] = None,
    range: Optional[List[float]] = None,
    steps: int = 36,
    against: Any = "all",
    checks: Optional[List[Dict[str, Any]]] = None,
    quality: Any = None,
    variables: Optional[Dict[str, Any]] = None,
    include_paths: Optional[List[str]] = None,
    volume: bool = False,
    response_format: str = "concise",
    ctx: Optional[Context] = None,
) -> Dict[str, Any]:
    """
    Relations between named parts, exported separately (never unioned),
    cached, in the assembly frame. parts=[{name, code, place?, frame?,
    ghost?, mass_g?, motion?}] (code "lid();", place "translate(P)") or
    check_file= (YAML/JSON: frames, quality, parts, checks, model).
    mode "interference": clear|contact|interference with penetration depth
    and witness point (flush contact is contact). "clearance": exact
    minimum distance, closest points, min_mm. "contact": area, normal,
    plane; kind=static|sliding. "alignment": coaxial hole stacks across
    parts, offsets, orphans. "motion": sweep moving= about axis/center over
    range deg, or along vector over range mm; full turns add a certificate.
    "rules": every rule in the check file; exit_code 0/1/2. Any number or
    vector in a rule or motion may be a SCAD expression string ("[BOLT_R,
    0, BASE_H]") evaluated in the model's scope. quality:
    draft|normal|high or $fn, echoed per row; distances inside the
    tessellation error bound are UNRESOLVED. volume=true cross-checks with
    OpenSCAD's intersection volume.
    """
    from .assembly import apply_expression_values, collect_expression_slots
    from .checks import Quality, RuleEngine, exit_code, summarize

    t0 = time.time()
    mode = (mode or "interference").lower()
    try:
        if mode not in ("interference", "clearance", "contact", "alignment", "motion", "rules"):
            raise ValueError("mode must be one of: interference, clearance, contact, alignment, motion, rules")
        asm, scad_content, scad_file, all_vars = _resolve_check_inputs(
            scad_content, scad_file, check_file, parts, frames, quality, variables, checks
        )
        _validate_include_paths(include_paths)
        detailed = (response_format or "concise").lower() == "detailed"

        timings: Dict[str, float] = {}
        with _ModelSource(scad_content, scad_file, "check") as src:
            # Expression-valued numbers ("[BOLT_R, 0, BASE_H]") in rules and
            # motion blocks are evaluated in the model's scope first, so the
            # rules below see plain numbers that track the design.
            slots = collect_expression_slots(asm)
            if slots:
                t1 = time.time()
                values = await asyncio.get_running_loop().run_in_executor(
                    None, _eval_in_model_scope, src, [sl.expr for sl in slots], all_vars,
                    include_paths, "expr",
                )
                apply_expression_values(slots, values)
                timings["expressions_s"] = round(time.time() - t1, 3)
            t1 = time.time()
            exported = await _export_parts(src, asm, all_vars, include_paths, ctx)
            timings["export_s"] = round(time.time() - t1, 3)
            t1 = time.time()
            meshes = {n: _load_mesh(e) for n, e in exported.items() if not e.empty}
            timings["load_s"] = round(time.time() - t1, 3)
            empties = [n for n, e in exported.items() if e.empty]

            # The CSG dump feeds alignment and the curved-feature radius that
            # sets the tessellation error bound on every distance; it is
            # cached with the part mesh, so it is cheap after the first run.
            need_features = True
            features_by_part: Dict[str, Any] = {}
            if need_features:
                t1 = time.time()
                try:
                    features_by_part = await _features_for_parts(src, asm, all_vars, include_paths)
                except Exception as exc:  # features are optional provenance
                    logger.warning("feature extraction failed: %s", exc)
                timings["features_s"] = round(time.time() - t1, 3)

            qual = Quality(fn=_fn_of(all_vars), curved_radius_mm=_curved_radius(features_by_part, list(meshes)))

            loop = asyncio.get_running_loop()

            def predicate_runner(exprs: List[str]) -> List[Dict[str, Any]]:
                return _eval_in_model_scope(src, exprs, all_vars, include_paths, "pred")

            def feature_provider() -> Dict[str, Any]:
                from . import csgfeatures

                return csgfeatures.align_features(
                    {n: list(fs.features) for n, fs in features_by_part.items()},
                    tolerance_mm=float(tolerance_mm or 0.2),
                )

            def volume_cross_check(a: str, b: str) -> Optional[float]:
                if not volume:
                    return None
                from .wrappers import build_wrapper as _bw

                body = ("!intersection() {\n    " + asm.part_statement(asm.part(a)) + "\n    "
                        + asm.part_statement(asm.part(b)) + "\n}\n")
                wrapped = _bw(src.text, all_vars, extra_body=body)
                wpath = src.wrapper_file(wrapped)
                out = Path(get_config().temp_dir) / f"ix_{uuid.uuid4().hex[:8]}.stl"
                try:
                    ev = _evaluate_scad(None, str(wpath), str(out), None, None,
                                        src.include_paths_for_wrapper(include_paths), "export", "ix")
                    if ev.output_path is None:
                        return 0.0
                    from . import mesh as meshlib

                    return float(meshlib.analyze_stl(ev.output_path).volume)
                finally:
                    if out.exists():
                        out.unlink()

            def printability_provider(name: str, rule: Dict[str, Any]) -> Dict[str, Any]:
                from . import printability

                part = asm.part(name)
                orientation = (part.print or {}).get("orientation") or rule.get("orientation")
                return printability.analyze(
                    meshes[name].triangles, orientation=orientation,
                    overhang_deg=float(rule.get("max_overhang_deg", 45)),
                    nozzle_mm=float(rule.get("nozzle_mm", 0.4)),
                    layer_height_mm=rule.get("layer_height_mm"),
                ).to_dict(detailed=False)

            engine = RuleEngine(
                asm, meshes, qual,
                predicate_runner=predicate_runner,
                feature_provider=feature_provider if features_by_part else None,
                printability_provider=printability_provider,
                volume_cross_check=volume_cross_check if volume else None,
            )

            if mode == "rules":
                rules = asm.checks
                if not rules:
                    raise ValueError("no checks: give check_file with a 'checks:' list or checks=[...]")
                t1 = time.time()
                rows = await loop.run_in_executor(None, engine.run, rules)
                timings["rules_s"] = round(time.time() - t1, 3)
            else:
                rule: Dict[str, Any] = {"rule": mode, "pairs": pairs}
                if mode == "interference":
                    rule["tolerance_mm"] = float(tolerance_mm)
                elif mode == "clearance":
                    rule["min_mm"] = float(min_mm if min_mm is not None else 0.0)
                elif mode == "contact":
                    rule["kind"] = kind or "static"
                    if min_mm is not None:
                        rule["min_gap_mm" if kind == "sliding" else "min_area_mm2"] = float(min_mm)
                elif mode == "alignment":
                    rule["tolerance_mm"] = float(tolerance_mm or 0.2)
                elif mode == "motion":
                    if not moving:
                        raise ValueError("mode=motion needs moving=<part name>")
                    rule = {"rule": "sweep", "moving": moving, "against": against, "steps": int(steps)}
                    if vector:
                        rule.update({"type": "translate", "vector": vector, "range_mm": range or [0, 10]})
                    else:
                        rule.update({"type": "rotate", "axis": axis or [0, 0, 1],
                                     "center": center or [0, 0, 0], "range_deg": range or [0, 360]})
                t1 = time.time()
                rows = await loop.run_in_executor(None, engine.run, [rule])
                timings["rules_s"] = round(time.time() - t1, 3)

        for name in empties:
            rows.append({
                "rule": mode if mode != "rules" else "parts",
                "subject": [name],
                "status": "UNRESOLVED",
                "state": "empty",
                "note": (
                    f"part '{name}' produced no geometry (unknown module, empty difference, "
                    "or geometry guarded by $preview); no relation involving it was checked"
                ),
            })
        if not detailed:
            for r in rows:
                r.pop("closest", None)
                if r.get("status") == "PASS" and r.get("rule") in ("interference", "clearance"):
                    r.pop("normal", None)
        summary = summarize(rows)
        result: Dict[str, Any] = {
            "success": True,
            "mode": mode,
            "units": "mm",
            "frame": "assembly",
            "parts": [p.name + (" (ghost)" if p.ghost else "") for p in asm.parts],
            "quality": qual.to_dict(),
            "cache": {
                "parts": len(exported),
                "hits": sum(1 for e in exported.values() if e.cached),
                "misses": sum(1 for e in exported.values() if not e.cached),
            },
            "pairs_evaluated": engine.pairs_evaluated,
            "pairs_aabb_separated": engine.pairs_aabb_separated,
            "findings": rows,
            "summary": summary,
            "exit_code": exit_code(rows),
            "timing_s": round(time.time() - t0, 3),
            "timings": timings,
        }
        if empties:
            result["empty_parts"] = empties
        warnings = [w for e in exported.values() for w in e.diagnostics.warnings]
        if warnings:
            result["warnings"] = warnings[:20]
        return result
    except Exception as e:
        if ctx:
            await ctx.error(f"check failed: {e}")
        return {"success": False, "mode": mode, "error": str(e)}


# ============================================================================
# measure: extended modes (probe, features, mass over parts, printability,
# orientation, anchors)
# ============================================================================

_BOX_ANCHORS = [
    "CENTER", "TOP", "BOTTOM", "LEFT", "RIGHT", "FRONT", "BACK",
    "TOP+LEFT", "TOP+RIGHT", "TOP+FRONT", "TOP+BACK",
    "BOTTOM+LEFT", "BOTTOM+RIGHT", "BOTTOM+FRONT", "BOTTOM+BACK",
]


def _anchor_probe_body(asm: Assembly, part: Part, names: List[str]) -> str:
    """Children of the part's module that echo every anchor's frame.

    Runs with the model composed by ``include`` (the wrapper), so BOSL2's
    ``$transform`` is the true world matrix of the parent and the echoed
    world position is in the assembly frame.
    """
    code = part.code.strip().rstrip(";")
    lines = []
    for n in names:
        expr = n if n in _BOX_ANCHORS else f'"{n}"'
        lines.append(
            f'echo("__ANCHOR__", "{n}", _find_anchor({expr}, $parent_geom)[1], '
            f'_find_anchor({expr}, $parent_geom)[2], '
            f'apply($transform, _find_anchor({expr}, $parent_geom)[1]));'
        )
    children = "\n        ".join(lines)
    placement = asm.placement_expr(part)
    return (
        f"!union() {{\n    {placement} {{\n    {code} {{\n        {children}\n    }}\n    }}\n}}\n"
    )


def _parse_anchor_echo(echo_lines: List[str]) -> List[Dict[str, Any]]:
    from .wrappers import parse_echo_values

    out = []
    for line in echo_lines:
        if "__ANCHOR__" not in line:
            continue
        try:
            vals = parse_echo_values(line)
        except ValueError:
            continue
        if len(vals) < 5 or vals[0] != "__ANCHOR__":
            continue
        name, local_pos, direction, world_pos = vals[1], vals[2], vals[3], vals[4]
        out.append({
            "name": name,
            "local": local_pos,
            "direction": direction,
            "assembly": world_pos,
        })
    return out


async def _measure_extended(
    mode: str,
    scad_content: Optional[str],
    scad_file: Optional[str],
    parsed_vars: Dict[str, Any],
    include_paths: Optional[List[str]],
    parts: Any,
    part: Optional[str],
    points: Any,
    rays: Any,
    polyline: Any,
    orientation: Any,
    about_axis: Any,
    material: Optional[str],
    density_g_cm3: Optional[float],
    nozzle_mm: float,
    layer_height_mm: Optional[float],
    quality: Any,
    detailed: bool,
    ctx: Optional[Context],
) -> Dict[str, Any]:
    from . import geom

    loop = asyncio.get_running_loop()
    result: Dict[str, Any] = {"success": True, "mode": mode, "units": "mm"}

    # Assembly: explicit parts, or the whole model as one part named "model".
    if parts:
        part_list = parse_parts(parts)
    elif part:
        part_list = parse_parts([{"name": part.split("(")[0].strip() or "part", "code": part}])
    else:
        part_list = []
    all_vars = dict(parsed_vars)
    if quality is not None:
        all_vars.update(_quality_to_variables(quality))
    asm = Assembly(parts=part_list, quality={k.lstrip("$"): v for k, v in all_vars.items() if k in ("$fn", "$fa", "$fs")})
    result["quality"] = {"fn": _fn_of(all_vars)}

    with _ModelSource(scad_content, scad_file, "measure") as src:
        if mode in ("probe", "printability", "orientation") or (mode == "mass" and (parts or about_axis)):
            if not part_list:
                # Whole model as one part: export via the plain measure path.
                stats, diag = await loop.run_in_executor(
                    None, _measure_source, src, all_vars, include_paths
                )
                meshes = {"model": geom.Mesh(list(_triangles_of(src, all_vars, include_paths)), name="model")}
                exported: Dict[str, ExportedPart] = {}
            else:
                exported = await _export_parts(src, asm, all_vars, include_paths, ctx)
                meshes = {n: _load_mesh(e) for n, e in exported.items() if not e.empty}
            result["frame"] = "assembly" if part_list else "local"

        if mode == "probe":
            probe_meshes = {n: m for n, m in meshes.items() if not (part_list and asm.part(n).ghost)}
            out_points = []
            for p in parse_list_param(points, []) if points else []:
                pt = tuple(float(v) for v in p[:3])
                res = geom.classify_point(probe_meshes, pt)
                out_points.append({"at": list(pt), "state": res["state"], "parts": res["parts"]}
                                  | ({"winding": res.get("winding")} if detailed else {}))
            out_rays = []
            for r in (rays or []):
                if isinstance(r, dict):
                    origin = tuple(float(v) for v in r["origin"])
                    direction = tuple(float(v) for v in r["direction"])
                    max_d = r.get("max_distance")
                else:
                    origin = tuple(float(v) for v in r[:3])
                    direction = tuple(float(v) for v in r[3:6])
                    max_d = r[6] if len(r) > 6 else None
                hits = geom.ray_cast_parts(probe_meshes, origin, direction, max_d)
                out_rays.append({
                    "origin": list(origin), "direction": list(direction),
                    "first_hit": None if not hits else {
                        "part": hits[0].part, "distance_mm": round(hits[0].t, 4),
                        "point": [round(v, 4) for v in hits[0].point],
                    },
                    "crossings": [
                        {"part": h.part, "distance_mm": round(h.t, 4), "entering": h.entering}
                        for h in hits[: (50 if detailed else 12)]
                    ],
                })
            out_polyline = None
            if polyline:
                pts = [tuple(float(v) for v in p[:3]) for p in polyline]
                out_polyline = geom.polyline_clear(probe_meshes, pts)
            result.update({"points": out_points, "rays": out_rays})
            if out_polyline is not None:
                result["polyline"] = out_polyline
            result["parts"] = list(meshes)
            return result

        if mode == "features":
            from . import csgfeatures

            if part_list:
                feats = await _features_for_parts(src, asm, all_vars, include_paths)
                per: Dict[str, Any] = {}
                for n, fs in feats.items():
                    per[n] = csgfeatures.to_dict(fs, detailed)
                result["parts"] = per
                result["frame"] = "assembly"
            else:
                whole = Part(name="model", code="__whole__();")
                # Whole-model features: dump the model's own top-level geometry.
                from .wrappers import build_wrapper

                wrapped = build_wrapper(src.text, all_vars)
                wpath = src.wrapper_file(wrapped)
                out = Path(get_config().temp_dir) / f"csg_{uuid.uuid4().hex[:8]}.csg"
                try:
                    ev = await loop.run_in_executor(
                        None, _evaluate_scad, None, str(wpath), str(out), None, None,
                        src.include_paths_for_wrapper(include_paths), "export", "csg",
                    )
                    text = ev.output_path.read_text(errors="replace") if ev.output_path else ""
                finally:
                    if out.exists():
                        out.unlink()
                fs = csgfeatures.extract_features(text) if text else None
                result.update(csgfeatures.to_dict(fs, detailed) if fs else {"features": [], "note": "no CSG output"})
                result["frame"] = "local"
                del whole
            result["note"] = (
                "circular subtractive features from the CSG tree (post-expansion, pre-boolean); "
                "not holes in the finished part. Features inside hull()/minkowski() are masked."
            )
            return result

        if mode == "mass":
            from . import massprops

            entries = []
            for p in part_list:
                if p.name not in meshes and not p.mass_g:
                    continue
                if p.mass_g and p.name not in meshes:
                    mp = massprops.point_mass(p.mass_g, (0.0, 0.0, 0.0))
                else:
                    tris = meshes[p.name].triangles
                    mat = p.material or material
                    dens = p.density_g_cm3 or density_g_cm3
                    if p.mass_g is None and dens is None and not mat:
                        mat = "PLA"
                    mp = massprops.mass_properties(tris, density_g_cm3=dens, material=mat, mass_g=p.mass_g)
                entries.append((p.name, mp))
            if not entries:
                raise ValueError("mode=mass over parts needs parts=[{name, code, material|mass_g}]")
            composed = massprops.compose(entries)
            result.update(composed)
            not_wt = [n for n, mp in entries if not mp.is_watertight]
            if not_wt:
                result["warning"] = f"not watertight, numbers unreliable: {not_wt}"
            if about_axis:
                pt = tuple(float(v) for v in about_axis[0])
                dr = tuple(float(v) for v in about_axis[1])
                total = 0.0
                per_axis = {}
                for n, mp in entries:
                    val = mp.inertia_about_axis(pt, dr)
                    per_axis[n] = round(val, 3)
                    total += val
                result["inertia_about_axis_g_mm2"] = round(total, 3)
                result["inertia_about_axis_kg_m2"] = total * 1e-9
                if detailed:
                    result["inertia_about_axis_per_part"] = per_axis
            return result

        if mode in ("printability", "orientation"):
            from . import printability

            target = None
            if part_list:
                if len(part_list) != 1:
                    raise ValueError("mode=printability/orientation takes exactly one part")
                target = part_list[0].name
            tris = meshes[target or "model"].triangles
            if mode == "orientation":
                cands = await loop.run_in_executor(
                    None, lambda: printability.orientation_candidates(tris)
                )
                result["candidates"] = cands
                result["note"] = (
                    "sorted by overhang area then height; no winner is chosen: pick by which "
                    "faces must look good and how the part loads"
                )
                return result
            facts = await loop.run_in_executor(
                None,
                lambda: printability.analyze(
                    tris, orientation=orientation, nozzle_mm=nozzle_mm,
                    layer_height_mm=layer_height_mm,
                ),
            )
            result.update(facts.to_dict(detailed=detailed))
            result["note"] = "facts only; thresholds and rules live in validate(mode=printability)"
            return result

        if mode == "anchors":
            if not part_list:
                raise ValueError("mode=anchors needs part=\"module();\" or parts=[...] (BOSL2 attachables)")
            from .wrappers import build_wrapper

            names = list(_BOX_ANCHORS)
            extra_names = parse_list_param(points, []) if points else []
            names += [str(n) for n in extra_names]
            per_part: Dict[str, Any] = {}
            for p in part_list:
                wrapped = build_wrapper(src.text, all_vars, extra_body=_anchor_probe_body(asm, p, names))
                wpath = src.wrapper_file(wrapped)
                null_output = "NUL" if platform.system() == "Windows" else "/dev/null"
                ev = await loop.run_in_executor(
                    None, _evaluate_scad, None, str(wpath), null_output, "csg", None,
                    src.include_paths_for_wrapper(include_paths), "validation", "anchors",
                )
                _rebase_diagnostics(ev.diagnostics, wpath, wrapped, src.display_name)
                anchors = _parse_anchor_echo(ev.diagnostics.echo_output)
                entry: Dict[str, Any] = {"anchors": anchors, "frame": "assembly"}
                if not anchors:
                    entry["note"] = (
                        "no anchors echoed: the module is not a BOSL2 attachable, or BOSL2 is "
                        "not included by the model"
                    )
                    entry["errors"] = ev.diagnostics.errors
                    entry["warnings"] = ev.diagnostics.warnings[:5]
                per_part[p.name] = entry
            result["parts"] = per_part
            result["note"] = (
                "positions are in the assembly frame because the model is composed with include; "
                "extra anchor names can be passed via points=[\"name\", ...]"
            )
            return result

        raise ValueError(f"unhandled mode {mode}")


def _triangles_of(src: _ModelSource, variables: Dict[str, Any], include_paths: Optional[List[str]]):
    """Triangles of the whole model via a temporary STL export."""
    from . import mesh as meshlib

    config = get_config()
    out = Path(config.temp_dir) / f"whole_{uuid.uuid4().hex[:8]}.stl"
    try:
        ev = _evaluate_scad(
            src.scad_content, None if src.scad_content else src.scad_file, str(out), None,
            variables, include_paths, "export", "whole",
        )
        if ev.output_path is None:
            return []
        return meshlib.load_stl(ev.output_path)
    finally:
        if out.exists():
            out.unlink()



async def _predicate_sweep(
    scad_content: Optional[str], scad_file: Optional[str], parsed_vars: Dict[str, Any],
    include_paths: Optional[List[str]], exprs: List[str], sweep: Dict[str, Any],
) -> Dict[str, Any]:
    """Re-evaluate predicates across values of one variable; report the crossing."""
    from .wrappers import build_wrapper, collect_eval_results

    variable = str(sweep.get("variable") or "")
    values = list(sweep.get("values") or [])
    if not variable or not values:
        raise ValueError("sweep needs {variable, values: [...]}")
    _validate_variable_names({variable: 0})
    if len(values) > 12:
        raise ValueError("sweep is capped at 12 values; narrow the range")
    loop = asyncio.get_running_loop()
    semaphore = get_render_semaphore()
    null_output = "NUL" if platform.system() == "Windows" else "/dev/null"

    async def _point(value: Any) -> Dict[str, Any]:
        vars_ = dict(parsed_vars)
        vars_[variable] = value
        with _ModelSource(scad_content, scad_file, "sweep") as src:
            wrapped = build_wrapper(src.text, vars_, extra_body="\n".join(
                f'echo("__OPENSCAD_MCP_EVAL__", {i}, ({e}));' for i, e in enumerate(exprs)
            ))
            wpath = src.wrapper_file(wrapped)
            async with semaphore:
                ev = await loop.run_in_executor(
                    None, _evaluate_scad, None, str(wpath), null_output, "csg", None,
                    src.include_paths_for_wrapper(include_paths), "validation", "sweep",
                )
        res = collect_eval_results(ev.diagnostics.echo_output, len(exprs))
        return {
            "value": value,
            "results": [bool(r.get("evaluated") and r.get("value") is True) for r in res],
            "values": [r.get("value") for r in res],
            "all_pass": all(r.get("evaluated") and r.get("value") is True for r in res),
        }

    points = await asyncio.gather(*[_point(v) for v in values])
    first_failure = next((p for p in points if not p["all_pass"]), None)
    passes = [p["all_pass"] for p in points]
    monotonic = passes == sorted(passes) or passes == sorted(passes, reverse=True)
    crossing = None
    for a, b in zip(points, points[1:], strict=False):
        if a["all_pass"] != b["all_pass"]:
            crossing = {"between": [a["value"], b["value"]], "from_pass": a["all_pass"]}
            break
    return {
        "variable": variable,
        "points": points,
        "first_failure": None if first_failure is None else first_failure["value"],
        "crossing": crossing,
        "monotonic": monotonic,
    }


def _apply_lint_fixes(path: Path, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Apply safe rewrite plans from the BOSL2 lint to the model file."""
    from . import analysis

    out = []
    for f in findings:
        entry = dict(f)
        used = f.get("used_file")
        try:
            if used:
                # A used file that draws geometry at top level cannot simply be
                # included; the plan then points the include at a private
                # shadow copy with the instantiations stripped, kept next to
                # the project so the rewritten file keeps working.
                shadow_dir = path.parent / ".openscad-mcp-shadow"
                plan = analysis.plan_use_to_include_rewrite(path, Path(used), shadow_dir=shadow_dir)
                if plan.safe:
                    analysis.apply_rewrite(plan)
                    entry["fixed"] = True
                    entry["fix_applied"] = (
                        "use <> rewritten to include <> (no name collisions); a shadow copy "
                        f"without top-level geometry may have been written under {shadow_dir}"
                    )
                else:
                    entry["fixed"] = False
                    entry["fix_skipped"] = "; ".join(plan.reasons)
        except Exception as exc:
            entry["fixed"] = False
            entry["fix_skipped"] = str(exc)
        out.append(entry)
    return out


async def _validate_printability(
    scad_content, scad_file, variables, include_paths, orientation, profile, ctx
) -> Dict[str, Any]:
    """Thin rules layer over measure(mode=printability) facts."""
    from . import reference as ref

    facts = await _tool_fn(measure)(
        scad_content=scad_content, scad_file=scad_file, mode="printability",
        variables=variables, include_paths=include_paths, orientation=orientation,
        nozzle_mm=float(profile.get("nozzle_mm", 0.4)),
        layer_height_mm=profile.get("layer_height_mm"), response_format="detailed", ctx=ctx,
    )
    if not facts.get("success"):
        return facts
    thresholds = {
        "overhang_deg": float(profile.get("overhang_deg", 45)),
        "max_unsupported_reach_mm": float(profile.get("max_unsupported_reach_mm", 20)),
        "min_wall_mm": float(profile.get("min_wall_mm", 0.8)),
        "nozzle_mm": float(profile.get("nozzle_mm", 0.4)),
    }
    try:
        dfm = ref.lookup("dfm")
        source = "; ".join(sorted({e.get("source", "") for e in dfm.get("entries", []) if e.get("source")}))[:300]
    except Exception:
        source = ""
    findings: List[Dict[str, Any]] = []
    over = facts.get("overhang") or {}
    for patch in over.get("patches", []):
        reach = patch.get("max_unsupported_reach_mm")
        if reach is not None and reach > thresholds["max_unsupported_reach_mm"]:
            findings.append({
                "code": "unsupported_reach", "severity": "warning",
                "magnitude": {"max_unsupported_reach_mm": reach, "limit_mm": thresholds["max_unsupported_reach_mm"]},
                "at": patch.get("center"), "detail": f"overhang patch of {patch.get('area_mm2')} mm2 at z={patch.get('z_min')}",
            })
    th = facts.get("thickness") or {}
    below = th.get("area_below_nozzle_mm2")
    if below and below > 0:
        findings.append({
            "code": "feature_thinner_than_nozzle", "severity": "error",
            "magnitude": {"area_mm2": below, "min_mm": th.get("min"), "nozzle_mm": thresholds["nozzle_mm"]},
            "at": th.get("min_location"),
        })
    p05 = th.get("p05")
    if p05 is not None and p05 < thresholds["min_wall_mm"]:
        findings.append({
            "code": "thin_wall", "severity": "warning",
            "magnitude": {"p05_mm": p05, "min_wall_mm": thresholds["min_wall_mm"]}, "at": th.get("min_location"),
        })
    islands = facts.get("islands") or {}
    if isinstance(islands, dict) and islands.get("count"):
        findings.append({
            "code": "unsupported_islands", "severity": "warning",
            "magnitude": {"count": islands["count"]},
            "detail": (islands.get("list") or islands.get("items") or [])[:3],
        })
    return {
        "success": True, "mode": "printability",
        "valid": not any(f["severity"] == "error" for f in findings),
        "findings": findings, "thresholds": thresholds, "thresholds_source": source,
        "orientation": facts.get("orientation"), "bed_contact_area_mm2": facts.get("bed_contact_area_mm2"),
        "overhang_area_mm2": over.get("area_mm2"),
    }


async def _export_parts_bundle(
    scad_content, scad_file, output_format, output_path, variables, include_paths, parts,
    quality, ctx,
) -> Dict[str, Any]:
    """Per-part export of an assembly into one 3MF or a directory of STLs."""
    fmt = (output_format or "3mf").lower()
    if fmt not in ("3mf", "stl"):
        raise ValueError("parts export supports output_format='3mf' (one file) or 'stl' (a directory)")
    asm, scad_content, scad_file, all_vars = _resolve_check_inputs(
        scad_content, scad_file, None, parts, None, quality, variables, None
    )
    _validate_include_paths(include_paths)
    config = get_config()
    temp_dir_path = Path(config.temp_dir)
    with _ModelSource(scad_content, scad_file, "bundle") as src:
        exported = await _export_parts(src, asm, all_vars, include_paths, ctx)
    manifest = []
    if fmt == "3mf":
        from . import threemf
        from .analysis import assign_colors

        colors = assign_colors([p.name for p in asm.parts])
        target = Path(output_path) if output_path else temp_dir_path / "exports" / f"assembly_{uuid.uuid4().hex[:8]}.3mf"
        _check_allowed_path(target.parent, "Output directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        objs = []
        for p in asm.parts:
            e = exported[p.name]
            if e.empty:
                manifest.append({"name": p.name, "empty": True})
                continue
            objs.append({"name": p.name, "stl": str(e.stl_path), "color": p.color or colors.get(p.name)})
            manifest.append({"name": p.name, "cached": e.cached, "color": p.color or colors.get(p.name),
                             "ghost": p.ghost, "frame": "assembly"})
        info = threemf.write_3mf_from_stls(str(target), objs)
        return {"success": True, "format": "3mf", "output_path": str(target), "objects": manifest,
                "object_count": info.get("object_count"), "triangle_count": info.get("triangle_count"),
                "file_size_bytes": target.stat().st_size, "frame": "assembly"}
    out_dir = Path(output_path) if output_path else temp_dir_path / "exports" / f"parts_{uuid.uuid4().hex[:8]}"
    _check_allowed_path(out_dir, "Output directory")
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in asm.parts:
        e = exported[p.name]
        dest = out_dir / f"{p.name}.stl"
        shutil.copyfile(e.stl_path, dest)
        manifest.append({"name": p.name, "path": str(dest), "cached": e.cached, "empty": e.empty,
                         "frame": "assembly"})
    return {"success": True, "format": "stl", "output_path": str(out_dir), "objects": manifest,
            "frame": "assembly"}

# ============================================================================
# MCP Resources
# ============================================================================


@mcp.resource("openscad://conventions", mime_type="text/plain")
def conventions_resource() -> str:
    """Assembly and coordinate conventions the model should follow."""
    return _server_instructions()


@mcp.resource("openscad://cheatsheet", mime_type="text/plain")
def cheatsheet_resource() -> str:
    """OpenSCAD syntax reminders for things language models get wrong."""
    from .reference import cheatsheet

    return cheatsheet()


@mcp.resource("openscad://reference/{topic}", mime_type="application/json")
def reference_resource(topic: str) -> Dict[str, Any]:
    """Engineering reference data for one topic (see the reference tool)."""
    from .reference import lookup

    return lookup(topic, None, detailed=True)


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


def _run_sync(coro: Any) -> Any:
    """Run a coroutine to completion, even when called from inside a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _cli_check(argv: List[str]) -> int:
    """``openscad-mcp check <check_file> [--model f] [--fn N] [--json]`` -> exit code."""
    import argparse

    parser = argparse.ArgumentParser(prog="openscad-mcp check")
    parser.add_argument("check_file")
    parser.add_argument("--model", help=".scad file (overrides the check file's model:)")
    parser.add_argument("--fn", type=int, help="tessellation $fn for every part")
    parser.add_argument("--json", action="store_true", help="print the full JSON result")
    parser.add_argument("--allow", action="append", help="allowed path root (repeatable)")
    args = parser.parse_args(argv)
    if args.allow:
        cfg = get_config()
        cfg.security.allowed_paths = list(args.allow)
    result = _run_sync(_tool_fn(check)(
        scad_file=args.model, check_file=args.check_file, mode="rules", quality=args.fn,
    ))
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        if not result.get("success"):
            print(f"ERROR: {result.get('error')}")
            return 3
        for row in result.get("findings", []):
            subj = ",".join(str(x) for x in row.get("subject", []))
            mag = row.get("magnitude") or {}
            magtxt = " ".join(f"{k}={v}" for k, v in mag.items())
            note = row.get("note") or row.get("why") or ""
            print(f"{row.get('status'):10s} {row.get('rule'):12s} {subj:30s} {row.get('state', ''):13s} {magtxt} {note}".rstrip())
        summ = result.get("summary", {})
        print(f"pass {summ.get('pass', 0)}  fail {summ.get('fail', 0)}  unresolved {summ.get('unresolved', 0)}  "
              f"({result.get('timing_s')} s, cache hits {result.get('cache', {}).get('hits')})")
    return 3 if not result.get("success") else int(result.get("exit_code", 0))


def main():
    """Main entry point for the server."""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "check":
        sys.exit(_cli_check(sys.argv[2:]))

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