"""Static analysis for OpenSCAD projects.

Everything here is *lexical*. OpenSCAD source is masked (comments and string
literals blanked out, offsets preserved) and then walked with a brace-depth
aware statement scanner, so "file scope" means brace depth zero. No geometry
is evaluated and no subprocess is started.

Four capabilities:

``lint_use_shadowing``
    Warn when a ``use <X>`` edge crosses a file that sets ``$``-prefixed
    variables at file scope. BOSL2 does this for its whole attachment
    protocol, so modules imported with ``use`` silently lose the parent's
    ``$transform``/``$parent_*`` context and land at ``CENTER``.

``make_include_safe_copy`` / ``plan_use_to_include_rewrite`` / ``apply_rewrite``
    Shadow copies and autofix. A ``use`` edge can be turned into an
    ``include`` edge once we know nothing collides and nothing draws; when
    the used file *does* draw at top level we can include a private copy
    with those statements stripped instead.

``trace_symbol``
    Dependency trace over file-scope constants: what a constant is derived
    from, what is derived from it, and which files mention any of them.

``validate_expression``
    A grammar-based check that a user-supplied expression is an expression
    and not a smuggled statement, for inlining into generated wrappers.

Plus ``stable_color``/``assign_colors``: name-derived, order-independent,
collision-free part colours.
"""

from __future__ import annotations

import bisect
import hashlib
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .diagnostics import extract_source_dependencies
from .wrappers import absolutize_file_refs

__all__ = [
    "Finding",
    "ShadowCopy",
    "RewritePlan",
    "Statement",
    "FileScan",
    "mask_source",
    "scan_file",
    "scan_text",
    "clear_scan_cache",
    "resolve_reference",
    "include_closure",
    "default_library_paths",
    "lint_use_shadowing",
    "make_include_safe_copy",
    "variable_collisions",
    "plan_use_to_include_rewrite",
    "apply_rewrite",
    "remove_preview_guards",
    "trace_symbol",
    "validate_expression",
    "stable_color",
    "assign_colors",
    "FALLBACK_PALETTE",
]


# ---------------------------------------------------------------------------
# lexical masking
# ---------------------------------------------------------------------------


