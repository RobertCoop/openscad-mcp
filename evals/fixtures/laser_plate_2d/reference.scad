// Flat 2D profile for laser cutting; deliberately not extruded.
plate_x = 120;
plate_y = 80;
hole_d = 5;
inset = 10;
slot_x = 40;
slot_y = 20;

module plate_profile() {
    difference() {
        square([plate_x, plate_y]);
        for (x = [inset, plate_x - inset])
            for (y = [inset, plate_y - inset])
                translate([x, y]) circle(d = hole_d, $fn = 64);
        translate([(plate_x - slot_x) / 2, (plate_y - slot_y) / 2])
            square([slot_x, slot_y]);
    }
}

plate_profile();
