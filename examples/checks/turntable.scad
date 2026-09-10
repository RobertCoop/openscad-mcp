// Sample model for examples/checks/turntable.yaml.
//
// A small hand turntable: a base plate with a centre post, a platter that
// turns on that post over a purchased thrust washer, and an L-bracket bolted
// to the base with an M3 screw. Dimensions are millimetres.
//
// The check file drives each module separately, so the top-level
// instantiations at the bottom of this file are only for `render` and for
// opening the file in the OpenSCAD GUI.

$fn = 64;

BASE_D    = 90;    // base plate diameter
BASE_H    = 6;     // base plate thickness
POST_D    = 10;    // centre post diameter
POST_TOP  = 36;    // centre post top, from the base underside
BOLT_D    = 3.2;   // clearance hole for an M3 screw
BOLT_R    = 36;    // bolt circle radius

WASHER_ID = 10.4;  // purchased thrust washer
WASHER_OD = 20;
WASHER_T  = 2;

PLATTER_D = 48;    // platter diameter
PLATTER_H = 5;     // platter thickness
PLATTER_Z = 8;     // platter underside = base top + washer thickness
BORE_D    = 10.4;  // 0.2 mm radial running clearance on the post
LUG_L     = 5;     // finger lug on the platter rim
LUG_W     = 8;
LUG_H     = 4;

BRACKET_W = 12;    // bracket footprint, square
BRACKET_T = 3;     // bracket wall thickness
BRACKET_H = 14;    // bracket upright height

module base() {
    difference() {
        union() {
            cylinder(d = BASE_D, h = BASE_H);
            cylinder(d = POST_D, h = POST_TOP);
        }
        translate([BOLT_R, 0, -1]) cylinder(d = BOLT_D, h = BASE_H + 2);
    }
}

module platter() {
    difference() {
        union() {
            cylinder(d = PLATTER_D, h = PLATTER_H);
            translate([PLATTER_D / 2 - 1, -LUG_W / 2, 0]) cube([LUG_L, LUG_W, LUG_H]);
        }
        translate([0, 0, -1]) cylinder(d = BORE_D, h = PLATTER_H + 2);
    }
}

module bracket() {
    difference() {
        union() {
            cube([BRACKET_W, BRACKET_W, BRACKET_T]);
            translate([0, BRACKET_W - BRACKET_T, 0]) cube([BRACKET_W, BRACKET_T, BRACKET_H]);
        }
        translate([BRACKET_W / 2, BRACKET_W / 2, -1]) cylinder(d = BOLT_D, h = BRACKET_T + 2);
    }
}

// Purchased parts. Modelled only so the checks can see their envelopes.
module washer() {
    difference() {
        cylinder(d = WASHER_OD, h = WASHER_T);
        translate([0, 0, -1]) cylinder(d = WASHER_ID, h = WASHER_T + 2);
    }
}

module m3_screw() {          // M3 x 8 pan head, origin at the underside of the head
    cylinder(d = 5.5, h = 2);
    translate([0, 0, -8]) cylinder(d = 3.0, h = 8);
}

base();
translate([0, 0, BASE_H]) washer();
translate([0, 0, PLATTER_Z]) platter();
translate([BOLT_R - BRACKET_W / 2, -BRACKET_W / 2, BASE_H]) bracket();
translate([BOLT_R, 0, BASE_H + BRACKET_T]) m3_screw();
