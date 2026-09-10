//////////////////////////////////////////////////////////////////////////////
// openscad-mcp purchased-parts catalog
// NEMA 17 stepper motor, 17HS4401 / 17HS15-1504S class (42 mm frame, 40 mm body)
//
// GENERATED FILE. The numbers live in src/openscad_mcp/parts_catalog.py.
//
// This is a THIN DELTA over BOSL2's nema_stepper_motor(), which already encodes
// the NEMA frame correctly: nema_motor_info(17) returns
//   [42.3, 2.00, 22.0, 31.00, 3.0, 4.5, 5.00]
//   = body width, plinth height, plinth dia, screw spacing, screw dia, screw
//     depth, shaft dia
// and those agree with NEMA ICS 16-2001 flange 17 (pitch circle 1.725 in =
// 30.98 mm, pilot 0.8661 in = 22.00 mm, shaft 0.1969 in = 5.000 mm) and with
// the manufacturer drawings. What BOSL2 leaves to the caller, and what this
// file adds, is the real body length, the real shaft length and the D-flat.
//
// Sources:
//   NEMA ICS 16-2001 Table 2/4/6, flange 17:
//     https://smoothieware.github.io/Webif-pack/documentation/web/images/ics16.pdf
//   StepperOnline 17HS15-1504S-X1 full datasheet (drawing A0660):
//     https://www.omc-stepperonline.com/index.php?route=product/product/get_file&file=839/17HS15-1504S-X1_Full_Datasheet.pdf
//   GEMS GM42BYG NEMA 17 datasheet:  https://gemsmotor.com/stepper/nema17-stepper-motor.pdf
//   MotionKing 17HS series MK1106 Rev.04:
//     https://www.laskakit.cz/user/related_files/17hsxxxx-motionking.pdf
//
// Dimensions are facts and are not copyrightable. This module is original work,
// MIT licensed with the rest of openscad-mcp. BOSL2 itself is BSD-2-Clause and
// is used, not vendored.
//
// CAUTION: 42.3 mm is NOT a NEMA number -- ICS 16 lists flange square BD as
// "approximate values for reference only". Nor is the M3 thread; ICS 16 specs
// 4-40 for the C flange. Both are manufacturer convention that every vendor
// follows. See the `verify` list in parts_catalog.py for what is unsourced.
//////////////////////////////////////////////////////////////////////////////

// BOSL2 is included unconditionally, which is safe under BOTH `include <>` and
// `use <>` of this file. OpenSCAD's include is a preprocessor directive and
// cannot be made conditional.
include <BOSL2/std.scad>
include <BOSL2/nema_steppers.scad>
assert(is_list(BOSL_VERSION) && BOSL_VERSION[0] >= 2,
       "nema17.scad needs BOSL2 2.x with nema_steppers.scad.");

// Facet count pinned on every round feature so a clearance is not eaten by an
// octagon. This also forces BOSL2's segs() inside nema_stepper_motor(), which
// would otherwise give the 5 mm shaft only 12 sides.
_N17_FN = 64;

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
_N17 = [
    ["body_w",          42.3],   // "42.3MAX" on the StepperOnline and GEMS drawings
    ["body_h",          40.0],   // "40MAX"; the 17HS4401 body length
    ["body_chamfer",     2.0],   // BOSL2's model of the rolled corner; not a datasheet value
    ["hole_spacing",    31.0],   // "31 +/-0.1"; = NEMA 1.725 in pitch circle
    ["hole_dia",         3.0],   // 4-M3
    ["hole_depth",       4.5],   // "4-M3 DEEP 4.5Min"
    ["plinth_dia",      22.0],   // "Phi 22 0/-0.05"; = NEMA pilot 0.8661 in
    ["plinth_h",         2.0],   // BOSL2 table; NEMA bounds pilot depth to 0.76-2.29
    ["shaft_dia",        5.0],   // "Phi 5 0/-0.012"
    ["shaft_len_face",  24.0],   // "24 +/-1", front face to shaft tip
    ["flat_across",      4.5],   // "4.5 +/-0.1" across the D; depth = 5.0 - 4.5 = 0.5
    ["flat_len",        15.0],   // "15 +/-0.25" along the shaft, back from the tip
    ["mass_g",         280.0],   // MotionKing table, Handsontec, StepperOnline
];

// Function: part_nema17_info()
// Description: Key numbers for this part, as a BOSL2 struct (list of [key, value]).
function part_nema17_info() = _N17;

function _n17(k) = struct_val(_N17, k);

// FRAME. Origin at the centre of the overall bounding box so the attachable
// size is the real geometry. _n17_org() is where the natural datum -- the body
// axis at mid body height -- sits in that frame.
function _n17_size() = [
    _n17("body_w"),
    _n17("body_w"),
    _n17("body_h") + _n17("shaft_len_face")
];
function _n17_org() = [0, 0, -_n17("shaft_len_face") / 2];

