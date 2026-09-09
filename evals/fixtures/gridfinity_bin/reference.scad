// Gridfinity-style 1x1 bin: 42 mm grid pitch, 41.5 mm footprint, open top.
grid_pitch = 42;
size = 41.5;
corner_r = 4;
height = 40;
wall = 1.2;
floor_t = 2;

module footprint(inset = 0) {
    offset(r = corner_r - inset, $fn = 48)
        square([size - 2 * corner_r, size - 2 * corner_r], center = true);
}

module bin() {
    difference() {
        linear_extrude(height = height) footprint();
        translate([0, 0, floor_t])
            linear_extrude(height = height) footprint(inset = wall);
    }
}

bin();
