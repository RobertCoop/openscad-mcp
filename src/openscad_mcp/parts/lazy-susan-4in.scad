//////////////////////////////////////////////////////////////////////////////
// openscad-mcp purchased-parts catalog
// 4 inch square lazy susan turntable bearing
//
// GENERATED FILE. The numbers live in src/openscad_mcp/parts_catalog.py.
//
// Source: Triangle Manufacturing Co. (Oshkosh, WI) part 4C, the US OEM of this
// item. Dimensioned drawing, change level J 03-01-23, "ALL DIMS +/-.020 UNLESS
// SPECIFIED":
//   https://www.triangleoshkosh.com/media/catalog/product/technical-images/4C.jpg
//   product page https://www.triangleoshkosh.com/lazy-susan-turntable-bearing-4c
// Cross-checks: Rockler 28969 https://www.rockler.com/low-profile-lazy-susans
//               Lee Valley https://assets.leevalley.com/Original/10091/44042-lazy-susan-bearings-c-01-e.pdf
//
// Dimensions are facts and are not copyrightable. This module is original work,
// MIT licensed with the rest of openscad-mcp.
//
// IMPORTANT MODELLING NOTE. This is the INSTALLED ENVELOPE of the bearing, not
// its internal structure: the two stamped plates and the ball race are one
// solid slab. That is what you need for a pocket, a bolt pattern and a stack-up.
// It is NOT a model of how the bearing comes apart. The plates are ~20 gauge
// steel, NOT aluminium, on every source checked. See `verify` in
// parts_catalog.py -- notably the plate thickness, on which Triangle's own
// drawing, spec table and marketing copy give three different answers.
//////////////////////////////////////////////////////////////////////////////

// BOSL2 is included unconditionally, which is safe under BOTH `include <>` and
// `use <>` of this file. OpenSCAD's include is a preprocessor directive and
// cannot be made conditional.
include <BOSL2/std.scad>
assert(is_list(BOSL_VERSION) && BOSL_VERSION[0] >= 2,
       "lazy-susan-4in.scad needs BOSL2 2.x: include <BOSL2/std.scad> must resolve.");

_LS4_FN = 64;

// ---------------------------------------------------------------------------
// Data. Drawing values are inches; the bracketed millimetres are the drawing's
// own conversions, so they are quoted, not computed here.
// ---------------------------------------------------------------------------
_LS4 = [
    ["plate_sq",       101.60],  // "4X 4.000 [101.60]", a true 4 inch, not 100 mm
    ["height",           8.13],  // "0.32 [8.13]" overall, section A-A
    ["bore_dia",        54.86],  // "2.16 [54.86]" centre opening
    ["plate_t",          0.91],  // 20 gauge per Triangle's spec table -- DISPUTED
    ["corner_r",         5.94],  // "R0.234 ON CORNERS AS MFG'S OPTION" -- not modelled
    ["big_hole_dia",     3.97],  // "0.156 [3.97] 8 HOLES", clearance
    ["small_hole_dia",   2.39],  // "0.094 [2.39] DIA. FOR #6 SELF TAPPING SCREW 8 HOLES"
    ["a_outer_bc",      80.96],  // "4X 3.188 [80.96]" bolt circle, plate A
    ["a_inner_bc",      69.85],  // "4X 2.750 [69.85]" bolt circle, plate A
    ["b_outer_bc",      89.69],  // "4X 3.531 [89.69]" bolt circle, plate B
    ["b_inner_bc",      74.61],  // "4X 2.937 [74.61]" bolt circle, plate B
    ["ball_circle",     76.20],  // Rockler "Diameter of Bearing Circle: 3''"
    ["load_lb",           300],  // Triangle, Rockler and Lee Valley all say 300 lb
];

// Function: part_lazy_susan_4in_info()
// Description: Key numbers for this part, as a BOSL2 struct (list of [key, value]).
function part_lazy_susan_4in_info() = _LS4;

function _ls4(k) = struct_val(_LS4, k);

// FRAME. The part is symmetric, so the origin is both the bounding box centre
// and the rotation axis at mid height. Plate A (holes on the X and Y axes) is
// the +Z face; plate B (holes on the diagonals) is the -Z face.
function _ls4_size() = [_ls4("plate_sq"), _ls4("plate_sq"), _ls4("height")];
function _ls4_org()  = [0, 0, 0];

function _ls4_anchors() =
    let(
        z  = _ls4("height") / 2,
        ao = _ls4("a_outer_bc") / 2,
        ai = _ls4("a_inner_bc") / 2,
        bo = _ls4("b_outer_bc") / 2 / sqrt(2),
        bi = _ls4("b_inner_bc") / 2 / sqrt(2)
    ) [
        named_anchor("mount-plane",  [0, 0,  z], UP, 0),    // plate A, the turntable
        named_anchor("mount-plane-b",[0, 0, -z], DOWN, 0),  // plate B, the base
        named_anchor("axis",         [0, 0,  0], UP, 0),    // rotation axis
        named_anchor("center-bore",  [0, 0,  z], UP, 0),
        named_anchor("hole-a",       [ao, 0,  z], UP, 0),   // plate A outer, clearance
        named_anchor("hole-b",       [ai, 0,  z], UP, 0),   // plate A inner, pilot
        named_anchor("hole-c",       [bo, bo, -z], DOWN, 0),// plate B outer, clearance
        named_anchor("hole-d",       [bi, bi, -z], DOWN, 0),// plate B inner, pilot
    ];

