#!/usr/bin/env python3
"""
Render POC Card Dealer Views
Specific rendering script for the POC card dealer with all component views.
"""

from scad_renderer import ScadRenderer
import sys
from pathlib import Path


def render_poc_dealer():
    """Render all views of the POC card dealer."""
    
    # Initialize renderer
    scad_file = Path(__file__).parent / "poc_dealer.scad"
    renderer = ScadRenderer(scad_file, "renders")
    
    # Define custom views for the card dealer
    custom_views = [
        # Full assembly views
        {
            'filename': '01_assembly_iso.png',
            'camera_pos': (200, 200, 150),
            'camera_look_at': (0, 0, 40),
            'variables': {'view_mode': 'assembly', 'show_ghosts': True}
        },
        {
            'filename': '02_assembly_front.png',
            'camera_pos': (0, -250, 100),
            'camera_look_at': (0, 0, 40),
            'variables': {'view_mode': 'assembly', 'show_ghosts': True}
        },
        {
            'filename': '03_assembly_top.png',
            'camera_pos': (0, 0, 300),
            'camera_look_at': (0, 0, 0),
            'camera_up': (0, 1, 0),
            'variables': {'view_mode': 'assembly', 'show_ghosts': False}
        },
        
        # Exploded view
        {
            'filename': '04_exploded.png',
            'camera_pos': (200, 200, 150),
            'camera_look_at': (0, 0, 60),
            'variables': {
                'view_mode': 'exploded',
                'explode_distance': 80,
                'show_ghosts': True
            }
        },
        
        # Base component views
        {
            'filename': '05_base_iso.png',
            'camera_pos': (200, 200, 100),
            'camera_look_at': (0, 0, 30),
            'variables': {'view_mode': 'base', 'show_ghosts': True}
        },
        {
            'filename': '06_base_bottom.png',
            'camera_pos': (0, 0, -200),
            'camera_look_at': (0, 0, 30),
            'camera_up': (0, -1, 0),
            'variables': {'view_mode': 'base', 'show_ghosts': False}
        },
        {
            'filename': '07_base_qr_window.png',
            'camera_pos': (0, 100, 80),
            'camera_look_at': (0, 30, 55),
            'variables': {'view_mode': 'base', 'show_ghosts': True}
        },
        
        # Platform component views
        {
            'filename': '08_platform_iso.png',
            'camera_pos': (150, 150, 100),
            'camera_look_at': (0, 0, 20),
            'variables': {'view_mode': 'platform', 'show_ghosts': True}
        },
        {
            'filename': '09_platform_top.png',
            'camera_pos': (0, 0, 150),
            'camera_look_at': (0, 0, 0),
            'camera_up': (0, 1, 0),
            'variables': {'view_mode': 'platform', 'show_ghosts': True}
        },
        {
            'filename': '10_platform_bottom.png',
            'camera_pos': (0, 0, -150),
            'camera_look_at': (0, 0, 0),
            'camera_up': (0, -1, 0),
            'variables': {'view_mode': 'platform', 'show_ghosts': False}
        },
        
        # Cross sections
        {
            'filename': '11_cross_section_mid.png',
            'camera_pos': (200, 200, 100),
            'camera_look_at': (0, 0, 40),
            'variables': {
                'view_mode': 'assembly',
                'cross_section_enabled': True,
                'cross_section_height': 40,
                'show_ghosts': True
            }
        },
        {
            'filename': '12_cross_section_low.png',
            'camera_pos': (200, 200, 50),
            'camera_look_at': (0, 0, 20),
            'variables': {
                'view_mode': 'assembly',
                'cross_section_enabled': True,
                'cross_section_height': 20,
                'show_ghosts': True
            }
        },
        
        # Print orientations
        {
            'filename': '13_print_base.png',
            'camera_pos': (150, 150, 100),
            'camera_look_at': (0, 0, 30),
            'variables': {'view_mode': 'print_base'}
        },
        {
            'filename': '14_print_platform.png',
            'camera_pos': (150, 150, 100),
            'camera_look_at': (0, 0, 20),
            'variables': {'view_mode': 'print_platform'}
        },
        
        # Detail views
        {
            'filename': '15_detail_deck_holder.png',
            'camera_pos': (100, 100, 120),
            'camera_look_at': (0, 0, 75),
            'variables': {'view_mode': 'platform', 'show_ghosts': True}
        },
        {
            'filename': '16_detail_motor_mount.png',
            'camera_pos': (100, -100, 50),
            'camera_look_at': (0, 0, 15),
            'variables': {'view_mode': 'base', 'show_ghosts': True}
        },
    ]
    
    print("=" * 60)
    print("POC CARD DEALER RENDERING")
    print("=" * 60)
    
    # Render all custom views
    print("\n=== Rendering component views ===")
    rendered_files = renderer.render_custom_views(custom_views)
    
    # Render turntable animation
    print("\n=== Rendering turntable animation ===")
    turntable_frames = renderer.render_turntable(
        base_name="turntable",
        num_frames=36,
        radius=250,
        height=120,
        look_at=(0, 0, 40),
        variables={'view_mode': 'assembly', 'show_ghosts': True},
        img_size=(800, 600)
    )
    
    # Create animated GIF if possible
    if turntable_frames:
        print("\n=== Creating animated GIF ===")
        success = renderer.create_animation_gif(
            turntable_frames, 
            "turntable_animation.gif",
            delay=8
        )
        if not success:
            print("Note: Install ImageMagick to create animated GIFs")
    
    # Create HTML gallery
    print("\n=== Creating HTML gallery ===")
    create_html_gallery(rendered_files + turntable_frames)
    
    print("\n✓ All renders complete!")
    print(f"✓ View renders in: {renderer.output_dir}")
    print(f"✓ Open gallery.html in a browser to view all renders")


