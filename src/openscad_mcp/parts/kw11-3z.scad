//////////////////////////////////////////////////////////////////////////////
// openscad-mcp purchased-parts catalog
// KW11-3Z / KW12-3 miniature snap-action microswitch (SPDT, 3 terminals)
//
// GENERATED FILE. The numbers live in src/openscad_mcp/parts_catalog.py.
//
// Source: Zhejiang Zhongxun Electronics Co. Ltd, the UL file holder for type
// KW11-3Z (UL E203463). Outline drawing:
//   http://www.zxgroup.com/upload/201611/28/201611280805157094.jpg
//   lever/plunger option table  .../201611280804568024.jpg
//   ratings table               .../201611280807259956.jpg
//   product page http://www.zxgroup.com/zxdzwx/showproducts.aspx?id=838
// Cross-check on the 9.5 mm mounting pitch, which is the shared footprint of
// this whole family: Omron SS series datasheet X303-E-1,
//   https://www.mouser.com/datasheet/2/307/SS_1110-14757.pdf
//   "Two, 2.4-dia. mounting holes or M2.3 screw holes 9.5 +/-0.1"
// KW12-3 shares the body: https://www.mouser.com/datasheet/2/1398/Soldered_101411_microswitch_kw12_3-3532466.pdf
//
// Dimensions are facts and are not copyrightable. This module is original work,
// MIT licensed with the rest of openscad-mcp.
//
// SIZE CORRECTION. This part is 20 x 6.4 x 9.8 mm. It is often described as
// "28.5 x 16 x 10", which is the larger V-15 / KW7 class limit switch, a
// different family. Marketplace listings mix the two up constantly.
//
// The KW11-3Z is NOT dimensionally identical to the Omron it copies: 20 vs
// 19.8 long, 2.5 vs 2.35 mounting holes, 9.8/10.7 vs 10.2 high.
//////////////////////////////////////////////////////////////////////////////

// BOSL2 is included unconditionally, which is safe under BOTH `include <>` and
// `use <>` of this file. OpenSCAD's include is a preprocessor directive and
// cannot be made conditional.
include <BOSL2/std.scad>
assert(is_list(BOSL_VERSION) && BOSL_VERSION[0] >= 2,
       "kw11-3z.scad needs BOSL2 2.x: include <BOSL2/std.scad> must resolve.");

_KW_FN = 64;

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
_KW = [
    ["body_l",          20.0],   // drawing "20 +/-0.2", along X
    ["body_t",           6.4],   // drawing "6.4 +/-0.1", across Y, the mounting axis
    ["body_h",           9.8],   // drawing "9.8 +/-0.1", case only
    ["free_h",          10.7],   // drawing "10.7 +/-0.1", to the plunger free position
    ["plunger_dia",      4.2],   // drawing "4.2"
    ["hole_dia",         2.5],   // drawing "2-phi 2.5"
    ["hole_spacing",     9.5],   // drawing "9.5 +/-0.1"; Omron confirms the pitch
    ["hole_z",           2.8],   // drawing "2.8 +/-0.1", hole centre above the base
    ["term_com_no",      8.8],   // drawing "8.8 +/-0.1", COM to NO
    ["term_com_nc",     16.0],   // drawing "16 +/-0.2", COM to NC
    ["term_t",           0.5],   // Omron "Terminal plate thickness is 0.5 mm for all models"
    ["operating_pt",     0.5],   // drawing "0.5 +0.3 -0.2" pre-travel
];

// Function: part_kw11_3z_info()
// Description: Key numbers for this part, as a BOSL2 struct (list of [key, value]).
function part_kw11_3z_info() = _KW;

function _kw(k) = struct_val(_KW, k);

// FRAME. Origin at the centre of the overall bounding box. The natural datum is
// the centre of the 20 x 6.4 x 9.8 case; the plunger adds 0.9 mm on +Z, so the
// two differ by 0.45 mm.
//
// SYMMETRY ASSUMPTION, see `verify` in parts_catalog.py: the drawing dimensions
// the 9.5 mm hole pitch and the 8.8/16 mm terminal pitches but NOT where either
// group sits along the 20 mm body, and it does not dimension the plunger's X
// position at all. This model centres all three groups on the body. Measure your
// switch before you cut a panel.
function _kw_size() = [_kw("body_l"), _kw("body_t"), _kw("free_h")];
function _kw_org()  = [0, 0, -(_kw("free_h") - _kw("body_h")) / 2];

function _kw_anchors() =
    let(
        o    = _kw_org(),
        hh   = _kw("body_h") / 2,
        side = _kw("body_t") / 2,
        hs   = _kw("hole_spacing") / 2,
        hz   = -hh + _kw("hole_z"),
        tip  = -hh + _kw("free_h"),
        com  = -_kw("term_com_nc") / 2
    ) [
        named_anchor("mount-plane",  o + [0, -side, 0], FWD, 0),
        named_anchor("hole-a",       o + [-hs, -side, hz], FWD, 0),
        named_anchor("hole-b",       o + [ hs, -side, hz], FWD, 0),
        named_anchor("plunger-tip",  o + [0, 0, tip], UP, 0),
        named_anchor("operating-pt", o + [0, 0, tip - _kw("operating_pt")], UP, 0),
        named_anchor("term-com",     o + [com, 0, -hh], DOWN, 0),
        named_anchor("term-no",      o + [com + _kw("term_com_no"), 0, -hh], DOWN, 0),
        named_anchor("term-nc",      o + [com + _kw("term_com_nc"), 0, -hh], DOWN, 0),
    ];

