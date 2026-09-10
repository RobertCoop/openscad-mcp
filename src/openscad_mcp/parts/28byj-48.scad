//////////////////////////////////////////////////////////////////////////////
// openscad-mcp purchased-parts catalog
// 28BYJ-48 5 V unipolar geared stepper motor
//
// GENERATED FILE. The numbers live in src/openscad_mcp/parts_catalog.py; this
// file is the OpenSCAD face of that entry. Edit both together.
//
// Dimensions from the manufacturer drawing "28BYJ-48 - 5V Stepper Motor":
//   https://components101.com/sites/default/files/component_datasheet/28byj48-step-motor-datasheet.pdf
//   mirrored at https://cdn-shop.adafruit.com/datasheets/28byj48dimension.jpg
// Cross-checked against the measured drawing at https://cookierobotics.com/042/
// and the CAD parameters in nophead's NopSCADlib vitamins/geared_steppers.scad.
// Electrical from the OSEPP STEPD-01 datasheet:
//   https://www.mouser.com/datasheet/2/758/stepd-01-data-sheet-1143075.pdf
//
// Dimensions are facts and are not copyrightable. This module is original work,
// MIT licensed with the rest of openscad-mcp.
//
// NOT ON ANY DRAWING (see the `verify` list in parts_catalog.py): tab plate
// thickness, connector box axial height, mass.
//////////////////////////////////////////////////////////////////////////////

// BOSL2 is included unconditionally, which is safe under BOTH `include <>` and
// `use <>` of this file: OpenSCAD's include is a preprocessor directive and
// cannot be made conditional, and re-reading BOSL2 only redefines identical
// modules. Do not try to guard it with an if().
include <BOSL2/std.scad>
assert(is_list(BOSL_VERSION) && BOSL_VERSION[0] >= 2,
       "28byj-48.scad needs BOSL2 2.x: include <BOSL2/std.scad> must resolve.");

// Facet count pinned on every round feature so that a clearance is not eaten by
// an octagon, and so that solid and mask polygonise identically.
_BYJ_FN = 64;

// ---------------------------------------------------------------------------
// Data. Every value here is repeated in parts_catalog.py.
// ---------------------------------------------------------------------------
_BYJ = [
    ["body_dia",        28.0],   // drawing "Phi 28", untoleranced
    ["body_h",          19.0],   // drawing "19", front face to back, untoleranced
    ["tab_span",        42.0],   // derived: hole_spacing + 2 * tab_r
    ["tab_w",            7.0],   // drawing "7"
    ["tab_r",            3.5],   // drawing "2-R3.5"
    ["tab_t",            1.0],   // NOT on the drawing; cookierobotics measured 1.0
    ["hole_spacing",    35.0],   // drawing "35 +/-0.2", hole centre to hole centre
    ["hole_dia",         4.2],   // drawing "2-Phi 4.2 +/-0.15"
    ["boss_dia",         9.0],   // drawing "Phi 9"
    ["boss_h",           1.5],   // drawing "1.5"
    ["shaft_offset",     8.0],   // drawing "8", shaft axis off the body axis
    ["shaft_dia",        5.0],   // drawing "Phi 5 0/-0.1"
    ["shaft_len_face",  10.0],   // drawing "10 +/-0.5", tip to front face
    ["flat_across",      3.0],   // drawing "3 0/-0.1", DOUBLE D, across both flats
    ["flat_len",         6.0],   // drawing "6 +/-0.2", measured from the shaft tip
    ["wirebox_w",       14.6],   // drawing "14.6 +0.2/-0", across the connector box
    ["wirebox_reach",   17.0],   // drawing "17", body axis to the outer box face
    ["wirebox_h",       16.5],   // NOT on the drawing; NopSCADlib models 16.5
    ["mass_g",          36.5],   // vendor consensus 36-37 g, no datasheet value
];

