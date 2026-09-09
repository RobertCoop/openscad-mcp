// Peg shown inserted in its hole; the clearance must keep the parts disjoint.
block_x = 30;
block_y = 30;
block_z = 10;
peg_d = 8.0;
peg_h = 20;
hole_d = 8.4;

module hole_block() {
    difference() {
        translate([-block_x / 2, -block_y / 2, 0])
            cube([block_x, block_y, block_z]);
        translate([0, 0, -1])
            cylinder(h = block_z + 2, d = hole_d, $fn = 64);
    }
}

module peg() {
    cylinder(h = peg_h, d = peg_d, $fn = 64);
}

hole_block();
peg();
