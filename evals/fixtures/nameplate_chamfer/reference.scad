// Blank nameplate with a chamfer around the top face.
plate_x = 80;
plate_y = 30;
plate_z = 4;
chamfer = 1.5;

module nameplate() {
    hull() {
        cube([plate_x, plate_y, plate_z - chamfer]);
        translate([chamfer, chamfer, 0])
            cube([plate_x - 2 * chamfer, plate_y - 2 * chamfer, plate_z]);
    }
}

nameplate();
