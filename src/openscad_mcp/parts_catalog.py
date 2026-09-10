"""Catalog of purchased ("vitamin") parts, with a BOSL2 module for each.

A language model designing a bracket needs to know how big the motor is, where
its screws go and which way its shaft points. Guessing produces a bracket that
does not fit. This module ships a small number of thoroughly sourced parts, each
paired with a generated OpenSCAD file under ``parts/`` that provides three
modules:

``part_<id>()``
    The part as purchased, a BOSL2 ``attachable()`` with named anchors on the
    features you mate to: ``mount-plane``, ``shaft-axis``, ``hole-a`` and so on.
``part_<id>_mask(clr, bore_clr, install, install_len)``
    The negative: the pocket the part sits in, with separate running clearance
    on flat body faces and bore clearance on round features, plus an optional
    straight-line install sweep so the part can be put in and taken out.
``part_<id>_mount_holes_mask(d, h)``
    Just the screw holes, kept separate so the caller picks tap, clearance or
    heat-set diameter.

All three share one coordinate frame, so the same ``anchor=`` argument puts the
solid, its pocket and its screw holes in the same place.

Data policy, which is the point of this module:

* Every dimension carries a ``source`` and a ``confidence`` (the same
  ``standard`` / ``consensus`` / ``calibrate`` labels the ``reference`` module
  uses).
* A number nobody publishes does not get invented. It goes in the entry's
  ``verify`` list, which is not optional and is never empty, and the OpenSCAD
  file exposes it as a parameter you can override after you measure your part.
* Dimensions are facts and are not copyrightable. The generated modules are
  original work, MIT licensed with the rest of openscad-mcp.

Public API::

    list_parts() -> list[dict]
    lookup_part(query, detailed=False) -> dict | None
    part_scad_source(part_id) -> str
    part_scad_path(part_id) -> Path
    self_check(part_id, openscad="openscad") -> dict
"""

from __future__ import annotations

import copy
import math
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

__all__ = [
    "PARTS",
    "PARTS_SUMMARY",
    "PARTS_NOTES",
    "list_parts",
    "lookup_part",
    "part_scad_source",
    "part_scad_path",
    "reference_entries",
    "self_check",
]

PARTS_DIR = Path(__file__).parent / "parts"

#: Tolerance for the geometric self-check, in millimetres.
SELF_CHECK_TOL_MM = 0.05

_MIT = (
    "Dimensions are facts and are not copyrightable. The OpenSCAD module is "
    "original work, MIT licensed with the rest of openscad-mcp. BOSL2 is used, "
    "not vendored; it is BSD-2-Clause."
)

# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

_SRC_BYJ_DRAWING = (
    "Manufacturer drawing '28BYJ-48 - 5V Stepper Motor', "
    "https://components101.com/sites/default/files/component_datasheet/28byj48-step-motor-datasheet.pdf"
    " (identical drawing mirrored at https://cdn-shop.adafruit.com/datasheets/28byj48dimension.jpg)"
)
_SRC_BYJ_MEASURED = (
    "Cookie Robotics, measured drawing and reproduced STEP model, "
    "https://cookierobotics.com/042/ (explicitly not manufacturer data)"
)
_SRC_BYJ_NOPSCAD = (
    "nophead, NopSCADlib vitamins/geared_steppers.scad 28BYJ_48 parameters, "
    "https://raw.githubusercontent.com/nophead/NopSCADlib/master/vitamins/geared_steppers.scad"
)
_SRC_BYJ_STEPD = (
    "OSEPP STEPD-01 datasheet (electrical), "
    "https://www.mouser.com/datasheet/2/758/stepd-01-data-sheet-1143075.pdf"
)
_SRC_BYJ_MASS = (
    "Vendor consensus on mass, no datasheet value: Adafruit 858 'Weight: 37 g', "
    "https://www.adafruit.com/product/858 ; Electrokit 'Weight: 36 g', "
    "https://www.electrokit.com/en/stegmotor-28byj-48-5v-unipolar"
)

_SRC_NEMA_ICS16 = (
    "NEMA ICS 16-2001 Tables 2, 4 and 6, flange number 17, "
    "https://smoothieware.github.io/Webif-pack/documentation/web/images/ics16.pdf"
)
_SRC_NEMA_STEPPERONLINE = (
    "StepperOnline 17HS15-1504S-X1 full datasheet, drawing A0660, "
    "https://www.omc-stepperonline.com/index.php?route=product/product/get_file&file=839/17HS15-1504S-X1_Full_Datasheet.pdf"
)
_SRC_NEMA_GEMS = (
    "GEMS Motor GM42BYG NEMA 17 datasheet, "
    "https://gemsmotor.com/stepper/nema17-stepper-motor.pdf"
)
_SRC_NEMA_MOTIONKING = (
    "MotionKing 17HS series datasheet MK1106 Rev.04, "
    "https://www.laskakit.cz/user/related_files/17hsxxxx-motionking.pdf"
)
_SRC_NEMA_BOSL2 = (
    "BOSL2 nema_steppers.scad, nema_motor_info(17), "
    "https://github.com/BelfrySCAD/BOSL2/blob/master/nema_steppers.scad"
)

_SRC_LS_TRIANGLE = (
    "Triangle Manufacturing Co. part 4C dimensioned drawing, change level "
    "J 03-01-23, https://www.triangleoshkosh.com/media/catalog/product/technical-images/4C.jpg"
    " (product page https://www.triangleoshkosh.com/lazy-susan-turntable-bearing-4c)"
)
_SRC_LS_ROCKLER = (
    "Rockler 28969 low-profile lazy susan, https://www.rockler.com/low-profile-lazy-susans"
    " and instruction sheet https://go.rockler.com/tech/28951-985.pdf"
)
_SRC_LS_LEEVALLEY = (
    "Lee Valley lazy susan bearings selection table, "
    "https://assets.leevalley.com/Original/10091/44042-lazy-susan-bearings-c-01-e.pdf"
)

_SRC_KW_ZHONGXUN = (
    "Zhejiang Zhongxun Electronics KW11-3Z outline drawing (UL file E203463), "
    "http://www.zxgroup.com/upload/201611/28/201611280805157094.jpg ; option table "
    "http://www.zxgroup.com/upload/201611/28/201611280804568024.jpg ; ratings "
    "http://www.zxgroup.com/upload/201611/28/201611280807259956.jpg ; product page "
    "http://www.zxgroup.com/zxdzwx/showproducts.aspx?id=838"
)
_SRC_KW_OMRON = (
    "Omron SS series datasheet Cat. No. X303-E-1 (the footprint this family "
    "copies), https://www.mouser.com/datasheet/2/307/SS_1110-14757.pdf"
)
_SRC_KW_KW12 = (
    "Soldered KW12-3 microswitch datasheet, "
    "https://www.mouser.com/datasheet/2/1398/Soldered_101411_microswitch_kw12_3-3532466.pdf"
)

