// Fully sealed enclosure: one outer shell around one enclosed void.
outer_x = 50;
outer_y = 40;
outer_z = 30;
wall = 2.5;

module enclosure() {
    difference() {
        cube([outer_x, outer_y, outer_z], center = true);
        cube([outer_x - 2 * wall, outer_y - 2 * wall, outer_z - 2 * wall], center = true);
    }
}

enclosure();