// ---------------------------------------------------------------------------
// Solid
// ---------------------------------------------------------------------------
// Module: part_lazy_susan_4in()
// Usage: part_lazy_susan_4in([anchor], [spin], [orient]) [children];
// Description:
//   The bearing as an installed envelope: one slab, the centre opening through
//   it, and the four bolt patterns as blind holes in the face they belong to.
//   Plate A (holes on the X and Y axes) faces +Z. Authored for plain
//   difference(): do NOT wrap it in BOSL2 diff() or tag().
module part_lazy_susan_4in(anchor = CENTER, spin = 0, orient = UP) {
    z = _ls4("height") / 2;
    t = _ls4("plate_t");
    attachable(anchor, spin, orient,
               size = _ls4_size(), anchors = _ls4_anchors()) {
        difference() {
            color("silver") cuboid(_ls4_size());
            cyl(d = _ls4("bore_dia"), h = _ls4("height") + 1, $fn = _LS4_FN);
            // Plate A: two square patterns on the X and Y axes, +Z face.
            up(z - t / 2 + 0.001) {
                _ls4_ring(4, _ls4("a_outer_bc") / 2, 0,
                          _ls4("big_hole_dia"), t + 0.002);
                _ls4_ring(4, _ls4("a_inner_bc") / 2, 0,
                          _ls4("small_hole_dia"), t + 0.002);
            }
            // Plate B: two square patterns on the diagonals, -Z face.
            down(z - t / 2 + 0.001) {
                _ls4_ring(4, _ls4("b_outer_bc") / 2, 45,
                          _ls4("big_hole_dia"), t + 0.002);
                _ls4_ring(4, _ls4("b_inner_bc") / 2, 45,
                          _ls4("small_hole_dia"), t + 0.002);
            }
        }
        children();
    }
}

module _ls4_ring(n, r, phase, d, h) {
    for (i = [0 : n - 1])
        zrot(phase + i * 360 / n) right(r) cyl(d = d, h = h, $fn = _LS4_FN);
}

// ---------------------------------------------------------------------------
// Mask (negative)
// ---------------------------------------------------------------------------
// Module: part_lazy_susan_4in_mask()
// Usage: difference() { your_part(); part_lazy_susan_4in_mask(clr=0.4); }
// Description:
//   The recess for the bearing: the square slab plus clearance. `bore_clr` grows
//   the centre opening, which only matters if you are routing a shaft or wiring
//   through it -- the opening is a hole in the part, so it is NOT subtracted
//   from the pocket. Set `install_len` to sweep the mask along `install`. Shares
//   the solid's frame.
module part_lazy_susan_4in_mask(clr = 0.25, bore_clr = 0.25, install = UP,
                                install_len = 0, anchor = CENTER, spin = 0,
                                orient = UP) {
    attachable(anchor, spin, orient,
               size = _ls4_size(), anchors = _ls4_anchors()) {
        _ls4_sweep(install, install_len)
            cuboid([_ls4("plate_sq") + 2 * clr, _ls4("plate_sq") + 2 * clr,
                    _ls4("height") + 2 * clr]);
        children();
    }
}

module _ls4_sweep(dir, len) {
    if (is_undef(dir) || len <= 0) children();
    else {
        u = unit(dir);
        minkowski() {
            children();
            translate(u * len / 2) cyl(d = 0.01, h = len, orient = u, $fn = 4);
        }
    }
}

// Module: part_lazy_susan_4in_mount_holes_mask()
// Usage: difference() { your_part(); part_lazy_susan_4in_mount_holes_mask(d=4.3, h=20); }
// Description:
//   One bolt pattern, centred on the face it belongs to and running h/2 either
//   side. `plate` selects which: "a" is the +Z face (holes on the X and Y axes),
//   "b" the -Z face (holes on the diagonals). `ring` selects "outer" (the
//   0.156 in clearance holes), "inner" (the 0.094 in pilots for #6 self tappers)
//   or "both". Same frame as part_lazy_susan_4in().
module part_lazy_susan_4in_mount_holes_mask(d = 4.3, h = 20, plate = "a",
                                            ring = "outer", anchor = CENTER,
                                            spin = 0, orient = UP) {
    assert(plate == "a" || plate == "b", "plate must be \"a\" or \"b\"");
    assert(ring == "outer" || ring == "inner" || ring == "both",
           "ring must be \"outer\", \"inner\" or \"both\"");
    z     = _ls4("height") / 2;
    top   = plate == "a";
    phase = top ? 0 : 45;
    ro    = (top ? _ls4("a_outer_bc") : _ls4("b_outer_bc")) / 2;
    ri    = (top ? _ls4("a_inner_bc") : _ls4("b_inner_bc")) / 2;
    attachable(anchor, spin, orient,
               size = _ls4_size(), anchors = _ls4_anchors()) {
        translate([0, 0, top ? z : -z]) union() {
            if (ring != "inner") _ls4_ring(4, ro, phase, d, h);
            if (ring != "outer") _ls4_ring(4, ri, phase, d, h);
        }
        children();
    }
}