def mask_source(text: str) -> str:
    """Blank comments and string literals, keeping every offset and newline.

    The result has the same length as ``text`` and the same line breaks, so
    an index into the mask is an index into the original. Everything else in
    this module scans the mask, which is why a ``//`` in a string or a ``{``
    in a comment cannot derail the brace-depth bookkeeping.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append("".join(" " if c != "\n" else "\n" for c in text[i:j]))
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# statement scanning
# ---------------------------------------------------------------------------

IDENT_RE = re.compile(r"\$?[A-Za-z_][A-Za-z0-9_]*")

_INCLUDE_HEAD_RE = re.compile(r"^(include|use)\s*<([^>\n]*)>")
_MODULE_HEAD_RE = re.compile(r"^module\s+([A-Za-z_][A-Za-z0-9_]*)")
_FUNCTION_HEAD_RE = re.compile(r"^function\s+([A-Za-z_][A-Za-z0-9_]*)")
_ASSIGN_HEAD_RE = re.compile(r"^(\$?[A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)")
_CONTROL_HEAD_RE = re.compile(r"^(if|for|intersection_for)\b")
_ECHO_HEAD_RE = re.compile(r"^(echo|assert)\s*\(")

#: Statement kinds that never produce geometry when a file is ``include``d.
QUIET_KINDS = frozenset({"include", "use", "module", "function", "assign", "echo"})
#: Statement kinds that draw (or may draw) when a file is ``include``d.
DRAWING_KINDS = frozenset({"instantiation", "control"})


@dataclass
class Statement:
    """One file-scope statement, located in the source text."""

    kind: str
    start: int
    end: int
    line: int
    text: str
    name: str | None = None
    ref: str | None = None
    condition: str | None = None
    cond_end: int | None = None
    else_start: int | None = None

    @property
    def draws(self) -> bool:
        return self.kind in DRAWING_KINDS

    def preview(self, width: int = 60) -> str:
        flat = " ".join(self.text.split())
        return flat if len(flat) <= width else flat[: width - 3] + "..."


def _consume_statement(masked: str, i: int) -> int:
    """Index just past the statement starting at ``i`` in ``masked``."""
    n = len(masked)
    depth = 0
    while i < n:
        c = masked[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth <= 0 and c == "}":
                return i + 1
            if depth < 0:
                return i + 1
        elif c == ";" and depth == 0:
            return i + 1
        i += 1
    return n


def _skip_ws(masked: str, i: int) -> int:
    n = len(masked)
    while i < n and masked[i] in " \t\r\n":
        i += 1
    return i


def _matching_paren(masked: str, i: int) -> int:
    """Index just past the ``)`` matching the ``(`` at or after ``i``."""
    n = len(masked)
    while i < n and masked[i] != "(":
        i += 1
    depth = 0
    while i < n:
        if masked[i] == "(":
            depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for m in re.finditer("\n", text):
        starts.append(m.end())
    return starts


def _line_of(starts: Sequence[int], pos: int) -> int:
    return bisect.bisect_right(starts, pos)


def _scan_statements(text: str, masked: str) -> list[Statement]:
    """File-scope (brace depth 0) statements, in source order."""
    starts = _line_starts(text)
    stmts: list[Statement] = []
    i, n = 0, len(masked)
    while i < n:
        while i < n and masked[i] in " \t\r\n;":
            i += 1
        if i >= n:
            break
        if masked[i] in ")]}":  # stray closer, keep scanning
            i += 1
            continue
        start = i
        end = _consume_statement(masked, i)
        head = masked[start:end].lstrip()
        kind = "instantiation"
        name: str | None = None
        ref: str | None = None
        condition: str | None = None
        cond_end: int | None = None
        else_start: int | None = None

        m_inc = _INCLUDE_HEAD_RE.match(head)
        m_mod = _MODULE_HEAD_RE.match(head)
        m_fun = _FUNCTION_HEAD_RE.match(head)
        m_asg = _ASSIGN_HEAD_RE.match(head)
        m_ctl = _CONTROL_HEAD_RE.match(head)
        if m_inc:
            kind = m_inc.group(1)
            ref = m_inc.group(2).strip()
            # `include <a>` has no terminator; _consume_statement ran to the
            # next `;` or `}`. Cut it back to the closing `>`.
            end = start + (len(masked[start:end]) - len(head)) + m_inc.end()
            if end < n and masked[end] == ";":
                end += 1
        elif m_mod:
            kind, name = "module", m_mod.group(1)
        elif m_fun:
            kind, name = "function", m_fun.group(1)
        elif m_asg:
            kind, name = "assign", m_asg.group(1)
        elif m_ctl:
            kind = "control"
            cond_end = _matching_paren(masked, start)
            open_paren = masked.find("(", start)
            if 0 <= open_paren < cond_end:
                condition = text[open_paren + 1 : cond_end - 1]
            # Swallow `else` chains into this statement.
            while True:
                nxt = _skip_ws(masked, end)
                if masked.startswith("else", nxt) and not IDENT_RE.match(masked, nxt + 4):
                    if else_start is None:
                        else_start = nxt
                    end = _consume_statement(masked, nxt + 4)
                    continue
                break
        elif _ECHO_HEAD_RE.match(head) and "{" not in head:
            kind = "echo"

        stmts.append(
            Statement(
                kind=kind,
                start=start,
                end=end,
                line=_line_of(starts, start),
                text=text[start:end],
                name=name,
                ref=ref,
                condition=condition,
                cond_end=cond_end,
                else_start=else_start,
            )
        )
        i = max(end, start + 1)
    return stmts


# ---------------------------------------------------------------------------
# file scans
# ---------------------------------------------------------------------------


@dataclass
class FileScan:
    """Everything the lexical pass knows about one file."""

    path: Path | None
    text: str
    masked: str
    statements: list[Statement]
    includes: list[tuple[str, int]] = field(default_factory=list)
    uses: list[tuple[str, int]] = field(default_factory=list)
    modules: dict[str, int] = field(default_factory=dict)
    functions: dict[str, int] = field(default_factory=dict)
    assignments: dict[str, tuple[int, str]] = field(default_factory=dict)
    dollar_vars: dict[str, int] = field(default_factory=dict)
    data_refs: list[str] = field(default_factory=list)

    @property
    def drawing_statements(self) -> list[Statement]:
        return [s for s in self.statements if s.draws]


def scan_text(text: str, path: Path | None = None) -> FileScan:
    """Scan source text without touching the cache."""
    masked = mask_source(text)
    stmts = _scan_statements(text, masked)
    scan = FileScan(path=path, text=text, masked=masked, statements=stmts)
    for st in stmts:
        if st.kind == "include" and st.ref:
            scan.includes.append((st.ref, st.line))
        elif st.kind == "use" and st.ref:
            scan.uses.append((st.ref, st.line))
        elif st.kind == "module" and st.name:
            scan.modules.setdefault(st.name, st.line)
        elif st.kind == "function" and st.name:
            scan.functions.setdefault(st.name, st.line)
        elif st.kind == "assign" and st.name:
            rhs = _assignment_rhs(st)
            scan.assignments[st.name] = (st.line, rhs)
            if st.name.startswith("$"):
                scan.dollar_vars.setdefault(st.name, st.line)
    refs = {r for r, _ in scan.includes} | {r for r, _ in scan.uses}
    scan.data_refs = [r for r in extract_source_dependencies(text) if r not in refs]
    return scan


def _assignment_rhs(st: Statement) -> str:
    body = st.text
    eq = body.find("=")
    rhs = body[eq + 1 :] if eq >= 0 else ""
    return rhs.rstrip().rstrip(";").strip()


_SCAN_CACHE: dict[tuple[str, int, int], FileScan] = {}
_CLOSURE_CACHE: dict[tuple[str, int, int, tuple[str, ...]], list[Path]] = {}


def clear_scan_cache() -> None:
    """Drop the in-process file-scan and include-closure caches."""
    _SCAN_CACHE.clear()
    _CLOSURE_CACHE.clear()


def _stat_key(path: Path) -> tuple[str, int, int]:
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), 0, -1)


def scan_file(path: Path) -> FileScan:
    """Scan a file, memoised on (path, mtime, size).

    The cache is what makes the BOSL2 include closure affordable: the whole
    library is scanned once per process and reused for every ``use`` edge.
    """
    path = Path(path)
    key = _stat_key(path)
    cached = _SCAN_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        text = path.read_text(errors="replace")
    except OSError:
        text = ""
    scan = scan_text(text, path)
    _SCAN_CACHE[key] = scan
    return scan


# ---------------------------------------------------------------------------
# reference resolution and the include graph
# ---------------------------------------------------------------------------


def default_library_paths() -> list[Path]:
    """OpenSCAD library directories for this platform, ``OPENSCADPATH`` first."""
    paths: list[Path] = []
    env = os.environ.get("OPENSCADPATH")
    if env:
        paths.extend(Path(p) for p in env.split(os.pathsep) if p)
    home = Path.home()
    if sys.platform == "darwin":
        paths.append(home / "Documents" / "OpenSCAD" / "libraries")
        paths.append(home / "Library" / "Application Support" / "OpenSCAD" / "libraries")
    elif sys.platform.startswith("win"):
        paths.append(home / "Documents" / "OpenSCAD" / "libraries")
    paths.append(home / ".local" / "share" / "OpenSCAD" / "libraries")
    paths.append(Path("/usr/share/openscad/libraries"))
    paths.append(Path("/usr/share/openscad-nightly/libraries"))
    paths.append(Path("/usr/local/share/openscad/libraries"))
    return paths


def resolve_reference(
    ref: str,
    from_file: Path,
    include_paths: Sequence[Path] | None = None,
    library_paths: Sequence[Path] | None = None,
) -> Path | None:
    """Resolve ``include``/``use`` target ``ref`` the way OpenSCAD would.

    The including file's own directory wins, then explicit ``include_paths``,
    then the library directories.
    """
    ref = ref.strip()
    if not ref:
        return None
    candidate = Path(ref)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    roots: list[Path] = [Path(from_file).parent]
    roots.extend(Path(p) for p in (include_paths or ()))
    roots.extend(
        Path(p) for p in (default_library_paths() if library_paths is None else library_paths)
    )
    for root in roots:
        try:
            p = (root / ref).resolve()
        except OSError:
            continue
        if p.is_file():
            return p
    return None


def include_closure(
    path: Path,
    include_paths: Sequence[Path] | None = None,
    library_paths: Sequence[Path] | None = None,
) -> list[Path]:
    """Files whose file scope becomes ``path``'s file scope, ``path`` first.

    Only ``include`` edges are followed. ``use`` deliberately does not merge
    scopes, which is the whole subject of :func:`lint_use_shadowing`.
    """
    path = Path(path)
    libs = default_library_paths() if library_paths is None else list(library_paths)
    key = (
        *_stat_key(path),
        tuple(str(p) for p in list(include_paths or ())) + ("|",) + tuple(str(p) for p in libs),
    )
    cached = _CLOSURE_CACHE.get(key)
    if cached is not None:
        return cached
    seen: list[Path] = []
    seen_set: set[Path] = set()
    stack = [path]
    while stack:
        cur = stack.pop()
        if cur in seen_set:
            continue
        seen_set.add(cur)
        seen.append(cur)
        for ref, _line in scan_file(cur).includes:
            target = resolve_reference(ref, cur, include_paths, libs)
            if target is not None and target not in seen_set:
                stack.append(target)
    _CLOSURE_CACHE[key] = seen
    return seen


# ---------------------------------------------------------------------------
# capability 1: BOSL2 $-variable shadowing lint
# ---------------------------------------------------------------------------

Finding = dict[str, Any]

#: ``$``-variables OpenSCAD itself owns. A library assigning these at file
#: scope is not a shadowing hazard, it is just picking a default.
BUILTIN_DOLLAR_VARS = frozenset(
    {
        "$fn",
        "$fa",
        "$fs",
        "$t",
        "$vpr",
        "$vpt",
        "$vpd",
        "$vpf",
        "$preview",
        "$children",
        "$parent_modules",
    }
)

_ATTACHMENT_PREFIXES = (
    "$tag",
    "$attach",
    "$parent_",
    "$anchor",
    "$edge_",
    "$overlap",
    "$ghost",
    "$highlight",
    "$color",
    "$save_",
)

CATEGORY_LABELS = {
    "attachment_context": "attachment context",
    "gear_mating": "gear mating",
    "transform": "$transform",
    "library_state": "library state",
}

#: Constructs whose entire contract travels through ``$``-variables. A module
#: from a ``use``d file appearing as their child is the failure mode.
CONTEXT_CALLS = (
    "attach",
    "position",
    "align",
    "tag",
    "tag_scope",
    "force_tag",
    "diff",
    "intersect",
    "conv_hull",
    "hide",
    "show_only",
    "recolor",
    "color_this",
    "ghost",
    "highlight",
    "orient",
)

#: Constructs that alone are enough to escalate a finding to ERROR.
ESCALATING_CALLS = ("attach", "position", "align", "tag")


def _categorize_dollar_var(name: str) -> str:
    if name == "$transform":
        return "transform"
    if name.startswith("$parent_gear"):
        return "gear_mating"
    if name.startswith(_ATTACHMENT_PREFIXES):
        return "attachment_context"
    return "library_state"


def _describe_categories(categories: Mapping[str, int]) -> str:
    order = ["attachment_context", "gear_mating", "transform", "library_state"]
    parts: list[str] = []
    for key in order:
        count = categories.get(key)
        if not count:
            continue
        label = CATEGORY_LABELS[key]
        parts.append(label if label.startswith("$") and count == 1 else f"{label} ({count})")
    return ", ".join(parts)


def _child_statement_span(masked: str, call_end: int) -> tuple[int, int]:
    """Span of the statement that follows a construct's argument list."""
    start = _skip_ws(masked, call_end)
    return start, _consume_statement(masked, start)


def _context_call_sites(scan: FileScan, module_names: set[str]) -> list[dict[str, Any]]:
    """Call sites where a module from the used file is a context-call child."""
    if not module_names:
        return []
    sites: list[dict[str, Any]] = []
    starts = _line_starts(scan.text)
    masked = scan.masked
    for call in CONTEXT_CALLS:
        for m in re.finditer(rf"\b{call}\s*\(", masked):
            paren_end = _matching_paren(masked, m.end() - 1)
            body_start, body_end = _child_statement_span(masked, paren_end)
            body = masked[body_start:body_end]
            for mod in sorted(module_names):
                if re.search(rf"\b{re.escape(mod)}\s*\(", body):
                    sites.append(
                        {
                            "construct": call,
                            "module": mod,
                            "line": _line_of(starts, m.start()),
                            "escalates": call in ESCALATING_CALLS,
                        }
                    )
                    break
    sites.sort(key=lambda s: (s["line"], s["construct"]))
    return sites


def _shadowed_dollar_vars(
    used_path: Path,
    include_paths: Sequence[Path] | None,
    library_paths: Sequence[Path] | None,
) -> tuple[dict[str, str], set[str], set[str]]:
    """``$``-vars set at file scope anywhere in ``used_path``'s include closure."""
    shadowed: dict[str, str] = {}
    providers: set[str] = set()
    modules: set[str] = set()
    for f in include_closure(used_path, include_paths, library_paths):
        fscan = scan_file(f)
        modules |= set(fscan.modules)
        for var, line in fscan.dollar_vars.items():
            if var in BUILTIN_DOLLAR_VARS:
                continue
            if var not in shadowed:
                shadowed[var] = f"{f.name}:{line}"
                providers.add(f.name)
    return shadowed, providers, modules


def _fix_hint(caller_scan: FileScan, used_path: Path, ref: str) -> dict[str, Any]:
    """Cheap, read-only preview of what :func:`plan_use_to_include_rewrite` would do."""
    used_scan = scan_file(used_path)
    collisions = sorted(set(caller_scan.assignments) & set(used_scan.assignments))
    drawing = used_scan.drawing_statements
    settable = _settable_guard_vars(used_scan, drawing)
    needs_shadow = bool(drawing) and settable is None
    reasons: list[str] = []
    if collisions:
        reasons.append(
            f"{len(collisions)} file-scope name(s) are assigned in both files "
            f"({', '.join(collisions[:4])}); include would merge them"
        )
    if drawing:
        if settable is None:
            reasons.append(
                f"{len(drawing)} top-level statement(s) in {used_path.name} draw geometry; "
                "include a stripped private copy instead"
            )
        else:
            reasons.append(
                f"top-level geometry in {used_path.name} is guarded by "
                f"{', '.join(sorted(settable))}, which the caller can set"
            )
    return {
        "action": "use_to_include",
        "replacement": f"include <{ref}>",
        "safe": not collisions,
        "needs_shadow_copy": needs_shadow,
        "collisions": collisions,
        "reasons": reasons,
    }


def lint_use_shadowing(
    root_file: str | Path,
    include_paths: Sequence[Path] | None = None,
    library_paths: Sequence[Path] | None = None,
    recursive: bool = True,
) -> list[Finding]:
    """Flag ``use <X>`` edges that cross a file-scope ``$``-variable boundary.

    ``use`` imports modules but not file scope. When ``X``'s include closure
    assigns ``$``-variables at file scope (BOSL2 assigns 40 of them, covering
    the whole attachment and gear-mating protocol), a module from ``X`` is
    evaluated with *those* values, not the caller's. As the child of
    ``attach()``/``position()``/``align()`` it therefore reads a reset
    ``$transform``/``$parent_geom`` and is silently placed at ``CENTER``.

    The include-graph walk is the primary signal. Call sites only escalate:
    on a real project that had already been burned by this, matching
    ``attach()`` textually found one false positive and no true positives,
    because the workaround people reach for is to stop calling ``attach()``.

    Returns one finding per ``(caller, used file)`` pair. With ``recursive``
    the caller's whole project-local include/use graph is linted, so an
    assembly file reports the hazards of the parts it pulls in too; library
    files are never treated as callers.
    """
    root = Path(root_file).resolve()
    libs = default_library_paths() if library_paths is None else list(library_paths)
    lib_roots = [p.resolve() for p in libs if p.exists()]

    def is_library(path: Path) -> bool:
        return any(_is_within(path, root_dir) for root_dir in lib_roots)

    findings: list[Finding] = []
    seen: set[Path] = set()
    queue: list[Path] = [root]
    while queue:
        caller = queue.pop(0)
        if caller in seen or not caller.is_file():
            continue
        seen.add(caller)
        caller_scan = scan_file(caller)
        for ref, line in caller_scan.uses:
            target = resolve_reference(ref, caller, include_paths, libs)
            if target is None:
                continue
            if recursive and not is_library(target):
                queue.append(target)
            shadowed, providers, modules = _shadowed_dollar_vars(target, include_paths, libs)
            if not shadowed:
                continue
            categories: dict[str, int] = {}
            for var in shadowed:
                cat = _categorize_dollar_var(var)
                categories[cat] = categories.get(cat, 0) + 1
            own_modules = set(scan_file(target).modules)
            sites = _context_call_sites(caller_scan, own_modules)
            escalate = [s for s in sites if s["escalates"]]
            severity = "ERROR" if escalate else "WARNING"
            summary = _describe_categories(categories)
            message = (
                f"use <{ref}> crosses a $-variable boundary: {len(shadowed)} file-scope "
                f"$-variables ({summary}) are reset for modules imported from "
                f"{target.name}, so attach()/position()/align()/tag() children from that "
                "file are placed at CENTER instead of the parent anchor"
            )
            if escalate:
                first = escalate[0]
                message += (
                    f"; {caller.name}:{first['line']} calls {first['construct']}() with "
                    f"{first['module']}() as its child"
                )
            findings.append(
                {
                    "code": "bosl2_use_shadowing",
                    "severity": severity,
                    "file": str(caller),
                    "line": line,
                    "used_file": str(target),
                    "categories": categories,
                    "message": message,
                    "fix": _fix_hint(caller_scan, target, ref),
                    "shadowed_count": len(shadowed),
                    "providers": sorted(providers)[:3],
                    "call_sites": sites[:6],
                }
            )
    findings.sort(key=lambda f: (f["file"], f["line"]))
    return findings


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# capability 2: shadow copies and autofix
# ---------------------------------------------------------------------------


@dataclass
class ShadowCopy:
    """A private copy of a file with its top-level instantiations removed."""

    source: Path
    path: Path
    text: str
    removed: list[dict[str, Any]] = field(default_factory=list)
    kept_modules: list[str] = field(default_factory=list)
    kept_assignments: list[str] = field(default_factory=list)
    rewritten_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "path": str(self.path),
            "removed": self.removed,
            "kept_modules": self.kept_modules,
            "kept_assignments": self.kept_assignments,
            "rewritten_refs": self.rewritten_refs,
        }