function _n17_anchors() =
    let(
        o    = _n17_org(),
        face = _n17("body_h") / 2,
        hs   = _n17("hole_spacing") / 2,
        tip  = face + _n17("shaft_len_face"),
        fx   = _n17("flat_across") - _n17("shaft_dia") / 2   // 4.5 - 2.5 = 2.0
    ) [
        named_anchor("mount-plane", o + [0, 0, face], UP, 0),
        named_anchor("shaft-axis",  o + [0, 0, face], UP, 0),
        named_anchor("boss-top",    o + [0, 0, face + _n17("plinth_h")], UP, 0),
        named_anchor("shaft-tip",   o + [0, 0, tip], UP, 0),
        named_anchor("flat-face",   o + [fx, 0, tip - _n17("flat_len") / 2], RIGHT, 0),
        named_anchor("hole-a",      o + [ hs,  hs, face], UP, 0),
        named_anchor("hole-b",      o + [-hs,  hs, face], UP, 0),
        named_anchor("hole-c",      o + [-hs, -hs, face], UP, 0),
        named_anchor("hole-d",      o + [ hs, -hs, face], UP, 0),
        named_anchor("wire-exit",   o + [0, 0, -face], DOWN, 0),
    ];

// ---------------------------------------------------------------------------
// Solid
// ---------------------------------------------------------------------------
// Module: part_nema17()
// Usage: part_nema17([anchor], [spin], [orient]) [children];
// Description:
//   A 17HS4401-class NEMA 17, face and shaft toward +Z, with the D-flat on +X.
//   Origin at the centre of the bounding box; anchor to "mount-plane",
//   "shaft-axis" or "flat-face" to place it. Authored for plain difference():
//   do NOT wrap it in BOSL2 diff() or tag(), which silently union across use<>.
module part_nema17(anchor = CENTER, spin = 0, orient = UP) {
    attachable(anchor, spin, orient,
               size = _n17_size(), anchors = _n17_anchors()) {
        translate(_n17_org()) difference() {
            nema_stepper_motor(size = 17, h = _n17("body_h"),
                               shaft_len = _n17("shaft_len_face"),
                               details = true, anchor = CENTER, $fn = _N17_FN);
            _n17_flat();
        }
        children();
    }
}

// The single D-flat: "4.5 +/-0.1" across, running flat_len back from the tip.
module _n17_flat() {
    d   = _n17("shaft_dia");
    fl  = _n17("flat_len");
    tip = _n17("body_h") / 2 + _n17("shaft_len_face");
    fx  = _n17("flat_across") - d / 2;
    right(fx + d / 2) up(tip - fl / 2 + 0.001)
        cuboid([d, d + 1, fl + 0.002]);
}

// ---------------------------------------------------------------------------
// Mask (negative)
// ---------------------------------------------------------------------------
// Module: part_nema17_mask()
// Usage: difference() { your_part(); part_nema17_mask(clr=0.4, install=DOWN, install_len=50); }
// Description:
//   The pocket for this motor. `clr` is running clearance per side on the body,
//   `bore_clr` is per-side clearance on the round plinth and shaft. The body
//   mask is the full 42.3 mm square, not the chamfered outline, so it also
//   clears a motor whose corners are rolled differently. Set `install_len` to
//   sweep the mask along `install` (a minkowski() with a thin prism, which is
//   exact for concave parts; a hull() of two poses is not). Shares the solid's
//   frame, so any anchor puts the pocket where the motor goes.
module part_nema17_mask(clr = 0.25, bore_clr = 0.25, install = UP, install_len = 0,
                        anchor = CENTER, spin = 0, orient = UP) {
    face = _n17("body_h") / 2;
    attachable(anchor, spin, orient,
               size = _n17_size(), anchors = _n17_anchors()) {
        translate(_n17_org()) _n17_sweep(install, install_len) union() {
            cuboid([_n17("body_w") + 2 * clr, _n17("body_w") + 2 * clr,
                    _n17("body_h") + 2 * clr]);
            up(face - clr)
                cylinder(d = _n17("plinth_dia") + 2 * bore_clr,
                         h = _n17("plinth_h") + clr + bore_clr, $fn = _N17_FN);
            up(face)
                cylinder(d = _n17("shaft_dia") + 2 * bore_clr,
                         h = _n17("shaft_len_face") + bore_clr, $fn = _N17_FN);
        }
        children();
    }
}

module _n17_sweep(dir, len) {
    if (is_undef(dir) || len <= 0) children();
    else {
        u = unit(dir);
        minkowski() {
            children();
            translate(u * len / 2) cyl(d = 0.01, h = len, orient = u, $fn = 4);
        }
    }
}

// Module: part_nema17_mount_holes_mask()
// Usage: difference() { your_part(); part_nema17_mount_holes_mask(d=3.4, h=30); }
// Description:
//   The four M3 mounting holes on the 31 mm square pattern, centred on the
//   mounting plane and running h/2 above and below it. Same frame as
//   part_nema17(). Pick d yourself: 3.4 for an ISO 273 medium clearance hole,
//   3.0 for a tight one.
module part_nema17_mount_holes_mask(d = 3.4, h = 30, anchor = CENTER, spin = 0,
                                    orient = UP) {
    attachable(anchor, spin, orient,
               size = _n17_size(), anchors = _n17_anchors()) {
        translate(_n17_org()) up(_n17("body_h") / 2)
            xcopies(_n17("hole_spacing")) ycopies(_n17("hole_spacing"))
                cyl(d = d, h = h, $fn = _N17_FN);
        children();
    }
}
