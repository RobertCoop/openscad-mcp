"""
Rule evaluation for assemblies: the ``check`` tool's engine.

Every rule produces rows of one shape::

    {"rule": ..., "subject": [...], "status": "PASS"|"FAIL"|"UNRESOLVED",
     "state": ..., "magnitude": {...}, "at": [x,y,z], "quality": {...},
     "tier": "python"|"openscad", "why": ...}

Geometry comes from :mod:`openscad_mcp.geom` over per-part meshes that the
server exported separately (never unioned). Interference/contact/clearance
are one classification ladder; a coincident-face pair is *contact*, never
interference, and never decided by an OpenSCAD intersection volume.

Quality provenance: every geometric row carries the tessellation ``fn`` and,
when the caller supplies the largest curved radius of the parts involved,
the inscribed-polygon error bound. A distance smaller than that bound is
reported as UNRESOLVED rather than as a number.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import geom
from .assembly import Assembly, Part, pairs_for

Vec3 = Tuple[float, float, float]


def _v3(seq: Any) -> Vec3:
    """Coerce a 3-sequence to a typed Vec3."""
    return (float(seq[0]), float(seq[1]), float(seq[2]))


@dataclass
class Quality:
    fn: Optional[int]
    curved_radius_mm: Optional[float] = None  # largest curved feature radius among the parts

    def error_bound_mm(self) -> Optional[float]:
        if self.curved_radius_mm is None:
            return None
        segs = geom.segments_for(self.curved_radius_mm, self.fn or 0)
        return geom.inscribed_polygon_error(self.curved_radius_mm, segs)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"fn": self.fn, "curved_features": self.curved_radius_mm is not None}
        if self.fn is None:
            d["note"] = "no $fn override; the model's own $fn/$fa/$fs apply"
        bound = self.error_bound_mm()
        if bound is not None:
            d["error_bound_mm"] = round(bound, 4)
        return d


def _round_vec(v: Optional[Sequence[float]], nd: int = 3) -> Optional[List[float]]:
    if v is None:
        return None
    return [round(float(x), nd) for x in v]


def relation_row(
    rule: str,
    a: str,
    b: str,
    rel: geom.PairRelation,
    quality: Quality,
    why: str = "",
) -> Dict[str, Any]:
    if not math.isfinite(rel.distance_mm):
        magnitude: Dict[str, Any] = {"distance_mm": None}
    else:
        magnitude = {"distance_mm": round(rel.distance_mm, 4)}
    if rel.penetration_mm is not None:
        magnitude["penetration_mm"] = round(rel.penetration_mm, 4)
    if rel.contact_area_mm2 is not None:
        magnitude["contact_area_mm2"] = round(rel.contact_area_mm2, 3)
    if rel.intersection_volume_mm3 is not None:
        magnitude["intersection_volume_mm3"] = round(rel.intersection_volume_mm3, 4)
    row: Dict[str, Any] = {
        "rule": rule,
        "subject": [a, b],
        "state": rel.state,
        "magnitude": magnitude,
        "at": _round_vec(rel.at),
        "quality": quality.to_dict(),
        "tier": "python",
    }
    if rel.normal is not None:
        row["normal"] = _round_vec(rel.normal)
    if rel.plane_offset is not None and rel.normal is not None:
        normal = rel.normal
        axis = max(range(3), key=lambda i: abs(normal[i]))
        if abs(abs(normal[axis]) - 1.0) < 1e-6:
            row["plane"] = (
                f"{'xyz'[axis]} = {rel.plane_offset * (1 if normal[axis] > 0 else -1):.3f}"
            )
    if rel.closest is not None:
        row["closest"] = [_round_vec(rel.closest[0]), _round_vec(rel.closest[1])]
    if not math.isfinite(rel.distance_mm):
        row["status"] = "UNRESOLVED"
        row["note"] = "one part has degenerate geometry (no finite distance)"
    if why:
        row["why"] = why
    return row


def _unresolved(row: Dict[str, Any], bound: float) -> Dict[str, Any]:
    row["status"] = "UNRESOLVED"
    fn = row["quality"].get("fn")
    fn_text = f"$fn={fn}" if fn is not None else "the model's own $fn/$fa/$fs"
    row["note"] = (
        f"distance {row['magnitude'].get('distance_mm')} mm is inside the tessellation "
        f"error bound {bound:.4f} mm at {fn_text}; re-run with quality=high or a larger "
        "$fn to resolve"
    )
    return row


class RuleEngine:
    """Evaluates assembly rules over exported meshes.

    ``meshes`` maps part name -> :class:`geom.Mesh` in the assembly frame.
    ``predicate_runner`` and ``feature_provider`` are supplied by the server
    for rules that need OpenSCAD (predicates) or the CSG dump (alignment).
    """

    def __init__(
        self,
        assembly: Assembly,
        meshes: Dict[str, geom.Mesh],
        quality: Quality,
        predicate_runner: Optional[Callable[[List[str]], List[Dict[str, Any]]]] = None,
        feature_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        printability_provider: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
        volume_cross_check: Optional[Callable[[str, str], Optional[float]]] = None,
    ):
        self.asm = assembly
        self.meshes = meshes
        self.quality = quality
        self.predicate_runner = predicate_runner
        self.feature_provider = feature_provider
        self.printability_provider = printability_provider
        self.volume_cross_check = volume_cross_check
        self._relations: Dict[Tuple[str, str], geom.PairRelation] = {}
        self.pairs_evaluated = 0
        self.pairs_aabb_separated = 0

    # -- pairwise relations (computed once per pair) --------------------------

    def relation(self, a: str, b: str, tolerance: float = 0.0) -> geom.PairRelation:
        key = (a, b) if a <= b else (b, a)
        cached = self._relations.get(key)
        if cached is not None and tolerance == 0.0:
            return cached
        ma, mb = self.meshes[a], self.meshes[b]
        gap = geom.aabb_gap(ma, mb)
        if gap > max(tolerance, 0.0) + 1e-9:
            self.pairs_aabb_separated += 1
            self.pairs_evaluated += 1
            rel = geom.PairRelation(
                state="clear",
                distance_mm=gap,
                penetration_mm=None,
                contact_area_mm2=None,
                normal=None,
                plane_offset=None,
                at=None,
                closest=None,
            )
            # AABB gap is a lower bound; refine with the exact distance so
            # clearance rules get a real number.
            exact = geom.min_distance(ma, mb)
            rel.distance_mm = exact.distance
            rel.closest = (exact.point_a, exact.point_b)
            rel.at = _v3([(exact.point_a[i] + exact.point_b[i]) / 2 for i in range(3)])
        else:
            self.pairs_evaluated += 1
            rel = geom.classify_pair(ma, mb, tolerance=tolerance)
        if self.volume_cross_check is not None and rel.state == "interference":
            rel.intersection_volume_mm3 = self.volume_cross_check(a, b)
        if tolerance == 0.0:
            self._relations[key] = rel
        return rel

    # -- rules ------------------------------------------------------------------

    def run(self, rules: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for rule in rules if rules is not None else self.asm.checks:
            handler = getattr(self, f"rule_{rule['rule']}", None)
            if handler is None:
                rows.append(
                    {
                        "rule": rule["rule"],
                        "subject": [],
                        "status": "UNRESOLVED",
                        "note": f"rule '{rule['rule']}' is not implemented",
                    }
                )
                continue
            try:
                t0 = time.perf_counter()
                produced = handler(rule)
                elapsed = round(time.perf_counter() - t0, 3)
                for r in produced:
                    r.setdefault("elapsed_s", elapsed)
                rows.extend(produced)
            except Exception as exc:  # one bad rule must not kill the report
                rows.append(
                    {
                        "rule": rule["rule"],
                        "subject": [],
                        "status": "UNRESOLVED",
                        "note": f"{type(exc).__name__}: {exc}",
                    }
                )
        return rows

    def _pairs(self, rule: Dict[str, Any], include_ghosts: bool = True) -> List[Tuple[str, str]]:
        return [
            (a, b)
            for a, b in pairs_for(self.asm, rule.get("pairs"), include_ghosts=include_ghosts)
            if a in self.meshes and b in self.meshes
        ]

    def rule_interference(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        tol = float(rule.get("tolerance_mm", 0.0))
        why = rule.get("why", "parts must not overlap")
        rows = []
        for a, b in self._pairs(rule):
            rel = self.relation(a, b)
            row = relation_row("interference", a, b, rel, self.quality, why)
            if rel.state == "interference":
                depth = rel.penetration_mm or 0.0
                row["status"] = "FAIL" if depth > tol else "PASS"
            else:
                row["status"] = "PASS"
            rows.append(row)
        return rows

    def rule_clearance(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        min_mm = float(rule.get("min_mm", rule.get("required_mm", 0.0)))
        why = rule.get("why", f"parts must keep >= {min_mm} mm apart")
        rows = []
        bound = self.quality.error_bound_mm()
        for a, b in self._pairs(rule):
            rel = self.relation(a, b)
            row = relation_row("clearance", a, b, rel, self.quality, why)
            row["magnitude"]["required_mm"] = min_mm
            if rel.state != "clear":
                row["status"] = "FAIL"
            elif bound is not None and rel.distance_mm < bound:
                _unresolved(row, bound)
            else:
                row["status"] = "PASS" if rel.distance_mm >= min_mm else "FAIL"
            rows.append(row)
        return rows

    def rule_contact(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        kind = rule.get("kind", "static")
        rows = []
        for a, b in self._pairs(rule):
            rel = self.relation(a, b)
            moving = self._relative_motion(a, b)
            if kind == "sliding" or (kind is None and moving):
                min_gap = float(rule.get("min_gap_mm", 0.0))
                why = rule.get("why", "sliding pair must not touch")
                row = relation_row("contact", a, b, rel, self.quality, why)
                row["kind"] = "sliding"
                row["status"] = (
                    "PASS" if (rel.state == "clear" and rel.distance_mm >= min_gap) else "FAIL"
                )
            else:
                min_area = float(rule.get("min_area_mm2", 0.0))
                why = rule.get("why", "static pair must be in contact")
                row = relation_row("contact", a, b, rel, self.quality, why)
                row["kind"] = "static"
                if rel.state == "contact":
                    row["status"] = "PASS" if (rel.contact_area_mm2 or 0.0) >= min_area else "FAIL"
                elif rel.state == "interference":
                    row["status"] = "FAIL"
                    row["note"] = "parts overlap instead of touching"
                else:
                    row["status"] = "FAIL"
                    row["note"] = f"parts are {rel.distance_mm:.3f} mm apart, not in contact"
            if moving and kind == "static":
                row["note"] = (
                    row.get("note", "") + " (one part has a motion; sliding pair?)"
                ).strip()
            rows.append(row)
        return rows

    def _relative_motion(self, a: str, b: str) -> bool:
        pa, pb = self.asm.part(a), self.asm.part(b)
        return bool(pa.motion) != bool(pb.motion) or (
            bool(pa.motion) and bool(pb.motion) and pa.motion != pb.motion
        )

    def rule_predicate(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self.predicate_runner is None:
            return [
                {
                    "rule": "predicate",
                    "subject": [rule.get("expr")],
                    "status": "UNRESOLVED",
                    "note": "no OpenSCAD evaluator available",
                }
            ]
        expr = str(rule["expr"])
        results = self.predicate_runner([expr])
        r = results[0] if results else {}
        passed = bool(r.get("evaluated")) and r.get("value") is True
        return [
            {
                "rule": "predicate",
                "subject": [expr],
                "status": (
                    "PASS" if passed else ("UNRESOLVED" if not r.get("evaluated") else "FAIL")
                ),
                "value": r.get("value"),
                "tier": "openscad",
                "why": rule.get("why", ""),
            }
        ]

    def rule_probe(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        point = _v3(rule["point"])
        expect = str(rule.get("expect", "SOLID")).upper()
        size = float(rule.get("size_mm", 0.0))
        probe_meshes = {n: m for n, m in self.meshes.items() if not self.asm.part(n).ghost}
        res = geom.classify_point(probe_meshes, point)
        state = res["state"]
        if size > 0 and state == "on_surface":
            # A probe with a size is a small box: count it solid if any corner is
            half = size / 2
            corners = [
                (point[0] + dx, point[1] + dy, point[2] + dz)
                for dx in (-half, half)
                for dy in (-half, half)
                for dz in (-half, half)
            ]
            hits = [geom.classify_point(probe_meshes, c) for c in corners]
            state = "solid" if any(h["state"] == "solid" for h in hits) else "air"
        observed = "SOLID" if state == "solid" else ("AIR" if state == "air" else "SURFACE")
        return [
            {
                "rule": "probe",
                "subject": list(res.get("parts", [])),
                "at": list(point),
                "expected": expect,
                "observed": observed,
                "status": (
                    "PASS"
                    if observed == expect
                    else ("UNRESOLVED" if observed == "SURFACE" else "FAIL")
                ),
                "quality": self.quality.to_dict(),
                "tier": "python",
                "why": rule.get("reason", rule.get("why", "")),
            }
        ]

    def rule_ray(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        origin = _v3(rule["origin"])
        direction = _v3(rule["direction"])
        max_d = rule.get("max_distance_mm")
        # Ghost parts (reference solids, purchased parts) take part in pair
        # checks but not in probes; a ray is a probe.
        ray_meshes = {n: m for n, m in self.meshes.items() if not self.asm.part(n).ghost}
        hits = geom.ray_cast_parts(ray_meshes, origin, direction, max_d)
        first = hits[0] if hits else None
        want = rule.get("first_hit")
        row: Dict[str, Any] = {
            "rule": "ray",
            "subject": [want] if want else [],
            "origin": list(origin),
            "direction": list(direction),
            "first_hit": (
                None
                if first is None
                else {
                    "part": first.part,
                    "distance_mm": round(first.t, 4),
                    "point": _round_vec(first.point),
                }
            ),
            "crossings": [
                {"part": h.part, "distance_mm": round(h.t, 4), "entering": h.entering}
                for h in hits[:12]
            ],
            "quality": self.quality.to_dict(),
            "tier": "python",
            "why": rule.get("why", ""),
        }
        if want is None:
            row["status"] = "PASS" if first is None else "FAIL"
        else:
            row["status"] = "PASS" if (first is not None and first.part == want) else "FAIL"
        return [row]

    def rule_sweep(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        moving = str(rule["moving"])
        part = self.asm.part(moving)
        motion = dict(part.motion or {})
        motion.update(
            {
                k: v
                for k, v in rule.items()
                if k
                in ("axis", "center", "vector", "range", "range_deg", "range_mm", "steps", "type")
            }
        )
        against_spec = rule.get("against", "all")
        against = {
            n: m
            for n, m in self.meshes.items()
            if n != moving and (against_spec == "all" or n in against_spec)
        }
        steps = int(motion.get("steps", rule.get("steps", 36)))
        mesh = self.meshes[moving]
        kind = motion.get("type", "rotate" if "axis" in motion else "translate")
        if kind == "rotate":
            axis = _v3(motion.get("axis", (0, 0, 1)))
            center = _v3(motion.get("center", (0, 0, 0)))
            rng = motion.get("range_deg") or motion.get("range") or (0, 360)
            steps_out = geom.sweep_rotation(
                mesh, axis, center, (float(rng[0]), float(rng[1])), steps, against
            )
            full_turn = abs(float(rng[1]) - float(rng[0])) >= 360 - 1e-9
            certificate = None
            if full_turn:
                # One footprint for the moving part, reused against every
                # static part; the certificate is a proof, not a sample.
                fp = geom.rz_footprint(mesh, axis, center)
                certificate = {}
                for n, m in against.items():
                    touch, gap = geom.can_ever_touch(fp, geom.rz_footprint(m, axis, center))
                    certificate[n] = {"can_ever_touch": touch, "min_gap_mm": round(gap, 4)}
        else:
            vector = _v3(motion.get("vector", motion.get("axis", (0, 0, 1))))
            rng = motion.get("range_mm") or motion.get("range") or (0, 10)
            steps_out = geom.sweep_translation(
                mesh, vector, (float(rng[0]), float(rng[1])), steps, against
            )
            certificate = None
        worst = None
        for st in steps_out:
            if st.get("max_penetration_mm", 0) > 0 and (
                worst is None or st["max_penetration_mm"] > worst["max_penetration_mm"]
            ):
                worst = st
        min_gap = min((st.get("min_gap_mm", math.inf) for st in steps_out), default=math.inf)
        first_contact = next((st for st in steps_out if st.get("contacts")), None)
        row: Dict[str, Any] = {
            "rule": "sweep",
            "subject": [moving],
            "motion": kind,
            "steps": len(steps_out),
            "first_contact": (
                None
                if first_contact is None
                else {
                    k: first_contact[k]
                    for k in first_contact
                    if k in ("angle_deg", "offset_mm", "contacts")
                }
            ),
            "worst": (
                None
                if worst is None
                else {
                    k: worst[k]
                    for k in worst
                    if k in ("angle_deg", "offset_mm", "max_penetration_mm", "worst_part")
                }
            ),
            "min_gap_mm": None if min_gap == math.inf else round(min_gap, 4),
            "quality": self.quality.to_dict(),
            "tier": "python",
            "why": rule.get("why", ""),
        }
        if certificate is not None:
            row["all_angles"] = certificate
        row["status"] = "PASS" if worst is None else "FAIL"
        return [row]

    def rule_alignment(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self.feature_provider is None:
            return [
                {
                    "rule": "alignment",
                    "subject": [],
                    "status": "UNRESOLVED",
                    "note": "no feature provider (CSG dump) available",
                }
            ]
        result = self.feature_provider()
        tol = float(rule.get("tolerance_mm", 0.2))
        rows = []
        for mis in result.get("misaligned", []):
            rows.append(
                {
                    "rule": "alignment",
                    "subject": [mis["a"].get("part"), mis["b"].get("part")],
                    "status": "FAIL" if mis["offset_mm"] > tol else "PASS",
                    "magnitude": {"offset_mm": round(mis["offset_mm"], 4)},
                    "reading": mis.get("reading"),
                    "tier": "python",
                    "why": rule.get("why", "holes must be coaxial"),
                }
            )
        # Orphans are informational: one row per part with a count, not one
        # row per hole, or a plate with sixteen tapped holes drowns the report.
        by_part: Dict[str, List[Dict[str, Any]]] = {}
        for orphan in result.get("orphans", []):
            by_part.setdefault(str(orphan.get("part")), []).append(orphan)
        for part_name, orphans in by_part.items():
            diameters = sorted({round(float(o.get("nominal_d_mm", 0.0)), 2) for o in orphans})
            rows.append(
                {
                    "rule": "alignment",
                    "subject": [part_name],
                    "status": "PASS",
                    "state": "orphan",
                    "magnitude": {"count": len(orphans)},
                    "note": (
                        f"{len(orphans)} subtractive feature(s) with no coaxial partner in "
                        f"another part (diameters {diameters}); expected for holes that mate "
                        "with purchased parts not in the assembly"
                    ),
                }
            )
        if not rows:
            rows.append(
                {
                    "rule": "alignment",
                    "subject": [],
                    "status": "PASS",
                    "axes": len(result.get("axes", [])),
                    "note": "no misaligned coaxial features",
                }
            )
        return rows

    def rule_print(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        if self.printability_provider is None:
            return [
                {
                    "rule": "print",
                    "subject": [rule.get("part")],
                    "status": "UNRESOLVED",
                    "note": "no printability analyser available",
                }
            ]
        name = str(rule["part"])
        facts = self.printability_provider(name, rule)
        rows: List[Dict[str, Any]] = []
        max_over = rule.get("max_overhang_area_mm2")
        if max_over is not None:
            area = facts.get("overhang", {}).get("area_mm2", 0.0)
            rows.append(
                {
                    "rule": "print",
                    "subject": [name],
                    "check": "overhang_area",
                    "magnitude": {"area_mm2": area, "max_mm2": max_over},
                    "status": "PASS" if area <= max_over else "FAIL",
                }
            )
        min_feat = rule.get("min_feature_mm")
        if min_feat is not None:
            thin = facts.get("thickness", {}).get("area_below_nozzle_mm2")
            observed_min = facts.get("thickness", {}).get("min")
            status = (
                "UNRESOLVED"
                if observed_min is None
                else ("PASS" if (thin or 0.0) <= 0.0 else "FAIL")
            )
            rows.append(
                {
                    "rule": "print",
                    "subject": [name],
                    "check": "min_feature",
                    "magnitude": {
                        "min_mm": observed_min,
                        "area_below_mm2": thin,
                        "required_mm": min_feat,
                    },
                    "at": facts.get("thickness", {}).get("min_location"),
                    "status": status,
                }
            )
        reach = rule.get("max_unsupported_reach_mm")
        if reach is not None:
            worst = max(
                (
                    p.get("max_unsupported_reach_mm", 0.0)
                    for p in facts.get("overhang", {}).get("patches", [])
                ),
                default=0.0,
            )
            rows.append(
                {
                    "rule": "print",
                    "subject": [name],
                    "check": "unsupported_reach",
                    "magnitude": {"max_unsupported_reach_mm": worst, "limit_mm": reach},
                    "status": "PASS" if worst <= reach else "FAIL",
                }
            )
        if not rows:
            rows.append({"rule": "print", "subject": [name], "status": "PASS", "facts": facts})
        return rows

    def rule_mass(self, rule: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "rule": "mass",
                "subject": [],
                "status": "UNRESOLVED",
                "note": "mass rules are evaluated by measure(mode=mass); not part of check",
            }
        ]


def exit_code(rows: List[Dict[str, Any]]) -> int:
    """0 all pass, 1 any FAIL, 2 unresolved only."""
    if any(r.get("status") == "FAIL" for r in rows):
        return 1
    if any(r.get("status") == "UNRESOLVED" for r in rows):
        return 2
    return 0


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"pass": 0, "fail": 0, "unresolved": 0}
    for r in rows:
        key = {"PASS": "pass", "FAIL": "fail"}.get(str(r.get("status")), "unresolved")
        out[key] += 1
    return out
