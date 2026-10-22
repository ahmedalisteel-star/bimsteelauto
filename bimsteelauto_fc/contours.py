"""
bimsteelauto_fc.contours - the result picture, drawn in the 3D view.

    contours.json  ->  draw_contours()  ->  a coloured deflected frame

This is Layer 2: it draws what the engine computed and works nothing out
for itself.  Every number on the screen - the displacement, the stress, the
utilisation, the scale of the deflected shape - comes from
:mod:`bimsteelauto.analysis.results`, which is where those relations are
verified.  If a colour looks wrong, the engine is where to look; this
module can only place it in the wrong spot.

What is drawn
-------------
* the frame in its deflected position, magnified, one straight edge per
  station interval, each edge coloured by its own value - the same way
  STAAD draws a contour, and for the same reason: a member is not one
  colour, a tapered rafter least of all
* the undeformed frame behind it, thin and grey, so the movement is
  readable against something
* a colour bar with numbered bands, and a title block naming the
  combination, the quantity, its unit and the magnification

Coordinates
-----------
The engine's frame plane is (X across, Y up).  The BIM model built by
``builder.py`` places a frame-line point at ``Vector(X, y_line, Y)``, and
``ContourSet.frame_y`` carries ``y_line``, so the contour lands on its own
frame line and lines up with the model in a side view.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import FreeCAD as App
import Part

__all__ = [
    "ContourError",
    "ContourDraw",
    "draw_contours",
    "colour_for",
    "RAINBOW",
]


class ContourError(RuntimeError):
    """The contour could not be drawn."""


#: The analysis-package rainbow, low to high: magenta, red, orange, yellow,
#: green, cyan, blue.  Kept because it is what an engineer checking this
#: drawing against a STAAD plot expects to see; it is a lookup, not a
#: judgement about colour.
RAINBOW = (
    (0.80, 0.00, 0.80),
    (0.60, 0.00, 0.90),
    (0.90, 0.00, 0.40),
    (1.00, 0.00, 0.00),
    (1.00, 0.35, 0.00),
    (1.00, 0.60, 0.00),
    (1.00, 0.85, 0.00),
    (0.85, 1.00, 0.00),
    (0.40, 0.95, 0.00),
    (0.00, 0.85, 0.30),
    (0.00, 0.85, 0.85),
    (0.00, 0.55, 1.00),
    (0.00, 0.20, 1.00),
)

#: Utilisation reads as a pass/fail picture, not a rainbow: green under
#: 0.75, amber to 1.00, red over.  A ratio contour that goes blue at the
#: top would hide the only thing it is drawn to show.
UTIL_BANDS = (
    (0.50, (0.00, 0.70, 0.25)),
    (0.75, (0.55, 0.85, 0.00)),
    (0.90, (1.00, 0.80, 0.00)),
    (1.00, (1.00, 0.45, 0.00)),
    (1e9,  (0.90, 0.00, 0.00)),
)


def colour_for(value: float, vmin: float, vmax: float, *, utilisation: bool = False):
    """Band colour for one value."""
    if utilisation:
        for limit, rgb in UTIL_BANDS:
            if value <= limit:
                return rgb
        return UTIL_BANDS[-1][1]                       # pragma: no cover
    if vmax <= vmin:
        return RAINBOW[0]
    f = (value - vmin) / (vmax - vmin)
    i = int(f * len(RAINBOW))
    return RAINBOW[max(0, min(len(RAINBOW) - 1, i))]


@dataclass
class ContourDraw:
    """What was drawn, for the caller's report."""

    group_name: str
    quantity: str
    combo: str
    scale: float
    vmin: float
    vmax: float
    peak_member: str
    peak_x: float
    unit: str
    notes: tuple[str, ...] = ()

    def summary(self) -> str:
        return (
            f"{self.quantity} contour, {self.combo}: peak "
            f"{self.vmax:,.3f} {self.unit} on {self.peak_member} at "
            f"x = {self.peak_x:,.0f} mm, deflected shape x{self.scale:,.0f}"
        )