def _blank(segment: str) -> str:
    return "".join(c if c == "\n" else " " for c in segment)


def _relative_ref_edits(
    scan: FileScan, source: Path
) -> tuple[list[tuple[int, int, str]], list[str]]:
    """Absolutise refs that resolve next to ``source`` so a copy elsewhere still works."""
    edits: list[tuple[int, int, str]] = []
    rewritten: list[str] = []
    source_dir = source.parent.resolve()
    for st in scan.statements:
        if st.kind not in ("include", "use") or not st.ref:
            continue
        if Path(st.ref).is_absolute():
            continue
        try:
            local = (source_dir / st.ref).resolve()
        except OSError:
            continue
        if not local.is_file():
            # Not found next to the original, so it came off the library
            # path, which is the same wherever the copy lives. Leave it.
            continue
        target = local
        replacement = f"{st.kind} <{target}>"
        if st.text.rstrip().endswith(";"):
            replacement += ";"
        edits.append((st.start, st.end, replacement))
        rewritten.append(f"{st.ref} -> {target}")
    return edits, rewritten


def make_include_safe_copy(path: str | Path, dest_dir: str | Path) -> ShadowCopy:
    """Write a copy of ``path`` that draws nothing when ``include``d.

    Module and function definitions, assignments, ``include``/``use`` lines
    and ``echo``/``assert`` survive. Top-level instantiations and top-level
    ``if``/``for`` blocks (the ``if (make_stl || $preview) part();`` idiom
    included) are blanked out, character for character, so every surviving
    line keeps its original number and diagnostics still line up.

    Relative ``include``/``use`` refs that resolved next to the original, and
    relative ``import()``/``surface()`` paths, are rewritten to absolute
    paths, because the copy lives in another directory.
    """
    source = Path(path).resolve()
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    scan = scan_file(source)

    edits: list[tuple[int, int, str]] = []
    removed: list[dict[str, Any]] = []
    for st in scan.statements:
        if not st.draws:
            continue
        edits.append((st.start, st.end, _blank(st.text)))
        removed.append(
            {
                "line": st.line,
                "kind": st.kind,
                "text": st.preview(),
            }
        )
    ref_edits, rewritten = _relative_ref_edits(scan, source)
    edits.extend(ref_edits)

    text = scan.text
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    text = absolutize_file_refs(text, source.parent)
    text = text.rstrip("\n") + (
        f"\n\n// include-safe copy of {source}\n"
        f"// {len(removed)} top-level instantiation(s) removed by openscad-mcp\n"
    )

    out = dest / f"{source.stem}__include_safe.scad"
    out.write_text(text)
    return ShadowCopy(
        source=source,
        path=out,
        text=text,
        removed=removed,
        kept_modules=sorted(scan.modules),
        kept_assignments=sorted(scan.assignments),
        rewritten_refs=rewritten,
    )


