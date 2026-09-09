"""
Source-level wrappers around a user's OpenSCAD model, and echo value parsing.

Several tools need to evaluate *the user's geometry inside another
expression*: a cross-section is ``projection(cut=true)`` of the model, a
per-part render is ``color(c) part()`` next to the model's module
definitions, and expression evaluation needs the model's own variables in
scope. OpenSCAD cannot wrap a file's top-level statements from the outside,
but it can include a file inside a module body::

    module __model() { include <model.scad> }
    projection(cut=true) __model();

Two facts about that construction, verified on 2021.01:

* ``-D name=value`` does **not** reach variables inside the included file
  when it is module-scoped. An assignment appended inside the module body
  after the include line does override them (last assignment in a scope
  wins) and prints no warning. So caller variables are injected that way.
* A cut plane that misses the solid makes OpenSCAD print
  ``WARNING: Projection() failed.`` and exit 1 with no output file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MODEL_MODULE = "__model"
EVAL_MARKER = "__OPENSCAD_MCP_EVAL__"


def format_scad_value(value: Any) -> str:
    """Render a Python value as an OpenSCAD literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "undef"
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(format_scad_value(v) for v in value) + "]"
    return str(value)


def variable_assignments(variables: Optional[Dict[str, Any]]) -> str:
    """OpenSCAD assignment statements for a variables dict."""
    if not variables:
        return ""
    return "\n".join(f"{k} = {format_scad_value(v)};" for k, v in variables.items()) + "\n"


def model_module(
    include_path: Path,
    variables: Optional[Dict[str, Any]] = None,
    extra_body: str = "",
) -> str:
    """Define ``__model()`` wrapping the user's file, with variables injected."""
    return (
        f"module {MODEL_MODULE}() {{\n"
        f"    include <{include_path.as_posix()}>\n"
        f"{_indent(variable_assignments(variables))}"
        f"{_indent(extra_body)}"
        "}\n"
    )


def _indent(text: str, prefix: str = "    ") -> str:
    if not text:
        return ""
    return "".join(prefix + line + "\n" for line in text.rstrip("\n").splitlines())


SECTION_AXES = {"x", "y", "z"}


def section_transform(axis: str, offset: float) -> str:
    """Transform that moves the requested cut plane onto ``z = 0``.

    ``axis`` is the normal of the cut plane: ``"z"`` cuts horizontally at
    ``z = offset``; ``"x"`` cuts the plane ``x = offset``; ``"y"`` the plane
    ``y = offset``. After the transform the section lies in the XY plane and
    is exported by ``projection(cut=true)`` with these in-plane axes:

    * axis z: section X = model X, section Y = model Y
    * axis x: section X = model Y, section Y = model Z
    * axis y: section X = model X, section Y = model Z
    """
    axis = axis.lower()
    if axis not in SECTION_AXES:
        raise ValueError(f"section axis must be one of {sorted(SECTION_AXES)}, got {axis!r}")
    if axis == "z":
        return f"translate([0, 0, {-offset}])"
    if axis == "x":
        # rotate about Y by -90: (x,y,z) -> (-z, y, x); then about Z by -90 so
        # in-plane X = model Y and in-plane Y = model Z (a view from +X).
        return f"rotate([0, 0, -90]) rotate([0, -90, 0]) translate([{-offset}, 0, 0])"
    # axis y: rotate about X by +90: (x,y,z) -> (x, -z, y); in-plane X = model X,
    # in-plane Y = model Z (a view from -Y, i.e. the front).
    return f"rotate([90, 0, 0]) translate([0, {-offset}, 0])"


def section_in_plane_axes(axis: str) -> Tuple[str, str]:
    axis = axis.lower()
    return {"z": ("x", "y"), "x": ("y", "z"), "y": ("x", "z")}[axis]


def section_wrapper(
    include_path: Path,
    axis: str,
    offset: float,
    variables: Optional[Dict[str, Any]] = None,
) -> str:
    """Source that exports the cross-section of the model as 2D geometry."""
    return (
        model_module(include_path, variables)
        + f"projection(cut = true) {section_transform(axis, offset)} {MODEL_MODULE}();\n"
    )


def parts_wrapper(
    include_path: Path,
    parts: List[Dict[str, str]],
    colors: List[str],
    isolate: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    ghost_others: bool = True,
) -> str:
    """Source that instantiates each part in its own colour.

    ``parts`` is a list of ``{"name": ..., "code": ...}`` where ``code`` is an
    OpenSCAD statement using the model's modules (for example ``"lid();"``).
    The parts are wrapped in a ``!``-rooted union, so the model file's own
    top-level geometry is not drawn: only the parts are. With ``isolate``
    set, every other part is drawn as a translucent ghost (the ``%``
    modifier) or omitted when ``ghost_others`` is false.
    """
    body = []
    for idx, part in enumerate(parts):
        code = _statement(part["code"])
        color = colors[idx % len(colors)]
        if isolate and part["name"] != isolate:
            if not ghost_others:
                continue
            # % alone keeps a color() child's colour but not translucency;
            # an explicit alpha makes the ghost see-through in preview.
            body.append(f'%color("{color}", 0.3) {{ {code} }}')
        else:
            body.append(f'color("{color}") {{ {code} }}')
    rooted = "!union() {\n" + _indent(chr(10).join(body)) + "}\n"
    return model_module(include_path, variables, extra_body=rooted) + f"{MODEL_MODULE}();\n"


