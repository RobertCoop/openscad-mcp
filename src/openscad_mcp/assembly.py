"""
The assembly model: named parts with placements, never unioned.

An assembly is an ordered set of named parts plus named frames. Each part
is evaluated *separately* (exported to its own mesh with its placement
applied) and every inter-part relation is a pairwise query between two
solids. This is deliberate: CGAL's union destroys part identity, and does
so non-uniformly depending on CSG history, so identity has to be carried
through the pipeline rather than recovered from a merged mesh.

This module holds the data model, the check-file grammar (YAML or JSON),
validation, frame resolution, and the per-part wrapper text. Exporting and
the geometric rules live in the server and in ``checks.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# A part's code must be a module instantiation, optionally wrapped in
# transforms/modifiers, ending in a call: "pinion();", "translate(P) lid();".
CALL_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*\s*\(")
FORBIDDEN_IN_CODE = re.compile(r"\b(include|use|import|surface|echo|assert)\b|[{}#!;]")
FORBIDDEN_IN_PLACE = re.compile(r"\b(include|use|import|surface|echo|assert)\b|[{}#!;]")

WORLD = "world"


class AssemblyError(ValueError):
    """A malformed assembly definition or check file."""


@dataclass
class Frame:
    name: str
    parent: Optional[str] = None
    lift: Optional[str] = None  # transform expression applied in the parent frame


@dataclass
class Part:
    name: str
    code: str
    place: Optional[str] = None
    frame: str = WORLD
    material: Optional[str] = None
    density_g_cm3: Optional[float] = None
    mass_g: Optional[float] = None
    printed: bool = True
    ghost: bool = False
    motion: Optional[Dict[str, Any]] = None
    print: Optional[Dict[str, Any]] = None
    color: Optional[str] = None
    explode: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "code": self.code}
        for key in (
            "place",
            "frame",
            "material",
            "density_g_cm3",
            "mass_g",
            "motion",
            "print",
            "color",
            "explode",
        ):
            val = getattr(self, key)
            if val is not None and not (key == "frame" and val == WORLD):
                d[key] = val
        if not self.printed:
            d["printed"] = False
        if self.ghost:
            d["ghost"] = True
        return d


@dataclass
class Assembly:
    parts: List[Part]
    frames: Dict[str, Frame] = field(default_factory=dict)
    quality: Dict[str, Any] = field(default_factory=dict)
    checks: List[Dict[str, Any]] = field(default_factory=list)
    variables: Dict[str, Any] = field(default_factory=dict)
    scad_file: Optional[str] = None
    version: int = 1

    def part(self, name: str) -> Part:
        for p in self.parts:
            if p.name == name:
                return p
        raise AssemblyError(f"unknown part '{name}'; parts are: {[p.name for p in self.parts]}")

    def names(self, include_ghosts: bool = True) -> List[str]:
        return [p.name for p in self.parts if include_ghosts or not p.ghost]

    @property
    def fn(self) -> Optional[int]:
        val = self.quality.get("fn")
        return int(val) if val is not None else None

    # -- frames -------------------------------------------------------------

    def frame_chain(self, frame_name: str) -> List[Frame]:
        """Frames from the root down to *frame_name* (root first)."""
        chain: List[Frame] = []
        seen: set[str] = set()
        current: Optional[str] = frame_name
        while current is not None and current != WORLD:
            if current in seen:
                raise AssemblyError(f"frame cycle at '{current}'")
            seen.add(current)
            frame = self.frames.get(current)
            if frame is None:
                raise AssemblyError(f"unknown frame '{current}'")
            chain.append(frame)
            current = frame.parent or WORLD
        chain.reverse()
        return chain

    def frame_transform_expr(self, frame_name: str) -> str:
        """Composed lift expressions, outermost (root) first, as SCAD prefix text."""
        lifts = [f.lift for f in self.frame_chain(frame_name) if f.lift]
        return " ".join(lifts)

    def placement_expr(self, part: Part) -> str:
        """Everything that goes in front of the part's code: frames then place."""
        pieces = []
        frame_expr = self.frame_transform_expr(part.frame)
        if frame_expr:
            pieces.append(frame_expr)
        if part.place:
            pieces.append(part.place.strip())
        return " ".join(pieces)

    def part_statement(self, part: Part, explode: bool = False) -> str:
        """``placement { code }`` for one part, as a single SCAD statement."""
        code = part.code.strip()
        if not code.endswith(";") and not code.endswith("}"):
            code += ";"
        prefix = self.placement_expr(part)
        if explode and part.explode:
            vec = ", ".join(str(float(v)) for v in part.explode)
            prefix = f"translate([{vec}]) {prefix}".strip()
        if prefix:
            return f"{prefix} {{ {code} }}"
        return code

    def part_body(self, part: Part) -> str:
        """The rooted body to append to a wrapper: exports exactly this part, placed."""
        return "!union() {\n    " + self.part_statement(part) + "\n}\n"

    def cache_material(self, part: Part) -> str:
        """The part-specific portion of a mesh cache key."""
        return json.dumps(
            {
                "code": part.code,
                "placement": self.placement_expr(part),
                "fn": self.fn,
                "fa": self.quality.get("fa"),
                "fs": self.quality.get("fs"),
            },
            sort_keys=True,
        )

    def quality_variables(self) -> Dict[str, Any]:
        """``$fn``/``$fa``/``$fs`` overrides implied by ``quality``."""
        out: Dict[str, Any] = {}
        for key in ("fn", "fa", "fs"):
            if self.quality.get(key) is not None:
                out[f"${key}"] = self.quality[key]
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "quality": dict(self.quality),
            "frames": {
                n: {k: v for k, v in (("parent", f.parent), ("lift", f.lift)) if v}
                for n, f in self.frames.items()
            },
            "parts": [p.to_dict() for p in self.parts],
            "checks": list(self.checks),
        }


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------


