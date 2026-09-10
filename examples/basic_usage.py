#!/usr/bin/env python3
"""Drive the OpenSCAD MCP server in process, with no client and no subprocess.

``fastmcp.Client`` accepts a server object directly and speaks MCP to it over
an in-memory transport, so this script exercises exactly the tools an
assistant would call. It walks the workflow the server recommends:

    validate(mode=syntax) -> measure(mode=model) -> render(grounded=true) -> check

on a two-part model: a tray and a lid that drops into it.

    uv run python examples/basic_usage.py

OpenSCAD must be installed and on PATH, or OPENSCAD_PATH must point at it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from fastmcp import Client

from openscad_mcp.server import mcp

# Two named parts, driven separately by check(). The lid sits 0.2 mm proud of
# the tray walls on every side, which is the fit the checks below assert.
MODEL = """
$fn = 64;

TRAY_X   = 60;
TRAY_Y   = 40;
TRAY_H   = 18;
WALL     = 2;
FIT      = 0.2;   // radial clearance between lid and tray bore
LID_H    = 3;

module tray() {
    difference() {
        cube([TRAY_X, TRAY_Y, TRAY_H]);
        translate([WALL, WALL, WALL])
            cube([TRAY_X - 2 * WALL, TRAY_Y - 2 * WALL, TRAY_H]);
    }
}

module lid() {
    cube([
        TRAY_X - 2 * WALL - 2 * FIT,
        TRAY_Y - 2 * WALL - 2 * FIT,
        LID_H,
    ]);
}

tray();
translate([WALL + FIT, WALL + FIT, TRAY_H - LID_H]) lid();
"""

PARTS = [
    {"name": "tray", "code": "tray();"},
    {
        "name": "lid",
        "code": "lid();",
        "place": "translate([WALL + FIT, WALL + FIT, TRAY_H - LID_H])",
    },
]


def show(title: str, body: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}\n{body}")


async def main() -> int:
    async with Client(mcp) as client:
        # 1. Syntax first. Nothing else is worth running on a file that does
        #    not parse, and this call does not render anything.
        result = await client.call_tool("validate", {"scad_content": MODEL, "mode": "syntax"})
        valid = result.data
        show(
            "validate(mode=syntax)",
            f"valid={valid['valid']} errors={valid['errors']} warnings={valid['warnings']}",
        )
        if not valid["valid"]:
            return 1

        # 2. Measure before you look. Numbers come from the exported mesh, so
        #    they answer size and watertightness questions a picture cannot.
        result = await client.call_tool("measure", {"scad_content": MODEL, "mode": "model"})
        m = result.data
        show(
            "measure(mode=model)",
            "\n".join(
                [
                    f"bounding box : {m['bbox_min']} .. {m['bbox_max']} mm",
                    f"dimensions   : {m['dimensions']} mm",
                    f"volume       : {m['volume']:.1f} mm^3",
                    f"watertight   : {m['is_watertight']}   manifold: {m['is_manifold']}",
                    f"components   : {m['component_count']}",
                ]
            ),
        )

        # 3. A grounded render is orthographic with a stated mm/px scale, so
        #    the leading text block says where the camera is and how big a
        #    pixel is. The image itself comes back as a separate content block.
        result = await client.call_tool(
            "render",
            {
                "scad_content": MODEL,
                "views": ["isometric"],
                "grounded": True,
                "image_size": [640, 480],
            },
        )
        lines = []
        for block in result.content:
            if block.type == "image":
                lines.append(f"[image] {block.mimeType}, {len(block.data)} base64 chars")
            elif block.text.lstrip().startswith("{"):
                meta = json.loads(block.text)
                lines.append(f"[meta] views={meta['views']} image_tokens={meta['image_tokens']}")
            else:
                lines.append(block.text.strip())
        show("render(grounded=true)", "\n".join(lines))

        # 4. Geometric rules over the two parts. Each part is exported to its
        #    own mesh, so "do they overlap" is a real query and not a guess.
        result = await client.call_tool(
            "check",
            {
                "scad_content": MODEL,
                "parts": PARTS,
                "mode": "rules",
                "checks": [
                    {"rule": "interference", "pairs": "all"},
                    {
                        "rule": "clearance",
                        "pairs": [["lid", "tray"]],
                        "min_mm": 0.15,
                        "why": "the lid must drop in without a press fit",
                    },
                    {
                        "rule": "probe",
                        "point": [30, 20, 10],
                        "expect": "AIR",
                        "reason": "the tray must stay hollow",
                    },
                ],
            },
        )
        c = result.data
        rows = []
        for row in c.get("findings", []):
            subject = ",".join(str(s) for s in row.get("subject", []))
            magnitude = " ".join(f"{k}={v}" for k, v in (row.get("magnitude") or {}).items())
            rows.append(f"{row['status']:<10} {row['rule']:<13} {subject:<12} {magnitude}".rstrip())
        summary = c.get("summary", {})
        rows.append(
            f"\npass {summary.get('pass', 0)}  fail {summary.get('fail', 0)}  "
            f"unresolved {summary.get('unresolved', 0)}  -> exit code {c.get('exit_code')}"
        )
        show("check(mode=rules)", "\n".join(rows))

        return 0 if c.get("exit_code") == 0 else 1


if __name__ == "__main__":
    # FastMCP installs its own handler at INFO and relays every server-side log
    # record back to the client. Quiet it so the output below is just results.
    logging.getLogger("fastmcp").setLevel(logging.WARNING)
    sys.exit(asyncio.run(main()))
