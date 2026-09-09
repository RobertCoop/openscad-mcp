// Plate with four 90-degree countersunk holes for M4 flat head screws.
plate_x = 60;
plate_y = 40;
plate_z = 6;
hole_d = 4.3;
head_d = 8.6;
cs_depth = (head_d - hole_d) / 2;   // 90 degree included angle

module countersunk_hole() {
    translate([0, 0, -1])
        cylinder(h = plate_z + 2, d = hole_d, $fn = 64);
    translate([0, 0, plate_z - cs_depth])
        cylinder(h = cs_depth + 1, d1 = hole_d, d2 = head_d + 2, $fn = 64);
}

module plate() {
    difference() {
        cube([plate_x, plate_y, plate_z]);
        for (x = [12, plate_x - 12])
            for (y = [10, plate_y - 10])
                translate([x, y, 0]) countersunk_hole();
    }
}

plate();
