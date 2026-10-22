"""
bimsteelauto.analysis.bracing
=============================

The longitudinal system: what carries wind along the building.

The frame analysis in :mod:`bimsteelauto.analysis.frame` is a plane frame -
it resists wind ACROSS the building and nothing else.  Wind on the endwall
acts along the ridge, and the rigid frames have almost no stiffness in that
direction.  It is carried instead by a load path made of four pieces:

    endwall posts  ->  eave strut  ->  roof bracing  ->  wall bracing  ->  base

Miss any one of them and the building is a row of playing cards.  This
module works out the force in each, from closed-form statics that a
reviewer can redo on paper - no solver, because a determinate tension-only
X-braced bay does not need one, and a hand-checkable answer is worth more
here than a general one.

What is assumed, and what is not
--------------------------------
X-bracing is taken as TENSION-ONLY: the compression diagonal is assumed to
have buckled and to carry nothing, so the tension diagonal takes the whole
panel shear.  That is the standard assumption for rod and small-angle
bracing and it is conservative for the diagonal.  It is NOT conservative for
the strut, which then carries the full horizontal component - so the strut
force is computed from the same assumption rather than halved.

Wind pressure on the endwall is an INPUT.  This module does not derive it;
it comes from the wind module, which knows the geometry and the code.

Units: kN, mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..spec import PEBGeometry

__all__ = [
    "VERIFIED",
    "BracingError",
    "BracingPlane",
    "BracedBay",
    "LongitudinalSystem",
    "BraceForces",
    "endwall_force",
    "solve_longitudinal",
]


class BracingError(ValueError):
    """An impossible bracing arrangement."""


#: CLOSED 2026-09-01.  Every relation here is closed-form, so the reference
#: is arithmetic written out longhand in the test - the strongest check
#: available for a determinate system, and the reason this module was
#: written without a solver.
#:
#:     endwall_tributary    30 x 7 rectangle + 30 x 1.5 gable = 232.5 m2 by
#:                          hand; the gable's share is shown to arrive
#:                          wholly at eave level, because there is no
#:                          structure beneath it to take half
#:     bay_distribution     each braced bay takes F_eave / n, and doubling
#:                          the braced bays halves the share
#:     diagonal_resolution  T = V x L_diag / width, checked against hypot;
#:                          the 1/cos amplification of a 6 m bay against a
#:                          15 m panel is pinned at 2.693
#:     strut_accumulation   end bays braced -> strut carries nothing; braced
#:                          inboard -> every bay outside the braced pair
#:                          carries F_eave/2, and the force is shown NOT to
#:                          be uniform along the building
VERIFIED: dict[str, bool] = {
    "endwall_tributary": True,
    "bay_distribution": True,
    "diagonal_resolution": True,
    "strut_accumulation": True,
}


class BracingPlane(str, Enum):
    ROOF = "roof"
    SIDEWALL = "sidewall"


@dataclass(frozen=True)
class BracedBay:
    """One X-braced panel.

    ``width`` is along the building (the bay spacing); ``depth`` is across
    the panel - the eave height for a sidewall bay, or the distance between
    the two braced frame lines' eave struts for a roof bay.
    """

    index: int                 # which bay, 0-based from the left
    plane: BracingPlane
    width: float               # mm
    depth: float               # mm

    def __post_init__(self) -> None:
        if self.width <= 0 or self.depth <= 0:
            raise BracingError(
                f"bay {self.index}: width and depth must be positive"
            )

    @property
    def diagonal_length(self) -> float:
        return math.hypot(self.width, self.depth)

    @property
    def angle(self) -> float:
        """Angle of the diagonal from the horizontal, radians."""
        return math.atan2(self.depth, self.width)

    @property
    def cos_theta(self) -> float:
        """Horizontal component per unit diagonal force."""
        return self.width / self.diagonal_length

    def diagonal_force(self, panel_shear: float) -> float:
        """Tension in the working diagonal, kN.

        Tension-only: the whole panel shear goes into one diagonal, so
        ``T = V / cos(theta)``.  A shallow bay - a wide bay spacing against a
        low eave - gives a small angle and a large force; at 15 degrees the
        diagonal carries 3.9 times the panel shear.
        """
        return abs(panel_shear) / self.cos_theta


@dataclass(frozen=True)
class LongitudinalSystem:
    """Where the bracing actually is.

    ``braced_bays`` are the bay indices that carry bracing, counted from the
    left.  A PEB normally braces the end bays and then every fourth or fifth
    bay; leaving the list empty is not a design, it is a missing one, and
    :func:`solve_longitudinal` refuses it.
    """

    geometry: PEBGeometry
    braced_bays: tuple[int, ...]
    #: Number of sidewalls that carry wall bracing.  Two on a normal
    #: building; one where the other side is built against something.
    n_braced_walls: int = 2
    #: Depth of one roof bracing panel, mm - the dimension ACROSS the
    #: building.  Defaults to the full sloping rafter length, i.e. one X
    #: over the whole slope, which is the common PEB arrangement.
    #:
    #: It matters more than it looks.  The diagonal force is
    #: ``V / cos(theta)`` with ``cos(theta) = bay_spacing / diagonal``, so a
    #: 6 m bay against a 15 m slope gives cos = 0.37 and a diagonal carrying
    #: 2.7 times the panel shear.  Splitting the slope into two panels
    #: roughly halves that.  Stated, not assumed.
    roof_panel_depth: float | None = None

    def __post_init__(self) -> None:
        n = self.geometry.n_bays
        if not self.braced_bays:
            raise BracingError(
                "no braced bays were given. A building with no longitudinal "
                "bracing has no load path for wind on the endwall - this is "
                "a missing design, not a zero one."
            )
        bad = [b for b in self.braced_bays if not 0 <= b < n]
        if bad:
            raise BracingError(
                f"braced bay index/indices {bad} outside 0..{n - 1}"
            )
        if len(set(self.braced_bays)) != len(self.braced_bays):
            raise BracingError("a bay is listed as braced more than once")
        if self.n_braced_walls not in (1, 2):
            raise BracingError("n_braced_walls must be 1 or 2")
        if self.roof_panel_depth is not None and self.roof_panel_depth <= 0:
            raise BracingError("roof_panel_depth must be positive")

    @property
    def n_braced(self) -> int:
        return len(self.braced_bays)

    def roof_bay(self, index: int) -> BracedBay:
        g = self.geometry
        return BracedBay(
            index=index,
            plane=BracingPlane.ROOF,
            width=g.bay_spacings[index],
            depth=self.roof_panel_depth or g.rafter_length,
        )

    def wall_bay(self, index: int) -> BracedBay:
        return BracedBay(
            index=index,
            plane=BracingPlane.SIDEWALL,
            width=self.geometry.bay_spacings[index],
            depth=self.geometry.eave_height,
        )


# ---------------------------------------------------------------------------
# Forces
# ---------------------------------------------------------------------------


def endwall_force(
    geometry: PEBGeometry,
    pressure: float,
    *,
    eave_share: float = 0.5,
) -> tuple[float, float]:
    """Longitudinal wind on one endwall, split base / eave.  kN.

    Parameters
    ----------
    pressure:
        Net longitudinal wind pressure on the endwall, kN/m^2.  An input -
        this module does not know the code.
    eave_share:
        Fraction of the endwall area whose load is delivered at eave level
        rather than to the floor.  0.5 is the ordinary result of endwall
        posts spanning floor to eave: each support takes half.  Stated here
        rather than buried, because a post that is propped at mid-height
        changes it.

    Returns ``(F_eave, F_base)`` in kN.
    """
    if not 0.0 <= eave_share <= 1.0:
        raise BracingError(f"eave_share must lie in [0, 1], got {eave_share}")

    # Endwall area: the rectangle to the eave plus the gable triangle above.
    rect = geometry.width * geometry.eave_height
    gable = 0.5 * geometry.width * geometry.ridge_rise
    area = (rect + gable) * 1e-6          # mm^2 -> m^2
    total = pressure * area

    # The gable's load has nowhere below it to go, so all of it arrives at
    # eave level or above - it is not shared with the base.
    gable_force = pressure * gable * 1e-6
    rect_force = total - gable_force
    return rect_force * eave_share + gable_force, rect_force * (1.0 - eave_share)


@dataclass(frozen=True)
class BraceForces:
    """The longitudinal load path, resolved.  kN, tension positive."""

    F_eave: float                 # total longitudinal force at eave level
    F_base: float                 # delivered straight to the floor slab
    per_braced_bay: float         # share reaching one braced bay
    roof_diagonal: float          # tension in one roof bracing diagonal
    wall_diagonal: float          # tension in one wall bracing diagonal
    strut_forces: tuple[tuple[int, float], ...]   # (bay index, axial kN)
    roof_bay: BracedBay
    wall_bay: BracedBay

    @property
    def max_strut_force(self) -> float:
        return max((abs(f) for _, f in self.strut_forces), default=0.0)


def solve_longitudinal(
    system: LongitudinalSystem,
    pressure: float,
    *,
    eave_share: float = 0.5,
) -> BraceForces:
    """Resolve the longitudinal wind into brace and strut forces.

    The chain, in order:

    1. Wind on the endwall gives ``F_eave`` at the eave, the rest at the base.
    2. The eave strut drags that force along the building to the braced bays.
       Both endwalls are loaded, and the system is symmetric, so each braced
       bay carries an equal share - ``F_eave / n_braced``.
    3. In a braced bay the panel shear goes into one tension diagonal.
    4. The wall bracing below carries the same share down to the base, split
       between the sidewalls that are actually braced.
    """
    if pressure < 0:
        raise BracingError("pressure must be a positive magnitude")

    g = system.geometry
    F_eave, F_base = endwall_force(g, pressure, eave_share=eave_share)

    per_bay = F_eave / system.n_braced
    roof_bay = system.roof_bay(system.braced_bays[0])
    wall_bay = system.wall_bay(system.braced_bays[0])

    # The roof bracing on one slope carries half the eave-level force; the
    # two slopes act as a pair of horizontal trusses.
    roof_panel_shear = per_bay / 2.0
    roof_diag = roof_bay.diagonal_force(roof_panel_shear)

    # Wall bracing takes the bay's whole share, split between braced walls.
    wall_panel_shear = per_bay / system.n_braced_walls
    wall_diag = wall_bay.diagonal_force(wall_panel_shear)

    return BraceForces(
        F_eave=F_eave,
        F_base=F_base,
        per_braced_bay=per_bay,
        roof_diagonal=roof_diag,
        wall_diagonal=wall_diag,
        strut_forces=_strut_forces(system, F_eave),
        roof_bay=roof_bay,
        wall_bay=wall_bay,
    )


def _strut_forces(
    system: LongitudinalSystem, F_eave: float
) -> tuple[tuple[int, float], ...]:
    """Axial force in the eave strut, bay by bay.

    The strut is a drag member: it collects the endwall force and carries it
    to the nearest braced bay, accumulating along the way.  The force is
    NOT uniform - it is largest in the bay next to the endwall and falls to
    zero at a braced bay - and a design that uses one strut size for the
    whole building must be checked against the maximum, not the average.

    Sign: positive is compression, which is what the strut sees when the
    wind blows onto the endwall beside it.
    """
    n = system.geometry.n_bays
    braced = sorted(system.braced_bays)
    per_end = F_eave / 2.0            # one endwall's worth, each end

    out: list[tuple[int, float]] = []
    for bay in range(n):
        # Distance, in bays, to the nearest braced bay on either side.
        nearer = min(abs(bay - b) for b in braced)
        if nearer == 0:
            # A braced bay sheds its force straight into the diagonal; the
            # strut passes through it carrying nothing further.
            out.append((bay, 0.0))
            continue
        # Every bay between an endwall and the nearest braced bay carries the
        # whole end force; past the braced bay it has been shed.
        if bay < braced[0] or bay > braced[-1]:
            out.append((bay, per_end))
        else:
            # Between two braced bays the strut carries only what the
            # intermediate frames shed into it, which this module does not
            # model - reported as zero and named in the coverage gaps.
            out.append((bay, 0.0))
    return tuple(out)
