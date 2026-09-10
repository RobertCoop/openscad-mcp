# Purchased-parts catalog: data policy

These `.scad` files are package data for `openscad_mcp.parts_catalog`. One file
per catalogued part. They ship in the wheel and the sdist, and are reachable at
runtime with `part_scad_path(part_id)` and `part_scad_source(part_id)`.

## Why the policy exists

A model that guesses a motor's bolt circle produces a bracket that does not fit,
and the guess is invisible: it looks exactly like a measurement. Everything here
is designed so that a number you can rely on and a number you cannot are
distinguishable at a glance.

## The rules

**Every dimension carries a source and a confidence.** Confidence uses the same
three labels as the `reference` module:

| label | meaning |
| --- | --- |
| `standard` | a published standard, a manufacturer drawing, or a manufacturer datasheet |
| `consensus` | several independent vendors agree, with no governing document |
| `calibrate` | the honest answer is "measure it"; the number is a starting point |

**Numbers nobody publishes are not invented.** They go in the entry's `verify`
list, which is required and is never empty, and the OpenSCAD file exposes them as
a module parameter you can override once you have measured your part. Nothing is
silently filled in with a plausible value.

`verify` also records the disagreements. Where two vendors give different figures
for the same feature, both figures and both sources are named, and the model
picks one and says which. Triangle Manufacturing publishes three different plate
thicknesses for the same lazy susan; the entry says so rather than picking one
and looking confident.

**Derived values are labelled derived.** The 28BYJ-48's 42 mm tab span appears on
no drawing; it is `hole_spacing + 2 * tab_r`. The entry says that.

**Where a well-maintained library already has the part right, this is a thin
delta over it, not a reimplementation.** `nema17.scad` calls BOSL2's
`nema_stepper_motor()` and adds only what BOSL2 leaves to the caller: the real
body length, the real shaft length and the D-flat. BOSL2's `nema_motor_info(17)`
table was checked against NEMA ICS 16-2001 and against two manufacturer drawings
before being trusted.

**Corrections are recorded, not quietly applied.** The KW11-3Z case is
20 x 6.4 x 9.8 mm; the entry's `envelope_mm` reads 10.7 high because it includes
the plunger at its free position. The part is widely listed as "28.5 x 16 x 10",
which is a different, larger family. The entry says which part it is and how to
tell if you have the other one.

## Licensing

Dimensions are facts. Facts are not copyrightable, and a dimension read off a
public drawing carries no licence with it. The drawings themselves are not
copied, redistributed or vendored here; the entries cite their URLs.

The OpenSCAD modules are original work, MIT licensed with the rest of
openscad-mcp. BOSL2 is used via `include <BOSL2/std.scad>`, not vendored; it is
BSD-2-Clause and must be installed separately.

Each entry repeats this in its own `license_note` field so that it travels with
the data when a single entry is passed around.

## Modelling conventions these files follow

- The part origin is the centre of the overall bounding box, so the BOSL2
  `attachable()` size is the real geometry and `TOP`, `RIGHT` and the rest are
  exact. Use the **named** anchors for the datums you actually mate to.
- Four modules per part, sharing one frame: a solid, a clearance mask
  (`clr`, `bore_clr`, `install`, `install_len`), a mounting-hole mask (`d`, `h`)
  and an `_info()` function that returns the key numbers as a BOSL2 struct. The
  same `anchor=` argument places the first three identically. Individual parts
  add parameters of their own: the lazy susan's hole mask takes `plate`, the
  microswitch solid takes `lever`.
- **The module names are data, not a formula.** They live in the entry's
  `modules` dict (`solid`, `mask`, `mount_holes_mask`, `info`) and the id's
  punctuation is normalised away rather than mapped one-to-one: `28byj-48`
  gives `part_28byj48`, `part_28byj48_mask`,
  `part_28byj48_mount_holes_mask` and `part_28byj48_info`, while
  `lazy-susan-4in` gives `part_lazy_susan_4in`. Read the names off
  `lookup_part(id)["modules"]`; do not derive them.
- Masks are authored for plain `difference()`. Do **not** wrap them in BOSL2
  `tag()` or `diff()`: tags do not cross a `use<>` boundary, so the mask would
  silently union into your part instead of cutting it.
- `install_len` sweeps the mask along `install` with a `minkowski()` against a
  thin prism. That is exact for concave parts and costs a second or two. A
  `hull()` of two poses is wrong for anything concave, because it fills the
  concavity in.
- Round features pin `$fn = 64`, so a clearance is not eaten by a polygon
  cutting inside the true circle, and so a solid and its mask polygonise
  identically. That last point is what makes the containment self-check exact.
- `include <BOSL2/std.scad>` is unconditional. OpenSCAD's `include` is a
  preprocessor directive and cannot be guarded with an `if`; re-reading BOSL2
  only redefines identical modules, so these files are safe under both
  `include <>` and `use <>`.

## The self-check

`openscad_mcp.parts_catalog.self_check(part_id)` renders each part through the
real OpenSCAD binary and checks the geometry against the entry:

1. **envelope** - the exported bounding box matches `envelope_mm` to
   `SELF_CHECK_TOL_MM`, currently 0.05 mm.
2. **anchors** - each named anchor is probed with
   `<solid>() !position("name") cube(0.2, center=true)` and the marker's centre
   must land where the entry says.
3. **containment** - `difference() { <solid>(); <mask>(clr=0, bore_clr=0); }`
   must be **empty**. OpenSCAD signals empty by exiting non-zero
   with "Current top level object is empty" and writing no file, and that is the
   pass condition. A mask that does not fully contain its part at zero clearance
   would leave slivers of material in every pocket cut with it.

A new part is not finished until all three pass.
