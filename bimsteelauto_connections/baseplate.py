"""
peb_baseplate.py — Column base plate design (AISC Design Guide 1).
==================================================================

Task #5 of the connection layer.  Handles the general case — axial force
plus moment plus shear — with the pinned base (``Mu = 0``) falling out as
a special case, so one module covers both PEB detailing conventions.

    concrete_bearing()        AISC 360-16 Sect. J8 via pyaisc360
    lambda_n_prime()          DG 1 Eq. 3.3.14a/b — the yield-line cantilever
    design_base_plate()       full design: bearing, thickness, anchor tension

Units: SI throughout — kN, mm, MPa, kNm.

Verification status
-------------------
The DG 1 relations here carry ``_VERIFIED`` flags, the same gate that
caught a real error in the 4E end-plate Y_p.  ``design_base_plate`` refuses
to run under ``strict=True`` until each is checked against an independent
source.

Recommended cross-check: **Hilti PROFIS Engineering** (free) for the anchor
side, and any base-plate calculator for the bearing/thickness side.  Note
that CET.SteelConnDesign does NOT cover base plates.

The two eccentricity cases
--------------------------
Everything hinges on ``e = Mu/Pu`` against the critical eccentricity:

* **small e** — the whole base is in bearing, anchors carry no tension.
  Thickness is set by cantilever bending of the plate off the column.
* **large e** — bearing collapses to a strip near one edge and the anchors
  on the far side go into tension.  That tension is the input to the ACI
  318 Ch. 17 anchor check (task #6).

Getting this split wrong is the classic base-plate error: assume small
eccentricity, report zero anchor tension, and the holding-down bolts are
never designed for the uplift that governs a PEB frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from pyaisc360 import chapter_j as _j

from .bolts import (
    ConnectionError_,
    GeometryError,
    _to_imp,
    _to_si,
    get_grade,
)

PHI_C: Final[float] = 0.65      # AISC J8, concrete crushing
PHI_B: Final[float] = 0.90      # plate flexural yielding

# ----------------------------------------------------------------------
# The PROFIS cross-check model
# ----------------------------------------------------------------------
# Recorded 2026-08-30. The Case A run had been made without its inputs
# written down anywhere in the package; they were recovered by inverting
# the Case C and Case D predictions and cross-checking against the anchor
# model already recorded in ``anchors.VERIFIED`` (2x2 @ 350 mm -> f = 175,
# f'c 30 MPa). Both predictions reproduce to the digit:
#
#   BasePlateGeometry(N=450, B=450, d=300, bf=150, f=175)   [bf per IPE 300]
#   plate_grade "S355JR", fc_prime = 30.0 MPa, A2/A1 = 1.0
#   -> phi*Pp = 3356.4375 kN,  q_max = 7.45875 kN/mm
#
#   CASE   Pu (kN)   Mu (kNm)   Vu (kN)   module     PROFIS rigid
#   A      +150       0          60         0.000      -0.005   matched
#   C      +150     120          60       241.283     306.481   -21.3%
#   D      -150      80          60       287.546     306.032    -6.0%
#          (D read 378.571 / +23.7% before the defect #9b fix below)
#   E      +500     150          60       168.686     275.997   -38.9%
#   E'     +500     150          60       168.686     276.000   -38.9%  (HEB 300 control)
#
# Model CONFIRMED against the PROFIS reports 2026-08-30 (BASEPLATE VERIFY,
# ...VERIFY 1, ...VERIFY 2): plate 450x450x50 A36, profile IPE 300, tension
# resultant at y = 175.0 mm (Sect. 1.2), f'c 30 MPa, h 800, hef 450,
# 2x2 @ 350, ca 450 all round. IPE 300 -> d 300, bf 150 (not 200); bf
# affects the n / n' cantilevers and hence THICKNESS, not T_anchor.
#
# ---------------------------------------------------------------------
# RESOLVED 2026-08-31 against AISC Design Guide 1, 2ND EDITION itself
# ---------------------------------------------------------------------
# The PROFIS comparison above ran for three cases plus a control and left
# an open question: was the module misapplying DG 1, or do DG 1 and PROFIS
# genuinely differ? Reading the Design Guide settles it.
#
# Every relation checks out, exactly:
#
#   DG 1 eq.   relation                                       match
#   3.3.4      q = f_p(max) * B, FULL plate width B           exact
#   3.3.7      e_crit = N/2 - Pr/(2 q_max)                    exact
#   3.4.2      T = q_max*Y - Pr   (vertical equilibrium)      exact
#   3.4.3      Y = (f + N/2) - sqrt[(f+N/2)^2 - 2Pr(e+f)/q]   exact
#   3.4.4      radical condition (f+N/2)^2 >= 2Pr(e+f)/q      exact
#   3.1.2      m, n, n' = sqrt(d*bf)/4                        exact
#   3.1.2      X, lambda = 2sqrtX/(1+sqrt(1-X)) <= 1          exact
#   3.1.1      Pp = 0.85 f'c A1 sqrt(A2/A1)                   exact
#
# DG 1's own moment equation about point B is
#   q_max*Y*(N/2 - Y/2 + f) - Pr*(e + f) = 0
# which places the bearing resultant at N/2 - Y/2 from the plate centre --
# a uniform block at the PLATE EDGE, across the FULL width B. That is
# precisely what this module does.
#
# So there was no defect #9a. The 21-39% gap against PROFIS is a genuine
# METHOD difference, not an error: DG 1 uses a uniform plastic bearing
# block over the whole plate; PROFIS's CBFEM concentrates bearing near the
# column footprint. Both are defensible. DG 1 is this project's stated
# design basis, and the module implements it correctly.
#
#   *** If the project ever wants PROFIS-like anchor tensions, that is a
#   *** decision to CHANGE THE DESIGN BASIS, not a bug fix. The gap is
#   *** recorded in tests/fixtures/profis_baseplate_cases.json so the
#   *** choice stays visible on any calculation sheet.
#
# Defect #9b (the net-uplift branch failing moment equilibrium) was real
# and was fixed 2026-08-30 -- see the branch itself. DG 1 has no explicit
# uplift-plus-moment case; the fix is its own Eq. 3.4.3 with Pr carried
# through signed, which is the direct extension rather than a new method.
#
# ---------------------------------------------------------------------
# DEFECTS #13 and #14 -- found by the same reading, both FIXED
# ---------------------------------------------------------------------
# #13  Eq. 3.3.15 was missing. The bearing-interface thickness only ever
#      used Eq. 3.3.14 (the Y >= l form). DG 1 gives a separate expression
#      for Y < l. Case C sits in that range (Y 52.5, m 82.5), so it was
#      not hypothetical. 3.3.15 <= 3.3.14 for all Y < l, so the omission
#      was CONSERVATIVE.
#
# #14  The tension interface halved the moment. DG 1 Eq. 3.4.5 gives
#      Mpl = Tu*x/B per unit width with NO 1/2. The spurious /2 made the
#      required thickness exactly 1/sqrt(2) = 0.707 of DG 1's -- 29% LOW,
#      and UNCONSERVATIVE. The lever arm was wrong too: Eq. 3.4.6 defines
#      x = f - d/2 + tf/2, not f - 0.95d/2, so BasePlateGeometry now takes
#      tf and warns when it is missing.
#
# Residual, recorded rather than buried: these equations were read off
# scanned pages of DG 1 2nd ed. The transcription risk is now on THIS
# side of the comparison. Anyone re-checking should read Sects. 3.1.2,
# 3.3 and 3.4 directly rather than trusting this summary.
#
# ---------------------------------------------------------------------
# DG 1 EXAMPLE 4.7 (Large Moment Base Plate), checked 2026-08-31
# ---------------------------------------------------------------------
# W12x96, 20x20 plate, Pu = 376 kips, Mu = 3600 kip-in, Fy 36, f'c 4.
# NOT a blind check -- the worked solution arrived with the inputs -- so
# it CONFIRMS the four flags rather than closing them. No code changed.
#
#   quantity              DG 1      module     note
#   m                     3.97      3.968      exact
#   n  (controls)         5.12      5.120      exact
#   q_max                 44.2      44.200     exact
#   e / e_crit         9.57/5.75  9.574/5.747  exact
#   f_p                   2.21      2.2100     exact
#   t (bearing iface)     1.90      1.884      -0.84%, fully accounted:
#                                              DG 1 rounds 1.4907 -> 1.5
#                                              and Fy 36 ksi = 248.2 MPa
#                                              vs the A36 palette's 250.
#   plate selected        2 in.     50 mm      same
#   Y                     12.0      12.606     +5.1%   <-- see below
#   T_anchor              156       181.19     +16.2%  <-- see below
#
# Y and T do not match because EXAMPLE 4.7 SUBSTITUTES f = 9.5 in. two
# lines after computing f = N/2 - 1.5 = 8.5 in. for the same plate. 9.5
# is N/2 from the first (19x19) iteration. The slip is visible inside one
# inequality, which prints
#       (f + N/2)^2          = (8.5 + 10)^2            = 342   [f = 8.5]
#       2 Pu (e+f) / q_max   = (2)(376)(9.57+9.5)/44.2 = 324   [f = 9.5]
# Feed f = 9.5 through Eq. 3.4.3 and you get 12.035 and 155.95, i.e. the
# example's printed 12.0 and 156. Feed its own stated f = 8.5 and you get
# 12.606 and 181.19, i.e. this module. The formula is confirmed; the
# printed Y and T are not reproducible from the example's own geometry.
# Consequence in the example: 181.2/3 = 60.4 kips per rod against the
# 57.7 kips it cites for a 1-1/2 in. Grade 36 rod -- the rod it selects
# would not check out. Recorded in
# tests/fixtures/dg1_example_4_7_large_moment.json and asserted in
# tests/test_dg1_example_4_7.py.
#
# SECOND ERROR in Example 4.7, and the lever arm question it opened:
# RESOLVED 2026-08-31 against the printed Sect. 3.4. Eq. 3.4.6 reads
#
#       x = f - d/2 + tf/2                                      (3.4.6)
#
# and its prose states the cantilever span is "the distance from the rod
# centerline to the CENTER OF THE COLUMN FLANGE". Example 4.7 uses
# x = N/2 - d/2 - 1.5 = f - d/2 = 2.15 in., which measures to the OUTER
# FACE and is short by tf/2 = 0.45 in. THE MODULE IS CORRECT; the tf/2
# term stays, and the warning issued when tf is omitted is right as
# written. Neither arm governs in that example (bearing asks 1.90 in.
# against 1.71 with tf and 1.55 without), so its final plate stands.
#
# The rest of Sect. 3.4 was transcribed a second time and agrees with the
# first reading line for line -- 3.4.2, 3.4.3, 3.4.4, 3.3.7, 3.3.14a,
# 3.3.15a and its "substitute n for m" note, 3.4.5a (no 1/2, confirming
# the defect #14 fix), 3.4.7a. Recorded in
# tests/fixtures/dg1_2nd_ed_sect_3_4_text.json.

VERIFIED: dict[str, bool] = {
    # All four VERIFIED 2026-08-31 against AISC Design Guide 1, 2nd ed.,
    # Sects. 3.1.1, 3.1.2, 3.3 and 3.4 -- the design basis itself, not a
    # tool that implements it. See the block above for the equation map.
    "bearing": True,         # Eq. 3.3.4 q = fp(max)*B; Pp = 0.85f'c A1 sqrt(A2/A1)
    "thickness": True,       # Eq. 3.3.14 / 3.3.15 / 3.4.5-3.4.7 (defects #13, #14 fixed)
    "lambda": True,          # Eq. 3.1.2 X and lambda
    "eccentricity": True,    # Eq. 3.3.7 e_crit; 3.4.2 / 3.4.3 / 3.4.4
}


class UnverifiedFormula(ConnectionError_):
    """A Design Guide 1 relation has not been independently checked."""


class BearingCase(str, Enum):
    """Which side of the critical eccentricity the base falls on."""

    CONCENTRIC = "concentric"          # Mu = 0
    SMALL_ECCENTRICITY = "small e"     # full bearing, no anchor tension
    LARGE_ECCENTRICITY = "large e"     # partial bearing, anchors in tension


# ======================================================================
# Geometry
# ======================================================================

@dataclass(frozen=True, slots=True)
class BasePlateGeometry:
    """Base plate and column geometry, mm.

    Attributes
    ----------
    N:
        Plate length, parallel to the column web / the moment axis.
    B:
        Plate width.
    d:
        Column depth.
    bf:
        Column flange width.
    f:
        Distance from the column centreline to the anchor-rod group
        centreline on the tension side.  Required for the moment case.
    A2_over_A1:
        Ratio of the geometrically similar concrete area to the plate
        area.  1.0 = plate covers the pedestal; the sqrt bonus is capped
        at 2.0 by AISC J8-2, so values above 4.0 buy nothing.
    tf:
        Column flange thickness.  Used only for the tension-interface
        lever arm, DG 1 Eq. 3.4.6: ``x = f - d/2 + tf/2``.  When omitted
        the module falls back to ``f - d/2`` and WARNS, because dropping
        ``tf/2`` shortens the lever and is unconservative.
    """

    N: float
    B: float
    d: float
    bf: float
    f: float | None = None
    A2_over_A1: float = 1.0
    tf: float | None = None

    def __post_init__(self) -> None:
        for label in ("N", "B", "d", "bf"):
            v = getattr(self, label)
            if v <= 0:
                raise GeometryError(f"{label} must be > 0, got {v}")
        if self.A2_over_A1 < 1.0:
            raise GeometryError(
                f"A2_over_A1 must be >= 1.0, got {self.A2_over_A1}"
            )
        if self.N < 0.95 * self.d:
            raise GeometryError(
                f"plate length N = {self.N:.0f} mm is shorter than "
                f"0.95d = {0.95 * self.d:.0f} mm — the column does not fit"
            )
        if self.B < 0.80 * self.bf:
            raise GeometryError(
                f"plate width B = {self.B:.0f} mm is narrower than "
                f"0.80bf = {0.80 * self.bf:.0f} mm — the column does not fit"
            )

    @property
    def A1(self) -> float:
        """Plate bearing area, mm²."""
        return self.N * self.B

    @property
    def m(self) -> float:
        """Cantilever overhang parallel to N, mm (DG 1 Eq. 3.1.10)."""
        return (self.N - 0.95 * self.d) / 2.0

    @property
    def n(self) -> float:
        """Cantilever overhang parallel to B, mm (DG 1 Eq. 3.1.11)."""
        return (self.B - 0.80 * self.bf) / 2.0

    @property
    def n_prime(self) -> float:
        """Yield-line cantilever under the column web, mm.

        ``n' = sqrt(d*bf)/4``  (DG 1 Eq. 3.1.12)
        """
        return math.sqrt(self.d * self.bf) / 4.0


def lambda_factor(Pu: float, phiPp: float, d: float, bf: float) -> float:
    """DG 1 lambda factor for the ``n'`` cantilever, dimensionless.

    ``X = [4*d*bf/(d+bf)^2] * (Pu / phiPp)``
    ``lambda = 2*sqrt(X) / (1 + sqrt(1-X))  <= 1.0``

    Lambda approaches 1.0 as the plate approaches its bearing capacity and
    falls below 1 for lightly loaded plates, shortening the effective
    cantilever.  Many offices simply take lambda = 1.0; that is
    conservative and DG 1 permits it.
    """
    if phiPp <= 0:
        raise GeometryError("phiPp must be > 0")
    if Pu <= 0:
        return 0.0
    X = (4.0 * d * bf / (d + bf) ** 2) * (Pu / phiPp)
    if X >= 1.0:
        return 1.0
    return min(1.0, 2.0 * math.sqrt(X) / (1.0 + math.sqrt(1.0 - X)))


# ======================================================================
# Bearing
# ======================================================================

def concrete_bearing(fc_prime: float, A1: float,
                     A2_over_A1: float = 1.0) -> float:
    """Design bearing strength on concrete, kN (AISC 360-16 Sect. J8).

    Delegates to :func:`pyaisc360.chapter_j.concrete_bearing_strength`.
    ``phi_c = 0.65``, and the confinement bonus ``sqrt(A2/A1)`` is capped
    at 2.0 by Eq. J8-2.
    """
    if fc_prime <= 0:
        raise GeometryError(f"fc_prime must be > 0, got {fc_prime}")
    if A1 <= 0:
        raise GeometryError(f"A1 must be > 0, got {A1}")

    A1_in2 = A1 / (25.4 ** 2)
    A2_in2 = A1_in2 * A2_over_A1
    r = _j.concrete_bearing_strength(
        fc_prime=_to_imp(stress_MPa=fc_prime),
        A1=A1_in2,
        A2=A2_in2 if A2_over_A1 > 1.0 else None,
    )
    return _to_si(force_kip=r.available)


# ======================================================================
# Result
# ======================================================================

@dataclass(frozen=True, slots=True)
class BasePlateDesign:
    """Complete base plate design outcome."""

    case: BearingCase
    t_required: float           # mm
    t_selected: float           # mm
    Fy: float                   # MPa, at the selected thickness

    Pu: float                   # kN, factored axial (compression +ve)
    Mu: float                   # kNm
    Vu: float                   # kN
    eccentricity: float         # mm, Mu/Pu (inf if Pu = 0)
    e_critical: float           # mm

    phiPp: float                # kN, concrete bearing capacity
    q_max: float                # kN/mm, max bearing per unit length
    Y_bearing: float            # mm, length of the bearing block
    f_p: float                  # MPa, peak bearing pressure
    bearing_utilization: float

    T_anchor: float             # kN, TOTAL tension in the anchor group
    l_cantilever: float         # mm, governing cantilever (m, n or lambda*n')
    lambda_: float
    governing_cantilever: str

    passed: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)
    citation: str = "AISC Design Guide 1, 2nd ed. (with AISC 360-16 J8)"

    @property
    def has_anchor_tension(self) -> bool:
        """True when the anchors must be designed for uplift."""
        return self.T_anchor > 0.0

    def report(self) -> str:
        out = [
            f"Base plate  [{self.citation}]  "
            f"{'PASS' if self.passed else 'FAIL'}",
            f"  Pu = {self.Pu:8.2f} kN   Mu = {self.Mu:8.2f} kNm   "
            f"Vu = {self.Vu:7.2f} kN",
            f"  e = Mu/Pu   = {self.eccentricity:8.1f} mm"
            f"   e_crit = {self.e_critical:8.1f} mm",
            f"  CASE: {self.case.value}",
            "",
            f"  phi*Pp (bearing) = {self.phiPp:8.2f} kN"
            f"   U = {self.bearing_utilization:.3f}",
            f"  bearing length Y = {self.Y_bearing:8.1f} mm"
            f"   f_p = {self.f_p:.2f} MPa",
            "",
            f"  cantilever m     = {self.l_cantilever:8.2f} mm"
            f"   ({self.governing_cantilever}, lambda = {self.lambda_:.3f})",
            f"  t_required       = {self.t_required:8.2f} mm",
            f"  t_selected       = {self.t_selected:8.1f} mm"
            f"   (Fy = {self.Fy:.0f} MPa)",
            "",
            f"  ANCHOR TENSION   = {self.T_anchor:8.2f} kN"
            + ("   -> design anchors to ACI 318 Ch. 17"
               if self.has_anchor_tension else "   (none)"),
        ]
        out += [f"  ! {w}" for w in self.warnings]
        return "\n".join(out)


# ======================================================================
# Design
# ======================================================================

DEFAULT_PLATE_PALETTE: Final[tuple[float, ...]] = (
    12.0, 16.0, 20.0, 25.0, 30.0, 32.0, 35.0, 40.0, 45.0, 50.0, 60.0, 70.0,
)


def design_base_plate(
    *,
    Pu: float,
    Mu: float = 0.0,
    Vu: float = 0.0,
    geom: BasePlateGeometry,
    plate_grade: str,
    fc_prime: float = 25.0,
    palette=DEFAULT_PLATE_PALETTE,
    use_lambda: bool = True,
    strict: bool = True,
) -> BasePlateDesign:
    """Design a column base plate for axial + moment + shear.

    Parameters
    ----------
    Pu:
        Factored axial force, kN.  **Compression positive.**  A negative
        value (net uplift, common in a PEB under wind) puts the whole
        anchor group in tension and is handled explicitly.
    Mu:
        Factored moment about the plate's strong axis, kNm.  Zero for a
        pinned base.
    Vu:
        Factored shear, kN.  Carried through to the anchor check; it does
        not affect plate thickness.
    fc_prime:
        Concrete cylinder strength, MPa (25 MPa is a common pedestal mix).
    use_lambda:
        Apply the DG 1 lambda reduction to ``n'``.  False = lambda 1.0,
        which is conservative and what many offices use.

    Returns
    -------
    BasePlateDesign — including ``T_anchor``, the input to task #6.

    Raises
    ------
    UnverifiedFormula
        ``strict=True`` while the DG 1 relations are unverified.
    GeometryError
        Moment case without ``geom.f``, or no plate thick enough.
    """
    if strict:
        unver = [k for k, v in VERIFIED.items() if not v]
        if unver:
            raise UnverifiedFormula(
                f"Design Guide 1 relations not yet checked: "
                f"{', '.join(unver)}. Cross-check against Hilti PROFIS "
                f"(anchors) and a base-plate calculator (bearing/thickness), "
                f"then flip _VERIFIED. Pass strict=False for development."
            )

    if Vu < 0:
        raise GeometryError(f"Vu must be >= 0, got {Vu}")
    if Mu < 0:
        raise GeometryError(f"Mu must be >= 0, got {Mu} (use the magnitude)")

    warnings: list[str] = []

    phiPp = concrete_bearing(fc_prime, geom.A1, geom.A2_over_A1)
    # Max bearing per unit LENGTH along N, kN/mm
    q_max = phiPp / geom.N

    # ---- classify -------------------------------------------------
    if Pu <= 0.0:
        # ---- NET UPLIFT (Pu <= 0) ---------------------------------
        # FIXED 2026-08-30 (defect #9b). The previous form was
        #     T = |Pu| + Mu/(2f)
        # which satisfies force equilibrium but NOT moment equilibrium about
        # the plate centroid: it implies sum(M) = Mu + |Pu|*f, an excess of
        # exactly |Pu|*f. That was provable from the formula alone and did
        # not depend on any external tool.
        #
        # The replacement is not a new method — it is the SAME DG 1 Sect. 3.4
        # relation the large-e branch uses, with Pu carried through signed.
        # Writing the quadratic in terms of (Mu + Pu*f) rather than
        # Pu*(e + f) removes the singularity at Pu -> 0 that forced the split
        # in the first place, since e = Mu/Pu is meaningless under uplift:
        #
        #     Y^2 - 2(f + N/2) Y + 2(Mu + Pu*f)/q_max = 0
        #     T = q_max*Y - Pu                      (Pu negative here)
        #
        # so that when the bearing model itself is decided (defect #9a, the
        # open question about bearing width and lever arm) ONE change fixes
        # every branch instead of three.
        case = BearingCase.LARGE_ECCENTRICITY
        e = math.inf                      # Mu/Pu is meaningless under uplift
        e_crit = 0.0
        if geom.f is None:
            raise GeometryError("net uplift requires geom.f (anchor lever arm)")

        uplift = abs(Pu)
        M_net = Mu * 1000.0 - uplift * geom.f      # kN*mm

        if M_net <= 0.0:
            # The moment is too small to close a bearing zone: every anchor
            # is still in tension and the group simply carries the uplift.
            Y = 0.0
            f_p = 0.0
            T_anchor = uplift
            if Mu > 0.0:
                T_far = uplift / 2.0 + Mu * 1000.0 / (2.0 * geom.f)
                warnings.append(
                    f"net uplift with moment, no bearing zone: the group "
                    f"total is {uplift:.1f} kN but the rows are UNEQUAL — the "
                    f"far row carries {T_far:.1f} kN. Group total is the "
                    f"right demand for ACI 17.6.2 breakout; check steel and "
                    f"pullout against the row force"
                )
        else:
            half = geom.f + geom.N / 2.0
            disc = half ** 2 - 2.0 * M_net / q_max
            if disc < 0:
                raise GeometryError(
                    f"no bearing solution under uplift: Pu = {Pu:.0f} kN with "
                    f"Mu = {Mu:.0f} kNm needs more bearing length than the "
                    f"plate has. Enlarge the plate, raise f'c, or move the "
                    f"anchors out."
                )
            Y = half - math.sqrt(disc)
            if Y <= 0 or Y > geom.N:
                raise GeometryError(
                    f"bearing length Y = {Y:.1f} mm is outside 0..N "
                    f"({geom.N:.0f} mm) under uplift — check the geometry"
                )
            T_anchor = q_max * Y - Pu          # Pu < 0, so this adds
            f_p = q_max * 1000.0 / geom.B      # kN/mm^2 -> MPa

        warnings.append(
            "net uplift (Pu <= 0) — this usually governs a PEB base under "
            "wind. Anchor tension is the input to the ACI 318 Ch. 17 check"
        )
    elif Mu == 0.0:
        case = BearingCase.CONCENTRIC
        e = 0.0
        e_crit = math.inf
        Y = geom.N
        f_p = Pu * 1000.0 / geom.A1        # kN/mm^2 -> MPa
        T_anchor = 0.0
    else:
        e = Mu * 1000.0 / Pu
        e_crit = geom.N / 2.0 - Pu / (2.0 * q_max)
        if e <= e_crit:
            case = BearingCase.SMALL_ECCENTRICITY
            Y = geom.N - 2.0 * e
            f_p = Pu * 1000.0 / (Y * geom.B) if Y > 0 else math.inf
            T_anchor = 0.0
        else:
            case = BearingCase.LARGE_ECCENTRICITY
            if geom.f is None:
                raise GeometryError(
                    "large eccentricity requires geom.f (distance from column "
                    "centreline to the anchor group)"
                )
            f_arm = geom.f
            # DG 1 Sect. 3.4: Y^2 - 2(f + N/2)Y + 2*Pu*(e + f)/q_max = 0
            half = f_arm + geom.N / 2.0
            disc = half ** 2 - 2.0 * Pu * (e + f_arm) / q_max
            if disc < 0:
                raise GeometryError(
                    f"no bearing solution: the plate is too small for "
                    f"Pu = {Pu:.0f} kN with e = {e:.0f} mm. "
                    f"Enlarge the plate, raise f'c, or move the anchors out."
                )
            Y = half - math.sqrt(disc)
            if Y <= 0 or Y > geom.N:
                raise GeometryError(
                    f"bearing length Y = {Y:.1f} mm is outside 0..N "
                    f"({geom.N:.0f} mm) — check the geometry"
                )
            T_anchor = q_max * Y - Pu
            f_p = q_max * 1000.0 / geom.B      # kN/mm^2 -> MPa
            if T_anchor < 0:
                T_anchor = 0.0

    bearing_util = (
        (Pu / phiPp) if (phiPp > 0 and Pu > 0) else 0.0
    )
    if bearing_util > 1.0:
        warnings.append(
            f"concrete bearing exceeded (U = {bearing_util:.2f}) — "
            f"enlarge the plate or raise f'c"
        )

    # ---- cantilever ------------------------------------------------
    lam = (
        lambda_factor(max(Pu, 0.0), phiPp, geom.d, geom.bf)
        if use_lambda else 1.0
    )
    cands = (("m", geom.m), ("n", geom.n), ("lambda*n'", lam * geom.n_prime))
    governing_cantilever, l_cant = max(cands, key=lambda kv: kv[1])

    # ---- thickness -------------------------------------------------
    # DG 1: t_req = l * sqrt(2*Pu / (phi*Fy*B*N))  for the bearing case.
    # Under partial bearing the pressure is f_p, so the general form is
    # t_req = l * sqrt(2*f_p / (phi*Fy)).
    # ---- DEFECTS #13 and #14, fixed 2026-08-31 against DG 1 2nd ed. -----
    # #13  The bearing interface only ever used Eq. 3.3.14 (Y >= l). DG 1
    #      gives a SEPARATE expression for Y < l, Eq. 3.3.15, where the
    #      bearing block is shorter than the cantilever and the moment is
    #      f_p*Y*(l - Y/2) rather than f_p*l^2/2. Case C sits in that range
    #      (Y = 52.5 mm, m = 82.5 mm) so the branch is not hypothetical.
    #      3.3.15 <= 3.3.14 for all Y < l (they meet at Y = l), so the old
    #      code was CONSERVATIVE -- an omission, not a danger.
    # #14  The tension interface halved the moment. DG 1 Eq. 3.4.5 gives
    #      Mpl = Tu*x/B per unit width with NO 1/2 -- the cantilever carries
    #      the full anchor force at lever x. The spurious /2 made the
    #      required thickness exactly 1/sqrt(2) = 0.707 of DG 1's, i.e. 29%
    #      LOW. That one IS unconservative. The lever arm was also wrong:
    #      DG 1 Eq. 3.4.6 defines x = f - d/2 + tf/2, not f - 0.95d/2.
    x_arm = 0.0
    if T_anchor > 0 and geom.f is not None:
        if geom.tf is None:
            x_arm = max(geom.f - geom.d / 2.0, 0.0)
            warnings.append(
                f"column flange thickness tf not given — the tension-side "
                f"lever arm x (DG 1 Eq. 3.4.6) is taken as f - d/2 = "
                f"{x_arm:.1f} mm instead of f - d/2 + tf/2. That omits tf/2 "
                f"and is UNCONSERVATIVE on the tension-interface thickness "
                f"by roughly sqrt(1 + tf/(2x)) — about 8% for a 10 mm "
                f"flange here. Pass BasePlateGeometry(tf=...) for the exact "
                f"value"
            )
        else:
            x_arm = max(geom.f - geom.d / 2.0 + geom.tf / 2.0, 0.0)

    chosen: float | None = None
    t_req = 0.0
    for t in sorted(palette):
        Fy, _ = get_grade(plate_grade, t)
        pressure = f_p if f_p > 0 else 0.0

        # --- bearing interface, DG 1 Eq. 3.3.14 / 3.3.15 ---------------
        if pressure <= 0:
            t_req = 0.0
        elif Y >= l_cant or Y <= 0.0:
            # Eq. 3.3.14: full cantilever in bearing.  tp = l*sqrt(2fp/(phi Fy))
            # (DG 1 prints the rounded coefficient 1.5 = 2/sqrt(0.9) = 1.4907)
            t_req = l_cant * math.sqrt(2.0 * pressure / (PHI_B * Fy))
        else:
            # Eq. 3.3.15: bearing block shorter than the cantilever.
            # phi*Fy*tp^2/4 = fp*Y*(l - Y/2)  ->  tp = 2/sqrt(phi) * sqrt(...)
            # (DG 1 prints the rounded coefficient 2.11 = 2/sqrt(0.9) = 2.108)
            t_req = math.sqrt(
                4.0 * pressure * Y * (l_cant - Y / 2.0) / (PHI_B * Fy)
            )

        # --- tension interface, DG 1 Eq. 3.4.5 / 3.4.7 -----------------
        if T_anchor > 0 and x_arm > 0:
            # Mpl = Tu*x/B per unit width;  phi*Fy*tp^2/4 = Mpl
            t_up = math.sqrt(
                4.0 * (T_anchor * 1000.0) * x_arm / (PHI_B * Fy * geom.B)
            )
            t_req = max(t_req, t_up)

        if t >= t_req:
            chosen = t
            break

    if chosen is None:
        raise GeometryError(
            f"no plate in the palette reaches t_required = {t_req:.1f} mm. "
            f"Enlarge the plate (shorter cantilever) or use a higher grade."
        )

    Fy_sel, _ = get_grade(plate_grade, chosen)

    if Vu > 0:
        warnings.append(
            f"Vu = {Vu:.1f} kN must be resisted by anchor shear, a shear lug, "
            f"or friction — not checked here (task #6)"
        )

    return BasePlateDesign(
        case=case, t_required=t_req, t_selected=chosen, Fy=Fy_sel,
        Pu=Pu, Mu=Mu, Vu=Vu, eccentricity=e, e_critical=e_crit,
        phiPp=phiPp, q_max=q_max, Y_bearing=Y, f_p=f_p,
        bearing_utilization=bearing_util,
        T_anchor=T_anchor, l_cantilever=l_cant, lambda_=lam,
        governing_cantilever=governing_cantilever,
        passed=(bearing_util <= 1.0 and chosen >= t_req),
        warnings=tuple(warnings),
    )


# ======================================================================
# Invariants
# ======================================================================

def _run_invariants() -> list[tuple[str, bool, str]]:
    res: list[tuple[str, bool, str]] = []

    def chk(n: str, ok: bool, d: str = "") -> None:
        res.append((n, ok, d))

    G = BasePlateGeometry(N=500.0, B=400.0, d=300.0, bf=200.0, f=200.0)
    kw = dict(geom=G, plate_grade="S355JR", fc_prime=25.0, strict=False)

    c = design_base_plate(Pu=800.0, Mu=0.0, **kw)
    chk("concentric -> no anchor tension",
        c.case is BearingCase.CONCENTRIC and c.T_anchor == 0.0,
        f"T = {c.T_anchor:.1f} kN")

    chk("concentric bearing over full plate",
        math.isclose(c.Y_bearing, G.N), f"Y = {c.Y_bearing:.0f} mm")

    s = design_base_plate(Pu=800.0, Mu=40.0, **kw)
    chk("small e -> still no anchor tension",
        s.case is BearingCase.SMALL_ECCENTRICITY and s.T_anchor == 0.0,
        f"e={s.eccentricity:.0f} <= e_crit={s.e_critical:.0f} mm")

    L = design_base_plate(Pu=200.0, Mu=120.0, **kw)
    chk("large e -> anchors in tension",
        L.case is BearingCase.LARGE_ECCENTRICITY and L.T_anchor > 0,
        f"T = {L.T_anchor:.1f} kN")

    chk("large e: bearing shorter than plate",
        0 < L.Y_bearing < G.N, f"Y = {L.Y_bearing:.0f} mm of {G.N:.0f}")

    u = design_base_plate(Pu=-150.0, Mu=0.0, **kw)
    chk("net uplift -> T = |Pu|",
        math.isclose(u.T_anchor, 150.0), f"T = {u.T_anchor:.1f} kN")

    chk("more moment -> more anchor tension",
        design_base_plate(Pu=200.0, Mu=150.0, **kw).T_anchor > L.T_anchor,
        f"{design_base_plate(Pu=200.0,Mu=150.0,**kw).T_anchor:.1f} "
        f"> {L.T_anchor:.1f} kN")

    chk("more axial -> less anchor tension",
        design_base_plate(Pu=400.0, Mu=120.0, **kw).T_anchor < L.T_anchor,
        f"{design_base_plate(Pu=400.0,Mu=120.0,**kw).T_anchor:.1f} "
        f"< {L.T_anchor:.1f} kN")

    G_big = BasePlateGeometry(N=700.0, B=600.0, d=300.0, bf=200.0, f=280.0)
    b = design_base_plate(Pu=800.0, Mu=0.0, geom=G_big,
                          plate_grade="S355JR", strict=False)
    chk("bigger plate -> lower bearing utilisation",
        b.bearing_utilization < c.bearing_utilization,
        f"{b.bearing_utilization:.3f} < {c.bearing_utilization:.3f}")

    chk("lambda <= 1.0", 0.0 <= c.lambda_ <= 1.0, f"lambda = {c.lambda_:.3f}")

    conf = design_base_plate(Pu=800.0, Mu=0.0, geom=BasePlateGeometry(
        N=500.0, B=400.0, d=300.0, bf=200.0, f=200.0, A2_over_A1=4.0),
        plate_grade="S355JR", strict=False)
    chk("confinement A2/A1 raises bearing capacity",
        conf.phiPp > c.phiPp, f"{conf.phiPp:.0f} > {c.phiPp:.0f} kN")

    # --- ABSOLUTE unit checks: relative invariants cannot catch a
    # --- uniform scale error. A 1000x kN/mm^2 -> MPa slip passed every
    # --- comparison test above before these two were added.
    chk("f_p absolute = Pu/A1 (concentric)",
        math.isclose(c.f_p, 800.0 * 1000.0 / G.A1, rel_tol=1e-9),
        f"{c.f_p:.3f} MPa vs {800.0*1000.0/G.A1:.3f}")

    chk("f_p at large e = phi*0.85*fc' (physical max)",
        math.isclose(L.f_p, 0.65 * 0.85 * 25.0, rel_tol=1e-3),
        f"{L.f_p:.3f} vs {0.65*0.85*25.0:.3f} MPa")

    chk("t_required is a realistic plate thickness",
        5.0 < c.t_required < 100.0, f"{c.t_required:.2f} mm")

    # The gate held until 2026-08-31, when all four DG 1 relations were
    # checked against the Design Guide itself. It must now get OUT OF THE
    # WAY -- a gate that still refuses after the check is bureaucracy.
    try:
        v = design_base_plate(Pu=800.0, Mu=0.0, geom=G, plate_grade="S355JR",
                              strict=True)
        chk("strict=True gate runs now that DG 1 is checked",
            v.t_selected > 0, f"t = {v.t_selected:.0f} mm")
    except UnverifiedFormula as ex:
        chk("strict=True gate runs now that DG 1 is checked", False, str(ex))

    # Eq. 3.3.15 (defect #13): a bearing block shorter than the cantilever
    # must need LESS plate than the full-cantilever form, and the two must
    # meet at Y = l.
    short = design_base_plate(Pu=200.0, Mu=120.0, **kw)
    chk("Y < l uses Eq. 3.3.15, not 3.3.14",
        short.Y_bearing < short.l_cantilever
        and short.t_required < short.l_cantilever
        * math.sqrt(2.0 * short.f_p / (PHI_B * short.Fy)),
        f"Y = {short.Y_bearing:.1f} < l = {short.l_cantilever:.1f} mm")

    return res


if __name__ == "__main__":
    print("=" * 72)
    print("TASK #5 — BASE PLATE (AISC Design Guide 1)")
    print("=" * 72)

    G = BasePlateGeometry(N=500.0, B=400.0, d=300.0, bf=200.0, f=200.0)
    kw = dict(geom=G, plate_grade="S355JR", fc_prime=25.0, strict=False)

    print("\n--- PINNED base (Mu = 0) ---")
    print(design_base_plate(Pu=800.0, Mu=0.0, Vu=60.0, **kw).report())

    print("\n--- FIXED base, small eccentricity ---")
    print(design_base_plate(Pu=800.0, Mu=40.0, Vu=60.0, **kw).report())

    print("\n--- FIXED base, large eccentricity ---")
    print(design_base_plate(Pu=200.0, Mu=120.0, Vu=60.0, **kw).report())

    print("\n--- NET UPLIFT (0.9D + 1.0W governs a PEB) ---")
    print(design_base_plate(Pu=-150.0, Mu=80.0, Vu=60.0, **kw).report())

    print("\n" + "=" * 72)
    print("INVARIANT TESTS")
    print("=" * 72)
    rows = _run_invariants()
    for n, ok, d in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {n:<42s} {d}")
    print(f"\n  {sum(1 for _, ok, _ in rows if ok)}/{len(rows)} passed")