_SRC_TC_VISHAY = "Vishay TCRT5000 datasheet, document 83760 rev 1.7, https://www.vishay.com/docs/83760/tcrt5000.pdf"
_SRC_TC_HANDSONTEC = (
    "Handsontec TCRT5000 IR line detection sensor module, "
    "https://handsontec.com/index.php/product/tcrt5000-infrared-ir-line-detection-sensor-module/"
)
_SRC_TC_VARIANTS = (
    "Board-outline variants across vendors: OpenImpulse 32x14 "
    "https://www.openimpulse.com/blog/products-page/product-category/tcrt5000-infrared-sensor-module/ ; "
    "dfh.fm ~32x14 with two 3 mm holes on 28 mm centres https://dfh.fm/products/tcrt5000-sensor ; "
    "Twinschip 35x10 "
    "https://www.twinschip.com/Infrared_Line_Tracking_%20Sensor_Module_TCRT5000"
)


def _anchor(pos: list[float], direction: str, note: str) -> dict[str, Any]:
    """One named anchor: where it is in the part frame, and which way it faces."""
    return {"pos_mm": [round(float(v), 4) for v in pos], "dir": direction, "note": note}


# --------------------------------------------------------------------------
# 1. 28BYJ-48
# --------------------------------------------------------------------------

# Geometry constants, used both for the entry and to derive the anchor
# positions, so the two can never drift apart.
_BYJ_BODY_H = 19.0
_BYJ_SHAFT_LEN = 10.0
_BYJ_REACH = 17.0
_BYJ_ORG = [(_BYJ_REACH - 28.0 / 2) / 2, 0.0, -_BYJ_SHAFT_LEN / 2]


def _byj_a(x: float, y: float, z: float) -> list[float]:
    return [_BYJ_ORG[0] + x, _BYJ_ORG[1] + y, _BYJ_ORG[2] + z]


_28BYJ48: dict[str, Any] = {
    "id": "28byj-48",
    "name": "28BYJ-48 5 V unipolar geared stepper motor",
    "aliases": ["28byj48", "28byj", "byj48", "blue stepper", "uln2003 stepper", "5v stepper"],
    "category": "motor",
    "scad_file": "28byj-48.scad",
    "envelope_mm": [31.0, 42.0, 29.0],
    "body": (
        "Ø28 x 19 steel can. A 1 mm stamped tab plate with two R3.5 ears caps the "
        "shaft face, spanning 42 mm tip to tip. The plastic connector box hangs off "
        "one side, 14.6 mm wide, reaching 17 mm from the body axis, and sets the "
        "-X limit of the envelope; the can sets +X. The shaft is offset 8 mm from "
        "the body axis, away from the connector box."
    ),
    "mount": {
        "pattern": "2 holes on a line through the body axis, perpendicular to the shaft offset",
        "hole_spacing_mm": 35.0,
        "hole_dia_mm": 4.2,
        "screw": "M3 or M4 through the 4.2 mm tab hole; M3 with a washer is usual",
        "thickness_mm": 1.0,
        "plane": "the tab plate face, which is the shaft face of the can",
    },
    "interface": {
        "shaft_dia_mm": 5.0,
        "shaft_len_mm": 10.0,
        "shaft_len_datum": "tip to the front face, includes the boss",
        "flat": "double D, 3.0 mm across both flats, 6 mm long measured back from the tip",
        "boss_dia_mm": 9.0,
        "boss_h_mm": 1.5,
        "shaft_offset_mm": 8.0,
    },
    "mass_g": 36.5,
    "electrical": (
        "5 V DC, 4 phase unipolar, 5 wires. 50 Ω ±7 % per phase at 25 °C. Stride "
        "angle 5.625°/64, so 2048 full steps per output revolution at the nominal "
        "1/64 reduction. ~240 mA in use. JST XH 2.5 mm 5-pin connector, wires "
        "blue / pink / yellow / orange / red, red being the common centre tap. "
        "Normally driven by a ULN2003 board."
    ),
    "modules": {
        "solid": "part_28byj48",
        "mask": "part_28byj48_mask",
        "mount_holes_mask": "part_28byj48_mount_holes_mask",
        "info": "part_28byj48_info",
    },
    "anchors": {
        "mount-plane": _anchor(
            _byj_a(0, 0, _BYJ_BODY_H / 2), "UP", "tab plate face; this is what bolts down"
        ),
        "shaft-axis": _anchor(
            _byj_a(8, 0, _BYJ_BODY_H / 2), "UP", "shaft axis where it leaves the face"
        ),
        "boss-top": _anchor(
            _byj_a(8, 0, _BYJ_BODY_H / 2 + 1.5), "UP", "top of the Ø9 boss the hub rests on"
        ),
        "shaft-tip": _anchor(_byj_a(8, 0, _BYJ_BODY_H / 2 + 10.0), "UP", "end of the shaft"),
        "hole-a": _anchor(_byj_a(0, 17.5, _BYJ_BODY_H / 2), "UP", "tab screw hole, +Y ear"),
        "hole-b": _anchor(_byj_a(0, -17.5, _BYJ_BODY_H / 2), "UP", "tab screw hole, -Y ear"),
        "wire-exit": _anchor(
            _byj_a(-17.0, 0, -_BYJ_BODY_H / 2 + 16.5 / 2),
            "LEFT",
            "outer face of the connector box; the harness leaves here",
        ),
        "body-back": _anchor(_byj_a(0, 0, -_BYJ_BODY_H / 2), "DOWN", "back of the can"),
    },
    "confidence": "standard",
    "sources": [
        _SRC_BYJ_DRAWING,
        _SRC_BYJ_MEASURED,
        _SRC_BYJ_NOPSCAD,
        _SRC_BYJ_STEPD,
        _SRC_BYJ_MASS,
    ],
    "license_note": _MIT,
    "verify": [
        "Tab plate thickness. Modelled at 1.0 mm. It is on no manufacturer "
        "drawing; Cookie Robotics measured 1.0 and NopSCADlib models 0.85.",
        "Connector box axial height. Modelled at 16.5 mm from NopSCADlib's CAD "
        "parameters only; no drawing dimensions it.",
        "Mass. Modelled at 36.5 g. No datasheet gives a mass; vendors say 36-37 g "
        "and one outlier says 30 g.",
        "Whether the 10 ±0.5 mm shaft protrusion is measured from the tab plate "
        "face or from the can face. The drawing dimensions both 10 and 19 from one "
        "datum and does not separate the 1 mm plate, so the two readings differ by "
        "1 mm. This model measures from the plate face.",
        "Gear ratio. The datasheet and the OEM say 1/64, and two independent "
        "teardowns counted 64. But units with 1/63.68395 and with 1/16.128 ship "
        "under the same part number, so steps per revolution must be measured per "
        "batch, not assumed.",
        "The 42 mm tab span is derived (35 + 2 x R3.5), not printed on any drawing.",
    ],
}

