#!/usr/bin/env python3
"""
Comprehensive part rendering script
Generates 4 strategic views for each part to verify functionality
"""

import sys
import os
from pathlib import Path
sys.path.append(str(Path(__file__).parent))
from scad_renderer import ScadRenderer

# Define viewpoints for each part
# Each part gets 4 views: Overview, Critical Feature, Interface, Detail

PART_VIEWS = {
    "part_1_scanner_base": [
        {
            "filename": "p1_isometric.png",
            "camera_pos": (150, 150, 100),
            "camera_look_at": (0, 0, 20),
            "description": "Overall view"
        },
        {
            "filename": "p1_bottom.png",
            "camera_pos": (0, 0, -100),
            "camera_look_at": (0, 0, 20),
            "camera_up": (0, -1, 0),
            "description": "QR window from below"
        },
        {
            "filename": "p1_top.png",
            "camera_pos": (0, 0, 150),
            "camera_look_at": (0, 0, 20),
            "description": "Mounting posts and guides"
        },
        {
            "filename": "p1_section.png",
            "camera_pos": (0, -150, 40),
            "camera_look_at": (0, 0, 20),
            "description": "Cross-section view"
        }
    ],
    
    "part_2_card_chamber": [
        {
            "filename": "p2_isometric.png",
            "camera_pos": (120, 120, 120),
            "camera_look_at": (0, 0, 40),
            "description": "Overall frame structure"
        },
        {
            "filename": "p2_bottom.png",
            "camera_pos": (0, 0, -80),
            "camera_look_at": (0, 0, 40),
            "camera_up": (0, -1, 0),
            "description": "Bottom opening for QR"
        },
        {
            "filename": "p2_front.png",
            "camera_pos": (0, -150, 40),
            "camera_look_at": (0, 0, 40),
            "description": "Exit chute and frame"
        },
        {
            "filename": "p2_detail.png",
            "camera_pos": (50, 50, 70),
            "camera_look_at": (0, 44, 75),
            "description": "Spring mount detail"
        }
    ],
    
    "part_3a_roller_housing": [
        {
            "filename": "p3a_isometric.png",
            "camera_pos": (80, 80, 60),
            "camera_look_at": (0, 0, 10),
            "description": "Overall housing"
        },
        {
            "filename": "p3a_top.png",
            "camera_pos": (0, 0, 80),
            "camera_look_at": (0, 0, 10),
            "description": "Bearing seats and card path"
        },
        {
            "filename": "p3a_front.png",
            "camera_pos": (0, -80, 10),
            "camera_look_at": (0, 0, 10),
            "description": "Roller cavity"
        },
        {
            "filename": "p3a_bottom.png",
            "camera_pos": (0, 0, -60),
            "camera_look_at": (0, 0, 10),
            "camera_up": (0, -1, 0),
            "description": "Wedge mount interface"
        }
    ],
    
    "part_3b_motor_mount": [
        {
            "filename": "p3b_isometric.png",
            "camera_pos": (60, 60, 50),
            "camera_look_at": (0, 0, 12),
            "description": "Overall mount"
        },
        {
            "filename": "p3b_side.png",
            "camera_pos": (80, 0, 12),
            "camera_look_at": (0, 0, 12),
            "description": "Motor cavity profile"
        },
        {
            "filename": "p3b_front.png",
            "camera_pos": (0, -60, 12),
            "camera_look_at": (0, 0, 12),
            "description": "Mounting holes"
        },
        {
            "filename": "p3b_detail.png",
            "camera_pos": (30, 30, 25),
            "camera_look_at": (0, 17, 12),
            "description": "Cable clip detail"
        }
    ],
    
    "part_3c_separator_wedge": [
        {
            "filename": "p3c_isometric.png",
            "camera_pos": (40, 40, 20),
            "camera_look_at": (0, 0, 0),
            "description": "Overall wedge"
        },
        {
            "filename": "p3c_side.png",
            "camera_pos": (50, 0, 0),
            "camera_look_at": (0, 0, 0),
            "description": "Wedge angle profile"
        },
        {
            "filename": "p3c_front.png",
            "camera_pos": (0, -30, 0),
            "camera_look_at": (0, 0, 0),
            "description": "Leading edge view"
        },
        {
            "filename": "p3c_bottom.png",
            "camera_pos": (0, 0, -20),
            "camera_look_at": (0, 0, 0),
            "camera_up": (0, -1, 0),
            "description": "Dovetail base"
        }
    ],
    
    "part_3d_roller": [
        {
            "filename": "p3d_isometric.png",
            "camera_pos": (60, 60, 40),
            "camera_look_at": (0, 0, 0),
            "description": "Overall roller"
        },
        {
            "filename": "p3d_side.png",
            "camera_pos": (0, 60, 0),
            "camera_look_at": (0, 0, 0),
            "description": "Roller profile with grooves"
        },
        {
            "filename": "p3d_end.png",
            "camera_pos": (60, 0, 0),
            "camera_look_at": (0, 0, 0),
            "description": "Shaft and bearing interface"
        },
        {
            "filename": "p3d_detail.png",
            "camera_pos": (30, 30, 20),
            "camera_look_at": (25, 0, 0),
            "description": "D-shaft coupling detail"
        }
    ],
    
    "part_4_pressure_plate": [
        {
            "filename": "p4_isometric.png",
            "camera_pos": (80, 80, 40),
            "camera_look_at": (0, 0, 0),
            "description": "Overall plate"
        },
        {
            "filename": "p4_top.png",
            "camera_pos": (0, 0, 60),
            "camera_look_at": (0, 0, 0),
            "description": "Weight reduction pattern"
        },
        {
            "filename": "p4_bottom.png",
            "camera_pos": (0, 0, -40),
            "camera_look_at": (0, 0, 0),
            "camera_up": (0, -1, 0),
            "description": "Guide posts and springs"
        },
        {
            "filename": "p4_detail.png",
            "camera_pos": (40, 40, 20),
            "camera_look_at": (29, 42, 0),
            "description": "Spring mount detail"
        }
    ]
}

def render_all_parts():
    """Render all parts with their specific views"""
    
    parts_dir = Path(__file__).parent.parent / "dealing_mechanism" / "parts"
    
    for part_name, views in PART_VIEWS.items():
        scad_file = parts_dir / f"{part_name}.scad"
        
        if not scad_file.exists():
            print(f"Warning: {scad_file} not found, skipping...")
            continue
            
        print(f"\n{'='*60}")
        print(f"Rendering {part_name}")
        print(f"{'='*60}")
        
        # Create renderer for this part
        renderer = ScadRenderer(str(scad_file))
        
        # Render each view
        for view in views:
            print(f"\nRendering: {view['description']}")
            print(f"  File: {view['filename']}")
            print(f"  Camera: {view.get('camera_pos', 'default')}")
            
            # Extract parameters
            params = {
                'output_file': view['filename'],
                'camera_pos': view.get('camera_pos', (100, 100, 100)),
                'camera_look_at': view.get('camera_look_at', (0, 0, 0)),
                'img_size': (1024, 768)
            }
            
            if 'camera_up' in view:
                params['camera_up'] = view['camera_up']
                
            # Render the view
            success = renderer.render_single(**params)
            
            if success:
                print(f"  ✓ Success")
            else:
                print(f"  ✗ Failed")
    
    print(f"\n{'='*60}")
    print("All rendering complete!")
    print(f"Images saved to respective parts/renders directories")

if __name__ == "__main__":
    render_all_parts()