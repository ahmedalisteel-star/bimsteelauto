"""
peb_welds.py — Weld design and column-side checks.
==================================================

Tasks #2 and #3 of the connection layer.  Both are pure assembly over
:mod:`pyaisc360.chapter_j` — every capacity equation already exists there;
what was missing is the layer that picks a weld size and decides whether
the column needs stiffeners.

    size_fillet_weld()     AISC 360-16 Sect. J2 — pick a leg from a palette
    check_column_side()    AISC 360-16 Sect. J10 — stiffener / doubler call

Units: SI throughout — kN, mm, MPa, kNm.

Why J10 matters
---------------
A PEB eave connection can pass every bolt and plate check while the column
web crushes under the haunch flange force.  J10 is the only place that
failure mode appears, so a "PASS" without it is not a check.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final, Sequence

from pyaisc360 import chapter_j as _j

from .bolts import (
    ConnectionError_,
    GeometryError,
    _to_imp,
    _to_si,
    get_grade,
)

E_STEEL: Final[float] = 200_000.0     # MPa, AISC 360-16 Sect. B3


# ======================================================================
# Electrode / weld metal
# ======================================================================

# FEXX, MPa. E70 dominates; E60 for lower-strength base metal.
ELECTRODES: Final[dict[str, float]] = {
    "E60": 414.0,
    "E70": 482.0,
    "E80": 552.0,
}

# Fabricator leg-size palette, mm.
DEFAULT_WELD_PALETTE: Final[tuple[float, ...]] = (
    4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0,
)


def min_weld_size(t_thicker: float) -> float:
    """Minimum fillet leg for the thicker part joined, mm (AISC Table J2.4).

    Imperial table converted: 1/8" -> 3 mm, 3/16" -> 5 mm, 1/4" -> 6 mm,
    5/16" -> 8 mm.  The governing thickness is the THICKER of the two parts.
    """
    if t_thicker <= 0:
        raise GeometryError(f"thickness must be > 0, got {t_thicker}")
    if t_thicker <= 6.0:
        return 3.0
    if t_thicker <= 13.0:
        return 5.0
    if t_thicker <= 19.0:
        return 6.0
    return 8.0


def max_weld_size(t_edge: float) -> float:
    """Maximum fillet leg along an edge, mm (AISC Sect. J2.2b).

    ``t < 6 mm`` -> leg = t.   ``t >= 6 mm`` -> leg = t - 2 mm.
    """
    if t_edge <= 0:
        raise GeometryError(f"thickness must be > 0, got {t_edge}")
    return t_edge if t_edge < 6.0 else t_edge - 2.0


@dataclass(frozen=True, slots=True)
class WeldDesign:
    """Selected fillet weld and its governing limit state."""

    leg: float                 # mm, selected from the palette
    leg_required: float        # mm, exact
    throat: float              # mm, effective = 0.707 * leg
    length: float              # mm, effective length used
    theta: float               # degrees, load angle to the weld axis
    demand: float              # kN
    capacity_weld: float       # kN, weld metal
    capacity_base: float       # kN, base metal
    capacity: float            # kN, governing
    utilization: float
    governing: str
    directional_factor: float  # 1.0 + 0.50*sin^1.5(theta)
    passed: bool
    electrode: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    citation: str = "AISC 360-16 Sect. J2, Table J2.5"

    def report(self) -> str:
        out = [
            f"Fillet weld  [{self.citation}]  "
            f"{'PASS' if self.passed else 'FAIL'}",
            f"  demand        = {self.demand:8.2f} kN   "
            f"theta = {self.theta:.0f} deg",
            f"  leg required  = {self.leg_required:8.2f} mm",
            f"  leg selected  = {self.leg:8.1f} mm  ({self.electrode})"
            f"   throat = {self.throat:.2f} mm",
            f"  length        = {self.length:8.1f} mm",
            f"  dir. factor   = {self.directional_factor:8.3f}",
            f"  weld metal    = {self.capacity_weld:8.2f} kN",
            f"  base metal    = {self.capacity_base:8.2f} kN",
            f"  GOVERNS: {self.governing}   U = {self.utilization:.3f}",
        ]
        out += [f"  ! {w}" for w in self.warnings]
        return "\n".join(out)


def size_fillet_weld(
    *,
    demand: float,
    length: float,
    t_part1: float,
    t_part2: float,
    base_grade: str,
    theta: float = 0.0,
    electrode: str = "E70",
    n_welds: int = 2,
    directional_increase: bool = True,
    palette: Sequence[float] = DEFAULT_WELD_PALETTE,
) -> WeldDesign:
    """Pick the smallest fillet weld that carries ``demand``.

    Two limit states are checked and the lower governs:

    * **weld metal**   ``Rn = Fnw * Awe``    (Table J2.5), with the
      directional strength increase ``1.0 + 0.50 sin^1.5(theta)`` when the
      load is not parallel to the weld axis
    * **base metal**   ``Rn = FnBM * ABM``   (Eq. J2-2), shear rupture of
      the connected part along the weld line

    Parameters
    ----------
    demand:
        Factored force on the weld group, kN.
    length:
        Weld length per weld, mm.
    t_part1, t_part2:
        Thicknesses of the two joined parts, mm.  Sets the min/max leg.
    theta:
        Angle between the load and the weld longitudinal axis, degrees.
        ``0`` = longitudinal (web welds in shear).
        ``90`` = transverse (flange welds in tension) — 50% stronger.
    n_welds:
        Number of weld lines sharing the load (2 for a double fillet).
    directional_increase:
        Apply Sect. J2.4(a) increase.  Leave True unless the weld group
        mixes orientations, where J2.4(b) applies instead.

    Raises
    ------
    GeometryError
        No leg in the palette carries the demand, or min > max leg.
    """
    for label, v in (("demand", demand), ("length", length),
                     ("t_part1", t_part1), ("t_part2", t_part2)):
        if v <= 0:
            raise GeometryError(f"{label} must be > 0, got {v}")
    if not 0.0 <= theta <= 90.0:
        raise ConnectionError_(f"theta must be 0..90 deg, got {theta}")
    if n_welds < 1:
        raise GeometryError(f"n_welds must be >= 1, got {n_welds}")
    if electrode not in ELECTRODES:
        raise ConnectionError_(
            f"unknown electrode {electrode!r}. Known: {', '.join(ELECTRODES)}"
        )

    warnings: list[str] = []
    FEXX = ELECTRODES[electrode]
    t_min_leg = min_weld_size(max(t_part1, t_part2))
    t_max_leg = max_weld_size(min(t_part1, t_part2))

    if t_min_leg > t_max_leg:
        raise GeometryError(
            f"minimum leg {t_min_leg:.1f} mm exceeds maximum {t_max_leg:.1f} mm "
            f"for parts {t_part1:.1f} / {t_part2:.1f} mm — plates are "
            f"mismatched; use a groove weld or thicken the thinner part"
        )

    # Base metal shear rupture: FnBM = 0.60*Fu of the thinner connected part
    _, Fu1 = get_grade(base_grade, t_part1)
    _, Fu2 = get_grade(base_grade, t_part2)
    Fu_base = min(Fu1, Fu2)
    t_base = min(t_part1, t_part2)

    dir_factor = (
        _j.directional_strength_increase(theta) if directional_increase else 1.0
    )

    demand_per_weld = demand / n_welds
    L_in = _to_imp(length_mm=length)
    FEXX_ksi = _to_imp(stress_MPa=FEXX)

    chosen: float | None = None
    cap_w = cap_b = 0.0

    for leg in sorted(palette):
        if leg < t_min_leg or leg > t_max_leg:
            continue
        throat_mm = 0.707 * leg
        Awe_in2 = _to_imp(length_mm=throat_mm) * L_in

        rw = _j.fillet_weld_strength(
            FEXX=FEXX_ksi, Awe=Awe_in2, theta_degrees=theta,
            directional_increase=directional_increase,
        )
        cap_w = _to_si(force_kip=rw.available)

        # Base metal: shear rupture on the fusion face, ABM = t_base * L
        ABM_in2 = _to_imp(length_mm=t_base) * L_in
        FnBM_ksi = _to_imp(stress_MPa=0.60 * Fu_base)
        rb = _j.base_metal_strength(FnBM=FnBM_ksi, ABM=ABM_in2)
        cap_b = _to_si(force_kip=rb.available)

        if min(cap_w, cap_b) >= demand_per_weld:
            chosen = leg
            break

    if chosen is None:
        raise GeometryError(
            f"no leg between {t_min_leg:.1f} and {t_max_leg:.1f} mm carries "
            f"{demand_per_weld:.1f} kN over {length:.0f} mm. "
            f"Lengthen the weld, add weld lines, or use a groove weld."
        )

    if chosen == t_min_leg:
        warnings.append(
            f"leg is at the Table J2.4 minimum ({t_min_leg:.1f} mm) — "
            f"governed by plate thickness, not by demand"
        )

    capacity = min(cap_w, cap_b)
    governing = "weld metal" if cap_w <= cap_b else "base metal"
    # exact leg required, scaling from the chosen leg (capacity is linear in throat)
    leg_req = chosen * demand_per_weld / capacity if capacity > 0 else math.inf

    return WeldDesign(
        leg=chosen, leg_required=leg_req, throat=0.707 * chosen,
        length=length, theta=theta, demand=demand_per_weld,
        capacity_weld=cap_w, capacity_base=cap_b, capacity=capacity,
        utilization=demand_per_weld / capacity if capacity > 0 else math.inf,
        governing=governing, directional_factor=dir_factor,
        passed=demand_per_weld <= capacity, electrode=electrode,
        warnings=tuple(warnings),
    )


# ======================================================================
# Column-side checks — AISC 360-16 Sect. J10
# ======================================================================

@dataclass(frozen=True, slots=True)
class ColumnSideCheck:
    """J10 checks at a concentrated flange force, and what they demand."""

    Puf: float                       # kN, concentrated flange force
    checks: tuple[tuple[str, float, float], ...]   # (name, capacity kN, U)
    governing: str
    utilization: float
    passed: bool
    needs_transverse_stiffener: bool
    needs_web_doubler: bool
    remedy: str
    citation: str = "AISC 360-16 Sect. J10"

    def report(self) -> str:
        out = [
            f"Column-side checks  [{self.citation}]  "
            f"{'PASS' if self.passed else 'FAIL'}",
            f"  concentrated flange force Puf = {self.Puf:.2f} kN",
        ]
        out += [
            f"  {n:<28s} phiRn = {c:8.2f} kN   U = {u:.3f}"
            for n, c, u in self.checks
        ]
        out.append(f"  GOVERNS: {self.governing}   U = {self.utilization:.3f}")
        out.append(f"  REMEDY: {self.remedy}")
        return "\n".join(out)


def flange_force_from_moment(Mu: float, d_beam: float,
                             tf_beam: float) -> float:
    """Concentrated flange force from a beam moment, kN.

    ``Puf = Mu / (d - tf)`` — the moment resolved into a tension/compression
    couple at the flange centroids.  This is the force J10 checks against.
    """
    if Mu < 0:
        raise GeometryError(f"Mu must be >= 0, got {Mu}")
    lever = d_beam - tf_beam
    if lever <= 0:
        raise GeometryError(
            f"lever arm (d - tf) = {lever:.1f} mm must be > 0"
        )
    return Mu * 1000.0 / lever


def check_column_side(
    *,
    Mu: float,
    d_beam: float,
    tf_beam: float,
    col_grade: str,
    d_col: float,
    tw_col: float,
    tf_col: float,
    k_col: float,
    h_col: float,
    lb: float,
    Pr_col: float = 0.0,
    Py_col: float = 0.0,
    bcf: float = 0.0,
    near_member_end: bool = False,
) -> ColumnSideCheck:
    """Run the AISC 360-16 J10 family at a beam-to-column moment connection.

    Checks, in the order they usually govern for a PEB haunch:

    * **J10.1 flange local bending**   — column flange bends under the bolt
      tension; drives the need for a transverse stiffener
    * **J10.2 web local yielding**     — web crushes at the flange force
    * **J10.3 web local crippling**    — web buckles locally
    * **J10.5 web compression buckling** — full-depth web buckling
    * **J10.6 panel zone shear**       — web shears in the joint panel

    Parameters
    ----------
    Mu:
        Factored beam moment at the column face, kNm.
    d_beam, tf_beam:
        Beam depth and flange thickness, mm — set the flange force lever.
    d_col, tw_col, tf_col, k_col, h_col:
        Column depth, web thickness, flange thickness, k distance, and
        clear web depth, mm.
    lb:
        Bearing length of the concentrated force, mm.  For a bolted end
        plate this is the beam flange thickness plus the weld leg, roughly.
    Pr_col, Py_col:
        Column required axial force and axial yield strength, kN.
        Needed for panel zone shear; leave zero to skip that check.
    bcf:
        Column flange width, mm.  Enables the panel-zone flange term.

    Returns
    -------
    ColumnSideCheck — including whether a stiffener or doubler is required.
    """
    for label, v in (("d_beam", d_beam), ("tf_beam", tf_beam),
                     ("d_col", d_col), ("tw_col", tw_col),
                     ("tf_col", tf_col), ("k_col", k_col),
                     ("h_col", h_col), ("lb", lb)):
        if v <= 0:
            raise GeometryError(f"{label} must be > 0, got {v}")

    Puf = flange_force_from_moment(Mu, d_beam, tf_beam)

    Fy_web, _ = get_grade(col_grade, tw_col)
    Fy_flange, _ = get_grade(col_grade, tf_col)

    # -> imperial for pyaisc360
    tw_in = _to_imp(length_mm=tw_col)
    tf_in = _to_imp(length_mm=tf_col)
    d_in = _to_imp(length_mm=d_col)
    k_in = _to_imp(length_mm=k_col)
    h_in = _to_imp(length_mm=h_col)
    lb_in = _to_imp(length_mm=lb)
    Fyw_ksi = _to_imp(stress_MPa=Fy_web)
    Fyf_ksi = _to_imp(stress_MPa=Fy_flange)
    E_ksi = _to_imp(stress_MPa=E_STEEL)

    raw: list[tuple[str, float]] = []

    r = _j.flange_local_bending(Fyf=Fyf_ksi, tf=tf_in,
                                near_member_end=near_member_end)
    raw.append(("J10.1 flange local bending", _to_si(force_kip=r.available)))

    r = _j.web_local_yielding(Fyw=Fyw_ksi, tw=tw_in, k=k_in, lb=lb_in,
                              near_member_end=near_member_end)
    raw.append(("J10.2 web local yielding", _to_si(force_kip=r.available)))

    r = _j.web_local_crippling(tw=tw_in, tf=tf_in, d=d_in, lb=lb_in,
                               E=E_ksi, Fyw=Fyw_ksi,
                               near_member_end=near_member_end)
    raw.append(("J10.3 web local crippling", _to_si(force_kip=r.available)))

    r = _j.web_compression_buckling(tw=tw_in, h=h_in, E=E_ksi, Fyw=Fyw_ksi,
                                    near_member_end=near_member_end)
    raw.append(("J10.5 web compression buckling",
                _to_si(force_kip=r.available)))

    panel_zone_cap: float | None = None
    if Py_col > 0:
        r = _j.panel_zone_shear(
            Fy=Fyw_ksi, dc=d_in, tw=tw_in,
            Pr=_to_imp(force_kN=Pr_col), Py=_to_imp(force_kN=Py_col),
            bcf=_to_imp(length_mm=bcf) if bcf else 0.0,
            tcf=tf_in, db=_to_imp(length_mm=d_beam),
        )
        panel_zone_cap = _to_si(force_kip=r.available)
        raw.append(("J10.6 panel zone shear", panel_zone_cap))

    checks = tuple(
        (name, cap, Puf / cap if cap > 0 else math.inf) for name, cap in raw
    )
    governing, _, util = max(checks, key=lambda t: t[2])

    # Which remedy does the failing check call for?
    failing = {n for n, _, u in checks if u > 1.0}
    stiffener_states = {
        "J10.1 flange local bending",
        "J10.2 web local yielding",
        "J10.3 web local crippling",
        "J10.5 web compression buckling",
    }
    needs_stiff = bool(failing & stiffener_states)
    needs_doubler = "J10.6 panel zone shear" in failing

    if not failing:
        remedy = "none — column adequate as-is"
    else:
        parts = []
        if needs_stiff:
            parts.append("transverse stiffeners (continuity plates) at both "
                         "beam flange levels")
        if needs_doubler:
            parts.append("web doubler plate in the panel zone")
        remedy = " + ".join(parts)

    return ColumnSideCheck(
        Puf=Puf, checks=checks, governing=governing, utilization=util,
        passed=not failing,
        needs_transverse_stiffener=needs_stiff,
        needs_web_doubler=needs_doubler,
        remedy=remedy,
    )


# ======================================================================
# Invariants
# ======================================================================

def _run_invariants() -> list[tuple[str, bool, str]]:
    res: list[tuple[str, bool, str]] = []

    def chk(name: str, ok: bool, detail: str = "") -> None:
        res.append((name, ok, detail))

    # --- welds ---
    w_long = size_fillet_weld(demand=300.0, length=400.0, t_part1=20.0,
                              t_part2=12.0, base_grade="S355JR", theta=0.0)
    w_tran = size_fillet_weld(demand=300.0, length=400.0, t_part1=20.0,
                              t_part2=12.0, base_grade="S355JR", theta=90.0)
    chk("transverse weld stronger than longitudinal",
        w_tran.capacity_weld > w_long.capacity_weld,
        f"{w_tran.capacity_weld:.0f} > {w_long.capacity_weld:.0f} kN")

    chk("directional factor = 1.5 at theta=90",
        math.isclose(w_tran.directional_factor, 1.5, rel_tol=1e-6),
        f"{w_tran.directional_factor:.4f}")
    chk("directional factor = 1.0 at theta=0",
        math.isclose(w_long.directional_factor, 1.0, rel_tol=1e-9),
        f"{w_long.directional_factor:.4f}")

    chk("selected leg within J2.4 / J2.2b limits",
        min_weld_size(20.0) <= w_long.leg <= max_weld_size(12.0),
        f"{min_weld_size(20.0):.0f} <= {w_long.leg:.0f} <= "
        f"{max_weld_size(12.0):.0f} mm")

    w_short = size_fillet_weld(demand=300.0, length=200.0, t_part1=20.0,
                               t_part2=12.0, base_grade="S355JR")
    chk("half length -> larger or equal leg", w_short.leg >= w_long.leg,
        f"{w_short.leg:.0f} >= {w_long.leg:.0f} mm")

    chk("throat = 0.707 * leg",
        math.isclose(w_long.throat, 0.707 * w_long.leg, rel_tol=1e-9),
        f"{w_long.throat:.3f} mm")

    # --- J10 ---
    c = check_column_side(
        Mu=250.0, d_beam=500.0, tf_beam=16.0, col_grade="S355JR",
        d_col=400.0, tw_col=9.0, tf_col=14.0, k_col=30.0, h_col=340.0,
        lb=24.0,
    )
    chk("Puf = Mu/(d-tf)",
        math.isclose(c.Puf, 250.0 * 1000.0 / (500.0 - 16.0), rel_tol=1e-9),
        f"{c.Puf:.1f} kN")

    c2 = check_column_side(
        Mu=500.0, d_beam=500.0, tf_beam=16.0, col_grade="S355JR",
        d_col=400.0, tw_col=9.0, tf_col=14.0, k_col=30.0, h_col=340.0,
        lb=24.0,
    )
    chk("2x moment -> 2x utilization",
        math.isclose(c2.utilization / c.utilization, 2.0, rel_tol=1e-6),
        f"{c.utilization:.3f} -> {c2.utilization:.3f}")

    c_thick = check_column_side(
        Mu=250.0, d_beam=500.0, tf_beam=16.0, col_grade="S355JR",
        d_col=400.0, tw_col=20.0, tf_col=30.0, k_col=45.0, h_col=310.0,
        lb=24.0,
    )
    chk("thicker column -> lower utilization",
        c_thick.utilization < c.utilization,
        f"{c_thick.utilization:.3f} < {c.utilization:.3f}")

    chk("failing check triggers a remedy",
        (c.passed and c.remedy.startswith("none"))
        or (not c.passed and "stiffener" in c.remedy or "doubler" in c.remedy),
        c.remedy[:44])

    return res


if __name__ == "__main__":
    print("=" * 70)
    print("TASK #2 — WELD DESIGN (AISC 360-16 Sect. J2)")
    print("=" * 70)

    print("\nFlange weld (transverse, theta = 90):")
    wf = size_fillet_weld(demand=516.5, length=250.0, t_part1=20.0,
                          t_part2=16.0, base_grade="S355JR", theta=90.0)
    print(wf.report())

    print("\nWeb weld (longitudinal, theta = 0):")
    ww = size_fillet_weld(demand=45.0, length=400.0, t_part1=20.0,
                          t_part2=10.0, base_grade="S355JR", theta=0.0)
    print(ww.report())

    print("\n" + "=" * 70)
    print("TASK #3 — COLUMN-SIDE CHECKS (AISC 360-16 Sect. J10)")
    print("=" * 70)
    print()
    c = check_column_side(
        Mu=250.0, d_beam=500.0, tf_beam=16.0, col_grade="S355JR",
        d_col=400.0, tw_col=9.0, tf_col=14.0, k_col=30.0, h_col=340.0,
        lb=24.0, Pr_col=200.0, Py_col=2500.0, bcf=180.0,
    )
    print(c.report())

    print("\n" + "=" * 70)
    print("INVARIANT TESTS")
    print("=" * 70)
    rows = _run_invariants()
    for name, ok, detail in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<42s} {detail}")
    print(f"\n  {sum(1 for _, ok, _ in rows if ok)}/{len(rows)} passed")