// ---------------------------------------------------------------------------
// Solid
// ---------------------------------------------------------------------------
// Module: part_kw11_3z()
// Usage: part_kw11_3z([anchor], [spin], [orient], [lever]) [children];
// Description:
//   The switch case with its plunger up +Z and its mounting face on -Y. The
//   terminals are NOT modelled: their length below the case is not dimensioned
//   on any drawing found, and inventing it would be worse than leaving it out.
//   Use the "term-com"/"term-no"/"term-nc" anchors for their positions and the
//   mask's `term_len` for their keep-out.
//   `lever` is the length of a straight lever laid along +X on top of the
//   plunger; 0 (default) is the plunger-only variant, which is what the declared
//   envelope describes. The catalogued lever options are 14.4, 16.8, 18, 22, 24,
//   31.5 and 56 mm. A non-zero lever makes the part longer than its envelope.
//   Authored for plain difference(): do NOT wrap it in BOSL2 diff() or tag().
module part_kw11_3z(anchor = CENTER, spin = 0, orient = UP, lever = 0) {
    hh = _kw("body_h") / 2;
    attachable(anchor, spin, orient,
               size = _kw_size(), anchors = _kw_anchors()) {
        translate(_kw_org()) union() {
            color("black") _kw_case(0);
            color("silver") _kw_plunger(0);
            if (lever > 0)
                color("silver")
                    up(-hh + _kw("free_h") + _kw("term_t") / 2)
                        right(lever / 2 - _kw("plunger_dia") / 2)
                            cuboid([lever, _kw("body_t") * 0.7, _kw("term_t")]);
        }
        children();
    }
}

module _kw_case(g) {
    cuboid([_kw("body_l") + 2 * g, _kw("body_t") + 2 * g, _kw("body_h") + 2 * g]);
}

module _kw_plunger(g) {
    hh = _kw("body_h") / 2;
    up(hh) cyl(d = _kw("plunger_dia") + 2 * g,
               h = _kw("free_h") - _kw("body_h") + g, anchor = BOTTOM, $fn = _KW_FN);
}

// ---------------------------------------------------------------------------
// Mask (negative)
// ---------------------------------------------------------------------------
// Module: part_kw11_3z_mask()
// Usage: difference() { your_part(); part_kw11_3z_mask(clr=0.3, term_len=6); }
// Description:
//   The pocket for the switch. `clr` is running clearance per side on the case,
//   `bore_clr` on the round plunger. `term_len` adds a keep-out prism below the
//   case for the three terminals and their wires -- it defaults to 0 because the
//   terminal length is unsourced, so you choose it. `travel` extends the plunger
//   bore above the free position so an actuator has somewhere to go. Set
//   `install_len` to sweep the mask along `install`. Shares the solid's frame.
module part_kw11_3z_mask(clr = 0.25, bore_clr = 0.25, install = UP, install_len = 0,
                         term_len = 0, travel = 0, anchor = CENTER, spin = 0,
                         orient = UP) {
    hh = _kw("body_h") / 2;
    attachable(anchor, spin, orient,
               size = _kw_size(), anchors = _kw_anchors()) {
        translate(_kw_org()) _kw_sweep(install, install_len) union() {
            _kw_case(clr);
            up(hh) cyl(d = _kw("plunger_dia") + 2 * bore_clr,
                       h = _kw("free_h") - _kw("body_h") + clr + travel,
                       anchor = BOTTOM, $fn = _KW_FN);
            if (term_len > 0)
                down(hh + term_len / 2 - 0.001)
                    cuboid([_kw("term_com_nc") + 2 * _kw("plunger_dia"),
                            _kw("body_t") + 2 * clr, term_len + 0.002]);
        }
        children();
    }
}

module _kw_sweep(dir, len) {
    if (is_undef(dir) || len <= 0) children();
    else {
        u = unit(dir);
        minkowski() {
            children();
            translate(u * len / 2) cyl(d = 0.01, h = len, orient = u, $fn = 4);
        }
    }
}

// Module: part_kw11_3z_mount_holes_mask()
// Usage: difference() { your_part(); part_kw11_3z_mount_holes_mask(d=2.5, h=20); }
// Description:
//   The two mounting holes, axis along Y (the switch bolts through its 6.4 mm
//   face), centred on the mounting face and running h/2 either side. Same frame
//   as part_kw11_3z(). M2 or M2.5 screws; the holes are 2.5 mm in the switch.
module part_kw11_3z_mount_holes_mask(d = 2.5, h = 20, anchor = CENTER, spin = 0,
                                     orient = UP) {
    hh = _kw("body_h") / 2;
    attachable(anchor, spin, orient,
               size = _kw_size(), anchors = _kw_anchors()) {
        translate(_kw_org()) up(-hh + _kw("hole_z"))
            xcopies(_kw("hole_spacing")) ycyl(d = d, h = h, $fn = _KW_FN);
        children();
    }
}
