// Two interlocked chain links in the print-in-place position.
major_r = 10;
minor_r = 2.5;

module link() {
    rotate_extrude($fn = 64)
        translate([major_r, 0, 0])
            circle(r = minor_r, $fn = 32);
}

module link_a() {
    link();
}

module link_b() {
    translate([major_r, 0, 0])
        rotate([90, 0, 0])
            link();
}

link_a();
link_b();