# --------------------------------------------------------------------------
# 2. NEMA 17
# --------------------------------------------------------------------------

_N17_BODY_H = 40.0
_N17_SHAFT_LEN = 24.0
_N17_ORG = [0.0, 0.0, -_N17_SHAFT_LEN / 2]


def _n17_a(x: float, y: float, z: float) -> list[float]:
    return [_N17_ORG[0] + x, _N17_ORG[1] + y, _N17_ORG[2] + z]


_NEMA17: dict[str, Any] = {
    "id": "nema17",
    "name": "NEMA 17 stepper motor, 17HS4401 class (42 mm frame, 40 mm body)",
    "aliases": [
        "nema 17",
        "nema-17",
        "17hs4401",
        "17hs15-1504s",
        "42 stepper",
        "42bygh",
        "stepper motor",
    ],
    "category": "motor",
    "scad_file": "nema17.scad",
    "envelope_mm": [42.3, 42.3, 64.0],
    "body": (
        "42.3 mm square body, 40 mm long, corners relieved (modelled as BOSL2's "
        "2 mm chamfer). A Ø22 x 2 pilot boss stands proud of the front face and "
        "the Ø5 shaft rises 24 mm from that face, so the overall height is 64 mm. "
        "Built as a thin delta over BOSL2's nema_stepper_motor(): that module "
        "already gets the NEMA frame right, and this file supplies the real body "
        "length, the real shaft length and the D-flat, which BOSL2 leaves to the "
        "caller."
    ),
    "mount": {
        "pattern": "4 holes on a 31 mm square, one per corner of the front face",
        "hole_spacing_mm": 31.0,
        "hole_dia_mm": 3.0,
        "screw": "M3, tapped into the motor 4.5 mm deep; use M3 x 8 to M3 x 10",
        "thickness_mm": 4.5,
        "plane": "the front face of the body, around the pilot boss",
    },
    "interface": {
        "shaft_dia_mm": 5.0,
        "shaft_len_mm": 24.0,
        "shaft_len_datum": "tip to the front face; the boss is inside this length",
        "flat": "single D, 4.5 mm across the flat (0.5 mm deep), 15 mm long back from the tip",
        "boss_dia_mm": 22.0,
        "boss_h_mm": 2.0,
        "shaft_offset_mm": 0.0,
    },
    "mass_g": 280.0,
    "electrical": (
        "Bipolar, 4 wires, 1.8° per full step. '17HS4401' covers two electrically "
        "different motors and you must check which you have: the MotionKing part is "
        "1.7 A, 1.5 Ω, 2.8 mH, 40 N·cm; the Usongshine/StepperOnline respin is "
        "1.5 A, 2.3-2.4 Ω, 3.7-4.4 mH, 42-45 N·cm. Wire colours on the "
        "StepperOnline part are A+ black, A- green, B+ red, B- blue."
    ),
    "modules": {
        "solid": "part_nema17",
        "mask": "part_nema17_mask",
        "mount_holes_mask": "part_nema17_mount_holes_mask",
        "info": "part_nema17_info",
    },
    "anchors": {
        "mount-plane": _anchor(
            _n17_a(0, 0, _N17_BODY_H / 2), "UP", "front face; this is what bolts down"
        ),
        "shaft-axis": _anchor(
            _n17_a(0, 0, _N17_BODY_H / 2), "UP", "shaft axis at the front face; on the body axis"
        ),
        "boss-top": _anchor(_n17_a(0, 0, _N17_BODY_H / 2 + 2.0), "UP", "top of the Ø22 pilot boss"),
        "shaft-tip": _anchor(_n17_a(0, 0, _N17_BODY_H / 2 + 24.0), "UP", "end of the shaft"),
        "flat-face": _anchor(
            _n17_a(2.0, 0, _N17_BODY_H / 2 + 24.0 - 15.0 / 2),
            "RIGHT",
            "middle of the D-flat; set a grub screw against this",
        ),
        "hole-a": _anchor(_n17_a(15.5, 15.5, _N17_BODY_H / 2), "UP", "M3 mounting hole, +X +Y"),
        "hole-b": _anchor(_n17_a(-15.5, 15.5, _N17_BODY_H / 2), "UP", "M3 mounting hole, -X +Y"),
        "hole-c": _anchor(_n17_a(-15.5, -15.5, _N17_BODY_H / 2), "UP", "M3 mounting hole, -X -Y"),
        "hole-d": _anchor(_n17_a(15.5, -15.5, _N17_BODY_H / 2), "UP", "M3 mounting hole, +X -Y"),
        "wire-exit": _anchor(
            _n17_a(0, 0, -_N17_BODY_H / 2), "DOWN", "back face; the harness leaves here"
        ),
    },
    "confidence": "standard",
    "sources": [
        _SRC_NEMA_ICS16,
        _SRC_NEMA_STEPPERONLINE,
        _SRC_NEMA_GEMS,
        _SRC_NEMA_MOTIONKING,
        _SRC_NEMA_BOSL2,
    ],
    "license_note": _MIT,
    "verify": [
        "Pilot boss height, modelled at 2.0 mm. No manufacturer datasheet read "
        "gives it. It comes from BOSL2's nema_motor_info table and sits inside "
        "the 0.76-2.29 mm band NEMA ICS 16 allows for pilot depth T.",
        "D-flat depth and length, modelled at 0.5 mm deep and 15 mm long. Both are "
        "read off StepperOnline's drawing image ('4.5 ±0.1' and '15 ±0.25') with no "
        "visible leader line, and the 0.5 mm depth is arithmetic on the 5.0 mm "
        "shaft. GEMS' drawing carries '10 ±1' in a comparable position, so flat "
        "length very likely varies by manufacturer.",
        "Shaft-end chamfer. Not modelled; no source found.",
        "The 2 mm corner chamfer is BOSL2's model of the body, not a datasheet "
        "value. Real bodies have rolled or radiused corners that vary.",
        "42.3 mm is NOT a NEMA number. ICS 16 lists the flange square BD as "
        "'approximate values for reference only ... determined by the "
        "manufacturer'. Nor is M3: ICS 16 specifies 4-40 for the C flange. Both "
        "are universal manufacturer convention, not standard.",
        "Electrical rating. Pick 1.5 A or 1.7 A deliberately; '17HS4401' is sold "
        "as both and the resistance and inductance differ by 50 %.",
        "Body length. Everyone says 40 mm, but StepperOnline's own page title says "
        "39 and one vendor lists 38.",
    ],
}

