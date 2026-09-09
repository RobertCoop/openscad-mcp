// Rectangular spacer plate, one corner at the origin.
plate_x = 60;
plate_y = 40;
plate_z = 5;

module spacer() {
    cube([plate_x, plate_y, plate_z]);
}

spacer();
