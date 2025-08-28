"""
Main entry point for the OpenSCAD MCP Server.

This module allows running the server as a module:
    python -m openscad_mcp
"""

from .server import main

if __name__ == "__main__":
    main()