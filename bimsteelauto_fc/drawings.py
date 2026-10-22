"""
bimsteelauto_fc.drawings
========================

2D output: general arrangement sheets and single-part drawings, as SVG and
DXF.

Why the SVG is written here rather than by TechDraw's page machinery
--------------------------------------------------------------------
``TechDraw`` does the hard part - hidden-line removal on a real solid - and
this module does the easy part around it: a sheet, a border, a title block,
a scale, and a NOT FOR ISSUE stamp that is driven by the design rather than
typed on.  Going through ``TechDraw``'s pages and templates would drag in
template files and a GUI-shaped workflow for no gain, and would make the
sheet harder to control than it is worth.

What is drawn
-------------
* **General arrangement** - frame elevation, side elevation and roof plan,
  each on its own sheet, at a stated scale.
* **Single-part drawings** - one per distinct member mark, which is what a
  fabricator actually cuts from.

Every sheet carries the job number, the view, the scale and - the point of
the whole exercise - whether the design behind it is issuable.  A drawing
that does not say it came from a failing design is the most dangerous
document this package can produce, so the stamp is not optional and is not
a caller's choice.

Units are mm throughout; the sheet is A3 landscape.
"""

from __future__ import annotations

import datetime
import math
import os
from dataclasses import dataclass, field

import FreeCAD as App
import Part
import TechDraw

__all__ = [
    "COVERAGE_GAPS",
    "DrawingError",
    "Sheet",
    "DrawingSet",
    "Dimension",
    "VIEW_TO_SHEET",
    "projection_map",
    "general_arrangement",
    "single_part_drawings",
    "export_dxf",
]

COVERAGE_GAPS: tuple[str, ...] = (
    "Sheets carry SET-OUT dimensions only: the grid, the levels, the "
    "overall size of each part and the bolt set-out on a connection plate. "
    "Section depths along a tapered member, hole diameters, weld symbols, "
    "section marks, revision clouds and material or finish notes are not "
    "drawn, so a sheet still needs a detailer's annotation before issue.",
    "Hidden lines are computed by TechDraw but are not drawn - only the "
    "visible outline is. On a part sheet that hides the far flange and any "
    "hole seen through the web.",
    "A dimension is placed against the model, not against a drawing "
    "standard: there is no dimension-style table, no tolerance, and no "
    "rule about which dimensions a given view must carry.",
)

#: A3 landscape, mm.
SHEET_W, SHEET_H = 420.0, 297.0
MARGIN = 12.0
TITLE_H = 34.0

#: Scales a drawing office would actually use, largest first.
_SCALES = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000)


class DrawingError(RuntimeError):
    """A drawing could not be produced."""


@dataclass
class Sheet:
    name: str
    path: str
    scale: str
    view: str