def _validate_code(name: str, code: str) -> str:
    code = (code or "").strip()
    if not code:
        raise AssemblyError(f"part '{name}': code is required, e.g. \"{name}();\"")
    if FORBIDDEN_IN_CODE.search(code.rstrip(";")):
        raise AssemblyError(
            f"part '{name}': code must be a plain module instantiation "
            f"(no include/use/import, braces or ';' inside): {code!r}"
        )
    if not CALL_RE.match(code):
        raise AssemblyError(f"part '{name}': code must start with a module call: {code!r}")
    return code


def _validate_place(name: str, place: Optional[str]) -> Optional[str]:
    if place is None:
        return None
    place = str(place).strip()
    if not place:
        return None
    if FORBIDDEN_IN_PLACE.search(place):
        raise AssemblyError(
            f"part '{name}': place must be a transform expression such as "
            f'"translate(PINION_POS)": {place!r}'
        )
    return place


def _coerce_part(raw: Any, index: int) -> Part:
    if isinstance(raw, str):
        raw = {"name": raw.rstrip("(); ").strip(), "code": raw}
    if not isinstance(raw, dict):
        raise AssemblyError(f"part #{index}: expected an object with name and code")
    name = str(raw.get("name") or "").strip()
    if not name:
        code_guess = str(raw.get("code") or "")
        name = code_guess.split("(")[0].strip()
    if not IDENT_RE.match(name):
        raise AssemblyError(f"part #{index}: name {name!r} must match {IDENT_RE.pattern}")
    part = Part(
        name=name,
        code=_validate_code(name, str(raw.get("code") or "")),
        place=_validate_place(name, raw.get("place")),
        frame=str(raw.get("frame") or WORLD),
        material=raw.get("material"),
        density_g_cm3=_opt_float(raw.get("density_g_cm3")),
        mass_g=_opt_float(raw.get("mass_g")),
        printed=bool(raw.get("printed", True)),
        ghost=bool(raw.get("ghost", False)),
        motion=raw.get("motion"),
        print=raw.get("print"),
        color=raw.get("color"),
        explode=[float(v) for v in raw["explode"]] if raw.get("explode") else None,
    )
    if part.motion is not None:
        _validate_motion(name, part.motion)
    return part


def _opt_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _validate_motion(name: str, motion: Dict[str, Any]) -> None:
    kind = motion.get("type")
    if kind not in ("rotate", "translate"):
        raise AssemblyError(f"part '{name}': motion.type must be rotate or translate")
    axis = motion.get("axis") or motion.get("vector")
    if not _is_vec_or_expr(axis, 3):
        raise AssemblyError(f"part '{name}': motion needs axis (rotate) or vector (translate)")
    if "range" in motion and not _is_vec_or_expr(motion["range"], 2):
        raise AssemblyError(f"part '{name}': motion.range must be [start, end]")
    _validate_expressions(motion, f"part '{name}' motion")


