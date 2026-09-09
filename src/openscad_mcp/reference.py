"""Static engineering reference data for OpenSCAD part design.

This module ships sourced engineering numbers so that a language model designing
parts does not have to invent fit clearances, fastener dimensions or bearing
sizes from memory. Everything is hard-coded; nothing is read from disk at
runtime.

Every entry carries a ``confidence`` label:

``standard``
    Taken from a published standard or a manufacturer catalogue. The number is
    what the standard says.
``consensus``
    Widely used community practice with no governing standard. Reasonable
    default, not authoritative.
``calibrate``
    The honest answer is "print a test coupon". The number is a starting point
    only and will move with printer, material and slicer settings.

Public API::

    list_topics() -> list[dict]
    lookup(topic, query=None, detailed=False) -> dict
    conventions_brief() -> str
    cheatsheet() -> str
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = [
    "TOPICS",
    "CONFIDENCE_LEVELS",
    "list_topics",
    "lookup",
    "conventions_brief",
    "cheatsheet",
]

TOPICS = [
    "fits",
    "fasteners",
    "inserts",
    "bearings",
    "magnets",
    "joints",
    "conventions",
    "cheatsheet",
    "dfm",
    "materials",
]

CONFIDENCE_LEVELS = ("standard", "consensus", "calibrate")

# Fields stripped from entries when ``detailed=False``.
_VERBOSE_FIELDS = ("note", "keywords")

# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

_SRC_ISO_273 = "ISO 273 (clearance holes for bolts and screws); table via IS 1821:1987, identical to ISO 273-1979, https://law.resource.org/pub/in/bis/S01/is.1821.1987.pdf"  # noqa: E501
_SRC_ISO_4762 = "ISO 4762 (hexagon socket head cap screws), https://www.fasteners.eu/standards/ISO/4762/"  # noqa: E501
_SRC_ISO_4032 = "ISO 4032 (hexagon regular nuts, style 1); table as encoded in BOSL2 screws.scad _nut_info_metric, https://github.com/BelfrySCAD/BOSL2/blob/master/screws.scad"  # noqa: E501
_SRC_TAP_DRILL = "Fuller Fasteners recommended tapping drill sizes (ISO metric coarse), https://fullerfasteners.com/tech/recommended-tapping-drill-size/"  # noqa: E501
_SRC_SLOP = "BOSL2 constants.scad, $slop and get_slop(), https://github.com/BelfrySCAD/BOSL2/wiki/constants.scad"  # noqa: E501
_SRC_POLYHOLES = "nophead, 'Polyholes' (printed holes come out undersized), https://hydraraptor.blogspot.com/2011/02/polyholes.html"  # noqa: E501
_SRC_CNC_KITCHEN = "CNC Kitchen, Heat-Set Insert Dimensions and Design Guidelines (PDF), https://www.cnckitchen.com/s/CNCKitchen_Heat-Set-Insert-Dimensions-and-Design-Guidelines.pdf"  # noqa: E501
_SRC_SPIROL = "SPIROL, How to Design the Proper Hole for Heat/Ultrasonic Inserts, https://www.spirol.com/resources/white-papers/how-to-design-the-proper-hole-for-heat-ultrasonic-inserts/"  # noqa: E501
_SRC_BOSL2_BEARINGS = "BOSL2 ball_bearings.scad trade size table, https://github.com/BelfrySCAD/BOSL2/blob/master/ball_bearings.scad"  # noqa: E501
_SRC_IGUS_BEARINGS = "igus ball bearing dimensions table, https://www.igus.eu/ball-bearings/wiki/ball-bearings-dimensions-table"  # noqa: E501
_SRC_MISUMI = "MISUMI miniature ball bearing size pages, https://us.misumi-ec.com/blog/ball-bearings/miniature/"
_SRC_SUPERMAGNETE = "supermagnete disc magnet catalogue (nominal sizes and tolerances), https://www.supermagnete.de/eng/disc-magnets-neodymium"  # noqa: E501
_SRC_BOSL2_JOINERS = "BOSL2 joiners.scad, https://github.com/BelfrySCAD/BOSL2/wiki/joiners.scad"
_SRC_BOSL2_PARTITIONS = (
    "BOSL2 partitions.scad, https://github.com/BelfrySCAD/BOSL2/wiki/partitions.scad"  # noqa: E501
)
_SRC_BOSL2_HINGES = "BOSL2 hinges.scad, https://github.com/BelfrySCAD/BOSL2/wiki/hinges.scad"
_SRC_OPENSCAD_MANUAL = "OpenSCAD User Manual, https://en.wikibooks.org/wiki/OpenSCAD_User_Manual"
_SRC_BASF_SNAPFIT = "BASF Snap-Fit Design Manual (cantilever beam snap-fit design), https://web.mit.edu/2.75/resources/random/Snap-Fit%20Design%20Manual.pdf"  # noqa: E501
_SRC_SIMPLIFY3D = "Simplify3D Materials Guide properties table, https://www.simplify3d.com/resources/materials-guide/properties-table/"  # noqa: E501
_SRC_FORMLABS_DENSITY = "Formlabs, Density of selected Formlabs SLA resins, https://formlabs.com/support/Density-of-selected-Formlabs-SLA-resins/"  # noqa: E501
_SRC_PRUSA_PETG = "Prusament PETG technical data sheet (density per ISO 1183), https://prusament.com/wp-content/uploads/2023/07/PETG_V0_ENG.pdf"  # noqa: E501
_SRC_XOMETRY_FILAMENT = "Xometry, Types of 3D Printer Filaments, https://www.xometry.com/resources/3d-printing/types-of-3d-printer-filaments/"  # noqa: E501
_SRC_FDM_PRACTICE = "Community FDM practice; consistent with the BOSL2 $slop calibration procedure, https://github.com/BelfrySCAD/BOSL2/wiki/constants.scad#constant-slop"  # noqa: E501

# --------------------------------------------------------------------------
# 1. fits
# --------------------------------------------------------------------------

_FITS: list[dict[str, Any]] = [
    {
        "name": "press fit",
        "keywords": ["interference", "force fit", "hammer fit", "pin"],
        "clearance_per_side_mm": 0.0,
        "clearance_per_side_range_mm": [-0.05, 0.0],
        "clearance_diametral_mm": 0.0,
        "clearance_diametral_range_mm": [-0.10, 0.0],
        "confidence": "calibrate",
        "note": (
            "Zero to slight interference: model hole and shaft at the same nominal size, or "
            "make the hole up to 0.05 mm/side smaller. Because FDM holes already print "
            "undersized, a nominally zero-clearance pair usually needs real force. Requires a "
            "printed test coupon; a press fit that is 0.05 mm too tight splits thin walls."
        ),
        "source": _SRC_FDM_PRACTICE,
    },
    {
        "name": "snap fit / light interference",
        "keywords": ["snap", "light press", "detent", "click"],
        "clearance_per_side_mm": 0.05,
        "clearance_per_side_range_mm": [0.0, 0.10],
        "clearance_diametral_mm": 0.10,
        "clearance_diametral_range_mm": [0.0, 0.20],
        "confidence": "calibrate",
        "note": (
            "Assembles by hand with a noticeable click and stays put. Pair with a lead-in "
            "chamfer of 0.5-1 mm at 30-45 degrees or the parts will not start. Print a test "
            "coupon: the difference between a satisfying click and a pair that will not go "
            "together at all is about 0.05 mm."
        ),
        "source": _SRC_FDM_PRACTICE,
    },
    {
        "name": "close running fit (slip)",
        "keywords": ["slip", "running", "close", "shaft", "rotating"],
        "clearance_per_side_mm": 0.10,
        "clearance_per_side_range_mm": [0.08, 0.15],
        "clearance_diametral_mm": 0.20,
        "clearance_diametral_range_mm": [0.15, 0.30],
        "confidence": "consensus",
        "note": (
            "Parts slide or rotate with light finger pressure and little play. This is the "
            "clearance most FDM users converge on, and it matches the 0.10-0.15 mm range that "
            "the BOSL2 $slop calibration part typically lands on for a well-tuned printer."
        ),
        "source": _SRC_FDM_PRACTICE,
    },
    {
        "name": "free running fit (loose)",
        "keywords": ["loose", "free", "clearance", "sloppy"],
        "clearance_per_side_mm": 0.20,
        "clearance_per_side_range_mm": [0.15, 0.30],
        "clearance_diametral_mm": 0.40,
        "clearance_diametral_range_mm": [0.30, 0.60],
        "confidence": "consensus",
        "note": (
            "Falls together with obvious play. Use where the parts must never bind: hinge "
            "barrels, shafts running dry, anything printed in place or printed on a machine "
            "you have not calibrated."
        ),
        "source": _SRC_FDM_PRACTICE,
    },
    {
        "name": "sliding lid / drawer fit",
        "keywords": ["lid", "drawer", "slide", "tray", "rail", "flat"],
        "clearance_per_side_mm": 0.25,
        "clearance_per_side_range_mm": [0.20, 0.40],
        "clearance_diametral_mm": 0.50,
        "clearance_diametral_range_mm": [0.40, 0.80],
        "confidence": "consensus",
        "note": (
            "Long flat sliding surfaces need more clearance than a short round one because "
            "warp and bow accumulate over the length. Above roughly 80 mm of travel move "
            "toward the top of the range, and relieve the middle of the slot so only the ends "
            "make contact."
        ),
        "source": _SRC_FDM_PRACTICE,
    },
    {
        "name": "screw clearance fit",
        "keywords": ["screw", "bolt", "through hole", "fastener"],
        "clearance_per_side_mm": 0.20,
        "clearance_per_side_range_mm": [0.10, 0.30],
        "clearance_diametral_mm": 0.40,
        "clearance_diametral_range_mm": [0.20, 0.60],
        "confidence": "standard",
        "note": (
            "For screws use the ISO 273 medium series rather than a generic clearance: M3 "
            "gets a 3.4 mm hole, M4 gets 4.5 mm, M5 gets 5.5 mm. See the 'fasteners' topic "
            "for the full table. The per-side figures here are just the ISO 273 medium "
            "series expressed as clearance. Add hole compensation on top for FDM."
        ),
        "source": _SRC_ISO_273,
    },
]

_FITS_NOTES = [
    "Vertical holes (axis parallel to Z) print close to nominal. Horizontal holes (axis in "
    "the XY plane) print undersized by roughly 0.2-0.4 mm on the diameter because the "
    "unsupported top of the bore sags and the polygonal approximation cuts inside the true "
    "circle. Oversize horizontal holes by that much, or model them as a teardrop/hexagon so "
    "the roof is self-supporting. Confidence: consensus.",
    "OpenSCAD renders a circle as an inscribed polygon, so a cylinder with a low $fn is "
    "already smaller than the nominal diameter before the printer touches it. Either raise "
    "$fn or scale the radius by 1/cos(180/$fn). Confidence: standard.",
    "Elephant foot: the first layer squashes outward by roughly 0.1-0.3 mm, so the bottom "
    "few tenths of a millimetre of every part is oversized and every bottom hole is "
    "undersized. Fix it in the slicer with elephant-foot compensation, or design a 0.5 mm "
    "45-degree chamfer on bottom edges. Confidence: consensus.",
    "Typical achievable FDM tolerance in XY is around +/-0.1 to +/-0.2 mm on a tuned "
    "consumer machine. Do not design a fit that needs better than that without measuring "
    "your own printer first. Confidence: consensus.",
    "BOSL2 exposes a $slop special variable for exactly this problem. Its default is 0.0, "
    "and it is applied PER SIDE: BOSL2 grows a hole radius by get_slop() and a hole "
    "diameter by 2*get_slop(). Set it once near the top of your file, after the include, "
    "e.g. '$slop = 0.15;'. Print the calibration part in the BOSL2 $slop documentation to "
    "find your own value; 0.10-0.20 is the usual landing zone. Pass it as a keyword "
    "argument (threaded_nut(..., $slop=0.17)) when one feature needs a different value. "
    "Confidence: standard.",
    "Always read the slop or clearance value out of a single named top-level variable so "
    "that one edit retunes the whole model.",
]

# --------------------------------------------------------------------------
# 2. fasteners
# --------------------------------------------------------------------------


def _fastener(
    name: str,
    nominal: float,
    pitch: float,
    close: float,
    medium: float,
    free: float,
    tap: float,
    head_d: float,
    head_h: float,
    nut_af: float,
    nut_h: float,
    counterbore: float,
) -> dict[str, Any]:
    return {
        "name": name,
        "keywords": ["metric", "screw", "bolt", "shcs", "nut"],
        "nominal_diameter_mm": nominal,
        "thread_pitch_coarse_mm": pitch,
        "clearance_hole_close_mm": close,
        "clearance_hole_medium_mm": medium,
        "clearance_hole_free_mm": free,
        "tap_drill_mm": tap,
        "shcs_head_diameter_mm": head_d,
        "shcs_head_height_mm": head_h,
        "hex_nut_width_across_flats_mm": nut_af,
        "hex_nut_height_mm": nut_h,
        "counterbore_diameter_mm": counterbore,
        "confidence": "standard",
        "note": (
            f"Clearance holes are the ISO 273 fine/medium/coarse series; use the medium "
            f"column ({medium} mm) unless you have a reason not to. Tap drill is the ISO "
            f"metric coarse recommendation. Socket head cap screw head is ISO 4762 (head "
            f"height equals the nominal diameter for this series). Hex nut is ISO 4032 "
            f"style 1, height given as the maximum. Counterbore is head diameter plus "
            f"0.5 mm of clearance, which is a working rule rather than a standard value; "
            f"for machined parts consult DIN 974-1 instead."
        ),
        "source": f"{_SRC_ISO_273} | {_SRC_TAP_DRILL} | {_SRC_ISO_4762} | {_SRC_ISO_4032}",
    }


_FASTENERS: list[dict[str, Any]] = [
    #        name    nom  pitch close  med  free   tap  headD headH nutAF nutH  cbore
    _fastener("M2", 2.0, 0.40, 2.2, 2.4, 2.6, 1.60, 3.8, 2.0, 4.0, 1.6, 4.3),
    _fastener("M2.5", 2.5, 0.45, 2.7, 2.9, 3.1, 2.05, 4.5, 2.5, 5.0, 2.0, 5.0),
    _fastener("M3", 3.0, 0.50, 3.2, 3.4, 3.6, 2.50, 5.5, 3.0, 5.5, 2.4, 6.0),
    _fastener("M4", 4.0, 0.70, 4.3, 4.5, 4.8, 3.30, 7.0, 4.0, 7.0, 3.2, 7.5),
    _fastener("M5", 5.0, 0.80, 5.3, 5.5, 5.8, 4.20, 8.5, 5.0, 8.0, 4.7, 9.0),
    _fastener("M6", 6.0, 1.00, 6.4, 6.6, 7.0, 5.00, 10.0, 6.0, 10.0, 5.2, 10.5),
    _fastener("M8", 8.0, 1.25, 8.4, 9.0, 10.0, 6.80, 13.0, 8.0, 13.0, 6.8, 13.5),
]

_FASTENERS_NOTES = [
    "Counterbore depth for a socket head cap screw should be the head height plus 0.2-0.4 mm "
    "so the head finishes below the surface. Confidence: consensus.",
    "Do not tap threads directly into FDM plastic for anything that will be undone more than "
    "a few times. Use a heat-set insert (see the 'inserts' topic) or a captive nut trap.",
    "A self-tapping screw driven straight into printed plastic wants a pilot hole around the "
    "thread root diameter, roughly the nominal diameter minus the pitch, and at least two "
    "perimeters of material around it. Confidence: consensus.",
    "Printed clearance holes come out undersized. Either add hole compensation or drill/ream "
    "the hole after printing if the screw must pass freely.",
]

# --------------------------------------------------------------------------
# 3. inserts (heat-set)
# --------------------------------------------------------------------------


def _insert(
    name: str,
    hole_d: float,
    insert_len: float,
    insert_od: float,
    hole_depth: float,
    wall: float,
    boss_od: float,
) -> dict[str, Any]:
    return {
        "name": name,
        "keywords": ["heat set", "heatset", "threaded insert", "brass", "voron", "ruthex"],
        "hole_diameter_mm": hole_d,
        "insert_length_mm": insert_len,
        "insert_outer_diameter_mm": insert_od,
        "recommended_hole_depth_mm": hole_depth,
        "min_wall_thickness_mm": wall,
        "suggested_boss_outer_diameter_mm": boss_od,
        "confidence": "consensus",
        "note": (
            f"Check your insert's datasheet. Heat-set insert dimensions vary by vendor and "
            f"even by product line within one vendor. These are the CNC Kitchen values, "
            f"which ruthex matches on hole diameter for {name} to within 0.1 mm but not "
            f"always on length. Hole is straight, not tapered, and needs no chamfer. Blind "
            f"holes get roughly 1 mm of extra depth so displaced plastic has somewhere to "
            f"go. Suggested boss outer diameter is insert OD plus twice the minimum wall; "
            f"SPIROL recommends a more conservative boss of 2-3x the insert diameter for "
            f"injection-moulded parts, which is worth following on load-bearing bosses."
        ),
        "source": f"{_SRC_CNC_KITCHEN} | {_SRC_SPIROL}",
    }


_INSERTS: list[dict[str, Any]] = [
    #      name   holeD  len   OD   depth  wall  bossOD
    _insert("M2", 3.2, 3.0, 3.6, 4.0, 1.3, 6.2),
    _insert("M2.5", 4.0, 4.0, 4.6, 5.0, 1.6, 7.8),
    _insert("M3", 4.0, 5.7, 4.6, 6.7, 1.6, 7.8),
    _insert("M3 Voron (short)", 4.4, 4.0, 5.0, 5.0, 1.6, 8.2),
    _insert("M4", 5.7, 8.1, 6.3, 9.1, 2.1, 10.5),
    _insert("M5", 6.5, 9.5, 7.1, 10.5, 2.6, 12.3),
]

_INSERTS_NOTES = [
    "Vendor spread is real. For M4 CNC Kitchen calls for a 5.7 mm hole and ruthex 5.6 mm; "
    "for M5 it is 6.5 versus 6.4 mm. For M2 and M2.5 the hole diameters agree but the insert "
    "lengths differ by 1.7 mm. Measure the inserts you actually bought.",
    "Printed holes come out undersized, so a hole modelled at the datasheet diameter often "
    "ends up tight. Print a test coupon with the hole at nominal, +0.1 and +0.2 mm before "
    "committing a boss pattern to a real part. Confidence: calibrate.",
    "Install with a temperature-controlled iron at roughly 30-50 C above the polymer's "
    "printing temperature, pressed in square and slowly. Set the insert flush or 0.1-0.2 mm "
    "below the surface, never proud.",
    "PLA holds heat-set inserts adequately but creeps under sustained preload. PETG, ABS and "
    "ASA hold better. Confidence: consensus.",
]

# --------------------------------------------------------------------------
# 4. bearings
# --------------------------------------------------------------------------


def _bearing(
    name: str,
    bore: float,
    od: float,
    width: float,
    common: str,
    source: str,
    confidence: str = "standard",
    extra_note: str = "",
) -> dict[str, Any]:
    note = (
        f"{common} Pocket for a press fit: model the bore of the pocket at the bearing OD "
        f"plus 0.0 to 0.1 mm and expect to press it in; at plus 0.2 mm it will be a slip fit "
        f"that needs retaining. Support the outer race only, never the inner race: leave a "
        f"shoulder no larger than roughly 2 mm smaller than the OD, and give the pocket floor "
        f"a relief so the seal cannot rub."
    )
    if extra_note:
        note = f"{note} {extra_note}"
    return {
        "name": name,
        "keywords": ["bearing", "ball bearing", "deep groove", "skate", "rc"],
        "bore_mm": bore,
        "outer_diameter_mm": od,
        "width_mm": width,
        "confidence": confidence,
        "note": note,
        "source": source,
    }


_BEARINGS: list[dict[str, Any]] = [
    _bearing(
        "608",
        8.0,
        22.0,
        7.0,
        "The skateboard bearing; by far the easiest to source.",
        f"{_SRC_BOSL2_BEARINGS} | {_SRC_IGUS_BEARINGS}",
    ),
    _bearing(
        "625",
        5.0,
        16.0,
        5.0,
        "Common small deep-groove bearing, often used on 3D printer idlers.",
        _SRC_IGUS_BEARINGS,
    ),
    _bearing(
        "623",
        3.0,
        10.0,
        4.0,
        "Small deep-groove bearing for M3 shafts and idler pulleys.",
        _SRC_IGUS_BEARINGS,
    ),
    _bearing(
        "626",
        6.0,
        19.0,
        6.0,
        "Deep-groove bearing for 6 mm shafts.",
        _SRC_IGUS_BEARINGS,
    ),
    _bearing(
        "6000",
        10.0,
        26.0,
        8.0,
        "Entry size of the 6000 series, for 10 mm shafts.",
        f"{_SRC_BOSL2_BEARINGS} | {_SRC_IGUS_BEARINGS}",
    ),
    _bearing(
        "688 (sealed, -2RS / -ZZ)",
        8.0,
        16.0,
        5.0,
        "Thin 8 mm-bore bearing widely used on printer idlers.",
        f"{_SRC_MISUMI} | {_SRC_IGUS_BEARINGS}",
        extra_note=(
            "Width depends on the variant: the open 688 is 4 mm wide, the sealed 688-2RS and "
            "shielded 688-ZZ are 5 mm. Consumer listings almost always mean the 5 mm sealed "
            "part. Check before you model the pocket depth."
        ),
    ),
    _bearing(
        "MR105 (shielded/sealed, -ZZ / -2RS)",
        5.0,
        10.0,
        4.0,
        "Miniature metric bearing for 5 mm shafts.",
        _SRC_MISUMI,
        extra_note=(
            "Width depends on the variant: the open MR105 is 3 mm wide, the shielded MR105ZZ "
            "and sealed MR105-2RS are 4 mm. Hobby suppliers almost always ship the 4 mm part."
        ),
    ),
]

_BEARINGS_NOTES = [
    "Bearing outer diameters are held to a few hundredths of a millimetre; your printer is "
    "not. Print a pocket test coupon at OD, OD+0.1 and OD+0.2 before committing. Confidence: "
    "calibrate.",
    "A printed press fit into plastic relaxes over days as the polymer creeps. For anything "
    "that must not fall out, add a mechanical retainer: a lip on one side, a snap ring "
    "groove, a screwed-on retaining plate, or a second part clamping the outer race.",
    "Press the bearing in cold and square. Heating the plastic to ease the fit destroys the "
    "interference you were relying on.",
    "Model the pocket bore as a vertical cylinder wherever you can. A bearing pocket whose "
    "axis lies in the XY plane prints undersized and out of round.",
]

# --------------------------------------------------------------------------
# 5. magnets
# --------------------------------------------------------------------------


def _magnet(name: str, dia: float, thickness: float, note_extra: str = "") -> dict[str, Any]:
    note = (
        "Nominal catalogue size; sintered neodymium discs are typically held to about "
        "+/-0.1 mm. Pocket: 0.1-0.2 mm clearance on the diameter for a glued-in magnet, or "
        "0.0-0.05 mm for a press fit, and 0.1-0.2 mm extra depth so the magnet sits at or "
        "just below the surface. Leave 0.4-0.8 mm of plastic (one or two layers) capping the "
        "pocket rather than exposing the magnet: it protects the nickel plating and the "
        "holding force barely changes over that distance."
    )
    if note_extra:
        note = f"{note} {note_extra}"
    return {
        "name": name,
        "keywords": ["magnet", "neodymium", "disc", "ndfeb"],
        "diameter_mm": dia,
        "thickness_mm": thickness,
        "pocket_diameter_glue_mm": round(dia + 0.15, 2),
        "pocket_diameter_press_mm": round(dia + 0.05, 2),
        "pocket_depth_mm": round(thickness + 0.15, 2),
        "confidence": "consensus",
        "note": note,
        "source": _SRC_SUPERMAGNETE,
    }


_MAGNETS: list[dict[str, Any]] = [
    _magnet(
        "6x3 disc",
        6.0,
        3.0,
        "The default hobby magnet: strong enough for lids and enclosure doors, small enough "
        "to hide in a 2 mm wall boss.",
    ),
    _magnet("8x3 disc", 8.0, 3.0),
    _magnet("10x3 disc", 10.0, 3.0),
    _magnet(
        "5x2 disc",
        5.0,
        2.0,
        "Useful where space is tight; holding force is modest, so use pairs.",
    ),
]

_MAGNETS_NOTES = [
    "Design the pocket so the magnet is inserted before the roof layer prints (pause the "
    "print) or from the back through an open face. A pocket with a printed roof and no back "
    "access cannot be filled.",
    "Get the polarity right. If two mating parts each hold a magnet, mark the intended pole "
    "in a comment and in an echo(), because you cannot tell from the render.",
    "Neodymium loses strength permanently above roughly 80 C for standard N grades. Do not "
    "put them near a heated bed or a hot end, and do not print over them at high nozzle "
    "temperatures for long.",
    "Actual holding force depends on grade, air gap and the steel it pulls against, so treat "
    "any force figure as calibrate-by-experiment.",
]

# --------------------------------------------------------------------------
# 6. joints
# --------------------------------------------------------------------------

_JOINTS: list[dict[str, Any]] = [
    {
        "name": "dovetail",
        "keywords": ["dovetail", "sliding", "tail", "socket"],
        "typical_angle_deg": 10.0,
        "angle_range_deg": [8.0, 14.0],
        "typical_clearance_per_side_mm": 0.15,
        "bosl2_modules": ["dovetail()"],
        "use_when": (
            "Joining two printed parts along a straight slide, or splitting a model too large "
            "for the bed. Resists everything except sliding along the joint axis."
        ),
        "confidence": "consensus",
        "note": (
            "Below about 8 degrees the tail pulls out under load; above about 14 degrees the "
            "thin corners of the tail become fragile and print badly. Print the slide axis "
            "vertically if you can, so the interlocking faces are layer boundaries in "
            'compression rather than in peel. BOSL2\'s dovetail() takes gender="male" or '
            '"female" plus width, height and slide, and accepts either angle or slope. Add '
            "a taper so the joint only tightens over the last few millimetres."
        ),
        "source": _SRC_BOSL2_JOINERS,
    },
    {
        "name": "snap-fit cantilever",
        "keywords": ["snap", "cantilever", "clip", "hook", "latch"],
        "typical_deflection_mm": 0.75,
        "deflection_range_mm": [0.5, 1.0],
        "insertion_angle_deg": 30.0,
        "retention_angle_deg": 90.0,
        "retention_angle_range_deg": [60.0, 90.0],
        "bosl2_modules": ["rabbit_clip()", "snap_pin()", "snap_pin_socket()"],
        "use_when": (
            "Tool-free assembly of a lid, cover or battery door. Best where the joint is "
            "opened occasionally, not constantly."
        ),
        "confidence": "consensus",
        "note": (
            "A 30-degree lead-in face lets the clip deflect on assembly; a 60-90 degree "
            "retention face decides whether it can be released (60-70) or is permanent (90). "
            "Keep peak strain low by tapering the beam toward the tip so it deflects along "
            "its whole length instead of hinging at the root, and fillet the root generously. "
            "Orient the beam so it flexes across layers, not so that layer lines run across "
            "the root in tension. PETG and ABS tolerate repeated flexing far better than PLA. "
            "The deflection figures are typical FDM practice; for a real stress calculation "
            "use the BASF cantilever formulas with your material's strain limit."
        ),
        "source": f"{_SRC_BOSL2_JOINERS} | {_SRC_BASF_SNAPFIT}",
    },
    {
        "name": "press-fit pin",
        "keywords": ["pin", "press", "dowel", "peg", "boss"],
        "typical_clearance_per_side_mm": 0.0,
        "clearance_range_per_side_mm": [-0.05, 0.05],
        "recommended_engagement_ratio": 2.0,
        "bosl2_modules": ["snap_pin()", "snap_pin_socket()"],
        "use_when": (
            "Aligning two parts that are also screwed or glued. A press-fit pin on its own is "
            "an alignment feature, not a fastening."
        ),
        "confidence": "calibrate",
        "note": (
            "Engage at least twice the pin diameter or the joint will rock. Chamfer the pin "
            "tip 0.5-1 mm at 45 degrees and give the hole a matching lead-in. Print pins "
            "standing up so the load is not peeling layers apart, and expect to tune the "
            "diameter on a test coupon. Use BOSL2's snap_pin()/snap_pin_socket() pair when "
            "you want the pin to latch rather than merely grip."
        ),
        "source": _SRC_BOSL2_JOINERS,
    },
    {
        "name": "tongue and groove",
        "keywords": ["tongue", "groove", "shiplap", "panel", "seam"],
        "typical_tongue_thickness_mm": 2.0,
        "tongue_thickness_range_mm": [1.6, 3.0],
        "typical_clearance_per_side_mm": 0.15,
        "bosl2_modules": ["partition()", "partition_mask()", "partition_cut_mask()"],
        "use_when": (
            "Aligning and light-sealing a lid to a box, or joining flat panels edge to edge."
        ),
        "confidence": "consensus",
        "note": (
            "Make the tongue a multiple of the extrusion width, typically two to four "
            "perimeters, so it prints solid. Widen the groove by twice the slop and shorten "
            "the tongue by one slop, which is exactly what the BOSL2 $slop documentation "
            "prescribes for this joint. Chamfer the tongue tip so it self-aligns. BOSL2's "
            'partition() splits a model along an interlocking cut path such as "jigsaw" or '
            '"dovetail", which does the same job for oversized parts.'
        ),
        "source": f"{_SRC_BOSL2_PARTITIONS} | {_SRC_SLOP}",
    },
    {
        "name": "living hinge",
        "keywords": ["living hinge", "hinge", "flexure", "fold", "bend"],
        "typical_thickness_mm": 0.4,
        "thickness_range_mm": [0.3, 0.5],
        "typical_width_mm": 10.0,
        "bosl2_modules": ["knuckle_hinge()", "living_hinge_mask()"],
        "use_when": (
            "A lid that folds rather than pivots, printed as one part, in polypropylene or " "PETG."
        ),
        "confidence": "consensus",
        "note": (
            "Material choice decides this, not geometry. Polypropylene survives thousands of "
            "cycles; PETG manages many. PLA cracks within a handful of folds and ABS is "
            "little better, so do not specify a living hinge in PLA. Keep the flexure one or "
            "two layers thick, 0.3-0.5 mm, and lay it flat on the bed so the fold axis runs "
            "along a layer. Fillet where the thin section meets the thick panels. BOSL2 "
            "offers living_hinge_mask() to cut the flexure and knuckle_hinge() for the "
            "printed-pivot alternative, which is the better choice in PLA."
        ),
        "source": _SRC_BOSL2_HINGES,
    },
    {
        "name": "mortise and tenon",
        "keywords": ["mortise", "tenon", "peg", "slot", "frame"],
        "typical_clearance_per_side_mm": 0.15,
        "recommended_tenon_thickness_ratio": 0.33,
        "use_when": (
            "Right-angle joints in a frame, where a dovetail cannot be slid into place. "
            "Usually glued or pinned."
        ),
        "confidence": "consensus",
        "note": (
            "Size the tenon at roughly a third of the stock thickness so neither the tenon "
            "nor the walls of the mortise become the weak part. On its own the joint resists "
            "shear but not pull-out; add a cross pin, a screw or adhesive. Cut the mortise "
            "slightly deeper than the tenon is long so the shoulders, not the tenon end, seat "
            "against the mating face."
        ),
        "source": _SRC_FDM_PRACTICE,
    },
    {
        "name": "screw boss",
        "keywords": ["boss", "screw", "post", "pillar", "self tapping"],
        "recommended_wall_thickness_mm": 2.0,
        "wall_thickness_range_mm": [1.6, 3.0],
        "recommended_engagement_ratio": 2.0,
        "use_when": "Fastening two parts with a screw where a nut cannot be reached.",
        "confidence": "consensus",
        "note": (
            "Give the boss at least two perimeters of wall around the hole, roughly 1.6-2 mm "
            "with a 0.4 mm nozzle, and engage at least twice the screw diameter of thread. "
            "Add a fillet where the boss meets the wall or it snaps off at the root; add a "
            "small relief gap between the boss and any adjacent wall so the two do not fuse "
            "into a stress riser. For repeated assembly fit a heat-set insert instead of "
            "screwing into plastic; see the 'inserts' topic."
        ),
        "source": _SRC_FDM_PRACTICE,
    },
    {
        "name": "captive nut trap",
        "keywords": ["nut trap", "captive nut", "hex", "pocket", "nut"],
        "typical_clearance_across_flats_mm": 0.2,
        "clearance_across_flats_range_mm": [0.1, 0.3],
        "typical_depth_clearance_mm": 0.2,
        "use_when": (
            "A strong, reusable, cheap threaded joint in a printed part when you have room "
            "for a nut and access to insert it."
        ),
        "confidence": "consensus",
        "note": (
            "Size the hexagonal pocket across the flats from the ISO 4032 dimension in the "
            "'fasteners' topic plus 0.1-0.3 mm total, and the depth at nut height plus about "
            "0.2 mm so the nut cannot rock but the screw still pulls the parts together. Two "
            "shapes work: a slot in the side wall that the nut slides into, or a pocket in "
            "the top face that the print bridges over, which needs the pocket roof to be a "
            "clean bridge. A captive nut is much stronger than a thread cut into plastic and "
            "survives being undone repeatedly."
        ),
        "source": _SRC_ISO_4032,
    },
]

_JOINTS_NOTES = [
    "Every clearance figure in this topic assumes a calibrated printer. Drive them all from "
    "one top-level slop variable so a single edit retunes the model.",
    "Print orientation matters more than the joint geometry. Layer adhesion is the weak "
    "direction, so orient the joint so working loads act across layers in compression or "
    "shear rather than pulling layers apart.",
    "BOSL2 module names in this topic were checked against the installed library source and "
    "the BOSL2 wiki. joiners.scad provides dovetail(), snap_pin(), snap_pin_socket(), "
    "rabbit_clip(), joiner(), half_joiner() and hirth(); partitions.scad provides "
    "partition(), partition_mask() and partition_cut_mask().",
]

# --------------------------------------------------------------------------
# 7. conventions
# --------------------------------------------------------------------------


def _convention(name: str, rule: str, why: str, keywords: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "keywords": keywords,
        "rule": rule,
        "confidence": "consensus",
        "note": why,
        "source": _SRC_OPENSCAD_MANUAL,
    }


_CONVENTIONS: list[dict[str, Any]] = [
    _convention(
        "units",
        "All dimensions are millimetres. Never mix in inches; multiply by 25.4 at the point "
        "of entry and comment the conversion.",
        "OpenSCAD is unitless and every downstream slicer assumes millimetres. BOSL2 defines "
        "an INCH constant equal to 25.4 if you need it.",
        ["mm", "millimetre", "units", "inch", "scale"],
    ),
    _convention(
        "coordinate system",
        "Z is up, the coordinate system is right-handed, and the XY plane is the build plate.",
        "This matches the printer, the slicer and OpenSCAD's own preview, so a part that "
        "looks right in the render sits right on the bed.",
        ["z up", "axis", "right handed", "coordinates", "orientation"],
    ),
    _convention(
        "part origin",
        "Put the origin at a meaningful datum. For a printed part that is normally "
        "bottom-centre: centred in X and Y with the base at Z=0. State the datum in a comment "
        "at the top of the module.",
        "A consistent datum makes parts composable and means the part is already sitting on "
        "the bed when exported. A reader cannot infer the datum from the geometry, so say it.",
        ["origin", "datum", "bottom center", "anchor", "reference"],
    ),
    _convention(
        "module per part",
        "One module per physical part. Give a top-level part parameter, or separate named "
        "modules, so a single file can render any one part or the whole assembly.",
        "Each printed part must be exportable on its own, and the assembly view is what "
        "reveals interference.",
        ["module", "part", "assembly", "structure", "organisation"],
    ),
    _convention(
        "epsilon overlap",
        "Overlap coplanar boundaries in union() and difference() by a small epsilon, 0.01 mm. "
        "Extend a cutting solid beyond both faces it passes through. Never inset.",
        "Exactly coincident faces produce zero-thickness geometry that renders as z-fighting "
        "in preview and as non-manifold errors in F6 and in the slicer. Insetting instead "
        "leaves a real, printed film of plastic across the hole.",
        ["epsilon", "overlap", "coplanar", "z-fighting", "manifold", "difference"],
    ),
    _convention(
        "named dimensions",
        "Declare every key dimension as a commented top-level variable. No magic numbers "
        "inside geometry.",
        "It is the only way a later reader, human or model, can retune the part. Include the "
        "unit and the intent in the comment.",
        ["variables", "parameters", "magic numbers", "naming"],
    ),
    _convention(
        "explicit clearance",
        "Make clearance a named variable, for example 'clearance = 0.15; // per side, tuned "
        "on this printer'. Apply it in one place per mating feature.",
        "Fits are the thing most likely to need retuning after the first print. Scattered "
        "literals make that a rewrite instead of an edit. If BOSL2 is in use, set $slop once "
        "and let its modules apply it.",
        ["clearance", "slop", "tolerance", "fit", "variable"],
    ),
    _convention(
        "facet resolution",
        "Use $fn moderately: 24 for draft iteration, 64 or more for the final render and "
        "export. Set it per-object rather than globally where a large model would get slow.",
        "A high global $fn makes every preview slow for no visual gain, and a low one makes "
        "cylinders measurably undersized because OpenSCAD inscribes the polygon.",
        ["$fn", "$fa", "$fs", "resolution", "facets", "smooth", "circle"],
    ),
    _convention(
        "library preference",
        "Prefer 'include <BOSL2/std.scad>' when BOSL2 is installed, and use its attachment, "
        "rounding and joiner modules rather than reimplementing them.",
        "BOSL2 gives you anchoring, chamfers and fillets, and a tested $slop mechanism. Check "
        "availability first with the get_libraries tool.",
        ["bosl2", "library", "include", "use", "mcad"],
    ),
    _convention(
        "state assumptions",
        "echo() the assumptions and the derived key dimensions: clearances used, computed "
        "overall size, which fastener is expected.",
        "The echo output comes back with the render, so the assumptions become checkable "
        "instead of hidden. Pair with assert() for the ones that must hold.",
        ["echo", "assert", "assumptions", "debug", "output"],
    ),
    _convention(
        "measure, do not eyeball",
        "Verify dimensions with the analyze_model measurement tool, not by looking at the "
        "rendered image.",
        "A render has no scale and a perspective camera distorts it. Two parts that look "
        "flush in a preview can be a millimetre apart. Measure.",
        ["measure", "verify", "analyze", "bounding box", "check"],
    ),
]

_CONVENTIONS_NOTES = [
    "These are house conventions for this server, chosen so that generated models are "
    "readable, retunable and printable. They are not an OpenSCAD standard.",
    "conventions_brief() returns the same guidance condensed for a system prompt.",
]

# --------------------------------------------------------------------------
# 8. cheatsheet entries
# --------------------------------------------------------------------------


def _syntax(name: str, rule: str, keywords: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "keywords": keywords,
        "rule": rule,
        "confidence": "standard",
        "note": "OpenSCAD language behaviour, as documented in the user manual.",
        "source": _SRC_OPENSCAD_MANUAL,
    }


_CHEATSHEET: list[dict[str, Any]] = [
    _syntax(
        "variables are compile-time",
        "Variables are not assignable at run time. Within a scope the LAST assignment wins "
        "and applies to the whole scope, including lines above it. You cannot accumulate into "
        "a variable inside a for loop; use a list comprehension, a recursive function, or "
        "let() to bind a new value in a child scope.",
        ["variable", "assignment", "scope", "reassign", "last wins"],
    ),
    _syntax(
        "for unions its children",
        "A for() loop is not a loop in the imperative sense: it instantiates its children "
        "once per value and implicitly unions the results. To intersect them instead use "
        "intersection_for().",
        ["for", "loop", "union", "intersection_for"],
    ),
    _syntax(
        "difference subtracts all but the first",
        "difference() keeps the first child and subtracts every later child from it. Order "
        "matters. If nothing is subtracted, check that the cutting solid is actually a later "
        "child and that it protrudes past both surfaces.",
        ["difference", "subtract", "boolean", "order"],
    ),
    _syntax(
        "center semantics",
        "cube() and square() default to center=false and sit in the positive octant; "
        "cylinder() defaults to center=false and sits on Z=0; sphere() is always centred on "
        "the origin. center=true centres about the origin in every axis.",
        ["center", "cube", "cylinder", "square", "origin"],
    ),
    _syntax(
        "transform order",
        "Transformations apply innermost-first to the child. translate([0,0,10]) rotate([0,90,0]) "
        "cube() rotates the cube and then moves it. rotate() takes degrees and applies X, "
        "then Y, then Z when given a vector.",
        ["translate", "rotate", "mirror", "scale", "order", "transform"],
    ),
    _syntax(
        "hull and minkowski",
        "hull() takes the convex hull of its children, which is the cheap way to make a "
        "rounded slot from two cylinders. minkowski() sweeps one shape over another to round "
        "edges but is very slow; prefer offset(), a hull of spheres, or BOSL2 rounding.",
        ["hull", "minkowski", "round", "fillet", "convex"],
    ),
    _syntax(
        "linear_extrude",
        "linear_extrude(height, center=false, convexity=1, twist=0, slices, scale=1) turns a "
        "2D child into a solid. twist is in degrees over the full height and needs enough "
        "slices to look smooth. scale may be a number or an [x,y] pair.",
        ["linear_extrude", "extrude", "2d", "twist", "scale"],
    ),
    _syntax(
        "rotate_extrude",
        "rotate_extrude(angle=360, convexity=2) revolves a 2D child about the Z axis. The "
        "child must lie entirely in X>=0; anything crossing the axis is an error.",
        ["rotate_extrude", "revolve", "lathe", "angle"],
    ),
    _syntax(
        "offset",
        "offset(r=) grows or shrinks a 2D shape with rounded corners; offset(delta=) keeps "
        "corners sharp, and offset(delta=, chamfer=true) chamfers them. 2D only. This is the "
        "standard way to make a shell or a clearance version of a profile.",
        ["offset", "2d", "shell", "chamfer", "delta"],
    ),
    _syntax(
        "projection",
        "projection(cut=false) flattens the whole 3D child's silhouette to 2D; "
        "projection(cut=true) takes the cross-section in the Z=0 plane only. Translate the "
        "object to choose the section height.",
        ["projection", "cut", "section", "silhouette", "2d"],
    ),
    _syntax(
        "modifier characters",
        "Prefix a statement with % to make it a transparent background object excluded from "
        "the result, # to highlight it in red while keeping it, ! to render only that subtree "
        "and ignore everything else, and * to disable it. % and # are for debugging and must "
        "be removed before export.",
        ["modifier", "debug", "highlight", "background", "root", "disable"],
    ),
    _syntax(
        "assert and echo",
        'assert(condition, "message") stops the render with your message and is the right '
        "way to enforce a design constraint such as a minimum wall. echo() prints to the "
        "console and is how you report computed dimensions back to the caller.",
        ["assert", "echo", "validate", "constraint", "debug"],
    ),
    _syntax(
        "let and list comprehensions",
        "let(a=1, b=2) binds values for one child expression or statement. List "
        "comprehensions build vectors: [for (i=[0:5]) i*2], with optional if and let clauses. "
        "each flattens a nested list into the surrounding one.",
        ["let", "list comprehension", "each", "vector", "list"],
    ),
    _syntax(
        "undef checks",
        "is_undef(x) tests whether a variable was never assigned. Also available: is_num, "
        "is_string, is_list, is_bool. Use them to give module parameters optional behaviour "
        "rather than sentinel values.",
        ["is_undef", "undef", "is_num", "is_list", "optional"],
    ),
    _syntax(
        "special variables for resolution",
        "$fn fixes the facet count outright and overrides the others. Otherwise $fa is the "
        "minimum facet angle in degrees (default 12) and $fs the minimum facet size in mm "
        "(default 2), and the finer of the two wins. Set them as arguments to a single "
        "object, cylinder(r=5, $fn=64), to avoid slowing the whole model.",
        ["$fn", "$fa", "$fs", "facets", "resolution", "special variables"],
    ),
    _syntax(
        "import needs convexity",
        'import("file.stl", convexity=10) needs an adequate convexity value or preview '
        "shows the mesh with holes through it. convexity affects preview only, not the F6 "
        "render. Paths are relative to the including file.",
        ["import", "stl", "convexity", "mesh", "preview"],
    ),
    _syntax(
        "text needs fonts",
        "text(t, size, font, halign, valign, spacing) produces 2D geometry and needs a font "
        "installed on the machine doing the render. A missing font falls back silently and "
        "changes the size. Extrude the result to get a solid.",
        ["text", "font", "label", "engrave", "2d"],
    ),
    _syntax(
        "2D and 3D do not mix",
        "You cannot union or difference a 2D shape with a 3D solid. Extrude the 2D shape "
        "first with linear_extrude or rotate_extrude, or flatten the 3D one with projection.",
        ["2d", "3d", "mix", "extrude", "error"],
    ),
]

_CHEATSHEET_NOTES = [
    "cheatsheet() returns these condensed into a single short block of text.",
    "The most common OpenSCAD mistake by far is expecting a variable to change inside a loop. "
    "It cannot. Build a list instead.",
]

# --------------------------------------------------------------------------
# 9. dfm
# --------------------------------------------------------------------------


def _dfm(
    name: str,
    rule: str,
    keywords: list[str],
    confidence: str,
    source: str,
    **fields: Any,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "keywords": keywords,
        "confidence": confidence,
        "note": rule,
        "source": source,
    }
    entry.update(fields)
    return entry


_DFM: list[dict[str, Any]] = [
    _dfm(
        "overhang 45 degree rule",
        "Surfaces sloping more than about 45 degrees from vertical need support. At 45 "
        "degrees each layer is still half-supported by the one below. Design chamfers instead "
        "of overhangs wherever you can, and reorient the part before you reach for supports. "
        "Some printers manage 55-60 degrees with good cooling; 45 is the safe design target.",
        ["overhang", "45", "support", "slope", "angle"],
        "consensus",
        _SRC_FDM_PRACTICE,
        max_unsupported_overhang_deg=45.0,
        overhang_range_deg=[45.0, 60.0],
    ),
    _dfm(
        "bridging",
        "Flat unsupported spans between two anchored walls bridge reliably up to roughly "
        "20-30 mm with good part cooling, and often further. A bridge sags slightly, so the "
        "first layer over a nut trap or a horizontal hole is never dimensionally accurate. "
        "Keep bridges short and straight, and never bridge onto a curved surface.",
        ["bridge", "bridging", "span", "unsupported", "gap"],
        "consensus",
        _SRC_FDM_PRACTICE,
        typical_reliable_span_mm=25.0,
        span_range_mm=[20.0, 50.0],
    ),
    _dfm(
        "minimum wall thickness",
        "Design walls as a whole multiple of the extrusion width. With a 0.4 mm nozzle two "
        "perimeters give 0.8-0.9 mm, which is the practical minimum for a non-structural "
        "wall; use three or four perimeters, 1.2-1.6 mm, for anything that carries load. A "
        "wall that is not a multiple of the extrusion width gets filled with a thin gap-fill "
        "bead that is weak and ugly.",
        ["wall", "thickness", "perimeter", "minimum", "nozzle", "extrusion width"],
        "consensus",
        _SRC_FDM_PRACTICE,
        min_wall_mm=0.8,
        recommended_wall_mm=1.2,
        wall_range_mm=[0.8, 1.6],
        nozzle_diameter_mm=0.4,
    ),
    _dfm(
        "minimum feature size",
        "Nothing narrower than one extrusion width, 0.4 mm, will print at all. Embossed or "
        "engraved detail wants at least 0.8 mm of width and 0.4 mm of depth or height to be "
        "legible. Pins below about 2 mm diameter are fragile enough to snap on removal from "
        "the bed.",
        ["feature", "minimum", "detail", "emboss", "engrave", "pin", "text"],
        "consensus",
        _SRC_FDM_PRACTICE,
        min_feature_mm=0.4,
        min_legible_detail_mm=0.8,
    ),
    _dfm(
        "hole compensation",
        "Printed holes come out undersized: the extruder follows a polygonal path inside the "
        "true circle and the plastic pulls inward as it cools. Vertical holes lose roughly "
        "0.1-0.2 mm on the diameter and horizontal holes 0.2-0.4 mm. Oversize the model or "
        "drill afterwards. The classic fix is nophead's polyhole: model the hole as a polygon "
        "with few enough sides that the printer's own path circumscribes the intended circle.",
        ["hole", "compensation", "undersize", "polyhole", "shrink", "diameter"],
        "consensus",
        _SRC_POLYHOLES,
        vertical_hole_undersize_mm=0.15,
        horizontal_hole_undersize_mm=0.30,
        horizontal_hole_undersize_range_mm=[0.2, 0.4],
    ),
    _dfm(
        "chamfer rather than fillet on bottom edges",
        "A fillet on a bottom edge is a shallow overhang right where elephant foot is already "
        "distorting the part, and it prints badly. A 45-degree chamfer of 0.5-1 mm is "
        "self-supporting, hides elephant foot, and makes the part easier to insert into a "
        "mating pocket. Save fillets for vertical edges and for the top, where they cost "
        "nothing.",
        ["chamfer", "fillet", "bottom", "edge", "elephant foot", "round"],
        "consensus",
        _SRC_FDM_PRACTICE,
        recommended_bottom_chamfer_mm=0.5,
        bottom_chamfer_range_mm=[0.4, 1.0],
    ),
    _dfm(
        "orientation for strength",
        "A printed part is markedly weaker across layers than along them; layer adhesion is "
        "typically only about half the in-plane strength. Orient the part so that tensile and "
        "bending loads run within the layer plane, and so that no thin section has to carry a "
        "load by peeling layers apart. This single decision matters more than infill "
        "percentage or wall count.",
        ["orientation", "strength", "layer adhesion", "anisotropy", "z strength"],
        "consensus",
        _SRC_FDM_PRACTICE,
        approx_z_strength_fraction=0.5,
        z_strength_fraction_range=[0.3, 0.8],
    ),
    _dfm(
        "warping on large flat first layers",
        "A large flat footprint concentrates shrinkage stress at the corners and lifts them, "
        "in ABS and ASA especially. Break up big flat bottoms, round or chamfer the corners "
        "rather than leaving them square, add sacrificial anchors if you must, and prefer PLA "
        "or PETG for large flat parts. Ribs on the underside relieve stress better than a "
        "solid slab.",
        ["warp", "warping", "first layer", "flat", "corner", "lift", "abs"],
        "consensus",
        _SRC_FDM_PRACTICE,
        warp_risk_footprint_mm=100.0,
    ),
    _dfm(
        "tolerance stack",
        "Tolerances add. Three stacked printed parts at +/-0.15 mm each can be nearly half a "
        "millimetre out overall. Do not chain dimensions: reference every feature to one "
        "datum, and where a stack is unavoidable put the adjustment in a single slot or "
        "oversized hole rather than spreading it.",
        ["tolerance", "stack", "stackup", "accumulate", "datum"],
        "consensus",
        _SRC_FDM_PRACTICE,
        per_part_tolerance_mm=0.15,
        per_part_tolerance_range_mm=[0.1, 0.2],
    ),
    _dfm(
        "print-in-place clearances",
        "A joint printed already assembled needs a gap the slicer will not bridge across. "
        "0.3-0.5 mm works on most machines; below 0.3 mm the parts fuse and below 0.2 mm they "
        "certainly will. Orient the gap horizontally where you can, since a vertical gap is "
        "reproduced more faithfully than a horizontal one that has to bridge. Print a test "
        "coupon of the joint on its own first, because a fused print-in-place assembly "
        "cannot be rescued afterwards.",
        ["print in place", "in place", "hinge", "clearance", "fuse", "gap"],
        "calibrate",
        _SRC_FDM_PRACTICE,
        recommended_gap_mm=0.4,
        gap_range_mm=[0.3, 0.5],
    ),
]

_DFM_NOTES = [
    "All of these assume a 0.4 mm nozzle and 0.2 mm layers on a consumer FDM machine. Scale "
    "the wall and feature minimums with the nozzle if that is not what you are printing on.",
    "When two rules conflict, orientation wins. Reorienting a part fixes overhangs, layer "
    "strength, hole roundness and warping at once.",
]

# --------------------------------------------------------------------------
# 10. materials
# --------------------------------------------------------------------------


def _material(
    name: str,
    density: float,
    density_range: list[float],
    printing_note: str,
    source: str,
    confidence: str = "standard",
) -> dict[str, Any]:
    return {
        "name": name,
        "keywords": ["material", "filament", "density", "plastic"],
        "density_g_cm3": density,
        "density_range_g_cm3": density_range,
        "confidence": confidence,
        "note": printing_note,
        "source": source,
    }


_MATERIALS: list[dict[str, Any]] = [
    _material(
        "PLA",
        1.24,
        [1.24, 1.26],
        "Easiest to print, stiff and dimensionally accurate, but brittle and creeps under "
        "sustained load. Softens around 60 C, so never use it in a car or near a heated bed. "
        "The default choice for fit and form prototypes.",
        _SRC_SIMPLIFY3D,
    ),
    _material(
        "PETG",
        1.27,
        [1.23, 1.27],
        "Tougher than PLA and much less brittle, good outdoors, holds heat-set inserts well. "
        "Stringy, and it bonds to smooth build plates hard enough to pull off chunks. Good "
        "default for functional parts and for anything that must flex a little.",
        _SRC_PRUSA_PETG,
    ),
    _material(
        "ABS",
        1.04,
        [1.02, 1.06],
        "Tough and heat-resistant to roughly 95 C, and it can be smoothed with acetone. Warps "
        "badly without an enclosure and emits styrene, so it needs ventilation. Choose it for "
        "heat resistance, not for accuracy.",
        _SRC_SIMPLIFY3D,
    ),
    _material(
        "ASA",
        1.07,
        [1.05, 1.09],
        "ABS with genuine UV stability, which makes it the right choice for outdoor parts. "
        "Same enclosure and ventilation requirements as ABS, slightly better warping "
        "behaviour.",
        _SRC_XOMETRY_FILAMENT,
    ),
    _material(
        "TPU",
        1.21,
        [1.19, 1.24],
        "Flexible; shore hardness, usually 85A to 98A, matters more than any other property. "
        "Needs slow printing and a direct-drive extruder. Design clearances differently: TPU "
        "parts deform to fit, so a press fit that would be too tight in PLA can be right.",
        _SRC_SIMPLIFY3D,
    ),
    _material(
        "Nylon (PA)",
        1.10,
        [1.01, 1.14],
        "Tough, abrasion-resistant and self-lubricating, the best choice for living hinges, "
        "gears and wear surfaces. Absorbs moisture from the air greedily, so it must be dried "
        "before printing and the part will grow slightly in service. PA12 is around 1.01-1.02 "
        "and general PA6 blends 1.06-1.14.",
        _SRC_SIMPLIFY3D,
        confidence="consensus",
    ),
    _material(
        "PC (polycarbonate)",
        1.20,
        [1.18, 1.20],
        "The strongest common filament and heat-resistant past 110 C, but it needs a very hot "
        "nozzle and an enclosure, and it warps. Also hygroscopic. Use it when nothing else is "
        "stiff or hot enough.",
        _SRC_XOMETRY_FILAMENT,
    ),
    _material(
        "Photopolymer resin (SLA/DLP)",
        1.08,
        [1.06, 1.12],
        "Liquid density is about 1.06-1.12 for standard grades and cured parts land around "
        "1.11-1.21; filled grades such as rigid or ceramic resins go far higher. Resin gives "
        "much finer detail and near-isotropic strength, but standard grades are brittle and "
        "degrade in sunlight. Clearances behave differently from FDM: parts shrink on cure, "
        "so calibrate fits separately.",
        _SRC_FORMLABS_DENSITY,
    ),
]

_MATERIALS_NOTES = [
    "Densities are for the bulk polymer. A printed part is lighter, because infill and the "
    "gaps between beads mean it is not solid. Multiply by the effective solid fraction if you "
    "are estimating printed mass.",
    "Vendors vary, and filled or coloured grades vary more. Use the datasheet for the "
    "specific spool when the number matters.",
    "Density is the wrong property for most design decisions. Choose on stiffness, toughness, "
    "heat resistance and UV stability; use density only to estimate mass and filament cost.",
]

# --------------------------------------------------------------------------
# Topic registry
# --------------------------------------------------------------------------

_TOPIC_DATA: dict[str, dict[str, Any]] = {
    "fits": {
        "summary": (
            "Clearances for FDM-printed mating parts, per side and diametral: press, snap, "
            "close running, free running, sliding and screw clearance. Includes hole "
            "compensation, elephant foot and the BOSL2 $slop variable."
        ),
        "entries": _FITS,
        "notes": _FITS_NOTES,
    },
    "fasteners": {
        "summary": (
            "ISO metric screws M2 to M8: coarse thread pitch, ISO 273 clearance holes, tap "
            "drill, ISO 4762 socket head cap screw head size, ISO 4032 hex nut size and "
            "counterbore diameter."
        ),
        "entries": _FASTENERS,
        "notes": _FASTENERS_NOTES,
    },
    "inserts": {
        "summary": (
            "Heat-set brass threaded inserts M2 to M5: boss hole diameter and depth, insert "
            "size, and minimum wall thickness around the boss."
        ),
        "entries": _INSERTS,
        "notes": _INSERTS_NOTES,
    },
    "bearings": {
        "summary": (
            "Common skateboard and RC ball bearing sizes with bore, outer diameter and width, "
            "plus press-fit pocket advice."
        ),
        "entries": _BEARINGS,
        "notes": _BEARINGS_NOTES,
    },
    "magnets": {
        "summary": (
            "Common neodymium disc magnet sizes with pocket diameter and depth guidance for "
            "glued and press fits."
        ),
        "entries": _MAGNETS,
        "notes": _MAGNETS_NOTES,
    },
    "joints": {
        "summary": (
            "Parametric joining features with typical parameters, when to use each, and the "
            "BOSL2 modules that implement them: dovetail, snap-fit, press-fit pin, tongue and "
            "groove, living hinge, mortise and tenon, screw boss, captive nut trap."
        ),
        "entries": _JOINTS,
        "notes": _JOINTS_NOTES,
    },
    "conventions": {
        "summary": (
            "Modelling conventions for this server: units, coordinate system, part origin, "
            "module structure, epsilon overlap, named dimensions, explicit clearance, facet "
            "resolution, library preference and measuring rather than eyeballing."
        ),
        "entries": _CONVENTIONS,
        "notes": _CONVENTIONS_NOTES,
    },
    "cheatsheet": {
        "summary": (
            "OpenSCAD language behaviour that is commonly got wrong: compile-time variables, "
            "for and difference semantics, center, extrusions, offset, projection, modifier "
            "characters, special variables and the 2D/3D divide."
        ),
        "entries": _CHEATSHEET,
        "notes": _CHEATSHEET_NOTES,
    },
    "dfm": {
        "summary": (
            "Design-for-manufacturing rules for FDM: overhangs, bridging, wall and feature "
            "minimums, hole compensation, chamfers, orientation for strength, warping, "
            "tolerance stack and print-in-place clearances."
        ),
        "entries": _DFM,
        "notes": _DFM_NOTES,
    },
    "materials": {
        "summary": (
            "Densities and one-line printing notes for PLA, PETG, ABS, ASA, TPU, nylon, "
            "polycarbonate and photopolymer resin."
        ),
        "entries": _MATERIALS,
        "notes": _MATERIALS_NOTES,
    },
}

# --------------------------------------------------------------------------
# Long-form text
# --------------------------------------------------------------------------

_CONVENTIONS_BRIEF = """\
Modelling conventions (OpenSCAD, this server):
- Units are millimetres. Z is up, right-handed, XY is the build plate.
- Put the part origin at a meaningful datum, normally bottom-centre (centred in X and Y, \
base at Z=0), and state the datum in a comment.
- One module per physical part, with a `part` parameter or separate named modules, so any \
one part or the whole assembly can be rendered.
- Overlap coplanar boundaries in union/difference by an epsilon of 0.01 mm; extend cutting \
solids past both faces. Never inset: exact coincidence gives non-manifold geometry.
- Declare every key dimension as a commented top-level variable. No magic numbers in \
geometry.
- Make clearance an explicit named variable, e.g. `clearance = 0.15; // per side`. If BOSL2 \
is available, set `$slop` once instead and let its modules apply it (BOSL2 applies $slop per \
side; its default is 0).
- $fn moderately: 24 while iterating, 64+ for final renders and export.
- Prefer `include <BOSL2/std.scad>` when BOSL2 is installed.
- echo() the assumptions and derived sizes; assert() the constraints that must hold.
- Check results with the measurement tool, not by judging the rendered picture.
"""

_CHEATSHEET_TEXT = """\
OpenSCAD reminders (things that are commonly got wrong):
- Variables are compile-time. The LAST assignment in a scope wins for the whole scope. You
  cannot accumulate in a for loop; use a list comprehension, recursion, or let().
