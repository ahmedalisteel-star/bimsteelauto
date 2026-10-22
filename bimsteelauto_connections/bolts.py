"""
peb_connections.py — Connection design layer for the PEB pipeline.
==================================================================

Scope
-----
This module supplies the pieces AISC 360-16 does *not* contain, and delegates
everything else to :mod:`pyaisc360`:

    get_grade()          EN 1993-1-1 Table 3.1 plate grades, thickness-aware
    BOLT_GRADES          AISC Table J3.2 + EN 8.8/10.9 mapping
    check_bolt_prying()  AISC Manual Part 9  -- NOT in the Specification
    bolt_row_forces()    AISC 358-16 Sect. 6.2 bolt force distribution
    evaluate_bolt_row()  assembles the above into one governing check

Units
-----
**Internal units are SI: kN, mm, MPa.**  Imperial appears only inside the
private ``_to_imp`` / ``_to_si`` boundary functions, because ``pyaisc360``
is imperial-only (Kip, Inch, Ksi).  Never mix.  Every public function takes
and returns SI.

Design basis
------------
LRFD.  Prying uses phi = 0.90 (flexural yielding of the fitting, Manual
Part 9).  Bolt rupture phi = 0.75 is applied inside pyaisc360.

References
----------
AISC 360-16 Sect. J3           -- bolt strength
AISC Manual 15th ed., Part 9   -- prying action
AISC 358-16 Sect. 6.2          -- extended end-plate bolt force
EN 1993-1-1:2005 Table 3.1     -- EN plate grades
EN 1993-1-8:2005 Table 3.1     -- EN bolt grades (f_ub)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final, Literal, Sequence

try:
    from pyaisc360 import chapter_j as _j
    from pyaisc360.core.enums import LimitState as _LS
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "peb_connections requires pyaisc360. Install it, or add its parent "
        "directory to sys.path before importing this module."
    ) from exc


# ======================================================================
# Errors
# ======================================================================

class ConnectionError_(ValueError):
    """Invalid connection input, geometry or material."""


class GradeNotFound(ConnectionError_):
    """Requested steel or bolt grade is not in the database."""


class GeometryError(ConnectionError_):
    """Connection geometry violates a code limit or is physically impossible."""


# ======================================================================
# Unit boundary  (SI internal  <->  imperial for pyaisc360)
# ======================================================================

KN_PER_KIP: Final[float] = 4.4482216152605
MM_PER_IN: Final[float] = 25.4
MPA_PER_KSI: Final[float] = 6.8947572931683


def _to_imp(*, force_kN: float | None = None,
            length_mm: float | None = None,
            stress_MPa: float | None = None) -> float:
    """SI -> imperial. Exactly one keyword may be supplied."""
    given = [v for v in (force_kN, length_mm, stress_MPa) if v is not None]
    if len(given) != 1:
        raise ValueError("_to_imp takes exactly one keyword argument")
    if force_kN is not None:
        return force_kN / KN_PER_KIP
    if length_mm is not None:
        return length_mm / MM_PER_IN
    return stress_MPa / MPA_PER_KSI          # type: ignore[operator]


def _to_si(*, force_kip: float | None = None,
           length_in: float | None = None,
           stress_ksi: float | None = None) -> float:
    """Imperial -> SI. Exactly one keyword may be supplied."""
    given = [v for v in (force_kip, length_in, stress_ksi) if v is not None]
    if len(given) != 1:
        raise ValueError("_to_si takes exactly one keyword argument")
    if force_kip is not None:
        return force_kip * KN_PER_KIP
    if length_in is not None:
        return length_in * MM_PER_IN
    return stress_ksi * MPA_PER_KSI          # type: ignore[operator]


# ======================================================================
# 1. Material grade database
# ======================================================================
# EN 1993-1-1:2005 Table 3.1 -- fy AND fu both fall with nominal thickness.
# Format: grade -> tuple of (t_max_mm, fy_MPa, fu_MPa), ascending t_max.

_PLATE_GRADES: Final[dict[str, tuple[tuple[float, float, float], ...]]] = {
    # ---- EN 10025-2 (EN 1993-1-1 Table 3.1) ----
    "S235JR": ((40.0, 235.0, 360.0), (80.0, 215.0, 360.0)),
    "S275JR": ((40.0, 275.0, 430.0), (80.0, 255.0, 410.0)),
    "S355JR": ((40.0, 355.0, 490.0), (80.0, 335.0, 470.0)),
    "S355J2": ((40.0, 355.0, 490.0), (80.0, 335.0, 470.0)),
    "S450J0": ((40.0, 440.0, 550.0), (80.0, 410.0, 550.0)),
    # ---- ASTM (nominal minimums, thickness-independent in these ranges) ----
    "A36":     ((200.0, 250.0, 400.0),),
    "A572-50": ((100.0, 345.0, 450.0),),
    "A992":    ((100.0, 345.0, 450.0),),
}

# Aliases so callers can write either convention.
_PLATE_ALIASES: Final[dict[str, str]] = {
    "S235": "S235JR", "S275": "S275JR", "S355": "S355JR",
    "A572": "A572-50", "GR50": "A572-50",
}


def get_grade(name: str, thickness_mm: float) -> tuple[float, float]:
    """Return ``(Fy, Fu)`` in MPa for a plate grade at a nominal thickness.

    Both yield and ultimate strength reduce with plate thickness in
    EN 1993-1-1 Table 3.1.  A flat lookup is unsafe for PEB base plates,
    which are routinely 25-40 mm.

    Parameters
    ----------
    name:
        Grade designation, e.g. ``"S355JR"``, ``"S355"``, ``"A36"``.
        Case-insensitive.
    thickness_mm:
        Nominal plate thickness, mm.

    Returns
    -------
    (Fy, Fu) in MPa.

    Raises
    ------
    GradeNotFound
        Grade is not in the database.
    GeometryError
        Thickness is non-positive or exceeds the tabulated range.

    Examples
    --------
    >>> get_grade("S355JR", 20.0)
    (355.0, 490.0)
    >>> get_grade("S355JR", 50.0)
    (335.0, 470.0)
    """
    if thickness_mm <= 0:
        raise GeometryError(f"thickness must be > 0, got {thickness_mm}")

    key = name.strip().upper().replace(" ", "")
    key = _PLATE_ALIASES.get(key, key)
    if key not in _PLATE_GRADES:
        raise GradeNotFound(
            f"unknown plate grade {name!r}. "
            f"Known: {', '.join(sorted(_PLATE_GRADES))}"
        )

    for t_max, fy, fu in _PLATE_GRADES[key]:
        if thickness_mm <= t_max:
            return fy, fu

    t_limit = _PLATE_GRADES[key][-1][0]
    raise GeometryError(
        f"{key}: thickness {thickness_mm} mm exceeds tabulated limit "
        f"{t_limit} mm. Enter properties from the mill certificate."
    )


# ----------------------------------------------------------------------
# Bolt grades
# ----------------------------------------------------------------------
# AISC 360-16 Table J3.2 gives, per bolt group:
#     Fnt  = nominal tensile stress
#     Fnv  = nominal shear stress, threads iNcluded (N) or eXcluded (X)
#
# EN bolts are NOT in Table J3.2.  They are mapped here using the ratios
# AISC itself uses against f_ub, which are consistent across A325/A490:
#     Fnt   = 0.750 * f_ub
#     Fnv_N = 0.450 * f_ub
#     Fnv_X = 0.563 * f_ub
# This mapping is an engineering decision, not a code table -- it is
# flagged in `is_mapped` so the calc sheet can state it.

@dataclass(frozen=True, slots=True)
class BoltGrade:
    """Nominal bolt stresses, MPa."""

    name: str
    Fnt: float           # nominal tensile stress, MPa
    Fnv_N: float         # nominal shear, threads iNcluded, MPa
    Fnv_X: float         # nominal shear, threads eXcluded, MPa
    f_ub: float          # specified min tensile strength, MPa
    is_mapped: bool = False   # True => derived from f_ub, not Table J3.2
    source: str = "AISC 360-16 Table J3.2"


def _en_bolt(name: str, f_ub: float) -> BoltGrade:
    return BoltGrade(
        name=name,
        Fnt=0.750 * f_ub,
        Fnv_N=0.450 * f_ub,
        Fnv_X=0.563 * f_ub,
        f_ub=f_ub,
        is_mapped=True,
        source="EN 1993-1-8 Table 3.1 f_ub, mapped by AISC Table J3.2 ratios",
    )


BOLT_GRADES: Final[dict[str, BoltGrade]] = {
    # AISC Group A / B -- ksi values converted exactly
    "A325": BoltGrade("A325",  620.5, 372.3, 468.8,  827.4),
    "A490": BoltGrade("A490",  779.1, 468.8, 579.2, 1034.2),
    # EN property classes -- mapped
    "8.8":  _en_bolt("8.8",   800.0),
    "10.9": _en_bolt("10.9", 1000.0),
}


def get_bolt(grade: str) -> BoltGrade:
    """Look up a bolt grade. Raises :class:`GradeNotFound` if absent."""
    key = grade.strip().upper()
    if key not in BOLT_GRADES:
        raise GradeNotFound(
            f"unknown bolt grade {grade!r}. "
            f"Known: {', '.join(BOLT_GRADES)}"
        )
    return BOLT_GRADES[key]


def bolt_tension_capacity(grade: str, d_bolt_mm: float) -> float:
    """Available tensile strength of one bolt, kN (LRFD, phi = 0.75).

    Delegates to :func:`pyaisc360.chapter_j.bolt_strength` (Eq. J3-1).
    ``Ab`` is the nominal *unthreaded body* area -- the thread reduction is
    already inside ``Fnt``.

    Raises
    ------
    GeometryError
        Non-positive diameter.
    """
    if d_bolt_mm <= 0:
        raise GeometryError(f"d_bolt must be > 0, got {d_bolt_mm}")

    bolt = get_bolt(grade)
    d_in = _to_imp(length_mm=d_bolt_mm)
    Ab_in2 = math.pi * d_in ** 2 / 4.0
    Fnt_ksi = _to_imp(stress_MPa=bolt.Fnt)

    res = _j.bolt_strength(Fn=Fnt_ksi, Ab=Ab_in2,
                           limit_state=_LS.BOLT_TENSION)
    return _to_si(force_kip=res.available)


def bolt_shear_capacity(grade: str, d_bolt_mm: float,
                        threads: Literal["N", "X"] = "N") -> float:
    """Available shear strength of one bolt, kN (LRFD, single shear plane).

    ``threads="N"`` = threads included in the shear plane (the safe default
    and normal PEB detailing); ``"X"`` = excluded.
    """
    if d_bolt_mm <= 0:
        raise GeometryError(f"d_bolt must be > 0, got {d_bolt_mm}")
    if threads not in ("N", "X"):
        raise ConnectionError_(f"threads must be 'N' or 'X', got {threads!r}")

    bolt = get_bolt(grade)
    Fnv = bolt.Fnv_N if threads == "N" else bolt.Fnv_X
    d_in = _to_imp(length_mm=d_bolt_mm)
    Ab_in2 = math.pi * d_in ** 2 / 4.0

    res = _j.bolt_strength(Fn=_to_imp(stress_MPa=Fnv), Ab=Ab_in2,
                           limit_state=_LS.BOLT_SHEAR)
    return _to_si(force_kip=res.available)


# ======================================================================
# 2. Prying engine  -- AISC Manual 15th ed., Part 9
# ======================================================================

@dataclass(frozen=True, slots=True)
class PryingResult:
    """Outcome of a Part 9 prying check, with every intermediate value.

    The intermediates are carried so the calculation sheet can show the
    derivation rather than a bare number.
    """

    # --- headline results ---
    T_available: float     # effective bolt tension capacity after prying, kN
    alpha: float           # prying factor, 0.0 = none .. 1.0 = full
    t_critical: float      # plate thickness that eliminates prying, mm

    # --- intermediates, for the calc sheet ---
    b_prime: float = 0.0   # mm, bolt line to stem face, adjusted
    a_prime: float = 0.0   # mm, bolt line to plate edge, adjusted & capped
    rho: float = 0.0       # b'/a'
    delta: float = 0.0     # net-to-gross length ratio at bolt line
    Q: float = 1.0         # capacity reduction factor, T_available / B
    B_bolt: float = 0.0    # bolt capacity BEFORE prying, kN
    t_provided: float = 0.0  # mm
    governs: str = ""      # "bolt" or "plate bending"
    warnings: tuple[str, ...] = field(default_factory=tuple)
    citation: str = "AISC Manual 15th ed., Part 9 (prying action)"

    @property
    def has_prying(self) -> bool:
        """True when plate flexibility reduces the bolt capacity."""
        return self.alpha > 0.0

    @property
    def loss_pct(self) -> float:
        """Percentage of bolt capacity lost to prying."""
        return 0.0 if self.B_bolt <= 0 else 100.0 * (1.0 - self.Q)

    def report(self) -> str:
        """One-block calc-sheet fragment."""
        lines = [
            f"Prying check  [{self.citation}]",
            f"  b' = {self.b_prime:8.2f} mm      a' = {self.a_prime:8.2f} mm",
            f"  rho = {self.rho:7.4f}         delta = {self.delta:7.4f}",
            f"  t_provided = {self.t_provided:6.2f} mm   "
            f"t_critical = {self.t_critical:6.2f} mm",
            f"  alpha' = {self.alpha:6.4f}        Q = {self.Q:6.4f}",
            f"  B (bolt alone)   = {self.B_bolt:8.2f} kN",
            f"  T (with prying)  = {self.T_available:8.2f} kN"
            f"   [{self.loss_pct:.0f}% loss]",
            f"  governs: {self.governs}",
        ]
        lines += [f"  ! {w}" for w in self.warnings]
        return "\n".join(lines)


def check_bolt_prying(
    *,
    B_bolt: float,
    t_plate: float,
    b: float,
    a: float,
    p: float,
    d_bolt: float,
    d_hole: float,
    Fu_plate: float,
    phi: float = 0.90,
) -> PryingResult:
    """Reduce a bolt's tension capacity for prying action (Manual Part 9).

    A flexible end plate or angle bends under load, and the deformed plate
    levers additional tension into the bolt.  AISC 360-16 Sect. J3.6 states
    explicitly that this is *not* in Eq. J3-1 and must be added by the
    engineer -- which is why this function exists.

    Note the two diameters are used for different things and must not be
    interchanged:

    * ``d_bolt`` sets the effective lever arms ``b'`` and ``a'``
    * ``d_hole`` sets ``delta``, the net-to-gross ratio along the bolt line

    Parameters
    ----------
    B_bolt:
        Available tensile strength of ONE bolt before prying, kN.
        Normally from :func:`bolt_tension_capacity`.
    t_plate:
        Thickness of the bending element (end plate, angle leg), mm.
    b:
        Distance from bolt centreline to the face of the stem, mm.
    a:
        Distance from bolt centreline to the plate edge, mm.
        Capped at ``1.25 * b`` per Part 9; a warning is raised, not silence.
    p:
        Tributary length of plate per bolt along the bolt line, mm.
    d_bolt:
        Nominal bolt diameter, mm.
    d_hole:
        Hole diameter, mm (standard = d_bolt + 2 mm for M-series).
    Fu_plate:
        Ultimate tensile strength of the PLATE, MPa.  Part 9 uses Fu,
        not Fy -- passing Fy here is unconservative.
    phi:
        Resistance factor for plate flexural yielding, default 0.90.

    Returns
    -------
    PryingResult

    Raises
    ------
    GeometryError
        Any dimension non-positive, or ``d_hole <= d_bolt``, or the hole
        consumes the whole tributary length (``delta <= 0``).

    Examples
    --------
    >>> r = check_bolt_prying(B_bolt=180.0, t_plate=20.0, b=50.0, a=50.0,
    ...                       p=90.0, d_bolt=22.0, d_hole=24.0,
    ...                       Fu_plate=490.0)
    >>> r.has_prying
    True
    """
    # ---- validation -------------------------------------------------
    for label, value in (("B_bolt", B_bolt), ("t_plate", t_plate),
                         ("b", b), ("a", a), ("p", p),
                         ("d_bolt", d_bolt), ("d_hole", d_hole),
                         ("Fu_plate", Fu_plate)):
        if value <= 0:
            raise GeometryError(f"{label} must be > 0, got {value}")
    if not 0.0 < phi <= 1.0:
        raise ConnectionError_(f"phi must be in (0, 1], got {phi}")
    if d_hole < d_bolt:
        raise GeometryError(
            f"d_hole ({d_hole}) must be >= d_bolt ({d_bolt})"
        )

    warnings: list[str] = []

    # ---- Part 9 geometry -------------------------------------------
    a_capped = min(a, 1.25 * b)
    if a > 1.25 * b:
        warnings.append(
            f"a = {a:.1f} mm exceeds 1.25b = {1.25 * b:.1f} mm; "
            f"capped per Part 9 (extra edge distance is not effective)"
        )

    b_prime = b - d_bolt / 2.0
    a_prime = a_capped + d_bolt / 2.0
    if b_prime <= 0:
        raise GeometryError(
            f"b' = {b_prime:.2f} mm <= 0: bolt is inside the stem face"
        )

    rho = b_prime / a_prime
    delta = 1.0 - d_hole / p
    if delta <= 0:
        raise GeometryError(
            f"delta = {delta:.3f} <= 0: hole ({d_hole} mm) consumes the "
            f"tributary length p ({p} mm). Increase bolt spacing."
        )

    # ---- critical thickness (kN, mm, MPa -> N/mm consistent) --------
    # 4*B*b' / (phi*p*Fu):  B in kN -> N by *1000
    t_critical = math.sqrt(4.0 * (B_bolt * 1000.0) * b_prime
                           / (phi * p * Fu_plate))

    # ---- prying factor ---------------------------------------------
    if t_plate >= t_critical:
        alpha = 0.0
        Q = 1.0
        governs = "bolt"
    else:
        alpha_raw = (1.0 / (delta * (1.0 + rho))) * ((t_critical / t_plate) ** 2 - 1.0)
        alpha = max(0.0, min(1.0, alpha_raw))
        Q = (t_plate / t_critical) ** 2 * (1.0 + delta * alpha)
        Q = min(Q, 1.0)
        governs = "plate bending"
        if alpha_raw > 1.0:
            warnings.append(
                "alpha' > 1.0 -- plate is very flexible; consider a thicker "
                "end plate or a stiffener"
            )

    return PryingResult(
        T_available=B_bolt * Q,
        alpha=alpha,
        t_critical=t_critical,
        b_prime=b_prime,
        a_prime=a_prime,
        rho=rho,
        delta=delta,
        Q=Q,
        B_bolt=B_bolt,
        t_provided=t_plate,
        governs=governs,
        warnings=tuple(warnings),
    )


# ======================================================================
# 3. Bolt force distribution  -- AISC 358-16 Sect. 6.2
# ======================================================================
# This replaces the "extract Tu from PyNite" step.  PyNite returns M, V, P
# at a node; it cannot return a per-bolt tension.  Converting the moment to
# bolt forces IS the end-plate design, and 358 gives the procedure:
#
#     phi*Mnp = phi * n_per_row * Pt * SUM(h_i)
#
# i.e. every tension-side bolt is taken at its full capacity Pt, and the
# moment arm h_i runs from the compression-flange centreline to each bolt
# row.  Inverting gives the demand per bolt.

@dataclass(frozen=True, slots=True)
class BoltRowForces:
    """Per-bolt tension demand from an end-plate moment."""

    T_per_bolt: float             # kN, demand in one tension bolt
    rows_mm: tuple[float, ...]    # h_i, compression flange to each row, mm
    n_per_row: int
    lever_sum: float              # SUM(h_i), mm
    Mu: float                     # kNm applied
    citation: str = "AISC 358-16 Sect. 6.2 (bolt rupture, uniform Pt)"

    def report(self) -> str:
        rows = ", ".join(f"{h:.0f}" for h in self.rows_mm)
        return (
            f"Bolt force distribution  [{self.citation}]\n"
            f"  Mu = {self.Mu:.2f} kNm   rows h_i = [{rows}] mm\n"
            f"  n_per_row = {self.n_per_row}   SUM(h_i) = {self.lever_sum:.0f} mm\n"
            f"  Tu per bolt = {self.T_per_bolt:.2f} kN"
        )


def bolt_row_forces(
    Mu: float,
    rows_mm: Sequence[float],
    n_per_row: int = 2,
) -> BoltRowForces:
    """Convert an end-plate moment into a per-bolt tension demand.

    Uses the AISC 358-16 Sect. 6.2 assumption that all tension-side bolts
    reach the same force.  This is the prequalified procedure; for a
    non-prequalified configuration an elastic distribution will differ and
    should be checked separately.

    Parameters
    ----------
    Mu:
        Factored moment at the connection, kNm.  From a Pynite load combo.
    rows_mm:
        Lever arms ``h_i`` from the compression-flange centreline to each
        tension bolt row, mm.  A 4E plate has two: one outside the tension
        flange, one inside.
    n_per_row:
        Bolts per row across the plate width (2 for 4E/4ES, 4 for 8ES).

    Returns
    -------
    BoltRowForces

    Raises
    ------
    GeometryError
        Empty row list, non-positive lever arm, or bad ``n_per_row``.

    Examples
    --------
    >>> f = bolt_row_forces(Mu=250.0, rows_mm=[720.0, 600.0])
    >>> round(f.T_per_bolt, 1)
    94.7
    """
    if Mu < 0:
        raise GeometryError(f"Mu must be >= 0, got {Mu}")
    if not rows_mm:
        raise GeometryError("rows_mm must contain at least one lever arm")
    if n_per_row < 1:
        raise GeometryError(f"n_per_row must be >= 1, got {n_per_row}")
    for h in rows_mm:
        if h <= 0:
            raise GeometryError(f"lever arm must be > 0, got {h}")

    lever_sum = float(sum(rows_mm))
    # Mu [kNm] -> kNmm  => *1000 ; divide by mm gives kN
    T = (Mu * 1000.0) / (n_per_row * lever_sum)

    return BoltRowForces(
        T_per_bolt=T,
        rows_mm=tuple(float(h) for h in rows_mm),
        n_per_row=n_per_row,
        lever_sum=lever_sum,
        Mu=Mu,
    )


# ======================================================================
# 4. Assembled check
# ======================================================================

@dataclass(frozen=True, slots=True)
class BoltRowCheck:
    """Governing check for one bolt: tension + prying + shear + interaction."""

    utilization: float
    governing: str
    passed: bool
    Tu: float                 # kN demand, tension
    Vu: float                 # kN demand, shear
    T_available: float        # kN, after prying
    V_available: float        # kN
    prying: PryingResult
    checks: tuple[tuple[str, float], ...]   # (name, utilization)

    def report(self) -> str:
        head = (
            f"Bolt row check -- {'PASS' if self.passed else 'FAIL'}\n"
            f"  Tu = {self.Tu:.2f} kN   Vu = {self.Vu:.2f} kN\n"
        )
        body = "\n".join(f"  {n:<28s} U = {u:.3f}" for n, u in self.checks)
        tail = (
            f"\n  GOVERNS: {self.governing}  U = {self.utilization:.3f}\n\n"
            + self.prying.report()
        )
        return head + body + tail


def evaluate_bolt_row(
    *,
    Tu: float,
    Vu: float,
    bolt_grade: str,
    d_bolt: float,
    plate_grade: str,
    t_plate: float,
    b: float,
    a: float,
    p: float,
    d_hole: float | None = None,
    threads: Literal["N", "X"] = "N",
) -> BoltRowCheck:
    """Full check of one bolt: tension with prying, shear, and interaction.

    Prying alone is not a connection check.  This assembles the three
    limit states that always apply to a bolt carrying combined tension and
    shear, so a passing result means something.

    Still NOT covered here (design them separately): end-plate flexural
    yielding, weld strength, column-side web yielding/crippling (J10),
    block shear, and bolt bearing/tearout.

    Parameters
    ----------
    Tu, Vu:
        Factored tension and shear demand in ONE bolt, kN.
        ``Tu`` normally comes from :func:`bolt_row_forces`.
    d_hole:
        Defaults to ``d_bolt + 2.0`` mm (standard M-series clearance).

    Returns
    -------
    BoltRowCheck
    """
    if Tu < 0 or Vu < 0:
        raise GeometryError("Tu and Vu must be >= 0")

    hole = d_bolt + 2.0 if d_hole is None else d_hole
    _, Fu_plate = get_grade(plate_grade, t_plate)

    B = bolt_tension_capacity(bolt_grade, d_bolt)
    V_avail = bolt_shear_capacity(bolt_grade, d_bolt, threads=threads)

    pry = check_bolt_prying(
        B_bolt=B, t_plate=t_plate, b=b, a=a, p=p,
        d_bolt=d_bolt, d_hole=hole, Fu_plate=Fu_plate,
    )

    u_tension = Tu / pry.T_available if pry.T_available > 0 else math.inf
    u_shear = Vu / V_avail if V_avail > 0 else math.inf
    # Elliptical interaction (conservative stand-in for Eq. J3-3a/J3-7;
    # replace with chapter_j.combined_tension_shear once the bolt group
    # layout module supplies the reduced Fnt).
    u_combined = math.hypot(u_tension, u_shear)

    checks = (
        ("bolt tension + prying", u_tension),
        ("bolt shear", u_shear),
        ("combined T+V", u_combined),
    )
    name, util = max(checks, key=lambda kv: kv[1])

    return BoltRowCheck(
        utilization=util,
        governing=name,
        passed=util <= 1.0,
        Tu=Tu, Vu=Vu,
        T_available=pry.T_available,
        V_available=V_avail,
        prying=pry,
        checks=checks,
    )


# ======================================================================
# Demo
# ======================================================================

if __name__ == "__main__":
    print("=" * 66)
    print("PEB eave connection -- 4E extended end plate")
    print("=" * 66)

    Fy, Fu = get_grade("S355JR", 20.0)
    print(f"\nPlate S355JR t=20mm : Fy={Fy:.0f} MPa  Fu={Fu:.0f} MPa")
    Fy40, Fu40 = get_grade("S355JR", 50.0)
    print(f"Plate S355JR t=50mm : Fy={Fy40:.0f} MPa  Fu={Fu40:.0f} MPa"
          "   <- thickness matters")

    bolt = get_bolt("10.9")
    print(f"\nBolt {bolt.name}: Fnt={bolt.Fnt:.0f} MPa  "
          f"(mapped={bolt.is_mapped})")
    print(f"  source: {bolt.source}")

    print("\n" + "-" * 66)
    forces = bolt_row_forces(Mu=250.0, rows_mm=[720.0, 600.0], n_per_row=2)
    print(forces.report())

    print("\n" + "-" * 66)
    chk = evaluate_bolt_row(
        Tu=forces.T_per_bolt, Vu=25.0,
        bolt_grade="10.9", d_bolt=24.0,
        plate_grade="S355JR", t_plate=20.0,
        b=50.0, a=45.0, p=90.0,
    )
    print(chk.report())

    print("\n" + "-" * 66)
    print("Same connection, end plate 20mm -> 32mm:")
    chk2 = evaluate_bolt_row(
        Tu=forces.T_per_bolt, Vu=25.0,
        bolt_grade="10.9", d_bolt=24.0,
        plate_grade="S355JR", t_plate=32.0,
        b=50.0, a=45.0, p=90.0,
    )
    print(f"  prying alpha : {chk.prying.alpha:.3f} -> {chk2.prying.alpha:.3f}")
    print(f"  utilization  : {chk.utilization:.3f} -> {chk2.utilization:.3f}"
          f"   [{'PASS' if chk2.passed else 'FAIL'}]")
