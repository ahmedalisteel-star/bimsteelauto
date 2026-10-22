"""
bimsteelauto.loads.wind
=======================

ASCE 7-16 Chapter 28 Part 1 - the envelope procedure for low-rise buildings.

Why Chapter 28 and not Chapter 27
---------------------------------
A PEB is a low-rise building (h <= 18 m and h <= least horizontal dimension),
which is exactly what the envelope procedure was written for.  Chapter 27's
directional procedure applies too, but the envelope method's ``GCpf`` zones
already contain the worst pressure any wind direction produces at each part
of the frame - which is what a frame designer needs, in one pass.

Nothing else in the toolkit had this.  The ``asce7-loads`` scripts implement
Chapter 27 only, and say so in their own docstring.

The five inputs
---------------
V, exposure, enclosure, Kzt and risk category.  Everything else on this page
is derived from the geometry.

Sign, and why it is easy to state backwards
-------------------------------------------
::

    p = qh [ (GCpf) - (GCpi) ]

``GCpf`` is negative over a roof in suction and ``GCpi`` is positive for a
PRESSURISED interior.  Subtracting a positive internal pressure from a
negative external one makes the uplift WORSE - a pressurised building pushes
its own roof off.  So the positive ``GCpi`` case governs uplift, and it is
the case that sizes the holding-down bolts.

Verification
------------
Two runs, at two roof angles, from two unrelated sources:

* **Struware CS2024**, a commercial ASCE 7-16 program written by a third
  party, at theta = 1.2 deg.  It prints the whole of Fig. 28.3-1 - all
  eight Load Case A values and all twelve Load Case B values - and every
  one matches this module's tables exactly.  It prints each zone with BOTH
  signs of internal pressure as well, which confirms Eq. 28.3-1 term by
  term and in the direction that governs uplift.  It also prints ``Kz`` and
  ``qh``, which closes the velocity pressure.
* **A published hand calculation at theta = 26.57 deg**, which pins the
  linear interpolation between the 20 and 30 degree rows: all eight zones
  reproduce its printed values to the two decimals it prints.

Both records are in ``tests/fixtures/``; see
``tests/test_wind_verification.py`` for exactly what each closes, and
:data:`COVERAGE_GAPS` for the checks this module does NOT make.

The edition question, answered
------------------------------
The theta = 26.57 deg example is ASCE 7-10, where this figure is numbered
28.4-1; 7-16 renumbered it 28.3-1.  That left open whether the coefficients
had also changed.  The Struware sheet says "Wind Loads : ASCE 7-16" on its
face and reproduces the same coefficients, so they did not.  Closed.

Units: kN, m, kN/m^2 (= kPa), degrees.  Geometry arrives in mm from
``PEBGeometry`` and is converted here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..analysis import FrameLayout, LoadDirection
from ..spec import (
    EnclosureClassification,
    ExposureCategory,
    MemberType,
    PEBGeometry,
    ProjectSite,
    RoofType,
)
from .frame import FrameLoad, tributary_width
from .gravity import LoadError

__all__ = [
    "VERIFIED",
    "COVERAGE_GAPS",
    "EXPOSURE_CONSTANTS",
    "GCPF_LOAD_CASE_A",
    "GCPF_LOAD_CASE_B",
    "MIN_WALL_PRESSURE",
    "MIN_ROOF_PRESSURE",
    "ZoneRole",
    "WindResult",
    "MinimumWindLoad",
    "velocity_pressure_coefficient",
    "velocity_pressure",
    "end_zone_width",
    "gcpf_load_case_a",
    "gcpf_load_case_b",
    "envelope_pressures",
    "wind_case_names",
    "frame_is_in_end_zone",
    "zone_map_for_frame",
    "wind_loads_for_frame",
    "net_horizontal_pressures",
    "minimum_design_wind_load",
    "longitudinal_endwall_pressure",
]

#: CLOSED 2026-09-02.  Every flag names the record that earns it; the
#: records are ``tests/fixtures/struware_asce716_envelope_theta1p2.json``
#: (Struware CS2024, ASCE 7-16, theta = 1.2 deg, with the source PDF beside
#: it) and the theta = 26.57 deg published hand calculation cited in
#: ``tests/test_wind_verification.py``.
#:
#:   gcpf_table
#:       Struware prints Fig. 28.3-1 in full at theta = 1.2 deg: all 8 Load
#:       Case A values and all 12 Load Case B values.  Every one matches
#:       this module's literals exactly.  That closes the 0-5 degree row -
#:       which is the ONLY row a PEB uses, since a 1:10 roof is 5.7 deg and
#:       a 0.5:12 roof is 2.4 deg.
#:       The other rows rest on weaker but stated evidence, and a reader is
#:       entitled to know which is which:
#:         20 and 30 deg - pinned jointly by the 26.57 deg example.  One
#:           interpolation cannot separate two rows in principle, but it is
#:           8 simultaneous agreements at f = 0.657, and a transcription
#:           slip in either row would have to be cancelled by a matching
#:           slip in the other to survive it.
#:         45 deg - equal to the 30 deg row by the figure's own ">30 to 45"
#:           band, so it is the same numbers, not a second transcription.
#:         90 deg - satisfies the limiting-case identity exactly: at 90 deg
#:           the "roof" IS the wall, so zone 2 must equal zone 1 and zone 3
#:           must equal zone 4, in both the interior and the end set.  All
#:           four pairs hold (0.56/0.56, -0.37/-0.37, 0.69/0.69,
#:           -0.48/-0.48).  A 90 degree roof cannot occur on a PEB; the row
#:           exists only to terminate the interpolation.
#:   angle_interpolation
#:       The 26.57 deg example, f = 0.657 between the 20 and 30 deg rows:
#:       all eight zones reproduce its printed two decimals.  The Struware
#:       run cannot close this one - it is a single angle inside the flat
#:       band and exercises no interpolation at all.
#:   end_zone_width
#:       Struware: 175 x 300 ft, h = 62 ft -> "Edge Strip (a) = 17.5 ft",
#:       "End Zone (2a) = 35.0 ft".  0.1B = 17.5 against 0.4h = 24.8, so
#:       the "smaller of" branch is exercised with 0.1B governing, and the
#:       4% floor (7.0 ft) is shown not to bind.  The 26.57 deg example
#:       gives a second run of the same branch (3.00 ft).  The 0.4h branch
#:       and the two floors are pinned by longhand arithmetic on the
#:       printed rule, which is what the rule is - four numbers and two
#:       comparisons, with no formula to get wrong.
#:   velocity_pressure
#:       Struware prints Kz = Kh = 1.144 at z = 62 ft Exposure C; the
#:       equation gives 1.14436 - agreement to four significant figures,
#:       against a program rather than against the table the equation is
#:       tabulated from.  Its qh = 35.9 psf reproduces longhand as 35.858.
#:       Backed by a 48-point sweep of Table 26.10-1 (3 exposures x 16
#:       heights, 15 to 200 ft): every deviation lies in -0.0028 to
#:       +0.0073, with 12 of the 48 on each side of zero - an ordinary
#:       two-decimal rounding, i.e. the table and the equation are the same
#:       relation.  Neither side is systematically the safer, so preferring
#:       the equation is a matter of precision, not of conservatism.
#:       Anchored finally by Kz(10 m, Exposure C) = 1.0009, which follows
#:       from the DEFINITION of the basic wind speed rather than from any
#:       table.
#:   internal_pressure_sign
#:       Struware prints every zone twice, "w/-GCpi" and "w/+GCpi": 20
#:       zone/case pairs, each equal to GCpf - GCpi to the printed two
#:       decimals.  On every roof zone the +GCpi column is the more
#:       negative one - the pressurised interior pushing its own roof off.
#:       That is the sign rule, confirmed by an implementation that did not
#:       come from here.
#:   minimum_design_load
#:       CLOSED 2026-09-02, Sect. 28.3.4.  Three legs:
#:         The constants are an EXACT conversion, not a measurement: one
#:           lbf is 4.4482216152605 N and one foot is 0.3048 m, both
#:           defined, so 16 lb/ft2 is 0.766084 kN/m2 and 8 is 0.383042.
#:         The quantity the minimum is compared against - the net
#:           across-the-frame pressure, windward zone minus leeward zone -
#:           is pinned against Struware's four printed "Horizontal MWFRS
#:           Simple Diaphragm Pressures": 24.7 and -11.5 psf interior,
#:           37.3 and -19.4 psf in the end zone.  All four reproduce.
#:         Struware's building is a NEGATIVE CONTROL: at 24.7 psf on the
#:           wall and 11.5 on the roof the minimum does NOT govern there,
#:           and the check must say so.  A minimum-load routine that
#:           reported "governs" everywhere would pass a confirmation-only
#:           test; it fails this one.
VERIFIED: dict[str, bool] = {
    "gcpf_table": True,
    "angle_interpolation": True,
    "end_zone_width": True,
    "velocity_pressure": True,
    "internal_pressure_sign": True,
    "minimum_design_load": True,
}

#: Checks this module does NOT perform.  Distinct from an unverified flag:
#: the relations above ARE checked, these are relations that are absent.
#: They print on the calculation sheet through
#: ``report.collect_coverage_gaps()``.
COVERAGE_GAPS: tuple[str, ...] = (
    "The Sect. 28.3.4 minimum is enforced on the TRANSVERSE direction "
    "(Load Case A) only. Load Case B produces no net transverse force on a "
    "frame - its two sidewall zones are equal and opposite - so the "
    "transverse minimum cannot be applied to it. The longitudinal "
    "direction's minimum is applied separately, where the longitudinal "
    "system is solved.",
    "Where the Sect. 28.3.4 minimum governs it is enforced by SCALING the "
    "wind case up to it. That is conservative for the wind-dominated "
    "combinations, but in a combination where wind RELIEVES gravity "
    "(1.2D + 1.6Lr + 0.5W with the roof in suction) a scaled-up wind is "
    "the less onerous direction. Check the gravity-dominated combinations "
    "against the unscaled case as well.",
    "Torsional load cases (Fig. 28.3-1, the 25%-of-zone-pressure cases) are "
    "NOT generated. They are what size a frame against plan twist, and the "
    "exemption in the figure's own note has to be checked by hand.",
    "Parapet (Sect. 28.3.2) and roof overhang (Sect. 28.3.3) pressures are "
    "NOT computed. A PEB with a parapet or a canopy needs them added.",
    "Wind is mapped onto a transverse frame from ONE direction at a time. "
    "For a symmetric frame the other direction is its mirror and nothing is "
    "lost; for an asymmetric frame the caller must run from_left=False as "
    "well and envelope the two.",
    "Only a single-span gable frame has a transverse zone mapping here. "
    "Multi-span (multi-ridge) and monoslope frames raise rather than guess, "
    "because Fig. 28.3-1 numbers its zones around ONE ridge.",
    "Components and cladding pressures are not this module's scope - they "
    "are Chapter 30 and live in bimsteelauto.loads.cladding, which purlins "
    "and girts must be designed on. A secondary member designed on the "
    "Chapter 28 pressures here is UNDER-designed: on this package's own "
    "demonstration building the worst MWFRS roof suction is GCpf = -1.07 "
    "against a C&C edge-zone GCp of -2.3.",
    "Ke is a caller input defaulting to 1.0. There is no ground-elevation "
    "lookup, so a site well above sea level is designed conservatively "
    "rather than accurately.",
)


# ---------------------------------------------------------------------------
# Velocity pressure
# ---------------------------------------------------------------------------

#: ASCE 7-16 Table 26.11-1: (alpha, zg in metres).  ``Kz`` is
#: ``2.01 (z/zg)^(2/alpha)``, which is the equation Table 26.10-1 is
#: tabulated from - so using it reproduces the table rather than
#: interpolating a transcription of it.
EXPOSURE_CONSTANTS: dict[ExposureCategory, tuple[float, float]] = {
    ExposureCategory.B: (7.0, 365.76),
    ExposureCategory.C: (9.5, 274.32),
    ExposureCategory.D: (11.5, 213.36),
}

#: Below this height Table 26.10-1 holds Kz constant, m.  4.572 m is 15 ft
#: exactly, which is the height the table's first row actually means; ASCE's
#: own SI parenthetical rounds it to 4.6 m.
_Z_MIN = 4.572


def velocity_pressure_coefficient(
    z: float, exposure: ExposureCategory
) -> float:
    """``Kz``, ASCE 7-16 Table 26.10-1 / Eq. C26.10-1.

    ``z`` in metres.  Held constant below 4.572 m (15 ft), as the table is.
    """
    if z <= 0:
        raise LoadError(f"height must be positive, got {z}")
    alpha, zg = EXPOSURE_CONSTANTS[exposure]
    return 2.01 * (max(z, _Z_MIN) / zg) ** (2.0 / alpha)


def velocity_pressure(
    V: float,
    z: float,
    exposure: ExposureCategory,
    *,
    Kzt: float = 1.0,
    Kd: float = 0.85,
    Ke: float = 1.0,
) -> float:
    """``qz = 0.613 Kz Kzt Kd Ke V^2``, ASCE 7-16 Eq. 26.10-1.  kN/m^2.

    The 0.613 is the SI constant; the imperial form uses 0.00256 with V in
    mph.  The two are not exactly equivalent - ASCE rounds both - so an SI
    answer and an imperial one differ by about 0.03%.  ``Ke`` is the ground
    elevation factor of Sect. 26.9, which is 1.0 at sea level and is
    conservatively permitted to be taken as 1.0 anywhere.
    """
    if V <= 0:
        raise LoadError(f"wind speed must be positive, got {V}")
    Kz = velocity_pressure_coefficient(z, exposure)
    return 0.613 * Kz * Kzt * Kd * Ke * V**2 / 1000.0


# ---------------------------------------------------------------------------
# Fig. 28.3-1
# ---------------------------------------------------------------------------

#: ASCE 7-16 Fig. 28.3-1, Load Case A, in zone order
#: ``(1, 2, 3, 4, 1E, 2E, 3E, 4E)``, keyed by roof angle in degrees.
#:
#: On a transverse section the zones are, in order: windward wall, windward
#: roof, leeward roof, leeward wall - and the ``E`` set is the same four in
#: the end zone.
GCPF_LOAD_CASE_A: dict[float, tuple[float, ...]] = {
    0.0:  (0.40, -0.69, -0.37, -0.29, 0.61, -1.07, -0.53, -0.43),
    5.0:  (0.40, -0.69, -0.37, -0.29, 0.61, -1.07, -0.53, -0.43),
    20.0: (0.53, -0.69, -0.48, -0.43, 0.80, -1.07, -0.69, -0.64),
    30.0: (0.56,  0.21, -0.43, -0.37, 0.69,  0.27, -0.53, -0.48),
    45.0: (0.56,  0.21, -0.43, -0.37, 0.69,  0.27, -0.53, -0.48),
    90.0: (0.56,  0.56, -0.37, -0.37, 0.69,  0.69, -0.48, -0.48),
}

#: Load Case B, zones ``(1, 2, 3, 4, 5, 6, 1E, 2E, 3E, 4E, 5E, 6E)``.
#: Independent of roof angle - it is the wind blowing along the building,
#: which Fig. 28.3-1's own note directs be taken at theta = 0 with the zone
#: 2/3 boundary at mid-length.  Zones 5 and 6 are the END walls.
GCPF_LOAD_CASE_B: tuple[float, ...] = (
    -0.45, -0.69, -0.37, -0.45, 0.40, -0.29,
    -0.48, -1.07, -0.53, -0.48, 0.61, -0.43,
)

_ZONES_A = ("1", "2", "3", "4", "1E", "2E", "3E", "4E")
_ZONES_B = ("1", "2", "3", "4", "5", "6", "1E", "2E", "3E", "4E", "5E", "6E")

#: Exactly one pound-force per square foot, in kN/m^2.  1 lbf is
#: 4.4482216152605 N and 1 ft is 0.3048 m; both are DEFINED, so this
#: conversion is exact rather than measured.
_PSF: float = 4.4482216152605 / (0.3048 ** 2) / 1000.0     # 0.047880259

#: ASCE 7-16 Sect. 28.3.4, the minimum design wind loading.  The clause
#: defines 16 and 8 lb/ft^2 and prints "(0.77 kN/m2)" and "(0.38 kN/m2)"
#: beside them as SOFT conversions.  The exact equivalents are 0.766084 and
#: 0.383042, and those are what is used here: the imperial numbers are the
#: ones the clause actually defines, and ASCE's printed 0.38 sits 0.8%
#: BELOW the value it stands for.  Rounding a MINIMUM downward is the wrong
#: direction to round.
#:
#: (``end_zone_width`` goes the other way and uses ASCE's printed 0.9 m
#: rather than the exact 0.9144 m.  That is not an inconsistency of
#: principle: there the printed value is the more onerous of the two, so
#: both places take the safer reading of the same ambiguity.)
MIN_WALL_PRESSURE: float = 16.0 * _PSF      # 0.766084 kN/m^2
MIN_ROOF_PRESSURE: float = 8.0 * _PSF       # 0.383042 kN/m^2


class ZoneRole:
    """Which part of a transverse frame each Load Case A zone acts on."""

    WINDWARD_WALL = "1"
    WINDWARD_ROOF = "2"
    LEEWARD_ROOF = "3"
    LEEWARD_WALL = "4"


def gcpf_load_case_a(theta_deg: float) -> dict[str, float]:
    """Interpolated Load Case A coefficients at a roof angle.

    Linear between the tabulated angles, which is what Fig. 28.3-1's own
    note directs.  Below 5 deg and above 90 deg the end values are held.
    """
    if theta_deg < 0.0:
        raise LoadError(f"roof angle must not be negative, got {theta_deg}")
    angles = sorted(GCPF_LOAD_CASE_A)
    t = min(max(theta_deg, angles[0]), angles[-1])

    lo = max(a for a in angles if a <= t)
    hi = min(a for a in angles if a >= t)
    if lo == hi:
        vals = GCPF_LOAD_CASE_A[lo]
    else:
        f = (t - lo) / (hi - lo)
        a, b = GCPF_LOAD_CASE_A[lo], GCPF_LOAD_CASE_A[hi]
        vals = tuple(a[i] + f * (b[i] - a[i]) for i in range(len(a)))
    return dict(zip(_ZONES_A, vals))


def gcpf_load_case_b() -> dict[str, float]:
    return dict(zip(_ZONES_B, GCPF_LOAD_CASE_B))


def end_zone_width(geometry: PEBGeometry) -> float:
    """``a``, ASCE 7-16 Fig. 28.3-1 note 9.  Returned in mm.

    ``a`` is 10% of the least horizontal dimension or 0.4h, whichever is
    SMALLER, but not less than 4% of the least horizontal dimension or
    0.9 m (3 ft).

    Both halves matter: the "smaller of" caps it on a tall building and the
    "not less than" floors it on a squat one.  Dropping either gives the
    wrong end-zone width, and with it the wrong pressure on the end frames -
    which are the ones with the most onerous coefficients.

    The 0.9 m floor is ASCE's own soft conversion of 3 ft, printed as
    "3 ft (0.9 m)" in the figure.  3 ft is 0.9144 m, so 0.9 m sits 1.6%
    below the imperial value; it is used because it is what the Standard
    says in SI, not because it was rounded here.
    """
    least = min(geometry.width, geometry.length) / 1000.0    # m
    h = geometry.mean_roof_height / 1000.0
    a = min(0.1 * least, 0.4 * h)
    a = max(a, 0.04 * least, 0.9)
    return a * 1000.0


# ---------------------------------------------------------------------------
# The four wind cases
# ---------------------------------------------------------------------------


def wind_case_names() -> tuple[str, ...]:
    """The load case names this module produces.

    Four, because Chapter 28 requires Load Case A and Load Case B, each with
    both signs of internal pressure.  Hand these to
    ``loads.combinations.combinations_for(wind_cases=...)`` and every one is
    enveloped.
    """
    return ("W_A_pi_pos", "W_A_pi_neg", "W_B_pi_pos", "W_B_pi_neg")


def _gcpi_for(case: str, gcpi: tuple[float, float]) -> float:
    """The internal pressure coefficient one case name implies."""
    if case.endswith("_pi_pos"):
        return gcpi[0]
    if case.endswith("_pi_neg"):
        return gcpi[1]
    raise LoadError(f"{case!r} is not one of {wind_case_names()}")


@dataclass(frozen=True)
class WindResult:
    """Everything the sheet and the analysis need from Chapter 28."""

    qh: float                       # kN/m^2 at mean roof height
    Kz: float
    Kd: float
    Kzt: float
    Ke: float
    theta_deg: float
    end_zone_a: float               # mm
    GCpi: tuple[float, float]       # (positive, negative)
    #: ``pressures[case][zone]`` in kN/m^2, positive TOWARD the surface.
    pressures: dict[str, dict[str, float]]
    #: ``gcpf[case][zone]`` - the Fig. 28.3-1 coefficient behind each of
    #: those pressures, kept so a reviewer reading the sheet sees the
    #: coefficient beside the number it produced instead of taking it on
    #: trust.
    gcpf: dict[str, dict[str, float]] = field(default_factory=dict)
    #: Carried because Sect. 28.3.4's minimum applies to enclosed and
    #: partially enclosed buildings only, and the check must be able to
    #: tell.  ``None`` means the caller built this by hand and the minimum
    #: cannot be decided.
    enclosure: EnclosureClassification | None = None
    reference: str = "ASCE 7-16 Ch. 28 Part 1 (Fig. 28.3-1)"

    def roof_uplift_case(self) -> str:
        """The case with the most negative windward-roof pressure.

        Named rather than assumed: it is the one that sizes the anchors, and
        a reader should be able to see which of the four it was.
        """
        return min(
            self.pressures,
            key=lambda c: self.pressures[c].get(ZoneRole.WINDWARD_ROOF, 0.0),
        )

    @property
    def steps(self) -> tuple[tuple[str, float, str], ...]:
        return (
            ("Kz at mean roof height", self.Kz, "-"),
            ("Kzt (Sect. 26.8)", self.Kzt, "-"),
            ("Kd (Table 26.6-1)", self.Kd, "-"),
            ("Ke (Sect. 26.9)", self.Ke, "-"),
            ("qh (Eq. 26.10-1)", self.qh, "kN/m2"),
            ("roof angle theta", self.theta_deg, "deg"),
            ("end zone width a (Fig. 28.3-1 note 9)", self.end_zone_a, "mm"),
            ("GCpi positive (Table 26.13-1)", self.GCpi[0], "-"),
            ("GCpi negative", self.GCpi[1], "-"),
        )


def envelope_pressures(
    geometry: PEBGeometry,
    site: ProjectSite,
    *,
    Ke: float = 1.0,
) -> WindResult:
    """Run Chapter 28 Part 1 and return all four wind cases.

    ``p = qh [ (GCpf) - (GCpi) ]``, Eq. 28.3-1.  Every zone of every case is
    computed; nothing is pre-selected, because which zone governs which
    member is the analysis's business, not this module's.
    """
    h = geometry.mean_roof_height / 1000.0
    Kz = velocity_pressure_coefficient(h, site.exposure_category)
    qh = velocity_pressure(
        site.basic_wind_speed, h, site.exposure_category,
        Kzt=site.Kzt, Kd=site.Kd, Ke=Ke,
    )

    theta = geometry.slope_deg
    A = gcpf_load_case_a(theta)
    B = gcpf_load_case_b()
    gcpi_pos, gcpi_neg = site.GCpi

    pressures: dict[str, dict[str, float]] = {}
    coefficients: dict[str, dict[str, float]] = {}
    for case, table, gcpi in (
        ("W_A_pi_pos", A, gcpi_pos),
        ("W_A_pi_neg", A, gcpi_neg),
        ("W_B_pi_pos", B, gcpi_pos),
        ("W_B_pi_neg", B, gcpi_neg),
    ):
        pressures[case] = {
            zone: qh * (gcpf - gcpi) for zone, gcpf in table.items()
        }
        coefficients[case] = dict(table)

    return WindResult(
        qh=qh,
        Kz=Kz,
        Kd=site.Kd,
        Kzt=site.Kzt,
        Ke=Ke,
        theta_deg=theta,
        end_zone_a=end_zone_width(geometry),
        GCpi=(gcpi_pos, gcpi_neg),
        pressures=pressures,
        gcpf=coefficients,
        enclosure=site.enclosure_classification,
    )


# ---------------------------------------------------------------------------
# Sect. 28.3.4 - the minimum design wind loading
# ---------------------------------------------------------------------------


def net_horizontal_pressures(
    wind: WindResult, *, end_zone: bool = False
) -> tuple[float, float]:
    """``(wall, roof)`` net across-the-frame pressure, kN/m^2.

    Windward zone minus leeward zone, on the wall and on the roof.  These
    are the quantities a wind program prints as its "simple diaphragm"
    pressures, and they are what Sect. 28.3.4's minimum is compared against.

    The internal pressure CANCELS in the subtraction - it pushes on the
    windward and leeward faces at the same time - so the answer does not
    depend on which ``GCpi`` case is read, and the minimum-load check is
    made once rather than four times.  That cancellation is worth stating
    because it is also the reason a sign error in ``GCpi`` cannot be caught
    by looking at base shear alone.
    """
    e = "E" if end_zone else ""
    g = wind.gcpf.get("W_A_pi_pos")
    if not g:
        raise LoadError(
            "this WindResult carries no Load Case A coefficients, so the net "
            "horizontal pressures cannot be formed"
        )
    wall = wind.qh * (
        g[ZoneRole.WINDWARD_WALL + e] - g[ZoneRole.LEEWARD_WALL + e]
    )
    roof = wind.qh * (
        g[ZoneRole.WINDWARD_ROOF + e] - g[ZoneRole.LEEWARD_ROOF + e]
    )
    return wall, roof


@dataclass(frozen=True)
class MinimumWindLoad:
    """ASCE 7-16 Sect. 28.3.4, checked and reported rather than assumed."""

    #: Net across-the-frame pressures actually computed, kN/m^2.
    p_wall: float
    p_roof: float
    #: The clause minimums, kN/m^2.
    p_wall_min: float
    p_roof_min: float
    #: Projected areas of the WHOLE building onto the vertical plane normal
    #: to the wind, m^2.  Wall is L x H; roof is L x rise, a rectangle -
    #: both roof planes project onto the same strip, so it is not halved.
    A_wall: float
    A_roof: float
    #: Total horizontal force, kN - computed and minimum.
    F_computed: float
    F_minimum: float
    end_zone: bool
    applicable: bool
    note: str = ""

    @property
    def governs(self) -> bool:
        return self.applicable and self.F_computed < self.F_minimum

    @property
    def scale(self) -> float:
        """Factor the wind case must be multiplied by.  Never below 1.0.

        ``inf`` when the computed force is zero or negative and the minimum
        applies - there is nothing to scale, and the caller must be told
        rather than handed a number that reads like a pass.
        """
        if not self.applicable:
            return 1.0
        if self.F_computed <= 0.0:
            return math.inf
        return max(1.0, self.F_minimum / self.F_computed)

    @property
    def steps(self) -> tuple[tuple[str, float, str], ...]:
        return (
            ("net wall pressure (windward - leeward)", self.p_wall, "kN/m2"),
            ("Sect. 28.3.4 wall minimum (16 psf)", self.p_wall_min, "kN/m2"),
            ("net roof pressure (windward - leeward)", self.p_roof, "kN/m2"),
            ("Sect. 28.3.4 roof minimum (8 psf)", self.p_roof_min, "kN/m2"),
            ("projected wall area L x H", self.A_wall, "m2"),
            ("projected roof area L x rise", self.A_roof, "m2"),
            ("total horizontal force computed", self.F_computed, "kN"),
            ("total horizontal force minimum", self.F_minimum, "kN"),
            ("scale applied to the wind case", self.scale, "-"),
        )


def minimum_design_wind_load(
    geometry: PEBGeometry, wind: WindResult, *, end_zone: bool = False
) -> MinimumWindLoad:
    """Run Sect. 28.3.4 for the transverse direction.

    The clause: the wind load used for the MWFRS shall not be less than
    16 lb/ft^2 on the wall area plus 8 lb/ft^2 on the roof area projected
    onto a vertical plane normal to the wind direction.  It applies to
    enclosed and partially enclosed buildings; an open building has no
    minimum, and this returns ``applicable=False`` for one.

    The roof's projection needs care.  For transverse wind the plane normal
    to the wind contains the building LENGTH, and both roof planes project
    onto the same strip between eave and ridge - so the projected roof area
    is ``L x rise``, a rectangle, and not the gable triangle.  The triangle
    is the LONGITUDINAL projection, and it is used in
    :func:`longitudinal_endwall_pressure`.

    The computed roof force is taken WITH ITS SIGN.  On a low-slope gable
    it is negative - the windward roof's suction pulls the frame back
    against the wind - so it reduces the total.  ASCE's companion rule that
    the total may not fall below the walls-alone value carries an explicit
    exception for MWFRS moment frames, and a PEB portal is one, so that
    rule is not applied here.
    """
    applicable = wind.enclosure in (
        EnclosureClassification.ENCLOSED,
        EnclosureClassification.PARTIALLY_ENCLOSED,
        EnclosureClassification.PARTIALLY_OPEN,
    )
    note = ""
    if wind.enclosure is None:
        note = (
            "the enclosure classification is unknown on this WindResult, so "
            "Sect. 28.3.4 could not be decided and was NOT applied"
        )
    elif not applicable:
        note = (
            f"Sect. 28.3.4 does not apply to an "
            f"{wind.enclosure.value} building"
        )

    p_wall, p_roof = net_horizontal_pressures(wind, end_zone=end_zone)
    L = geometry.length / 1000.0
    H = geometry.eave_height / 1000.0
    rise = geometry.ridge_rise / 1000.0

    A_wall = L * H
    A_roof = L * rise
    F_computed = p_wall * A_wall + p_roof * A_roof
    F_minimum = MIN_WALL_PRESSURE * A_wall + MIN_ROOF_PRESSURE * A_roof

    return MinimumWindLoad(
        p_wall=p_wall,
        p_roof=p_roof,
        p_wall_min=MIN_WALL_PRESSURE,
        p_roof_min=MIN_ROOF_PRESSURE,
        A_wall=A_wall,
        A_roof=A_roof,
        F_computed=F_computed,
        F_minimum=F_minimum if applicable else 0.0,
        end_zone=end_zone,
        applicable=applicable,
        note=note,
    )


def longitudinal_endwall_pressure(
    geometry: PEBGeometry, wind: WindResult, *, apply_minimum: bool = True
) -> tuple[float, MinimumWindLoad | None]:
    """Net endwall pressure driving the longitudinal system, kN/m^2.

    Load Case B's zones 5 and 6 ARE the two endwalls - windward and leeward
    - so their difference is the net longitudinal pressure, with the
    internal pressure cancelling exactly as it does across the frame.  This
    is the number :func:`~bimsteelauto.analysis.bracing.solve_longitudinal`
    needs, and taking it from here rather than from a typed-in value is the
    difference between a derived bracing design and an assumed one.

    Sect. 28.3.4 applies in this direction too, over the ENDWALL's
    projection: ``B x H`` of wall plus the gable triangle ``B x rise / 2``
    of roof.  Because ``solve_longitudinal`` takes one blended pressure
    over that whole silhouette, the minimum is returned blended the same
    way - the area-weighted mean of 16 and 8 psf - so the two are compared
    on equal terms.

    Returns ``(pressure, minimum_or_None)``; the second is ``None`` when
    ``apply_minimum`` is False or the clause does not apply.
    """
    g = wind.gcpf.get("W_B_pi_pos")
    if not g:
        raise LoadError(
            "this WindResult carries no Load Case B coefficients, so the "
            "longitudinal endwall pressure cannot be formed"
        )
    pressure = wind.qh * (g["5"] - g["6"])

    if not apply_minimum:
        return pressure, None

    B = geometry.width / 1000.0
    H = geometry.eave_height / 1000.0
    rise = geometry.ridge_rise / 1000.0
    A_wall = B * H
    A_roof = 0.5 * B * rise                 # the gable silhouette

    check = MinimumWindLoad(
        p_wall=pressure,
        p_roof=pressure,
        p_wall_min=MIN_WALL_PRESSURE,
        p_roof_min=MIN_ROOF_PRESSURE,
        A_wall=A_wall,
        A_roof=A_roof,
        F_computed=pressure * (A_wall + A_roof),
        F_minimum=(
            MIN_WALL_PRESSURE * A_wall + MIN_ROOF_PRESSURE * A_roof
            if wind.enclosure
            in (
                EnclosureClassification.ENCLOSED,
                EnclosureClassification.PARTIALLY_ENCLOSED,
                EnclosureClassification.PARTIALLY_OPEN,
            )
            else 0.0
        ),
        end_zone=False,
        applicable=wind.enclosure
        in (
            EnclosureClassification.ENCLOSED,
            EnclosureClassification.PARTIALLY_ENCLOSED,
            EnclosureClassification.PARTIALLY_OPEN,
        ),
        note="longitudinal direction, over the endwall projection",
    )
    if check.governs and math.isfinite(check.scale):
        pressure *= check.scale
    return pressure, check


def frame_is_in_end_zone(
    geometry: PEBGeometry, frame_index: int, a: float
) -> bool:
    """True when a frame line lies within the end zone, width ``2a``.

    Fig. 28.3-1 measures the end zone from the windward edge, so with wind
    from either end the frames within ``2a`` of EITHER endwall must be
    checked with the ``E`` coefficients.

    ``a`` is in mm, the same units as the geometry.
    """
    if not 0 <= frame_index < geometry.n_frames:
        raise LoadError(
            f"frame_index {frame_index} outside 0..{geometry.n_frames - 1}"
        )
    x = sum(geometry.bay_spacings[:frame_index])
    return x <= 2.0 * a or (geometry.length - x) <= 2.0 * a


# ---------------------------------------------------------------------------
# Zone pressures -> frame line loads
# ---------------------------------------------------------------------------


def zone_map_for_frame(
    layout: FrameLayout, *, from_left: bool = True
) -> dict[str, str]:
    """Which Fig. 28.3-1 section zone acts on which member.  ``mark -> zone``.

    The zone numbers returned are the interior ones; the caller appends
    ``E`` when the frame line falls in the end zone.

    ``from_left`` is the wind direction across the section.  True means the
    wind blows in the +X direction, so the column at x = 0 is the windward
    wall.  Run it both ways on an asymmetric frame - see
    :data:`COVERAGE_GAPS`.
    """
    geometry = layout.geometry
    if len(geometry.span_widths) != 1:
        raise LoadError(
            f"the transverse zone mapping is defined for a single-span gable "
            f"frame; this one has {len(geometry.span_widths)} spans. "
            "Fig. 28.3-1 numbers its zones around ONE ridge, so a multi-span "
            "frame needs a zoning decision this module will not make for you."
        )
    if geometry.roof_type is not RoofType.GABLE:
        raise LoadError(
            f"the transverse zone mapping is defined for a gable roof; this "
            f"one is {geometry.roof_type.value}. Fig. 28.3-1's zones 2 and 3 "
            "are the two sides of a ridge, and a monoslope roof has no ridge "
            "to put the 2/3 boundary on."
        )

    columns = sorted(
        (m for m in layout.members if m.member_type is MemberType.COLUMN),
        key=lambda m: m.i_point[0],
    )
    rafters = sorted(
        (m for m in layout.members if m.member_type is MemberType.RAFTER),
        key=lambda m: m.i_point[0],
    )
    if len(columns) != 2 or len(rafters) != 2:
        raise LoadError(
            f"expected 2 columns and 2 rafters on a single-span gable frame, "
            f"found {len(columns)} and {len(rafters)}"
        )

    if not from_left:
        columns.reverse()
        rafters.reverse()

    windward_wall, leeward_wall = columns
    windward_roof, leeward_roof = rafters
    return {
        windward_wall.mark: ZoneRole.WINDWARD_WALL,
        windward_roof.mark: ZoneRole.WINDWARD_ROOF,
        leeward_roof.mark: ZoneRole.LEEWARD_ROOF,
        leeward_wall.mark: ZoneRole.LEEWARD_WALL,
    }


def _normal_sign(geometry: PEBGeometry, member) -> float:
    """Turn "pressure positive toward the surface" into the NORMAL sign.

    ``LoadDirection.NORMAL`` is defined against the member's LOCAL axes, and
    the analysis layer's convention is that a positive ``w`` presses onto
    the member's top face.  What "top face" means in global terms depends on
    how the member was built, so the mapping is worked out here once rather
    than guessed at each call site:

    * **A rafter.**  The solver keeps a sloped member's local y-axis pointing
      upward whichever way the member runs - it is the rule that makes the
      top of a beam always the top.  So a positive ``w`` presses DOWN onto
      the roof on both sides of the ridge, which is exactly what a positive
      external pressure on a roof does.  Sign ``+1``.
    * **A column.**  Both columns are built base -> eave, so both have the
      same local axes, and a positive ``w`` pushes both toward global +X.
      For the column at x = 0 the interior is at +X, so pressure toward the
      surface is +X and the sign is ``+1``.  For the column at x = B the
      interior is at -X, so the sign is ``-1``.

    That asymmetry is not a fudge - it is the price of building both columns
    in the same direction, which is what keeps the layout code simple.  It
    is pinned by a test that solves the frame and checks the base reactions
    come out the physical way round.
    """
    xi, yi = member.i_point
    xj, yj = member.j_point
    if not math.isclose(xi, xj, abs_tol=1e-6):
        return 1.0                                   # a rafter
    if yj < yi:
        raise LoadError(
            f"column {member.mark!r} runs eave -> base; the NORMAL sign rule "
            "assumes every column is built base -> eave"
        )
    return 1.0 if xi < 0.5 * geometry.width else -1.0


def wind_loads_for_frame(
    layout: FrameLayout,
    frame_index: int,
    wind: WindResult,
    *,
    from_left: bool = True,
    enforce_minimum: bool = True,
) -> tuple[FrameLoad, ...]:
    """Chapter 28 zone pressures -> line loads on one transverse frame.

    Sixteen loads: four members x four cases.  Wind acts NORMAL to the
    surface it blows on, per metre of the member it is spread along, so no
    ``cos(theta)`` appears anywhere here - the tributary width is a plan
    width measured ALONG the building, perpendicular to the frame's plane,
    and it is the same width for a wall as for a roof.

    Whether this frame takes the interior or the ``E`` coefficients is
    decided by :func:`frame_is_in_end_zone` from the ``a`` already carried
    on the :class:`WindResult` - not recomputed, so the sheet's printed
    ``a`` and the applied loads cannot disagree.

    ``enforce_minimum`` applies ASCE 7-16 Sect. 28.3.4.  Where the clause
    minimum exceeds the computed transverse force, the Load Case A loads
    are scaled up to it and every scaled load SAYS SO in its ``origin``, so
    a reader of the sheet can see which numbers were raised and by how
    much.  Load Case B is never scaled by it: its two sidewall zones are
    equal and opposite, so it produces no net transverse force for the
    transverse minimum to bite on.  Turning it off is legitimate for a
    comparison run and is recorded the same way.
    """
    geometry = layout.geometry
    zones = zone_map_for_frame(layout, from_left=from_left)
    w_trib = tributary_width(geometry, frame_index)
    in_end_zone = frame_is_in_end_zone(geometry, frame_index, wind.end_zone_a)
    suffix = "E" if in_end_zone else ""
    where = "end zone" if in_end_zone else "interior zone"

    scale = 1.0
    minimum: MinimumWindLoad | None = None
    if enforce_minimum:
        minimum = minimum_design_wind_load(
            geometry, wind, end_zone=in_end_zone
        )
        if minimum.governs:
            if not math.isfinite(minimum.scale):
                raise LoadError(
                    "the Sect. 28.3.4 minimum governs but the computed "
                    f"transverse force is {minimum.F_computed:.3f} kN, so "
                    "there is nothing to scale up. The roof suction is "
                    "cancelling the wall pressure entirely - apply the "
                    "minimum as a separate load case by hand."
                )
            scale = minimum.scale

    out: list[FrameLoad] = []
    for case in wind_case_names():
        table = wind.pressures[case]
        coeffs = wind.gcpf.get(case, {})
        gcpi = _gcpi_for(case, wind.GCpi)
        # The transverse minimum bites on Load Case A only.
        factor = scale if case.startswith("W_A") else 1.0
        for mark, section_zone in zones.items():
            zone = section_zone + suffix
            if zone not in table:
                raise LoadError(
                    f"case {case} has no zone {zone}; it carries "
                    f"{sorted(table)}"
                )
            p = table[zone]
            sign = _normal_sign(geometry, layout.member(mark))
            gcpf = coeffs.get(zone, float("nan"))
            flip = "" if sign > 0 else (
                " (NORMAL is +X on both columns, and this one's exterior "
                "faces +X, so the sign is flipped)"
            )
            raised = "" if factor == 1.0 else (
                f" x {factor:.4f} (ASCE 7-16 Sect. 28.3.4 minimum governs: "
                f"{minimum.F_computed:.1f} kN computed against "
                f"{minimum.F_minimum:.1f} kN minimum)"
            )
            out.append(
                FrameLoad(
                    case=case,
                    member_mark=mark,
                    w=sign * p * w_trib * factor,
                    direction=LoadDirection.NORMAL,
                    origin=(
                        f"ASCE 7-16 Fig. 28.3-1 zone {zone} ({where}): "
                        f"GCpf {gcpf:+.3f}, GCpi {gcpi:+.2f}, "
                        f"p = {p:+.4f} kN/m2 x {w_trib:.3f} m tributary"
                        + flip + raised
                    ),
                )
            )
    return tuple(out)