def variable_collisions(caller_path: str | Path, used_path: str | Path) -> list[str]:
    """File-scope assignment names present in both files.

    ``include`` merges the two file scopes into one, and the last assignment
    wins, so a shared name silently changes which value the library module
    reads. This is the check that decides whether a ``use``-to-``include``
    rewrite is safe.
    """
    caller = scan_file(Path(caller_path))
    used = scan_file(Path(used_path))
    return sorted(set(caller.assignments) & set(used.assignments))


def _settable_guard_vars(scan: FileScan, drawing: Sequence[Statement]) -> set[str] | None:
    """Variables that switch off *all* top-level geometry, or ``None``.

    Returns a set of file-scope variable names when every drawing statement
    is a top-level ``if`` whose condition only mentions variables the caller
    can assign. ``$preview`` does not count: preview is exactly the mode a
    PNG render runs in, so a ``$preview`` guard still draws.
    """
    if not drawing:
        return set()
    names: set[str] = set()
    for st in drawing:
        if st.kind != "control" or st.condition is None:
            return None
        idents = {m.group(0) for m in IDENT_RE.finditer(mask_source(st.condition))}
        if not idents or any(i.startswith("$") for i in idents):
            return None
        if not idents <= set(scan.assignments):
            return None
        names |= idents
    return names


