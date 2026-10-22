"""
bimsteelauto.loads.cladding
===========================

ASCE 7-16 Chapter 30 Part 1 - components and cladding for low-rise
buildings.  Purlins, girts and sheeting.

Why this is a separate module from ``loads.wind``
-------------------------------------------------
Chapter 28 sizes the FRAME and Chapter 30 sizes the things hanging off it,
and they are not the same load.  A purlin is a component: it sees a small
patch of roof for a short time, so its coefficients are much larger than the
frame's whole-building averages.  On this package's demonstration building
the worst MWFRS roof suction is ``GCpf = -1.07`` while the worst C&C roof
suction on a purlin is ``GCp = -2.3`` in the edge zone - more than twice as
onerous.

Designing a purlin on MWFRS pressures is therefore unconservative, and it is
unconservative in the direction nobody notices, because every number on the
sheet still looks reasonable.  That was a declared coverage gap in
``loads.wind`` until this module existed.

The two things that make C&C different
--------------------------------------
**Effective wind area.**  ``GCp`` falls as the loaded area grows, because a
gust does not peak everywhere at once.  ASCE 7-16 Sect. 26.2 defines the
effective wind area as the span times an effective width *that need not be
taken as less than one third of the span* - so a purlin at 1.5 m centres
spanning 7.5 m is designed on 7.5 x 2.5 m, not 7.5 x 1.5 m.  Using the
tributary width alone would overstate ``GCp`` and oversize every purlin in
the building.

**Zones.**  Corners are worse than edges, edges are worse than the field.
Fig. 30.3-2A splits a low-slope roof into 3 (corner), 2 (edge), 1 and 1'
(interior), and Fig. 30.3-1 splits the walls into 5 (corner) and 4
(interior).  Which zone a member sits in is a GEOMETRY decision this module
does not make - see :data:`COVERAGE_GAPS`.

Sign
----
``p = qh [ (GCp) - (GCpi) ]``, Eq. 30.3-1 - the same form as Chapter 28's
Eq. 28.3-1, and the same trap: the POSITIVE internal pressure case makes a
suction worse.  Each zone therefore yields two pressures, an inward one and
an outward one, and a purlin has to be checked for both.

Verification
------------
Struware CS2024, ASCE 7-16, at theta = 1.2 deg prints the whole C&C table -
both figures, every zone, eight effective wind areas.  All 32 roof pressures
and all 24 wall values reproduce, and its positive-roof row visibly hits the
Sect. 30.2.2 minimum, which pins that clause too.  See
``tests/test_cladding_verification.py``.

Units: kN, m, m^2, kN/m^2 (= kPa).  The figures are tabulated against area
in square FEET; the breakpoints below are converted once, exactly, and
nothing downstream sees an imperial number.  Log interpolation is unaffected
by the unit anyway, since only the ratio of two areas enters it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..spec import PEBGeometry
from .gravity import LoadError
from .wind import WindResult

__all__ = [
    "VERIFIED",
    "COVERAGE_GAPS",
    "MIN_CC_PRESSURE",
    "MAX_SLOPE_FIG_30_3_2A",
    "CCZone",
    "GCP_WALL",
    "GCP_ROOF_LOW_SLOPE",
    "CladdingPressure",
    "effective_wind_area",
    "gcp",
    "cladding_pressure",
    "governing_pressure",
    "purlin_pressures",
    "girt_pressures",
]

#: CLOSED 2026-09-02 against Struware CS2024, an independent ASCE 7-16
#: implementation, at theta = 1.2 deg.  Fixture:
#: ``tests/fixtures/struware_asce716_cladding.json``.
#:
#:   gcp_wall_table
#:       Fig. 30.3-1.  Struware prints (GCp - GCpi) at 10, 100, 200 and 500
#:       ft^2 for zone 4, zone 5 and the positive case - twelve values, all
#:       of which reproduce to the printed two decimals, and the twelve
#:       pressures they produce reproduce to 0.1 psf.  That pins the
#:       endpoints (-1.1 and -1.4 at 10 ft^2, both -0.8 at 500; +1.0 to
#:       +0.7) AND the log interpolation between them, since 100 and 200
#:       ft^2 are interior points.
#:   gcp_roof_table
#:       Fig. 30.3-2A, theta <= 7 deg.  Struware prints GCp at four areas
#:       and the resulting pressure at EIGHT, for zones 1', 1, 2, 3 and the
#:       positive case: 32 pressures, every one reproduced.  Zone 1' is the
#:       one worth naming - it is flat to 100 ft^2 and then falls, so a
#:       single straight interpolation would miss it, and the 500 and 1000
#:       ft^2 columns are what show that.
#:   effective_wind_area
#:       Sect. 26.2's "need not be less than one third the span" is
#:       arithmetic, and is pinned by hand for both branches: a purlin
#:       whose spacing is below span/3 (the usual case, where the rule
#:       bites) and one above it (where it does not).
#:   log_interpolation
#:       Exercised at every interior column of both figures above.  A
#:       LINEAR interpolation on area instead of on its logarithm would
#:       give -1.16 rather than -1.29 for roof zone 1 at 100 ft^2, which
#:       the printed table rejects.
#:   theta_10_deg_wall_reduction
#:       Fig. 30.3-1's footnote reduces wall GCp by 10% when theta <= 10
#:       deg.  Struware prints the note AND applies it, and the wall values
#:       only reproduce with the factor in - without it every wall number
#:       is 10% out, in the conservative direction, which is exactly the
#:       kind of error that survives a casual review.
#:   minimum_cc_pressure
#:       Sect. 30.2.2's 16 psf net floor.  Struware's positive-roof row
#:       reads 17.2, 16.1, 16.0, 16.0, 16.0 ... across the eight areas: the
#:       first two are the real computed values and the rest are the floor,
#:       visible in its own printed output.  A negative control comes free
#:       - the floor must NOT bite on the suction rows, and it does not.
VERIFIED: dict[str, bool] = {
    "gcp_wall_table": True,
    "gcp_roof_table": True,
    "effective_wind_area": True,
    "log_interpolation": True,
    "theta_10_deg_wall_reduction": True,
    "minimum_cc_pressure": True,
}

#: Checks this module does NOT perform.
COVERAGE_GAPS: tuple[str, ...] = (
    "Which Chapter 30 zone a purlin or girt sits in is a GEOMETRY decision "
    "the caller must make. This module computes a pressure per zone and "
    "will return the governing one over a stated list of zones, but it does "
    "not lay the zones out on the building or work out which members cross "
    "them. Fig. 30.3-2A puts zone 3 in the corners, zone 2 in strips of "
    "width a along every edge and the ridge, zone 1 within 2a, and zone 1' "
    "in the field beyond.",
    "Only Fig. 30.3-2A is implemented, i.e. gable and hip roofs at "
    "theta <= 7 degrees. A steeper roof RAISES rather than extrapolating: "
    "Figs. 30.3-2B and 30.3-2C have different zone layouts as well as "
    "different coefficients, so there is nothing safe to extrapolate to.",
    "Roof overhangs (Fig. 30.3-2A's overhang rows) are NOT computed. An "
    "overhanging purlin sees pressure on its soffit as well as its top and "
    "is significantly worse than the same purlin inboard.",
    "Parapets (Sect. 30.9) and rooftop equipment (Sect. 30.10) are not "
    "covered. Sheeting and fastener pull-through/pull-over are not checked "
    "at all - this module sizes the purlin, not its fixings.",
    "The Chapter 30 pressures are used for the SECONDARY members only. The "
    "main frame stays on Chapter 28, which is correct - a frame is not a "
    "component - but it means two different wind derivations appear on one "
    "calculation sheet and a reader must not mix them.",
)

#: Exactly one pound-force per square foot, kN/m^2.  Both 1 lbf and 1 ft
#: are defined quantities, so this is exact.
_PSF: float = 4.4482216152605 / (0.3048 ** 2) / 1000.0

#: One square foot, in m^2.  Exact.
_SQFT: float = 0.3048 ** 2

#: ASCE 7-16 Sect. 30.2.2: the C&C design pressure shall not be less than a
#: net 16 lb/ft^2 acting in EITHER direction normal to the surface.
#:
#: The same 16 lb/ft^2 appears in Sect. 28.3.4 for the MWFRS, but it is a
#: different clause about a different thing, so it is written out again
#: here rather than imported.  A test pins that the two stay equal.
MIN_CC_PRESSURE: float = 16.0 * _PSF          # 0.766084 kN/m^2

#: Fig. 30.3-2A covers gable and hip roofs up to this slope.  Above it the
#: figure changes, and so does the zone layout.
MAX_SLOPE_FIG_30_3_2A: float = 7.0            # degrees

#: Fig. 30.3-1's footnote: wall GCp is reduced by 10% when theta <= 10 deg.
_WALL_REDUCTION_SLOPE: float = 10.0
_WALL_REDUCTION: float = 0.90


class CCZone:
    """Chapter 30 zone names.

    Roof zones are Fig. 30.3-2A's; wall zones are Fig. 30.3-1's.  The
    numbering is ASCE's own, kept verbatim so a reader can look it up.
    """

    ROOF_INTERIOR = "1'"      # the field, beyond 2a
    ROOF_1 = "1"              # interior, within 2a
    ROOF_EDGE = "2"
    ROOF_CORNER = "3"
    WALL_INTERIOR = "4"
    WALL_CORNER = "5"

    ROOF = ("1'", "1", "2", "3")
    WALL = ("4", "5")


#: ASCE 7-16 Fig. 30.3-1, walls of a building with h <= 60 ft.
#: ``(effective wind area m^2, GCp)`` breakpoints, log-linear between and
#: held flat outside.  Areas are 10 and 500 ft^2 converted exactly.
#:
#: The NEGATIVE entry is the suction curve for that zone; the POSITIVE
#: curve is shared by zones 4 and 5, which is why it is stored once.
GCP_WALL: dict[str, list[tuple[float, float]]] = {
    "4": [(10 * _SQFT, -1.1), (500 * _SQFT, -0.8)],
    "5": [(10 * _SQFT, -1.4), (500 * _SQFT, -0.8)],
    "+": [(10 * _SQFT, +1.0), (500 * _SQFT, +0.7)],
}

#: ASCE 7-16 Fig. 30.3-2A, gable and hip roofs with theta <= 7 deg.
#:
#: Zone 1' is the one with three breakpoints: it is FLAT at -0.9 out to
#: 100 ft^2 and only then falls away to -0.4 at 1000 ft^2.  Collapsing it
#: to a single straight line between its endpoints would understate the
#: suction on every purlin between those areas.
GCP_ROOF_LOW_SLOPE: dict[str, list[tuple[float, float]]] = {
    "1'": [(10 * _SQFT, -0.9), (100 * _SQFT, -0.9), (1000 * _SQFT, -0.4)],
    "1":  [(10 * _SQFT, -1.7), (500 * _SQFT, -1.0)],
    "2":  [(10 * _SQFT, -2.3), (500 * _SQFT, -1.4)],
    "3":  [(10 * _SQFT, -3.2), (500 * _SQFT, -1.4)],
    "+":  [(10 * _SQFT, +0.3), (100 * _SQFT, +0.2)],
}


# ---------------------------------------------------------------------------
# Effective wind area
# ---------------------------------------------------------------------------


def effective_wind_area(span: float, tributary_width: float) -> float:
    """``A`` per ASCE 7-16 Sect. 26.2, m^2.  Both inputs in mm.

    The span times an effective width that NEED NOT BE LESS THAN one third
    of the span.  For a purlin the effective width is normally the purlin
    spacing, and span/3 is normally larger - so the rule usually bites, and
    ignoring it makes every purlin in the building heavier than it needs to
    be.

    It is a floor on the AREA, never a cap: a widely spaced member keeps its
    real tributary width.
    """
    if span <= 0:
        raise LoadError(f"span must be positive, got {span}")
    if tributary_width <= 0:
        raise LoadError(
            f"tributary width must be positive, got {tributary_width}"
        )
    width = max(tributary_width, span / 3.0)
    return span * width / 1e6            # mm^2 -> m^2


def _log_interpolate(
    breakpoints: list[tuple[float, float]], area: float
) -> float:
    """Log-linear in area, held flat outside the tabulated range.

    ASCE's C&C figures are drawn on a logarithmic area axis, so a straight
    line on the figure is a straight line in ``log(A)``.  Interpolating
    linearly in ``A`` instead gives visibly different numbers - roof zone 1
    at 100 ft^2 comes out -1.16 rather than -1.29 - and the printed tables
    reject it.
    """
    if area <= breakpoints[0][0]:
        return breakpoints[0][1]
    if area >= breakpoints[-1][0]:
        return breakpoints[-1][1]
    for (a1, v1), (a2, v2) in zip(breakpoints, breakpoints[1:]):
        if a1 <= area <= a2:
            f = math.log10(area / a1) / math.log10(a2 / a1)
            return v1 + f * (v2 - v1)
    raise LoadError(f"area {area} fell through the breakpoints")  # unreachable


# ---------------------------------------------------------------------------
# GCp
# ---------------------------------------------------------------------------


def gcp(zone: str, area: float, *, theta_deg: float) -> tuple[float, float]:
    """``(negative, positive)`` GCp for one zone at one effective area.

    ``area`` in m^2, ``theta_deg`` the roof slope.

    Both signs are returned together because a component must be checked
    both ways: a girt is pushed in on the windward wall and sucked out on
    the leeward one, and the same girt has to survive both.
    """
    if area <= 0:
        raise LoadError(f"effective wind area must be positive, got {area}")
    if theta_deg < 0:
        raise LoadError(f"roof angle must not be negative, got {theta_deg}")

    if zone in CCZone.WALL:
        table = GCP_WALL
        # Fig. 30.3-1 footnote: wall GCp is reduced 10% on a shallow roof.
        factor = (
            _WALL_REDUCTION if theta_deg <= _WALL_REDUCTION_SLOPE else 1.0
        )
    elif zone in CCZone.ROOF:
        if theta_deg > MAX_SLOPE_FIG_30_3_2A:
            raise LoadError(
                f"roof slope {theta_deg:.2f} deg is above the "
                f"{MAX_SLOPE_FIG_30_3_2A:.0f} deg limit of Fig. 30.3-2A, "
                "which is the only C&C roof figure implemented. Figs. "
                "30.3-2B and 30.3-2C change the zone LAYOUT as well as the "
                "coefficients, so there is nothing safe to extrapolate to."
            )
        table = GCP_ROOF_LOW_SLOPE
        factor = 1.0
    else:
        raise LoadError(
            f"{zone!r} is not a Chapter 30 zone. Roof: {CCZone.ROOF}; "
            f"wall: {CCZone.WALL}"
        )

    negative = _log_interpolate(table[zone], area) * factor
    positive = _log_interpolate(table["+"], area) * factor
    return negative, positive


# ---------------------------------------------------------------------------
# Pressures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CladdingPressure:
    """One zone's C&C pressures, both directions.  kN/m^2.

    ``outward`` is negative (suction, lifting the sheeting off) and
    ``inward`` is positive (pressing on it).  Both are net of internal
    pressure and both already carry the Sect. 30.2.2 floor.
    """

    zone: str
    area: float                  # m^2, effective wind area
    gcp_negative: float
    gcp_positive: float
    inward: float                # kN/m^2, positive
    outward: float               # kN/m^2, negative
    qh: float
    GCpi: tuple[float, float]
    minimum_applied: tuple[bool, bool]      # (inward, outward)
    reference: str = "ASCE 7-16 Ch. 30 Part 1 (Eq. 30.3-1)"

    @property
    def uplift(self) -> float:
        """Magnitude of the suction, kN/m^2 - what sizes a purlin."""
        return abs(self.outward)


def _apply_minimum(p: float) -> tuple[float, bool]:
    """Sect. 30.2.2: not less than 16 psf net, in EITHER direction.

    "Either direction" is the part that matters.  The floor is applied to
    the magnitude and the sign is kept, so a small suction is raised to a
    16 psf suction rather than being turned into a pressure.
    """
    if abs(p) < MIN_CC_PRESSURE:
        return math.copysign(MIN_CC_PRESSURE, p if p != 0.0 else 1.0), True
    return p, False


def cladding_pressure(
    wind: WindResult, zone: str, area: float, *, theta_deg: float | None = None
) -> CladdingPressure:
    """Eq. 30.3-1 for one zone at one effective wind area.

    ``p = qh [ (GCp) - (GCpi) ]``.  The worst inward pressure pairs the
    positive ``GCp`` with the NEGATIVE ``GCpi`` (the interior sucking
    inward while the wind pushes), and the worst outward pressure pairs the
    negative ``GCp`` with the POSITIVE ``GCpi`` (the interior pushing out
    while the wind lifts).  Pairing them the other way round gives two
    numbers that are both too small and both look plausible.
    """
    theta = wind.theta_deg if theta_deg is None else theta_deg
    gcp_neg, gcp_pos = gcp(zone, area, theta_deg=theta)
    gcpi_pos, gcpi_neg = wind.GCpi

    inward, min_in = _apply_minimum(wind.qh * (gcp_pos - gcpi_neg))
    outward, min_out = _apply_minimum(wind.qh * (gcp_neg - gcpi_pos))

    return CladdingPressure(
        zone=zone,
        area=area,
        gcp_negative=gcp_neg,
        gcp_positive=gcp_pos,
        inward=inward,
        outward=outward,
        qh=wind.qh,
        GCpi=wind.GCpi,
        minimum_applied=(min_in, min_out),
    )


def governing_pressure(
    wind: WindResult,
    zones: tuple[str, ...],
    area: float,
    *,
    theta_deg: float | None = None,
) -> CladdingPressure:
    """The worst zone of a stated set, by suction.

    A purlin runs the length of the building and crosses several zones, so
    the caller states which ones it crosses and this returns the governing
    one whole - zone name included, so the sheet can say WHICH zone sized
    the member rather than only what the number was.

    Suction governs the choice because that is what sizes a purlin: under
    uplift its bottom flange is in compression and unbraced.  The inward
    pressure of the returned zone is carried along with it.
    """
    if not zones:
        raise LoadError("no zones were given to choose between")
    results = [
        cladding_pressure(wind, z, area, theta_deg=theta_deg) for z in zones
    ]
    return min(results, key=lambda r: r.outward)


def purlin_pressures(
    geometry: PEBGeometry,
    wind: WindResult,
    *,
    span: float | None = None,
    zones: tuple[str, ...] = (CCZone.ROOF_EDGE,),
) -> CladdingPressure:
    """C&C pressure on a purlin line.

    ``span`` defaults to the LARGEST bay, which is the one that governs on
    a non-uniform bay layout - and it is also the one that gives the
    largest effective wind area, hence the SMALLEST ``GCp``.  Those two
    pull opposite ways, so the governing bay is not obvious and is worth
    stating rather than assuming; the default takes the largest span
    because the bending moment grows with span squared and beats the mild
    reduction in GCp.

    ``zones`` defaults to the edge zone (2), which is where most of a PEB's
    purlins actually are once the eave and ridge strips are counted.  It is
    a default the caller should override with the real zone layout - see
    :data:`COVERAGE_GAPS`.
    """
    L = max(geometry.bay_spacings) if span is None else span
    area = effective_wind_area(L, geometry.purlin_spacing)
    return governing_pressure(wind, zones, area)


def girt_pressures(
    geometry: PEBGeometry,
    wind: WindResult,
    *,
    span: float | None = None,
    zones: tuple[str, ...] = (CCZone.WALL_INTERIOR,),
) -> CladdingPressure:
    """C&C pressure on a girt line.

    Walls have no slope of their own, so the Fig. 30.3-1 footnote is driven
    by the ROOF angle - a shallow roof reduces the wall coefficients by
    10%.  That is ASCE's rule and it is easy to miss, because nothing about
    a wall suggests its coefficients should depend on the roof.
    """
    L = max(geometry.bay_spacings) if span is None else span
    area = effective_wind_area(L, geometry.girt_spacing)
    return governing_pressure(wind, zones, area)
