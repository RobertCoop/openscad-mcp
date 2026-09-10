---
name: openscad-design
description: Design, modify, and verify 3D-printable parts and assemblies in OpenSCAD through the openscad-mcp tools. Use when the user asks for a bracket, enclosure, adapter, jig, or other parametric part, or wants an existing .scad file changed, measured, fit-checked, or exported for printing.
license: MIT
---

# OpenSCAD design loop

One rule sits above the rest: **numbers decide, pictures confirm.** A render is a
low-bandwidth summary of geometry. Vision models routinely rate a broken model as
fine, because a missing internal wall, a 0.02 mm gap, or a non-manifold seam looks
identical to a correct part at 800x600. Never conclude "that looks right" from an
image. Conclude it from `measure` and `validate`, then use the image to catch the
class of error numbers miss: wrong orientation, a feature on the wrong face, a part
that is inside-out.

## The loop

### 1. State assumptions before writing code

Say these out loud in one or two lines, because they are invisible in the source and
wrong in half of all first attempts:

- **Units**: OpenSCAD is unitless; everything here is millimetres.
- **Orientation**: which axis is up, and which face sits on the print bed.
- **Datum**: where the origin is. Corner-at-origin and centred-on-origin are both
  fine, but say which, and keep it consistent across parts of an assembly.
- **Print process**: FDM layer height and nozzle width if the user has not said,
  because they set minimum wall and minimum feature size.

### 2. Declare key dimensions as variables

Every number that the user might change, or that appears more than once, becomes a
named variable at the top of the file. Derive the rest with expressions rather than
retyping arithmetic. This is what makes step 6 cheap: an iteration becomes one
variable edit, not a search-and-replace through solid geometry.

```openscad
wall      = 2.4;   // 3 perimeters at 0.8 mm
inner_x   = 60;
inner_y   = 40;
inner_z   = 25;
clearance = 0.2;   // from reference(topic="fits")
eps       = 0.01;  // coplanar-face overlap
```

### 3. `validate(mode="syntax")` first

Before any render or export. OpenSCAD's exit code is not a success signal: a failed
`assert()`, an unknown module, a missing include, and an unclosed polyhedron all
exit 0 while printing to stderr. `validate` parses that stream. Read `errors`,
`warnings`, and `hints` on the response. Fix everything in `errors` before moving on
and read every warning, since "Ignoring unknown module" means a whole feature is
silently absent from the geometry you are about to measure.

### 4. `measure` before you trust any picture

`measure(mode="model")` exports the mesh and returns exact numbers: bounding box,
dimensions, volume, surface area, component count, watertightness. Compare those
against the dimensions you intended. A bounding box 0.4 mm larger than expected is a
clearance bug you would never see in a render.

`measure` also accepts an existing STL through `mesh=`, so you can measure a file
the user already has without re-rendering it.

Modes: `model` for the whole thing, `parts` for per-solid numbers and the solid
count, `section` for a cut plane, `mass` for volume-times-density estimates. The
rest — `probe`, `features`, `printability`, `orientation`, `anchors` — are in the
tool reference at the end.

### 5. `render(grounded=true)`, one to three views

Only after the numbers agree. Ask for the smallest number of views that answers the
open question, usually one isometric plus one orthographic view of the face in
question. More images degrade the model's own counting and comparison, so a
four-view contact sheet is worse than one well-chosen view, not better.

Modes: `views` for standard camera positions, `section` for a cut, `parts` for
per-part colouring, `compare` for before/after.

### 6. Multi-part designs

- **One module per part.** `module lid() { ... }`, `module body() { ... }`, and a
  separate assembly view that translates them into place. Never one monolithic union.
- **Overlap coplanar faces by epsilon.** Two solids that exactly touch along a face
  or an edge produce a non-manifold result that CGAL drops or reports as not
  2-manifold. Add `eps` so the parts genuinely interpenetrate, and subtract `eps` on
  through-holes so the cutter pokes out both sides.
- **Get clearances from `reference(topic="fits")`**, not from memory. Press fit,
  slip fit, and free fit differ by tenths of a millimetre and the right number
  depends on the process. Related topics: `fasteners`, `inserts`, `bearings`,
  `magnets`, `joints`, `dfm`, `materials`, `conventions`, `cheatsheet`.
- **Check the solid count.** `measure(mode="parts")` should report exactly as many
  components as you designed. Two when you expected one means a part is floating
  free; one when you expected two means they fused and will print as a single lump.
- **Check manifoldness.** `validate(mode="geometry")` returns `mesh_health`. Look at
  `mesh_health.manifold`: `true` is good, `false` names the defect in
  `mesh_health.issue`, and `null` means the check was not performed, which is not the
  same as passing.