@dataclass
class RewritePlan:
    """A proposed ``use`` to ``include`` rewrite of one caller file."""

    caller_path: Path
    used_path: Path
    original_text: str
    new_text: str
    safe: bool
    reasons: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)
    include_target: str = ""
    shadow: ShadowCopy | None = None
    set_variables: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.new_text != self.original_text

    def to_dict(self) -> dict[str, Any]:
        return {
            "caller_path": str(self.caller_path),
            "used_path": str(self.used_path),
            "safe": self.safe,
            "changed": self.changed,
            "reasons": self.reasons,
            "collisions": self.collisions,
            "include_target": self.include_target,
            "shadow_copy": str(self.shadow.path) if self.shadow else None,
            "set_variables": self.set_variables,
        }


def plan_use_to_include_rewrite(
    caller_path: str | Path,
    used_path: str | Path,
    shadow_dir: str | Path | None = None,
    include_paths: Sequence[Path] | None = None,
    library_paths: Sequence[Path] | None = None,
) -> RewritePlan:
    """Plan replacing ``use <used>`` with ``include <used>`` in ``caller``.

    The rewrite is safe when the two files share no file-scope assignment
    names *and* the used file draws nothing at top level. When it does draw
    and ``shadow_dir`` is given, an include-safe private copy is written and
    the plan includes that copy instead. When the geometry is merely guarded
    by an ordinary variable the caller can set, the plan records the
    assignment to add rather than copying anything.

    The returned plan is never applied on its own, and
    :func:`apply_rewrite` refuses an unsafe one.
    """
    caller = Path(caller_path).resolve()
    used = Path(used_path).resolve()
    caller_scan = scan_file(caller)
    used_scan = scan_file(used)
    original = caller_scan.text

    reasons: list[str] = []
    collisions = sorted(set(caller_scan.assignments) & set(used_scan.assignments))
    safe = True

    targets: list[Statement] = []
    for st in caller_scan.statements:
        if st.kind != "use" or not st.ref:
            continue
        resolved = resolve_reference(st.ref, caller, include_paths, library_paths)
        if resolved is not None and resolved.resolve() == used:
            targets.append(st)
    if not targets:
        return RewritePlan(
            caller_path=caller,
            used_path=used,
            original_text=original,
            new_text=original,
            safe=False,
            reasons=[f"{caller.name} has no `use <>` statement resolving to {used.name}"],
            collisions=collisions,
        )

    if collisions:
        safe = False
        reasons.append(
            "include merges file scopes and the last assignment wins; both files assign "
            + ", ".join(collisions[:6])
            + (" ..." if len(collisions) > 6 else "")
        )

    shadow: ShadowCopy | None = None
    set_variables: dict[str, Any] = {}
    drawing = used_scan.drawing_statements
    settable = _settable_guard_vars(used_scan, drawing)
    include_ref = targets[0].ref or used.name
    if drawing:
        if settable:
            set_variables = dict.fromkeys(sorted(settable), False)
            reasons.append(
                f"top-level geometry in {used.name} is guarded by "
                + ", ".join(sorted(settable))
                + "; set it false in the caller"
            )
        elif shadow_dir is not None:
            shadow = make_include_safe_copy(used, shadow_dir)
            include_ref = str(shadow.path)
            reasons.append(
                f"{used.name} has {len(drawing)} top-level instantiation(s); including an "
                f"include-safe private copy at {shadow.path}"
            )
        else:
            safe = False
            reasons.append(
                f"{used.name} has {len(drawing)} top-level instantiation(s) that would draw "
                f"(first at line {drawing[0].line}); pass shadow_dir to include a stripped copy"
            )

    if not safe:
        return RewritePlan(
            caller_path=caller,
            used_path=used,
            original_text=original,
            new_text=original,
            safe=False,
            reasons=reasons,
            collisions=collisions,
            include_target=include_ref,
            shadow=shadow,
        )

    new_text = original
    for st in sorted(targets, key=lambda s: s.start, reverse=True):
        replacement = f"include <{include_ref}>"
        if st.text.rstrip().endswith(";"):
            replacement += ";"
        new_text = new_text[: st.start] + replacement + new_text[st.end :]
    if set_variables:
        assigns = "".join(f"{name} = false;\n" for name in set_variables)
        insert_at = targets[-1].end
        line_end = new_text.find("\n", insert_at)
        line_end = len(new_text) if line_end < 0 else line_end + 1
        new_text = new_text[:line_end] + assigns + new_text[line_end:]
    reasons.insert(0, f"replaced use <{targets[0].ref}> with include <{include_ref}>")
    return RewritePlan(
        caller_path=caller,
        used_path=used,
        original_text=original,
        new_text=new_text,
        safe=True,
        reasons=reasons,
        collisions=collisions,
        include_target=include_ref,
        shadow=shadow,
        set_variables=set_variables,
    )