def parse_parts(parts: Any) -> List[Part]:
    """Accept the tool argument forms: list of dicts, dict name->code, JSON text."""
    if isinstance(parts, str):
        try:
            parts = json.loads(parts)
        except json.JSONDecodeError as exc:
            raise AssemblyError("parts must be a list of {name, code, place?} objects") from exc
    if isinstance(parts, dict):
        parts = [
            {"name": k, **(v if isinstance(v, dict) else {"code": v})} for k, v in parts.items()
        ]
    if not isinstance(parts, list) or not parts:
        raise AssemblyError("parts must be a non-empty list of {name, code, place?} objects")
    out: List[Part] = []
    seen: set[str] = set()
    for i, raw in enumerate(parts):
        part = _coerce_part(raw, i)
        if part.name in seen:
            raise AssemblyError(f"duplicate part name '{part.name}'")
        seen.add(part.name)
        out.append(part)
    return out


def parse_frames(raw: Any) -> Dict[str, Frame]:
    frames: Dict[str, Frame] = {}
    if not raw:
        return frames
    if not isinstance(raw, dict):
        raise AssemblyError("frames must be a mapping of name -> {parent, lift}")
    for name, spec in raw.items():
        if not IDENT_RE.match(str(name)):
            raise AssemblyError(f"frame name {name!r} must match {IDENT_RE.pattern}")
        spec = spec or {}
        if not isinstance(spec, dict):
            raise AssemblyError(f"frame '{name}': expected {{parent, lift}}")
        lift = _validate_place(f"frame {name}", spec.get("lift"))
        frames[str(name)] = Frame(name=str(name), parent=spec.get("parent"), lift=lift)
    for frame in frames.values():
        if frame.parent and frame.parent != WORLD and frame.parent not in frames:
            raise AssemblyError(f"frame '{frame.name}': unknown parent '{frame.parent}'")
    return frames


KNOWN_RULES = {
    "no_intersect",
    "interference",
    "clearance",
    "contact",
    "alignment",
    "predicate",
    "probe",
    "ray",
    "sweep",
    "print",
    "mass",
}


def _validate_check(rule: Dict[str, Any], index: int, part_names: List[str]) -> Dict[str, Any]:
    if not isinstance(rule, dict) or "rule" not in rule:
        raise AssemblyError(f"checks[{index}]: expected an object with a 'rule' key")
    kind = rule["rule"]
    if kind not in KNOWN_RULES:
        raise AssemblyError(f"checks[{index}]: unknown rule '{kind}'; known: {sorted(KNOWN_RULES)}")
    if kind == "no_intersect":
        rule = dict(rule, rule="interference")
        kind = "interference"
    pairs = rule.get("pairs")
    if pairs not in (None, "all"):
        if not isinstance(pairs, list):
            raise AssemblyError(f"checks[{index}]: pairs must be 'all' or a list of [a, b]")
        for pair in pairs:
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                raise AssemblyError(f"checks[{index}]: each pair must be [a, b]")
            for nm in pair:
                if nm not in part_names:
                    raise AssemblyError(f"checks[{index}]: unknown part '{nm}' in pairs")
    if kind == "contact" and rule.get("kind") not in (None, "static", "sliding"):
        raise AssemblyError(f"checks[{index}]: contact.kind must be static or sliding")
    for key in ("part", "moving", "first_hit"):
        val = rule.get(key)
        if val is not None and val != "all" and val not in part_names:
            raise AssemblyError(f"checks[{index}]: unknown part '{val}' in {key}")
    if kind == "mass":
        _validate_mass_rule(rule, index, part_names)
    _validate_expressions(rule, f"checks[{index}]")
    return rule