// Function: part_28byj48_info()
// Usage: info = part_28byj48_info();  d = struct_val(info, "shaft_dia");
// Description: Key numbers for this part, as a BOSL2 struct (list of [key, value]).
function part_28byj48_info() = _BYJ;

function _byj(k) = struct_val(_BYJ, k);

// FRAME. The part origin is the centre of the overall bounding box, so that the
// attachable size below is the real geometry and the standard anchors (TOP,
// RIGHT, ...) are exact. Use the NAMED anchors for the datums you actually mate
// to. _byj_org() is where the natural datum -- the body axis at mid body height
// -- sits in that frame; the geometry below is authored around the natural datum
// and translated by it once.
function _byj_size() = [
    _byj("wirebox_reach") + _byj("body_dia") / 2,                 // 17 + 14 = 31
    _byj("tab_span"),                                             // 42
    _byj("body_h") / 2 + _byj("shaft_len_face") + _byj("body_h") / 2  // 29
];
function _byj_org() = [
    (_byj("wirebox_reach") - _byj("body_dia") / 2) / 2,           // +1.5
    0,
    -_byj("shaft_len_face") / 2                                   // -5
];

// Named anchors for mating features. Positions are in the part frame; every one
// of them is also listed under `anchors` in parts_catalog.py.
function _byj_anchors() =
    let(
        o    = _byj_org(),
        face = _byj("body_h") / 2,
        so   = _byj("shaft_offset"),
        hs   = _byj("hole_spacing") / 2
    ) [
        named_anchor("mount-plane", o + [0, 0, face], UP, 0),
        named_anchor("shaft-axis",  o + [so, 0, face], UP, 0),
        named_anchor("boss-top",    o + [so, 0, face + _byj("boss_h")], UP, 0),
        named_anchor("shaft-tip",   o + [so, 0, face + _byj("shaft_len_face")], UP, 0),
        named_anchor("hole-a",      o + [0,  hs, face], UP, 0),
        named_anchor("hole-b",      o + [0, -hs, face], UP, 0),
        named_anchor("wire-exit",   o + [-_byj("wirebox_reach"), 0,
                                     -face + _byj("wirebox_h") / 2], LEFT, 0),
        named_anchor("body-back",   o + [0, 0, -face], DOWN, 0),
    ];

// ---------------------------------------------------------------------------
// Solid
// ---------------------------------------------------------------------------
// Module: part_28byj48()
// Usage: part_28byj48([anchor], [spin], [orient]) [children];
// Description:
//   The motor as purchased, mounting face and shaft toward +Z. The origin is the
//   centre of the bounding box; anchor to "mount-plane" or "shaft-axis" to place
//   it against something. Authored for plain difference(): do NOT wrap it in
//   BOSL2 diff() or tag(), which silently union across a use<> boundary.
module part_28byj48(anchor = CENTER, spin = 0, orient = UP) {
    attachable(anchor, spin, orient,
               size = _byj_size(), anchors = _byj_anchors()) {
        translate(_byj_org()) union() {
            color("dimgray") _byj_can(0);
            color("silver")  _byj_tabs(0);
            color("dimgray") _byj_boss(0);
            color("gold")    difference() { _byj_shaft(0); _byj_shaft_flats(); }
            color("navy")    _byj_wirebox(0);
        }
        children();
    }
}

// grow g is a per-side offset; g = 0 reproduces the solid exactly, so that
// difference(part, mask) is empty when the mask clearances are zero.
module _byj_can(g) {
    cyl(d = _byj("body_dia") + 2 * g, h = _byj("body_h") + 2 * g, $fn = _BYJ_FN);
}

module _byj_tabs(g) {
    hs = _byj("hole_spacing");
    up(_byj("body_h") / 2 - _byj("tab_t") / 2)
        hull() {
            ycopies(hs) cyl(d = 2 * _byj("tab_r") + 2 * g, h = _byj("tab_t") + 2 * g,
                            $fn = _BYJ_FN);
            cuboid([_byj("tab_w") + 2 * g, hs, _byj("tab_t") + 2 * g]);
        }
}