def apply_rewrite(plan: RewritePlan) -> Path:
    """Write a safe plan to its caller file. Raises on an unsafe plan."""
    if not plan.safe:
        raise ValueError(
            "refusing to apply an unsafe rewrite of "
            f"{plan.caller_path}: " + "; ".join(plan.reasons)
        )
    plan.caller_path.write_text(plan.new_text)
    return plan.caller_path


def remove_preview_guards(text: str, guard_names: Sequence[str] = ("$preview",)) -> str:
    """Make ``if (make_stl || $preview) { part(); }`` unconditional.

    Files written for a Makefile draw their part only under a render-control
    guard, so an export-time render of such a file is empty. A private copy
    with the guards removed lets a section or a measurement see the geometry.
    Only top-level ``if`` statements whose condition mentions one of
    ``guard_names`` are touched, any ``else`` branch is dropped, and the
    blanking preserves line numbers.
    """
    scan = scan_text(text)
    wanted = set(guard_names)
    edits: list[tuple[int, int, str]] = []
    for st in scan.statements:
        if st.kind != "control" or st.condition is None or st.cond_end is None:
            continue
        if not st.text.lstrip().startswith("if"):
            continue
        idents = {m.group(0) for m in IDENT_RE.finditer(mask_source(st.condition))}
        if not idents & wanted:
            continue
        edits.append((st.start, st.cond_end, _blank(text[st.start : st.cond_end])))
        if st.else_start is not None:
            edits.append((st.else_start, st.end, _blank(text[st.else_start : st.end])))
    out = text
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + replacement + out[end:]
    return out


# ---------------------------------------------------------------------------
# capability 3: dependency trace over file-scope constants
# ---------------------------------------------------------------------------

TRACE_NOTE = (
    "lexical, file-scope constants only: assignments at brace depth 0 with comments and "
    "strings masked. Module-local variables, parameters and conditional redefinition are "
    "not modelled."
)


def _constant_definitions(
    files: Sequence[Path],
) -> tuple[dict[str, dict[str, Any]], list[FileScan]]:
    defs: dict[str, dict[str, Any]] = {}
    scans: list[FileScan] = []
    for path in files:
        scan = scan_file(path)
        scans.append(scan)
        for st in scan.statements:
            if st.kind != "assign" or not st.name:
                continue
            expression = " ".join(_assignment_rhs(st).split())
            entry = defs.get(st.name)
            if entry is not None:
                entry.setdefault("redefined_in", []).append(f"{path.name}:{st.line}")
            defs[st.name] = {
                "name": st.name,
                "file": str(path),
                "line": st.line,
                "expression": expression,
                "raw_deps": sorted(
                    {m.group(0) for m in IDENT_RE.finditer(mask_source(expression))}
                ),
                "redefined_in": (entry or {}).get("redefined_in", []),
            }
    for entry in defs.values():
        entry["deps"] = [d for d in entry["raw_deps"] if d in defs and d != entry["name"]]
        del entry["raw_deps"]
    return defs, scans


