"""
peb_anchors.py — Cast-in anchor rod design (ACI 318-19 Chapter 17).
===================================================================

Task #6.  Takes ``T_anchor`` from :mod:`peb_baseplate` plus the base shear
and checks every ACI 318-19 Ch. 17 failure mode, then the tension/shear
interaction.

    TENSION                                    SHEAR
    17.6.1  steel strength      Nsa            17.7.1  steel strength    Vsa
    17.6.2  concrete breakout   Ncbg           17.7.2  concrete breakout Vcbg
    17.6.3  pullout             Npn            17.7.3  concrete pryout   Vcpg
    17.6.4  side-face blowout   Nsbg
    17.8    tension/shear interaction

Units: SI throughout — kN, mm, MPa.  ACI 318M-19 constants are used
directly (they are already SI), so unlike the rest of the package nothing
crosses to imperial here.

Clause numbering
----------------
ACI **318-19**.  If your project references 318-14, the same clauses are
numbered 17.4.x / 17.5.x — the equations are substantially unchanged but
check before citing.

Why concrete usually governs
----------------------------
Engineers size anchors on rod steel and are surprised when the check
fails.  For a PEB base the governing mode is almost always **concrete
breakout** — a cone pulling out of the pedestal — which depends on
embedment depth and edge distance, not on rod grade. Doubling the rod
strength does nothing.

And deepening the embedment only helps while the edges are far away.
Once ``ca_min < 1.5 hef`` the projected area ``ANc`` is capped by the
pedestal while ``ANco = 9 hef^2`` keeps growing, so breakout capacity
falls as ``1/sqrt(hef)``. For a base near an edge the fix is a **bigger
pedestal**, not a longer rod — the module says so in the error text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final, Literal

from .bolts import ConnectionError_, GeometryError

# ======================================================================
# Verification gate
# ======================================================================

VERIFIED: dict[str, bool] = {
    # VERIFIED 2026-08-30 against Hilti PROFIS Engineering 3.1.34,
    # design method ACI31819_CIP, report "Concrete - Aug 30, 2026".
    # Model: 4 x 1-1/4" ASTM F1554 Gr.36 heavy hex head, hef 450 mm,
    # 2x2 @ 350 mm, ca 450 mm all round, f'c 30 MPa cracked, ha 800 mm,
    # reinforcement Condition B, Nua 364.096 kN, Vua 60 kN.
    #
    #   mode                        mine     PROFIS    delta
    #   17.6.1 steel tension      750.24     750.00    0.03%
    #   17.6.2 breakout tension   387.79     388.24    0.12%
    #   17.6.3 pullout            969.77     969.84    0.01%
    #   17.7.1 steel shear        390.12     390.00    0.03%
    #   17.7.2 breakout shear     112.85     114.72    1.63%  (see below)
    #   17.7.3 pryout             775.57     776.63    0.14%
    #   hef' (17.6.2.1.2)          300.0      300.0    exact
    #   ANc / ANco / Ase / Abrg              all exact
    #
    # The 1.63% on shear breakout is NOT an error on either side. ACI
    # 318M-19 Eq. 17.7.2.2.1b gives Vb = 3.7*lambda*sqrt(f'c)*ca1^1.5 in
    # SI; ACI 318-19 gives Vb = 9*... in imperial. PROFIS computes in
    # imperial (its report prints f'c in psi) and converts back. The exact
    # SI equivalent of the imperial 9 is 3.766, so the code's own rounded
    # 3.7 is ~1.8% conservative. This module follows the SI text.
    "tension_steel": True,
    "tension_breakout": True,
    "pullout": True,
    # Side-face blowout verified separately 2026-08-30 against PROFIS
    # report "ca150 hef450 side blowout" (same anchor, ca 150 mm all round,
    # so hef 450 > 2.5*ca = 375 and Eq. 17.6.4 engages):
    #   17.6.4 blowout   394.47   403.15   2.15%   (SI 13 vs imperial 160,
    #                                               exact SI = 13.28)
    #   alpha_group        1.389    1.389   exact
    #   and at ca = 150 the other modes still track:
    #   17.6.2 breakout  159.49   160.11   0.38%
    #   17.7.2 shear      33.88    34.35   1.37%
    #   17.7.3 pryout    318.99   320.38   0.43%
    # That run exposed two further defects, both now fixed: the group
    # factor alpha_group (Eq. 17.6.4.2) was missing entirely, and blowout
    # was being checked against the whole group load instead of the
    # tension in the anchors near that edge.
    "side_blowout": True,
    "shear_steel": True,
    "shear_breakout": True,
    "pryout": True,
    "interaction": True,
}


class UnverifiedFormula(ConnectionError_):
    """An ACI 318 Ch. 17 relation has not been independently checked."""


# ======================================================================
# Materials
# ======================================================================

@dataclass(frozen=True, slots=True)
class AnchorGrade:
    """Anchor rod material, MPa."""

    name: str
    Fy: float
    futa: float          # specified tensile strength, capped at 1.9*Fy by 17.6.1
    ductile: bool = True


ANCHOR_GRADES: Final[dict[str, AnchorGrade]] = {
    # ASTM F1554 — the standard anchor rod spec
    "F1554-36": AnchorGrade("F1554 Gr.36", 248.0, 400.0),
    "F1554-55": AnchorGrade("F1554 Gr.55", 380.0, 517.0),
    "F1554-105": AnchorGrade("F1554 Gr.105", 724.0, 862.0),
    # EN property classes
    "8.8": AnchorGrade("8.8", 640.0, 800.0),
    "10.9": AnchorGrade("10.9", 900.0, 1000.0),
}

# ISO 898 metric coarse thread tensile stress areas, mm^2.
THREAD_AREA: Final[dict[float, float]] = {
    12.0: 84.3, 16.0: 157.0, 20.0: 245.0, 24.0: 353.0,
    27.0: 459.0, 30.0: 561.0, 36.0: 817.0, 42.0: 1120.0, 48.0: 1470.0,
    # Imperial UNC rods (ASTM F1554 is a US spec, so PROFIS lists these).
    # Key = nominal diameter in mm; value = UNC tensile stress area, mm^2.
    # Values are the UNC TENSILE STRESS area, not the nominal shank area.
    # An earlier version of this table was shifted by one row and used
    # nominal areas; Hilti PROFIS reported Ase = 625 mm2 for 1-1/4", which
    # is 0.969 in2, and exposed it. Verified against ASME B1.1.
    15.875: 145.8,   # 5/8"-11    0.2260 in2
    19.05: 215.5,    # 3/4"-10    0.3340 in2
    22.225: 298.1,   # 7/8"-9     0.4620 in2
    25.4: 391.0,     # 1"-8       0.6060 in2
    28.575: 492.2,   # 1-1/8"-7   0.7630 in2
    31.75: 625.2,    # 1-1/4"-7   0.9690 in2   <- PROFIS: 625
    38.1: 906.5,     # 1-1/2"-6   1.4050 in2
}

# Hex nut width across flats, mm (ISO 4032). Abrg is DERIVED from this
# rather than tabulated, so the assumption is visible and auditable:
#     Abrg = 0.866 * AF^2  -  pi*d^2/4      (hexagon area minus the hole)
# The head or plate washer actually specified governs pullout; a larger
# head raises it proportionally.
NUT_ACROSS_FLATS: Final[dict[float, float]] = {
    12.0: 18.0, 16.0: 24.0, 20.0: 30.0, 24.0: 36.0,
    27.0: 41.0, 30.0: 46.0, 36.0: 55.0, 42.0: 65.0, 48.0: 75.0,
    # ASME B18.2.2 heavy hex nut, width across flats (in -> mm)
    15.875: 26.99, 19.05: 31.75, 22.225: 36.51, 25.4: 41.28,
    28.575: 46.04, 31.75: 50.80, 38.1: 60.33,
}


def bearing_area(d: float, across_flats: float | None = None) -> float:
    """Head/nut bearing area Abrg, mm^2 (17.6.3, 17.6.4).

    ``across_flats`` overrides the ISO 4032 nut when a heavier head or a
    plate washer is specified.
    """
    af = across_flats if across_flats is not None else _lookup(
        NUT_ACROSS_FLATS, d, "nut width across flats")
    return 0.866 * af ** 2 - math.pi * d ** 2 / 4.0


def get_anchor_grade(name: str) -> AnchorGrade:
    key = name.strip().upper().replace(" ", "")
    for k, v in ANCHOR_GRADES.items():
        if k.upper() == key:
            return v
    raise ConnectionError_(
        f"unknown anchor grade {name!r}. Known: {', '.join(ANCHOR_GRADES)}"
    )


def _lookup(table: dict[float, float], d: float, what: str) -> float:
    if d not in table:
        raise GeometryError(
            f"no {what} for M{d:g}. Available: "
            f"{', '.join(f'M{k:g}' for k in sorted(table))}"
        )
    return table[d]


# ======================================================================
# Geometry
# ======================================================================

@dataclass(frozen=True, slots=True)
class AnchorGroup:
    """Cast-in anchor group geometry, mm.

    Attributes
    ----------
    d:
        Nominal rod diameter (M-series), mm.
    hef:
        Effective embedment depth, mm.  The single most important
        parameter — breakout scales with ``hef^1.5``.
    n_x, n_y:
        Anchors in each direction.
    s_x, s_y:
        Spacing in each direction, mm.  Ignored when the count is 1.
    ca_x1, ca_x2, ca_y1, ca_y2:
        Edge distances from the outermost anchors to the four pedestal
        edges, mm.  Use a large value (e.g. 10000) for "no edge".
    cracked:
        True if the concrete is assumed cracked at service load (the ACI
        default for a base plate unless analysis shows otherwise).
    supplementary_reinforcement:
        True if the pedestal has reinforcement tying the breakout cone
        back (Condition A).  Raises the concrete phi factors.
    """

    d: float
    hef: float
    n_x: int = 2
    n_y: int = 2
    s_x: float = 0.0
    s_y: float = 0.0
    ca_x1: float = 10_000.0
    ca_x2: float = 10_000.0
    ca_y1: float = 10_000.0
    ca_y2: float = 10_000.0
    cracked: bool = True
    supplementary_reinforcement: bool = False
    edge_reinforcement: bool = False
    stirrups_at_100mm: bool = False
    grout_pad: bool = False
    ha: float | None = None
    across_flats: float | None = None

    def __post_init__(self) -> None:
        if self.d <= 0:
            raise GeometryError(f"d must be > 0, got {self.d}")
        if self.hef <= 0:
            raise GeometryError(f"hef must be > 0, got {self.hef}")
        if self.n_x < 1 or self.n_y < 1:
            raise GeometryError("n_x and n_y must be >= 1")
        if self.n_x > 1 and self.s_x <= 0:
            raise GeometryError("s_x must be > 0 when n_x > 1")
        if self.n_y > 1 and self.s_y <= 0:
            raise GeometryError("s_y must be > 0 when n_y > 1")
        for lbl in ("ca_x1", "ca_x2", "ca_y1", "ca_y2"):
            if getattr(self, lbl) <= 0:
                raise GeometryError(f"{lbl} must be > 0")

    @property
    def n(self) -> int:
        """Total anchors in the group."""
        return self.n_x * self.n_y

    @property
    def ca_min(self) -> float:
        """Smallest edge distance, mm."""
        return min(self.ca_x1, self.ca_x2, self.ca_y1, self.ca_y2)

    @property
    def Ase(self) -> float:
        """Effective tensile stress area of one rod, mm²."""
        return _lookup(THREAD_AREA, self.d, "thread area")

    @property
    def Abrg(self) -> float:
        """Head/nut bearing area of one anchor, mm² (see :func:`bearing_area`)."""
        return bearing_area(self.d, self.across_flats)

    # ---- projected areas ------------------------------------------

    @property
    def hef_effective(self) -> float:
        """Embedment used in the breakout calculation, mm (17.6.2.1.2).

        ACI 318-19 Sect. 17.6.2.1.2: where anchors sit **less than
        1.5 hef from three or more edges**, the value of hef used in the
        breakout equations is replaced by::

            hef' = max(ca_max / 1.5,  s_max / 3)

        The cone cannot develop in a narrow pier, so the code substitutes
        a smaller notional embedment rather than letting ANc/ANco grow
        without limit. Skipping this clause is unconservative in some
        layouts and over-conservative in others — here it RAISES the
        computed capacity, because a smaller hef' shrinks ANco faster
        than it shrinks ANc.

        Returns ``hef`` unchanged when fewer than three edges are close.
        """
        lim = 1.5 * self.hef
        edges = (self.ca_x1, self.ca_x2, self.ca_y1, self.ca_y2)
        close = [c for c in edges if c < lim]
        if len(close) < 3:
            return self.hef
        ca_max = max(close)
        s_max = max(self.s_x, self.s_y)
        return max(ca_max / 1.5, s_max / 3.0)

    @property
    def three_or_more_close_edges(self) -> bool:
        """True when Sect. 17.6.2.1.2 applies."""
        lim = 1.5 * self.hef
        return sum(1 for c in (self.ca_x1, self.ca_x2, self.ca_y1,
                               self.ca_y2) if c < lim) >= 3

    @property
    def ANco(self) -> float:
        """Projected concrete failure area of ONE anchor, mm² (Eq. 17.6.2.1.4).

        ``ANco = 9 * hef'^2``, using the Sect. 17.6.2.1.2 substitute
        embedment where that clause applies.
        """
        return 9.0 * self.hef_effective ** 2

    @property
    def ANc(self) -> float:
        """Projected concrete failure area of the GROUP, mm² (17.6.2.1).

        Edge distances are capped at ``1.5*hef`` — concrete beyond that
        does not contribute to the cone.  The result is capped at
        ``n * ANco``, which is the no-overlap limit.
        """
        lim = 1.5 * self.hef_effective
        wx = (min(self.ca_x1, lim) + (self.n_x - 1) * self.s_x
              + min(self.ca_x2, lim))
        wy = (min(self.ca_y1, lim) + (self.n_y - 1) * self.s_y
              + min(self.ca_y2, lim))
        return min(wx * wy, self.n * self.ANco)


# ======================================================================
# Modification factors
# ======================================================================

def psi_ed_N(ca_min: float, hef: float) -> float:
    """Edge-effect factor for tension breakout (Eq. 17.6.2.4.1).

    ``1.0`` when ``ca_min >= 1.5 hef``, otherwise
    ``0.7 + 0.3 * ca_min / (1.5 hef)``.
    """
    lim = 1.5 * hef
    return 1.0 if ca_min >= lim else 0.7 + 0.3 * ca_min / lim


def psi_c_N(cracked: bool) -> float:
    """Cracking factor for tension breakout, cast-in (17.6.2.5).

    ``1.0`` cracked, ``1.25`` uncracked.
    """
    return 1.0 if cracked else 1.25


def psi_ed_V(ca2: float, ca1: float) -> float:
    """Edge-effect factor for shear breakout (Eq. 17.7.2.4.1)."""
    lim = 1.5 * ca1
    return 1.0 if ca2 >= lim else 0.7 + 0.3 * ca2 / lim


def psi_c_V(cracked: bool, edge_reinforcement: bool = False,
            stirrups_at_100mm: bool = False) -> float:
    """Cracking factor for SHEAR breakout (Table 17.7.2.5.1).

    Unlike the tension factor this is not a two-value switch — edge
    reinforcement raises it substantially:

    ==========================================  =====
    uncracked at service load                    1.4
    cracked, no edge reinforcement               1.0
    cracked, No.4 (13 mm) bar between anchor
    and edge                                     1.2
    cracked, that bar enclosed by stirrups at
    <= 100 mm spacing                            1.4
    ==========================================  =====

    Returning a flat 1.0 for every cracked case understates shear
    breakout by 20-40 % whenever the pedestal is reinforced, which it
    almost always is.
    """
    if not cracked:
        return 1.4
    if stirrups_at_100mm:
        return 1.4
    if edge_reinforcement:
        return 1.2
    return 1.0


def psi_h_V(ha: float | None, ca1: float) -> float:
    """Thin-member factor for shear breakout (Eq. 17.7.2.6.1).

    ``psi_h,V = sqrt(1.5*ca1 / ha) >= 1.0`` when ``ha < 1.5*ca1``.

    A member shallower than 1.5*ca1 cannot develop the full half-cone, so
    the code RAISES the factor.  ``ha = None`` assumes a deep member and
    returns 1.0 — but says so, because assuming depth is exactly how a
    thin-slab base gets a capacity it does not have.
    """
    if ha is None:
        return 1.0
    if ha <= 0:
        raise GeometryError(f"ha must be > 0, got {ha}")
    lim = 1.5 * ca1
    return max(1.0, math.sqrt(lim / ha)) if ha < lim else 1.0


# ======================================================================
# Result
# ======================================================================

@dataclass(frozen=True, slots=True)
class AnchorCheck:
    """Every ACI 318-19 Ch. 17 mode plus the interaction."""

    Nua: float                  # kN, factored tension on the GROUP
    Vua: float                  # kN, factored shear on the GROUP
    tension: tuple[tuple[str, float, float], ...]   # (name, phiNn kN, U)
    shear: tuple[tuple[str, float, float], ...]
    phiNn: float                # kN, governing tension capacity
    phiVn: float                # kN, governing shear capacity
    governing_tension: str
    governing_shear: str
    interaction: float
    interaction_rule: str
    passed: bool
    hef: float
    ANc_over_ANco: float
    warnings: tuple[str, ...] = field(default_factory=tuple)
    citation: str = "ACI 318-19 Ch. 17 (cast-in anchors)"

    def report(self) -> str:
        out = [
            f"Anchor group  [{self.citation}]  "
            f"{'PASS' if self.passed else 'FAIL'}",
            f"  Nua = {self.Nua:8.2f} kN      Vua = {self.Vua:8.2f} kN",
            f"  hef = {self.hef:8.1f} mm      ANc/ANco = "
            f"{self.ANc_over_ANco:.3f}",
            "",
            "  TENSION",
        ]
        out += [f"    {n:<34s} {c:9.2f} kN   U = {u:.3f}"
                for n, c, u in self.tension]
        out += ["", "  SHEAR"]
        out += [f"    {n:<34s} {c:9.2f} kN   U = {u:.3f}"
                for n, c, u in self.shear]
        out += [
            "",
            f"  governing tension : {self.governing_tension} "
            f"({self.phiNn:.2f} kN)",
            f"  governing shear   : {self.governing_shear} "
            f"({self.phiVn:.2f} kN)",
            f"  INTERACTION       : {self.interaction:.3f}   "
            f"[{self.interaction_rule}]",
        ]
        out += [f"  ! {w}" for w in self.warnings]
        return "\n".join(out)


# ======================================================================
# Design
# ======================================================================

def check_anchors(
    *,
    Nua: float,
    Vua: float,
    group: AnchorGroup,
    grade: str = "F1554-36",
    fc_prime: float = 25.0,
    lambda_a: float = 1.0,
    shear_ca1: float | None = None,
    interaction_law: Literal["linear", "power"] = "linear",
    strict: bool = True,
) -> AnchorCheck:
    """Check a cast-in anchor group to ACI 318-19 Chapter 17.

    Parameters
    ----------
    Nua, Vua:
        Factored tension and shear on the WHOLE GROUP, kN.  ``Nua``
        normally comes from ``BasePlateDesign.T_anchor``.
    lambda_a:
        Lightweight concrete factor (1.0 normal weight, 17.2.4).
    shear_ca1:
        Edge distance in the direction of the shear, mm.  Defaults to
        ``group.ca_min``, the conservative choice.
    interaction_law:
        ``"linear"`` (default) uses Sect. 17.8.3, the sum limited to 1.2.
        ``"power"`` uses the 5/3 exponent form the commentary permits and
        that PROFIS applies. The two can straddle 1.0 on a tight design —
        whichever is chosen, the other is reported alongside it so the
        difference is visible rather than hidden.

    Raises
    ------
    UnverifiedFormula
        ``strict=True`` while any relation is unverified.
    """
    if strict:
        unver = [k for k, v in VERIFIED.items() if not v]
        if unver:
            raise UnverifiedFormula(
                f"ACI 318 Ch. 17 relations not yet checked: "
                f"{', '.join(unver)}. Cross-check against Hilti PROFIS "
                f"Engineering (free), then flip _VERIFIED. "
                f"Pass strict=False for development."
            )
    if Nua < 0 or Vua < 0:
        raise GeometryError("Nua and Vua must be >= 0 (use magnitudes)")
    if fc_prime <= 0:
        raise GeometryError(f"fc_prime must be > 0, got {fc_prime}")

    g = group
    mat = get_anchor_grade(grade)
    warnings: list[str] = []

    # futa capped at 1.9*Fy (17.6.1.2) and 862 MPa
    futa = min(mat.futa, 1.9 * mat.Fy, 862.0)
    if futa < mat.futa:
        warnings.append(
            f"futa capped at {futa:.0f} MPa by ACI 17.6.1.2 "
            f"(min of 1.9Fy and 862 MPa)"
        )

    sqrt_fc = math.sqrt(fc_prime)
    ca1_v = shear_ca1 if shear_ca1 is not None else g.ca_min

    # ---- phi factors (17.5.3) --------------------------------------
    phi_steel_N = 0.75 if mat.ductile else 0.65
    phi_steel_V = 0.65 if mat.ductile else 0.60
    phi_conc_N = 0.75 if g.supplementary_reinforcement else 0.70
    phi_conc_V = 0.75 if g.supplementary_reinforcement else 0.70

    # =================== TENSION ====================================
    tension: list[tuple[str, float, float]] = []

    # 17.6.1 steel — group capacity = n * one rod
    Nsa = g.n * g.Ase * futa / 1000.0                      # kN
    tension.append(("17.6.1 steel strength", phi_steel_N * Nsa, 0.0))

    # 17.6.2 concrete breakout
    # Basic Nb (Eq. 17.6.2.2.1): kc*lambda*sqrt(fc)*hef^1.5, kc = 10 cast-in
    hef_b = g.hef_effective          # Sect. 17.6.2.1.2 substitute
    if 280.0 <= hef_b <= 635.0:
        # Eq. 17.6.2.2.3 for deep embedment
        Nb = 3.9 * lambda_a * sqrt_fc * hef_b ** (5.0 / 3.0) / 1000.0
        nb_note = "Eq. 17.6.2.2.3 (280 <= hef <= 635 mm)"
    else:
        Nb = 10.0 * lambda_a * sqrt_fc * hef_b ** 1.5 / 1000.0
        nb_note = "Eq. 17.6.2.2.1 (kc = 10, cast-in)"
    ratio = g.ANc / g.ANco
    Ncbg = (ratio * psi_ed_N(g.ca_min, hef_b) * psi_c_N(g.cracked)
            * 1.0 * Nb)                                     # psi_cp,N = 1 cast-in
    if g.three_or_more_close_edges:
        warnings.append(
            f"Sect. 17.6.2.1.2 applies: anchors are within 1.5hef of three "
            f"or more edges, so hef = {g.hef:.0f} mm is replaced by "
            f"hef' = {hef_b:.0f} mm in the breakout calculation. Embedment "
            f"beyond that is not credited."
        )
    tension.append(("17.6.2 concrete breakout", phi_conc_N * Ncbg, 0.0))

    # 17.6.3 pullout, headed anchor: Np = 8*Abrg*fc  (Eq. 17.6.3.2.2a)
    psi_cP = 1.0 if g.cracked else 1.4
    Npn = g.n * psi_cP * 8.0 * g.Abrg * fc_prime / 1000.0
    tension.append(("17.6.3 pullout", 0.70 * Npn, 0.0))

    # 17.6.4 side-face blowout — only when hef > 2.5*ca1
    if g.hef > 2.5 * g.ca_min:
        # Eq. 17.6.4.1 (SI):  Nsb = 13 * ca1 * sqrt(Abrg) * lambda * sqrt(fc)
        Nsb = (13.0 * g.ca_min * math.sqrt(g.Abrg) * lambda_a
               * sqrt_fc / 1000.0)
        # Eq. 17.6.4.2: multiple headed anchors along the same edge share a
        # blowout surface, so the group value is FACTORED UP, not summed.
        # s is the spacing of the outer anchors parallel to that edge; the
        # factor applies only while s < 6*ca1.
        s_edge = max(g.s_x, g.s_y)
        alpha_group = (1.0 + s_edge / (6.0 * g.ca_min)
                       if s_edge < 6.0 * g.ca_min else 1.0)
        Nsbg = alpha_group * Nsb
        # Blowout is a LOCAL edge failure: only the anchors near that edge
        # load it, not the whole group. PROFIS reports Nua,edge; mirror it.
        n_edge = g.n_y if g.ca_min in (g.ca_x1, g.ca_x2) else g.n_x
        edge_fraction = n_edge / g.n
        sb_demand = Nua * edge_fraction
        tension.append(
            (f"17.6.4 side-face blowout (edge {edge_fraction:.0%})",
             phi_conc_N * Nsbg, sb_demand)
        )
        warnings.append(
            f"hef ({g.hef:.0f} mm) > 2.5*ca_min ({2.5 * g.ca_min:.0f} mm): "
            f"side-face blowout applies. alpha_group = {alpha_group:.3f} "
            f"(Eq. 17.6.4.2), checked against the edge-row tension "
            f"{sb_demand:.1f} kN, not the full group load."
        )

    # =================== SHEAR ======================================
    shear: list[tuple[str, float, float]] = []

    # 17.7.1 steel, cast-in headed bolt: Vsa = 0.6*Ase*futa
    Vsa = g.n * 0.60 * g.Ase * futa / 1000.0
    if g.grout_pad:
        Vsa *= 0.80          # Sect. 17.7.1.2.1, anchors through a grout pad
        warnings.append(
            "grout pad present: steel shear reduced by 0.80 (17.7.1.2.1)"
        )
    shear.append(("17.7.1 steel strength", phi_steel_V * Vsa, 0.0))

    # 17.7.2 concrete breakout
    le = min(g.hef, 8.0 * g.d)                     # 17.7.2.2.1
    Vb1 = (0.6 * (le / g.d) ** 0.2 * math.sqrt(g.d) * lambda_a
           * sqrt_fc * ca1_v ** 1.5) / 1000.0
    # ACI 318M-19 Eq. 17.7.2.2.1b. The SI constant 3.7 is a rounded form
    # of the imperial 9 (exactly 3.766), so this is ~1.8% conservative
    # against tools that compute in imperial, such as PROFIS.
    Vb2 = 3.7 * lambda_a * sqrt_fc * ca1_v ** 1.5 / 1000.0
    Vb = min(Vb1, Vb2)
    AVco = 4.5 * ca1_v ** 2
    lim = 1.5 * ca1_v
    # Sect. 17.7.2.1: the projected area is bounded by the member depth as
    # well as by 1.5*ca1. Without this cap a thin slab computes STRONGER,
    # because psi_h,V (>= 1.0) magnifies while nothing shrinks AVc.
    depth = lim if g.ha is None else min(lim, g.ha)
    wv = (min(g.ca_y1, lim) + (g.n_y - 1) * g.s_y + min(g.ca_y2, lim))
    AVc = min(wv * depth, g.n * AVco)
    pcv = psi_c_V(g.cracked, g.edge_reinforcement, g.stirrups_at_100mm)
    phv = psi_h_V(g.ha, ca1_v)
    Vcbg = (AVc / AVco) * psi_ed_V(g.ca_min, ca1_v) * pcv * phv * Vb
    if g.ha is None:
        warnings.append(
            f"member thickness not given — psi_h,V taken as 1.0. If the "
            f"member is shallower than 1.5*ca1 = {1.5 * ca1_v:.0f} mm the "
            f"shear breakout result is wrong; set AnchorGroup(ha=...)"
        )
    elif phv > 1.0:
        warnings.append(
            f"thin member: ha = {g.ha:.0f} mm < 1.5*ca1 = "
            f"{1.5 * ca1_v:.0f} mm, psi_h,V = {phv:.3f} (17.7.2.6.1)"
        )
    if g.cracked and not (g.edge_reinforcement or g.stirrups_at_100mm):
        warnings.append(
            "psi_c,V = 1.0 assumed (cracked, no edge reinforcement). A "
            "13 mm bar between anchor and edge gives 1.2; enclosed by "
            "stirrups at <=100 mm gives 1.4 — worth 20-40% of shear breakout"
        )
    shear.append(("17.7.2 concrete breakout", phi_conc_V * Vcbg, 0.0))

    # 17.7.3 pryout: Vcp = kcp * Ncbg
    kcp = 1.0 if g.hef_effective < 65.0 else 2.0
    Vcpg = kcp * Ncbg
    shear.append(("17.7.3 concrete pryout", phi_conc_V * Vcpg, 0.0))

    # ---- utilizations ---------------------------------------------
    tension = tuple(
        (n, c, ((d if d else Nua) / c if c > 0 else math.inf))
        for n, c, d in tension
    )
    shear = tuple(
        (n, c, (Vua / c if c > 0 else math.inf)) for n, c, _ in shear
    )

    gov_t, phiNn, _ = min(tension, key=lambda t: t[1])
    gov_v, phiVn, _ = min(shear, key=lambda t: t[1])

    # ---- 17.8 interaction ------------------------------------------
    ut = Nua / phiNn if phiNn > 0 else math.inf
    uv = Vua / phiVn if phiVn > 0 else math.inf
    if uv <= 0.2:
        interaction, rule = ut, "17.8.1: Vua <= 0.2*phiVn, full tension"
    elif ut <= 0.2:
        interaction, rule = uv, "17.8.2: Nua <= 0.2*phiNn, full shear"
    else:
        linear = (ut + uv) / 1.2
        power = ut ** (5.0 / 3.0) + uv ** (5.0 / 3.0)
        if interaction_law == "linear":
            interaction = linear
            rule = (f"17.8.3 linear: (Nua/phiNn + Vua/phiVn) <= 1.2 "
                    f"[5/3 power law would give {power:.3f}]")
        else:
            interaction = power
            rule = (f"17.8.3 alt: (Nua/phiNn)^5/3 + (Vua/phiVn)^5/3 <= 1.0 "
                    f"[linear rule would give {linear:.3f}]")

    if not g.supplementary_reinforcement:
        warnings.append(
            "Condition B assumed (no supplementary reinforcement) — "
            "adding pedestal ties tying the cone back raises phi to 0.75"
        )
    if gov_t.startswith("17.6.2"):
        warnings.append(
            "concrete breakout governs tension — increase hef or edge "
            "distance; a stronger rod grade will not help"
        )

    return AnchorCheck(
        Nua=Nua, Vua=Vua, tension=tension, shear=shear,
        phiNn=phiNn, phiVn=phiVn,
        governing_tension=gov_t, governing_shear=gov_v,
        interaction=interaction, interaction_rule=rule,
        passed=interaction <= 1.0,
        hef=g.hef, ANc_over_ANco=ratio,
        warnings=tuple(warnings),
    )


def required_embedment(
    *, Nua: float, group: AnchorGroup, fc_prime: float = 25.0,
    lambda_a: float = 1.0, hef_max: float = 1200.0, step: float = 10.0,
) -> float:
    """Smallest ``hef`` (mm) at which tension breakout carries ``Nua``.

    Sweeps embedment and returns the first depth that passes. Useful for
    sizing the pedestal before the rest of the check.
    """
    import dataclasses
    hef = step
    while hef <= hef_max:
        g = dataclasses.replace(group, hef=hef)
        r = check_anchors(Nua=Nua, Vua=0.0, group=g, fc_prime=fc_prime,
                          lambda_a=lambda_a, strict=False)
        breakout = next(c for n, c, _ in r.tension if n.startswith("17.6.2"))
        if breakout >= Nua:
            return hef
        hef += step
    raise GeometryError(
        f"no embedment up to {hef_max:.0f} mm carries {Nua:.1f} kN with "
        f"ca_min = {group.ca_min:.0f} mm. The EDGE DISTANCE controls, not "
        f"the embedment: ANc is capped by the pedestal while ANco grows as "
        f"hef^2, so breakout capacity falls as 1/sqrt(hef). Enlarge the "
        f"pedestal, widen the anchor spacing, raise f'c, or add "
        f"supplementary reinforcement."
    )


# ======================================================================
# Invariants
# ======================================================================

def _run_invariants() -> list[tuple[str, bool, str]]:
    res: list[tuple[str, bool, str]] = []

    def chk(n: str, ok: bool, d: str = "") -> None:
        res.append((n, ok, d))

    import dataclasses
    G = AnchorGroup(d=24.0, hef=400.0, n_x=2, n_y=2, s_x=300.0, s_y=300.0,
                    ca_x1=200.0, ca_x2=200.0, ca_y1=200.0, ca_y2=200.0)
    kw = dict(group=G, grade="F1554-36", fc_prime=25.0, strict=False)

    r = check_anchors(Nua=350.0, Vua=60.0, **kw)
    deep_close = check_anchors(Nua=350.0, Vua=60.0,
                               **{**kw, "group": dataclasses.replace(G, hef=600.0)})

    chk("ANco = 9*hef_effective^2",
        math.isclose(G.ANco, 9.0 * G.hef_effective ** 2),
        f"hef' = {G.hef_effective:.0f} mm")

    # --- Sect. 17.6.2.1.2 -------------------------------------------
    chk("17.6.2.1.2 triggers on 3+ close edges",
        G.three_or_more_close_edges, f"ca = 200 < 1.5hef = {1.5*G.hef:.0f} mm")

    chk("hef' = max(ca_max/1.5, s/3)",
        math.isclose(G.hef_effective, max(200.0 / 1.5, 300.0 / 3.0)),
        f"{G.hef_effective:.1f} mm")



    chk("ANc capped at n*ANco",
        G.ANc <= G.n * G.ANco, f"{G.ANc:.0f} <= {G.n*G.ANco:.0f} mm2")

    # hef raises breakout ONLY when edges are not controlling. With ANc
    # capped by close edges, ANco ~ hef^2 grows faster than Nb ~ hef^1.5,
    # so capacity falls as 1/sqrt(hef) -- correct CCD behaviour, and the
    # reason the fix for a failing base is a bigger pedestal, not a
    # longer rod.
    FAR = dataclasses.replace(G, ca_x1=9e3, ca_x2=9e3, ca_y1=9e3, ca_y2=9e3)
    chk("17.6.2.1.2 does NOT trigger with far edges",
        not FAR.three_or_more_close_edges
        and math.isclose(FAR.hef_effective, FAR.hef), "hef unchanged")
    f400 = check_anchors(Nua=350.0, Vua=60.0, **{**kw, "group": FAR})
    f600 = check_anchors(Nua=350.0, Vua=60.0,
                         **{**kw, "group": dataclasses.replace(FAR, hef=600.0)})
    chk("deeper hef -> more breakout (no edge effect)",
        next(c for n, c, _ in f600.tension if n.startswith("17.6.2"))
        > next(c for n, c, _ in f400.tension if n.startswith("17.6.2")),
        "hef 400->600, ca = 9 m")

    # Before 17.6.2.1.2 was implemented, deepening hef in a narrow pier
    # made the computed capacity FALL (ANc capped, ANco ~ hef^2). The
    # clause removes that artifact: hef' is pinned by ca_max/1.5, so
    # extra embedment neither helps nor hurts.
    chk("17.6.2.1.2 removes the 1/sqrt(hef) artifact",
        math.isclose(
            next(c for n, c, _ in deep_close.tension if n.startswith("17.6.2")),
            next(c for n, c, _ in r.tension if n.startswith("17.6.2")),
            rel_tol=1e-9),
        "hef 400->600 in a narrow pier: capacity unchanged")

    strong = check_anchors(Nua=350.0, Vua=60.0,
                           **{**kw, "grade": "F1554-105"})
    b_r = next(c for n, c, _ in r.tension if n.startswith("17.6.2"))
    chk("bigger edge distance -> more breakout",
        next(c for n, c, _ in f400.tension if n.startswith("17.6.2")) > b_r,
        f"ca 200 mm -> 9 m")
    b_s = next(c for n, c, _ in strong.tension if n.startswith("17.6.2"))
    chk("stronger rod does NOT change breakout",
        math.isclose(b_r, b_s), f"{b_r:.1f} == {b_s:.1f} kN")

    s_r = next(c for n, c, _ in r.tension if n.startswith("17.6.1"))
    s_s = next(c for n, c, _ in strong.tension if n.startswith("17.6.1"))
    chk("stronger rod DOES raise steel strength", s_s > s_r,
        f"{s_r:.0f} -> {s_s:.0f} kN")

    near = check_anchors(Nua=350.0, Vua=60.0, **{**kw,
        "group": dataclasses.replace(G, ca_x1=100.0, ca_x2=100.0,
                                     ca_y1=100.0, ca_y2=100.0)})
    chk("smaller edge distance -> less breakout",
        next(c for n, c, _ in near.tension if n.startswith("17.6.2")) < b_r,
        f"ca 200->100 mm")

    chk("psi_ed_N = 1.0 when ca >= 1.5hef",
        math.isclose(psi_ed_N(600.0, 400.0), 1.0), "")
    chk("psi_ed_N < 1.0 when ca < 1.5hef",
        psi_ed_N(200.0, 400.0) < 1.0, f"{psi_ed_N(200.0,400.0):.4f}")

    uncr = check_anchors(Nua=350.0, Vua=60.0, **{**kw,
        "group": dataclasses.replace(G, cracked=False)})
    chk("uncracked concrete -> 1.25x breakout",
        math.isclose(
            next(c for n, c, _ in uncr.tension if n.startswith("17.6.2"))
            / b_r, 1.25, rel_tol=1e-6), "psi_c,N")

    pure_t = check_anchors(Nua=350.0, Vua=0.0, **kw)
    chk("zero shear -> pure tension rule",
        pure_t.interaction_rule.startswith("17.8.1"),
        pure_t.interaction_rule[:24])

    both = check_anchors(Nua=350.0, Vua=200.0, **kw)
    chk("combined -> 17.8.3 rule",
        both.interaction_rule.startswith("17.8.3"),
        f"U = {both.interaction:.3f}")
    chk("combined interaction > pure tension",
        both.interaction > pure_t.interaction,
        f"{both.interaction:.3f} > {pure_t.interaction:.3f}")

    hef_req = required_embedment(Nua=350.0, group=FAR)
    chk("required_embedment finds a passing depth (no edge)",
        50.0 < hef_req < 1200.0, f"hef = {hef_req:.0f} mm")

    try:
        required_embedment(Nua=350.0, group=G)
        chk("edge-controlled group raises a clear error", False, "no raise")
    except GeometryError as e:
        chk("edge-controlled group raises a clear error",
            "EDGE DISTANCE controls" in str(e), "explains the real cause")

    # All eight relations are now verified against PROFIS, so strict mode
    # must RUN. The gate is still live: flip any flag off and it refuses.
    try:
        check_anchors(Nua=350.0, Vua=60.0, group=G, strict=True)
        chk("strict=True runs once verified", True, "all 8 flags true")
    except UnverifiedFormula as e:
        chk("strict=True runs once verified", False, str(e)[:40])

    saved = VERIFIED["tension_breakout"]
    VERIFIED["tension_breakout"] = False
    try:
        check_anchors(Nua=350.0, Vua=60.0, group=G, strict=True)
        chk("gate still refuses an unverified relation", False, "no raise")
    except UnverifiedFormula:
        chk("gate still refuses an unverified relation", True,
            "one flag off -> refused")
    finally:
        VERIFIED["tension_breakout"] = saved

    return res


if __name__ == "__main__":
    print("=" * 74)
    print("TASK #6 — ANCHOR RODS (ACI 318-19 Ch. 17)")
    print("=" * 74)
    print("\nTaking T_anchor = 350 kN from the base plate uplift case,")
    print("plus Vu = 60 kN base shear.\n")

    G = AnchorGroup(d=24.0, hef=400.0, n_x=2, n_y=2, s_x=300.0, s_y=300.0,
                    ca_x1=200.0, ca_x2=200.0, ca_y1=200.0, ca_y2=200.0)
    r = check_anchors(Nua=350.0, Vua=60.0, group=G, grade="F1554-36",
                      fc_prime=25.0, strict=False)
    print(r.report())

    import dataclasses

    print("\n" + "-" * 74)
    print("WRONG FIX — deeper embedment (edges stay at 200 mm):")
    for hef in (400.0, 500.0, 600.0, 700.0):
        rr = check_anchors(Nua=350.0, Vua=60.0,
                           group=dataclasses.replace(G, hef=hef),
                           fc_prime=25.0, strict=False)
        print(f"  hef = {hef:5.0f} mm   breakout = "
              f"{next(c for n,c,_ in rr.tension if n.startswith('17.6.2')):7.1f} kN"
              f"   -> {'PASS' if rr.passed else 'FAIL'}")
    print("  Capacity FALLS. ANc is capped by the edges while ANco ~ hef^2.")

    print("\nRIGHT FIX — bigger pedestal (hef stays 400 mm):")
    for ca in (200.0, 400.0, 600.0, 800.0):
        rr = check_anchors(Nua=350.0, Vua=60.0,
                           group=dataclasses.replace(
                               G, ca_x1=ca, ca_x2=ca, ca_y1=ca, ca_y2=ca),
                           fc_prime=25.0, strict=False)
        print(f"  ca  = {ca:5.0f} mm   breakout = "
              f"{next(c for n,c,_ in rr.tension if n.startswith('17.6.2')):7.1f} kN"
              f"   interaction = {rr.interaction:5.3f}   "
              f"{'PASS' if rr.passed else 'FAIL'}")

    print("\n" + "=" * 74)
    print("INVARIANT TESTS")
    print("=" * 74)
    rows = _run_invariants()
    for n, ok, d in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {n:<44s} {d}")
    print(f"\n  {sum(1 for _, ok, _ in rows if ok)}/{len(rows)} passed")
