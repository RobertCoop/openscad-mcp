#!/usr/bin/env python3
"""
Render isolated card chamber and QR section views
"""

from scad_renderer import ScadRenderer
from pathlib import Path

def render_card_chamber():
    """Render the isolated card chamber and QR section."""
    
    # Initialize renderer
    scad_file = Path(__file__).parent / "poc_dealer.scad"
    renderer = ScadRenderer(scad_file, "renders_chamber")
    
    # Define card chamber views
    chamber_views = [
        # 1. Isometric view
        {
            'filename': 'chamber_01_isometric.png',
            'camera_pos': (150, 150, 100),
            'camera_look_at': (0, 0, 50),
            'variables': {'view_mode': 'card_chamber', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 2. Front view showing QR window alignment
        {
            'filename': 'chamber_02_front_alignment.png',
            'camera_pos': (0, -200, 60),
            'camera_look_at': (0, 0, 60),
            'variables': {'view_mode': 'card_chamber', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 3. Bottom view showing QR window
        {
            'filename': 'chamber_03_bottom_qr.png',
            'camera_pos': (0, 50, -100),
            'camera_look_at': (0, 30, 50),
            'camera_up': (0, -1, 0),
            'variables': {'view_mode': 'card_chamber', 'show_ghosts': False},
            'img_size': (1200, 900)
        },
        
        # 4. Top view showing card holder
        {
            'filename': 'chamber_04_top_holder.png',
            'camera_pos': (0, 0, 150),
            'camera_look_at': (0, 0, 60),
            'camera_up': (0, 1, 0),
            'variables': {'view_mode': 'card_chamber', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 5. Side cutaway
        {
            'filename': 'chamber_05_side_cutaway.png',
            'camera_pos': (200, 0, 60),
            'camera_look_at': (0, 0, 60),
            'variables': {
                'view_mode': 'card_chamber',
                'show_ghosts': True,
                'cross_section_enabled': True
            },
            'img_size': (1200, 900)
        },
        
        # 6. Angled view showing card path
        {
            'filename': 'chamber_06_card_path.png',
            'camera_pos': (100, -150, 80),
            'camera_look_at': (0, 30, 60),
            'variables': {'view_mode': 'card_chamber', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 7. Close-up of frame holder
        {
            'filename': 'chamber_07_frame_detail.png',
            'camera_pos': (80, 80, 100),
            'camera_look_at': (0, 0, 65),
            'variables': {'view_mode': 'card_chamber', 'show_ghosts': False},
            'img_size': (1200, 900)
        },
        
        # 8. QR scanner perspective
        {
            'filename': 'chamber_08_scanner_view.png',
            'camera_pos': (0, 30, 20),
            'camera_look_at': (0, 30, 65),
            'variables': {'view_mode': 'card_chamber', 'show_ghosts': True},
            'img_size': (1200, 900)
        }
    ]
    
    print("=" * 60)
    print("CARD CHAMBER & QR SECTION RENDERS")
    print("=" * 60)
    print("\nRendering isolated views of:")
    print("- Card chamber with frame holder")
    print("- QR scanner window section")
    print("- Dealing mechanism")
    print("- Alignment between components")
    print("-" * 60)
    
    # Render all views
    rendered_files = renderer.render_custom_views(chamber_views)
    
    print("\n" + "=" * 60)
    print("RENDERING COMPLETE")
    print("=" * 60)
    print("\nThe isolated card chamber view shows:")
    print("- Just the critical QR scanning area")
    print("- Frame-style deck holder")
    print("- Large rectangular QR window")
    print("- Dealing mechanism at platform level")
    print("- Ghost components for reference")
    print("-" * 60)
    
    print(f"\n✓ Card chamber renders complete!")
    print(f"✓ View results in: renders_chamber/")
    
    return rendered_files


if __name__ == '__main__':
    render_card_chamber()