def _closure(defs: Mapping[str, dict[str, Any]], root: str, reverse: bool) -> dict[str, int]:
    """BFS depths from ``root`` over the constant graph."""
    edges: dict[str, set[str]] = {}
    for name, entry in defs.items():
        for dep in entry["deps"]:
            if reverse:
                edges.setdefault(dep, set()).add(name)
            else:
                edges.setdefault(name, set()).add(dep)
    depth = {root: 0}
    frontier = {root}
    while frontier:
        nxt: set[str] = set()
        for node in frontier:
            for other in edges.get(node, ()):
                if other not in depth:
                    depth[other] = depth[node] + 1
                    nxt.add(other)
        frontier = nxt
    depth.pop(root, None)
    return depth


def _reference_sites(scans: Sequence[FileScan], names: set[str]) -> list[dict[str, Any]]:
    """Files whose bodies mention any of ``names``, excluding definition sites."""
    hits: list[dict[str, Any]] = []
    for scan in scans:
        if scan.path is None:
            continue
        lhs_spans = [
            (st.start, st.start + len(st.text) - len(st.text.lstrip()) + len(st.name or ""))
            for st in scan.statements
            if st.kind == "assign" and st.name
        ]
        starts = _line_starts(scan.text)
        found: dict[str, list[int]] = {}
        for m in IDENT_RE.finditer(scan.masked):
            token = m.group(0)
            if token not in names:
                continue
            if any(a <= m.start() < b for a, b in lhs_spans):
                continue
            found.setdefault(token, []).append(_line_of(starts, m.start()))
        if found:
            hits.append(
                {
                    "file": str(scan.path),
                    "names": sorted(found),
                    "lines": sorted({ln for lines in found.values() for ln in lines})[:20],
                    "defines": bool(set(scan.assignments) & names),
                }
            )
    hits.sort(key=lambda h: h["file"])
    return hits


def trace_symbol(
    symbol: str,
    files: Iterable[str | Path],
    direction: str = "downstream",
) -> dict[str, Any]:
    """Trace a file-scope constant through a set of ``.scad`` files.

    ``downstream`` lists the constants derived from ``symbol``, ``upstream``
    the constants it is derived from, each with a BFS depth. ``files`` lists
    the files whose bodies reference the traced set, which is the practical
    answer to "what breaks if I change this number".
    """
    if direction not in ("downstream", "upstream"):
        raise ValueError(f"direction must be 'downstream' or 'upstream', got {direction!r}")
    paths = [Path(f) for f in files]
    defs, scans = _constant_definitions(paths)

    definition = None
    if symbol in defs:
        entry = defs[symbol]
        definition = {
            "file": entry["file"],
            "line": entry["line"],
            "expression": entry["expression"],
        }

    def rows(depths: Mapping[str, int]) -> list[dict[str, Any]]:
        out = []
        for name, depth in depths.items():
            entry = defs[name]
            out.append(
                {
                    "name": name,
                    "depth": depth,
                    "expression": entry["expression"],
                    "file": entry["file"],
                    "line": entry["line"],
                }
            )
        out.sort(key=lambda r: (r["depth"], r["name"]))
        return out

    down = rows(_closure(defs, symbol, reverse=True)) if symbol in defs else []
    up = rows(_closure(defs, symbol, reverse=False)) if symbol in defs else []
    selected = down if direction == "downstream" else up
    names = {symbol} | {r["name"] for r in selected}
    return {
        "symbol": symbol,
        "direction": direction,
        "definition": definition,
        "downstream": down,
        "upstream": up,
        "files": _reference_sites(scans, names),
        "constants_parsed": len(defs),
        "note": TRACE_NOTE,
    }


# ---------------------------------------------------------------------------
# capability 4: expression validator
# ---------------------------------------------------------------------------