def _validate_mass_rule(rule: Dict[str, Any], index: int, part_names: List[str]) -> None:
    names = rule.get("parts")
    if names not in (None, "all"):
        if not isinstance(names, list) or not names:
            raise AssemblyError(f"checks[{index}]: mass.parts must be 'all' or a list of names")
        for nm in names:
            if nm not in part_names:
                raise AssemblyError(f"checks[{index}]: unknown part '{nm}' in parts")
    for key in ("max_g", "min_g", "com_within_mm", "max_inertia_g_mm2", "density_g_cm3"):
        val = rule.get(key)
        if val is not None and not isinstance(val, str):
            try:
                if float(val) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise AssemblyError(
                    f"checks[{index}]: mass.{key} must be a non-negative number"
                ) from None
    axis, point = rule.get("axis"), rule.get("point")
    if axis is not None and not isinstance(axis, str):
        if not (
            isinstance(axis, (list, tuple))
            and len(axis) == 2
            and all(_is_vec_or_expr(v, 3) for v in axis)
        ):
            raise AssemblyError(f"checks[{index}]: mass.axis must be [[x,y,z],[dx,dy,dz]]")
        direction = axis[1]
        if not isinstance(direction, str) and all(
            not isinstance(v, str) and float(v) == 0.0 for v in direction
        ):
            raise AssemblyError(f"checks[{index}]: mass.axis direction must not be zero")
    if point is not None and not _is_vec_or_expr(point, 3):
        raise AssemblyError(f"checks[{index}]: mass.point must be [x,y,z]")
    if rule.get("com_within_mm") is not None and axis is None and point is None:
        raise AssemblyError(f"checks[{index}]: mass.com_within_mm needs axis or point")
    if rule.get("max_inertia_g_mm2") is not None and axis is None:
        raise AssemblyError(f"checks[{index}]: mass.max_inertia_g_mm2 needs axis")
    if not any(
        rule.get(k) is not None for k in ("max_g", "min_g", "com_within_mm", "max_inertia_g_mm2")
    ):
        return  # facts-only row


# ---------------------------------------------------------------------------
# Expression-valued numbers
# ---------------------------------------------------------------------------
#
# Any number or vector under one of these keys may be written as a SCAD
# expression string ("[BOLT_R, 0, BASE_H]", "GAP * 2") and is evaluated in the
# model's own scope before the rules run, so a check file tracks the design's
# parameters instead of a snapshot of them. Text-valued keys (rule, why, part,
# expr, expect, ...) are never evaluated.

EXPRESSION_KEYS = frozenset(
    {
        # coordinates
        "point",
        "origin",
        "direction",
        "axis",
        "center",
        "vector",
        "range",
        "range_deg",
        "range_mm",
        # limits
        "tolerance_mm",
        "min_mm",
        "required_mm",
        "min_gap_mm",
        "min_area_mm2",
        "max_distance_mm",
        "size_mm",
        "steps",
        "max_g",
        "min_g",
        "com_within_mm",
        "max_inertia_g_mm2",
        "density_g_cm3",
        "max_overhang_deg",
        "max_overhang_area_mm2",
        "min_feature_mm",
        "max_unsupported_reach_mm",
        "nozzle_mm",
        "layer_height_mm",
    }
)

FORBIDDEN_IN_EXPRESSION = re.compile(r"\b(include|use|import|surface|echo|assert)\b|[{};]")


def _is_vec_or_expr(value: Any, length: int) -> bool:
    if isinstance(value, str):
        return True
    return isinstance(value, (list, tuple)) and len(value) == length


def _walk_expressions(container: Any, label: str):
    """Yield ``(container, key, expr, label)`` for every string under an expression key."""
    if isinstance(container, dict):
        for key, val in container.items():
            if key not in EXPRESSION_KEYS:
                continue
            yield from _walk_value(container, key, val, f"{label}.{key}")
    elif isinstance(container, list):
        for i, val in enumerate(container):
            yield from _walk_value(container, i, val, f"{label}[{i}]")


def _walk_value(parent: Any, key: Any, val: Any, label: str):
    if isinstance(val, str):
        yield (parent, key, val, label)
    elif isinstance(val, list):
        for i, item in enumerate(val):
            yield from _walk_value(val, i, item, f"{label}[{i}]")


def _validate_expressions(container: Dict[str, Any], label: str) -> None:
    for _parent, _key, expr, where in _walk_expressions(container, label):
        if not expr.strip():
            raise AssemblyError(f"{where}: empty expression")
        if FORBIDDEN_IN_EXPRESSION.search(expr):
            raise AssemblyError(
                f"{where}: expression {expr!r} may not contain statements, "
                "braces, semicolons or include/use/import/echo/assert"
            )


@dataclass
class ExpressionSlot:
    """One expression string waiting for its value: where it sits and what it says."""

    parent: Any  # the dict or list holding it
    key: Any  # the key or index in ``parent``
    expr: str
    label: str  # human-readable location, e.g. "checks[3].point[2]"
    rule: Optional[Dict[str, Any]]  # the owning rule, if any (None for a motion block)


