"""
bimsteelauto_fc.detailing
=========================

Shop geometry -> FreeCAD solids.

This is the second half of the v8 merge.  The first half is
``bimsteelauto.detailing``, which does the layout arithmetic in the engine
and writes it into ``design_result.json``; this module extrudes it.  The
split is deliberate and it is the whole point:

    the ENGINE decides where a hole goes.
    the ADAPTER cuts it.

So there is no dimension in this file.  Every width, height, thickness,
hole position and hole diameter arrives on the ``ConnectionDesign``, in
millimetres, from a layout that pytest already checked longhand.  If a
number is missing, the plate is SKIPPED and named - it is never guessed,
because a plate this module invented would be a plate no limit state ever
covered, and it would look exactly as convincing as the real ones.

What comes from v8
------------------
``peb_building_param_v8`` supplies the primitives - ``plate_box``,
``drill``, ``member_axes`` - and the working rules around them.  What is
NOT taken from v8 is its property model: those classes are FeaturePython
objects expression-bound to a DESIGN spreadsheet, and the binding happens
inside ``build()``, which is also why importing that file costs 95
seconds.  The geometry is what was worth having, and it is re-expressed
here against explicit 3-D corner points rather than a Placement matrix, so
a plate cannot end up in the right place at the wrong angle.

Coordinates
-----------
Same as the rest of Layer 2::

    X  across the frame (the span)
    Y  along the building (the length)
    Z  up

Each plate carries its own ``(u, v)`` frame, and the mapping from that to
the model is stated once per connection type, in ``builder._build_details``,
where it can be read in one place and checked.  For a vertical end plate
``u`` is global Y and ``v`` is global Z, which is what makes the layout's
"offsets down from the top flange" land on the flange.
"""

from __future__ import annotations

import FreeCAD as App
import Part

__all__ = [
    "COVERAGE_GAPS",
    "STEEL_DENSITY",
    "DetailError",
    "plate_solid",
    "drill",
    "geometry_of",
    "why_not",
    "end_plate_solid",
    "base_plate_solid",
    "anchor_solids",
    "cleat_solid",
    "gusset_solid",
    "mass_kg",
]

#: kg/m3.  The same value the engine's layout used for its ``mass_kg``, so
#: the two can be compared without a conversion in between - and they ARE
#: compared, in ``selftest.py``, which is the check that catches a plate
#: built at the wrong size while still being built in the right quantity.
STEEL_DENSITY = 7850.0

COVERAGE_GAPS: tuple[str, ...] = (
    "Bolts and anchors are drawn as plain shanks - no head, nut, washer or "
    "thread. The HOLE is the fabrication information; the fastener stack is "
    "assembly information.",
    "Welds are not modelled. Every plate here is welded to the member it "
    "sits on and none of those welds appears in the solid or the schedule.",
    "The member is not notched, coped or drilled where a plate meets it. "
    "The plate carries its holes; the matching holes in the flange or web "
    "are not cut, so the two are not yet a fabricable pair.",
    "A rod brace gets a rod plate at each END, on the face of the web the "
    "rod passes through - four per X panel, one per brace-end connection. "
    "The plate is sized to the rod by set-out rule "
    "(fabrication.rod_plate_geometry), not by a check: the engine's GP1 is "
    "a bolted gusset that a rod brace does not have, and it is not drawn. "
    "The hillside bevel of a real rod washer is not modelled, and the rod "
    "stops at the plate face instead of running through the web.",
)


class DetailError(RuntimeError):
    """A connection could not be built from this geometry."""


# ---------------------------------------------------------------------------
# Primitives - ported from peb_building_param_v8
# ---------------------------------------------------------------------------


def _u(v: App.Vector) -> App.Vector:
    """Unit vector, as a NEW vector.

    ``App.Vector.normalize()`` and ``.multiply()`` both mutate in place and
    return self, so a shared direction vector normalised once inside a loop
    is a direction vector rescaled on every pass. Every helper in this file
    copies before it scales, for that reason.
    """
    n = v.Length
    if n < 1e-12:
        raise DetailError("cannot normalise a zero-length direction")
    return App.Vector(v.x / n, v.y / n, v.z / n)