@dataclass
class DrawingSet:
    sheets: list[Sheet] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{len(self.sheets)} sheets"]
        for s in self.sheets:
            lines.append(f"  {s.view:22s} 1:{s.scale:<6s} {s.path}")
        lines += [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

#: ``(label, view direction)``.  The direction is the way the VIEWER looks.
VIEWS: dict[str, App.Vector] = {
    "frame elevation": App.Vector(0, -1, 0),   # along the building
    "side elevation": App.Vector(-1, 0, 0),    # across the building
    "roof plan": App.Vector(0, 0, -1),         # from above
}


def _fuse(objects) -> Part.Shape:
    """One shape from every built element.

    Compound rather than a boolean fuse: fusing sixty steel members is slow
    and can fail on touching faces, and hidden-line removal does not need
    them welded together.
    """
    shapes = []
    for o in objects:
        sh = getattr(o, "Shape", None)
        if sh is not None and not sh.isNull():
            shapes.append(sh)
    if not shapes:
        raise DrawingError("no shapes to draw")
    return Part.makeCompound(shapes)


def _project(shape, direction):
    """``(visible_shape, hidden_shape)`` in the projection plane."""
    parts = TechDraw.project(shape, direction)
    visible = parts[0] if len(parts) > 0 else None
    hidden = parts[1] if len(parts) > 1 else None
    return visible, hidden


def _orient(polylines):
    """Turn the projection plane's axes into drawing-sheet axes.

    ``TechDraw.project`` returns geometry in ITS OWN plane basis, which for
    a horizontal view direction comes back with the model's Z along the
    plane's -x. Drawn as-is, an elevation lies on its side: 30 m of span up
    the sheet and 8.5 m of height across it. It still looks like a frame,
    which is exactly why this is worth a named function and a test rather
    than a glance at the output.

    ``(x, y) -> (y, -x)`` is a single 90 degree rotation that puts height
    vertical on both elevations. The roof plan is rotated with it, which
    puts the building's length across the sheet - the usual way a long
    building is drawn on a landscape sheet anyway.
    """
    return [[(y, -x) for x, y in pl] for pl in polylines]


def _edges_2d(shape, deflection=2.0):
    """Every projected edge as a list of 2D points.

    Curves are discretised - a tapered flange projects to a straight line,
    but a rod projects to arcs, and an SVG polyline is the simplest thing
    that renders both correctly.
    """
    out = []
    if shape is None:
        return out
    for e in shape.Edges:
        try:
            pts = e.discretize(Deflection=deflection)
        except Exception:                                # noqa: BLE001
            pts = [v.Point for v in e.Vertexes]
        if len(pts) >= 2:
            out.append([(p.x, p.y) for p in pts])
    return out


def _pick_scale(w_mm: float, h_mm: float, pad: float = 0.0) -> tuple[float, str]:
    """The largest drawing-office scale that fits the sheet.

    ``pad`` is sheet millimetres to keep clear for dimensions. It has to be
    taken off BEFORE the scale is chosen: a drawing scaled to fill the sheet
    and then dimensioned puts its dimension lines over the border, which is
    how "we added dimensions" turns into "the sheet is unreadable".
    """
    avail_w = SHEET_W - 2 * MARGIN - pad
    avail_h = SHEET_H - 2 * MARGIN - TITLE_H - pad
    for s in _SCALES:
        if w_mm / s <= avail_w and h_mm / s <= avail_h:
            return float(s), str(s)
    s = float(_SCALES[-1])
    return s, str(int(s))


def _esc(t: str) -> str:
    return (
        str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------

#: Where a MODEL point lands in DRAWING coordinates, per view.
#:
#: ``TechDraw.project`` returns geometry in its own plane basis and
#: :func:`_orient` then rotates it upright, so the composition of the two is
#: what a dimension has to be placed in.  These three functions ARE that
#: composition, measured rather than assumed: a 1000 x 2000 x 3000 box at a
#: known origin was projected in each direction and the corners read back.
#:
#:     frame elevation   (x, z)     span across the sheet, height up
#:     side elevation    (-y, z)    the building runs right to left
#:     roof plan         (y, x)     length across the sheet, span up
#:
#: A dimension placed with the wrong one of these lands somewhere plausible
#: and points at the wrong steel, so ``selftest`` checks each mapping against
#: the projected bounding box of the real model rather than trusting it.
VIEW_TO_SHEET = {
    "frame elevation": lambda x, y, z: (x, z),
    "side elevation": lambda x, y, z: (-y, z),
    "roof plan": lambda x, y, z: (y, x),
}

_PROJ_CACHE: dict[tuple, object] = {}


def projection_map(direction):
    """The model -> drawing map for ANY view direction, MEASURED.

    ``TechDraw`` does not publish the basis it projects into, and the three
    entries in :data:`VIEW_TO_SHEET` were found by projecting a box and
    reading the corners back. That works for three fixed views and not for
    the fourth: a bracing gusset lies in an inclined plane, and drawn along
    a cardinal axis it comes out foreshortened - a part sheet showing a 500
    mm plate as 778 mm.

    So the basis is measured at run time instead. The projection is linear
    and passes through the origin, so projecting one line from the origin
    along each model axis reads off one column of the matrix each. Three
    tiny projections, exact for any direction, and the three hard-coded
    views above are kept as an independent check on it in ``selftest``.
    """
    d = App.Vector(direction)
    if d.Length < 1e-12:
        raise DrawingError("cannot project along a zero direction")
    d = App.Vector(d.x / d.Length, d.y / d.Length, d.z / d.Length)
    key = (round(d.x, 9), round(d.y, 9), round(d.z, 9))
    cached = _PROJ_CACHE.get(key)
    if cached is not None:
        return cached

    L = 1000.0
    e1, e2 = [], []
    for axis in (App.Vector(L, 0, 0), App.Vector(0, L, 0), App.Vector(0, 0, L)):
        img = App.Vector(0, 0, 0)
        try:
            proj = TechDraw.project(
                Part.makeLine(App.Vector(0, 0, 0), axis), d
            )[0]
            pts = [v.Point for v in proj.Vertexes]
            if pts:
                # One vertex is the image of the origin, which is (0, 0);
                # the other is the image of the axis. An axis PARALLEL to
                # the view direction projects to a point at the origin, and
                # then both are (0, 0) - which is the right answer.
                img = max(pts, key=lambda v: v.Length)
        except Exception:                                # noqa: BLE001
            img = App.Vector(0, 0, 0)
        e1.append(img.x / L)
        e2.append(img.y / L)

    a, b = tuple(e1), tuple(e2)

    def m(x, y, z, a=a, b=b):
        px = a[0] * x + a[1] * y + a[2] * z
        py = b[0] * x + b[1] * y + b[2] * z
        return (py, -px)            # the same rotation _orient applies

    _PROJ_CACHE[key] = m
    return m

#: Sheet millimetres.  These are drawn at SHEET scale, not model scale, so a
#: 1:200 GA and a 1:10 part sheet get the same size arrowheads and the same
#: readable text - which is the whole reason dimensions are rendered outside
#: the scaled group.
DIM_TEXT = 2.6
DIM_ARROW = 2.2
DIM_GAP = 1.2                 # object to the start of the extension line
DIM_OVERRUN = 1.5             # extension line past the dimension line
DIM_STEP = 9.0                # between one dimension chain and the next
#: Kept clear on the left and below for two chains and their text.
DIM_PAD = 3.0 * DIM_STEP


@dataclass(frozen=True)
class Dimension:
    """One dimension, in DRAWING coordinates (model mm, before scaling).

    ``a`` and ``b`` are the two points being dimensioned.  ``axis`` says
    which way the dimension is read - ``"h"`` measures the horizontal
    distance and sits below the geometry, ``"v"`` measures the vertical and
    sits to its left.  ``offset`` is in SHEET millimetres from the geometry's
    bounding box, so a chain steps out by ``DIM_STEP`` and does not depend on
    the drawing scale.

    ``text`` is normally None, and then the dimension reads the distance
    between its own two points.  Where it is set, the value came from the
    DESIGN and the two are cross-checked - a dimension that does not match
    the line it points at is worse than no dimension at all.
    """

    a: tuple[float, float]
    b: tuple[float, float]
    axis: str = "h"
    offset: float = 10.0
    text: str | None = None

    @property
    def value(self) -> float:
        """The distance this dimension measures, model mm."""
        if self.axis == "h":
            return abs(self.b[0] - self.a[0])
        return abs(self.b[1] - self.a[1])

    def label(self) -> str:
        return self.text if self.text is not None else f"{self.value:.0f}"


def _arrow(x: float, y: float, dx: float, dy: float) -> str:
    """A solid arrowhead at (x, y) pointing along the unit vector (dx, dy)."""
    bx, by = x - dx * DIM_ARROW, y - dy * DIM_ARROW
    w = DIM_ARROW * 0.30
    return (
        f'<polygon points="{x:.2f},{y:.2f} '
        f'{bx - dy * w:.2f},{by + dx * w:.2f} '
        f'{bx + dy * w:.2f},{by - dx * w:.2f}" fill="black"/>'
    )


def _dimension_svg(dim: Dimension, to_sheet, bbox_sheet) -> list[str]:
    """One dimension as SVG, in sheet coordinates.

    Extension lines, a dimension line, two arrowheads and the text - the
    four things that were missing. Everything is placed off the drawing's
    own bounding box on the sheet, so a dimension cannot land on top of the
    steel it is measuring.
    """
    (sx0, sy0, sx1, sy1) = bbox_sheet
    ax, ay = to_sheet(*dim.a)
    bx, by = to_sheet(*dim.b)
    out: list[str] = []
    stroke = 'stroke="black" stroke-width="0.18"'

    if dim.axis == "h":
        # Below the drawing. SVG y grows DOWN, so "below" is +y.
        line_y = sy1 + dim.offset
        for px, py in ((ax, ay), (bx, by)):
            out.append(
                f'<line x1="{px:.2f}" y1="{py + DIM_GAP:.2f}" '
                f'x2="{px:.2f}" y2="{line_y + DIM_OVERRUN:.2f}" {stroke}/>'
            )
        out.append(
            f'<line x1="{ax:.2f}" y1="{line_y:.2f}" '
            f'x2="{bx:.2f}" y2="{line_y:.2f}" {stroke}/>'
        )
        lo, hi = (ax, bx) if ax <= bx else (bx, ax)
        out.append(_arrow(lo, line_y, 1.0, 0.0))
        out.append(_arrow(hi, line_y, -1.0, 0.0))
        out.append(
            f'<text x="{(ax + bx) / 2.0:.2f}" y="{line_y - 1.0:.2f}" '
            f'font-family="sans-serif" font-size="{DIM_TEXT}" '
            f'text-anchor="middle">{_esc(dim.label())}</text>'
        )
    else:
        # To the left of the drawing.
        line_x = sx0 - dim.offset
        for px, py in ((ax, ay), (bx, by)):
            out.append(
                f'<line x1="{px - DIM_GAP:.2f}" y1="{py:.2f}" '
                f'x2="{line_x - DIM_OVERRUN:.2f}" y2="{py:.2f}" {stroke}/>'
            )
        out.append(
            f'<line x1="{line_x:.2f}" y1="{ay:.2f}" '
            f'x2="{line_x:.2f}" y2="{by:.2f}" {stroke}/>'
        )
        lo, hi = (ay, by) if ay <= by else (by, ay)
        out.append(_arrow(line_x, lo, 0.0, 1.0))
        out.append(_arrow(line_x, hi, 0.0, -1.0))
        my = (ay + by) / 2.0
        out.append(
            f'<text x="{line_x - 1.2:.2f}" y="{my:.2f}" '
            f'font-family="sans-serif" font-size="{DIM_TEXT}" '
            f'text-anchor="middle" '
            f'transform="rotate(-90 {line_x - 1.2:.2f} {my:.2f})">'
            f'{_esc(dim.label())}</text>'
        )
    return out


def _chain(points, y, axis="h", offset=10.0):
    """Successive dimensions between neighbouring set-out points.

    A chain plus an overall is how a grid is dimensioned, and it is not
    decoration: the chain is what a setter-out works to and the overall is
    what checks the chain adds up.
    """
    dims = []
    for p, q in zip(points, points[1:]):
        if abs(q - p) < 1e-6:
            continue
        if axis == "h":
            dims.append(Dimension((p, y), (q, y), "h", offset))
        else:
            dims.append(Dimension((y, p), (y, q), "v", offset))
    return dims


def _ga_dimensions(label: str, geometry) -> list[Dimension]:
    """The set-out dimensions for one general arrangement view.

    Every value here comes from ``PEBGeometry`` - the same object the frame
    was analysed and built from - and every POSITION is mapped through
    :data:`VIEW_TO_SHEET`. So the dimensions are the design's own numbers
    placed on the design's own geometry, not measured off a picture.
    """
    m = VIEW_TO_SHEET.get(label)
    if m is None:
        return []

    width = geometry.width
    length = geometry.length
    eave = geometry.eave_height
    ridge = geometry.ridge_height

    # Column lines across the frame, and frame lines along the building.
    xs, acc = [0.0], 0.0
    for s in geometry.span_widths:
        acc += s
        xs.append(acc)
    ys, acc = [0.0], 0.0
    for s in geometry.bay_spacings:
        acc += s
        ys.append(acc)

    dims: list[Dimension] = []
    if label == "frame elevation":
        base = m(0.0, 0.0, 0.0)[1]
        # Column lines first, the overall span outside them - but only
        # where there is more than one span. On a clear-span frame the chain
        # IS the overall, and printing 30000 twice, once above the other,
        # reads like two different dimensions that happen to agree.
        dims += _chain([m(x, 0.0, 0.0)[0] for x in xs], base, "h", DIM_STEP)
        if len(xs) > 2:
            dims.append(Dimension(m(0.0, 0.0, 0.0), m(width, 0.0, 0.0),
                                  "h", DIM_STEP * 2))
        # Eave and ridge levels, out to the left.
        dims.append(Dimension(m(0.0, 0.0, 0.0), m(0.0, 0.0, eave),
                              "v", DIM_STEP))
        dims.append(Dimension(m(0.0, 0.0, 0.0), m(0.0, 0.0, ridge),
                              "v", DIM_STEP * 2))
    elif label == "side elevation":
        base = m(0.0, 0.0, 0.0)[1]
        dims += _chain([m(0.0, y, 0.0)[0] for y in ys], base, "h", DIM_STEP)
        if len(ys) > 2:
            dims.append(Dimension(m(0.0, 0.0, 0.0), m(0.0, length, 0.0),
                                  "h", DIM_STEP * 2))
        dims.append(Dimension(m(0.0, 0.0, 0.0), m(0.0, 0.0, eave),
                              "v", DIM_STEP))
    elif label == "roof plan":
        base = m(0.0, 0.0, 0.0)[1]
        dims += _chain([m(0.0, y, 0.0)[0] for y in ys], base, "h", DIM_STEP)
        if len(ys) > 2:
            dims.append(Dimension(m(0.0, 0.0, 0.0), m(0.0, length, 0.0),
                                  "h", DIM_STEP * 2))
        dims += _chain([m(x, 0.0, 0.0)[1] for x in xs],
                       m(0.0, 0.0, 0.0)[0], "v", DIM_STEP)
        if len(xs) > 2:
            dims.append(Dimension(m(0.0, 0.0, 0.0), m(width, 0.0, 0.0),
                                  "v", DIM_STEP * 2))
    return dims


def _overall_dimensions(bbox) -> list[Dimension]:
    """Overall length and depth of whatever is on the sheet.

    Taken from the PROJECTED extents, so on a single-part sheet these
    measure the drawn outline itself. That is the right source there: the
    part sheet has to agree with the part, and the part is the solid the
    schedule and the IFC were taken off.
    """
    x0, y0, x1, y1 = bbox
    dims = []
    if x1 - x0 > 1e-6:
        dims.append(Dimension((x0, y0), (x1, y0), "h", DIM_STEP))
    if y1 - y0 > 1e-6:
        dims.append(Dimension((x0, y0), (x0, y1), "v", DIM_STEP))
    return dims


def _hole_dimensions(geom: dict, obj, m) -> list[Dimension]:
    """Bolt set-out on a connection-plate sheet.

    A plate drawing without its hole positions is a rectangle.  The hole
    centres are known in the plate's own ``(u, v)``, and the builder stamped
    the plate's ORIGIN and its two axis vectors on the object - so each hole
    is turned back into a model point and mapped through the same
    :data:`VIEW_TO_SHEET` function the view was drawn with.

    That is the long way round, and it is the right way round.  Deriving the
    positions from the drawn rectangle instead would need the sign of ``v``,
    which a rectangle does not carry: get it backwards and every dimension
    is right and attached to the wrong gap - on an end plate, where the top
    and bottom set-outs differ, that is a plate drilled upside down.
    """
    holes = [(float(u), float(v)) for u, v in geom.get("holes_uv_mm", ())]
    if len(holes) < 2 or m is None:
        return []
    try:
        o = obj.PlateOrigin
        eu = obj.PlateU
        ev = obj.PlateV
    except Exception:                                   # noqa: BLE001
        return []
    if eu.Length < 1e-9 or ev.Length < 1e-9:
        return []

    def to_drawing(u, v):
        p = o + App.Vector(eu) * u + App.Vector(ev) * v
        return m(p.x, p.y, p.z)

    half_h = float(geom["height_mm"]) / 2.0
    w = float(geom["width_mm"]) / 2.0
    v_top = float(geom.get("ext_top_mm", half_h))
    v_bot = v_top - float(geom["height_mm"])
    corners = [to_drawing(su * w, sv)
               for su in (-1.0, 1.0) for sv in (v_bot, v_top)]
    cx0 = min(c[0] for c in corners)
    cx1 = max(c[0] for c in corners)
    cy0 = min(c[1] for c in corners)
    cy1 = max(c[1] for c in corners)

    pts = [to_drawing(u, v) for u, v in holes]
    xs = sorted({round(x, 3) for x, _ in pts})
    ys = sorted({round(y, 3) for _, y in pts})

    # Edge -> hole -> ... -> hole -> edge, both ways: a chain that adds up
    # to the plate is a chain a setter-out can check.
    dims: list[Dimension] = []
    dims += _chain([cx0] + xs + [cx1], cy0, "h", DIM_STEP * 2)
    dims += _chain([cy0] + ys + [cy1], cx0, "v", DIM_STEP * 2)
    return dims


def _sheet_svg(
    polylines, *, title, subtitle, job, view, scale_txt, stamp, bbox,
    dimensions=(), dim_pad=0.0,
) -> str:
    """Wrap projected geometry in an A3 sheet with a title block.

    ``dimensions`` are given in DRAWING coordinates - model millimetres -
    and are rendered in SHEET coordinates, outside the scaled group. That is
    the difference between a dimension and a line: the steel scales with the
    drawing and the arrowheads and the text do not, so a 1:200 GA and a 1:10
    part sheet are both readable.
    """
    (x0, y0, x1, y1) = bbox
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    s = float(scale_txt)

    avail_w = SHEET_W - 2 * MARGIN - dim_pad
    avail_h = SHEET_H - 2 * MARGIN - TITLE_H - dim_pad
    # Centre the drawing in the space ABOVE the title block, with the
    # dimension allowance pushed to the LEFT and BELOW - which is where the
    # dimensions are drawn.
    #
    # ``oy`` is measured from the sheet's BOTTOM, so the title block has to
    # be added to it as well as taken off the available height. It was only
    # taken off, which put the drawing's lower edge 28 mm INTO the title
    # block - invisible while nothing was drawn below the steel, and
    # immediately visible once the dimensions were.
    ox = MARGIN + dim_pad + (avail_w - w / s) / 2.0 - x0 / s
    oy = MARGIN + TITLE_H + dim_pad + (avail_h - h / s) / 2.0 - y0 / s

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{SHEET_W}mm" height="{SHEET_H}mm" '
        f'viewBox="0 0 {SHEET_W} {SHEET_H}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<rect x="{MARGIN/2}" y="{MARGIN/2}" '
        f'width="{SHEET_W-MARGIN}" height="{SHEET_H-MARGIN}" '
        f'fill="none" stroke="black" stroke-width="0.5"/>',
        # the drawing, y flipped so up is up
        f'<g transform="translate({ox:.4f},{SHEET_H-oy:.4f}) scale({1/s:.8f},{-1/s:.8f})" '
        f'fill="none" stroke="black" stroke-width="{0.35*s:.4f}" '
        f'stroke-linecap="round" stroke-linejoin="round">',
    ]
    for pl in polylines:
        d = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in pl)
        parts.append(f'<path d="{d}"/>')
    parts.append("</g>")

    if dimensions:
        # The SAME transform the geometry group uses, applied by hand, so a
        # dimension and the line it measures cannot drift apart.
        def to_sheet(dx, dy):
            return (ox + dx / s, (SHEET_H - oy) - dy / s)

        bx0, by0 = to_sheet(x0, y1)          # y flips: model max -> sheet min
        bx1, by1 = to_sheet(x1, y0)
        bbox_sheet = (min(bx0, bx1), min(by0, by1),
                      max(bx0, bx1), max(by0, by1))
        parts.append('<g>')
        for dim in dimensions:
            parts.extend(_dimension_svg(dim, to_sheet, bbox_sheet))
        parts.append('</g>')

    # title block
    ty = SHEET_H - MARGIN / 2 - TITLE_H
    tx = SHEET_W - MARGIN / 2 - 190.0
    parts += [
        f'<rect x="{tx}" y="{ty}" width="190" height="{TITLE_H}" '
        f'fill="none" stroke="black" stroke-width="0.5"/>',
        f'<text x="{tx+4}" y="{ty+9}" font-family="sans-serif" '
        f'font-size="6" font-weight="bold">{_esc(title)}</text>',
        f'<text x="{tx+4}" y="{ty+17}" font-family="sans-serif" '
        f'font-size="4.5">{_esc(subtitle)}</text>',
        f'<text x="{tx+4}" y="{ty+25}" font-family="sans-serif" '
        f'font-size="4.5">Job {_esc(job)}   {_esc(view)}   Scale 1:{scale_txt}'
        f'   {datetime.date.today().isoformat()}</text>',
        f'<text x="{tx+4}" y="{ty+31}" font-family="sans-serif" '
        f'font-size="4.5">Generated by BIMSteelAuto - dimensions in mm</text>',
    ]
    if stamp:
        parts.append(
            f'<text x="{MARGIN}" y="{ty+20}" font-family="sans-serif" '
            f'font-size="11" font-weight="bold" fill="#a3121f">'
            f'{_esc(stamp)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _stamp_for(result) -> str:
    """The banner, taken from the DESIGN rather than from a caller.

    A drawing produced from a failing design must say so on its face. This
    is the one thing on the sheet nobody gets to override.
    """
    if result is None:
        return "NOT FOR ISSUE - no design result supplied"
    if result.ready_for_issue():
        return ""
    return "NOT FOR ISSUE"


# ---------------------------------------------------------------------------
# General arrangement
# ---------------------------------------------------------------------------


def general_arrangement(
    doc, out_dir: str, *, result=None, objects=None, views=None
) -> DrawingSet:
    """Frame elevation, side elevation and roof plan, one sheet each."""
    if objects is None:
        objects = [o for o in doc.Objects if hasattr(o, "MemberType")]
    if not objects:
        raise DrawingError(
            "nothing in this document carries a MemberType - the model was "
            "not built by this adapter"
        )
    os.makedirs(out_dir, exist_ok=True)
    shape = _fuse(objects)
    out = DrawingSet()

    project = result.spec.project_name if result is not None else doc.Name
    job = result.spec.job_number if result is not None else doc.Name
    stamp = _stamp_for(result)

    for label, direction in (views or VIEWS).items():
        try:
            visible, _hidden = _project(shape, direction)
        except Exception as exc:                        # noqa: BLE001
            out.warnings.append(f"{label}: projection failed - {exc}")
            continue
        polylines = _orient(_edges_2d(visible))
        if not polylines:
            out.warnings.append(f"{label}: projected to nothing")
            continue

        xs = [x for pl in polylines for x, _ in pl]
        ys = [y for pl in polylines for _, y in pl]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        _, scale_txt = _pick_scale(
            bbox[2] - bbox[0], bbox[3] - bbox[1], DIM_PAD
        )

        dims = []
        if result is not None:
            dims = _ga_dimensions(label, result.spec.geometry)
            # A set-out dimension that runs off the drawn steel is pointing
            # at something that was not built. Say so rather than printing
            # it: an unchecked dimension on a GA is how a wrong span gets
            # onto a fabrication drawing.
            pad = 1.0 + 1e-6
            for d in dims:
                for px, py in (d.a, d.b):
                    if not (bbox[0] - pad <= px <= bbox[2] + pad
                            and bbox[1] - pad <= py <= bbox[3] + pad):
                        out.warnings.append(
                            f"{label}: the {d.label()} mm dimension is set "
                            f"out at {px:.0f}, {py:.0f}, which is outside "
                            f"the drawn model - not drawn"
                        )
                        break
            dims = [
                d for d in dims
                if all(bbox[0] - pad <= p[0] <= bbox[2] + pad
                       and bbox[1] - pad <= p[1] <= bbox[3] + pad
                       for p in (d.a, d.b))
            ]

        svg = _sheet_svg(
            polylines, title=project, subtitle="General arrangement",
            job=job, view=label.title(), scale_txt=scale_txt, stamp=stamp,
            bbox=bbox, dimensions=dims, dim_pad=DIM_PAD,
        )
        path = os.path.join(
            out_dir, "GA_" + label.replace(" ", "_") + ".svg"
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        out.sheets.append(
            Sheet(name=label, path=path, scale=scale_txt, view=label.title())
        )
    return out


# ---------------------------------------------------------------------------
# Single-part drawings
# ---------------------------------------------------------------------------


def single_part_drawings(
    doc, out_dir: str, *, result=None, objects=None, limit: int = 40
) -> DrawingSet:
    """One sheet per distinct MARK - what a fabricator cuts from.

    Members are grouped by their mark PREFIX (``C1-F3`` -> ``C1``), because
    every frame line carries the same C1 and drawing it six times would be
    six identical sheets. The count is printed on the sheet instead.
    """
    if objects is None:
        objects = [o for o in doc.Objects if hasattr(o, "MemberType")]
    os.makedirs(out_dir, exist_ok=True)
    out = DrawingSet()

    project = result.spec.project_name if result is not None else doc.Name
    job = result.spec.job_number if result is not None else doc.Name
    stamp = _stamp_for(result)

    groups: dict[str, list] = {}
    for o in objects:
        mark = str(getattr(o, "Mark", o.Name)).split("-")[0]
        groups.setdefault(mark, []).append(o)

    # The shop geometry, by connection mark, so a plate's part sheet can be
    # dimensioned from the layout that produced it rather than from the
    # picture of it. Keyed on the mark the builder stamped, which is the
    # connection's own mark.
    plate_geometry: dict[str, dict] = {}
    for conn in (getattr(result, "connections", ()) or ()):
        detail = getattr(conn, "detail", None) or {}
        geom = detail.get("geometry") or {}
        if isinstance(geom, dict) and geom.get("available") is True:
            plate_geometry[conn.mark] = geom

    for mark in sorted(groups)[:limit]:
        members = groups[mark]
        obj = members[0]
        shape = obj.Shape
        # Look at the part along its THINNEST direction, which is the one
        # view that shows a part square-on: the web of a member, the face of
        # a plate. Looking along the LONGEST instead - which is what this
        # did first - draws a 20 mm end plate as a 20 mm line and calls it a
        # single-part drawing.
        bb = shape.BoundBox
        axes = ((bb.XLength, App.Vector(-1, 0, 0)),
                (bb.YLength, App.Vector(0, -1, 0)),
                (bb.ZLength, App.Vector(0, 0, -1)))
        direction = min(axes, key=lambda t: t[0])[1]

        # A plate carries its own plane, and the only true-shape view of a
        # plate is along its normal. Cardinal axes are right for a member
        # and wrong for a gusset, which lies in the plane of the bracing:
        # viewed along -X it drew a 500 mm plate as 778 mm of foreshortening
        # and dimensioned it that way.
        plate_normal = None
        try:
            eu, ev = obj.PlateU, obj.PlateV
            if eu.Length > 1e-9 and ev.Length > 1e-9:
                nrm = eu.cross(ev)
                if nrm.Length > 1e-9:
                    plate_normal = App.Vector(
                        -nrm.x / nrm.Length, -nrm.y / nrm.Length,
                        -nrm.z / nrm.Length,
                    )
        except Exception:                                # noqa: BLE001
            plate_normal = None
        if plate_normal is not None:
            direction = plate_normal

        try:
            visible, _ = _project(shape, direction)
        except Exception as exc:                        # noqa: BLE001
            out.warnings.append(f"{mark}: projection failed - {exc}")
            continue
        polylines = _orient(_edges_2d(visible, deflection=1.0))
        if not polylines:
            out.warnings.append(f"{mark}: projected to nothing")
            continue

        # A plate viewed along its own normal is true shape, and it can
        # still land on the sheet at whatever angle its brace runs at. Turn
        # it so its own u axis is horizontal: otherwise the overall
        # dimensions measure the bounding box of a tilted rectangle - a 500
        # x 600 gusset reads 778 x 778, which is not a dimension of
        # anything.
        m = projection_map(direction)
        if plate_normal is not None:
            o, eu = obj.PlateOrigin, obj.PlateU
            p0 = m(o.x, o.y, o.z)
            p1 = m(o.x + eu.x, o.y + eu.y, o.z + eu.z)
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            n = math.hypot(dx, dy)
            if n > 1e-9:
                c, sn = dx / n, dy / n
                polylines = [
                    [(x * c + y * sn, -x * sn + y * c) for x, y in pl]
                    for pl in polylines
                ]

                def m(x, y, z, _m=m, c=c, sn=sn):
                    px, py = _m(x, y, z)
                    return (px * c + py * sn, -px * sn + py * c)

        xs = [x for pl in polylines for x, _ in pl]
        ys = [y for pl in polylines for _, y in pl]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        _, scale_txt = _pick_scale(
            bbox[2] - bbox[0], bbox[3] - bbox[1], DIM_PAD
        )

        profile = str(getattr(obj, "Profile", ""))
        length = float(getattr(obj, "Length_mm", 0.0) or 0.0)
        mass = float(getattr(obj, "Weight_kg", 0.0) or 0.0)
        subtitle = (
            f"{mark}  x{len(members)}   {profile}   "
            f"L = {length:.0f} mm   {mass:.1f} kg each"
        )
        # Overall length and depth always; the bolt set-out too where the
        # part is a connection plate and the design carried its layout.
        dims = _overall_dimensions(bbox)
        geom = plate_geometry.get(mark)
        if geom is not None:
            dims = dims + _hole_dimensions(geom, obj, m)

        svg = _sheet_svg(
            polylines, title=project, subtitle=subtitle, job=job,
            view=f"Single part {mark}", scale_txt=scale_txt, stamp=stamp,
            bbox=bbox, dimensions=dims, dim_pad=DIM_PAD,
        )
        path = os.path.join(out_dir, f"PART_{mark}.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        out.sheets.append(
            Sheet(name=mark, path=path, scale=scale_txt,
                  view=f"Single part {mark}")
        )

    if len(groups) > limit:
        out.warnings.append(
            f"{len(groups)} distinct marks, only the first {limit} drawn - "
            "raise limit= to draw them all"
        )
    return out


# ---------------------------------------------------------------------------
# DXF
# ---------------------------------------------------------------------------


def export_dxf(doc, out_dir: str, *, objects=None, views=None) -> DrawingSet:
    """The same projections as DXF, which is what a fabricator imports."""
    if objects is None:
        objects = [o for o in doc.Objects if hasattr(o, "MemberType")]
    os.makedirs(out_dir, exist_ok=True)
    shape = _fuse(objects)
    out = DrawingSet()

    for label, direction in (views or VIEWS).items():
        path = os.path.join(
            out_dir, "GA_" + label.replace(" ", "_") + ".dxf"
        )
        try:
            dxf = TechDraw.projectToDXF(shape, direction)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(dxf)
        except Exception as exc:                        # noqa: BLE001
            out.warnings.append(f"{label}: DXF export failed - {exc}")
            continue
        if os.path.getsize(path) == 0:
            out.warnings.append(f"{label}: DXF came out empty")
            continue
        out.sheets.append(
            Sheet(name=label, path=path, scale="1", view=label.title())
        )
    return out
