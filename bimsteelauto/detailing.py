"""
bimsteelauto.detailing
======================

SHOP GEOMETRY, as numbers.

The connection layer answers *is this plate thick enough*.  It does not
answer *what shape is it and where are the holes* - and those are two
different questions.  A 20 mm end plate passes its yield-line check without
anyone knowing whether it is 250 or 400 mm wide, or whether the outer bolt
row falls on the plate at all.  This module is the second question, and only
the second one: it takes a section and a set of shop decisions and returns
the OUTLINE and the HOLE POSITIONS, in millimetres, as plain numbers.

Why it lives in Layer 1
-----------------------
Because the numbers are engineering, and the solid is not.  ``pfo``,
``pfi`` and ``de`` are AISC DG 4 proportions that the connection design
already used; turning them into a plate height and a list of row offsets is
arithmetic on those same proportions, and it must produce the same answer
whether or not FreeCAD is installed.  So the arithmetic is here, it is
tested under pytest, it lands in ``design_result.json``, and the adapter
does nothing but extrude what it is given.

That is the architecture rule from the handover applied literally: when the
``DesignResult`` turns out not to be sufficient for the adapter, the missing
thing is ADDED TO THE DESIGN RESULT.  It is not obtained by handing the
adapter a second object.

Provenance
----------
The end-plate row layout is ported from ``peb_building_param_v8.end_plate``
(GUI-verified over many builds).  Two things came with it and both are load
bearing:

1. **Rows are laid out FROM THE FLANGES, not from the plate edges.**  One
   outer row beyond each extended flange, then ``rows_in`` rows working away
   from each flange inner face.  That is what makes the joint a moment
   connection: a bolt in line with the flange force, rather than the middle
   of the plate taking it in bending.

2. **A plate cut across an inclined member sees 1/cos of everything.**  The
   apex and eave plates are cut VERTICALLY across a sloping rafter, so a
   flange of thickness ``tf`` occupies ``tf / cos`` of the plate and the
   section of depth ``d`` occupies ``d / cos``.  Miss it and every bolt row
   sits a few millimetres off the flange it is there to pick up - which is
   precisely the row carrying the flange force.  A splice plate cut NORMAL
   to its own axis passes ``cos_cut = 1.0`` and has no 1/cos anywhere.

Units are the package convention: mm throughout, kN elsewhere.

What this module does NOT do
----------------------------
It does not design anything.  Nothing here chooses a thickness, a bolt size
or a weld.  Every one of those arrives as an argument, from the connection
design that already checked it.  If a value is not supplied, this module
raises - it does not default, because a defaulted edge distance is an
invented design.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

from .sections import WeldedI

__all__ = [
    "VERIFIED",
    "COVERAGE_GAPS",
    "DetailingError",
    "PlateLayout",
    "EndPlateLayout",
    "BasePlateLayout",
    "CleatLayout",
    "GussetLayout",
    "bolt_gauge",
    "end_plate_layout",
    "base_plate_layout",
    "cleat_layout",
    "gusset_layout",
]


class DetailingError(ValueError):
    """A layout could not be produced from these inputs."""


# ---------------------------------------------------------------------------
# Verification gate
# ---------------------------------------------------------------------------

#: This module is geometry, so "verified" means the numbers it returns are
#: reproduced by longhand arithmetic on the same inputs - an independent
#: derivation, not a re-run of this code.  ``tests/test_detailing.py`` does
#: that by hand for a real PEB rafter and checks the closure conditions that
#: a re-run could never catch.
#:
#: CLOSED 2026-09-03.
#:
#:     end_plate_row_positions   every row offset is reproduced longhand from
#:                               the flange faces, for a normal cut and for a
#:                               1:12 inclined cut, and the two differ by the
#:                               1/cos factor and by nothing else
#:     end_plate_rows_on_plate   a row that would fall off the extension, or
#:                               within half a hole diameter of the edge, is
#:                               DROPPED rather than drawn overhanging - and
#:                               the drop is reported, not silent
#:     bolt_gauge_symmetry       the gauge is symmetric about the web and its
#:                               span equals (n - 1) x pitch, checked against
#:                               a hand-written expected list
#:     anchor_grid_positions     the anchor grid is symmetric about the plate
#:                               centre and every anchor clears the plate edge
#:                               by the stated edge distance
VERIFIED: dict[str, bool] = {
    "end_plate_row_positions": True,
    "end_plate_rows_on_plate": True,
    "bolt_gauge_symmetry": True,
    "anchor_grid_positions": True,
}

COVERAGE_GAPS: tuple[str, ...] = (
    "Welds are not laid out. Plate outlines and hole positions are "
    "produced; weld size, type and extent are not, so a fabrication "
    "drawing made from this geometry still needs its welding specified.",
    "Stiffeners are not laid out. AISC J10 column-side stiffeners and "
    "doublers are not invoked by the connection bridge, so no stiffener "
    "geometry is generated for a joint that may need one.",
    "The end plate outline is a plain rectangle. Clipped corners, "
    "haunch-following edges and shaped extensions are shop preferences "
    "that no input here carries.",
    "Bolt SOLIDS are not generated - the holes are. A hole through the "
    "plate is what a drawing and an NC file need; the bolt, nut and washer "
    "stack is assembly information and is not modelled.",
)


# ---------------------------------------------------------------------------
# Shared shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlateLayout:
    """What every plate layout has in common.

    ``holes`` is the list of hole CENTRES in the plate's own 2-D coordinates,
    ``(u, v)`` in mm, with the origin where the concrete subclass says it is.
    Keeping them as centres rather than as a pattern description means the
    adapter cuts exactly what was laid out here, and a test can check a hole
    against the flange it belongs to.
    """

    kind: str
    width: float                       # across, mm
    height: float                      # up, mm
    thickness: float                   # mm
    hole_diameter: float               # mm
    holes: tuple[tuple[float, float], ...] = ()
    notes: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("width", "height", "thickness", "hole_diameter"):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and v > 0.0):
                raise DetailingError(
                    f"{self.kind}: {name} must be a positive number, got {v!r}"
                )

    @property
    def n_holes(self) -> int:
        return len(self.holes)

    @property
    def area_mm2(self) -> float:
        """Plate area net of the holes - the area that becomes steel."""
        gross = self.width * self.height
        return gross - self.n_holes * math.pi * (self.hole_diameter / 2.0) ** 2

    def mass_kg(self, density: float = 7850.0) -> float:
        """Mass of one plate, kg, net of its holes."""
        return self.area_mm2 * self.thickness * density / 1.0e9

    def to_dict(self) -> dict[str, Any]:
        """A flat, JSON-safe dict - this is what crosses the layer boundary.

        Tuples become lists and every value is a float, an int, a string or
        a list of those, because the far side of the boundary is
        ``json.load`` inside FreeCAD and nothing else.
        """
        out: dict[str, Any] = {
            "kind": self.kind,
            "width_mm": round(self.width, 3),
            "height_mm": round(self.height, 3),
            "thickness_mm": round(self.thickness, 3),
            "hole_diameter_mm": round(self.hole_diameter, 3),
            "holes_uv_mm": [[round(u, 3), round(v, 3)] for u, v in self.holes],
            "n_holes": self.n_holes,
            "mass_kg": round(self.mass_kg(), 4),
            "notes": list(self.notes),
        }
        if self.dropped:
            out["dropped"] = list(self.dropped)
        return out


# ---------------------------------------------------------------------------
# Bolt set-out
# ---------------------------------------------------------------------------


def bolt_gauge(n: int, pitch: float, width: float | None = None) -> tuple[float, ...]:
    """``n`` bolts in a row at ``pitch`` centres, centred on zero.

    Ported from ``peb_building_param_v8.gauge_x``.  ``width`` is accepted so
    the caller can be told when the row does not fit on the plate; it is not
    used to move the bolts, because moving them silently is how a bolt ends
    up outside the steel it is supposed to clamp.
    """
    n = max(int(n), 1)
    if pitch <= 0.0 and n > 1:
        raise DetailingError(f"a {n}-bolt row needs a positive pitch")
    if n == 1:
        return (0.0,)
    span = (n - 1) * float(pitch)
    if width is not None and span >= float(width):
        raise DetailingError(
            f"{n} bolts at {pitch:g} mm span {span:g} mm, which does not fit "
            f"on a {width:g} mm plate"
        )
    start = -span / 2.0
    return tuple(round(start + k * float(pitch), 6) for k in range(n))


# ---------------------------------------------------------------------------
# End plate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EndPlateLayout(PlateLayout):
    """A bolted moment end plate.

    Plate coordinates: ``u`` runs across the flange width with ``u = 0`` on
    the web centreline, and ``v`` runs UP with ``v = 0`` on the TOP FLANGE
    OUTER FACE.  So the plate spans ``v = +ext_top`` down to
    ``v = -(dv + ext_bot)``, and a bolt row at offset ``a`` below the datum
    sits at ``v = -a``.

    That datum is the v8 one, and it is the useful one: every dimension a
    detailer checks - the outer row, the flange face, the extension - is
    measured from the flange, not from an edge that moves whenever the
    extension changes.
    """

    ext_top: float = 0.0
    ext_bot: float = 0.0
    depth_on_cut: float = 0.0          # dv - section depth in the cut plane
    tf_top_on_cut: float = 0.0
    tf_bot_on_cut: float = 0.0
    cos_cut: float = 1.0
    bolt_diameter: float = 0.0
    row_offsets: tuple[float, ...] = ()   # DOWN from the top-flange datum
    gauge: tuple[float, ...] = ()

    @property
    def n_rows(self) -> int:
        return len(self.row_offsets)

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out.update(
            ext_top_mm=round(self.ext_top, 3),
            ext_bot_mm=round(self.ext_bot, 3),
            depth_on_cut_mm=round(self.depth_on_cut, 3),
            tf_top_on_cut_mm=round(self.tf_top_on_cut, 3),
            tf_bot_on_cut_mm=round(self.tf_bot_on_cut, 3),
            cos_cut=round(self.cos_cut, 6),
            bolt_diameter_mm=round(self.bolt_diameter, 3),
            row_offsets_mm=[round(a, 3) for a in self.row_offsets],
            gauge_mm=[round(g, 3) for g in self.gauge],
            n_rows=self.n_rows,
        )
        return out


def end_plate_layout(
    section: WeldedI,
    *,
    thickness: float,
    bolt_diameter: float,
    width: float,
    gauge_g: float,
    e_out: float,
    e_in: float,
    de: float,
    rows_in: int = 1,
    pitch: float | None = None,
    cos_cut: float = 1.0,
    extended_top: bool = True,
    extended_bot: bool = True,
    hole_clearance: float = 2.0,
    n_per_row: int = 2,
) -> EndPlateLayout:
    """Lay out one moment end plate.

    Parameters mirror the AISC DG 4 proportions the connection design already
    used, so the plate that is drawn is the plate that was checked:

    ``e_out``
        ``pfo`` - flange OUTER face to the outer bolt row.
    ``e_in``
        ``pfi`` - flange INNER face to the first inner bolt row.
    ``de``
        edge distance from the outer bolt row to the plate edge, which is
        what fixes the extension: ``ext = e_out + de``.
    ``pitch``
        ``pb`` - row-to-row spacing, needed only when ``rows_in`` > 1.
    ``cos_cut``
        cosine of the angle between the plate normal and the member axis.
        1.0 for a plate cut normal to its member (a splice); ``cos(theta)``
        for a plate cut vertically across a rafter at slope ``theta``.

    A row that will not stay on the plate is DROPPED and named in
    ``dropped``, never drawn hanging over the edge.  That is the v8 rule,
    and it is the right one: a bolt half off the plate is not a detail
    anyone would fabricate, and a layout that silently produces one has told
    the detailer nothing.
    """
    if not isinstance(section, WeldedI):
        raise DetailingError("end_plate_layout needs a WeldedI section")
    if not (0.0 < cos_cut <= 1.0):
        raise DetailingError(f"cos_cut must be in (0, 1], got {cos_cut!r}")
    if rows_in < 1:
        raise DetailingError("an end plate needs at least one inner row")
    if rows_in > 1 and not (pitch and pitch > 0.0):
        raise DetailingError(
            f"{rows_in} inner rows need a positive row pitch (DG 4 pb)"
        )
    for name, v in (("thickness", thickness), ("bolt_diameter", bolt_diameter),
                    ("width", width), ("gauge_g", gauge_g), ("e_out", e_out),
                    ("e_in", e_in), ("de", de)):
        if not (isinstance(v, (int, float)) and v > 0.0):
            raise DetailingError(f"end plate: {name} must be positive, got {v!r}")

    # Everything the plate sees of an inclined member is stretched by 1/cos.
    dv = section.d / cos_cut
    tft_v = section.tf_top / cos_cut
    tfb_v = section.tf_bot / cos_cut

    ext_top = (e_out + de) if extended_top else 0.0
    ext_bot = (e_out + de) if extended_bot else 0.0
    d_hole = bolt_diameter + hole_clearance

    rows: list[float] = []
    dropped: list[str] = []

    # -- outer row above the top flange -------------------------------------
    if ext_top > 0.0:
        a = -e_out
        if a > -ext_top + 0.5 * d_hole:
            rows.append(a)
        else:
            dropped.append(
                f"outer row above the top flange (pfo = {e_out:g}) needs an "
                f"extension of at least {e_out + 0.5 * d_hole:g} mm to keep "
                f"the hole on the plate; the extension is {ext_top:g} mm"
            )

    # -- inner rows, working away from each flange inner face ---------------
    p = float(pitch or 0.0)
    for k in range(rows_in):
        rows.append(tft_v + e_in + k * p)
    for k in range(rows_in):
        rows.append(dv - tfb_v - e_in - k * p)

    # -- outer row below the bottom flange ----------------------------------
    if ext_bot > 0.0:
        a = dv + e_out
        if a < dv + ext_bot - 0.5 * d_hole:
            rows.append(a)
        else:
            dropped.append(
                f"outer row below the bottom flange (pfo = {e_out:g}) needs "
                f"an extension of at least {e_out + 0.5 * d_hole:g} mm; the "
                f"extension is {ext_bot:g} mm"
            )

    lo, hi = -ext_top, dv + ext_bot
    kept = sorted({round(r, 3) for r in rows if lo < r < hi})
    off_plate = sorted({round(r, 3) for r in rows})
    for r in off_plate:
        if r not in kept:
            dropped.append(
                f"row at {r:g} mm below the top flange falls outside the "
                f"plate, which runs {lo:g} to {hi:g}"
            )
    if not kept:
        raise DetailingError(
            "no bolt row stays on this end plate - check pfo, pfi and the "
            "extension against the section depth"
        )

    gauge = bolt_gauge(n_per_row, gauge_g, width)
    height = ext_top + dv + ext_bot
    holes = tuple(
        (u, -a) for a in kept for u in gauge
    )

    notes = [
        "Rows laid out FROM THE FLANGES per AISC DG 4: one outer row in each "
        "extension, then the inner rows away from each flange inner face.",
    ]
    if cos_cut < 1.0:
        theta = math.degrees(math.acos(cos_cut))
        notes.append(
            f"Plate cut across a member inclined at {theta:.2f} deg, so the "
            f"section occupies d/cos = {dv:.1f} mm of plate and each flange "
            f"tf/cos ({tft_v:.1f} / {tfb_v:.1f} mm)."
        )
    else:
        notes.append(
            "Plate cut normal to the member axis, so there is no 1/cos "
            "correction anywhere in this layout."
        )

    return EndPlateLayout(
        kind="end_plate",
        width=float(width),
        height=height,
        thickness=float(thickness),
        hole_diameter=d_hole,
        holes=holes,
        notes=tuple(notes),
        dropped=tuple(dropped),
        ext_top=ext_top,
        ext_bot=ext_bot,
        depth_on_cut=dv,
        tf_top_on_cut=tft_v,
        tf_bot_on_cut=tfb_v,
        cos_cut=float(cos_cut),
        bolt_diameter=float(bolt_diameter),
        row_offsets=tuple(kept),
        gauge=gauge,
    )


# ---------------------------------------------------------------------------
# Base plate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BasePlateLayout(PlateLayout):
    """A column base plate and its anchor grid.

    Plate coordinates: ``u`` across the frame (the plate's ``B``), ``v``
    along the building (the plate's ``N``), origin at the plate centre,
    which is the column centreline.  The plate itself lies in the horizontal
    plane; ``height`` is therefore ``N``, not a vertical dimension, and the
    adapter lays it flat.
    """

    N: float = 0.0
    B: float = 0.0
    anchor_diameter: float = 0.0
    anchor_embedment: float = 0.0
    anchor_projection: float = 0.0
    grout: float = 0.0
    n_x: int = 0
    n_y: int = 0
    edge_x: float = 0.0
    edge_y: float = 0.0

    @property
    def n_anchors(self) -> int:
        return self.n_x * self.n_y

    @property
    def anchor_length(self) -> float:
        """Overall anchor length: embedment + grout + plate + projection."""
        return (
            self.anchor_embedment + self.grout + self.thickness
            + self.anchor_projection
        )

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out.update(
            N_mm=round(self.N, 3),
            B_mm=round(self.B, 3),
            anchor_diameter_mm=round(self.anchor_diameter, 3),
            anchor_embedment_mm=round(self.anchor_embedment, 3),
            anchor_projection_mm=round(self.anchor_projection, 3),
            anchor_length_mm=round(self.anchor_length, 3),
            grout_mm=round(self.grout, 3),
            n_x=self.n_x,
            n_y=self.n_y,
            n_anchors=self.n_anchors,
            edge_x_mm=round(self.edge_x, 3),
            edge_y_mm=round(self.edge_y, 3),
        )
        return out


def base_plate_layout(
    *,
    N: float,
    B: float,
    thickness: float,
    anchor_diameter: float,
    n_x: int,
    n_y: int,
    s_x: float,
    s_y: float,
    anchor_embedment: float,
    anchor_projection: float = 75.0,
    grout: float = 25.0,
    hole_clearance: float = 4.0,
) -> BasePlateLayout:
    """Lay out one base plate and its anchors.

    ``B`` is across the frame and ``N`` along the building, matching
    ``ConnectionDetailing`` and ``BasePlateGeometry``, so the plate drawn is
    the plate the bearing check was done on.

    The anchor holes are ``anchor_diameter + hole_clearance`` - 4 mm by
    default, which is the v8 value.  An anchor hole is normally oversized
    well beyond a bolt hole to give the setting-out tolerance a cast-in
    anchor needs; 4 mm is the DRAWN clearance, not a licence to move the
    anchor, and the check was done on the anchor position, not the hole.
    """
    for name, v in (("N", N), ("B", B), ("thickness", thickness),
                    ("anchor_diameter", anchor_diameter),
                    ("anchor_embedment", anchor_embedment)):
        if not (isinstance(v, (int, float)) and v > 0.0):
            raise DetailingError(f"base plate: {name} must be positive, got {v!r}")
    if n_x < 1 or n_y < 1:
        raise DetailingError("a base plate needs at least one anchor each way")

    xs = bolt_gauge(n_x, s_x, B)
    ys = bolt_gauge(n_y, s_y, N)
    holes = tuple((x, y) for y in ys for x in xs)

    edge_x = (B - (n_x - 1) * float(s_x or 0.0)) / 2.0
    edge_y = (N - (n_y - 1) * float(s_y or 0.0)) / 2.0
    notes = [
        f"{n_x} x {n_y} anchor grid at {s_x:g} x {s_y:g} mm centres, "
        f"symmetric about the column centreline.",
        f"Edge distance on the plate: {edge_x:.0f} mm across the frame, "
        f"{edge_y:.0f} mm along it.",
        "Anchor length is embedment + grout + plate + projection; the "
        "embedment is the value the ACI 318 anchor check used.",
    ]
    dropped: list[str] = []
    min_edge = anchor_diameter + hole_clearance
    if edge_x < min_edge or edge_y < min_edge:
        dropped.append(
            f"an anchor sits {min(edge_x, edge_y):.0f} mm from the plate "
            f"edge, which is less than one hole diameter "
            f"({min_edge:.0f} mm) - the plate is too small for this grid"
        )

    return BasePlateLayout(
        kind="base_plate",
        width=float(B),
        height=float(N),
        thickness=float(thickness),
        hole_diameter=float(anchor_diameter) + float(hole_clearance),
        holes=holes,
        notes=tuple(notes),
        dropped=tuple(dropped),
        N=float(N),
        B=float(B),
        anchor_diameter=float(anchor_diameter),
        anchor_embedment=float(anchor_embedment),
        anchor_projection=float(anchor_projection),
        grout=float(grout),
        n_x=int(n_x),
        n_y=int(n_y),
        edge_x=edge_x,
        edge_y=edge_y,
    )


# ---------------------------------------------------------------------------
# Purlin cleat
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CleatLayout(PlateLayout):
    """A bolted purlin cleat - the plate welded to the rafter top flange.

    Plate coordinates: ``u`` along the building, ``v`` UP THE PURLIN WEB,
    origin at the cleat centre.  The bolt line runs up the web, which is
    why ``pitch`` and ``edge_end`` set the HEIGHT and not the width.
    """

    bolt_diameter: float = 0.0
    n_bolts: int = 0
    pitch: float = 0.0
    edge_end: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out.update(
            bolt_diameter_mm=round(self.bolt_diameter, 3),
            n_bolts=self.n_bolts,
            pitch_mm=round(self.pitch, 3),
            edge_end_mm=round(self.edge_end, 3),
        )
        return out


def cleat_layout(
    *,
    thickness: float,
    bolt_diameter: float,
    n_bolts: int,
    pitch: float,
    edge_end: float,
    purlin_depth: float,
    hole_clearance: float = 2.0,
) -> CleatLayout:
    """Lay out one purlin cleat from the values its check was run on.

    The bolt line runs UP the purlin web, so ``pitch`` and ``edge_end`` set
    the plate HEIGHT.  Both are the values handed to ``check_purlin_cleat``,
    so the cleat drawn is the cleat checked.

    The plate WIDTH - across the bolt line, along the building - has no
    input: nothing in the cleat check needs it.  Rather than invent one it
    is taken as twice the checked end distance, which is the same edge
    distance applied the other way, and the note says so.  ``purlin_depth``
    is a CHECK, not a dimension source: a bolt line taller than the web it
    bolts to is reported.
    """
    for name, v in (("thickness", thickness), ("bolt_diameter", bolt_diameter),
                    ("pitch", pitch), ("edge_end", edge_end),
                    ("purlin_depth", purlin_depth)):
        if not (isinstance(v, (int, float)) and v > 0.0):
            raise DetailingError(f"cleat: {name} must be positive, got {v!r}")
    if n_bolts < 1:
        raise DetailingError("a cleat needs at least one bolt")

    vs = bolt_gauge(n_bolts, pitch)
    height = (n_bolts - 1) * float(pitch) + 2.0 * float(edge_end)
    width = 2.0 * float(edge_end)
    holes = tuple((0.0, v) for v in vs)

    dropped: list[str] = []
    if height > float(purlin_depth):
        dropped.append(
            f"the bolt line stands {height:g} mm tall on a "
            f"{purlin_depth:g} mm purlin web - the cleat does not fit the "
            f"purlin it bolts to"
        )

    return CleatLayout(
        kind="purlin_cleat",
        width=width,
        height=height,
        thickness=float(thickness),
        hole_diameter=float(bolt_diameter) + float(hole_clearance),
        holes=holes,
        notes=(
            f"{n_bolts} x M{bolt_diameter:g} at {pitch:g} mm pitch with "
            f"{edge_end:g} mm end distance - the values the cleat check ran "
            f"on.",
            f"Plate width taken as 2 x the checked end distance "
            f"({2.0 * float(edge_end):g} mm): the cleat check needs no width, "
            f"so the same edge distance is applied across the bolt line "
            f"rather than a width being invented.",
            f"Bolt line {height:g} mm on a {purlin_depth:g} mm purlin web.",
            "The cleat is drawn as a flat plate. The bend, the weld to the "
            "rafter flange and any packing are not laid out.",
        ),
        dropped=tuple(dropped),
        bolt_diameter=float(bolt_diameter),
        n_bolts=int(n_bolts),
        pitch=float(pitch),
        edge_end=float(edge_end),
    )


# ---------------------------------------------------------------------------
# Bracing gusset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GussetLayout(PlateLayout):
    """A bracing gusset - drawn on the connection length it was checked on.

    Plate coordinates: ``u`` along the brace line, ``v`` across it, origin at
    the plate centre.
    """

    bolt_diameter: float = 0.0
    n_bolts: int = 0
    l_connection: float = 0.0
    w_first_row: float = 0.0
    whitmore_width: float | None = None

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out.update(
            bolt_diameter_mm=round(self.bolt_diameter, 3),
            n_bolts=self.n_bolts,
            l_connection_mm=round(self.l_connection, 3),
            w_first_row_mm=round(self.w_first_row, 3),
        )
        if self.whitmore_width is not None:
            out["whitmore_width_mm"] = round(self.whitmore_width, 3)
        return out


def gusset_layout(
    *,
    thickness: float,
    bolt_diameter: float,
    n_bolts: int,
    l_connection: float,
    w_first_row: float,
    b_available: float,
    whitmore_width: float | None = None,
    hole_clearance: float = 2.0,
) -> GussetLayout:
    """Lay out one bracing gusset from the values its check was run on.

    ``l_connection`` is the bolt line length and ``w_first_row`` the distance
    from the plate edge to the first bolt - the two dimensions the block
    shear and Whitmore checks used.  The bolts are spread evenly over
    ``l_connection``, which is the arrangement those checks assume.
    """
    for name, v in (("thickness", thickness), ("bolt_diameter", bolt_diameter),
                    ("l_connection", l_connection),
                    ("w_first_row", w_first_row),
                    ("b_available", b_available)):
        if not (isinstance(v, (int, float)) and v > 0.0):
            raise DetailingError(f"gusset: {name} must be positive, got {v!r}")
    if n_bolts < 2:
        raise DetailingError(
            "a gusset laid out on l_connection needs at least two bolts"
        )

    pitch = float(l_connection) / (n_bolts - 1)
    us = bolt_gauge(n_bolts, pitch)
    width = float(l_connection) + 2.0 * float(w_first_row)
    height = float(b_available)
    holes = tuple((u, 0.0) for u in us)

    notes = [
        f"{n_bolts} x M{bolt_diameter:g} spread over the {l_connection:g} mm "
        f"connection length at {pitch:.0f} mm pitch, with {w_first_row:g} mm "
        f"to the first row - the arrangement the block shear and Whitmore "
        f"checks assumed.",
        "The gusset is drawn as its available rectangle. The cut corners "
        "that make it a gusset are a shop preference no input here carries.",
    ]
    if whitmore_width is not None:
        notes.append(
            f"Whitmore width {whitmore_width:.0f} mm - the effective width "
            f"the tension and buckling checks used."
        )

    return GussetLayout(
        kind="bracing_gusset",
        width=width,
        height=height,
        thickness=float(thickness),
        hole_diameter=float(bolt_diameter) + float(hole_clearance),
        holes=holes,
        notes=tuple(notes),
        bolt_diameter=float(bolt_diameter),
        n_bolts=int(n_bolts),
        l_connection=float(l_connection),
        w_first_row=float(w_first_row),
        whitmore_width=(
            float(whitmore_width) if whitmore_width is not None else None
        ),
    )
