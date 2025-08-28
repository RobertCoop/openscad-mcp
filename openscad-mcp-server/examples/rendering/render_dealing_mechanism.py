#!/usr/bin/env python3
"""
Specialized renderer for the dealing mechanism with diagnostic views.
"""

from scad_renderer import ScadRenderer
from pathlib import Path
import sys

def render_dealing_mechanism():
    """Render diagnostic views of the dealing mechanism."""
    
    # Find the dealing mechanism SCAD file
    scad_file = Path(__file__).parent.parent / "dealing_mechanism" / "dealing_mechanism_standalone.scad"
    
    if not scad_file.exists():
        print(f"Error: SCAD file not found at {scad_file}")
        sys.exit(1)
    
    # Create renderer (will put renders in dealing_mechanism/renders/)
    renderer = ScadRenderer(str(scad_file))
    
    print(f"Rendering dealing mechanism from: {scad_file}")
    print(f"Output directory: {renderer.output_dir}")
    
    # Define diagnostic views
    diagnostic_views = [
        {
            'filename': 'overview_isometric.png',
            'camera_pos': (200, 200, 150),
            'camera_look_at': (0, 0, 40),
            'variables': {'show_ghosts': True, 'show_assembled': True}
        },
        {
            'filename': 'exploded_view.png',
            'camera_pos': (200, 200, 150),
            'camera_look_at': (0, 0, 60),
            'variables': {'show_exploded': True, 'show_ghosts': False}
        },
        {
            'filename': 'cross_section.png',
            'camera_pos': (0, -200, 80),
            'camera_look_at': (0, 0, 40),
            'variables': {'show_cross_section': True, 'show_ghosts': True}
        },
        {
            'filename': 'qr_scanner_alignment.png',
            'camera_pos': (0, 0, -150),
            'camera_look_at': (0, 0, 30),
            'variables': {'show_ghosts': True}
        },
        {
            'filename': 'card_chamber_detail.png',
            'camera_pos': (150, 150, 100),
            'camera_look_at': (0, 0, 60),
            'variables': {'show_ghosts': False}
        },
        {
            'filename': 'roller_mechanism_detail.png',
            'camera_pos': (100, -100, 50),
            'camera_look_at': (0, 30, 45),
            'variables': {'show_ghosts': True}
        },
        {
            'filename': 'bottom_view.png',
            'camera_pos': (0, 0, -200),
            'camera_look_at': (0, 0, 0),
            'camera_up': (0, -1, 0),
            'variables': {'show_ghosts': False}
        },
        {
            'filename': 'front_view.png',
            'camera_pos': (0, -200, 60),
            'camera_look_at': (0, 0, 60),
            'variables': {'show_ghosts': True}
        }
    ]
    
    print(f"\nRendering {len(diagnostic_views)} diagnostic views...")
    rendered = renderer.render_custom_views(diagnostic_views)
    
    print(f"\n✓ Rendered {len(rendered)} views successfully")
    
    # Also render a turntable animation
    print("\nRendering turntable animation...")
    frames = renderer.render_turntable(
        base_name="turntable",
        num_frames=12,
        radius=250,
        height=120,
        look_at=(0, 0, 50),
        variables={'show_ghosts': False}
    )
    
    if frames:
        print(f"✓ Rendered {len(frames)} turntable frames")
        # Try to create GIF
        renderer.create_animation_gif(frames, "turntable.gif", delay=10)
    
    print("\n=== Rendering Complete ===")
    print(f"All renders saved to: {renderer.output_dir}")
    
    return renderer.output_dir

if __name__ == "__main__":
    render_dealing_mechanism()