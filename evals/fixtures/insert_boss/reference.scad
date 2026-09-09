// Base plate with a boss bored for an M3 heat-set threaded insert.
plate_x = 30;
plate_y = 30;
plate_t = 3;
boss_d = 8;
boss_h = 10;
insert_hole_d = 4.0;
insert_depth = 8;

module boss_plate() {
    difference() {
        union() {
            translate([-plate_x / 2, -plate_y / 2, 0])
                cube([plate_x, plate_y, plate_t]);
            cylinder(h = plate_t + boss_h, d = boss_d, $fn = 64);
        }
        translate([0, 0, plate_t + boss_h - insert_depth])
            cylinder(h = insert_depth + 1, d = insert_hole_d, $fn = 64);
    }
}

boss_plate();
