"""Camera math and image annotation for OpenSCAD PNG renders.

This module turns OpenSCAD's ``--camera=eye,center`` parameters into a small
projection model, so that a caller can answer questions like "where does this
model corner land in the image?" and "how many millimetres is one pixel?".

Everything here is keyed to the behaviour of OpenSCAD 2021.01 PNG export with
``--projection=o`` (orthographic):

* the camera is ``--camera=eye_x,eye_y,eye_z,center_x,center_y,center_z``,
* the visible height of the image in model millimetres is
  ``VIEW_HEIGHT_FACTOR * distance`` where ``distance = |eye - center|``,
* that height is keyed to the image *height* only; the horizontal extent is
  ``mm_per_px * image_width``,
* the centre pixel of the image shows ``center``.

The module also provides annotation helpers (scale bar, axis triad, dimension
text, projected bounding box) and :func:`spatial_digest`, a compact text block
meant to be placed *before* an image in a tool response so that a reader knows
the scale and orientation of what they are about to look at.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

__all__ = [
    "VIEW_HEIGHT_FACTOR",
    "OrthoCamera",
    "fit_camera",
    "PALETTE",
    "part_color",
    "choose_scale_bar",
    "bbox_corners",
    "annotate",
    "spatial_digest",
]

Vec3 = tuple[float, float, float]

#: Visible model height per unit of camera distance under ``--projection=o``.
#: OpenSCAD uses a 22.5 degree vertical field of view, so the orthographic
#: frustum height is ``2 * tan(11.25 degrees) * distance``.
VIEW_HEIGHT_FACTOR = 2 * math.tan(math.radians(11.25))

#: Distances below this collapse the projection, so cameras are clamped to it.
MIN_DISTANCE = 1e-3

_EPS = 1e-12

# --------------------------------------------------------------------------
# vector helpers (plain tuples; no numpy dependency)
# --------------------------------------------------------------------------


def _vec(value) -> Vec3:
    """Coerce a 3-sequence into a tuple of three floats."""
    x, y, z = value
    return (float(x), float(y), float(z))


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _normalize(a: Vec3, fallback: Vec3 = (0.0, 0.0, 1.0)) -> Vec3:
    n = _length(a)
    if n < _EPS:
        return fallback
    return (a[0] / n, a[1] / n, a[2] / n)


def _fmt(value: float, places: int = 2) -> str:
    """Round to ``places`` decimals and strip trailing zeros ("5.00" -> "5")."""
    text = f"{round(float(value) + 0.0, places):.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _fmt_vec(v) -> str:
    return "(" + ", ".join(_fmt(c) for c in v) + ")"


def _axis_word(direction, relative_tol: float = 0.35) -> str:
    """Describe a direction as a compact axis word such as ``+X+Y+Z``.

    Components smaller than ``relative_tol`` of the largest component are
    dropped, so ``(0, -1, 0)`` becomes ``-Y`` and an isometric direction
    becomes ``+X+Y+Z``.
    """
    v = _vec(direction)
    biggest = max(abs(c) for c in v)
    if biggest < _EPS:
        return "(degenerate)"
    parts = []
    for name, component in zip("XYZ", v, strict=True):
        if abs(component) >= relative_tol * biggest:
            parts.append(("+" if component > 0 else "-") + name)
    return "".join(parts)


def _flip_axis_word(word: str) -> str:
    return word.replace("+", "\0").replace("-", "+").replace("\0", "-")


_AXIS_VIEW_NAMES = {
    "+X": "right view",
    "-X": "left view",
    "+Y": "back view",
    "-Y": "front view",
    "+Z": "top view",
    "-Z": "bottom view",
}


# --------------------------------------------------------------------------
# camera
# --------------------------------------------------------------------------


@dataclass
class OrthoCamera:
    """An OpenSCAD orthographic camera plus the image it renders into.

    ``eye``/``center`` are the two halves of ``--camera=...``; ``up`` is the
    world-space up vector OpenSCAD uses for the view (``+Z`` for the standard
    side views, ``+Y``/``-Y`` for top/bottom where ``+Z`` would be degenerate).
    """

    eye: Vec3
    center: Vec3 = (0.0, 0.0, 0.0)
    up: Vec3 = (0.0, 0.0, 1.0)
    image_size: tuple[int, int] = (800, 600)

    def __post_init__(self) -> None:
        self.eye = _vec(self.eye)
        self.center = _vec(self.center)
        self.up = _vec(self.up)
        width, height = self.image_size
        width, height = int(width), int(height)
        if width < 1 or height < 1:
            raise ValueError(f"image_size must be positive, got {self.image_size!r}")
        self.image_size = (width, height)

    # -- geometry ---------------------------------------------------------

    @property
    def distance(self) -> float:
        """Distance from eye to center, clamped away from zero."""
        return max(_length(_sub(self.center, self.eye)), MIN_DISTANCE)

    @property
    def view_height_mm(self) -> float:
        """Model millimetres spanned by the image height."""
        return VIEW_HEIGHT_FACTOR * self.distance

    @property
    def view_width_mm(self) -> float:
        """Model millimetres spanned by the image width."""
        return self.mm_per_px * self.image_size[0]

    @property
    def mm_per_px(self) -> float:
        return self.view_height_mm / self.image_size[1]

    @property
    def forward(self) -> Vec3:
        """Unit vector from the eye toward the center (the gaze direction)."""
        return _normalize(_sub(self.center, self.eye), (0.0, 1.0, 0.0))

    @property
    def basis(self) -> tuple[Vec3, Vec3, Vec3]:
        """``(right, screen_up, forward)`` orthonormal screen basis.

        If ``up`` is parallel to the gaze direction the basis would be
        degenerate, so a fallback up vector is substituted rather than raising.
        """
        forward = self.forward
        up = self.up
        right_raw = _cross(forward, up)
        if _length(right_raw) < 1e-6:
            for candidate in ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)):
                right_raw = _cross(forward, candidate)
                if _length(right_raw) >= 1e-6:
                    break
        right = _normalize(right_raw, (1.0, 0.0, 0.0))
        screen_up = _normalize(_cross(right, forward), (0.0, 0.0, 1.0))
        return right, screen_up, forward

    def project(self, point) -> tuple[float, float]:
        """Project a world point to pixel coordinates (x right, y down).

        Orthographic, so depth does not affect the result. The image centre
        corresponds to ``self.center``.
        """
        right, screen_up, _ = self.basis
        offset = _sub(_vec(point), self.center)
        mm_per_px = self.mm_per_px
        width, height = self.image_size
        x = width / 2.0 + _dot(offset, right) / mm_per_px
        y = height / 2.0 - _dot(offset, screen_up) / mm_per_px
        return (x, y)

    def project_points(self, points) -> list[tuple[float, float]]:
        return [self.project(p) for p in points]

    def depth(self, point) -> float:
        """Signed distance along the gaze direction; larger means further away."""
        return _dot(_sub(_vec(point), self.center), self.forward)

    # -- description ------------------------------------------------------

    def view_direction_words(self) -> str:
        """A short human sentence describing where the camera is and looks.

        Examples::

            looking along +Y from -Y (front view), +Z up
            looking down along -Z from +Z (top view), +Y up
            looking from +X+Y+Z toward the center (isometric), +Z up
        """
        eye_dir = _normalize(_sub(self.eye, self.center), (0.0, -1.0, 0.0))
        eye_word = _axis_word(eye_dir)
        gaze_word = _flip_axis_word(eye_word)
        up_word = _axis_word(self.up)

        if eye_word in _AXIS_VIEW_NAMES:
            name = _AXIS_VIEW_NAMES[eye_word]
            if eye_word == "+Z":
                head = f"looking down along {gaze_word} from {eye_word}"
            elif eye_word == "-Z":
                head = f"looking up along {gaze_word} from {eye_word}"
            else:
                head = f"looking along {gaze_word} from {eye_word}"
            return f"{head} ({name}), {up_word} up"

        components = [abs(c) for c in eye_dir]
        biggest = max(components)
        is_isometric = all(abs(c - biggest) < 0.05 * biggest for c in components)
        suffix = " (isometric)" if is_isometric else ""
        return f"looking from {eye_word} toward the center{suffix}, {up_word} up"


def fit_camera(
    bbox_min,
    bbox_max,
    direction,
    up=(0.0, 0.0, 1.0),
    image_size: tuple[int, int] = (800, 600),
    margin: float = 1.15,
) -> OrthoCamera:
    """Build a camera that frames a bounding box.

    ``direction`` is the unit vector pointing from the model centre toward the
    eye, e.g. ``(0, -1, 0)`` for a front view or ``(1, 1, 1)`` for isometric.
    The camera distance is chosen so that the box's bounding *sphere*, inflated
    by ``margin``, fits both the image height and the image width. Framing the
    sphere rather than the box means the model stays inside the frame at any
    orientation.
    """
    lo = _vec(bbox_min)
    hi = _vec(bbox_max)
    center = _scale(_add(lo, hi), 0.5)
    diameter = _length(_sub(hi, lo))

    width, height = int(image_size[0]), int(image_size[1])
    if width < 1 or height < 1:
        raise ValueError(f"image_size must be positive, got {image_size!r}")

    needed_mm = max(diameter * margin, MIN_DISTANCE)
    distance = needed_mm / VIEW_HEIGHT_FACTOR
    if width < height:
        # The horizontal extent is the limiting dimension; widen the view.
        distance *= height / width
    distance = max(distance, MIN_DISTANCE)

    eye = _add(center, _scale(_normalize(direction, (0.0, -1.0, 0.0)), distance))
    return OrthoCamera(eye=eye, center=center, up=_vec(up), image_size=(width, height))


# --------------------------------------------------------------------------
# colours
# --------------------------------------------------------------------------

#: Ten stable, colourblind-friendly colours (Okabe-Ito plus two greys/wine).
#: OpenSCAD's ``color()`` accepts these hex strings directly.
PALETTE: list[str] = [
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#009E73",  # bluish green
    "#F0E442",  # yellow
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#CC79A7",  # reddish purple
    "#999999",  # grey
    "#882255",  # wine
    "#332288",  # indigo
]


def part_color(index: int) -> str:
    """Return a stable palette colour for a part index, cycling as needed."""
    return PALETTE[int(index) % len(PALETTE)]


# --------------------------------------------------------------------------
# annotation
# --------------------------------------------------------------------------

_PLATE_FILL = (248, 248, 248, 235)
_PLATE_BORDER = (24, 24, 24, 255)
_INK = (17, 17, 17, 255)
_AXIS_COLORS = {
    "X": (200, 30, 30, 255),
    "Y": (25, 140, 45, 255),
    "Z": (35, 85, 200, 255),
}
_BBOX_COLOR = (60, 60, 60, 200)


def _pillow_version() -> tuple[int, ...]:
    raw = getattr(Image, "__version__", None) or "0.0"
    parts: list[int] = []
    for chunk in raw.split(".")[:3]:
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _font_size_for(image_size: tuple[int, int]) -> int:
    """Body text size: 16 px for normal renders, smaller for tiny images.

    16 px is the legibility floor for a real render, but on a 100 px thumbnail
    16 px fits about six characters, so the text is shrunk (never below 10 px)
    rather than clipped.
    """
    smallest = min(image_size)
    if smallest >= 200:
        return 16
    return max(10, int(round(16 * smallest / 200.0)))


def _load_font(size: int = 16):
    """Load a font of roughly ``size`` pixels, degrading gracefully.

    ``ImageFont.load_default(size=...)`` only exists in Pillow >= 10.1; older
    versions get a TrueType font if one can be found, else the bitmap default.
    """
    if _pillow_version() >= (10, 1):
        try:
            return ImageFont.load_default(size=size)
        except (TypeError, OSError):
            pass
    for name in ("DejaVuSans.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_box(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int, int, int]:
    """Return ``(width, height, left_offset, top_offset)`` for ``text``."""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return (int(math.ceil(right - left)), int(math.ceil(bottom - top)), int(left), int(top))


def _truncate_to_width(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """Shorten ``text`` with an ellipsis until it fits ``max_width`` pixels."""
    if max_width <= 0:
        return ""
    if _text_box(draw, text, font)[0] <= max_width:
        return text
    for cut in range(len(text) - 1, 0, -1):
        candidate = text[:cut] + ".."
        if _text_box(draw, candidate, font)[0] <= max_width:
            return candidate
    return ""


def _draw_plate_text(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font,
    box_left: float,
    box_top: float,
    image_size: tuple[int, int],
    align: str = "left",
    pad: int = 4,
    line_gap: int = 2,
) -> tuple[int, int, int, int]:
    """Draw one or more text lines on an opaque plate, clamped to the image.

    Returns the plate rectangle actually used.
    """
    if not lines:
        return (0, 0, 0, 0)
    img_w, img_h = image_size
    lines = [_truncate_to_width(draw, line, font, img_w - 2 * pad - 2) for line in lines]
    lines = [line for line in lines if line]
    if not lines:
        return (0, 0, 0, 0)
    measured = [_text_box(draw, line, font) for line in lines]
    text_w = max(m[0] for m in measured)
    text_h = sum(m[1] for m in measured) + line_gap * (len(lines) - 1)

    plate_w = text_w + 2 * pad
    plate_h = text_h + 2 * pad
    left = int(max(0, min(box_left, img_w - plate_w)))
    top = int(max(0, min(box_top, img_h - plate_h)))
    right = min(img_w - 1, left + plate_w)
    bottom = min(img_h - 1, top + plate_h)

    draw.rectangle([left, top, right, bottom], fill=_PLATE_FILL, outline=_PLATE_BORDER, width=1)

    y = top + pad
    for line, (w, h, off_x, off_y) in zip(lines, measured, strict=True):
        if align == "right":
            x = left + pad + (text_w - w)
        elif align == "center":
            x = left + pad + (text_w - w) // 2
        else:
            x = left + pad
        draw.text((x - off_x, y - off_y), line, font=font, fill=_INK)
        y += h + line_gap
    return (left, top, right, bottom)


def choose_scale_bar(
    mm_per_px: float,
    image_width: int,
    min_frac: float = 0.15,
    max_frac: float = 0.30,
) -> tuple[float, float]:
    """Pick a "nice" scale bar length (1, 2 or 5 x 10^n mm).

    Returns ``(length_mm, length_px)``. The bar is 15-30% of the image width
    when a nice number lands in that window, otherwise the closest nice number
    to 22% of the width.
    """
    if mm_per_px <= 0 or image_width <= 0:
        return (0.0, 0.0)
    span_mm = mm_per_px * image_width
    target = 0.22 * span_mm
    candidates = [m * (10.0**e) for e in range(-9, 10) for m in (1, 2, 5)]
    in_window = [c for c in candidates if min_frac * span_mm <= c <= max_frac * span_mm]
    pool = in_window or candidates
    best = min(pool, key=lambda c: abs(math.log(c / target)))
    return (best, best / mm_per_px)


def _draw_scale_bar(
    draw: ImageDraw.ImageDraw,
    font,
    mm_per_px: float,
    image_size: tuple[int, int],
    margin: int,
) -> tuple[int, int, int, int] | None:
    img_w, img_h = image_size
    length_mm, length_px = choose_scale_bar(mm_per_px, img_w)
    if length_px <= 0:
        return None
    max_bar = max(8, img_w - 2 * margin - 12)
    bar_px = int(round(min(length_px, max_bar)))

    label = f"{_fmt(length_mm, 3)} mm"
    text_w, text_h, off_x, off_y = _text_box(draw, label, font)

    pad = 5
    tick = 5
    content_w = max(bar_px, text_w)
    content_h = text_h + 6 + tick + 4
    plate_w = content_w + 2 * pad
    plate_h = content_h + 2 * pad
    left = int(max(0, min(margin, img_w - plate_w)))
    top = int(max(0, img_h - margin - plate_h))
    right = min(img_w - 1, left + plate_w)
    bottom = min(img_h - 1, top + plate_h)
    draw.rectangle([left, top, right, bottom], fill=_PLATE_FILL, outline=_PLATE_BORDER, width=1)

    draw.text((left + pad - off_x, top + pad - off_y), label, font=font, fill=_INK)

    bar_y = bottom - pad - 4
    bar_x0 = left + pad
    bar_x1 = min(right - pad, bar_x0 + bar_px)
    draw.line([(bar_x0, bar_y), (bar_x1, bar_y)], fill=_INK, width=2)
    for x in (bar_x0, bar_x1):
        draw.line([(x, bar_y - tick), (x, bar_y + 1)], fill=_INK, width=2)
    return (left, top, right, bottom)


def _draw_axis_triad(
    draw: ImageDraw.ImageDraw,
    camera: OrthoCamera,
    font,
    image_size: tuple[int, int],
    margin: int,
    avoid: tuple[int, int, int, int] | None = None,
) -> None:
    img_w, img_h = image_size
    right, screen_up, forward = camera.basis
    arm = min(40, max(10, (min(img_w, img_h) - 2 * margin) // 3))
    origin_x = margin + arm + 2
    origin_y = margin + arm + 2

    notes: list[str] = []
    axes = {
        "X": (1.0, 0.0, 0.0),
        "Y": (0.0, 1.0, 0.0),
        "Z": (0.0, 0.0, 1.0),
    }
    for name, axis in axes.items():
        dx = _dot(axis, right) * arm
        dy = -_dot(axis, screen_up) * arm
        if math.hypot(dx, dy) < 3.0:
            # Pointing at or away from the viewer: no useful screen direction.
            toward = "toward viewer" if _dot(axis, forward) < 0 else "away"
            notes.append(f"{name} ({toward})")
            continue
        tip_x = origin_x + dx
        tip_y = origin_y + dy
        draw.line([(origin_x, origin_y), (tip_x, tip_y)], fill=_AXIS_COLORS[name], width=2)
        label_w, label_h, off_x, off_y = _text_box(draw, name, font)
        norm = math.hypot(dx, dy) or 1.0
        lx = tip_x + (dx / norm) * 7 - label_w / 2
        ly = tip_y + (dy / norm) * 7 - label_h / 2
        lx = max(0, min(lx, img_w - label_w - 1))
        ly = max(0, min(ly, img_h - label_h - 1))
        draw.rectangle(
            [lx - 2, ly - 2, lx + label_w + 2, ly + label_h + 2],
            fill=_PLATE_FILL,
            outline=None,
        )
        draw.text((lx - off_x, ly - off_y), name, font=font, fill=_AXIS_COLORS[name])

    draw.ellipse([origin_x - 2, origin_y - 2, origin_x + 2, origin_y + 2], fill=_PLATE_BORDER)
    if notes:
        note_top = origin_y + arm + 6
        line_h = _text_box(draw, notes[0], font)[1]
        note_h = len(notes) * (line_h + 2) + 8
        if avoid is not None and note_top + note_h > avoid[1]:
            # The scale bar owns the bottom-left corner; sit above it if there
            # is room, otherwise drop the note rather than overlap the bar.
            note_top = avoid[1] - note_h - 2
            if note_top < origin_y + 4:
                return
        _draw_plate_text(draw, notes, font, margin, note_top, image_size, align="left")


def _dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    color,
    dash: int = 6,
    gap: int = 4,
) -> None:
    x0, y0 = start
    x1, y1 = end
    total = math.hypot(x1 - x0, y1 - y0)
    if total < 1:
        return
    step = dash + gap
    pos = 0.0
    while pos < total:
        seg = min(dash, total - pos)
        t0 = pos / total
        t1 = (pos + seg) / total
        draw.line(
            [
                (x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0),
                (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1),
            ],
            fill=color,
            width=1,
        )
        pos += step


def bbox_corners(bbox_min, bbox_max) -> list[Vec3]:
    """The eight corners of an axis-aligned bounding box."""
    lo = _vec(bbox_min)
    hi = _vec(bbox_max)
    return [
        (lo[0] if i & 1 else hi[0], lo[1] if i & 2 else hi[1], lo[2] if i & 4 else hi[2])
        for i in range(8)
    ]


def _draw_projected_bbox(
    draw: ImageDraw.ImageDraw,
    project,
    bbox_min,
    bbox_max,
    image_size: tuple[int, int],
) -> None:
    img_w, img_h = image_size
    pts = [project(c) for c in bbox_corners(bbox_min, bbox_max)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    left = max(0.0, min(xs))
    right = min(img_w - 1.0, max(xs))
    top = max(0.0, min(ys))
    bottom = min(img_h - 1.0, max(ys))
    if right <= left or bottom <= top:
        return
    _dashed_line(draw, (left, top), (right, top), _BBOX_COLOR)
    _dashed_line(draw, (right, top), (right, bottom), _BBOX_COLOR)
    _dashed_line(draw, (right, bottom), (left, bottom), _BBOX_COLOR)
    _dashed_line(draw, (left, bottom), (left, top), _BBOX_COLOR)


def annotate(
    png_bytes: bytes,
    camera: OrthoCamera,
    *,
    bbox_min=None,
    bbox_max=None,
    label: str | None = None,
    scale_bar: bool = True,
    axes: bool = True,
    dimensions: bool = True,
) -> bytes:
    """Draw scale, orientation and size cues onto a rendered PNG.

    The returned PNG has the same pixel dimensions as the input. Overlays are:
    a scale bar bottom-left, an axis triad top-left, dimension/label text
    top-right, and a dashed rectangle around the projected bounding box when
    one is supplied. If the image size differs from ``camera.image_size``,
    projected coordinates are scaled to fit the actual image.
    """
    source = Image.open(io.BytesIO(png_bytes))
    image = source.convert("RGBA")
    img_w, img_h = image.size
    draw = ImageDraw.Draw(image)
    font = _load_font(_font_size_for((img_w, img_h)))

    scale_x = img_w / camera.image_size[0]
    scale_y = img_h / camera.image_size[1]
    mm_per_px = camera.view_height_mm / img_h
    margin = 6 if min(img_w, img_h) >= 200 else 4

    def project(point):
        x, y = camera.project(point)
        return (x * scale_x, y * scale_y)

    has_bbox = bbox_min is not None and bbox_max is not None
    if has_bbox:
        _draw_projected_bbox(draw, project, bbox_min, bbox_max, (img_w, img_h))

    scale_rect = None
    if scale_bar:
        scale_rect = _draw_scale_bar(draw, font, mm_per_px, (img_w, img_h), margin)

    if axes:
        _draw_axis_triad(draw, camera, font, (img_w, img_h), margin, avoid=scale_rect)

    lines: list[str] = []
    if label:
        lines.append(str(label))
    if dimensions and has_bbox:
        lo = _vec(bbox_min)
        hi = _vec(bbox_max)
        size = _sub(hi, lo)
        lines.append(f"{_fmt(size[0])} x {_fmt(size[1])} x {_fmt(size[2])} mm")
    if lines:
        measured = [_text_box(draw, line, font) for line in lines]
        plate_w = max(m[0] for m in measured) + 8
        _draw_plate_text(
            draw,
            lines,
            font,
            img_w - margin - plate_w,
            margin,
            (img_w, img_h),
            align="right",
        )

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


# --------------------------------------------------------------------------
# text digest
# --------------------------------------------------------------------------


def spatial_digest(
    camera: OrthoCamera,
    *,
    bbox_min=None,
    bbox_max=None,
    projection: str = "orthographic",
    view_name: str | None = None,
    grounded: bool = True,
) -> str:
    """A compact text block describing scale and orientation of a render.

    Intended to be emitted immediately *before* the image in a tool response.
    Set ``grounded=False`` when the render used ``--viewall``/``--autocenter``,
    since the camera distance no longer implies a known scale.
    """
    width, height = camera.image_size
    lines = [
        f"view: {view_name or 'custom'} | projection: {projection} | units: mm | "
        "Z up, right-handed",
        f"camera eye={_fmt_vec(camera.eye)} center={_fmt_vec(camera.center)} "
        f"up={_fmt_vec(camera.up)} distance={_fmt(camera.distance)}",
        camera.view_direction_words(),
    ]

    if grounded:
        lines.append(
            f"scale: {camera.mm_per_px:.4g} mm/px "
            f"(image {width}x{height} => {_fmt(camera.view_width_mm, 1)} x "
            f"{_fmt(camera.view_height_mm, 1)} mm visible)"
        )
    else:
        lines.append("scale: unknown (auto-fit); use grounded=true or measure for absolute size")

    if bbox_min is not None and bbox_max is not None:
        lo = _vec(bbox_min)
        hi = _vec(bbox_max)
        size = _sub(hi, lo)
        center = _scale(_add(lo, hi), 0.5)
        lines.append(
            f"bbox: [{_fmt(lo[0])},{_fmt(lo[1])},{_fmt(lo[2])}].."
            f"[{_fmt(hi[0])},{_fmt(hi[1])},{_fmt(hi[2])}]  "
            f"size {_fmt(size[0])} x {_fmt(size[1])} x {_fmt(size[2])} mm  "
            f"center {_fmt_vec(center)}"
        )
        if grounded:
            longest_px = max(size) / camera.mm_per_px if camera.mm_per_px > 0 else 0.0
            lines.append(f"longest bbox edge is about {int(round(longest_px))} px in this image")

    return "\n".join(lines)