- for() instantiates children per value and UNIONS them. Use intersection_for() to intersect.
- difference() keeps child 1 and subtracts all later children. Order matters.
- center: cube/square/cylinder default center=false (positive octant / on Z=0); sphere is
  always centred. center=true centres on the origin.
- Transforms (translate/rotate/mirror/scale) apply innermost-first: translate() rotate()
  cube() rotates, then translates. rotate() is in degrees, applied X then Y then Z.
  mirror(v) reflects across the plane through the origin normal to v; it does not translate.
- hull() = convex hull, the cheap rounded-slot trick. minkowski() rounds but is very slow.
- linear_extrude(height, center, convexity, twist, slices, scale) and
  rotate_extrude(angle, convexity); the rotate_extrude child must lie in X>=0.
- offset(r=) rounds corners, offset(delta=) keeps them sharp, offset(delta=, chamfer=true)
  chamfers. 2D only.
- projection() flattens the silhouette; projection(cut=true) sections at Z=0.
- Modifiers: % background (excluded), # highlight (kept), ! render only this subtree,
  * disable. Remove % and # before export.
- assert(cond, "msg") enforces design constraints; echo() reports computed values.
- let(a=1) binds for one child. List comprehensions: [for (i=[0:5]) i*2], with if/let.
  each flattens a nested list.
