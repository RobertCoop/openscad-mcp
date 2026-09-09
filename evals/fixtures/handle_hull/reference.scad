// Drawer pull: a hulled grip bar carried on two posts.
grip_len = 60;
grip_d = 14;
post_d = 10;
post_h = 18;

module handle() {
    union() {
        hull() {
            translate([0, 0, post_h]) sphere(d = grip_d, $fn = 48);
            translate([grip_len, 0, post_h]) sphere(d = grip_d, $fn = 48);
        }
        cylinder(h = post_h, d = post_d, $fn = 64);
        translate([grip_len, 0, 0]) cylinder(h = post_h, d = post_d, $fn = 64);
    }
}

handle();