def collect_expression_slots(asm: Assembly) -> List[ExpressionSlot]:
    """Every expression-valued number in the assembly's rules and motion blocks."""
    slots: List[ExpressionSlot] = []
    for i, rule in enumerate(asm.checks):
        for parent, key, expr, label in _walk_expressions(rule, f"checks[{i}]"):
            slots.append(ExpressionSlot(parent, key, expr, label, rule))
    for part in asm.parts:
        if part.motion:
            for parent, key, expr, label in _walk_expressions(
                part.motion, f"parts.{part.name}.motion"
            ):
                slots.append(ExpressionSlot(parent, key, expr, label, None))
    return slots


def apply_expression_values(slots: List[ExpressionSlot], results: List[Dict[str, Any]]) -> None:
    """Substitute evaluated values into the slots.

    ``results`` is what :func:`openscad_mcp.wrappers.collect_eval_results`
    returns, one per slot in order. A slot whose expression did not evaluate
    to a number or a vector of numbers marks its rule ``_unresolved`` with a
    note instead of raising, so one bad expression yields one UNRESOLVED row.
    Every rule that had expressions gets an ``_expressions`` map of
    label -> {expr, value}, which the engine copies onto its rows.
    """
    for slot, res in zip(slots, results, strict=True):
        value = res.get("value") if res.get("evaluated") else None
        ok = _is_numeric_value(value)
        if ok:
            slot.parent[slot.key] = value
        if slot.rule is not None:
            record = slot.rule.setdefault("_expressions", {})
            record[slot.label] = {"expr": slot.expr, "value": value if ok else None}
            if not ok:
                shown = "undef" if value is None else repr(value)
                note = f"{slot.label}: expression {slot.expr!r} evaluated to {shown}, not a number"
                prev = slot.rule.get("_unresolved")
                slot.rule["_unresolved"] = f"{prev}; {note}" if prev else note
        elif not ok:
            raise AssemblyError(
                f"{slot.label}: expression {slot.expr!r} evaluated to "
                f"{'undef' if value is None else value!r}, not a number"
            )


def _is_numeric_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, list):
        return all(_is_numeric_value(v) for v in value)
    return False


def parse_assembly(data: Dict[str, Any], scad_file: Optional[str] = None) -> Assembly:
    """Build an Assembly from a parsed check file (dict)."""
    if not isinstance(data, dict):
        raise AssemblyError("check file must be a mapping")
    parts = parse_parts(data.get("parts") or [])
    frames = parse_frames(data.get("frames"))
    for p in parts:
        if p.frame != WORLD and p.frame not in frames:
            raise AssemblyError(f"part '{p.name}': unknown frame '{p.frame}'")
    quality = dict(data.get("quality") or {})
    names = [p.name for p in parts]
    checks = [_validate_check(c, i, names) for i, c in enumerate(data.get("checks") or [])]
    variables = dict(data.get("variables") or {})
    asm = Assembly(
        parts=parts,
        frames=frames,
        quality=quality,
        checks=checks,
        variables=variables,
        scad_file=data.get("model") or scad_file,
        version=int(data.get("version") or 1),
    )
    # Frame chains must resolve for every part.
    for p in parts:
        asm.frame_chain(p.frame)
    return asm


def load_check_file(text: str, scad_file: Optional[str] = None) -> Assembly:
    """Parse YAML or JSON check-file text."""
    data: Any
    stripped = text.strip()
    if stripped.startswith("{"):
        data = json.loads(stripped)
    else:
        import yaml

        data = yaml.safe_load(stripped)
    return parse_assembly(data, scad_file=scad_file)


def pairs_for(assembly: Assembly, pairs: Any, include_ghosts: bool = True) -> List[tuple[str, str]]:
    """Resolve a pairs argument ('all' | list | None) to name tuples."""
    names = assembly.names(include_ghosts=include_ghosts)
    if pairs in (None, "all"):
        return [(a, b) for i, a in enumerate(names) for b in names[i + 1 :]]
    out: List[tuple[str, str]] = []
    for pair in pairs:
        a, b = str(pair[0]), str(pair[1])
        assembly.part(a)
        assembly.part(b)
        out.append((a, b))
    return out


def assembly_digest(assembly: Assembly) -> str:
    """Stable hash of the whole definition (for result caching and reports)."""
    return hashlib.sha256(json.dumps(assembly.to_dict(), sort_keys=True).encode()).hexdigest()[:16]
