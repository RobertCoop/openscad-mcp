// L-shaped mounting bracket with two holes per flange.
len_x = 60;
depth_y = 40;
height_z = 40;
wall = 5;
hole_d = 5.5;

module bracket() {
    difference() {
        union() {
            cube([len_x, depth_y, wall]);      // horizontal flange
            cube([len_x, wall, height_z]);     // vertical flange
        }
        for (x = [15, 45]) {
            translate([x, 25, -1])
                cylinder(h = wall + 2, d = hole_d, $fn = 64);
            translate([x, wall + 1, 25])
                rotate([90, 0, 0])
                    cylinder(h = wall + 2, d = hole_d, $fn = 64);
        }
    }
}

bracket();