# ---------------------------------------------------------------------------


def _point(cs, X: float, Y: float, Z: float | None = None) -> App.Vector:
    """Engine coordinates to model coordinates.

    The engine works X across the frame, **Y UP**, Z along the building -
    a convention PyNite forces, because its local-axis rules are written
    around Y being up.  Layer 2's model is X across, Y along, Z up.  So the
    mapping is one swap and it lives here, in one function, for both the
    2D and the 3D path::

        (X, Y, Z)  ->  App.Vector(X, Z, Y)

    A 2D contour set has no Z of its own: its members lie in one frame's
    plane, so Z is that frame line's position along the building.  That is
    what ``frame_y`` is, and it is the fallback rather than zero - zero
    would pile every frame onto grid line 1.
    """
    return App.Vector(X, cs.frame_y if Z is None else Z, Y)


def _clear_group(doc, name: str):
    old = doc.getObject(name)
    if old is None:
        return
    for child in list(getattr(old, "Group", [])):
        doc.removeObject(child.Name)
    doc.removeObject(old.Name)


def _label(doc, text, point: App.Vector, size: float, colour=(0.1, 0.1, 0.1)):
    """A screen-facing annotation; ``text`` is a string or a list of lines.

    Draft is imported here, not at module scope: this module is imported by
    the workbench command, and Draft pulls in a large part of the GUI."""
    import Draft

    lines = [text] if isinstance(text, str) else list(text)
    t = Draft.make_text(lines, point)
    # Draft lays text in the XY plane.  The frame is in XZ, so unrotated
    # text is edge-on to the view that the contour is read in - present in
    # the document, invisible on the screen, which looks like a bug in the
    # contour rather than a missing rotation.
    t.Placement = App.Placement(point, App.Rotation(App.Vector(1, 0, 0), 90))
    if t.ViewObject is not None:
        t.ViewObject.FontSize = size
        t.ViewObject.TextColor = colour
    return t


def _each(cs, quantity, combo):
    """Every member that HAS this quantity, with its values.

    ``ContourSet.range`` and ``.peak`` scan every member and raise on the
    first that carries nothing. A 3D set has 180 members of which the
    secondary ones have no stress contour, so the scan has to skip rather
    than fail - and what it skipped is reported, not swallowed.
    """
    for m in cs.members:
        try:
            yield m, m.values(quantity, combo)
        except Exception:                               # noqa: BLE001
            continue


def _range(cs, quantity, combo):
    vals = [v for _m, vs in _each(cs, quantity, combo) for v in vs]
    if not vals:
        raise ContourError(
            f"no member in this set carries a {quantity} contour"
        )
    return min(vals), max(vals)


def _peak(cs, quantity, combo):
    best = ("", 0.0, -1.0)
    for m, vs in _each(cs, quantity, combo):
        for i, v in enumerate(vs):
            if abs(v) > best[2]:
                best = (m.mark, m.x[i], abs(v))
    return best