- is_undef(x) tests an unset variable; also is_num, is_string, is_list, is_bool.
- $fn overrides $fa (min angle, default 12) and $fs (min size, default 2). Prefer setting it
  per object: cylinder(r=5, $fn=64).
- import("f.stl", convexity=10): without adequate convexity, preview shows holes.
- text() needs a font installed; a missing font falls back silently and changes size.
- 2D and 3D cannot be mixed in a boolean. Extrude first, or use projection().
"""

# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def list_topics() -> list[dict[str, str]]:
    """Return every available topic with a one-line summary.

    Returns:
        A list of ``{"topic": str, "summary": str}`` dicts, in ``TOPICS`` order.
    """
    return [{"topic": name, "summary": _TOPIC_DATA[name]["summary"]} for name in TOPICS]


def _entry_matches(entry: dict[str, Any], needle: str) -> bool:
    """Return True if ``needle`` occurs in the entry's name, keys or keywords."""
    if needle in entry["name"].lower():
        return True
    if any(needle in key.lower() for key in entry):
        return True
    return any(needle in str(word).lower() for word in entry.get("keywords", ()))


def _compact(entry: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``entry`` without the verbose fields."""
    return {key: value for key, value in entry.items() if key not in _VERBOSE_FIELDS}


def lookup(topic: str, query: str | None = None, detailed: bool = False) -> dict[str, Any]:
    """Look up reference entries for a topic, optionally filtered by a query.

    Args:
        topic: One of ``TOPICS``. Case-insensitive.
        query: Optional case-insensitive substring matched against each entry's
            name, field names and keywords. ``"M3"`` finds the M3 fastener row,
            ``"608"`` the 608 bearing, ``"press"`` the press fit.
        detailed: When True, entries keep their ``note`` and ``keywords``
            fields. When False the entries are compact, but every entry still
            carries its ``confidence`` and ``source``.

    Returns:
        ``{"topic", "query", "entries", "notes", "sources"}``. The result is
        JSON-serialisable. ``entries`` is empty if the query matched nothing;
        ``notes`` and ``sources`` are still returned so the caller has context.

    Raises:
        ValueError: If ``topic`` is not one of ``TOPICS``.
    """
    key = topic.strip().lower() if isinstance(topic, str) else topic
    if key not in _TOPIC_DATA:
        raise ValueError(f"Unknown topic {topic!r}. Available topics: {', '.join(TOPICS)}")

    data = _TOPIC_DATA[key]
    entries: list[dict[str, Any]] = data["entries"]

    if query is not None and str(query).strip():
        needle = str(query).strip().lower()
        entries = [entry for entry in entries if _entry_matches(entry, needle)]

    result_entries = [copy.deepcopy(entry) if detailed else _compact(entry) for entry in entries]

    sources: list[str] = []
    for entry in entries:
        for source in str(entry["source"]).split(" | "):
            if source not in sources:
                sources.append(source)

    return {
        "topic": key,
        "query": query,
        "entries": result_entries,
        "notes": list(data["notes"]),
        "sources": sources,
    }


def conventions_brief() -> str:
    """Return the assembly and coordinate conventions as short plain text.

    Intended for the server's ``instructions=`` string. Kept under 1400
    characters, roughly 350 tokens.
    """
    return _CONVENTIONS_BRIEF


def cheatsheet() -> str:
    """Return OpenSCAD syntax reminders as plain text, under 2500 characters."""
    return _CHEATSHEET_TEXT
