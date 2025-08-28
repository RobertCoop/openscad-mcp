#!/usr/bin/env python3
"""
Verify QR Scanner Window Fixes
Renders views to confirm the corrections have been applied.
"""

from scad_renderer import ScadRenderer
from pathlib import Path

def verify_fixes():
    """Render verification views of the fixed design."""
    
    # Initialize renderer
    scad_file = Path(__file__).parent / "poc_dealer.scad"
    renderer = ScadRenderer(scad_file, "renders_verified")
    
    # Define verification views
    verification_views = [
        # 1. Overall assembly showing recessed deck
        {
            'filename': 'verify_01_assembly_overview.png',
            'camera_pos': (200, 200, 120),
            'camera_look_at': (0, 0, 40),
            'variables': {'view_mode': 'assembly', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 2. Side view showing deck sitting DOWN in device
        {
            'filename': 'verify_02_side_view_deck_position.png',
            'camera_pos': (250, 0, 60),
            'camera_look_at': (0, 0, 60),
            'variables': {'view_mode': 'assembly', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 3. Bottom view showing enlarged QR window
        {
            'filename': 'verify_03_bottom_qr_window_large.png',
            'camera_pos': (0, 50, -150),
            'camera_look_at': (0, 30, 40),
            'camera_up': (0, -1, 0),
            'variables': {'view_mode': 'assembly', 'show_ghosts': False},
            'img_size': (1200, 900)
        },
        
        # 4. Platform showing frame-style deck holder
        {
            'filename': 'verify_04_platform_frame_holder.png',
            'camera_pos': (150, 150, 100),
            'camera_look_at': (0, 0, 10),
            'variables': {'view_mode': 'platform', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 5. Close-up of frame deck holder from below
        {
            'filename': 'verify_05_deck_holder_bottom_exposure.png',
            'camera_pos': (100, 100, -50),
            'camera_look_at': (0, 0, 0),
            'variables': {'view_mode': 'platform', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 6. Cross-section showing card path
        {
            'filename': 'verify_06_cross_section_card_path.png',
            'camera_pos': (0, -250, 60),
            'camera_look_at': (0, 0, 60),
            'variables': {
                'view_mode': 'assembly',
                'cross_section_enabled': True,
                'cross_section_height': 65,
                'show_ghosts': True
            },
            'img_size': (1200, 900)
        },
        
        # 7. Detail of dealing mechanism and exit chute
        {
            'filename': 'verify_07_dealing_mechanism.png',
            'camera_pos': (100, 150, 50),
            'camera_look_at': (0, 50, 0),
            'variables': {'view_mode': 'platform', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 8. QR window alignment check
        {
            'filename': 'verify_08_qr_alignment.png',
            'camera_pos': (0, 100, -100),
            'camera_look_at': (0, 30, 50),
            'variables': {'view_mode': 'assembly', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 9. Exploded view showing improvements
        {
            'filename': 'verify_09_exploded_improved.png',
            'camera_pos': (200, 200, 150),
            'camera_look_at': (0, 0, 50),
            'variables': {
                'view_mode': 'exploded',
                'explode_distance': 80,
                'show_ghosts': True
            },
            'img_size': (1200, 900)
        },
        
        # 10. Front view showing exit slot
        {
            'filename': 'verify_10_front_exit_slot.png',
            'camera_pos': (0, -200, 40),
            'camera_look_at': (0, 60, 65),
            'variables': {'view_mode': 'assembly', 'show_ghosts': True},
            'img_size': (1200, 900)
        }
    ]
    
    print("=" * 60)
    print("VERIFICATION OF QR SCANNER FIXES")
    print("=" * 60)
    print("\nImprovements implemented:")
    print("✓ QR window enlarged to 60x50mm rectangular")
    print("✓ Chamfered edges for better viewing angle")
    print("✓ Frame-style deck holder for maximum card exposure")
    print("✓ Deck sits DOWN in device (recessed)")
    print("✓ Bottom dealing through horizontal slot")
    print("✓ Proper card exit chute")
    print("-" * 60)
    
    # Render all verification views
    rendered_files = renderer.render_custom_views(verification_views)
    
    print("\n" + "=" * 60)
    print("VERIFICATION COMPLETE")
    print("=" * 60)
    print("\nCheck rendered images for:")
    print("1. Large rectangular QR window (60x50mm)")
    print("2. Frame deck holder exposing most of bottom card")
    print("3. Deck sitting recessed in platform")
    print("4. Clear bottom dealing path through slot")
    print("5. Proper alignment of all components")
    print("-" * 60)
    
    print(f"\n✓ Verification renders complete!")
    print(f"✓ View results in: renders_verified/")
    
    return rendered_files


if __name__ == '__main__':
    verify_fixes()