def draw_contours(
    source,
    *,
    quantity: str = "displacement",
    combo: str | None = None,
    scale: float | None = None,
    doc=None,
    show_undeformed: bool = True,
    legend: bool = True,
    line_width: float = 4.0,
) -> ContourDraw:
    """Draw one contour.  ``source`` is a ``ContourSet`` or a path to one.

    ``scale`` is the displacement magnification; ``None`` asks the engine
    for one that makes the shape readable.  It magnifies the PICTURE only -
    every number printed is the real one.
    """
    from bimsteelauto.analysis.results import (
        ContourSet,
        Quantity,
        load_contours,
    )

    cs = source
    if isinstance(source, (str, bytes, os.PathLike)):
        cs = load_contours(source)
    if not isinstance(cs, ContourSet):                 # pragma: no cover
        raise ContourError("source must be a ContourSet or a path to one")

    if quantity not in Quantity.ALL:
        raise ContourError(
            f"{quantity!r} is not one of {', '.join(Quantity.ALL)}"
        )
    combo = combo or cs.governing_combo or cs.combos[0]
    if combo not in cs.combos:
        raise ContourError(
            f"{combo!r} was not solved - this file has "
            f"{len(cs.combos)} combinations"
        )

    is_util = quantity == Quantity.UTILISATION
    vmin, vmax = _range(cs, quantity, combo)
    if is_util:
        vmin = 0.0
    peak_mark, peak_x, _ = _peak(cs, quantity, combo)
    title, unit = Quantity.LABEL[quantity]

    # The deflected shape belongs to the combination being drawn - the
    # engine stores one per combination, so a stress contour for an uplift
    # case is never drawn on a gravity case's shape.
    if scale is None:
        scale = cs.auto_scale(combo)

    doc = doc or App.ActiveDocument or App.newDocument("Contours")
    gname = "BSA_Contour"
    _clear_group(doc, gname)
    group = doc.addObject("App::DocumentObjectGroup", gname)
    group.Label = f"{title} - {combo}"

    edges = []
    colours = []
    skipped: list[str] = []
    for m in cs.members:
        try:
            vals = m.values(quantity, combo)
        except Exception as exc:                        # noqa: BLE001
            # A member with no value for this quantity is LEFT OUT and
            # named, never drawn in the "zero" colour. A secondary member
            # with no stress contour coloured as zero stress would be the
            # calmest thing on the picture.
            skipped.append(f"{m.mark}: {exc}")
            continue
        pts = [
            _point(cs, px, py, pz)
            for px, py, pz in m.deflected3(combo, scale, cs.frame_y)
        ]
        for i in range(len(pts) - 1):
            if pts[i].distanceToPoint(pts[i + 1]) < 1e-9:
                continue
            edges.append(Part.LineSegment(pts[i], pts[i + 1]).toShape())
            v = 0.5 * (vals[i] + vals[i + 1])
            colours.append(colour_for(v, vmin, vmax, utilisation=is_util))

    if not edges:                                      # pragma: no cover
        raise ContourError("nothing to draw - the contour set is empty")

    obj = doc.addObject("Part::Feature", "BSA_ContourShape")
    obj.Label = f"{title} ({unit})"
    obj.Shape = Part.Compound(edges)
    group.addObject(obj)
    doc.recompute()
    vo = obj.ViewObject
    if vo is not None:
        vo.LineWidth = line_width
        # The fourth component is ALPHA, not transparency: 0.0 there makes
        # the whole contour invisible and looks like nothing was drawn.
        vo.LineColorArray = [(r, g, b, 1.0) for r, g, b in colours]

    if show_undeformed:
        base = []
        for m in cs.members:
            Zs = m.Z_at(cs.frame_y)
            pts = [_point(cs, m.X[i], m.Y[i], Zs[i])
                   for i in range(m.n_stations)]
            base.append(Part.LineSegment(pts[0], pts[-1]).toShape())
        u = doc.addObject("Part::Feature", "BSA_ContourUndeformed")
        u.Label = "Undeformed frame"
        u.Shape = Part.Compound(base)
        group.addObject(u)
        doc.recompute()
        if u.ViewObject is not None:
            u.ViewObject.LineWidth = 1.0
            u.ViewObject.LineColor = (0.55, 0.55, 0.55)
            u.ViewObject.DrawStyle = "Dashed"

    if legend:
        _draw_legend(doc, group, cs, quantity, combo, vmin, vmax, scale)

    doc.recompute()
    return ContourDraw(
        group_name=group.Name,
        quantity=quantity,
        combo=combo,
        scale=scale,
        vmin=vmin,
        vmax=vmax,
        peak_member=peak_mark,
        peak_x=peak_x,
        unit=unit,
        notes=tuple(cs.check_notes) + tuple(
            f"not drawn - {line}" for line in skipped
        ),
    )


