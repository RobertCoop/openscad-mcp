#!/usr/bin/env python3
"""
Comprehensive rendering script for all parts
Generates 6 views for each part: top, left, right, bottom, iso-left, iso-right
"""

import os
import subprocess
from pathlib import Path

# Define parts directory
PARTS_DIR = Path("/home/coop/projects/dealer/poc/dealing_mechanism/parts")
RENDERS_DIR = PARTS_DIR / "comprehensive_renders"
RENDERS_DIR.mkdir(exist_ok=True)

# Define standard camera positions for each view
VIEWS = {
    "top": {"camera": "0,0,200,0,0,0,200", "desc": "Top view"},
    "bottom": {"camera": "0,0,-200,0,0,0,200", "desc": "Bottom view"},
    "left": {"camera": "-200,0,0,0,0,0,200", "desc": "Left side view"},
    "right": {"camera": "200,0,0,0,0,0,200", "desc": "Right side view"},
    "iso_left": {"camera": "-150,-150,150,0,0,0,300", "desc": "Isometric left"},
    "iso_right": {"camera": "150,-150,150,0,0,0,300", "desc": "Isometric right"},
}

# Parts to render with their SCAD files and module calls
PARTS = {
    "part1_scanner_base": {
        "file": "part_1_scanner_base.scad",
        "module": "scanner_base()",
        "center": "0,0,20"
    },
    "part2_card_chamber": {
        "file": "part_2_card_chamber.scad",
        "module": "card_chamber()",
        "center": "0,0,40"
    },
    "part3a_roller_housing": {
        "file": "part_3a_roller_housing.scad",
        "module": "roller_housing()",
        "center": "0,0,10"
    },
    "part3b_motor_mount": {
        "file": "part_3b_motor_mount.scad",
        "module": "motor_mount()",
        "center": "0,0,12"
    },
    "part3c_separator_wedge": {
        "file": "part_3c_separator_wedge.scad",
        "module": "separator_wedge()",
        "center": "0,0,0"
    },
    "part3d_roller": {
        "file": "part_3d_roller.scad",
        "module": "roller_assembly()",
        "center": "0,0,0"
    },
    "part4_pressure_plate": {
        "file": "part_4_pressure_plate.scad",
        "module": "pressure_plate()",
        "center": "0,0,0"
    }
}

def render_view(scad_file, output_file, camera_pos, center, module_call):
    """Render a single view of a part"""
    # Update camera position with proper center
    cam_parts = camera_pos.split(',')
    center_parts = center.split(',')
    camera = f"{cam_parts[0]},{cam_parts[1]},{cam_parts[2]},{center_parts[0]},{center_parts[1]},{center_parts[2]},{cam_parts[6]}"
    
    cmd = [
        "openscad",
        "-o", str(output_file),
        "--camera", camera,
        "--imgsize", "1024,768",
        "--colorscheme", "Tomorrow Night",
        "--autocenter",
        "--viewall",
        "-D", "$fn=60",
        "-D", module_call,
        str(scad_file)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PARTS_DIR))
        if result.returncode == 0:
            print(f"  ✓ {output_file.name}")
        else:
            print(f"  ✗ {output_file.name}: {result.stderr[:100]}")
    except Exception as e:
        print(f"  ✗ {output_file.name}: {str(e)}")

def render_all_views_for_part(part_name, part_info):
    """Render all 6 views for a single part"""
    print(f"\n{'='*60}")
    print(f"Rendering {part_name}")
    print(f"{'='*60}")
    
    scad_file = PARTS_DIR / part_info["file"]
    if not scad_file.exists():
        print(f"  ✗ File not found: {scad_file}")
        return
    
    part_dir = RENDERS_DIR / part_name
    part_dir.mkdir(exist_ok=True)
    
    for view_name, view_info in VIEWS.items():
        output_file = part_dir / f"{view_name}.png"
        render_view(
            scad_file,
            output_file,
            view_info["camera"],
            part_info["center"],
            part_info["module"]
        )

def render_assembly():
    """Render all views of the complete assembly"""
    print(f"\n{'='*60}")
    print(f"Rendering Full Assembly")
    print(f"{'='*60}")
    
    # Create assembly test file
    assembly_file = PARTS_DIR / "full_assembly_test.scad"
    assembly_content = """
// Full assembly test
$fn = 60;

use <part_1_scanner_base.scad>
use <part_2_card_chamber.scad>
use <part_3a_roller_housing.scad>
use <part_3b_motor_mount.scad>
use <part_3c_separator_wedge.scad>
use <part_3d_roller.scad>
use <part_4_pressure_plate.scad>

// Assembly
color("Gray") scanner_base();
translate([0, 0, 40]) color("Blue") card_chamber();
translate([0, 30, 50]) color("Orange") roller_housing();
translate([-50, 30, 50]) color("DarkGray") motor_mount();
translate([0, 25, 48]) color("Red") separator_wedge();
translate([0, 30, 50]) rotate([90, 0, 0]) color("Orange", 0.8) roller_assembly();
translate([0, 0, 115]) color("Green") pressure_plate();
"""
    
    with open(assembly_file, 'w') as f:
        f.write(assembly_content)
    
    assembly_dir = RENDERS_DIR / "full_assembly"
    assembly_dir.mkdir(exist_ok=True)
    
    # Assembly has different camera distances
    assembly_views = {
        "top": {"camera": "0,0,400,0,0,60,500", "desc": "Top view"},
        "bottom": {"camera": "0,0,-400,0,0,60,500", "desc": "Bottom view"},
        "left": {"camera": "-400,0,60,0,0,60,500", "desc": "Left side view"},
        "right": {"camera": "400,0,60,0,0,60,500", "desc": "Right side view"},
        "iso_left": {"camera": "-300,-300,250,0,0,60,500", "desc": "Isometric left"},
        "iso_right": {"camera": "300,-300,250,0,0,60,500", "desc": "Isometric right"},
    }
    
    for view_name, view_info in assembly_views.items():
        output_file = assembly_dir / f"{view_name}.png"
        cmd = [
            "openscad",
            "-o", str(output_file),
            "--camera", view_info["camera"],
            "--imgsize", "1024,768",
            "--colorscheme", "Tomorrow Night",
            "--autocenter",
            "--viewall",
            str(assembly_file)
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PARTS_DIR))
            if result.returncode == 0:
                print(f"  ✓ {output_file.name}")
            else:
                print(f"  ✗ {output_file.name}")
        except Exception as e:
            print(f"  ✗ {output_file.name}: {str(e)}")

def main():
    print("Starting comprehensive render generation...")
    print(f"Output directory: {RENDERS_DIR}")
    
    # Render all individual parts
    for part_name, part_info in PARTS.items():
        render_all_views_for_part(part_name, part_info)
    
    # Render assembly
    render_assembly()
    
    print(f"\n{'='*60}")
    print("Rendering complete!")
    print(f"All renders saved to: {RENDERS_DIR}")
    
    # List all generated files
    total_files = 0
    for part_dir in RENDERS_DIR.iterdir():
        if part_dir.is_dir():
            files = list(part_dir.glob("*.png"))
            print(f"\n{part_dir.name}: {len(files)} renders")
            total_files += len(files)
    
    print(f"\nTotal renders generated: {total_files}")

if __name__ == "__main__":
    main()