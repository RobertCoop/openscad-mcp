// Two-part box and lid; the lid lip fits the opening with a per-side clearance.
box_x = 60;
box_y = 40;
box_z = 25;
wall = 2;
floor_t = 2;
clearance = 0.2;
lid_t = 3;
lip_h = 4;

module box() {
    difference() {
        cube([box_x, box_y, box_z]);
        translate([wall, wall, floor_t])
            cube([box_x - 2 * wall, box_y - 2 * wall, box_z]);
    }
}

module lid() {
    lip_x = box_x - 2 * wall - 2 * clearance;
    lip_y = box_y - 2 * wall - 2 * clearance;
    union() {
        cube([box_x, box_y, lid_t]);
        translate([(box_x - lip_x) / 2, (box_y - lip_y) / 2, lid_t])
            cube([lip_x, lip_y, lip_h]);
    }
}

box();
translate([70, 0, 0]) lid();
