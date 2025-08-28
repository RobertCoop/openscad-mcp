#!/usr/bin/env python3
"""
SCAD File Renderer
Renders OpenSCAD files to images from multiple perspectives using OpenSCAD CLI.
"""

import subprocess
import os
import sys
import json
import tempfile
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import argparse
from datetime import datetime


class ScadRenderer:
    """Renders SCAD files to images from various camera angles."""
    
    def __init__(self, scad_file: str, output_dir: str = None):
        """
        Initialize the renderer.
        
        Args:
            scad_file: Path to the SCAD file to render
            output_dir: Directory to save rendered images (defaults to ./renders relative to scad file)
        """
        self.scad_file = Path(scad_file)
        if not self.scad_file.exists():
            raise FileNotFoundError(f"SCAD file not found: {scad_file}")
        
        # Default output dir is sibling to the scad file's directory
        if output_dir is None:
            self.output_dir = self.scad_file.parent / "renders"
        else:
            self.output_dir = Path(output_dir)
        
        self.output_dir.mkdir(exist_ok=True)
        
        # Check if OpenSCAD is installed
        self.openscad_cmd = self._find_openscad()
        if not self.openscad_cmd:
            raise RuntimeError("OpenSCAD not found. Please install OpenSCAD first.")
    
    def _find_openscad(self) -> Optional[str]:
        """Find OpenSCAD executable."""
        # Common OpenSCAD executable names
        candidates = ['openscad', 'OpenSCAD', 'openscad.exe']
        
        for cmd in candidates:
            try:
                subprocess.run([cmd, '--version'], 
                             capture_output=True, 
                             check=False)
                return cmd
            except FileNotFoundError:
                continue
        
        # Check common installation paths
        common_paths = [
            '/usr/bin/openscad',
            '/usr/local/bin/openscad',
            '/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD',
            'C:\\Program Files\\OpenSCAD\\openscad.exe',
            'C:\\Program Files (x86)\\OpenSCAD\\openscad.exe'
        ]
        
        for path in common_paths:
            if os.path.exists(path):
                return path
        
        return None
    
    def render_single(self, 
                     output_file: str,
                     camera_pos: Tuple[float, float, float] = (100, 100, 100),
                     camera_look_at: Tuple[float, float, float] = (0, 0, 0),
                     camera_up: Tuple[float, float, float] = (0, 0, 1),
                     img_size: Tuple[int, int] = (1024, 768),
                     colorscheme: str = "Tomorrow Night",
                     variables: Dict[str, any] = None,
                     auto_center: bool = True) -> bool:
        """
        Render a single image from specified camera position.
        
        Args:
            output_file: Output image filename (PNG)
            camera_pos: Camera position (x, y, z)
            camera_look_at: Point camera looks at (x, y, z)
            camera_up: Camera up vector (x, y, z)
            img_size: Image size (width, height)
            colorscheme: OpenSCAD color scheme
            variables: SCAD variables to override
            auto_center: Auto-center the model
        
        Returns:
            True if successful, False otherwise
        """
        output_path = self.output_dir / output_file
        
        # Build OpenSCAD command
        cmd = [
            self.openscad_cmd,
            '-o', str(output_path),
            '--imgsize', f'{img_size[0]},{img_size[1]}',
            '--colorscheme', colorscheme,
        ]
        
        # Add camera parameters
        # Calculate distance for OpenSCAD camera
        import math
        dx = camera_pos[0] - camera_look_at[0]
        dy = camera_pos[1] - camera_look_at[1]
        dz = camera_pos[2] - camera_look_at[2]
        distance = math.sqrt(dx*dx + dy*dy + dz*dz)
        
        # OpenSCAD uses 7 parameters: eye_x,eye_y,eye_z,center_x,center_y,center_z,distance
        camera_str = (f'--camera='
                     f'{camera_pos[0]},{camera_pos[1]},{camera_pos[2]},'
                     f'{camera_look_at[0]},{camera_look_at[1]},{camera_look_at[2]},'
                     f'{distance}')
        cmd.append(camera_str)
        
        if auto_center:
            cmd.append('--autocenter')
            cmd.append('--viewall')
        
        # Add variable overrides
        if variables:
            for key, value in variables.items():
                # Format value based on type
                if isinstance(value, str):
                    val_str = f'"{value}"'
                elif isinstance(value, bool):
                    val_str = 'true' if value else 'false'
                else:
                    val_str = str(value)
                cmd.extend(['-D', f'{key}={val_str}'])
        
        # Add the SCAD file
        cmd.append(str(self.scad_file))
        
        # Run OpenSCAD
        print(f"Rendering: {output_file}")
        print(f"Camera: pos={camera_pos}, target={camera_look_at}")
        
        try:
            result = subprocess.run(cmd, 
                                  capture_output=True, 
                                  text=True, 
                                  check=False)
            
            if result.returncode != 0:
                print(f"Error rendering {output_file}:")
                print(result.stderr)
                return False
            
            print(f"✓ Saved to: {output_path}")
            return True
            
        except Exception as e:
            print(f"Error: {e}")
            return False
    
    def render_turntable(self, 
                        base_name: str = "turntable",
                        num_frames: int = 36,
                        radius: float = 200,
                        height: float = 100,
                        look_at: Tuple[float, float, float] = (0, 0, 30),
                        **kwargs) -> List[str]:
        """
        Render turntable animation frames.
        
        Args:
            base_name: Base name for output files
            num_frames: Number of frames (360/num_frames degrees per frame)
            radius: Distance from center
            height: Camera height
            look_at: Point to look at
            **kwargs: Additional arguments for render_single
        
        Returns:
            List of rendered filenames
        """
        import math
        
        rendered_files = []
        
        for i in range(num_frames):
            angle = (360 / num_frames) * i
            angle_rad = math.radians(angle)
            
            # Calculate camera position
            x = radius * math.cos(angle_rad)
            y = radius * math.sin(angle_rad)
            z = height
            
            filename = f"{base_name}_{i:03d}.png"
            
            success = self.render_single(
                filename,
                camera_pos=(x, y, z),
                camera_look_at=look_at,
                **kwargs
            )
            
            if success:
                rendered_files.append(filename)
        
        return rendered_files
    
    def render_perspectives(self, 
                           base_name: str = "view",
                           distance: float = 200,
                           **kwargs) -> Dict[str, str]:
        """
        Render standard orthographic-like perspectives.
        
        Args:
            base_name: Base name for output files
            distance: Distance from origin
            **kwargs: Additional arguments for render_single
        
        Returns:
            Dictionary of perspective names to filenames
        """
        perspectives = {
            'front': ((0, -distance, 0), (0, 0, 0), (0, 0, 1)),
            'back': ((0, distance, 0), (0, 0, 0), (0, 0, 1)),
            'left': ((-distance, 0, 0), (0, 0, 0), (0, 0, 1)),
            'right': ((distance, 0, 0), (0, 0, 0), (0, 0, 1)),
            'top': ((0, 0, distance), (0, 0, 0), (0, 1, 0)),
            'bottom': ((0, 0, -distance), (0, 0, 0), (0, -1, 0)),
            'isometric': ((distance, distance, distance), (0, 0, 0), (0, 0, 1)),
            'dimetric': ((distance, distance*0.5, distance), (0, 0, 0), (0, 0, 1)),
        }
        
        rendered = {}
        
        for name, (pos, look_at, up) in perspectives.items():
            filename = f"{base_name}_{name}.png"
            success = self.render_single(
                filename,
                camera_pos=pos,
                camera_look_at=look_at,
                camera_up=up,
                **kwargs
            )
            if success:
                rendered[name] = filename
        
        return rendered
    
    def render_custom_views(self, views: List[Dict]) -> List[str]:
        """
        Render custom views from a list of view specifications.
        
        Args:
            views: List of view dictionaries with camera parameters
        
        Returns:
            List of rendered filenames
        """
        rendered = []
        
        for i, view in enumerate(views):
            filename = view.get('filename', f'custom_view_{i:03d}.png')
            
            success = self.render_single(
                filename,
                camera_pos=view.get('camera_pos', (100, 100, 100)),
                camera_look_at=view.get('camera_look_at', (0, 0, 0)),
                camera_up=view.get('camera_up', (0, 0, 1)),
                img_size=view.get('img_size', (1024, 768)),
                colorscheme=view.get('colorscheme', 'Tomorrow Night'),
                variables=view.get('variables', None),
                auto_center=view.get('auto_center', True)
            )
            
            if success:
                rendered.append(filename)
        
        return rendered
    
    def create_animation_gif(self, 
                           frames: List[str], 
                           output_name: str = "animation.gif",
                           delay: int = 10) -> bool:
        """
        Create animated GIF from rendered frames using ImageMagick.
        
        Args:
            frames: List of frame filenames
            output_name: Output GIF filename
            delay: Delay between frames (1/100 seconds)
        
        Returns:
            True if successful
        """
        try:
            # Check if ImageMagick is installed
            subprocess.run(['convert', '--version'], 
                         capture_output=True, 
                         check=True)
        except:
            print("ImageMagick not found. Install it to create GIFs.")
            print("Ubuntu/Debian: sudo apt-get install imagemagick")
            print("macOS: brew install imagemagick")
            print("Windows: Download from https://imagemagick.org")
            return False
        
        # Build frame paths
        frame_paths = [str(self.output_dir / frame) for frame in frames]
        
        # Create GIF
        output_path = self.output_dir / output_name
        cmd = ['convert', '-delay', str(delay), '-loop', '0'] + frame_paths + [str(output_path)]
        
        try:
            subprocess.run(cmd, check=True)
            print(f"✓ Animation saved to: {output_path}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error creating GIF: {e}")
            return False


