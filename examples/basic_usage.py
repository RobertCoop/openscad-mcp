"""Basic usage example for OpenSCAD MCP Server

This example demonstrates how to use the OpenSCAD MCP Server
to render 3D models and perform various operations.
"""

import asyncio
import json
from pathlib import Path

# Example of basic MCP server interaction
async def basic_example():
    """Example showing basic MCP server usage."""
    
    # Example OpenSCAD code
    scad_code = """
    // Simple cube example
    $fn = 50;
    
    difference() {
        cube([30, 30, 30], center = true);
        sphere(r = 20);
    }
    """
    
    # Example of creating a render request
    render_request = {
        "tool": "render_model",
        "arguments": {
            "code": scad_code,
            "output_file": "example_output.png",
            "format": "png",
            "camera_position": [50, 50, 50],
            "resolution": [800, 600]
        }
    }
    
    print("Example render request:")
    print(json.dumps(render_request, indent=2))
    
    # Example of analyzing a model
    analyze_request = {
        "tool": "analyze_model",
        "arguments": {
            "code": scad_code
        }
    }
    
    print("\nExample analyze request:")
    print(json.dumps(analyze_request, indent=2))


def main():
    """Main entry point for the example."""
    print("OpenSCAD MCP Server - Basic Usage Example")
    print("=" * 50)
    print("\nThis example demonstrates the structure of MCP requests")
    print("for interacting with the OpenSCAD MCP Server.\n")
    
    # Run the async example
    asyncio.run(basic_example())
    
    print("\nFor more examples, see:")
    print("  - examples/rendering/   : Advanced rendering utilities")
    print("  - examples/config/      : Configuration examples")
    print("  - tests/               : Test cases and fixtures")


if __name__ == "__main__":
    main()