# AGENTS.md

Agent brief for the OpenSCAD MCP server. Claude Code should read the richer
`skills/openscad-design/SKILL.md` instead.

A Model Context Protocol server wrapping the OpenSCAD CLI: it renders `.scad` source
to images, exports meshes, and returns exact geometric measurements. OpenSCAD must be
installed on the host. Everything is in millimetres; OpenSCAD itself is unitless.

## Tools

| Tool | What it does |
|---|---|
| `render` | Images. `mode=views\|section\|parts\|compare`. `grounded=true` gives an orthographic render with a stated mm/px scale and drawn annotations. |
| `measure` | Exact numbers from the exported mesh. `mode=model\|parts\|section\|mass\|probe\|features\|printability\|orientation\|anchors`: bounding box, volume, area, component count, watertightness, point-in-solid probes, holes read off the CSG tree. Also takes an existing STL via `mesh`. |
| `check` | Relations between named parts of an assembly. `mode=interference\|clearance\|contact\|alignment\|motion\|rules`. Each part is exported separately, so identity survives; `mode=rules` runs a YAML check file and returns an exit code. |
| `validate` | `mode=syntax\|geometry\|predicates\|includes\|printability`. Diagnostics parsed from stderr, never from the exit code. |
| `scad_eval` | Evaluates expressions in the design's own parameter space, returning typed values. |
| `reference` | Shipped engineering data: fits, fasteners, inserts, bearings, magnets, joints, parts, conventions, cheatsheet, dfm, materials. |
| `export_model` | STL, 3MF, AMF, OFF, NEF3, DXF, SVG, PDF, CSG. `parts=` writes one named-object 3MF. |
| `check_openscad` | Binary presence, version, capabilities. |
| `get_libraries` | Libraries installed on this machine, with the exact import line for each. |
| `get_project_files` | `.scad` files in a directory and their include/use dependency graph. `mode=trace` follows a constant's dependents. |
| `clear_cache` | Drops the render cache. |
| `model` | Workspace file CRUD: `action=create\|get\|update\|list\|delete`. `template="part:<id>"` writes a catalogued purchased part. |

## The design loop

1. State units, orientation, datum, and print process first. They are invisible in the source and wrong in half of all first attempts.
2. Declare every meaningful dimension as a named variable at the top; derive the rest with expressions.
3. Run `validate(mode="syntax")` before anything else. OpenSCAD exits 0 on failed asserts, unknown modules, and missing includes, so the exit code proves nothing.
4. Run `measure` before trusting any picture. Numbers decide, pictures confirm; a vision model reads broken geometry as fine.
5. Then `render(grounded=true)` with one to three views. More images make counting and comparison worse, not better.
6. For assemblies: one module per part, overlap coplanar faces by an epsilon, and take clearances from `reference(topic="fits")` rather than memory. Never union the assembly.
   Then hand the parts to `check` by name: `interference` and `clearance` for fit, `contact` for what should touch, `alignment` for a hole that is off by half a millimetre (interference and clearance both read zero there), `motion` for a sweep. Every row carries `quality.fn`; a distance inside the tessellation error bound comes back `UNRESOLVED`, so re-run at higher `$fn` rather than believing it. `measure(mode="features")` lists the holes a part cuts, and `reference(topic="parts")` plus `model(action="create", template="part:<id>")` gives you a sourced purchased part with named anchors and a clearance mask instead of guessed dimensions. Freeze the rules in a check file and re-run `check(mode="rules")` after every edit.
7. Confirm `measure(mode="parts")` reports the component count you designed, and that `validate(mode="geometry")` returns `mesh_health.manifold == true`. A `null` there means the check did not run, not that it passed.
8. Iterate by changing one variable and re-measuring. Use `scad_eval` to check derived expressions before spending a render.
9. On every response read `errors`, then `warnings`, then `hints`, then the payload. Check `cached`: a cached result can predate an edit to an included file.
10. `export_model` last, once the numbers and the manifold check pass.

## Reading a render

The text digest before the image carries the camera eye/center/up, the projection, the
bounding box, and the scale in mm per pixel. That scale is valid only under
orthographic projection, which is what `grounded=true` selects. Auto-framed
(`--viewall`) renders fit the model to the frame, so absolute scale is unknown and a
2 mm cube looks identical to a 500 mm one.

## Gotchas

Variables are compile-time and scope-final, so assigning inside an `if` or `for` does
not change the outer value; use `x = cond ? a : b`. `difference()` subtracts every
child after the first, so order changes the result. 2D and 3D geometry cannot be
combined without extruding first. A global `$fn` applies to every curve and makes
exports slow and huge. `import()` needs `convexity=10` or it renders with holes.
Solids that touch exactly, and any zero-thickness wall, produce non-manifold output.