def plate_solid(
    datum: App.Vector,
    e_u: App.Vector,
    e_v: App.Vector,
    u_min: float,
    u_max: float,
    v_min: float,
    v_max: float,
    thickness: float,
) -> Part.Shape:
    """A rectangular plate, given its EXTENTS rather than its centre.

    ``datum`` is the plate's ``(u, v) = (0, 0)`` point - which for an end
    plate is the top flange outer face on the web centreline, not the middle
    of the plate. Taking extents rather than a centre and a size is what
    keeps that datum meaningful: an extended-below-only plate is
    ``v_min = -(dv + ext_bot), v_max = 0`` and nothing has to remember to
    halve anything.

    The plate is extruded along ``e_u x e_v``, half each way, so its
    mid-surface is the u-v plane through the datum.
    """
    if u_max <= u_min or v_max <= v_min or thickness <= 0.0:
        raise DetailError(
            f"degenerate plate: u {u_min}..{u_max}, v {v_min}..{v_max}, "
            f"t {thickness}"
        )
    eu, ev = _u(e_u), _u(e_v)
    n = _u(eu.cross(ev))

    def pt(u: float, v: float) -> App.Vector:
        return datum + App.Vector(eu) * u + App.Vector(ev) * v

    corners = [
        pt(u_min, v_min), pt(u_max, v_min), pt(u_max, v_max), pt(u_min, v_max),
    ]
    corners.append(corners[0])
    # Built on the BACK face and extruded forward by the full thickness, so
    # the plate's mid-surface is the u-v plane through the datum.
    back = [c + App.Vector(n) * (-thickness / 2.0) for c in corners]
    # A WIRE extruded gives an open tube - four faces, no end caps, no
    # volume, and it exports as a surface. Extrude the FACE.
    solid = Part.Face(Part.makePolygon(back)).extrude(
        App.Vector(n) * thickness
    )
    if not solid.Solids:                                # pragma: no cover
        raise DetailError("the extruded plate is not a solid")
    return solid


def drill(
    shape: Part.Shape,
    centre: App.Vector,
    axis: App.Vector,
    diameter: float,
    depth: float,
) -> Part.Shape:
    """Cut one hole of ``diameter`` clean through, centred on ``centre``."""
    a = _u(axis)
    start = centre + App.Vector(a) * (-depth / 2.0)
    return shape.cut(Part.makeCylinder(diameter / 2.0, depth, start, a))


def _drill_all(shape, datum, e_u, e_v, holes, diameter, thickness):
    """Every hole in a layout, cut in the plate's own (u, v) frame."""
    eu, ev = _u(e_u), _u(e_v)
    n = _u(eu.cross(ev))
    depth = thickness + 4.0
    for u, v in holes:
        centre = datum + App.Vector(eu) * float(u) + App.Vector(ev) * float(v)
        shape = drill(shape, centre, n, diameter, depth)
    return shape


# ---------------------------------------------------------------------------
# Reading the geometry off the result
# ---------------------------------------------------------------------------


def geometry_of(connection) -> dict | None:
    """The shop geometry on one ``ConnectionDesign``, or ``None``.

    ``None`` covers three different situations and the caller must treat
    them the same way - skip and report - but they are not the same thing
    and the reason is passed back with them:

    * a result written before schema 1.2.0, which has no geometry at all;
    * a connection whose layout did not close, which says ``available``
      False and carries ``why``;
    * a connection type this adapter does not build.

    None of them is a case for drawing something approximate.
    """
    detail = getattr(connection, "detail", None) or {}
    geom = detail.get("geometry") or {}
    if not isinstance(geom, dict) or not geom:
        return None
    if geom.get("available") is not True:
        return None
    if not geom.get("holes_uv_mm") and geom.get("n_holes"):   # pragma: no cover
        return None
    return geom


def why_not(connection) -> str:
    """Why :func:`geometry_of` returned ``None``, in words for the report."""
    detail = getattr(connection, "detail", None) or {}
    geom = detail.get("geometry")
    if not geom:
        return (
            "this result carries no shop geometry - it predates schema "
            "1.2.0, so the connection is on the calculation sheet but "
            "there is nothing to build"
        )
    if isinstance(geom, dict) and geom.get("available") is not True:
        return str(geom.get("why", "the layout did not close"))
    return "no geometry"                                     # pragma: no cover


def _holes(geom) -> list[tuple[float, float]]:
    return [(float(u), float(v)) for u, v in geom.get("holes_uv_mm", ())]


# ---------------------------------------------------------------------------
# One solid per connection type
# ---------------------------------------------------------------------------


def end_plate_solid(geom: dict, datum: App.Vector, e_u: App.Vector,
                    e_v: App.Vector) -> Part.Shape:
    """A bolted moment end plate, drilled.

    ``datum`` is the TOP FLANGE OUTER FACE on the web centreline, and ``e_v``
    points up. The plate runs from ``+ext_top`` down to
    ``-(depth_on_cut + ext_bot)`` - which is the engine's own datum, so a
    row offset can be checked against the flange it belongs to without any
    arithmetic in between.
    """
    w = float(geom["width_mm"])
    t = float(geom["thickness_mm"])
    top = float(geom["ext_top_mm"])
    bot = -(float(geom["depth_on_cut_mm"]) + float(geom["ext_bot_mm"]))
    shape = plate_solid(datum, e_u, e_v, -w / 2.0, +w / 2.0, bot, top, t)
    return _drill_all(shape, datum, e_u, e_v, _holes(geom),
                      float(geom["hole_diameter_mm"]), t)