def main():
    """Main entry point with CLI."""
    parser = argparse.ArgumentParser(description='Render OpenSCAD files to images')
    parser.add_argument('scad_file', help='Path to SCAD file')
    parser.add_argument('-o', '--output-dir', default=None, 
                       help='Output directory (default: ./renders relative to scad file)')
    parser.add_argument('-m', '--mode', 
                       choices=['single', 'turntable', 'perspectives', 'all'],
                       default='all',
                       help='Rendering mode')
    parser.add_argument('-n', '--num-frames', type=int, default=36,
                       help='Number of frames for turntable (default: 36)')
    parser.add_argument('-s', '--size', default='1024,768',
                       help='Image size as width,height (default: 1024,768)')
    parser.add_argument('-d', '--distance', type=float, default=200,
                       help='Camera distance (default: 200)')
    parser.add_argument('--gif', action='store_true',
                       help='Create animated GIF from turntable')
    parser.add_argument('-v', '--variables', action='append',
                       help='Set SCAD variables (e.g., -v view_mode=exploded)')
    
    args = parser.parse_args()
    
    # Parse image size
    try:
        width, height = map(int, args.size.split(','))
        img_size = (width, height)
    except:
        print(f"Invalid size format: {args.size}")
        sys.exit(1)
    
    # Parse variables
    variables = {}
    if args.variables:
        for var in args.variables:
            try:
                key, value = var.split('=', 1)
                # Try to parse value as JSON for proper type
                try:
                    value = json.loads(value)
                except:
                    pass  # Keep as string
                variables[key] = value
            except:
                print(f"Invalid variable format: {var}")
                sys.exit(1)
    
    # Create renderer
    try:
        renderer = ScadRenderer(args.scad_file, args.output_dir)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    
    # Render based on mode
    if args.mode == 'single' or args.mode == 'all':
        print("\n=== Rendering single view ===")
        renderer.render_single(
            'single_view.png',
            camera_pos=(150, 150, 100),
            camera_look_at=(0, 0, 30),
            img_size=img_size,
            variables=variables
        )
    
    if args.mode == 'perspectives' or args.mode == 'all':
        print("\n=== Rendering standard perspectives ===")
        renderer.render_perspectives(
            distance=args.distance,
            img_size=img_size,
            variables=variables
        )
    
    if args.mode == 'turntable' or args.mode == 'all':
        print(f"\n=== Rendering {args.num_frames} frame turntable ===")
        frames = renderer.render_turntable(
            num_frames=args.num_frames,
            radius=args.distance,
            height=args.distance * 0.5,
            img_size=img_size,
            variables=variables
        )
        
        if args.gif and frames:
            print("\n=== Creating animated GIF ===")
            renderer.create_animation_gif(frames, delay=5)
    
    print("\n✓ Rendering complete!")


if __name__ == '__main__':
    main()