# --------------------------------------------------------------------------
# 3. 4 inch square lazy susan bearing
# --------------------------------------------------------------------------

_LS_H = 8.13
_LS_Z = _LS_H / 2
_LS_B_OUTER = 89.69 / 2 / math.sqrt(2)
_LS_B_INNER = 74.61 / 2 / math.sqrt(2)

_LAZY_SUSAN: dict[str, Any] = {
    "id": "lazy-susan-4in",
    "name": "4 inch square lazy susan turntable bearing",
    "aliases": [
        "lazy susan",
        "lazysusan",
        "turntable bearing",
        "4in lazy susan",
        "100mm lazy susan",
        "triangle 4c",
        "rockler 28969",
    ],
    "category": "bearing",
    "scad_file": "lazy-susan-4in.scad",
    "envelope_mm": [101.6, 101.6, 8.13],
    "body": (
        "Two stamped steel plates, each a true 4 inch square (101.60 mm, not "
        "100 mm), riveted around a 3 inch ball race, 8.13 mm tall assembled, with "
        "a Ø54.86 open centre. The model is the INSTALLED ENVELOPE: one solid slab "
        "with the centre opening through it and the four bolt patterns as blind "
        "holes in the face each belongs to. It is not a model of the internals. "
        "The plates are electrogalvanised steel on every source checked, not "
        "aluminium. Corners are R5.94 at the manufacturer's option, so the model "
        "leaves them square, which is the conservative envelope."
    ),
    "mount": {
        "pattern": (
            "16 holes, 8 per plate, in 4 radial pairs per plate. Plate A (+Z) has "
            "its pairs on the X and Y axes; plate B (-Z) has its pairs on the "
            "diagonals. Each pair is one clearance hole outboard and one "
            "self-tapper pilot inboard."
        ),
        "hole_spacing_mm": {
            "a_outer_bolt_circle": 80.96,
            "a_inner_bolt_circle": 69.85,
            "b_outer_bolt_circle": 89.69,
            "b_inner_bolt_circle": 74.61,
        },
        "hole_dia_mm": {"clearance": 3.97, "pilot": 2.39},
        "screw": (
            "#6 wood screw through the 3.97 mm hole into a 5/32 in pilot, per "
            "Rockler's instruction sheet; or #10 flat head stove bolts through a "
            "7/32 in counterbored hole"
        ),
        "thickness_mm": 0.91,
        "plane": "either outer face; the two plates counter-rotate",
    },
    "interface": {
        "bore_dia_mm": 54.86,
        "ball_circle_dia_mm": 76.2,
        "load_rating_lb": 300,
        "suggested_turntable_dia_in": [12, 25],
        "note": (
            "The open centre is the whole point: you reach through it to drive the "
            "screws in the plate below. Access to plate B's screws needs plate A "
            "rotated so its holes line up with the opening."
        ),
    },
    "mass_g": None,
    "electrical": None,
    "modules": {
        "solid": "part_lazy_susan_4in",
        "mask": "part_lazy_susan_4in_mask",
        "mount_holes_mask": "part_lazy_susan_4in_mount_holes_mask",
        "info": "part_lazy_susan_4in_info",
    },
    "anchors": {
        "mount-plane": _anchor([0, 0, _LS_Z], "UP", "plate A face, holes on the X and Y axes"),
        "mount-plane-b": _anchor([0, 0, -_LS_Z], "DOWN", "plate B face, holes on the diagonals"),
        "axis": _anchor([0, 0, 0], "UP", "rotation axis at mid height"),
        "center-bore": _anchor([0, 0, _LS_Z], "UP", "centre of the Ø54.86 opening, plate A side"),
        "hole-a": _anchor([80.96 / 2, 0, _LS_Z], "UP", "plate A outer, Ø3.97 clearance"),
        "hole-b": _anchor([69.85 / 2, 0, _LS_Z], "UP", "plate A inner, Ø2.39 pilot"),
        "hole-c": _anchor([_LS_B_OUTER, _LS_B_OUTER, -_LS_Z], "DOWN", "plate B outer, Ø3.97"),
        "hole-d": _anchor([_LS_B_INNER, _LS_B_INNER, -_LS_Z], "DOWN", "plate B inner, Ø2.39"),
    },
    "confidence": "standard",
    "sources": [_SRC_LS_TRIANGLE, _SRC_LS_ROCKLER, _SRC_LS_LEEVALLEY],
    "license_note": _MIT,
    "verify": [
        "Plate sheet thickness, modelled at 0.91 mm. Triangle publishes three "
        "mutually inconsistent figures: the drawing's stacked limit dimension "
        "reads 0.032-0.058 in (0.81-1.47 mm), the spec table says 20 gauge "
        "(0.91 mm) and the marketing copy on the same page says 22 gauge "
        "(0.76 mm). Measure yours.",
        "Which hole size sits on which bolt circle. The drawing does not letter "
        "the holes. This model puts the Ø3.97 clearance hole on the outer circle "
        "and the Ø2.39 pilot inboard, inferred from the leader endpoints and from "
        "Triangle's own install instructions ('use the smallest holes as a "
        "template'). Check before drilling.",
        "Which plate is which. The drawing shows the two hole groups but does not "
        "say which belongs to the top plate. This model calls the axis-aligned "
        "group plate A and puts it on +Z.",
        "Overall height. The drawing says 0.32 in (8.13 mm); Rockler and "
        "Triangle's own marketing both say 5/16 in (7.94 mm).",
        "Centre opening. Triangle says 2.16 in (54.86 mm); Rockler 28969 says "
        "2 1/8 in (53.98 mm). Probably genuinely different tooling.",
        "Mass. Not published by anyone. Rockler shows a unitless group weight that "
        "is the same for the 3 inch part, so it is unusable.",
        "Triangle's own spec table lists 'Mount Hole Center to Center: 2.16\"', "
        "which equals its centre-hole diameter and matches none of the four "
        "patterns on its drawing. That is a CMS data-entry error. Trust the "
        "drawing.",
    ],
}