### 6b. Assemblies: name the parts, then ask `check`

Once a design has more than one part, stop reasoning from pictures and give the
server the parts by name. Every part is exported separately (never unioned), so
identity survives and every relation is a number in the assembly frame.

```
check(scad_file="assembly.scad", mode="interference",
      parts=[{"name": "bracket", "code": "bracket();"},
             {"name": "motor", "code": "motor();", "place": "translate(MOTOR_POS)",
              "ghost": true, "mass_g": 34}])
```

- `state` is `clear`, `contact`, or `interference`. Flush contact is *contact*,
  never interference; a sliding pair (a pinion on a plate) that reports contact is
  the defect, so declare `motion` on the moving part and use `contact` with
  `kind="sliding"`.
- `mode="clearance"` gives the exact minimum distance and both closest points;
  `mode="motion"` sweeps a part about an axis or along a vector and, for a full
  turn, adds a certificate that says whether the parts can *ever* touch.
- `mode="alignment"` is the only check that sees a 0.6 mm hole misalignment:
  interference and clearance both read zero there.
- `rule: mass` in a check file limits total mass, centre-of-mass offset from an
  axis or point, and inertia about an axis, composed from the exported meshes
  with each part's `mass_g`, `material` or `density_g_cm3` (PLA if none, flagged).
- Every row carries `quality.fn`. A distance smaller than the tessellation error
  bound comes back `UNRESOLVED`; re-run with `quality="high"` or an integer `$fn`.
- `measure(mode="probe")` answers "is there material at this point, and whose",
  ray casts ("does the scanner see the card"), and line-of-sight polylines.
- `measure(mode="features")` lists the holes a part cuts (axis, diameter, depth,
  fit name) straight from the CSG tree, and `reference(topic="fits", diameter_mm=3.3)`
  names what a hole is.
- Freeze the rules in a check file (YAML: `frames`, `quality`, `parts`, `checks`,
  `model`) and re-run `check(check_file=..., mode="rules")` after every edit;
  `openscad-mcp check file.yaml` does the same from a Makefile.

**Purchased parts.** `reference(topic="parts")` lists the five sourced entries:
`28byj-48`, `nema17`, `lazy-susan-4in`, `kw11-3z` (snap-action microswitch) and
`tcrt5000-module`. `model(action="create", template="part:28byj-48")` writes
BOSL2 modules with named anchors, a clearance mask for `difference()`, and a
`verify[]` list of dimensions to confirm on your own motor. The module names come
from the entry, not from the id: `28byj-48` gives `part_28byj48()`,
`part_28byj48_mask()`, `part_28byj48_mount_holes_mask()` and
`part_28byj48_info()`. Design the pocket from the part's named numbers, then let `check`
prove it: the mask, not a `minkowski()` grow, is the pocket; `hull()` of two
poses is wrong for an insertion sweep.

**Before printing.** `measure(mode="printability", orientation=[180,0,0])` gives
overhang patches with their unsupported reach, the wall-thickness distribution
against the nozzle, islands, and a support estimate. `mode="orientation"` lists
candidates but names no winner; you decide by which faces must look good.
`validate(mode="printability")` turns the facts into findings with thresholds.

### 7. Iterate by changing a variable and re-measuring

Change one variable, re-run `measure`, compare the number to the previous number.
That is the whole inner loop. Use `scad_eval` to check a derived expression in the
design's own parameter space before you commit to a render: it evaluates expressions
against the file's variables and returns typed results, which is far cheaper than
rendering to find out that `lid_inner` came back `undef`.

### 8. `export_model` last

Export only once the numbers and the manifold check pass. STL for printing, 3MF when
you want units and metadata preserved.

## Worked example: a box with a lid

```
1. validate(mode="syntax", scad_content=...)
   -> errors: []   warnings: []   hints: []
      Nothing else runs until errors is empty.

2. reference(topic="fits")
   -> pick the slip-fit clearance for a lid over a box; call it 0.2 mm.

3. scad_eval(scad_content=..., expressions=["outer_x", "lid_inner_x"])
   -> confirm lid_inner_x == outer_x + 2*clearance before rendering anything.
      A typed result of undef here means a variable name is wrong.

4. measure(mode="model", scad_content=...)
   -> bbox and dimensions. Check against the intended outside size.
      Check watertight == true.

5. measure(mode="parts", scad_content=...)
   -> components == 2 (body, lid). If it says 1, the lid fused to the body:
      clearance is zero or negative somewhere.

6. validate(mode="geometry", scad_content=...)
   -> mesh_health.manifold == true. If false, read mesh_health.issue; the usual
      cause is two solids meeting exactly on a face. Add eps and repeat.

7. render(mode="views", grounded=true, views=["isometric"], scad_content=...)
   -> read the digest text before the image: confirm the stated bbox matches
      step 4 and the lid sits on top rather than intersecting.

8. export_model(format="3mf", ...)
```