def create_html_gallery(image_files):
    """Create an HTML file to view all rendered images."""
    
    html_content = """<!DOCTYPE html>
<html>
<head>
    <title>POC Card Dealer - Rendered Views</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #1a1a1a;
            color: #ffffff;
            padding: 20px;
        }
        h1 {
            text-align: center;
            color: #4a9eff;
        }
        .gallery {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 20px;
            margin-top: 30px;
        }
        .image-container {
            background-color: #2a2a2a;
            border-radius: 8px;
            padding: 10px;
            text-align: center;
        }
        .image-container img {
            max-width: 100%;
            height: auto;
            border-radius: 4px;
        }
        .image-title {
            margin-top: 10px;
            font-size: 14px;
            color: #aaaaaa;
        }
        .section-title {
            grid-column: 1 / -1;
            margin-top: 30px;
            margin-bottom: 10px;
            padding: 10px;
            background-color: #333;
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <h1>POC Card Dealer - Rendered Views</h1>
    <div class="gallery">
"""
    
    # Group images by type
    assembly_imgs = [f for f in image_files if 'assembly' in f or 'exploded' in f]
    component_imgs = [f for f in image_files if 'base' in f or 'platform' in f]
    cross_section_imgs = [f for f in image_files if 'cross_section' in f]
    print_imgs = [f for f in image_files if 'print' in f]
    detail_imgs = [f for f in image_files if 'detail' in f]
    turntable_imgs = [f for f in image_files if 'turntable' in f]
    
    # Add sections to HTML
    sections = [
        ("Assembly Views", assembly_imgs),
        ("Component Views", component_imgs),
        ("Cross Sections", cross_section_imgs),
        ("Print Orientations", print_imgs),
        ("Detail Views", detail_imgs),
        ("Turntable Animation Frames", turntable_imgs[:6])  # Show first 6 frames
    ]
    
    for section_title, images in sections:
        if images:
            html_content += f'<div class="section-title"><h2>{section_title}</h2></div>\n'
            for img in images:
                title = img.replace('.png', '').replace('_', ' ').title()
                html_content += f"""
        <div class="image-container">
            <img src="{img}" alt="{title}">
            <div class="image-title">{title}</div>
        </div>
"""
    
    html_content += """
    </div>
</body>
</html>
"""
    
    # Save HTML file
    gallery_path = Path("renders") / "gallery.html"
    gallery_path.write_text(html_content)
    print(f"✓ HTML gallery saved to: {gallery_path}")


if __name__ == '__main__':
    render_poc_dealer()