# --------------------------------------------------------------------------
# 4. KW11-3Z microswitch
# --------------------------------------------------------------------------

_KW_BODY_H = 9.8
_KW_FREE_H = 10.7
_KW_HH = _KW_BODY_H / 2
_KW_ORG = [0.0, 0.0, -(_KW_FREE_H - _KW_BODY_H) / 2]


def _kw_a(x: float, y: float, z: float) -> list[float]:
    return [_KW_ORG[0] + x, _KW_ORG[1] + y, _KW_ORG[2] + z]


_KW11_3Z: dict[str, Any] = {
    "id": "kw11-3z",
    "name": "KW11-3Z / KW12-3 miniature snap-action microswitch, SPDT",
    "aliases": [
        "kw11",
        "kw11-3z",
        "kw12",
        "kw12-3",
        "microswitch",
        "micro switch",
        "limit switch",
        "endstop switch",
        "ss-5gl",
    ],
    "category": "switch",
    "scad_file": "kw11-3z.scad",
    "envelope_mm": [20.0, 6.4, 10.7],
    "body": (
        "20 x 6.4 x 9.8 moulded case with a Ø4.2 plunger standing 0.9 mm proud, so "
        "10.7 mm overall to the free position. Three terminals leave the bottom, "
        "COM then NO then NC. Levers screw the envelope up and are ordered "
        "separately: 14.4, 16.8, 18, 22, 24, 31.5 and 56 mm straight, or 17 and "
        "19 mm with a Ø4.6 or Ø6 roller. The model is the plunger-only variant; "
        "pass lever= to add one."
    ),
    "mount": {
        "pattern": "2 holes through the 6.4 mm thickness, on a line parallel to the body length",
        "hole_spacing_mm": 9.5,
        "hole_dia_mm": 2.5,
        "screw": "M2 or M2.3; Omron specifies M2.3 for the same 9.5 mm pitch",
        "thickness_mm": 6.4,
        "plane": "either 20 x 9.8 side face; the switch bolts through, not down",
    },
    "interface": {
        "actuator": "Ø4.2 plunger on the top face, pushed along -Z",
        "free_position_mm": 10.7,
        "operating_point_mm": 0.5,
        "operating_point_tol_mm": [-0.2, 0.3],
        "operating_force_g": [100, 200],
        "operating_force_b_option_g": [150, 350],
        "terminal_pitch_mm": {"com_to_no": 8.8, "com_to_nc": 16.0},
        "terminal_thickness_mm": 0.5,
    },
    "mass_g": None,
    "electrical": (
        "SPDT. CQC 5 A 250 VAC T105; UL 5 A 125/250 VAC and 10 A 125/250 VAC "
        "T125; TÜV 5 A 250 VAC T85. Dielectric 500 VAC/5 s between live parts, "
        "1500 VAC/5 s live to accessible metal. The Omron SS it copies is rated "
        "30 million mechanical and 200 000 electrical operations."
    ),
    "modules": {
        "solid": "part_kw11_3z",
        "mask": "part_kw11_3z_mask",
        "mount_holes_mask": "part_kw11_3z_mount_holes_mask",
        "info": "part_kw11_3z_info",
    },
    "anchors": {
        "mount-plane": _anchor(
            _kw_a(0, -3.2, 0), "FWD", "the -Y side face, which bolts to a bracket"
        ),
        "hole-a": _anchor(_kw_a(-4.75, -3.2, -_KW_HH + 2.8), "FWD", "mounting hole, -X"),
        "hole-b": _anchor(_kw_a(4.75, -3.2, -_KW_HH + 2.8), "FWD", "mounting hole, +X"),
        "plunger-tip": _anchor(
            _kw_a(0, 0, -_KW_HH + _KW_FREE_H), "UP", "plunger in its free position"
        ),
        "operating-pt": _anchor(
            _kw_a(0, 0, -_KW_HH + _KW_FREE_H - 0.5),
            "UP",
            "where the switch trips, 0.5 mm below the free position",
        ),
        "term-com": _anchor(_kw_a(-8.0, 0, -_KW_HH), "DOWN", "common terminal"),
        "term-no": _anchor(_kw_a(0.8, 0, -_KW_HH), "DOWN", "normally-open terminal"),
        "term-nc": _anchor(_kw_a(8.0, 0, -_KW_HH), "DOWN", "normally-closed terminal"),
    },
    "confidence": "standard",
    "sources": [_SRC_KW_ZHONGXUN, _SRC_KW_OMRON, _SRC_KW_KW12],
    "license_note": _MIT,
    "verify": [
        "Where the mounting holes sit along the 20 mm body. Only the 9.5 mm pitch "
        "is dimensioned. This model centres the pair on the body; Omron's "
        "equivalent runs 5.1 / 9.5 / 19.8, which is 5.2 mm at the far end, so "
        "close to but not exactly symmetric.",
        "Where the plunger sits along the body. Not dimensioned on any drawing "
        "found. This model puts it on the body centreline. If you are designing a "
        "cam or a lever, measure it.",
        "Where the terminal group sits along the body. The 8.8 and 16 mm pitches "
        "are dimensioned; their absolute position is not. This model centres the "
        "16 mm span.",
        "Terminal length below the case, and terminal width. Not dimensioned. The "
        "terminals are therefore NOT modelled; use the mask's term_len parameter "
        "to reserve the space you need. Omron gives 0.5 mm plate thickness for the "
        "solder-lug type, which is the only terminal number here that is sourced.",
        "Mass. No manufacturer figure. The Omron pin-plunger equivalent is about "
        "1.6 g; a KW12-3 vendor sheet says 5 g, probably including the lever.",
        "Overtravel, release force and differential travel. Not on the Zhongxun "
        "sheet. Omron's SS-5GL gives OT 1.2 mm min, RF 6 g min, MD 0.8 mm max.",
        "This part is NOT the '28.5 x 16 x 10 mm' switch it is often listed as. "
        "That is the larger V-15 / KW7 family. If your switch measures 28 mm long, "
        "this entry is the wrong part.",
    ],
}

# --------------------------------------------------------------------------
# 5. TCRT5000 module
# --------------------------------------------------------------------------

_TC_BOARD_T = 1.6
_TC_SENSOR_H = 7.0
_TC_ORG = [0.0, 0.0, -_TC_SENSOR_H / 2]


