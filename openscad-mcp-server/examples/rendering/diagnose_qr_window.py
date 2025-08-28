#!/usr/bin/env python3
"""
Diagnose QR Scanner Window Issues
Renders specific views to investigate the QR scanner window alignment and size.
"""

from scad_renderer import ScadRenderer
from pathlib import Path

def diagnose_qr_window():
    """Render diagnostic views of the QR scanner window."""
    
    # Initialize renderer
    scad_file = Path(__file__).parent / "poc_dealer.scad"
    renderer = ScadRenderer(scad_file, "renders_diagnosis")
    
    # Define diagnostic views focusing on QR window
    diagnostic_views = [
        # 1. Direct bottom view of base showing QR window
        {
            'filename': 'diag_01_base_bottom_direct.png',
            'camera_pos': (0, 0, -150),
            'camera_look_at': (0, 30, 55),  # Look at QR window location
            'camera_up': (0, -1, 0),
            'variables': {'view_mode': 'base', 'show_ghosts': False},
            'img_size': (1200, 900)
        },
        
        # 2. Angled view showing QR window from below
        {
            'filename': 'diag_02_base_qr_underneath.png',
            'camera_pos': (0, 80, -50),
            'camera_look_at': (0, 30, 55),
            'variables': {'view_mode': 'base', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 3. Platform bottom showing card slot alignment
        {
            'filename': 'diag_03_platform_bottom_slot.png',
            'camera_pos': (0, 50, -100),
            'camera_look_at': (0, 30, 0),
            'camera_up': (0, -1, 0),
            'variables': {'view_mode': 'platform', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 4. Assembly view from below showing alignment
        {
            'filename': 'diag_04_assembly_bottom_alignment.png',
            'camera_pos': (100, 100, -100),
            'camera_look_at': (0, 30, 40),
            'variables': {'view_mode': 'assembly', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 5. Cross section at QR window height
        {
            'filename': 'diag_05_cross_section_qr_level.png',
            'camera_pos': (200, 0, 55),  # Level with QR window
            'camera_look_at': (0, 0, 55),
            'variables': {
                'view_mode': 'assembly',
                'cross_section_enabled': True,
                'cross_section_height': 55,  # Cut at QR window height
                'show_ghosts': True
            },
            'img_size': (1200, 900)
        },
        
        # 6. Close-up of QR window area
        {
            'filename': 'diag_06_qr_window_closeup.png',
            'camera_pos': (0, 60, 50),
            'camera_look_at': (0, 30, 55),
            'variables': {'view_mode': 'base', 'show_ghosts': True},
            'img_size': (1200, 900)
        },
        
        # 7. Side cross-section showing card path
        {
            'filename': 'diag_07_side_section_card_path.png',
            'camera_pos': (250, 0, 60),
            'camera_look_at': (0, 0, 60),
            'variables': {
                'view_mode': 'assembly',
                'cross_section_enabled': True,
                'cross_section_height': 65,
                'show_ghosts': True
            },
            'img_size': (1200, 900)
        },
        
        # 8. Top-down view through platform to see alignment
        {
            'filename': 'diag_08_top_alignment_check.png',
            'camera_pos': (0, 0, 200),
            'camera_look_at': (0, 30, 0),
            'camera_up': (0, 1, 0),
            'variables': {
                'view_mode': 'assembly',
                'show_ghosts': True,
                'show_platform': True,
                'show_base': True
            },
            'img_size': (1200, 900)
        },
        
        # 9. Exploded view focusing on QR area
        {
            'filename': 'diag_09_exploded_qr_focus.png',
            'camera_pos': (0, 150, 100),
            'camera_look_at': (0, 30, 50),
            'variables': {
                'view_mode': 'exploded',
                'explode_distance': 60,
                'show_ghosts': True
            },
            'img_size': (1200, 900)
        },
        
        # 10. Direct front view at QR window level
        {
            'filename': 'diag_10_front_qr_level.png',
            'camera_pos': (0, -200, 55),
            'camera_look_at': (0, 0, 55),
            'variables': {'view_mode': 'assembly', 'show_ghosts': True},
            'img_size': (1200, 900)
        }
    ]
    
    print("=" * 60)
    print("QR SCANNER WINDOW DIAGNOSIS")
    print("=" * 60)
    print("\nRendering diagnostic views to investigate:")
    print("- QR scanner window size (should be 40x40mm)")
    print("- Alignment with card deck holder bottom slot")
    print("- Visibility of bottom card through the window")
    print("- Camera mounting position relative to window")
    print("-" * 60)
    
    # Render all diagnostic views
    rendered_files = renderer.render_custom_views(diagnostic_views)
    
    print("\n" + "=" * 60)
    print("DIAGNOSTIC SUMMARY")
    print("=" * 60)
    print("\nExpected QR Window Specifications:")
    print("- Size: 40x40mm (scanner_window_size parameter)")
    print("- Position: 30mm forward from center (Y-axis)")
    print("- Height: Base height - 5mm from top")
    print("\nCard Holder Bottom Slot:")
    print("- Width: card_width - 10mm (53mm)")
    print("- Length: 40mm")
    print("- Position: 30mm forward from center (should align)")
    print("\nCheck rendered images for:")
    print("1. Window size adequacy for QR code viewing")
    print("2. Proper alignment between base window and deck slot")
    print("3. Clear line of sight from scanner to bottom card")
    print("4. Any obstructions in the optical path")
    print("-" * 60)
    
    # Create diagnostic HTML report
    create_diagnostic_report(rendered_files)
    
    print(f"\n✓ Diagnostic renders complete!")
    print(f"✓ View detailed report: renders_diagnosis/diagnostic_report.html")
    
    return rendered_files


def create_diagnostic_report(image_files):
    """Create an HTML report for QR window diagnosis."""
    
    html_content = """<!DOCTYPE html>
<html>
<head>
    <title>QR Scanner Window Diagnostic Report</title>
    <style>
        body {
            font-family: 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: #ffffff;
            padding: 20px;
            margin: 0;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: rgba(26, 26, 26, 0.95);
            border-radius: 15px;
            padding: 30px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        h1 {
            text-align: center;
            color: #4a9eff;
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        .subtitle {
            text-align: center;
            color: #aaa;
            margin-bottom: 30px;
        }
        .diagnosis-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 30px;
            margin-top: 30px;
        }
        .diagnosis-item {
            background: rgba(42, 42, 42, 0.9);
            border-radius: 10px;
            padding: 15px;
            border: 1px solid rgba(74, 158, 255, 0.3);
        }
        .diagnosis-item img {
            width: 100%;
            height: auto;
            border-radius: 5px;
            margin-bottom: 10px;
        }
        .diagnosis-title {
            font-size: 1.2em;
            color: #4a9eff;
            margin-bottom: 5px;
        }
        .diagnosis-desc {
            color: #ccc;
            font-size: 0.9em;
            line-height: 1.4;
        }
        .specs-box {
            background: rgba(74, 158, 255, 0.1);
            border: 1px solid #4a9eff;
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
        }
        .specs-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        .spec-item {
            padding: 10px;
            background: rgba(0,0,0,0.3);
            border-radius: 5px;
        }
        .spec-label {
            color: #888;
            font-size: 0.85em;
        }
        .spec-value {
            color: #4a9eff;
            font-size: 1.1em;
            font-weight: bold;
        }
        .alert {
            background: rgba(255, 100, 100, 0.2);
            border: 1px solid #ff6464;
            border-radius: 8px;
            padding: 15px;
            margin: 20px 0;
        }
        .success {
            background: rgba(100, 255, 100, 0.2);
            border: 1px solid #64ff64;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 QR Scanner Window Diagnostic Report</h1>
        <div class="subtitle">Investigating window size and alignment issues</div>
        
        <div class="specs-box">
            <h2>Design Specifications</h2>
            <div class="specs-grid">
                <div class="spec-item">
                    <div class="spec-label">QR Window Size</div>
                    <div class="spec-value">40 × 40 mm</div>
                </div>
                <div class="spec-item">
                    <div class="spec-label">Window Position</div>
                    <div class="spec-value">Y: +30mm from center</div>
                </div>
                <div class="spec-item">
                    <div class="spec-label">Card Slot Width</div>
                    <div class="spec-value">53 mm</div>
                </div>
                <div class="spec-item">
                    <div class="spec-label">Card Slot Length</div>
                    <div class="spec-value">40 mm</div>
                </div>
                <div class="spec-item">
                    <div class="spec-label">Scanner Distance</div>
                    <div class="spec-value">~10 mm below base</div>
                </div>
                <div class="spec-item">
                    <div class="spec-label">QR Code Size</div>
                    <div class="spec-value">~20 × 20 mm typical</div>
                </div>
            </div>
        </div>
        
        <div class="alert">
            <h3>⚠️ Potential Issues to Check</h3>
            <ul>
                <li>QR window may be too small if QR codes are larger than 30mm</li>
                <li>Window-to-card distance might be too large for some QR scanners</li>
                <li>Alignment between base window and platform slot is critical</li>
                <li>Consider if window needs to be rectangular to match card width</li>
            </ul>
        </div>
        
        <div class="diagnosis-grid">
"""
    
    # Add diagnostic images with descriptions
    diagnostics = [
        ("diag_01_base_bottom_direct.png", "Base Bottom View", "Direct view from below showing QR scanner window size and position"),
        ("diag_02_base_qr_underneath.png", "Angled Bottom View", "Shows QR window accessibility from scanner mounting position"),
        ("diag_03_platform_bottom_slot.png", "Platform Bottom", "Card holder bottom slot that must align with QR window"),
        ("diag_04_assembly_bottom_alignment.png", "Assembly Alignment", "Shows how base window and platform slot align when assembled"),
        ("diag_05_cross_section_qr_level.png", "Cross Section at QR Level", "Horizontal cut through QR window showing internal clearance"),
        ("diag_06_qr_window_closeup.png", "QR Window Close-up", "Detailed view of window opening and edges"),
        ("diag_07_side_section_card_path.png", "Side Section", "Shows vertical alignment and card-to-scanner distance"),
        ("diag_08_top_alignment_check.png", "Top Alignment Check", "Verifies X-Y alignment of window and slot"),
        ("diag_09_exploded_qr_focus.png", "Exploded QR Area", "Component separation showing optical path"),
        ("diag_10_front_qr_level.png", "Front View at QR Level", "Shows window width relative to card holder")
    ]
    
    for filename, title, description in diagnostics:
        if filename in image_files:
            html_content += f"""
            <div class="diagnosis-item">
                <div class="diagnosis-title">{title}</div>
                <img src="{filename}" alt="{title}">
                <div class="diagnosis-desc">{description}</div>
            </div>
"""
    
    html_content += """
        </div>
        
        <div class="specs-box success">
            <h3>✅ Recommended Fixes</h3>
            <ol>
                <li><strong>Increase Window Size:</strong> Change to 50×50mm or 60×40mm rectangular</li>
                <li><strong>Adjust Window Position:</strong> Ensure perfect alignment with deck holder slot</li>
                <li><strong>Add Chamfer:</strong> Taper the window edges for better scanner viewing angle</li>
                <li><strong>Consider Transparent Window:</strong> Add clear acrylic insert for protection</li>
                <li><strong>Reduce Distance:</strong> Bring scanner closer or add lens focusing</li>
            </ol>
        </div>
    </div>
</body>
</html>
"""
    
    # Save HTML file
    report_path = Path("renders_diagnosis") / "diagnostic_report.html"
    report_path.write_text(html_content)
    print(f"✓ Diagnostic report saved to: {report_path}")


if __name__ == '__main__':
    diagnose_qr_window()