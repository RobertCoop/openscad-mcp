// Gear-like disc: a polygon profile extruded, with a central bore.
// The tooth pattern is phased so one tooth tip is centred on the +X axis.
teeth = 12;
root_r = 15;
tip_r = 18;
thickness = 6;
bore_d = 6;

function pt(r, a) = [r * cos(a), r * sin(a)];

module gear_profile() {
    step = 360 / teeth;
    polygon([
        for (i = [0 : teeth - 1])
            let (a = i * step - 15)
                each [
                    pt(root_r, a),
                    pt(root_r, a + 8),
                    pt(tip_r, a + 12),
                    pt(tip_r, a + 18),
                    pt(root_r, a + 22)
                ]
    ]);
}

module gear() {
    difference() {
        linear_extrude(height = thickness) gear_profile();
        translate([0, 0, -1])
            cylinder(h = thickness + 2, d = bore_d, $fn = 64);
    }
}

gear();
