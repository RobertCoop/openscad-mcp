//////////////////////////////////////////////////////////////////////////////
// openscad-mcp purchased-parts catalog
// TCRT5000 reflective infrared sensor breakout module
//
// GENERATED FILE. The numbers live in src/openscad_mcp/parts_catalog.py.
//
// Sensor package: Vishay TCRT5000 datasheet, document 83760 rev 1.7:
//   https://www.vishay.com/docs/83760/tcrt5000.pdf
//   "Dimensions (L x W x H in mm): 10.2 x 5.8 x 7"
//   "Peak operating distance: 2.5 mm"
//   "Operating range within > 20 % relative collector current: 0.2 mm to 15 mm"
// Board: Handsontec TCRT5000 module page,
//   https://handsontec.com/index.php/product/tcrt5000-infrared-ir-line-detection-sensor-module/
//   "Board size: (31 x 14)mm."  "Mounting Hole: 3mm."  "Operating Voltage: 3.3~5VDC."
//
// Dimensions are facts and are not copyrightable. This module is original work,
// MIT licensed with the rest of openscad-mcp.
//
// BOARD OUTLINES VARY BY VENDOR: 31 x 14, 32 x 14, 34 x 10 and 35 x 10 mm were
// all found, and the mounting hole count is 1 on some boards and 2 (on 28 mm
// centres) on others. This file models the 31 x 14 single-hole Handsontec
// variant. Pass board_l, board_w, hole_x etc. if yours differs -- and measure,
// because the placement of the hole and of the sensor on the board is on the
// `verify` list in parts_catalog.py, not sourced.
//
// The centimetre detection ranges quoted by module vendors (2-8 cm and similar)
// contradict Vishay. Trust the datasheet: 0.2-15 mm, best at 2.5 mm.
//////////////////////////////////////////////////////////////////////////////

// BOSL2 is included unconditionally, which is safe under BOTH `include <>` and
// `use <>` of this file. OpenSCAD's include is a preprocessor directive and
// cannot be made conditional.
include <BOSL2/std.scad>
assert(is_list(BOSL_VERSION) && BOSL_VERSION[0] >= 2,
       "tcrt5000-module.scad needs BOSL2 2.x: include <BOSL2/std.scad> must resolve.");

_TC_FN = 64;

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------
_TC = [
    ["board_l",         31.0],   // Handsontec "Board size: (31 x 14)mm"
    ["board_w",         14.0],   // as above
    ["board_t",          1.6],   // NOT sourced; the usual FR-4 thickness
    ["hole_dia",         3.0],   // Handsontec "Mounting Hole: 3mm"
    ["hole_x",         -12.5],   // NOT sourced; 3.0 mm in from the -X edge
    ["sensor_l",        10.2],   // Vishay 83760
    ["sensor_w",         5.8],   // Vishay 83760
    ["sensor_h",         7.0],   // Vishay 83760
    ["sensor_x",         0.0],   // NOT sourced; this model centres it
    ["peak_dist",        2.5],   // Vishay "Peak operating distance: 2.5 mm"
    ["range_min",        0.2],   // Vishay operating range low end
    ["range_max",       15.0],   // Vishay operating range high end
    ["header_pitch",     2.54],  // 4-pin 0.1 in header, VCC/GND/DO/AO
];

// Function: part_tcrt5000_module_info()
// Description: Key numbers for this part, as a BOSL2 struct (list of [key, value]).
function part_tcrt5000_module_info() = _TC;

function _tc(k) = struct_val(_TC, k);

// FRAME. Origin at the centre of the overall bounding box. The natural datum is
// the centre of the board; the sensor sits on the +Z face, so the two differ by
// half the sensor height.
//
// The pin header is NOT modelled: its position and height are not published by
// any vendor found, and this board's header is sometimes straight and sometimes
// right-angle. Leave room for it yourself.
function _tc_size() = [_tc("board_l"), _tc("board_w"), _tc("board_t") + _tc("sensor_h")];
function _tc_org()  = [0, 0, -_tc("sensor_h") / 2];

function _tc_anchors() =
    let(
        o  = _tc_org(),
        bt = _tc("board_t") / 2,
        sf = _tc("board_t") / 2 + _tc("sensor_h"),
        sx = _tc("sensor_x")
    ) [
        named_anchor("mount-plane",   o + [0, 0, -bt], DOWN, 0),  // solder side, sits on a boss
        named_anchor("board-top",     o + [0, 0,  bt], UP, 0),
        named_anchor("hole-a",        o + [_tc("hole_x"), 0, bt], UP, 0),
        named_anchor("sensor-face",   o + [sx, 0, sf], UP, 0),     // the optical face
        named_anchor("sense-point",   o + [sx, 0, sf + _tc("peak_dist")], UP, 0),
        named_anchor("sense-far",     o + [sx, 0, sf + _tc("range_max")], UP, 0),
    ];