def base_plate_solid(geom: dict, centre: App.Vector, e_u: App.Vector,
                     e_v: App.Vector) -> Part.Shape:
    """A base plate lying flat, drilled for its anchors.

    ``centre`` is the column centreline at the underside of the column, and
    the plate hangs BELOW it - a base plate the column stands on, not one
    the column passes through. ``e_u`` is across the frame (``B``) and
    ``e_v`` along the building (``N``).
    """
    B = float(geom["B_mm"])
    N = float(geom["N_mm"])
    t = float(geom["thickness_mm"])
    # plate_solid centres the thickness on the datum, so drop the datum by
    # half the plate to put the plate's TOP on the column's underside.
    n = _u(_u(e_u).cross(_u(e_v)))
    datum = centre + App.Vector(n) * (-t / 2.0)
    shape = plate_solid(datum, e_u, e_v, -B / 2.0, +B / 2.0, -N / 2.0, +N / 2.0, t)
    return _drill_all(shape, datum, e_u, e_v, _holes(geom),
                      float(geom["hole_diameter_mm"]), t)


def anchor_solids(geom: dict, centre: App.Vector, e_u: App.Vector,
                  e_v: App.Vector) -> Part.Shape | None:
    """The anchor rods of one base, as a single compound.

    Drawn on the NOMINAL shank diameter over the full embedment + grout +
    plate + projection stack, starting at the embedded end. The rods are
    what makes the base look like a base in the model; the tension check was
    done on the same diameter and the same ``hef``.
    """
    d = float(geom.get("anchor_diameter_mm", 0.0))
    L = float(geom.get("anchor_length_mm", 0.0))
    if d <= 0.0 or L <= 0.0:
        return None
    t = float(geom["thickness_mm"])
    emb = float(geom.get("anchor_embedment_mm", 0.0))
    grout = float(geom.get("grout_mm", 0.0))
    n = _u(_u(e_u).cross(_u(e_v)))          # points UP out of the plate
    # Bottom of the rod: embedment + grout + plate below the column underside.
    base = centre + App.Vector(n) * (-(emb + grout + t))
    rods = []
    for u, v in _holes(geom):
        p = (base + App.Vector(_u(e_u)) * float(u)
             + App.Vector(_u(e_v)) * float(v))
        rods.append(Part.makeCylinder(d / 2.0, L, p, App.Vector(n)))
    if not rods:                                        # pragma: no cover
        return None
    return Part.makeCompound(rods)


def cleat_solid(geom: dict, centre: App.Vector, e_u: App.Vector,
                e_v: App.Vector) -> Part.Shape:
    """A purlin cleat, centred on the purlin/rafter intersection."""
    w = float(geom["width_mm"])
    h = float(geom["height_mm"])
    t = float(geom["thickness_mm"])
    shape = plate_solid(centre, e_u, e_v, -w / 2.0, +w / 2.0, -h / 2.0, +h / 2.0, t)
    return _drill_all(shape, centre, e_u, e_v, _holes(geom),
                      float(geom["hole_diameter_mm"]), t)


def gusset_solid(geom: dict, node: App.Vector, along: App.Vector,
                 e_v: App.Vector) -> Part.Shape:
    """A bracing gusset at one brace end.

    ``along`` points from the node INTO the panel, along the brace, and the
    plate starts at the node and runs out along it - so the plate is where
    the brace is, not straddling the node with half of it outside the
    building.
    """
    w = float(geom["width_mm"])
    h = float(geom["height_mm"])
    t = float(geom["thickness_mm"])
    eu = _u(along)
    # The layout centres its holes on the plate, so the datum sits half a
    # plate along the brace from the node.
    datum = node + App.Vector(eu) * (w / 2.0)
    shape = plate_solid(datum, eu, e_v, -w / 2.0, +w / 2.0, -h / 2.0, +h / 2.0, t)
    return _drill_all(shape, datum, eu, e_v, _holes(geom),
                      float(geom["hole_diameter_mm"]), t)


# ---------------------------------------------------------------------------
# Mass, for checking
# ---------------------------------------------------------------------------


def mass_kg(shape: Part.Shape) -> float:
    """Mass of a built solid, kg, from its own volume.

    Used against the engine's ``mass_kg`` for the same plate. Two numbers
    from two different places agreeing is the only check that catches a
    plate built at the wrong size - COUNT IS NOT CORRECTNESS, and a
    correctly-counted set of wrong plates is exactly what a geometry bug
    produces.
    """
    return shape.Volume * STEEL_DENSITY / 1.0e9
