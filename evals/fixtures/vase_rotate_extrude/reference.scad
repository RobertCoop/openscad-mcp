// Open vase produced by revolving a wall profile.
base_r = 22;
top_r = 18;
height = 50;
wall = 2;
floor_t = 3;

module vase() {
    rotate_extrude($fn = 96)
        polygon([
            [0, 0],
            [base_r, 0],
            [top_r, height],
            [top_r - wall, height],
            [base_r - wall - 0.4, floor_t],
            [0, floor_t]
        ]);
}

vase();