def _tc_a(x: float, y: float, z: float) -> list[float]:
    return [_TC_ORG[0] + x, _TC_ORG[1] + y, _TC_ORG[2] + z]


_TCRT5000: dict[str, Any] = {
    "id": "tcrt5000-module",
    "name": "TCRT5000 reflective infrared sensor breakout module",
    "aliases": [
        "tcrt5000",
        "tcrt 5000",
        "ir line sensor",
        "line tracking sensor",
        "reflective sensor",
        "line follower module",
    ],
    "category": "sensor",
    "scad_file": "tcrt5000-module.scad",
    "envelope_mm": [31.0, 14.0, 8.6],
    "body": (
        "31 x 14 FR-4 board carrying the Vishay TCRT5000 package, 10.2 x 5.8 x 7.0, "
        "standing on the component face and looking along +Z, plus an LM393 "
        "comparator and a trim pot. One Ø3 mounting hole. The pin header is not "
        "modelled: no vendor publishes its position, and the board ships with "
        "straight and right-angle headers depending on the seller."
    ),
    "mount": {
        "pattern": "1 hole",
        "hole_spacing_mm": None,
        "hole_dia_mm": 3.0,
        "screw": "M3 clearance, or M2.5 with a nylon standoff, or an M3 heat-set insert boss",
        "thickness_mm": 1.6,
        "plane": "the solder side of the board",
    },
    "interface": {
        "sensing": "reflective, 950 nm emitter with a daylight blocking filter",
        "peak_distance_mm": 2.5,
        "operating_range_mm": [0.2, 15.0],
        "sensor_package_mm": [10.2, 5.8, 7.0],
        "supply_v": [3.3, 5.0],
        "outputs": "digital DO via the LM393 with a threshold pot, plus analogue AO on 4-pin boards",
        "header_pitch_mm": 2.54,
    },
    "mass_g": None,
    "electrical": (
        "3.3-5 V DC, around 10 mA with the emitter on. Emitter 950 nm, IF 60 mA "
        "max, VF 1.25 V typical. Phototransistor VCEO 70 V, IC 100 mA max. Pins "
        "are VCC / GND / DO on 3-pin boards and VCC / GND / DO / AO on 4-pin "
        "boards. The centimetre detection ranges module vendors quote contradict "
        "Vishay; the datasheet range is 0.2 to 15 mm, best at 2.5 mm."
    ),
    "modules": {
        "solid": "part_tcrt5000_module",
        "mask": "part_tcrt5000_module_mask",
        "mount_holes_mask": "part_tcrt5000_module_mount_holes_mask",
        "info": "part_tcrt5000_module_info",
    },
    "anchors": {
        "mount-plane": _anchor(
            _tc_a(0, 0, -_TC_BOARD_T / 2), "DOWN", "solder side; this is what sits on a standoff"
        ),
        "board-top": _anchor(_tc_a(0, 0, _TC_BOARD_T / 2), "UP", "component side of the board"),
        "hole-a": _anchor(_tc_a(-12.5, 0, _TC_BOARD_T / 2), "UP", "the Ø3 mounting hole"),
        "sensor-face": _anchor(
            _tc_a(0, 0, _TC_BOARD_T / 2 + _TC_SENSOR_H), "UP", "optical face of the TCRT5000"
        ),
        "sense-point": _anchor(
            _tc_a(0, 0, _TC_BOARD_T / 2 + _TC_SENSOR_H + 2.5),
            "UP",
            "where the reflector should be: Vishay's 2.5 mm peak operating distance",
        ),
        "sense-far": _anchor(
            _tc_a(0, 0, _TC_BOARD_T / 2 + _TC_SENSOR_H + 15.0),
            "UP",
            "far end of the usable range; outside the part's own envelope",
        ),
    },
    "confidence": "consensus",
    "sources": [_SRC_TC_VISHAY, _SRC_TC_HANDSONTEC, _SRC_TC_VARIANTS],
    "license_note": _MIT,
    "verify": [
        "Board outline. Modelled at 31 x 14 from Handsontec. Vendors ship 31 x 14, "
        "32 x 14, 34 x 10 and 35 x 10 boards under the same name. Measure yours "
        "and pass board_l / board_w.",
        "Mounting hole count and position. Modelled as one Ø3 hole 3.0 mm in from "
        "the -X edge on the centreline. The hole diameter is sourced; its POSITION "
        "is not sourced anywhere. Some boards have two holes on 28 mm centres "
        "instead. Pass hole_x, or measure.",
        "Where the TCRT5000 package sits on the board. Not sourced. This model "
        "centres it, which is almost certainly wrong for a line-follower board "
        "where the sensor is at one end. Pass sensor_x once you have measured.",
        "Board thickness. Modelled at 1.6 mm, the usual FR-4 thickness. No vendor " "publishes it.",
        "Standoff between the board and the underside of the sensor package. "
        "Modelled as zero (the package sits on the board). Not sourced.",
        "Pin header position, orientation and height. Not modelled; not published.",
        "Mass. One vendor says 4.5 g. No second source.",
        "Lens spacing and lead pitch of the bare TCRT5000. These exist only inside "
        "the drawing image on page 5 of Vishay document 83760, which has no text "
        "layer. Do not assume 2.54 mm lead pitch for the package: that is the "
        "module's header pitch, which is a different thing.",
    ],
}

PARTS: list[dict[str, Any]] = [_28BYJ48, _NEMA17, _LAZY_SUSAN, _KW11_3Z, _TCRT5000]

PARTS_SUMMARY = (
    "Purchased parts (vitamins) with a sourced dimension sheet and a BOSL2 "
    "module for each: 28BYJ-48 and NEMA 17 steppers, a 4 inch lazy susan "
    "bearing, a KW11-3Z microswitch and a TCRT5000 sensor module. Each ships "
    "part_<id>(), part_<id>_mask() and part_<id>_mount_holes_mask() with named "
    "anchors on the mating features."
)