module _byj_boss(g) {
    right(_byj("shaft_offset")) up(_byj("body_h") / 2)
        cyl(d = _byj("boss_dia") + 2 * g, h = _byj("boss_h") + g, anchor = BOTTOM,
            $fn = _BYJ_FN);
}

module _byj_shaft(g) {
    right(_byj("shaft_offset")) up(_byj("body_h") / 2)
        cyl(d = _byj("shaft_dia") + 2 * g, h = _byj("shaft_len_face") + g,
            anchor = BOTTOM, $fn = _BYJ_FN);
}

// Two flats, "3 0/-0.1" across, running flat_len back from the tip.
module _byj_shaft_flats() {
    d  = _byj("shaft_dia");
    fl = _byj("flat_len");
    tip = _byj("body_h") / 2 + _byj("shaft_len_face");
    right(_byj("shaft_offset")) down(0.001)
        xcopies(_byj("flat_across") + d)
            up(tip - fl / 2 + 0.001) cuboid([d, d + 1, fl + 0.002]);
}

module _byj_wirebox(g) {
    reach = _byj("wirebox_reach");
    len   = reach + g;               // outer face moves out by g; inner end is buried
    left(len / 2)
        up(-_byj("body_h") / 2 + _byj("wirebox_h") / 2)
            cuboid([len, _byj("wirebox_w") + 2 * g, _byj("wirebox_h") + 2 * g]);
}

// ---------------------------------------------------------------------------
// Mask (negative)
// ---------------------------------------------------------------------------
// Module: part_28byj48_mask()
// Usage: difference() { your_part(); part_28byj48_mask(clr=0.4, install=DOWN, install_len=60); }
// Description:
//   The pocket for this motor. `clr` is running clearance per side on the body,
//   tab plate and connector box; `bore_clr` is per-side clearance on the round
//   boss and shaft, which usually wants less. Set `install_len` to sweep the
//   whole mask `install_len` mm along `install` so the motor can be put in and
//   taken out; the sweep is a minkowski() with a thin prism, which is exact for
//   concave parts (a hull() of two poses is not).
//   The mask shares the solid's frame, so any anchor puts the pocket exactly
//   where part_28byj48() with the same anchor would put the motor.
module part_28byj48_mask(clr = 0.25, bore_clr = 0.25, install = UP, install_len = 0,
                         anchor = CENTER, spin = 0, orient = UP) {
    attachable(anchor, spin, orient,
               size = _byj_size(), anchors = _byj_anchors()) {
        translate(_byj_org()) _byj_sweep(install, install_len) union() {
            _byj_can(clr);
            _byj_tabs(clr);
            _byj_wirebox(clr);
            _byj_boss(bore_clr);
            _byj_shaft(bore_clr);
        }
        children();
    }
}

// Straight-line install sweep. minkowski() with a thin prism along `dir`.
module _byj_sweep(dir, len) {
    if (is_undef(dir) || len <= 0) children();
    else {
        u = unit(dir);
        minkowski() {
            children();
            translate(u * len / 2) cyl(d = 0.01, h = len, orient = u, $fn = 4);
        }
    }
}

// Module: part_28byj48_mount_holes_mask()
// Usage: difference() { your_part(); part_28byj48_mount_holes_mask(d=3.4, h=40); }
// Description:
//   The two tab screw holes, centred on the mounting plane and running h/2 above
//   and below it. Separate from part_28byj48_mask() so you choose tap, clearance
//   or heat-set diameter yourself. Same frame as part_28byj48().
module part_28byj48_mount_holes_mask(d = 3.4, h = 40, anchor = CENTER, spin = 0,
                                     orient = UP) {
    attachable(anchor, spin, orient,
               size = _byj_size(), anchors = _byj_anchors()) {
        translate(_byj_org()) up(_byj("body_h") / 2)
            ycopies(_byj("hole_spacing")) cyl(d = d, h = h, $fn = _BYJ_FN);
        children();
    }
}
