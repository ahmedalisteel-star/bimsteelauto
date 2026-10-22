"""
peb_endplate.py — End-plate flexural design (AISC 358-16 Sect. 6.2).
====================================================================

Companion to :mod:`peb_connections`.  That module handles the *bolts*
(tension, prying, shear).  This one handles the *plate*: the yield-line
mechanism that sets the required end-plate thickness.

    yield_line_Yp()            Y_p from AISC 358-16 Table 6.2
    endplate_moment_strength() phi*Mpl = phi * Fy * tp^2 * Yp
    required_thickness()       invert for the thickness the moment needs
    design_endplate()          auto-select config + thickness from a palette

Units: SI throughout — kN, mm, MPa, kNm.

.. warning::

   **VERIFICATION STATUS.**  The Y_p expressions below were written from
   the AISC 358-16 Sect. 6.2 procedure without the standard open on the
   desk.  Every one carries ``verified=False`` and a ``clause`` string.

   Before ANY of this reaches a stamped calculation, open AISC 358-16
   Table 6.2 and check each formula line by line, then flip the flag in
   ``_VERIFIED``.  ``design_endplate`` refuses to run in
   ``strict=True`` mode until that is done.

   The invariant tests at the bottom of this file catch coding errors
   (wrong sign, bad units, non-monotonic behaviour).  They CANNOT catch a
   transcription error in the formula itself.  Only the standard can.

Units check on the core relation:
    Mpl = Fy * tp^2 * Yp  ->  MPa * mm^2 * mm = N*mm     [dimensionally OK]
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Sequence

from .bolts import ConnectionError_, GeometryError, get_grade


# ======================================================================
# Verification gate
# ======================================================================
# Flip an entry to True only after checking that configuration's Y_p
# against AISC 358-16 Table 6.2 with the standard in front of you.

VERIFIED: dict[str, bool] = {
    # Verified 2026-08-29 against CET.SteelConnDesign report "B1_C1"
    # (W27x94 beam to W18x60 column flange, PL 1/4 x 9.84, A992).
    # Independently reproduced to better than 0.02 %:
    #     s       2.9343 in   vs CET   2.934  in
    #     Yp    352.17   in   vs CET 352.2    in
    #     tp_req  0.3895 in   vs CET   0.3895 in
    #     db_req  0.6317 in   vs CET   0.6317 in
    "4E": True,
    # VERIFIED 2026-08-31 against CET.SteelConnDesign V2.0, AISC 15th /
    # LRFD, two independent runs on DIFFERENT geometry:
    #   run 1  W24X76, bp 9.0,  s 2.80624  ->  Yp 290.191   (SUPPLIED the
    #          corrected formula: defect #10, two missing s-terms)
    #   run 2  W30X90, bp 10.5, s 3.03109  ->  Yp 400.935   (VERIFIED it)
    # Run 2's prediction was computed and recorded BEFORE CET's answer was
    # read, and matched to 0.0001%. s, Mnp, Yp, tp_req and db_req all agree
    # within 0.005%. CET printed the expression in full on BOTH runs and it
    # is identical:
    #   Yp = (bp/2)[h1(1/pfi + 1/s) + h0(1/pfo + 1/(2s))]
    #        + (2/g)[h1(pfi + s) + h0(de + pfo)]
    # Residual, recorded rather than buried: g, pfo, pfi and de were CET's
    # defaults in both runs and did not vary; and the module's de = min(de,
    # s) cap is untested because CET's de sat well below s both times.
    "4ES": True,
    # VERIFIED 2026-08-31 against CET.SteelConnDesign V2.0, AISC 15th /
    # LRFD, THREE runs. Defect #11 (a missing ``+ pb**2`` in the 2/g
    # bracket) was found on run 1 and DISCRIMINATED on run 3:
    #   run 1  W40X149  bp 11.875  pb 3.0  ->  Yp 904.742   (found the term)
    #   run 2  W36X150  bp 12.0    pb 3.0  ->  Yp 853.2     (same pb: no test
    #                                          of the term itself)
    #   run 3  W36X150  bp 12.0    pb 5.0  ->  Yp 938.2     (DISCRIMINATED)
    # Run 3 mattered because pb is SQUARED in the corrected term: at pb 3.0
    # every plausible form agreed within 3.4 in, so runs 1 and 2 could not
    # tell them apart. Predictions were registered before run 3 was made:
    #   pb^2 938.16 | pb^2/2 931.01 | 2pb 929.58 | pb*s 933.13 | pb^3/3 947.68
    # CET returned 938.2 and printed the bracket term as "(5 in.)^2". The
    # module gives 938.156 -- computed and posted BEFORE the report was
    # opened. The other four forms are ruled out by 5 to 10 in.
    # Residual, recorded rather than buried: g = 3.5 and de = 0.875 in all
    # three runs, so neither was varied; and the de = min(de, s) cap is
    # untested (de sat far below s = 3.24 every time).
    "8ES": True,
}


class UnverifiedFormula(ConnectionError_):
    """A Y_p expression has not yet been checked against AISC 358-16."""


class EndPlateConfig(str, Enum):
    """Prequalified extended end-plate configurations, AISC 358-16 Ch. 6."""

    FOUR_E = "4E"      # four bolts, unstiffened
    FOUR_ES = "4ES"    # four bolts, stiffened
    EIGHT_ES = "8ES"   # eight bolts, stiffened


# ======================================================================
# Geometry
# ======================================================================

@dataclass(frozen=True, slots=True)
class EndPlateGeometry:
    """End-plate and bolt layout, all mm.

    Attributes
    ----------
    bp:
        End-plate width.
    g:
        Bolt gage — horizontal centre-to-centre spacing across the web.
    pfo:
        Outer-face pitch: tension-flange OUTER face to the centreline of
        the bolt row outside the flange.
    pfi:
        Inner-face pitch: tension-flange INNER face to the centreline of
        the bolt row inside the flange.
    h_rows:
        Lever arms h_i, compression-flange centreline to each tension bolt
        row centreline.  Ordered OUTERMOST first (largest h first).
        4E / 4ES -> 2 entries.  8ES -> 4 entries.
    de:
        Distance from the outer bolt row to the end-plate edge.
        Required for stiffened configurations (4ES, 8ES).
    pb:
        Vertical pitch between the two bolt rows within a group (8ES only).
    """

    bp: float
    g: float
    pfo: float
    pfi: float
    h_rows: tuple[float, ...]
    de: float | None = None
    pb: float | None = None

    def __post_init__(self) -> None:
        for label in ("bp", "g", "pfo", "pfi"):
            v = getattr(self, label)
            if v <= 0:
                raise GeometryError(f"{label} must be > 0, got {v}")
        if not self.h_rows:
            raise GeometryError("h_rows must not be empty")
        if any(h <= 0 for h in self.h_rows):
            raise GeometryError(f"all h_rows must be > 0, got {self.h_rows}")
        if list(self.h_rows) != sorted(self.h_rows, reverse=True):
            raise GeometryError(
                f"h_rows must be ordered outermost first (descending), "
                f"got {self.h_rows}"
            )

    @property
    def s(self) -> float:
        """s = 0.5*sqrt(bp*g) — the yield-line pattern length, mm.

        AISC 358-16 Sect. 6.2.  ``pfi`` is capped at ``s`` in the Y_p
        expressions: a bolt further from the flange than ``s`` cannot
        develop a longer yield line.
        """
        return 0.5 * math.sqrt(self.bp * self.g)


# ======================================================================
# Yield-line mechanism parameter Y_p
# ======================================================================

@dataclass(frozen=True, slots=True)
class YieldLineResult:
    """Y_p and the values it was built from."""

    Yp: float                     # mm
    config: EndPlateConfig
    s: float                      # mm
    pfi_used: float               # mm, after the s cap
    capped: bool                  # True if pfi was capped at s
    clause: str
    verified: bool
    terms: dict[str, float] = field(default_factory=dict)

    def report(self) -> str:
        flag = "" if self.verified else "   [UNVERIFIED - check Table 6.2]"
        out = [
            f"Yield line  {self.config.value}  [{self.clause}]{flag}",
            f"  s = {self.s:.2f} mm    pfi used = {self.pfi_used:.2f} mm"
            + ("  (capped at s)" if self.capped else ""),
        ]
        out += [f"  {k:<24s} = {v:12.1f}" for k, v in self.terms.items()]
        out.append(f"  Yp = {self.Yp:.1f} mm")
        return "\n".join(out)


def yield_line_Yp(config: EndPlateConfig,
                  geom: EndPlateGeometry) -> YieldLineResult:
    """Yield-line mechanism parameter Y_p, mm (AISC 358-16 Table 6.2).

    Y_p is a *length*.  It multiplies ``Fy * tp^2`` to give the plate's
    plastic moment, so a larger Y_p means a stiffer yield-line pattern and
    a thinner plate for the same moment.

    Raises
    ------
    GeometryError
        Missing ``de`` for a stiffened configuration, or the wrong number
        of bolt rows for the configuration.
    """
    s = geom.s
    pfi = min(geom.pfi, s)
    capped = geom.pfi > s
    bp, g, pfo = geom.bp, geom.g, geom.pfo
    terms: dict[str, float] = {}

    if config is EndPlateConfig.FOUR_E:
        if len(geom.h_rows) != 2:
            raise GeometryError(
                f"4E needs exactly 2 bolt rows, got {len(geom.h_rows)}"
            )
        h0, h1 = geom.h_rows            # h0 outside flange, h1 inside
        # VERIFIED 2026-08-29 against CET.SteelConnDesign report B1_C1,
        # which prints this expression in full and cites AISC Design
        # Guide 4 Eq. 3.10.  Reproduced to 352.17 vs CET's 352.2 in.
        #   Yp = (bp/2)[h1(1/pfi + 1/s) + h0(1/pfo) - 1/2] + (2/g)[h1(pfi+s)]
        t_plate_bending = (bp / 2.0) * (
            h1 * (1.0 / pfi + 1.0 / s) + h0 * (1.0 / pfo) - 0.5
        )
        t_web_yield = (2.0 / g) * (h1 * (pfi + s))
        terms["plate bending (bp/2 term)"] = t_plate_bending
        terms["web-side (2/g term)"] = t_web_yield
        Yp = t_plate_bending + t_web_yield
        clause = "AISC Design Guide 4 Eq. 3.10 / 358-16 Table 6.2, 4E"

    elif config is EndPlateConfig.FOUR_ES:
        if len(geom.h_rows) != 2:
            raise GeometryError(
                f"4ES needs exactly 2 bolt rows, got {len(geom.h_rows)}"
            )
        if geom.de is None:
            raise GeometryError("4ES requires 'de' (outer bolt to plate edge)")
        h0, h1 = geom.h_rows
        de = min(geom.de, s)
        # DEFECT #10, fixed 2026-08-31. The bp/2 bracket was written as
        #     h1*(1/pfi) + h0*(1/pfo)
        # and was missing BOTH s-terms. CET.SteelConnDesign printed the
        # expression in full on report B1_C1 (W24x76 to W18x60, 2026-08-31):
        #     Yp = (bp/2)[h1(1/pfi + 1/s) + h0(1/pfo + 1/(2s))]
        #          + (2/g)[h1(pfi + s) + h0(de + pfo)]
        # The two omissions accounted for the entire 18.76% gap:
        #     (bp/2)*h1*(1/s)    = 34.384 in
        #     (bp/2)*h0*(1/(2s)) = 20.043 in   sum 54.427 in
        # Same family as defect #1, which cost the 4E form its 1/s term --
        # but NOT the same terms: 4E carries a -1/2 that 4ES does not, and
        # 4ES carries a 1/(2s) on h0 that 4E does not. The hypothesis that
        # 4ES simply mirrored 4E would have been wrong; only the printed
        # expression settled it.
        # The error was CONSERVATIVE -- a low Yp raises tp_req by ~10.9% --
        # which is why no invariant test caught it: Yp(4ES) still exceeded
        # Yp(4E), just by 2.7% instead of the correct 26.4%.
        t_plate_bending = (bp / 2.0) * (
            h1 * (1.0 / pfi + 1.0 / s)
            + h0 * (1.0 / pfo + 1.0 / (2.0 * s))
        )
        t_web_yield = (2.0 / g) * (h1 * (pfi + s) + h0 * (de + pfo))
        terms["plate bending (bp/2 term)"] = t_plate_bending
        terms["web+stiffener (2/g term)"] = t_web_yield
        Yp = t_plate_bending + t_web_yield
        clause = "AISC Design Guide 4 / 358-16 Table 6.2, four-bolt stiffened (4ES)"

    elif config is EndPlateConfig.EIGHT_ES:
        if len(geom.h_rows) != 4:
            raise GeometryError(
                f"8ES needs exactly 4 bolt rows, got {len(geom.h_rows)}"
            )
        if geom.de is None:
            raise GeometryError("8ES requires 'de'")
        if geom.pb is None:
            raise GeometryError("8ES requires 'pb' (row pitch within a group)")
        h1, h2, h3, h4 = geom.h_rows    # outermost -> innermost
        de, pb = geom.de, geom.pb
        t_plate_bending = (bp / 2.0) * (
            h1 * (1.0 / (2.0 * de)) + h2 * (1.0 / pfo)
            + h3 * (1.0 / pfi) + h4 * (1.0 / s)
        )
        # DEFECT #11, fixed 2026-08-31. The (2/g) bracket was missing a
        # trailing ``+ pb**2``. CET.SteelConnDesign printed the expression
        # in full (W40X149 to W18X60, 2026-08-31):
        #   Yp = (bp/2)[h1/(2de) + h2/pfo + h3/pfi + h4/s]
        #        + (2/g)[h1(de + pb/4) + h2(pfo + 3pb/4)
        #                + h3(pfi + pb/4) + h4(s + 3pb/4) + pb^2] + g
        # The omission accounted for the whole gap exactly:
        #   (2/g)*pb^2 = 5.1429 in  ->  899.599 vs CET's 904.742, -0.568%
        # Small and CONSERVATIVE (a low Yp raises tp_req), but a missing
        # term all the same -- and one that no invariant and no eyeballed
        # number could ever have caught. Note the module already carried
        # the unusual standalone ``+ g``, so the form was partly known;
        # only pb^2 was dropped.
        t_web_yield = (2.0 / g) * (
            h1 * (de + pb / 4.0) + h2 * (pfo + 3.0 * pb / 4.0)
            + h3 * (pfi + pb / 4.0) + h4 * (s + 3.0 * pb / 4.0)
            + pb ** 2
        )
        terms["plate bending (bp/2 term)"] = t_plate_bending
        terms["web+stiffener (2/g term)"] = t_web_yield
        terms["gage term"] = g
        Yp = t_plate_bending + t_web_yield + g
        clause = "AISC 358-16 Table 6.2, eight-bolt stiffened (8ES)"

    else:  # pragma: no cover
        raise ConnectionError_(f"unsupported configuration {config!r}")

    return YieldLineResult(
        Yp=Yp, config=config, s=s, pfi_used=pfi, capped=capped,
        clause=clause, verified=VERIFIED[config.value], terms=terms,
    )


# ======================================================================
# Plate flexural strength
# ======================================================================

PHI_PLATE: Final[float] = 0.90     # flexural yielding of the end plate


def endplate_moment_strength(t_plate: float, Fy: float, Yp: float,
                             phi: float = PHI_PLATE) -> float:
    """Design flexural strength of the end plate, kNm.

    ``phi * Mpl = phi * Fy * tp^2 * Yp``   (AISC 358-16 Sect. 6.2)

    MPa * mm^2 * mm = N*mm, divided by 1e6 to reach kNm.
    """
    if t_plate <= 0:
        raise GeometryError(f"t_plate must be > 0, got {t_plate}")
    if Fy <= 0 or Yp <= 0:
        raise GeometryError("Fy and Yp must be > 0")
    return phi * Fy * t_plate ** 2 * Yp / 1.0e6


def required_thickness(Mu: float, Fy: float, Yp: float,
                       phi: float = PHI_PLATE) -> float:
    """End-plate thickness that develops ``Mu`` (kNm) directly, mm.

    Inverts :func:`endplate_moment_strength`:
    ``tp = sqrt(Mu / (phi * Fy * Yp))`` with Mu converted kNm -> N*mm.

    .. note::
       This sizes the plate for the applied moment.  Design Guide 4 sizes
       it instead for the bolts' no-prying capacity -- see
       :func:`required_thickness_dg4`, which is the thick-plate approach
       CET.SteelConnDesign and most fabricators use.
    """
    if Mu < 0:
        raise GeometryError(f"Mu must be >= 0, got {Mu}")
    if Fy <= 0 or Yp <= 0:
        raise GeometryError("Fy and Yp must be > 0")
    return math.sqrt(Mu * 1.0e6 / (phi * Fy * Yp))


PHI_BOLT: Final[float] = 0.75


def no_prying_moment_strength(Pt: float, h_rows: Sequence[float],
                              n_per_row: int = 2) -> float:
    """Bolt moment strength with no prying, kNm (DG 4 / AISC 358 Sect. 6.2).

    ``Mnp = n_per_row * Pt * SUM(h_i)`` -- every tension bolt at full
    tensile strength ``Pt`` (kN), lever arms ``h_i`` (mm).  Nominal, not
    factored.
    """
    if Pt <= 0:
        raise GeometryError(f"Pt must be > 0, got {Pt}")
    return n_per_row * Pt * float(sum(h_rows)) / 1000.0


def required_thickness_dg4(Mnp: float, Fy: float, Yp: float,
                           phi_bolt: float = PHI_BOLT,
                           phi_plate: float = PHI_PLATE) -> float:
    """End-plate thickness by the Design Guide 4 thick-plate rule, mm.

    ``tp = sqrt(1.11 * phi_bolt * Mnp / (phi_plate * Fy * Yp))``
    (AISC Design Guide 4 Eq. 3.10)

    The philosophy differs from :func:`required_thickness`: the plate is
    NOT sized for the applied moment, it is sized so that the bolts reach
    their no-prying capacity before the plate yields.  The 1.11 factor is
    the margin that keeps prying from developing.  This is why a DG 4
    plate is thicker than one sized directly for ``Mu`` -- and why a
    connection can show a low moment utilisation and still fail the
    thickness check.

    Parameters
    ----------
    Mnp:
        NOMINAL no-prying bolt moment strength, kNm, from
        :func:`no_prying_moment_strength`.
    """
    if Mnp < 0:
        raise GeometryError(f"Mnp must be >= 0, got {Mnp}")
    if Fy <= 0 or Yp <= 0:
        raise GeometryError("Fy and Yp must be > 0")
    return math.sqrt(
        1.11 * phi_bolt * Mnp * 1.0e6 / (phi_plate * Fy * Yp)
    )


def required_bolt_diameter(Mu: float, Ft: float,
                           h_rows: Sequence[float],
                           phi_bolt: float = PHI_BOLT) -> float:
    """Required bolt diameter for a 4-bolt end plate, mm (DG 4 Eq. 3.5).

    ``db_req = sqrt(2*Mu / (pi * phi * Ft * SUM(h_i)))``

    Parameters
    ----------
    Mu:  factored moment, kNm.
    Ft:  nominal bolt tensile STRESS, MPa.
    """
    if Mu < 0:
        raise GeometryError(f"Mu must be >= 0, got {Mu}")
    if Ft <= 0:
        raise GeometryError(f"Ft must be > 0, got {Ft}")
    lever = float(sum(h_rows))
    return math.sqrt(
        2.0 * Mu * 1.0e6 / (math.pi * phi_bolt * Ft * lever)
    )


# ======================================================================
# Auto-selection
# ======================================================================

# Typical fabricator plate palette, mm. Replace with your own.
DEFAULT_PLATE_PALETTE: Final[tuple[float, ...]] = (
    12.0, 15.0, 16.0, 20.0, 25.0, 30.0, 32.0, 35.0, 40.0, 45.0, 50.0,
)


@dataclass(frozen=True, slots=True)
class EndPlateDesign:
    """Selected end-plate configuration and thickness."""

    config: EndPlateConfig
    t_plate: float                # mm, selected from the palette
    t_required: float             # mm, exact
    Mu: float                     # kNm
    Mn_design: float              # kNm, phi*Mpl of the selected plate
    utilization: float
    Fy: float
    yield_line: YieldLineResult
    passed: bool

    def report(self) -> str:
        return (
            f"End plate design -- {'PASS' if self.passed else 'FAIL'}\n"
            f"  Mu          = {self.Mu:.2f} kNm\n"
            f"  config      = {self.config.value}\n"
            f"  t_required  = {self.t_required:.2f} mm\n"
            f"  t_selected  = {self.t_plate:.1f} mm   (Fy = {self.Fy:.0f} MPa)\n"
            f"  phi*Mpl     = {self.Mn_design:.2f} kNm\n"
            f"  utilization = {self.utilization:.3f}\n\n"
            + self.yield_line.report()
        )


def design_endplate(
    *,
    Mu: float,
    geom: EndPlateGeometry,
    plate_grade: str,
    config: EndPlateConfig = EndPlateConfig.FOUR_E,
    palette: Sequence[float] = DEFAULT_PLATE_PALETTE,
    strict: bool = True,
) -> EndPlateDesign:
    """Pick the thinnest plate from ``palette`` that carries ``Mu``.

    ``get_grade`` is thickness-aware, so Fy is re-read for each candidate
    thickness -- a 50 mm S355 plate is 335 MPa, not 355, and selecting on
    a fixed Fy would be unconservative.

    Parameters
    ----------
    strict:
        When True (default), refuses to run until this configuration's
        Y_p has been checked against AISC 358-16 Table 6.2 and flipped in
        ``_VERIFIED``.  Pass ``strict=False`` for development only.

    Raises
    ------
    UnverifiedFormula
        ``strict=True`` and the Y_p for this configuration is unverified.
    GeometryError
        No plate in the palette is thick enough.
    """
    if strict and not VERIFIED[config.value]:
        raise UnverifiedFormula(
            f"Y_p for {config.value} has not been checked against "
            f"AISC 358-16 Table 6.2. Verify it and set "
            f"VERIFIED['{config.value}'] = True, or pass strict=False "
            f"for development."
        )

    yl = yield_line_Yp(config, geom)

    for t in sorted(palette):
        Fy, _ = get_grade(plate_grade, t)
        Mn = endplate_moment_strength(t, Fy, yl.Yp)
        if Mn >= Mu:
            return EndPlateDesign(
                config=config, t_plate=t,
                t_required=required_thickness(Mu, Fy, yl.Yp),
                Mu=Mu, Mn_design=Mn,
                utilization=Mu / Mn if Mn > 0 else math.inf,
                Fy=Fy, yield_line=yl, passed=True,
            )

    t_max = max(palette)
    Fy, _ = get_grade(plate_grade, t_max)
    Mn = endplate_moment_strength(t_max, Fy, yl.Yp)
    raise GeometryError(
        f"no plate in the palette carries Mu = {Mu:.1f} kNm in {config.value}. "
        f"Thickest ({t_max:.0f} mm) gives {Mn:.1f} kNm. "
        f"Try a stiffened configuration, a wider plate, or more bolt rows."
    )


# ======================================================================
# Invariant tests -- catch coding errors, NOT transcription errors
# ======================================================================

def _run_invariants() -> list[tuple[str, bool, str]]:
    """Physical checks that must hold regardless of the exact Y_p formula."""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    g4 = EndPlateGeometry(bp=250.0, g=140.0, pfo=50.0, pfi=50.0,
                          h_rows=(720.0, 600.0), de=45.0)
    yl = yield_line_Yp(EndPlateConfig.FOUR_E, g4)

    # 1. Yp positive and dimensionally a length
    check("Yp > 0", yl.Yp > 0, f"Yp = {yl.Yp:.1f} mm")

    # 2. Strength scales with tp^2
    m1 = endplate_moment_strength(20.0, 355.0, yl.Yp)
    m2 = endplate_moment_strength(40.0, 355.0, yl.Yp)
    check("2x thickness -> 4x moment", math.isclose(m2 / m1, 4.0, rel_tol=1e-9),
          f"ratio = {m2 / m1:.4f}")

    # 3. Strength linear in Fy
    m3 = endplate_moment_strength(20.0, 710.0, yl.Yp)
    check("2x Fy -> 2x moment", math.isclose(m3 / m1, 2.0, rel_tol=1e-9),
          f"ratio = {m3 / m1:.4f}")

    # 4. required_thickness inverts endplate_moment_strength
    t_req = required_thickness(m1, 355.0, yl.Yp)
    check("required_thickness inverts strength",
          math.isclose(t_req, 20.0, rel_tol=1e-9), f"t = {t_req:.6f} mm")

    # 5. Stiffened must be stronger than unstiffened, same geometry
    yl_s = yield_line_Yp(EndPlateConfig.FOUR_ES, g4)
    check("Yp(4ES) > Yp(4E)", yl_s.Yp > yl.Yp,
          f"{yl_s.Yp:.1f} > {yl.Yp:.1f} mm")

    # 6. Deeper connection (larger lever arms) -> larger Yp
    g_deep = EndPlateGeometry(bp=250.0, g=140.0, pfo=50.0, pfi=50.0,
                              h_rows=(900.0, 780.0), de=45.0)
    yl_deep = yield_line_Yp(EndPlateConfig.FOUR_E, g_deep)
    check("deeper connection -> larger Yp", yl_deep.Yp > yl.Yp,
          f"{yl_deep.Yp:.1f} > {yl.Yp:.1f} mm")

    # 7. pfi capped at s
    g_far = EndPlateGeometry(bp=250.0, g=140.0, pfo=50.0, pfi=5000.0,
                             h_rows=(720.0, 600.0), de=45.0)
    yl_far = yield_line_Yp(EndPlateConfig.FOUR_E, g_far)
    check("pfi capped at s", yl_far.capped and yl_far.pfi_used == yl.s,
          f"pfi_used = {yl_far.pfi_used:.2f}, s = {yl.s:.2f}")

    # 8. Larger Mu -> thicker plate required (monotonic)
    t_a = required_thickness(200.0, 355.0, yl.Yp)
    t_b = required_thickness(400.0, 355.0, yl.Yp)
    check("larger Mu -> thicker plate", t_b > t_a,
          f"{t_b:.2f} > {t_a:.2f} mm")

    # 9. Thickness-aware Fy is applied on selection
    d = design_endplate(Mu=150.0, geom=g4, plate_grade="S355JR",
                        strict=False)
    Fy_expected, _ = get_grade("S355JR", d.t_plate)
    check("Fy re-read at selected thickness", d.Fy == Fy_expected,
          f"Fy = {d.Fy:.0f} MPa at t = {d.t_plate:.0f} mm")

    # 10. Selected plate actually carries the moment
    check("selected plate carries Mu", d.Mn_design >= d.Mu and d.utilization <= 1.0,
          f"U = {d.utilization:.3f}")

    return results


if __name__ == "__main__":
    print("=" * 68)
    print("End-plate yield line -- AISC 358-16 Sect. 6.2")
    print("=" * 68)

    geom = EndPlateGeometry(
        bp=250.0, g=140.0, pfo=50.0, pfi=50.0,
        h_rows=(720.0, 600.0), de=45.0,
    )

    d = design_endplate(Mu=250.0, geom=geom, plate_grade="S355JR",
                        config=EndPlateConfig.FOUR_E, strict=False)
    print("\n" + d.report())

    print("\n" + "-" * 68)
    print("Same moment, stiffened (4ES):")
    d2 = design_endplate(Mu=250.0, geom=geom, plate_grade="S355JR",
                         config=EndPlateConfig.FOUR_ES, strict=False)
    print(f"  4E  -> {d.t_plate:.0f} mm plate,  U = {d.utilization:.3f}")
    print(f"  4ES -> {d2.t_plate:.0f} mm plate,  U = {d2.utilization:.3f}")

    print("\n" + "=" * 68)
    print("INVARIANT TESTS")
    print("=" * 68)
    rows = _run_invariants()
    for name, ok, detail in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<38s} {detail}")
    n_ok = sum(1 for _, ok, _ in rows if ok)
    print(f"\n  {n_ok}/{len(rows)} passed")

    print("\n" + "!" * 68)
    print("  Y_p formulas are UNVERIFIED. Check against AISC 358-16")
    print("  Table 6.2, then set VERIFIED[...] = True.")
    print("  design_endplate(strict=True) refuses to run until you do.")
    print("!" * 68)