def _statement(code: str) -> str:
    code = code.strip()
    if not code.endswith(";") and not code.endswith("}"):
        code += ";"
    return code


def part_wrapper(
    include_path: Path,
    code: str,
    variables: Optional[Dict[str, Any]] = None,
) -> str:
    """Source that evaluates exactly one part's code with the model's modules.

    Uses the ``!`` root modifier so the model's own top-level geometry is
    excluded from the export.
    """
    rooted = "!union() {\n" + _indent(_statement(code)) + "}\n"
    return model_module(include_path, variables, extra_body=rooted) + f"{MODEL_MODULE}();\n"


def eval_wrapper(
    include_path: Path,
    expressions: List[str],
    variables: Optional[Dict[str, Any]] = None,
) -> str:
    """Source that echoes each expression, evaluated in the model's scope.

    The model's top-level geometry is still instantiated (echo runs during
    evaluation), but the run uses CSG export so no CGAL work happens.
    """
    echoes = "\n".join(
        f'echo("{EVAL_MARKER}", {i}, ({expr}));' for i, expr in enumerate(expressions)
    )
    return model_module(include_path, variables, extra_body=echoes) + f"{MODEL_MODULE}();\n"


# ---------------------------------------------------------------------------
# Echo value parsing
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


class _EchoParser:
    """Recursive-descent parser for the value syntax OpenSCAD prints in ECHO.

    Handles numbers (6 significant digits, scientific notation), strings,
    ``true``/``false``/``undef``, vectors (nested), and ranges
    ``[start : step : end]``.
    """

    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def parse_all(self) -> List[Any]:
        values = []
        self._ws()
        while self.pos < len(self.text):
            values.append(self._value())
            self._ws()
            if self.pos < len(self.text) and self.text[self.pos] == ",":
                self.pos += 1
                self._ws()
        return values

    def _ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def _value(self) -> Any:
        self._ws()
        if self.pos >= len(self.text):
            raise ValueError("unexpected end of echo text")
        ch = self.text[self.pos]
        if ch == '"':
            return self._string()
        if ch == "[":
            return self._vector_or_range()
        for word, val in (("true", True), ("false", False), ("undef", None)):
            if self.text.startswith(word, self.pos):
                self.pos += len(word)
                return val
        m = _NUMBER_RE.match(self.text, self.pos)
        if m:
            self.pos = m.end()
            s = m.group(0)
            if any(c in s for c in ".eE"):
                return float(s)
            return int(s)
        # Unknown token (e.g. a module reference): take a bare word
        m2 = re.compile(r"[^,\]\s]+").match(self.text, self.pos)
        if m2:
            self.pos = m2.end()
            return m2.group(0)
        raise ValueError(f"cannot parse echo value at {self.text[self.pos:self.pos + 20]!r}")

    def _string(self) -> str:
        assert self.text[self.pos] == '"'
        self.pos += 1
        out = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == "\\" and self.pos + 1 < len(self.text):
                nxt = self.text[self.pos + 1]
                out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
                self.pos += 2
                continue
            if ch == '"':
                self.pos += 1
                return "".join(out)
            out.append(ch)
            self.pos += 1
        raise ValueError("unterminated string in echo text")

    def _vector_or_range(self) -> Any:
        assert self.text[self.pos] == "["
        self.pos += 1
        items: List[Any] = []
        is_range = False
        self._ws()
        if self.pos < len(self.text) and self.text[self.pos] == "]":
            self.pos += 1
            return items
        while True:
            items.append(self._value())
            self._ws()
            if self.pos >= len(self.text):
                raise ValueError("unterminated vector in echo text")
            ch = self.text[self.pos]
            if ch == ",":
                self.pos += 1
                continue
            if ch == ":":
                is_range = True
                self.pos += 1
                continue
            if ch == "]":
                self.pos += 1
                break
            raise ValueError(f"unexpected {ch!r} in vector")
        if is_range:
            if len(items) == 2:
                start, end = items
                step = 1
            else:
                start, step, end = items[0], items[1], items[2]
            return {"range": [start, step, end]}
        return items


def parse_echo_values(text: str) -> List[Any]:
    """Parse the comma-separated values from one ECHO line's payload."""
    return _EchoParser(text).parse_all()


def collect_eval_results(echo_lines: List[str], count: int) -> List[Dict[str, Any]]:
    """Pair ``echo("<marker>", i, value)`` lines with their expression index."""
    results: List[Dict[str, Any]] = [
        {"index": i, "value": None, "evaluated": False} for i in range(count)
    ]
    for line in echo_lines:
        if EVAL_MARKER not in line:
            continue
        try:
            values = parse_echo_values(line)
        except ValueError as exc:
            # Keep going; report the raw text for this line.
            for r in results:
                if not r["evaluated"]:
                    r["error"] = f"could not parse echo: {exc}"
                    break
            continue
        if len(values) < 2 or values[0] != EVAL_MARKER:
            continue
        idx = values[1]
        if not isinstance(idx, int) or not 0 <= idx < count:
            continue
        value = values[2] if len(values) > 2 else None
        results[idx] = {
            "index": idx,
            "value": value,
            "type": scad_type_name(value),
            "evaluated": True,
        }
    return results


def scad_type_name(value: Any) -> str:
    if value is None:
        return "undef"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict) and "range" in value:
        return "range"
    if isinstance(value, list):
        return "vector"
    return "unknown"