def _draw_legend(doc, group, cs, quantity, combo, vmin, vmax, scale):
    """Colour bar and title block, in the frame plane, to the right."""
    from bimsteelauto.analysis.results import Quantity

    title, unit = Quantity.LABEL[quantity]
    is_util = quantity == Quantity.UTILISATION
    bands = UTIL_BANDS if is_util else RAINBOW
    n = len(bands)

    xs = [v for m in cs.members for v in m.X]
    ys = [v for m in cs.members for v in m.Y]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    W = max(x1 - x0, 1.0)
    H = max(y1 - y0, 1.0)

    bar_x = x1 + 0.06 * W
    bar_w = 0.020 * W
    bar_h = 0.80 * H
    dy = bar_h / n
    text_size = 0.055 * H

    faces = []
    cols = []
    for i in range(n):
        ylo = y0 + i * dy
        yhi = ylo + dy
        pts = [
            _point(cs, bar_x, ylo), _point(cs, bar_x + bar_w, ylo),
            _point(cs, bar_x + bar_w, yhi), _point(cs, bar_x, yhi),
        ]
        wire = Part.makePolygon(pts + [pts[0]])
        faces.append(Part.Face(wire))
        cols.append(bands[i][1] if is_util else bands[i])

    bar = doc.addObject("Part::Feature", "BSA_ContourLegend")
    bar.Label = "Colour bar"
    bar.Shape = Part.Compound(faces)
    group.addObject(bar)
    doc.recompute()
    if bar.ViewObject is not None:
        bar.ViewObject.DisplayMode = "Shaded"
        # The bar is a flat plate seen from one side; two-sided lighting is
        # what stops it going black when the view is spun round the frame.
        bar.ViewObject.Lighting = "Two side"
        bar.ViewObject.Transparency = 0
        bar.ViewObject.DiffuseColor = [(r, g, b, 1.0) for r, g, b in cols]

    labels = []
    for i in range(n + 1):
        if is_util:
            # The top band is "anything above 1.00", so it is labelled that
            # way rather than repeating 1.00 twice and looking like a bug.
            if i == 0:
                txt = "0.00"
            elif i >= len(UTIL_BANDS):
                txt = f"> 1.00   (peak {vmax:,.2f})"
            else:
                txt = f"{UTIL_BANDS[i - 1][0]:,.2f}"
        else:
            v = vmin + (vmax - vmin) * i / n
            txt = f"{v:,.3g}" if abs(v) < 1000 else f"{v:,.0f}"
        labels.append(
            _label(
                doc,
                txt,
                _point(cs, bar_x + bar_w * 1.4, y0 + i * dy - text_size * 0.4),
                text_size,
            )
        )

    head = [
        f"{cs.project_name}  -  job {cs.job_number}",
        f"{title}  [{unit}]",
        f"combination  {combo}",
        f"frame line {cs.frame_index + 1}"
        + ("   -   second-order (P-Delta)" if cs.p_delta else ""),
    ]
    head.append(f"deflected shape magnified  x {scale:,.0f}")
    labels.append(
        _label(doc, head, _point(cs, x0, y1 + 0.22 * H), text_size * 1.1)
    )
    for t in labels:
        group.addObject(t)
    doc.recompute()
    # Re-applied after the recompute: Draft builds its view provider lazily,
    # and a size or colour set before that is quietly dropped - which is why
    # a legend can come out in Draft's default green at the default size.
    for t in labels[:-1]:
        if t.ViewObject is not None:
            t.ViewObject.FontSize = text_size
            t.ViewObject.TextColor = (0.10, 0.10, 0.10)
    if labels and labels[-1].ViewObject is not None:
        labels[-1].ViewObject.FontSize = text_size * 1.15
        labels[-1].ViewObject.TextColor = (0.10, 0.10, 0.10)
    doc.recompute()