PARTS_NOTES = [
    "Every entry has a non-empty `verify` list. Those are the numbers nobody "
    "publishes, left as module parameters rather than invented. Read it before "
    "you cut anything, and measure your own part.",
    "The three modules for a part share one coordinate frame, so the same "
    "anchor= argument places the solid, its pocket and its screw holes "
    'identically: difference() { plate(); part_nema17_mask(anchor="mount-plane"); '
    'part_nema17_mount_holes_mask(d=3.4, anchor="mount-plane"); }',
    "The part origin is the centre of the overall bounding box, so the BOSL2 "
    "attachable size is the real geometry and TOP, RIGHT and so on are exact. "
    "Use the NAMED anchors for the datums you actually mate to.",
    "Masks are authored for plain difference(). Do NOT wrap them in BOSL2 "
    "tag() or diff(): tags do not cross a use<> boundary, so the mask silently "
    "unions into your part instead of cutting it.",
    "The mask separates `clr`, running clearance on flat body faces, from "
    "`bore_clr`, clearance on round shafts and bosses, because a shaft bore "
    "usually wants less than a body pocket.",
    "install_len sweeps the whole mask along install so the part can be put in "
    "and taken out. It is a minkowski() with a thin prism, which is exact for "
    "concave parts and takes a second or two. A hull() of two poses is wrong "
    "for anything concave: it fills in the concavity.",
    "Round features are pinned to $fn=64 so a clearance is not eaten by a "
    "polygon cutting inside the true circle, and so the solid and its mask "
    "polygonise identically.",
    "Confidence labels match the reference module: standard means a published "
    "standard or manufacturer drawing, consensus means several vendors agree "
    "with no governing document.",
]


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _brief(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "name": entry["name"],
        "category": entry["category"],
        "envelope_mm": list(entry["envelope_mm"]),
    }


def list_parts() -> list[dict[str, Any]]:
    """Return every catalogued part as ``{id, name, category, envelope_mm}``."""
    return [_brief(entry) for entry in PARTS]


def lookup_part(query: str, detailed: bool = False) -> dict[str, Any] | None:
    """Find one part by id, alias or substring.

    Args:
        query: An id (``"nema17"``), an alias (``"17hs4401"``, ``"blue
            stepper"``) or any substring of the id, name or aliases
            (``"lazy"``, ``"switch"``). Case-insensitive, and punctuation is
            ignored so ``"NEMA 17"``, ``"nema-17"`` and ``"nema17"`` all match.
        detailed: When False the long prose fields (``body``, ``electrical``,
            ``anchors``, ``sources``) are dropped and the entry is a compact
            summary. ``verify``, ``confidence`` and ``license_note`` are kept
            either way, because those are the fields you must not miss.

    Returns:
        A deep copy of the entry, or None if nothing matched. Exact id and
        alias matches win over substring matches.
    """
    needle = _normalise(query)
    if not needle:
        return None

    for entry in PARTS:
        if _normalise(entry["id"]) == needle:
            return _shape(entry, detailed)
    for entry in PARTS:
        if any(_normalise(alias) == needle for alias in entry["aliases"]):
            return _shape(entry, detailed)
    for entry in PARTS:
        haystacks = [entry["id"], entry["name"], *entry["aliases"], entry["category"]]
        if any(needle in _normalise(text) for text in haystacks):
            return _shape(entry, detailed)
    return None


def _normalise(text: str) -> str:
    """Lowercase and strip everything that is not a letter or a digit."""
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


#: Prose and provenance fields dropped from the compact view. The structured
#: blocks a caller designs against -- mount, interface, anchors -- are kept,
#: because dropping them would make the compact view useless for the job it
#: exists to do.
_COMPACT_DROP = ("body", "electrical", "sources", "aliases")


def _shape(entry: dict[str, Any], detailed: bool) -> dict[str, Any]:
    out = copy.deepcopy(entry)
    if not detailed:
        for key in _COMPACT_DROP:
            out.pop(key, None)
        # Anchors collapse to name -> position. The direction and the prose note
        # are the detailed view's job; the position is what you place against.
        out["anchors"] = {name: list(anchor["pos_mm"]) for name, anchor in entry["anchors"].items()}
    return out


def part_scad_path(part_id: str) -> Path:
    """Return the path of a part's generated OpenSCAD file.

    Raises:
        KeyError: If ``part_id`` is not a catalogued part.
        FileNotFoundError: If the package data file is missing.
    """
    entry = _entry(part_id)
    path: Path = PARTS_DIR / str(entry["scad_file"])
    if not path.is_file():
        raise FileNotFoundError(
            f"Catalog file for {entry['id']!r} is missing: {path}. The parts/*.scad "
            f"files ship as package data; check the wheel was built with them."
        )
    return path


def part_scad_source(part_id: str) -> str:
    """Return the text of a part's generated OpenSCAD file."""
    return part_scad_path(part_id).read_text(encoding="utf-8")


def _entry(part_id: str) -> dict[str, Any]:
    for entry in PARTS:
        if entry["id"] == part_id:
            return entry
    known = ", ".join(entry["id"] for entry in PARTS)
    raise KeyError(f"Unknown part {part_id!r}. Catalogued parts: {known}")


def reference_entries() -> list[dict[str, Any]]:
    """Return the catalog shaped for ``reference.lookup(topic="parts")``.

    The reference module expects entries with ``name``, ``confidence``,
    ``source`` and optional ``keywords``, so this flattens ``sources`` into one
    pipe-joined string and lifts the aliases into keywords.
    """
    entries: list[dict[str, Any]] = []
    for part in PARTS:
        entries.append(
            {
                "name": part["name"],
                "id": part["id"],
                # The id goes in the keywords too: reference._entry_matches()
                # searches names, key NAMES and keywords, not arbitrary values,
                # so without this lookup("parts", "nema17") would find nothing.
                "keywords": [part["id"], *part["aliases"], part["category"]],
                "category": part["category"],
                "envelope_mm": list(part["envelope_mm"]),
                "mount": copy.deepcopy(part["mount"]),
                "modules": copy.deepcopy(part["modules"]),
                "anchors": sorted(part["anchors"]),
                "verify": list(part["verify"]),
                "license_note": part["license_note"],
                "note": part["body"],
                "confidence": part["confidence"],
                "source": " | ".join(part["sources"]),
            }
        )
    return entries


# --------------------------------------------------------------------------
# Self-check
# --------------------------------------------------------------------------

_EMPTY_MARKERS = ("top level object is empty", "current top level object is empty")


def _stl_bbox(path: Path) -> tuple[list[float], list[float]]:
    """Return (min, max) corners of an STL file. Handles ASCII and binary."""
    data = path.read_bytes()
    verts: list[tuple[float, float, float]] = []

    binary = False
    if len(data) >= 84:
        (count,) = struct.unpack("<I", data[80:84])
        binary = len(data) == 84 + 50 * count and count > 0

    if binary:
        (count,) = struct.unpack("<I", data[80:84])
        for i in range(count):
            base = 84 + i * 50 + 12
            for j in range(3):
                verts.append(struct.unpack("<3f", data[base + j * 12 : base + j * 12 + 12]))
    else:
        for line in data.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0] == "vertex":
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))

    if not verts:
        raise ValueError(f"No vertices in {path}")
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    return lo, hi