// ---------------------------------------------------------------------------
// Solid
// ---------------------------------------------------------------------------
// Module: part_tcrt5000_module()
// Usage: part_tcrt5000_module([anchor], [spin], [orient]) [children];
// Description:
//   The board with its mounting hole, plus the TCRT5000 package standing on the
//   +Z face looking up +Z. The "sense-point" anchor is 2.5 mm above the optical
//   face, which is where Vishay says the reflector should be. Authored for plain
//   difference(): do NOT wrap it in BOSL2 diff() or tag().
module part_tcrt5000_module(anchor = CENTER, spin = 0, orient = UP) {
    attachable(anchor, spin, orient,
               size = _tc_size(), anchors = _tc_anchors()) {
        translate(_tc_org()) union() {
            color("darkgreen") difference() {
                _tc_board(0);
                right(_tc("hole_x"))
                    cyl(d = _tc("hole_dia"), h = _tc("board_t") + 1, $fn = _TC_FN);
            }
            color("dimgray") _tc_sensor(0);
        }
        children();
    }
}

module _tc_board(g) {
    cuboid([_tc("board_l") + 2 * g, _tc("board_w") + 2 * g, _tc("board_t") + 2 * g]);
}

module _tc_sensor(g) {
    right(_tc("sensor_x")) up(_tc("board_t") / 2)
        cuboid([_tc("sensor_l") + 2 * g, _tc("sensor_w") + 2 * g,
                _tc("sensor_h") + g], anchor = BOTTOM);
}

// ---------------------------------------------------------------------------
// Mask (negative)
// ---------------------------------------------------------------------------
// Module: part_tcrt5000_module_mask()
// Usage: difference() { your_part(); part_tcrt5000_module_mask(clr=0.3, view_len=15); }
// Description:
//   The pocket for the module. `clr` is running clearance per side. `bore_clr`
//   grows the sensor package specifically, which is where you want a little
//   extra so the lenses never touch anything. `view_len` opens an optical window
//   that far above the sensor face -- set it to your standoff so the sensor can
//   actually see out; it defaults to 0. Set `install_len` to sweep the mask
//   along `install`. Shares the solid's frame.
module part_tcrt5000_module_mask(clr = 0.25, bore_clr = 0.25, install = UP,
                                 install_len = 0, view_len = 0, anchor = CENTER,
                                 spin = 0, orient = UP) {
    attachable(anchor, spin, orient,
               size = _tc_size(), anchors = _tc_anchors()) {
        translate(_tc_org()) _tc_sweep(install, install_len) union() {
            _tc_board(clr);
            _tc_sensor(bore_clr);
            if (view_len > 0)
                right(_tc("sensor_x"))
                    up(_tc("board_t") / 2 + _tc("sensor_h") - 0.001)
                        cuboid([_tc("sensor_l") + 2 * bore_clr,
                                _tc("sensor_w") + 2 * bore_clr,
                                view_len + 0.002], anchor = BOTTOM);
        }
        children();
    }
}

module _tc_sweep(dir, len) {
    if (is_undef(dir) || len <= 0) children();
    else {
        u = unit(dir);
        minkowski() {
            children();
            translate(u * len / 2) cyl(d = 0.01, h = len, orient = u, $fn = 4);
        }
    }
}

// Module: part_tcrt5000_module_mount_holes_mask()
// Usage: difference() { your_part(); part_tcrt5000_module_mount_holes_mask(d=2.5, h=12); }
// Description:
//   The single mounting hole, centred on the board and running h/2 either side.
//   Same frame as part_tcrt5000_module(). d = 3.0 for an M3 clearance, 2.5 to
//   tap M3 into plastic, 2.4 for a heat-set M3 insert boss.
module part_tcrt5000_module_mount_holes_mask(d = 3.0, h = 12, anchor = CENTER,
                                             spin = 0, orient = UP) {
    attachable(anchor, spin, orient,
               size = _tc_size(), anchors = _tc_anchors()) {
        translate(_tc_org()) right(_tc("hole_x")) cyl(d = d, h = h, $fn = _TC_FN);
        children();
    }
}