What to read in every response, in order: `errors`, then `warnings`, then `hints`
(these carry the specific repair advice for the message class OpenSCAD emitted), then
the payload. On renders and measurements also check `cached`. A `cached: true`
response is a previous result replayed; if you edited an included file rather than
the top-level source and the flag says cached, the number you are looking at may
predate your edit. Force a fresh run or `clear_cache` before believing it.

## Reading a render

Every render arrives as a **text digest followed by an image**. The digest is the
part you reason from. It states the units, the up-axis and handedness, the exact
camera eye, center, and up vectors, the projection, the view direction in plain
language ("camera looks along +Y; +X is right, +Z is up"), the model's bounding box,
and the scale in millimetres per pixel.

- **`grounded=true` gives orthographic projection and a real scale.** The stated
  mm/px is true everywhere in the frame, so you can measure a feature by counting
  pixels and multiplying. Annotations and a scale bar are drawn into the image.
- **The mm/px guarantee holds only under orthographic projection.** Under the default
  perspective camera the scale varies across the image and no single number describes
  it.
- **Auto-framed renders have unknown absolute scale.** Anything rendered with
  `--viewall` fits the model to the frame, so a 2 mm cube and a 500 mm cube come out
  pixel-identical. Use those images for shape and topology only. If a question is
  about size, either read the bbox from the digest or ask for `grounded=true`.

## Gotchas that bite most often

**Variables are compile-time, not sequential.** OpenSCAD assignments are resolved
before evaluation, and the last assignment in a scope wins for the whole scope. This
does not work:

```openscad
h = 10;
if (tall) { h = 20; }   // does not change h outside the if
```

Use a conditional expression instead: `h = tall ? 20 : 10;`. The same rule means you
cannot accumulate a value in a `for` loop; build a list and use a function.

**`difference()` order matters.** The first child is the base solid and every later
child is subtracted from it. Reordering children silently produces a different shape,
and a `difference()` whose first child is smaller than the rest yields nothing at
all. When a model renders empty, check this before anything else.

**2D and 3D do not mix.** `square`, `circle`, `polygon`, `offset`, and `projection`
produce 2D geometry. `cube`, `cylinder`, `sphere`, and `linear_extrude` produce 3D.
Combining them in one boolean gives "Mixing 2D and 3D objects is not supported" and
an unusable result. Extrude first, then combine.

**`$fn` is a global trap.** Left unset, curve resolution comes from `$fa` and `$fs`.
Setting `$fn` high at the top of the file applies it to every curved primitive at
once, which makes exports slow and huge. Set `$fn` locally on the primitives that
need it, or use the server's quality presets, and remember that `$fn` on a small
hole should be low, not high.

**`import()` needs `convexity`.** Without `convexity=10` (or higher for complex
shapes), imported meshes render with holes and inverted surfaces in preview even
though the geometry is fine. The same applies to `linear_extrude` of complex
profiles and to `%import("ref.stl", convexity=10)` when ghosting a reference part.

**Zero-thickness walls and exact touches.** Anything with a dimension of exactly 0,
or two solids that share a face precisely, produces geometry CGAL cannot resolve.
Epsilon overlaps are not a hack here; they are the correct construction.

## Tool reference

| Tool | Use it for |
|---|---|
| `check` | `interference`, `clearance`, `contact`, `alignment`, `motion`, `rules` over named parts; check files (`model`, `quality`, `frames`, `parts`, `checks`); exit codes |
| `validate` | `syntax`, `geometry`, `predicates` (+`sweep`), `includes` (+BOSL2 lint, `autofix`), `printability` |
| `measure` | `model`, `parts`, `section`, `mass`, `probe`, `features`, `printability`, `orientation`, `anchors`; existing STL via `mesh` |
| `render` | `views`, `section`, `parts`, `compare`; `grounded=true` for real scale; `look_at`, `callouts` |
| `scad_eval` | typed evaluation of expressions in the design's parameter space |
| `reference` | fits (also by diameter or shaft+bore), fasteners, inserts, bearings, magnets, joints, parts, conventions, cheatsheet, dfm, materials |
| `export_model` | STL, 3MF, AMF, OFF, NEF3, DXF, SVG, PDF, CSG; `parts=` for a named-object 3MF |
| `model` | `action=create|get|update|list|delete`; `template="part:<id>"` |
| `check_openscad` | binary presence, version, capabilities |
| `get_libraries` | what is installed on this machine, and the exact import line |
| `get_project_files` | .scad files and their references; `mode=trace` for a constant's dependents |
| `clear_cache` | when a cached result may predate an edit to an included file |