def _find_openscad(openscad: str) -> str | None:
    """Resolve an OpenSCAD binary, or None if it is not installed."""
    candidate = Path(openscad)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return shutil.which(openscad)


def _render(
    binary: str, workdir: Path, name: str, body: str, scad: Path, timeout: float
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    """Write a driver that use<>s the part file, render it, return the STL path."""
    driver = workdir / f"{name}.scad"
    driver.write_text(
        "// generated by openscad_mcp.parts_catalog.self_check\n"
        "include <BOSL2/std.scad>\n"
        f"use <{scad.as_posix()}>\n"
        f"{body}\n",
        encoding="utf-8",
    )
    out = workdir / f"{name}.stl"
    proc = subprocess.run(  # noqa: S603
        [binary, "-o", str(out), str(driver)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return out, proc


def self_check(part_id: str, openscad: str = "openscad", timeout: float = 180.0) -> dict[str, Any]:
    """Render a catalogued part and check the model against its own entry.

    Three checks, all run through the real OpenSCAD binary so that what is
    verified is the geometry, not the Python:

    1. **envelope** - export ``part_<id>()`` to STL and compare its bounding box
       to ``envelope_mm``.
    2. **anchor:<name>** - for each named anchor, export
       ``part_<id>() !position("<name>") cube(0.2, center=true)`` and check the
       marker cube's centre lands where the entry says it does.
    3. **containment** - ``difference() { part_<id>(); part_<id>_mask(clr=0,
       bore_clr=0); }`` must come out EMPTY. OpenSCAD signals that by exiting
       non-zero with "Current top level object is empty" and writing no file,
       and that is the pass condition. A mask that does not fully contain its
       part at zero clearance would leave slivers of material behind in every
       pocket cut with it.

    Args:
        part_id: A catalogued part id.
        openscad: Path to, or name of, the OpenSCAD binary.
        timeout: Per-render timeout in seconds.

    Returns:
        ``{"id", "openscad", "available", "ok", "tolerance_mm", "checks"}``.
        When OpenSCAD is not installed, ``available`` is False, ``ok`` is None
        and ``checks`` is empty; that is a skip, not a failure.
    """
    entry = _entry(part_id)
    binary = _find_openscad(openscad)
    result: dict[str, Any] = {
        "id": entry["id"],
        "openscad": binary,
        "available": binary is not None,
        "ok": None,
        "tolerance_mm": SELF_CHECK_TOL_MM,
        "checks": [],
    }
    if binary is None:
        result["skipped"] = f"OpenSCAD not found (looked for {openscad!r})"
        return result

    scad = part_scad_path(entry["id"])
    solid = entry["modules"]["solid"]
    mask = entry["modules"]["mask"]
    checks: list[dict[str, Any]] = result["checks"]

    with tempfile.TemporaryDirectory(prefix="oscad-selfcheck-") as tmp:
        workdir = Path(tmp)

        # 1. envelope
        try:
            out, proc = _render(binary, workdir, "solid", f"{solid}();", scad, timeout)
            if not out.is_file():
                checks.append(
                    {
                        "check": "envelope",
                        "ok": False,
                        "detail": f"no STL produced: {proc.stderr.strip()[-400:]}",
                    }
                )
                lo = hi = None
            else:
                lo, hi = _stl_bbox(out)
                measured = [round(hi[i] - lo[i], 4) for i in range(3)]
                expected = [float(v) for v in entry["envelope_mm"]]
                deltas = [round(measured[i] - expected[i], 4) for i in range(3)]
                checks.append(
                    {
                        "check": "envelope",
                        "ok": all(abs(d) <= SELF_CHECK_TOL_MM for d in deltas),
                        "expected_mm": expected,
                        "measured_mm": measured,
                        "delta_mm": deltas,
                    }
                )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            checks.append({"check": "envelope", "ok": False, "detail": str(exc)})

        # 2. named anchors
        for anchor_name, anchor in entry["anchors"].items():
            label = f"anchor:{anchor_name}"
            body = f'{solid}() !position("{anchor_name}") cube(0.2, center=true);'
            try:
                out, proc = _render(
                    binary, workdir, f"anchor_{_normalise(anchor_name)}", body, scad, timeout
                )
                if not out.is_file():
                    checks.append(
                        {
                            "check": label,
                            "ok": False,
                            "detail": f"no STL produced: {proc.stderr.strip()[-400:]}",
                        }
                    )
                    continue
                lo, hi = _stl_bbox(out)
                measured = [round((lo[i] + hi[i]) / 2, 4) for i in range(3)]
                expected = [float(v) for v in anchor["pos_mm"]]
                deltas = [round(measured[i] - expected[i], 4) for i in range(3)]
                checks.append(
                    {
                        "check": label,
                        "ok": all(abs(d) <= SELF_CHECK_TOL_MM for d in deltas),
                        "expected_mm": expected,
                        "measured_mm": measured,
                        "delta_mm": deltas,
                    }
                )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                checks.append({"check": label, "ok": False, "detail": str(exc)})

        # 3. containment
        body = f"difference() {{ {solid}(); " f"{mask}(clr=0, bore_clr=0, install_len=0); }}"
        try:
            out, proc = _render(binary, workdir, "containment", body, scad, timeout)
            stderr = (proc.stderr or "").lower()
            empty = any(marker in stderr for marker in _EMPTY_MARKERS)
            leftover = out.is_file()
            detail = "mask contains the part at zero clearance"
            if leftover:
                try:
                    lo, hi = _stl_bbox(out)
                    detail = (
                        "mask does NOT contain the part: material left over, "
                        f"bbox {[round(hi[i] - lo[i], 3) for i in range(3)]}"
                    )
                except ValueError:
                    detail = "mask does NOT contain the part: an STL was written"
            elif not empty:
                detail = f"render failed: {proc.stderr.strip()[-400:]}"
            checks.append(
                {
                    "check": "containment",
                    "ok": empty and not leftover,
                    "detail": detail,
                }
            )
        except (OSError, subprocess.SubprocessError) as exc:
            checks.append({"check": "containment", "ok": False, "detail": str(exc)})

    result["ok"] = all(check["ok"] for check in checks)
    result["failed"] = [check["check"] for check in checks if not check["ok"]]
    return result
