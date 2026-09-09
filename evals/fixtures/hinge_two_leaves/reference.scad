// Two-leaf hinge printed in the assembled position.
// Leaf A owns the outer knuckles, leaf B the middle one; an axial gap keeps
// the knuckles clear of each other and a shared bore accepts the pin.
width_x = 36;
knuckle_r = 4;
bore_d = 4.2;
knuckle_gap = 0.3;
leaf_t = 3;
leaf_depth = 20;
web_depth = 7;

module bore() {
    translate([-1, 0, 0])
        rotate([0, 90, 0])
            cylinder(h = width_x + 2, d = bore_d, $fn = 48);
}

module knuckle(x0, x1) {
    translate([x0, 0, 0])
        rotate([0, 90, 0])
            cylinder(h = x1 - x0, r = knuckle_r, $fn = 64);
}

module leaf_a() {
    difference() {
        union() {
            knuckle(0, width_x / 3);
            knuckle(2 * width_x / 3, width_x);
            translate([0, -(knuckle_r + 2 + leaf_depth), -leaf_t / 2])
                cube([width_x, leaf_depth, leaf_t]);
            for (x0 = [0, 2 * width_x / 3])
                translate([x0, -(knuckle_r + 3), -leaf_t / 2])
                    cube([width_x / 3, web_depth, leaf_t]);
        }
        bore();
    }
}

module leaf_b() {
    difference() {
        union() {
            knuckle(width_x / 3 + knuckle_gap, 2 * width_x / 3 - knuckle_gap);
            translate([0, knuckle_r + 2, -leaf_t / 2])
                cube([width_x, leaf_depth, leaf_t]);
            translate([width_x / 3 + knuckle_gap, -knuckle_r + 1, -leaf_t / 2])
                cube([width_x / 3 - 2 * knuckle_gap, web_depth + 3, leaf_t]);
        }
        bore();
    }
}

leaf_a();
leaf_b();
