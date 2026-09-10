# OpenSCAD MCP Server — API Reference

Twelve tools, four resources, one CLI subcommand. Everything below was read
from the running server's tool schemas and verified by calling the tools
against OpenSCAD 2021.01 with BOSL2 installed.

## Contents

- [Overview](#overview)
- [Response conventions](#response-conventions)
- [Tools](#tools)
  - [render](#render) · [measure](#measure) · [check](#check) · [validate](#validate)
  - [scad_eval](#scad_eval) · [reference](#reference) · [export_model](#export_model)
  - [model](#model) · [get_project_files](#get_project_files)
  - [get_libraries](#get_libraries) · [check_openscad](#check_openscad) · [clear_cache](#clear_cache)
- [Check file format](#check-file-format)
- [Resources and server instructions](#resources-and-server-instructions)
- [Configuration](#configuration)
- [Error handling](#error-handling)

## Overview

### Running the server

```bash
uvx openscad-mcp            # stdio transport (default)
MCP_TRANSPORT=http MCP_PORT=8000 uvx openscad-mcp
```

Transports are `stdio`, `http` and `sse`, selected with `MCP_TRANSPORT`. HTTP
and SSE bind to `MCP_HOST` and `MCP_PORT`.

### The `check` CLI

The same rule engine runs outside MCP, for CI:

```bash
openscad-mcp check <check_file> [--model FILE] [--fn N] [--json] [--allow DIR]
```

| Exit code | Meaning |
|---|---|
| 0 | every rule passed |
| 1 | at least one rule FAILed |
| 2 | no failures, but at least one rule was UNRESOLVED |
| 3 | the run itself failed (bad check file, missing model, OpenSCAD error) |

`--allow` is repeatable and sets `security.allowed_paths` for the run.

Example output:

```
PASS       interference post,arm       clear   distance_mm=0.5 arm must clear the post
PASS       clearance    post,arm       clear   distance_mm=0.5 required_mm=0.3
FAIL       ray          post
pass 9  fail 1  unresolved 0  (0.387 s, cache hits 0)
```

### The twelve tools

| Tool | What it answers |
|---|---|
| `render` | what does it look like (images plus a spatial digest) |
| `measure` | exact numbers from the geometry |
| `check` | how named parts relate to each other |
| `validate` | is the model correct, buildable, printable |
| `scad_eval` | what does this expression evaluate to |
| `reference` | sourced engineering data (fits, fasteners, parts) |
| `export_model` | write a mesh, 2D or CSG file |
| `model` | CRUD over `.scad` files in a workspace |
| `get_project_files` | files and constant dependency graph in a directory |
| `get_libraries` | which OpenSCAD libraries are installed |
| `check_openscad` | is OpenSCAD installed, which version, what can it do |
| `clear_cache` | drop cached renders and part meshes |

## Response conventions

**`success` is derived from diagnostics, never from the exit code.** OpenSCAD
exits 0 after a failed `assert()` or an unknown module, and draws a blank
scene. Every tool parses stderr into structured records and sets `success`
(and `valid` in `validate`) from whether an ERROR record was seen.

**Diagnostic fields.** Most responses carry some of:

| Field | Meaning |
|---|---|
| `errors` | ERROR-severity messages, as strings |
| `warnings` | WARNING-severity messages |
| `deprecated` | deprecation notices |
| `echo_output` | `echo()` output, truncated with `echo_truncated: true` when long |
| `hints` | `[{code, hint}]`, one per detected failure class, with repair advice |
| `records` | full `{severity, message, file, line, trace}` objects (`validate(mode="syntax")` only) |

**Units are millimetres.** Angles are degrees. Z is up, right-handed.

**`frame`** is `"local"` when coordinates are in the model's own frame, and
`"assembly"` when `parts=` was given, because each part is then evaluated with
its `place`/`frame` transform applied. `check` is always `"assembly"`.

**Quality and UNRESOLVED.** `quality` accepts `draft`, `normal`, `high`, an
integer `$fn`, or `{fn, fa, fs}` in `measure`, `check` and `export_model`.
`render` accepts only the three preset names.

| Preset | Variables set |
|---|---|
| `draft` | `$fn=8, $fa=12, $fs=2` |
| `normal` | none (OpenSCAD defaults) |
| `high` | `$fn=64, $fa=2, $fs=0.5` |

`check` echoes `quality: {fn, curved_features, error_bound_mm}` on the result
and on every geometric row. The reported `fn` is what the caller passed, so it
is `null` when the model sets `$fn` in its own source. The bound is the inscribed-polygon error of the
largest curved radius among the parts. A distance smaller than that bound is
reported as `status: "UNRESOLVED"` with a note, not as a number.

**`cached`.** `render` reports `cached: true` when the PNG came from the render
cache. `check` and `export_model(parts=...)` report per-part cache hits under
`cache: {parts, hits, misses}` or `objects[].cached`. Cache keys are a SHA-256
of every render parameter plus a manifest of the dependency files.

**Images.** `render` returns an interleaved list: a text digest, then the
image, repeated per view, then one JSON metadata object. The digest always
precedes the image it describes and states the view direction, camera eye and
centre, projection, scale and bounding box. `image_tokens` in the metadata is
the estimated vision-token cost, `ceil(w/28) * ceil(h/28)` per image. The
default 800x600 is about 638 tokens.

## Tools

### render

Images of a model. Returns a list, not an object: alternating digest strings
and images, with a JSON metadata object last.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `scad_content` | string | null | source text; exactly one of this or `scad_file` |
| `scad_file` | string | null | path to a `.scad` file |
| `mode` | string | `"views"` | `views`, `section`, `parts`, `compare` |
| `views` | string[] | `["isometric"]` | any of front, back, left, right, top, bottom, isometric, dimetric |
| `camera_position` | string \| number[] \| object | null | custom eye, used when `views` is omitted |
| `camera_target` | string \| number[] \| object | null | custom look-at point |
| `camera_up` | string \| number[] \| object | null | custom up vector |
| `image_size` | string \| int[] | `[800, 600]` | clamped to `MCP_MAX_IMAGE_*`, aspect preserved |
| `color_scheme` | string | `"Cornfield"` | OpenSCAD colour scheme |
| `variables` | object | null | `-D` overrides |
| `quality` | preset \| int \| object | null | `draft`/`normal`/`high`, an integer `$fn`, or `{fn, fa, fs}` |
| `include_paths` | string[] | null | extra `OPENSCADPATH` entries |
| `grounded` | bool | false | orthographic view with an exact mm/px scale |
| `annotate` | bool | false | scale bar, axis triad, bbox size; implies `grounded` in `views` |
| `section_axis` | string | `"z"` | cut axis for `mode="section"` |
| `section_offset` | number \| string | `0.0` | mm, or an expression evaluated in the model's scope |
| `parts` | object[] | null | `[{name, code, place?, color?, ghost?, explode?}]` |
| `isolate` | string | null | render this part solid, ghost the rest |
| `look_at` | string \| number[] \| object | null | part name, point, or `{min, max}`; needs `grounded` |
| `callouts` | object[] | null | `[{label, at}]`; needs `grounded` |
| `variables_after` | object | null | `mode="compare"` right-hand side |
| `scad_content_after` | string | null | `mode="compare"` alternative source |

Camera and size parameters accept JSON strings, lists, dicts and CSV. That
flexibility is deliberate.

Metadata keys by mode: `views` adds `views`, `failed_views`, `bbox`; `section`
adds `section_offset`, `contours`, `empty_section`; `parts` adds `parts` (with
per-part colour, ghost flag and bbox) and `frame`; `compare` adds `before`,
`after`, `view`. All modes carry `mode`, `image_size`, `image_tokens` and
`success`.

Worked call, `render(scad_file="bracket.scad", views=["isometric"], grounded=True, annotate=True)`:

```
View: isometric
view: isometric | projection: orthographic | units: mm | Z up, right-handed
camera eye=(100.39, 92.39, 86.39) center=(20, 12, 6) up=(0, 0, 1) distance=139.24
looking from +X+Y+Z toward the center (isometric), +Z up
scale: 0.09232 mm/px (image 800x600 => 73.9 x 55.4 mm visible)
bbox: [0,0,0]..[40,24,12]  size 40 x 24 x 12 mm  center (20, 12, 6)
longest bbox edge is about 433 px in this image
<PNG image>
{"mode": "views", "image_size": [800, 600], "errors": [], "warnings": [],
 "echo_output": [], "cached": false, "views": ["isometric"],
 "bbox": {"min": [0,0,0], "max": [40,24,12]}, "image_tokens": 638, "success": true}
```

Without `grounded`, the digest says `scale: unknown (auto-fit)`. Auto-fit views
carry no absolute scale; use `measure` for sizes.

### measure

Exact numbers from the geometry. Prefer this over judging a picture.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `scad_content` / `scad_file` | string | null | exactly one, unless `mesh` is given |
| `mode` | string | `"model"` | see the mode table below |
| `variables` | object | null | `-D` overrides |
| `include_paths` | string[] | null | extra library paths |
| `parts` | object[] | null | `[{name, code, place?, material?, mass_g?}]`; switches coordinates to the assembly frame. The schema types these values as strings, so pass `mass_g` as `"5"` |
| `section_axis` | string | `"z"` | cut axis |
| `section_offset` | number \| string | `0.0` | mm or an expression |
| `material` | string | null | one of PLA, PETG, ABS, ASA, TPU, Nylon, PC, resin, aluminum, steel, brass |
| `density_g_cm3` | number | null | overrides `material` |
| `mesh` | string | null | measure an existing STL or SVG instead of source |
| `part` | string | null | a single `"module();"` for `anchors`/`printability` |
| `points` | array | null | probe points, or extra anchor names in `anchors` |
| `rays` | array | null | `[[ox,oy,oz,dx,dy,dz,max?]]` or `[{origin, direction, max_distance}]` |
| `polyline` | number[][] | null | line-of-sight test through the assembly |
| `orientation` | any | null | print orientation for `printability` |
| `about_axis` | number[][] | null | `[[point],[direction]]` for the mass moment of inertia |
| `nozzle_mm` | number | `0.4` | thin-feature threshold |
| `layer_height_mm` | number | null | enables island detection |
| `quality` | preset \| int \| object | null | tessellation |
| `response_format` | string | `"concise"` | `detailed` widens caps and adds fields |

| Mode | Returns |
|---|---|
| `model` | bbox, dimensions, centre, volume, surface area, solid/cavity/component counts, watertight and manifold flags, per-component stats, `mesh_health` |
| `parts` | the same per part, plus `assembly_bbox` and `bbox_overlaps` |
| `section` | `plane`, `in_plane_axes`, area, perimeter, `polygon_count`, `hole_count`, `contours` |
| `mass` | model stats plus `mass: {material, density_g_cm3, grams, note}`; with `parts=` the composed assembly mass |
| `probe` | `points[].state` (solid/air/on_surface) and which part, `rays[].first_hit` and `crossings`, `polyline` |
| `features` | circular subtractive features from the CSG tree: diameter, depth, axis, `undersize_mm` at the current `$fn`, `fit_candidates` |
| `printability` | overhang patches, thickness distribution, bed contact, support estimate; facts only |
| `orientation` | candidate orientations sorted by overhang then height, with `rejected_reason`; no winner is chosen |
| `anchors` | BOSL2 anchor names with `local` and `assembly` positions and direction |

2D models report `area` and `perimeter` instead of `volume`.

Worked call, `measure(scad_file="bracket.scad", mode="model")`:

```json
{"success": true, "mode": "model", "units": "mm",
 "triangle_count": 208, "vertex_count": 104,
 "bbox_min": [0,0,0], "bbox_max": [40,24,12],
 "dimensions": [40,24,12], "center": [20,12,6],
 "volume": 11265.8784, "surface_area": 3609.5429,
 "solid_count": 1, "cavity_count": 0, "component_count": 1,
 "open_edge_count": 0, "non_manifold_edge_count": 0,
 "is_watertight": true, "is_manifold": true, "degenerate_triangle_count": 0,
 "mesh_health": {"manifold": true, "vertices": 104, "edges": 156,
                 "facets": 54, "nef_volumes": 2},
 "errors": [], "warnings": [], "echo_output": []}
```

`mesh_health.manifold` is `true`, `false`, or `null` when CGAL did not run the
check. `nef_volumes` counts the unbounded outer volume and every cavity, so it
is not a body count.

`measure(mode="features")` on the same bracket:

```json
{"patterns": [{"count": 1, "polarity": "subtractive", "d_mm": 5.2,
   "length_mm": 12.02, "axis_dir": [0,0,-1], "entries": [[20,12,12.01]],
   "segments": 48, "effective_min_d_mm": 5.1889, "undersize_mm": 0.0111,
   "fit_candidates": [{"match": "M5 close clearance", "nominal_mm": 5.3,
                       "delta_mm": -0.1, "role": "clearance"}],
   "description": "1x D5.20 12.0 mm deep from z=+12.0 along -Z"}],
 "frame": "local"}
```

`measure(mode="mass", parts=[...], about_axis=[[0,0,0],[0,0,1]])` composes the
assembly and adds `total_mass_g`, `total_volume_mm3`, `center_of_mass`,
`inertia_about_com_g_mm2`, `principal_moments_g_mm2`, `principal_axes`,
`parts[].mass_fraction`, `inertia_about_axis_g_mm2` and
`inertia_about_axis_kg_m2`. A part with `mass_g` set is a purchased part and
its density is ignored.

### check

Relations between named parts. Parts are exported separately and never
unioned, because a CGAL union destroys part identity. Every result is in the
assembly frame.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `scad_content` / `scad_file` | string | null | the model defining the part modules |
| `check_file` | string | null | path to a YAML or JSON check file |
| `mode` | string | `"interference"` | `interference`, `clearance`, `contact`, `alignment`, `motion`, `rules` |
| `parts` | object[] | null | `[{name, code, place?, frame?, ghost?, mass_g?, motion?}]` |
| `frames` | object | null | `{name: {parent, lift}}` |
| `pairs` | `"all"` \| `[[a,b]]` | `"all"` | which part pairs to evaluate |
| `tolerance_mm` | number | `0.0` | allowed penetration; also the alignment tolerance (default 0.2 there) |
| `min_mm` | number | null | required clearance, or contact `min_area_mm2` / sliding `min_gap_mm` |
| `kind` | string | null | `static` or `sliding` for `mode="contact"` |
| `moving` | string | null | the moving part for `mode="motion"` |
| `axis` / `center` | number[] | null | rotation axis and centre |
| `vector` | number[] | null | translation direction; its presence selects a linear sweep |
| `range` | number[] | null | `[start, end]` in degrees or mm |
| `steps` | int | `36` | sweep samples |
| `against` | `"all"` \| string[] | `"all"` | which parts the sweep is tested against |
| `checks` | object[] | null | inline rules, same grammar as the check file |
| `quality` | preset \| int \| object | null | tessellation for every part |
| `variables` | object | null | `-D` overrides |
| `include_paths` | string[] | null | extra library paths |
| `volume` | bool | false | cross-check overlaps with OpenSCAD's intersection volume |
| `response_format` | string | `"concise"` | `detailed` keeps `closest` points and normals on passing rows |

Result shape, identical across modes:

```json
{"success": true, "mode": "rules", "units": "mm", "frame": "assembly",
 "parts": ["post", "arm", "pin (ghost)"],
 "quality": {"fn": 64, "curved_features": true, "error_bound_mm": 0.0072},
 "cache": {"parts": 3, "hits": 0, "misses": 3},
 "pairs_evaluated": 1, "pairs_aabb_separated": 1,
 "findings": [ ...rows... ],
 "summary": {"pass": 9, "fail": 1, "unresolved": 0},
 "exit_code": 1, "timing_s": 0.371,
 "timings": {"export_s": 0.232, "load_s": 0.002, "features_s": 0.044, "rules_s": 0.083}}
```

`empty_parts` lists parts that produced no geometry; each also gets an
`UNRESOLVED` finding with `state: "empty"`, so a check cannot pass green while
having tested nothing (exit code 2 unless another rule fails). `warnings`
carries up to twenty OpenSCAD warnings from the part exports.

The four relational modes classify a pair on one ladder. Flush face contact is
`contact`, never `interference`.

| State | Row fields |
|---|---|
| `clear` | `distance_mm`, witness point in `at` |
| `contact` | `contact_area_mm2`, `normal`, `plane` (e.g. `"z = 6.000"`) |
| `interference` | `penetration_mm`, `at`, and `intersection_volume_mm3` when `volume=true` |

Worked call, `check(scad_file="asm.scad", mode="contact", kind="static", parts=[{"name":"base","code":"base();"},{"name":"lid","code":"lid();","place":"translate([0,0,6])"}])`:

```json
{"findings": [{"rule": "contact", "subject": ["base", "lid"], "state": "contact",
  "magnitude": {"distance_mm": 0.0, "contact_area_mm2": 900.0},
  "at": [15, 15, 6], "normal": [0, 0, 1], "plane": "z = 6.000",
  "kind": "static", "status": "PASS", "tier": "python",
  "why": "static pair must be in contact"}],
 "summary": {"pass": 1, "fail": 0, "unresolved": 0}, "exit_code": 0}
```

`mode="motion"` sweeps `moving` and returns one `sweep` row with `first_contact`,
`worst`, `min_gap_mm` and `steps`. A full 360-degree rotation adds `all_angles`,
a certificate computed from the rotational footprints rather than from samples:

```json
{"rule": "sweep", "subject": ["arm"], "motion": "rotate", "steps": 24,
 "first_contact": null, "worst": null, "min_gap_mm": 0.5,
 "all_angles": {"post": {"can_ever_touch": false, "min_gap_mm": 0.25}},
 "status": "PASS"}
```

`mode="alignment"` reads the CSG tree, groups coaxial subtractive features
across parts and reports `offset_mm` per misaligned stack. Holes with no
partner in another part are collapsed into one informational `state: "orphan"`
row per part, since a plate with sixteen tapped holes would otherwise drown the
report.

### validate

Is the model correct. `valid` is false whenever an ERROR was reported,
whatever OpenSCAD's exit code was.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `scad_content` / `scad_file` | string | null | exactly one |
| `mode` | string | `"syntax"` | `syntax`, `geometry`, `predicates`, `includes`, `printability` |
| `variables` | object | null | `-D` overrides |
| `include_paths` | string[] | null | extra library paths |
| `predicates` | string[] | null | boolean expressions, each must be true |
| `sweep` | object | null | `{variable, values: [...]}` re-runs the predicates per value |
| `autofix` | bool | false | apply safe lint fixes to the file in `mode="includes"` |
| `orientation` | any | null | print orientation for `mode="printability"` |
| `profile` | object | null | `{overhang_deg, nozzle_mm, max_unsupported_reach_mm, min_wall_mm}` |

**`syntax`** parses and evaluates without producing geometry. Fast. Returns
`valid`, the diagnostic fields including full `records` with file and line, and
`unresolved_includes` when an `include`/`use` target could not be opened.

**`geometry`** exports the mesh and reports `findings: [{code, detail}]` with
codes `non_manifold`, `open_edges`, `non_manifold_edges`, `multiple_solids`,
`cavities`, `degenerate_triangles`, `openscad_error`, plus `mesh_health` and a
`summary` of the mesh statistics.

**`predicates`** evaluates each expression in the model's own scope:

```json
{"success": true, "mode": "predicates", "valid": false,
 "results": [{"predicate": "W > 30", "pass": true, "value": true, "type": "bool"},
             {"predicate": "hole_d < wall", "pass": false, "value": false, "type": "bool"}]}
```

With `sweep={"variable": "W", "values": [20, 30, 40, 50]}` it adds:

```json
{"sweep": {"variable": "W",
  "points": [{"value": 20, "results": [false], "all_pass": false},
             {"value": 40, "results": [true], "all_pass": true}],
  "first_failure": 20, "crossing": {"between": [30, 40], "from_pass": false},
  "monotonic": true}}
```

**`includes`** resolves every `include`/`use`/`import`/`surface` reference to a
path and runs the BOSL2 shadowing lint: a module from a `use<>`d file placed by
`attach()` or `position()` is silently put at CENTER. Returns `references`
(`[{reference, resolved_path, found}]`), `lint`, `files_read` and
`search_paths`. Findings carry a fix plan, applied when `autofix=true` and the
fix is safe. The lint only runs on `scad_file`, not on `scad_content`.

**`printability`** applies the rules from `reference(topic="dfm")` to the facts
from `measure(mode="printability")`:

```json
{"success": true, "mode": "printability", "valid": true, "findings": [],
 "thresholds": {"overhang_deg": 45.0, "max_unsupported_reach_mm": 20.0,
                "min_wall_mm": 0.8, "nozzle_mm": 0.4},
 "thresholds_source": "Community FDM practice; ...",
 "orientation": {"input": null, "form": "as-modelled",
                 "rotate": {"a": 0.0, "v": [0,0,1]}, "down": [0,0,-1],
                 "drop_to_bed_mm": 0.0},
 "bed_contact_area_mm2": 938.8232, "overhang_area_mm2": 0.0}
```

### scad_eval

Evaluate expressions and return typed values. No geometry is evaluated.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `expressions` | string[] | required | the expressions |
| `scad_content` / `scad_file` | string | null | optional; gives the expressions that model's scope |
| `variables` | object | null | `-D` overrides |
| `include_paths` | string[] | null | extra library paths |

Types are `number`, `vector`, `string`, `bool`, `range`, `undef`. Numbers carry
OpenSCAD's six significant digits.

```json
{"success": true,
 "results": [{"index": 0, "value": 6, "type": "number", "evaluated": true, "expression": "wall*2"},
             {"index": 1, "value": 34.8, "type": "number", "evaluated": true, "expression": "W - hole_d"}],
 "errors": [], "warnings": []}
```

`evaluated: false` means the expression itself failed; `value` is then unusable.

### reference

Sourced engineering data. Every entry carries a `confidence` label
(`standard`, `consensus`, `calibrate`) and a `source` string.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `topic` | string | `"conventions"` | see below; `"list"` enumerates topics |
| `query` | string | null | substring filter over entries |
| `detailed` | bool | false | full entries rather than the summary fields |
| `diameter_mm` | number | null | with `topic="fits"`, names what that hole is |
| `shaft_mm` + `bore_mm` | number | null | with `topic="fits"`, names the fit class |

Topics: `fits`, `fasteners`, `inserts`, `bearings`, `magnets`, `joints`,
`parts`, `conventions`, `cheatsheet`, `dfm`, `materials`.

A topic lookup returns `{topic, query, entries: [...], success}`. The `parts`
topic is the purchased-part catalog: envelope, mount pattern, shaft, mass,
named anchors, and the names of a BOSL2 solid module, a clearance mask and a
mount-holes mask. Each entry also carries a `verify` list naming the dimensions
that are not on any manufacturer drawing. Write a catalog part into a workspace
with `model(action="create", template="part:<id>")`.

`reference(topic="fits", shaft_mm=5.0, bore_mm=5.3)`:

```json
{"diametral_mm": 0.3, "per_side_mm": 0.15, "fit": "close running fit (slip)",
 "interference": false, "in_table": true,
 "alternatives": ["free running fit (loose)", "screw clearance fit"],
 "confidence": "consensus", "source": "...", "topic": "fits", "success": true}
```

`reference(topic="fits", diameter_mm=3.3)` returns `matches`, the top three
named holes with `delta_mm` from 3.3. The first is an M4 tap drill at 0.0.

### export_model

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `scad_content` / `scad_file` | string | null | exactly one |
| `output_format` | string | `"stl"` | `stl`, `3mf`, `amf`, `off`, `nef3` (3D); `dxf`, `svg`, `pdf` (2D); `csg` |
| `output_path` | string | null | destination; a temp directory is used when omitted |
| `variables` | object | null | `-D` overrides |
| `include_paths` | string[] | null | extra library paths |
| `parts` | object[] | null | `[{name, code, place?}]`, exports each part separately |
| `quality` | preset \| int \| object | null | tessellation |

The accepted format set is narrowed to what the detected binary supports; AMF
is gone from dev snapshots after 2025. Single-file export:

```json
{"success": true, "output_path": "/tmp/.../bracket.stl", "format": "stl",
 "file_size_bytes": 31282,
 "mesh_health": {"manifold": true, "vertices": 104, "edges": 156,
                 "facets": 54, "nef_volumes": 2},
 "errors": [], "warnings": []}
```

With `parts=`, `output_format="3mf"` bundles every part into one 3MF with named,
coloured objects; `output_format="stl"` writes a directory of per-part STLs. No
other format is accepted with `parts=`.

```json
{"success": true, "format": "3mf", "output_path": "/tmp/.../asm.3mf",
 "objects": [{"name": "base", "cached": true, "color": "#332288",
              "ghost": false, "frame": "assembly"},
             {"name": "lid", "cached": true, "color": "#D55E00",
              "ghost": false, "frame": "assembly"}],
 "object_count": 2, "triangle_count": 24, "file_size_bytes": 1181,
 "frame": "assembly"}
```

A non-manifold mesh usually means parts touch along an edge or a face. Overlap
them by an epsilon instead.

### model

Manage `.scad` files in a workspace.

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `action` | string | required | `create`, `get`, `update`, `list`, `delete` |
| `name` | string | null | file name; `.scad` is appended if missing |
| `content` | string | null | source for `create`/`update` |
| `workspace` | string | null | directory; defaults to the server temp models directory |
| `template` | string | null | `"part:<catalog id>"`, only with `action="create"` |

Names are restricted to alphanumerics, hyphens and underscores; no path
traversal. When `allowed_paths` is configured the workspace must lie inside it.

Every create, update and get returns an `etag`, the first 16 hex digits of the
content's SHA-256, so a later update can be checked against the version last
read. `create` refuses to overwrite; `update` refuses to create.

```json
{"success": true, "path": "/tmp/ws/demo.scad", "name": "demo.scad",
 "etag": "a07e6cec5cad312f"}
```

`action="list"` returns `{workspace, models: [{name, path, size_bytes, modified}], count}`.

With a template, the response also carries `template`, `modules`, `anchors`,
`envelope_mm` and `verify` from the catalog:

```json
{"success": true, "path": "/tmp/ws/stepper.scad", "name": "stepper.scad",
 "etag": "23e6f976e1f21c45", "template": "28byj-48",
 "modules": {"solid": "part_28byj48", "mask": "part_28byj48_mask",
             "mount_holes_mask": "part_28byj48_mount_holes_mask"},
 "anchors": {"mount-plane": [1.5, 0, 4.5], "shaft-axis": [9.5, 0, 4.5],
             "shaft-tip": [9.5, 0, 14.5], "hole-a": [1.5, 17.5, 4.5]},
 "envelope_mm": [31.0, 42.0, 29.0],
 "verify": ["Tab plate thickness. Modelled at 1.0 mm...", "..."]}
```

### get_project_files

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `project_dir` | string | required | must be inside `allowed_paths` when configured |
| `mode` | string | `"files"` | `files` or `trace` |
| `symbol` | string | null | the constant to trace |
| `direction` | string | `"downstream"` | `downstream` (what depends on it) or `upstream` |

`mode="files"` walks `*.scad` recursively:

```json
{"success": true,
 "files": [{"name": "lid.scad", "path": "/p/lid.scad", "relative_path": "lid.scad",
            "size_bytes": 78, "modified": 1789018002.6}],
 "dependencies": {"lid.scad": ["consts.scad"]}}
```

`mode="trace"` follows a file-scope constant across the project:

```json
{"success": true, "mode": "trace", "symbol": "WALL", "direction": "downstream",
 "definition": {"file": "/p/consts.scad", "line": 1, "expression": "3"},
 "downstream": [{"name": "LID_T", "depth": 1, "expression": "WALL * 2",
                 "file": "/p/lid.scad", "line": 2}],
 "upstream": [],
 "files": [{"file": "/p/lid.scad", "names": ["LID_T", "WALL"],
            "lines": [2, 3], "defines": true}],
 "constants_parsed": 3,
 "scope": "lexical, file-scope constants only"}
```

The trace is lexical. Module-local variables, parameters and conditional
redefinition are not modelled.

### get_libraries

No parameters. Scans the platform's standard OpenSCAD library directories plus
`OPENSCADPATH`. Read-only, and does not need OpenSCAD installed.

```json
{"success": true,
 "library_paths": ["/home/me/.local/share/OpenSCAD/libraries",
                   "/usr/share/openscad/libraries"],
 "libraries": [{"name": "BOSL2", "path": ".../BOSL2", "file_count": 97,
                "has_readme": true, "main_files": ["std.scad"]},
               {"name": "MCAD", "path": ".../MCAD", "file_count": 38,
                "has_readme": false, "main_files": []}]}
```

### check_openscad

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `include_paths` | bool | false | also return `searched_paths` or `library_paths` |

```json
{"installed": true, "version": "2021.01", "path": "openscad",
 "is_snapshot": false,
 "capabilities": {"has_manifold_backend": false, "has_summary_json": false,
                  "has_egl_headless": false, "amf_export": true},
 "supported_export_formats": ["3mf", "amf", "csg", "dxf", "nef3", "off",
                              "pdf", "stl", "svg"],
 "message": "OpenSCAD 2021.01 is installed at openscad",
 "upgrade_hint": "OpenSCAD 2021.01 is the last stable release; ..."}
```

When not installed, `installed` is false and `message` explains how to install
or set `OPENSCAD_PATH`. `success` is true in both cases; it reports whether the
check ran, not whether OpenSCAD was found.

### clear_cache

No parameters. Deletes cached PNGs, their dependency manifests, and the cached
per-part STL/CSG meshes.

```json
{"success": true, "cleared_files": 62, "freed_bytes": 5720040}
```

`cleared_files` counts every file deleted (images, dependency manifests, part
meshes and CSG dumps) and `freed_bytes` their total size. Reports success when
the cache is disabled or absent.

## Check file format

YAML or JSON, passed as `check_file=` to the `check` tool or as the argument to
the `openscad-mcp check` CLI. Every key is optional except `parts`.

```yaml
version: 1                    # grammar version, default 1
model: hinge.scad             # .scad defining the modules; --model / scad_file overrides
quality: {fn: 64}             # fn, fa, fs applied to every part export
variables: {wall: 3}          # -D overrides

frames:                       # named coordinate frames, composed root-first
  arm_frame:
    parent: world             # default is "world"; cycles are rejected
    lift: "translate([0,0,10.5])"   # a transform expression, applied in the parent

parts:
  - name: post                # must match ^[A-Za-z_][A-Za-z0-9_]*$
    code: "post();"           # a plain module call; no include/use/braces/semicolons inside
  - name: arm
    code: "arm();"
    place: "translate([0,20,0])"    # a transform expression applied after the frame
    frame: arm_frame
    material: PLA             # or density_g_cm3
    motion:                   # makes this part movable, and pairs with it "sliding"
      type: rotate            # rotate | translate
      axis: [0, 0, 1]         # axis (rotate) or vector (translate)
      center: [0, 0, 0]
      range: [0, 360]         # degrees for rotate, mm for translate
  - name: pin
    code: "pin();"
    place: "translate([0,0,-2])"
    ghost: true               # takes part in pair checks and sweeps; ignored by probes, rays and polylines
    printed: false            # a purchased part
    mass_g: 1.2               # overrides density for mass
    color: "#D55E00"          # render(mode="parts") colour override
    explode: [0, 0, 20]       # exploded-view offset
    print: {orientation: [0, 0, -1]}   # default orientation for the print rule

checks:
  - rule: interference        # "no_intersect" is an accepted alias
    pairs: [[post, arm]]      # "all" (default) or an explicit list
    tolerance_mm: 0.0         # penetration allowed before FAIL
    why: "arm must clear the post"

  - rule: clearance
    pairs: [[post, arm]]
    min_mm: 0.3               # "required_mm" also accepted

  - rule: contact
    pairs: [[post, arm]]
    kind: sliding             # static (must touch) | sliding (must not touch)
    min_gap_mm: 0.2           # sliding; use min_area_mm2 for static

  - rule: alignment
    tolerance_mm: 0.2         # max coaxial offset between hole stacks

  - rule: predicate
    expr: "bore_d > pin_d"    # evaluated in the model's own scope, must be true

  - rule: probe
    point: [0, 0, 5]
    expect: AIR               # SOLID | AIR; a SURFACE reading is UNRESOLVED
    size_mm: 0.0              # >0 treats the probe as a small box
    reason: "bore must be open"

  - rule: ray
    origin: [0, 0, -5]
    direction: [0, 0, 1]
    max_distance_mm: 50
    first_hit: post           # omit to require that the ray hits nothing

  - rule: sweep
    moving: arm               # takes the part's motion block, overridable here
    against: [post]           # "all" or a list
    steps: 24

  - rule: print
    part: post
    max_overhang_area_mm2: 200
    max_unsupported_reach_mm: 5
    min_feature_mm: 0.8
    nozzle_mm: 0.4
    layer_height_mm: 0.2
```

```yaml
  - rule: mass
    part: platter             # or parts: [a, b]; omit both for the whole assembly
    max_g: 40                 # each limit given adds one row; none = a facts row
    min_g: 5
    com_within_mm: 0.5        # centre of mass within this of `axis` or `point`
    axis: [[0, 0, 0], [0, 0, 1]]
    max_inertia_g_mm2: 4000   # about `axis`; the row also carries kg m^2
    material: PLA             # fallback density for parts that give none
```

Mass comes from the exported meshes by exact tetrahedral integration. A
part's own `mass_g` wins (a purchased part), then its `material` or
`density_g_cm3`, then the rule's, then PLA; every row carries a
`density_source` map so a defaulted density is visible. A part whose mesh is
not watertight, or that has neither a mesh nor a `mass_g`, makes the row
UNRESOLVED rather than a number. `at` is the centre of mass.

### The row shape

Every rule produces rows of one shape:

```json
{"rule": "clearance", "subject": ["post", "arm"], "status": "PASS",
 "state": "clear",
 "magnitude": {"distance_mm": 0.5, "required_mm": 0.3},
 "at": [0.0, -2.2, 10.25],
 "quality": {"fn": 64, "curved_features": true, "error_bound_mm": 0.0072},
 "tier": "python", "why": "parts must keep >= 0.3 mm apart",
 "elapsed_s": 0.0}
```

`status` is `PASS`, `FAIL` or `UNRESOLVED`. `tier` is `python` for rows decided
from the meshes and `openscad` for predicate rows. `magnitude` holds whatever
the rule measured. `at` is the witness point. `note` explains an UNRESOLVED or
a surprising FAIL. `closest` (the closest-point pair) and normals on passing
rows appear only under `response_format="detailed"`.

Ghost parts are rendered, swept against and ray-cast, but excluded from probe
point classification.

## Resources and server instructions

| URI | MIME type | Contents |
|---|---|---|
| `openscad://conventions` | `text/plain` | the modelling conventions, identical to the server instructions |
| `openscad://cheatsheet` | `text/plain` | OpenSCAD language behaviour that language models get wrong |
| `openscad://reference/{topic}` | `application/json` | one reference topic, always detailed |
| `resource://server/info` | JSON | version, OpenSCAD version, path and capabilities, `max_concurrent_renders`, `cache_enabled`, `allowed_paths`, `path_validation_enabled`, `supported_formats` |

The server advertises these instructions on connect:

```
Modelling conventions (OpenSCAD, this server):
- Units are millimetres. Z is up, right-handed, XY is the build plate.
- Put the part origin at a meaningful datum, normally bottom-centre, and state
  the datum in a comment.
- One module per physical part, with a `part` parameter or separate named
  modules, so any one part or the whole assembly can be rendered.
- Overlap coplanar boundaries in union/difference by an epsilon of 0.01 mm;
  extend cutting solids past both faces. Never inset: exact coincidence gives
  non-manifold geometry.
- Declare every key dimension as a commented top-level variable.
- Make clearance an explicit named variable. If BOSL2 is available, set $slop
  once instead and let its modules apply it.
- $fn moderately: 24 while iterating, 64+ for final renders and export.
- Prefer `include <BOSL2/std.scad>` when BOSL2 is installed.
- echo() the assumptions and derived sizes; assert() the constraints.
- Check results with the measurement tool, not by judging the rendered picture.
```

## Configuration

Environment variables are read at first use, from the process environment and
from a `.env` file.

| Variable | YAML key | Default | Meaning |
|---|---|---|---|
| `OPENSCAD_PATH` | `openscad_path` | auto-detected | OpenSCAD binary |
| `IMAGEMAGICK_PATH` | `imagemagick_path` | null | ImageMagick convert |
| `MCP_TEMP_DIR` | `temp_dir` | `/tmp/openscad-mcp` | scratch directory |
| `MCP_TRANSPORT` | `server.transport` | `stdio` | `stdio`, `http`, `sse` |
| `MCP_HOST` | `server.host` | `localhost` | HTTP/SSE bind host |
| `MCP_PORT` | `server.port` | `8000` | HTTP/SSE port, 1024-65535 |
| `MCP_MAX_CONCURRENT_RENDERS` | `rendering.max_concurrent` | `5` | 1-20 |
| `MCP_RENDER_TIMEOUT` | `rendering.timeout_seconds` | `300` | 30-3600 |
| `MCP_MAX_IMAGE_WIDTH` | `rendering.max_image_width` | `1568` | clamp, aspect preserved |
| `MCP_MAX_IMAGE_HEIGHT` | `rendering.max_image_height` | `1568` | clamp, aspect preserved |
| (none) | `rendering.default_color_scheme` | `Cornfield` | |
| `MCP_HARD_WARNINGS` | `rendering.hard_warnings` | `false` | pass `--hardwarnings` |
| `MCP_CACHE_ENABLED` | `cache.enabled` | `true` | |
| (none) | `cache.directory` | `~/.cache/openscad-mcp` | |
| `MCP_CACHE_SIZE_MB` | `cache.max_size_mb` | `500` | 100-10000, LRU eviction |
| `MCP_CACHE_TTL_HOURS` | `cache.ttl_hours` | `24` | 1-168 |
| `MCP_RATE_LIMIT` | `security.rate_limit` | `60` | requests per minute, 0 disables |
| `MCP_MAX_FILE_SIZE_MB` | `security.max_file_size_mb` | `10` | limit on `scad_content` |
| `MCP_ALLOWED_PATHS` | `security.allowed_paths` | unset | `os.pathsep`-separated roots |
| `MCP_MAX_MEMORY_MB` | `security.max_memory_mb` | `4096` | RLIMIT_AS per subprocess, 0 disables |
| `MCP_LOG_LEVEL` | `logging.level` | `INFO` | |
| `MCP_LOG_FILE` | `logging.file` | null | rotating file handler |

1568 px is the long-edge limit above which vision models downscale anyway.

`--hardwarnings` is off by default. The flag stops evaluation at the first
warning while still exiting 0, which silently truncates echo output and blanks
renders. Warnings are surfaced through structured diagnostics instead.

`allowed_paths` unset means no path validation at all, and a warning is logged
at startup. When set, it is enforced on `scad_file`, `mesh`, `output_path`,
`workspace`, `project_dir`, `include_paths`, and on the full closure of files
OpenSCAD actually read.

A YAML file passes the same keys:

```yaml
server:
  transport: stdio
rendering:
  max_concurrent: 5
  timeout_seconds: 300
cache:
  enabled: true
  max_size_mb: 500
security:
  allowed_paths:
    - /home/me/projects/parts
  max_memory_mb: 4096
```

## Error handling

**Tool-level errors.** Every tool catches exceptions and returns a plain
object rather than raising an MCP protocol error:

```json
{"success": false, "mode": "views",
 "error": "Invalid view name(s): nope. Must be one of: front, back, left, right, top, bottom, isometric, dimetric"}
```

`render` returns that object as the single element of its list. `model` also
echoes `action`; `measure`, `check`, `validate` and `render` echo `mode`.

**Silent OpenSCAD failures.** A failed `assert()` or an unknown module makes
OpenSCAD draw an empty scene and exit 0. Always read `errors`, `warnings` and
`hints` rather than trusting that an image came back.

**UNRESOLVED rows.** A `check` row is UNRESOLVED when the answer is below the
tessellation error bound, when a probe lands exactly on a surface, when a
predicate could not be evaluated, or when the rule's own evaluation raised:

```json
{"rule": "clearance", "subject": ["sleeve", "pin"], "status": "UNRESOLVED",
 "state": "clear",
 "magnitude": {"distance_mm": 0.049, "required_mm": 0.02},
 "at": null, "tier": "python",
 "quality": {"fn": null, "curved_features": true, "error_bound_mm": 0.0583},
 "note": "distance 0.049 mm is inside the tessellation error bound 0.0583 mm at fn=None; re-run at quality=high (or a larger $fn) to resolve"}
```

The whole result then carries `exit_code: 2`. UNRESOLVED is not a pass. A rule
that raises is caught and turned into an UNRESOLVED row so one bad rule cannot
kill the report.

**Empty geometry.** `measure` sets `empty: true` with a note and an
`empty_output` hint. `render(mode="section")` sets `empty_section: true` and
says so in the digest. `check` lists such parts under `empty_parts` and adds an
`UNRESOLVED` finding per empty part, so the exit code is 2 rather than a silent
0. A common cause is a file that instantiates geometry only under
`if ($preview)`, which does not exist at export time: pass the guard variable
through `variables`.

**Security withholding.** When a model reads a file outside `allowed_paths`
through `include`, `use`, `import` or `surface`, the run completes and its
output is discarded, because the contents could otherwise leave through echo
output or geometry:

```json
{"success": false, "mode": "model",
 "error": "The model reads files outside allowed paths and its output has been withheld: ['/etc/secret.scad']. Allowed roots: ['/home/me/projects/parts']"}
```

A path argument outside the sandbox is rejected before anything runs:

```json
{"success": false, "mode": "model",
 "error": "File path '/etc/hostname' is not within allowed paths: ['/home/me/projects/parts']"}
```