_EXPR_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<number>(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)
    | (?P<ident>\$?[A-Za-z_][A-Za-z0-9_]*)
    | (?P<string>"(?:[^"\\\n]|\\.)*")
    | (?P<op><=|>=|==|!=|&&|\|\||[-+*/%^()\[\],:?<>=])
    """,
    re.VERBOSE,
)

#: Words that would turn an "expression" into a statement or a file read.
BANNED_WORDS = frozenset({"include", "use", "import", "surface", "echo", "assert"})
_BANNED_CHAR_HINT = {
    ";": "statement separators",
    "{": "blocks",
    "}": "blocks",
    "#": "modifier characters",
    "!": "modifier characters",
    "`": "backticks",
    "\\": "backslashes",
    "@": "'@'",
}


@dataclass
class _Token:
    kind: str
    value: str
    pos: int


def _tokenize_expression(expr: str) -> list[_Token]:
    tokens: list[_Token] = []
    i, n = 0, len(expr)
    while i < n:
        m = _EXPR_TOKEN_RE.match(expr, i)
        if m is None:
            ch = expr[i]
            hint = _BANNED_CHAR_HINT.get(ch)
            what = f"{hint} are not allowed" if hint else f"unexpected character {ch!r}"
            raise _expr_error(expr, i, what)
        i = m.end()
        kind = m.lastgroup or "op"
        if kind == "ws":
            continue
        value = m.group()
        if kind == "ident" and value in BANNED_WORDS:
            raise _expr_error(expr, m.start(), f"{value!r} is not allowed in an expression")
        tokens.append(_Token(kind, value, m.start()))
    return tokens


def _expr_error(expr: str, pos: int, message: str) -> ValueError:
    caret = " " * pos + "^"
    return ValueError(f"invalid expression at position {pos}: {message}\n  {expr}\n  {caret}")


class _ExprParser:
    """Recursive-descent parser for the OpenSCAD expression grammar."""

    def __init__(self, expr: str, tokens: list[_Token]):
        self.expr = expr
        self.tokens = tokens
        self.i = 0

    def peek(self) -> _Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def at(self, *values: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "op" and tok.value in values

    def take(self) -> _Token:
        tok = self.peek()
        if tok is None:
            raise _expr_error(self.expr, len(self.expr), "expression ends early")
        self.i += 1
        return tok

    def expect(self, value: str) -> _Token:
        tok = self.peek()
        if tok is None or tok.value != value:
            pos = tok.pos if tok else len(self.expr)
            got = repr(tok.value) if tok else "end of input"
            raise _expr_error(self.expr, pos, f"expected {value!r} but found {got}")
        return self.take()

    # grammar ---------------------------------------------------------------

    def parse(self) -> None:
        if not self.tokens:
            raise ValueError("empty expression")
        self.ternary()
        tok = self.peek()
        if tok is not None:
            raise _expr_error(self.expr, tok.pos, f"unexpected trailing {tok.value!r}")

    def ternary(self) -> None:
        self.binary(0)
        if self.at("?"):
            self.take()
            self.ternary()
            self.expect(":")
            self.ternary()

    _LEVELS: tuple[tuple[str, ...], ...] = (
        ("||",),
        ("&&",),
        ("==", "!="),
        ("<", ">", "<=", ">="),
        ("+", "-"),
        ("*", "/", "%"),
    )

    def binary(self, level: int) -> None:
        if level >= len(self._LEVELS):
            self.unary()
            return
        self.binary(level + 1)
        while self.at(*self._LEVELS[level]):
            self.take()
            self.binary(level + 1)

    def unary(self) -> None:
        while self.at("+", "-"):
            self.take()
        self.power()

    def power(self) -> None:
        self.postfix()
        if self.at("^"):
            self.take()
            self.unary()

    def postfix(self) -> None:
        was_ident = self.primary()
        while True:
            if self.at("["):
                self.take()
                self.ternary()
                self.expect("]")
                was_ident = False
            elif was_ident and self.at("("):
                self.take()
                if not self.at(")"):
                    self.arguments()
                self.expect(")")
                was_ident = False
            else:
                return

    def arguments(self) -> None:
        while True:
            tok = self.peek()
            nxt = self.tokens[self.i + 1] if self.i + 1 < len(self.tokens) else None
            if tok is not None and tok.kind == "ident" and nxt is not None and nxt.value == "=":
                self.take()
                self.take()
            self.ternary()
            if self.at(","):
                self.take()
                continue
            return

    def primary(self) -> bool:
        tok = self.peek()
        if tok is None:
            raise _expr_error(self.expr, len(self.expr), "expression ends early")
        if tok.kind in ("number", "string"):
            self.take()
            return False
        if tok.kind == "ident":
            self.take()
            return True
        if tok.value == "(":
            self.take()
            self.ternary()
            self.expect(")")
            return False
        if tok.value == "[":
            self.take()
            self.list_or_range()
            return False
        raise _expr_error(self.expr, tok.pos, f"{tok.value!r} cannot start an expression")

    def list_or_range(self) -> None:
        if self.at("]"):
            self.take()
            return
        self.ternary()
        if self.at(":"):
            while self.at(":"):
                self.take()
                self.ternary()
            self.expect("]")
            return
        while self.at(","):
            self.take()
            if self.at("]"):
                break
            self.ternary()
        self.expect("]")


def validate_expression(expr: str) -> str:
    """Validate an OpenSCAD expression and return it normalised to one line.

    Accepts identifiers including ``$fn``, numbers, strings, ``+ - * / % ^``,
    comparisons, boolean operators, ``? :``, parentheses, vectors, ranges,
    indexing and function calls with named arguments. Rejects anything that
    would end the expression and start a statement, so a caller can inline
    the result into a generated transform. Raises ``ValueError`` pointing at
    the offending character.
    """
    if not isinstance(expr, str):
        raise ValueError(f"expression must be a string, got {type(expr).__name__}")
    flat = " ".join(expr.split())
    if not flat:
        raise ValueError("empty expression")
    parser = _ExprParser(flat, _tokenize_expression(flat))
    parser.parse()
    return flat


# ---------------------------------------------------------------------------
# capability 5: stable part colours
# ---------------------------------------------------------------------------

#: Used when :mod:`openscad_mcp.camera` is unavailable. Okabe-Ito plus two.
FALLBACK_PALETTE: list[str] = [
    "#E69F00",
    "#56B4E9",
    "#009E73",
    "#F0E442",
    "#0072B2",
    "#D55E00",
    "#CC79A7",
    "#999999",
    "#882255",
    "#332288",
]


def _resolve_palette(palette: Sequence[str] | None = None) -> list[str]:
    if palette:
        return list(palette)
    try:
        from .camera import PALETTE
    except Exception:  # pragma: no cover - camera is normally importable
        return list(FALLBACK_PALETTE)
    return list(PALETTE) or list(FALLBACK_PALETTE)


def _name_index(name: str, modulus: int) -> int:
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % modulus


def stable_color(
    name: str,
    palette: Sequence[str] | None = None,
    taken: Iterable[str] | None = None,
) -> str:
    """A palette colour derived from ``name``, avoiding colours in ``taken``.

    The index comes from a BLAKE2b digest, not Python's salted ``hash``, so
    the same part gets the same colour in every process and every run. When
    the preferred colour is taken the search probes forward through the
    palette; once every colour is taken it wraps and reuses the preferred one.
    """
    pal = _resolve_palette(palette)
    if not pal:
        raise ValueError("palette is empty")
    used = set(taken or ())
    start = _name_index(name, len(pal))
    for step in range(len(pal)):
        candidate = pal[(start + step) % len(pal)]
        if candidate not in used:
            return candidate
    return pal[start]


def assign_colors(
    names: Iterable[str],
    palette: Sequence[str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Assign each name a distinct colour, independent of input order.

    Names are sorted before probing, so the same set of parts produces the
    same mapping however the caller listed them. Explicit ``overrides`` are
    honoured and their colours are reserved first.
    """
    pal = _resolve_palette(palette)
    result: dict[str, str] = {}
    used: set[str] = set()
    override_map = dict(overrides or {})
    unique = sorted(set(names))
    for name in unique:
        if name in override_map:
            result[name] = override_map[name]
            used.add(override_map[name])
    for name in unique:
        if name in result:
            continue
        colour = stable_color(name, pal, used)
        result[name] = colour
        used.add(colour)
    return result
