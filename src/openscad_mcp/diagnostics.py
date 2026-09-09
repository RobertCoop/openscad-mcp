"""
Structured interpretation of OpenSCAD process output.

OpenSCAD's exit code is not a reliable success signal. On 2021.01 a failed
``assert()``, a call to an unknown module, a non-closed polyhedron and a
missing include all exit 0 while printing their diagnostic to stderr, so
every tool path has to read stderr rather than trust the return code. This
module turns that stream into structured records, extracts the CGAL
statistics banner into a mesh-health summary, attaches repair hints keyed to
message strings OpenSCAD actually emits, and parses the Makefile-style
dependency file written by ``-d``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# One tolerant pattern covers every line class OpenSCAD 2021.01 emits. There
# is no column number in 2021.01, and both "msg in file X, line N" and
# "msg, in file X, line N" occur.
_DIAG_RE = re.compile(
    r"^(?P<sev>WARNING|ERROR|TRACE|DEPRECATED|ECHO|EXPORT-WARNING):\s*"
    r"(?P<msg>.*?)"
    r"(?:,?\s+in file (?P<file>.+?), line (?P<line>\d+))?\s*$"
)

_TRACE_RE = re.compile(r"^TRACE:\s*(?P<msg>.*)$")

# CGAL statistics banner printed on every mesh export (suppressed by -q).
_STAT_RE = re.compile(
    r"^\s*(Simple|Vertices|Halfedges|Edges|Halffacets|Facets|Volumes|Contours):\s+(\S+)\s*$"
)

# Lines OpenSCAD prints outside the severity scheme that still matter.
_EMPTY_OBJECT = "Current top level object is empty"
_CANT_PARSE_RE = re.compile(r"^Can't parse file '(?P<file>.+?)'!?$")

# Bound the echo channel: it is both the return leg of include-as-data reads
# and an injection surface, so it never goes back verbatim and unbounded.
ECHO_MAX_LINES = 200
ECHO_MAX_CHARS = 2000

SEVERITY_ERRORS = {"ERROR"}
SEVERITY_WARNINGS = {"WARNING", "EXPORT-WARNING"}

# Repair hints keyed to substrings that appear in real 2021.01 output. The
# text is written for the model that reads it, so it says what to do next.
_HINTS: List[tuple[str, str, str]] = [
    (
        "unknown_symbol",
        "Ignoring unknown",
        "A module, function or variable is used before it is defined or is misspelled. "
        "Check the name, and if it comes from a library make sure the matching "
        "include <...> or use <...> line is present and the library is installed.",
    ),
    (
        "missing_include",
        "Can't open include file",
        "An include <...> or use <...> target could not be found. Check the path and "
        "OPENSCADPATH / include_paths; the render continued without it, so the picture "
        "is missing that file's contents.",
    ),
    (
        "missing_include",
        "Can't open library",
        "A use <...> target could not be found. Check the path and OPENSCADPATH / "
        "include_paths.",
    ),
    (
        "syntax_error",
        "Parser error",
        "The file did not parse. Nothing was evaluated, so no geometry or echo output exists.",
    ),
    (
        "assertion_failed",
        "Assertion",
        "An assert() failed. Evaluation stopped at that point; any output produced is "
        "incomplete and must not be treated as the finished model.",
    ),
    (
        "non_manifold",
        "not closed",
        "A polyhedron is not a closed surface. OpenSCAD drops it from mesh output, so "
        "exported geometry is missing that part. Check face winding and that every edge "
        "is shared by exactly two faces.",
    ),
    (
        "non_manifold",
        "2-manifold",
        "The result is not a valid 2-manifold (parts touch along an edge or a face, or "
        "a surface self-intersects). Overlap coplanar or edge-sharing parts by a small "
        "epsilon instead of letting them just touch.",
    ),
    (
        "mixed_2d_3d",
        "Mixing 2D and 3D",
        "2D and 3D objects were combined in one operation. Extrude 2D shapes with "
        "linear_extrude() or rotate_extrude() before combining them with solids.",
    ),
    (
        "empty_output",
        _EMPTY_OBJECT,
        "The evaluated model contains no geometry. A difference() may have removed "
        "everything, a conditional may be false, or every child may be a 2D object "
        "inside a 3D operation.",
    ),
    (
        "undefined_operation",
        "undefined operation",
        "An arithmetic operation used an undefined value, usually an unknown variable "
        "or a missing function argument; the result of that expression is undef.",
    ),
]


@dataclass
class DiagnosticRecord:
    """One parsed stderr line, with any TRACE lines folded in as a call stack."""

    severity: str
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    trace: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"severity": self.severity, "message": self.message}
        if self.file is not None:
            d["file"] = self.file
        if self.line is not None:
            d["line"] = self.line
        if self.trace:
            d["trace"] = list(self.trace)
        return d

    def format(self) -> str:
        loc = ""
        if self.file is not None and self.line is not None:
            loc = f" in file {self.file}, line {self.line}"
        return f"{self.severity}: {self.message}{loc}"


@dataclass
class Diagnostics:
    """Everything a tool needs to know about one OpenSCAD run."""

    returncode: Optional[int]
    records: List[DiagnosticRecord] = field(default_factory=list)
    echo_output: List[str] = field(default_factory=list)
    echo_truncated: bool = False
    statistics: Dict[str, Any] = field(default_factory=dict)
    empty_output: bool = False
    raw_tail: List[str] = field(default_factory=list)

    @property
    def errors(self) -> List[str]:
        return [r.format() for r in self.records if r.severity in SEVERITY_ERRORS]

    @property
    def warnings(self) -> List[str]:
        return [r.format() for r in self.records if r.severity in SEVERITY_WARNINGS]

    @property
    def deprecated(self) -> List[str]:
        return [r.format() for r in self.records if r.severity == "DEPRECATED"]

    @property
    def ok(self) -> bool:
        """The run produced a trustworthy result: exit 0 and no ERROR record."""
        return (self.returncode == 0) and not self.errors

    def hints(self) -> List[Dict[str, str]]:
        """Repair hints for the message classes present, each emitted once."""
        seen: set[str] = set()
        out: List[Dict[str, str]] = []
        texts = [r.message for r in self.records]
        if self.empty_output:
            texts.append(_EMPTY_OBJECT)
        for code, needle, advice in _HINTS:
            if code in seen:
                continue
            if any(needle in t for t in texts):
                seen.add(code)
                out.append({"code": code, "hint": advice})
        return out

    def mesh_health(self) -> Dict[str, Any]:
        """Summarise the CGAL banner. Absent statistics mean *unknown*, not failure.

        ``Volumes`` is deliberately not reported as a body count: CGAL counts the
        unbounded outer volume and every interior cavity, so a single hollow
        shell and two separate cubes both report 3.
        """
        stats = self.statistics
        simple = stats.get("Simple")
        manifold: Optional[bool] = None if simple is None else str(simple).lower() == "yes"
        health: Dict[str, Any] = {"manifold": manifold}
        if manifold is False:
            health["issue"] = (
                "not a valid 2-manifold; parts likely touch along an edge or face, "
                "or a surface self-intersects"
            )
        for key, out_key in (
            ("Vertices", "vertices"),
            ("Edges", "edges"),
            ("Facets", "facets"),
            ("Volumes", "nef_volumes"),
            ("Contours", "contours"),
        ):
            if key in stats:
                health[out_key] = stats[key]
        if manifold is None and "Facets" in stats:
            health["note"] = (
                "CGAL manifold check not performed (single primitive or preview path); "
                "manifoldness unknown"
            )
        return health

    def to_dict(self, include_records: bool = True) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "errors": self.errors,
            "warnings": self.warnings,
            "deprecated": self.deprecated,
            "echo_output": self.echo_output,
        }
        if self.echo_truncated:
            d["echo_truncated"] = True
        hints = self.hints()
        if hints:
            d["hints"] = hints
        if include_records:
            d["records"] = [r.to_dict() for r in self.records]
        return d


def _coerce_stat(value: str) -> Any:
    try:
        return int(value)
    except ValueError:
        return value


def parse_openscad_output(
    stderr: str,
    returncode: Optional[int] = None,
    inline_path: Optional[str] = None,
    echo_max_lines: int = ECHO_MAX_LINES,
    echo_max_chars: int = ECHO_MAX_CHARS,
) -> Diagnostics:
    """Parse OpenSCAD stderr into a :class:`Diagnostics`.

    Args:
        stderr: Raw stderr text.
        returncode: Process exit code, if known.
        inline_path: When the source was inline content written to a temp
            file, that file's path. Occurrences are rewritten to ``<inline>``
            so the model is not shown a temp path it cannot use.
        echo_max_lines / echo_max_chars: Caps on the echo channel.
    """
    diag = Diagnostics(returncode=returncode)
    inline_name = Path(inline_path).name if inline_path else None
    echo_count = 0

    for raw in (stderr or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        if inline_path:
            line = line.replace(inline_path, "<inline>")
            if inline_name:
                line = line.replace(inline_name, "<inline>")

        m_stat = _STAT_RE.match(line)
        if m_stat:
            diag.statistics[m_stat.group(1)] = _coerce_stat(m_stat.group(2))
            continue

        stripped = line.strip()

        if _EMPTY_OBJECT in stripped:
            diag.empty_output = True
            continue

        m_trace = _TRACE_RE.match(stripped)
        if m_trace:
            if diag.records:
                diag.records[-1].trace.append(m_trace.group("msg").strip())
            continue

        m = _DIAG_RE.match(stripped)
        if m:
            sev = m.group("sev")
            if sev == "ECHO":
                echo_count += 1
                if echo_count <= echo_max_lines:
                    text = m.group("msg")
                    # The regex may have peeled off a trailing "in file" clause
                    # from echo text that happens to contain it; restore it.
                    if m.group("file") is not None:
                        text = f"{text} in file {m.group('file')}, line {m.group('line')}"
                    if len(text) > echo_max_chars:
                        text = text[:echo_max_chars] + "...[truncated]"
                        diag.echo_truncated = True
                    diag.echo_output.append(text)
                else:
                    diag.echo_truncated = True
                continue
            line_no = int(m.group("line")) if m.group("line") else None
            diag.records.append(
                DiagnosticRecord(
                    severity=sev,
                    message=m.group("msg").strip(),
                    file=m.group("file"),
                    line=line_no,
                )
            )
            continue

        m_parse = _CANT_PARSE_RE.match(stripped)
        if m_parse:
            # Always follows a "Parser error" ERROR record; carries no new info.
            continue

        if stripped.startswith("ERROR"):
            # Colon-less error line (seen from some export paths). Strip the
            # marker so format() does not double it.
            diag.records.append(
                DiagnosticRecord(severity="ERROR", message=stripped[len("ERROR") :].strip())
            )
            continue

        # Keep the last few unrecognised lines so a failure with no known
        # marker can still be explained.
        diag.raw_tail.append(stripped)
        if len(diag.raw_tail) > 8:
            diag.raw_tail.pop(0)

    # A non-zero exit with no ERROR record means OpenSCAD failed for a reason
    # this parser has no pattern for: a rejected flag, a missing export format,
    # an unreadable file. Returning no error there is indistinguishable from
    # "no problems found", so synthesise one from the raw output.
    if returncode is not None and returncode != 0 and not diag.errors:
        if diag.empty_output:
            detail = "Current top level object is empty."
        else:
            detail = " | ".join(diag.raw_tail[-5:]) if diag.raw_tail else "no diagnostic output"
        diag.records.append(
            DiagnosticRecord(
                severity="ERROR",
                message=f"OpenSCAD exited with status {returncode}: {detail}",
            )
        )

    return diag


# ---------------------------------------------------------------------------
# Dependency file (-d) parsing
# ---------------------------------------------------------------------------


def parse_deps_file(text: str) -> List[str]:
    """Parse the Makefile-style dependency list OpenSCAD writes for ``-d``.

    Format::

        target: \\
        \t/abs/path/one.scad \\
        \t/abs/path/with\\ space/two.scad \\
        \tmain.scad

    Spaces inside paths are escaped with a backslash. The top-level file is
    echoed exactly as it was passed on the command line (possibly relative);
    every resolved dependency is absolute.

    Returns:
        Dependency paths in file order, unescaped, excluding the target.
    """
    if not text:
        return []
    joined = text.replace("\\\r\n", " ").replace("\\\n", " ")
    # Split off the target. A Windows drive letter is "X:\" so the first
    # ": " (colon + whitespace) is the separator, not the first colon.
    m = re.search(r":(?=\s)", joined)
    if not m:
        return []
    body = joined[m.end() :]
    deps: List[str] = []
    token: List[str] = []
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\" and i + 1 < n and body[i + 1] in (" ", "\t", "\\"):
            token.append(body[i + 1])
            i += 2
            continue
        if ch in (" ", "\t", "\n", "\r"):
            if token:
                deps.append("".join(token))
                token = []
            i += 1
            continue
        token.append(ch)
        i += 1
    if token:
        deps.append("".join(token))
    return [d.replace("$$", "$") for d in deps]


_MISSING_INCLUDE_RE = re.compile(r"Can't open (?:include file|library) '(?P<name>[^']+)'")


def unresolved_includes(diag: Diagnostics) -> List[str]:
    """Names of include/use targets OpenSCAD reported it could not open.

    ``-d`` records files that *were* read, not files that would have been.
    These names are negative dependencies: if one of them appears later, a
    cached render built without it is stale.
    """
    names: List[str] = []
    for r in diag.records:
        m = _MISSING_INCLUDE_RE.search(r.message)
        if m and m.group("name") not in names:
            names.append(m.group("name"))
    return names


# ---------------------------------------------------------------------------
# Source-level dependency extraction (no OpenSCAD process)
# ---------------------------------------------------------------------------

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_INCLUDE_USE_RE = re.compile(r"\b(?:include|use)\s*<\s*([^>\n]+?)\s*>")
_FILE_CALL_RE = re.compile(r"\b(?:import|surface)\s*\(\s*(?:file\s*=\s*)?\"([^\"\n]+)\"")


def extract_source_dependencies(text: str) -> List[str]:
    """Return the file references written in OpenSCAD source, in order.

    Finds ``include <...>`` and ``use <...>`` anywhere in the text (not only
    when alone on a line), plus ``import("...")`` and ``surface(file="...")``
    string literals. Comments are stripped first so commented-out references
    are ignored. Strings are not tokenised, so a ``<`` inside a string that
    looks like an include is a false positive; this is a static hint, and
    the ``-d`` closure is the authoritative list.
    """
    stripped = _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", text))
    found: List[str] = []
    for m in _INCLUDE_USE_RE.finditer(stripped):
        ref = m.group(1).strip()
        if ref and ref not in found:
            found.append(ref)
    for m in _FILE_CALL_RE.finditer(stripped):
        ref = m.group(1).strip()
        if ref and ref not in found:
            found.append(ref)
    return found


def image_token_estimate(width: int, height: int) -> int:
    """Approximate vision tokens for an image: ceil(w/28) * ceil(h/28)."""
    return -(-int(width) // 28) * -(-int(height) // 28)
