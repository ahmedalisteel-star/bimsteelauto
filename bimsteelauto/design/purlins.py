"""
bimsteelauto.design.purlins
===========================

Cold-formed C and Z purlins and girts to AISI S100-16, over the ``cfs``
library.

Why this is a separate module from ``members.py``
--------------------------------------------------
A PEB has two steel codes in it.  The rigid frame is hot-rolled or welded
plate and goes to AISC 360-16; the purlins and girts are cold-formed and go
to AISI S100-16.  They are not variations of one check - different
buckling modes govern, and even ``E`` differs (AISI uses 29,500 ksi, AISC
29,000).  The two engines are kept apart so neither can borrow the other's
constants.

The three things this module refuses to guess
----------------------------------------------
1. **Section properties.**  A cold-formed section's ``Se``, ``Ix`` and ``Sx``
   come from the manufacturer's catalogue or the SSMA tables.  The flat-width
   helper in ``cfs`` is explicitly a preliminary approximation, so nothing
   here computes properties from the profile dimensions.

2. **Elastic buckling moments.**  DSM needs ``Mcre``, ``Mcrl`` and ``Mcrd``
   from a finite-strip analysis (CUFSM).  If they are not supplied, this
   module does NOT substitute a closed form - it runs the section-strength
   check and says on the sheet that global and distortional buckling were
   not evaluated.

3. **What braces the compression flange.**  Under gravity the sheeting
   braces the top flange continuously.  Under wind uplift the compression
   flange is the BOTTOM one, which the sheeting does not touch - it is
   braced only where an anti-sag tie or sag rod is actually fitted.  That is
   the case that governs a PEB purlin, and the module will not silently
   treat it as continuously braced.

Analysis of the purlin line
---------------------------
Purlins run continuously over several frame lines, so the moment is not
``wL^2/8``.  Rather than take coefficients from a table, the line is solved
as a continuous beam with the same PyNite solver the frame uses - exact for
any span arrangement, including the unequal end bays a PEB usually has.

Units: kN, kN.m, mm, MPa at this module's boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

from ..spec import DesignMethod, MemberCheck, MemberDesign, MemberForces, MemberType
from .units import (
    kip_to_kn,
    kipin_to_knm,
    kn_to_kip,
    knm_to_kipin,
    mm2_to_in2,
    mm3_to_in3,
    mm4_to_in4,
    mm6_to_in6,
    mm_to_in,
    mpa_to_ksi,
)

__all__ = [
    "VERIFIED",
    "COVERAGE_GAPS",
    "PurlinError",
    "CFShape",
    "ColdFormedSection",
    "ElasticBuckling",
    "PurlinBracing",
    "PurlinLine",
    "PurlinLoadCase",
    "continuous_beam_forces",
    "check_purlin",
]


class PurlinError(RuntimeError):
    """A purlin could not be checked."""


#: CLOSED 2026-09-01.  The statics are checked against closed forms, which
#: are independent of every solver; the AISI checks against the ``cfs``
#: library's own provisions with the arithmetic the Specification prints.
#:
#:     continuous_beam_statics  single span exactly wL^2/8 and wL/2; two,
#:                              three and four equal spans return the
#:                              textbook coefficients 0.125, 0.100/0.080 and
#:                              0.1071 wL^2; mid-span deflection exactly
#:                              5wL^4/384EI
#:     unit_conversion_aisi     E = 203,000 MPa is AISI's 29,500 ksi, not
#:                              AISC's 29,000 - pinned by a deflection ratio
#:     section_strength         EWM section strength returns a finite,
#:                              positive capacity and is labelled as
#:                              section-strength-only on the sheet
#:     shear_strength           Sect. G2, and ASD is shown to be lower than
#:                              LRFD by the phi/Omega ratio
#:     dsm_dispatch             DSM and EWM are shown to return DIFFERENT
#:                              capacities, so a silent fallback to one of
#:                              them cannot pass unnoticed
#:     uplift_bracing_rule      with no bottom-flange bracing the uplift
#:                              flexural check reports ratio = inf and the
#:                              member fails; it can never read as a pass
VERIFIED: dict[str, bool] = {
    "continuous_beam_statics": True,
    "unit_conversion_aisi": True,
    "section_strength": True,
    "shear_strength": True,
    "dsm_dispatch": True,
    "uplift_bracing_rule": True,
}

COVERAGE_GAPS: tuple[str, ...] = (
    "Web crippling (AISI S100-16 Sect. G5) is not checked. Its coefficients "
    "C, C_R, C_N and C_h depend on the exact bearing condition - end or "
    "interior, one flange or two, fastened or not - and are an input, not a "
    "table this module holds.",
    "Lapped and continuous Z-purlin systems are analysed as a prismatic "
    "continuous beam. The increased stiffness and strength over the lap "
    "length is not modelled, so a lapped system is checked conservatively "
    "at the support and unconservatively for deflection in the end bay.",
    "The R-factor reduction for through-fastened panels under uplift (AISI "
    "S100-16 Sect. I6.2.1) is not applied; where the bottom flange is not "
    "positively braced this module reports the check as not evaluated "
    "rather than substituting an R-factor.",
    "Purlin-to-rafter cleat connections are checked by the connection "
    "layer, not here.",
)


class CFShape(str, Enum):
    C = "C"
    Z = "Z"


@dataclass(frozen=True)
class ColdFormedSection:
    """A catalogue cold-formed section, in SI.

    Every property is a CATALOGUE value.  Nothing is derived from the profile
    dimensions here: the ``cfs`` library's own flat-width routine is
    documented as a preliminary approximation with no corner-radius
    correction, and a wrong ``Se`` produces a calculation sheet that is
    confidently wrong.

    ``Se_top`` / ``Se_bot`` are the EFFECTIVE section moduli at ``Fy``.  If
    the catalogue gives only one value for a symmetric C, pass it for both.
    """

    name: str            # e.g. "C200x60x20x2.5"
    shape: CFShape
    t: float             # mm, base metal thickness
    H: float             # mm, overall depth
    B: float             # mm, flange width
    Dlip: float          # mm, lip length
    A: float             # mm^2
    Ix: float            # mm^4
    Sx: float            # mm^3, gross
    Se_top: float        # mm^3, effective
    Se_bot: float        # mm^3, effective
    Iy: float = 0.0
    Sy: float = 0.0
    J: float = 0.0
    Cw: float = 0.0
    r: float = 0.0       # mm, inside corner radius
    ho: float = 0.0      # mm, out-to-out depth for web shear

    @property
    def Aw(self) -> float:
        """Web area for shear, mm^2.  ``ho x t``, AISI Sect. G2."""
        return (self.ho or self.H) * self.t

    @property
    def h_flat(self) -> float:
        """Flat web depth, mm."""
        return self.H - 2.0 * (self.t + self.r)

    def __post_init__(self) -> None:
        problems = []
        for n in ("t", "H", "B", "A", "Ix", "Sx", "Se_top", "Se_bot"):
            if getattr(self, n) <= 0:
                problems.append(f"{n} must be positive")
        if not problems:
            if self.Se_top > self.Sx * 1.001 or self.Se_bot > self.Sx * 1.001:
                problems.append(
                    "effective section modulus exceeds the gross value; Se is "
                    "a REDUCED property"
                )
            if self.t > 8.0:
                problems.append(
                    f"t = {self.t} mm is outside the cold-formed range; a "
                    "hot-rolled member belongs in design.members"
                )
        if problems:
            raise PurlinError(f"{self.name}: " + "; ".join(problems))


@dataclass(frozen=True)
class ElasticBuckling:
    """Finite-strip elastic buckling moments, kN.m.  From CUFSM.

    There is no default and no closed-form fallback.  ``None`` for the whole
    object means DSM cannot run, and the module says so on the sheet rather
    than inventing numbers.
    """

    Mcre: float          # global (lateral-torsional)
    Mcrl: float          # local
    Mcrd: float          # distortional
    source: str = "CUFSM finite-strip analysis"

    def __post_init__(self) -> None:
        for n in ("Mcre", "Mcrl", "Mcrd"):
            if getattr(self, n) <= 0:
                raise PurlinError(f"{n} must be positive")


@dataclass(frozen=True)
class PurlinBracing:
    """What braces each flange, and over what length.

    ``sheeting_braces_top`` is the ordinary case: fixed sheeting restrains
    the top flange continuously under gravity.

    ``bottom_flange_brace_spacing`` is the one that matters under uplift.
    ``None`` means the bottom flange has NO positive bracing, which is not
    the same as "braced" - the module reports the uplift flexural check as
    not evaluated rather than assuming either way.
    """

    sheeting_braces_top: bool = True
    bottom_flange_brace_spacing: float | None = None   # mm, anti-sag ties

    def unbraced_length(self, uplift: bool, span: float) -> float | None:
        if not uplift:
            return 0.0 if self.sheeting_braces_top else span
        if self.bottom_flange_brace_spacing:
            return self.bottom_flange_brace_spacing
        return None


@dataclass(frozen=True)
class PurlinLoadCase:
    """One load case on the purlin line, kN/m, and its sense.

    ``uplift`` is not derived from the sign, because a designer can write an
    uplift case as a positive magnitude.  It is stated.
    """

    name: str
    w: float             # kN/m, magnitude
    uplift: bool = False


@dataclass(frozen=True)
class PurlinLine:
    """One run of purlins across the building.

    ``spans`` are the bay spacings in mm, in order - the same non-uniform
    list the frame uses, not an average.
    """

    mark: str
    section: ColdFormedSection
    spans: tuple[float, ...]
    spacing: float                       # mm, purlin centres on the roof
    bracing: PurlinBracing = field(default_factory=PurlinBracing)
    elastic_buckling: ElasticBuckling | None = None
    member_type: MemberType = MemberType.PURLIN

    def __post_init__(self) -> None:
        if not self.spans or any(s <= 0 for s in self.spans):
            raise PurlinError(f"{self.mark}: spans must all be positive")
        if self.spacing <= 0:
            raise PurlinError(f"{self.mark}: spacing must be positive")

    @property
    def n_spans(self) -> int:
        return len(self.spans)

    @property
    def max_span(self) -> float:
        return max(self.spans)

    @property
    def total_length(self) -> float:
        return sum(self.spans)

    @property
    def is_continuous(self) -> bool:
        return self.n_spans > 1


# ---------------------------------------------------------------------------
# Continuous beam analysis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BeamForces:
    """Envelope over the whole purlin line, kN and kN.m.

    Sign convention, established empirically rather than assumed: for a beam
    along global X carrying a downward load, PyNite's ``Mz`` comes back
    NEGATIVE in the sagging region and POSITIVE over an interior support.
    Calibrated against a single span, where the mid-span value is exactly
    ``-wL^2/8``.

    The two are kept apart rather than collapsed to one magnitude because
    they load different flanges: sagging compresses the top flange, which
    the sheeting braces, while hogging over a support compresses the BOTTOM
    flange, which it does not.
    """

    M_sagging: float      # kN.m, positive magnitude
    M_hogging: float      # kN.m, positive magnitude
    V_max: float
    delta_max: float      # mm, largest deflection
    delta_span: float     # mm, the span it occurred in
    stations: tuple[tuple[float, float, float, float], ...] = ()

    @property
    def M_design(self) -> float:
        """The larger of the two, kN.m."""
        return max(self.M_sagging, self.M_hogging)


def continuous_beam_forces(
    spans: Sequence[float],
    w: float,
    section: ColdFormedSection,
    *,
    E: float = 203_000.0,
    n_stations: int = 21,
) -> BeamForces:
    """Solve the purlin line as a continuous beam.

    Exact for any span arrangement.  Calibrated against closed forms rather
    than trusted: a single span returns exactly ``wL^2/8`` and ``wL/2``, and
    three equal spans return ``0.1000 wL^2`` at the first interior support
    and ``0.0797 wL^2`` in the end span, against the textbook 0.100 and
    0.080.  Both are pinned by tests.

    ``E = 203,000 MPa`` is AISI's 29,500 ksi, NOT AISC's 29,000.  Using the
    AISC value here would overstate purlin deflection stiffness by 1.7%.
    """
    try:
        from Pynite import FEModel3D
    except ImportError as exc:  # pragma: no cover - environment issue
        raise PurlinError(
            "PyNiteFEA is required to analyse the purlin line."
        ) from exc

    model = FEModel3D()
    model.add_material("cf", E, E / (2 * 1.3), 0.30, 7.85e-9)
    model.add_section("s", section.A, section.Iy or section.Ix, section.Ix,
                      section.J or 1.0)

    x = 0.0
    model.add_node("N0", 0.0, 0.0, 0.0)
    nodes = ["N0"]
    for i, L in enumerate(spans):
        x += L
        name = f"N{i + 1}"
        model.add_node(name, x, 0.0, 0.0)
        nodes.append(name)
        model.add_member(f"M{i}", nodes[i], name, "cf", "s")

    # Simple supports at every frame line: the purlin sits on a cleat, which
    # is a bearing, not a moment connection.
    for i, n in enumerate(nodes):
        model.def_support(
            n,
            support_DX=(i == 0),
            support_DY=True,
            support_DZ=True,
            support_RX=True,
            support_RY=True,
        )

    for i in range(len(spans)):
        model.add_member_dist_load(f"M{i}", "FY", -w, -w, case="w")
    model.add_load_combo("w", {"w": 1.0})
    model.analyze(log=False)

    m_sag = m_hog = 0.0
    v_max = 0.0
    d_max = 0.0
    d_span = 0.0
    stations: list[tuple[float, float, float, float]] = []
    x0 = 0.0
    for i, L in enumerate(spans):
        mem = model.members[f"M{i}"]
        seg_L = mem.L() if callable(mem.L) else mem.L
        for k in range(n_stations):
            xl = seg_L * k / (n_stations - 1)
            M = mem.moment("Mz", xl, "w") / 1e6      # N.mm -> kN.m
            V = mem.shear("Fy", xl, "w") / 1e3       # N -> kN
            d = mem.deflection("dy", xl, "w")        # mm
            stations.append((x0 + xl, M, V, d))
            # See BeamForces: sagging arrives negative, hogging positive.
            m_sag = max(m_sag, -M)
            m_hog = max(m_hog, M)
            v_max = max(v_max, abs(V))
            if abs(d) > abs(d_max):
                d_max, d_span = d, L
        x0 += L

    return BeamForces(
        M_sagging=m_sag,
        M_hogging=m_hog,
        V_max=v_max,
        delta_max=d_max,
        delta_span=d_span,
        stations=tuple(stations),
    )


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def _cfs_section(s: ColdFormedSection):
    from aisi_s100 import CFSSection

    return CFSSection(
        name=s.name,
        shape=s.shape.value,
        t=mm_to_in(s.t),
        H=mm_to_in(s.H),
        B=mm_to_in(s.B),
        Dlip=mm_to_in(s.Dlip),
        r=mm_to_in(s.r),
        A=mm2_to_in2(s.A),
        Ix=mm4_to_in4(s.Ix),
        Sx=mm3_to_in3(s.Sx),
        Iy=mm4_to_in4(s.Iy),
        Sy=mm3_to_in3(s.Sy),
        J=mm4_to_in4(s.J),
        Cw=mm6_to_in6(s.Cw),
        ho=mm_to_in(s.ho or s.H),
        Se_top=mm3_to_in3(s.Se_top),
        Se_bot=mm3_to_in3(s.Se_bot),
        Aw=mm2_to_in2(s.Aw),
        h_flat=mm_to_in(s.h_flat),
    )


def _cfs_steel(Fy: float, Fu: float):
    from aisi_s100 import Steel

    return Steel(name=f"Fy {Fy:.0f} MPa", Fy=mpa_to_ksi(Fy), Fu=mpa_to_ksi(Fu))


def check_purlin(
    line: PurlinLine,
    cases: Sequence[PurlinLoadCase],
    *,
    Fy: float,
    Fu: float,
    method: DesignMethod = DesignMethod.LRFD,
    deflection_limit: int = 180,
) -> MemberDesign:
    """Check one purlin line against every load case.

    The worst case over all of them is returned, with one
    :class:`MemberCheck` per limit state.  A check that could not be
    performed - typically uplift flexure with no bottom-flange bracing -
    appears with an infinite ratio and a note saying why, so it cannot be
    mistaken for a pass.
    """
    try:
        import aisi_s100 as A
        from direct_strength import beam_strength_dsm
    except ImportError as exc:  # pragma: no cover - environment issue
        raise PurlinError(
            "the cfs library (aisi_s100, direct_strength) is required for "
            "cold-formed design; put its folder on sys.path"
        ) from exc

    if not cases:
        raise PurlinError(f"{line.mark}: no load cases to check")

    sec = _cfs_section(line.section)
    steel = _cfs_steel(Fy, Fu)
    basis = "LRFD" if method is DesignMethod.LRFD else "ASD"

    worst_ratio = -math.inf
    worst: dict[str, Any] | None = None

    for case in cases:
        # Line load on the purlin: kN/m^2 of roof x purlin spacing.
        forces = continuous_beam_forces(line.spans, case.w, line.section)
        checks = _check_case(
            line=line,
            case=case,
            forces=forces,
            sec=sec,
            steel=steel,
            basis=basis,
            deflection_limit=deflection_limit,
            A=A,
            beam_strength_dsm=beam_strength_dsm,
        )
        ratio = max((c.ratio for c in checks), default=0.0)
        if ratio > worst_ratio:
            worst_ratio = ratio
            worst = {"case": case, "forces": forces, "checks": checks}

    assert worst is not None
    f = worst["forces"]
    return MemberDesign(
        mark=line.mark,
        member_type=line.member_type,
        section_id=line.section.name,
        length=line.max_span,
        governing_combination=worst["case"].name,
        forces=MemberForces(P=0.0, Vy=f.V_max, Mz=f.M_design, station=0.0),
        checks=tuple(worst["checks"]),
        weight=line.section.A * 1e-6 * 7850.0 * (line.total_length / 1000.0),
        note=(
            f"{line.n_spans} span(s), max {line.max_span:.0f} mm, purlins at "
            f"{line.spacing:.0f} mm centres"
        ),
    )


def _check_case(
    *, line, case, forces, sec, steel, basis, deflection_limit, A,
    beam_strength_dsm,
) -> list[MemberCheck]:
    checks: list[MemberCheck] = []
    Lb = line.bracing.unbraced_length(case.uplift, line.max_span)

    # -- flexure -------------------------------------------------------------
    if Lb is None:
        checks.append(
            MemberCheck(
                name="Flexure under uplift - NOT EVALUATED",
                reference="AISI S100-16 Sect. F2 / I6.2.1",
                demand=forces.M_design,
                capacity=0.0,
                ratio=math.inf,
                unit="kN.m",
                note=(
                    "The bottom flange is the compression flange under "
                    "uplift and no anti-sag tie spacing was given. Sheeting "
                    "restrains the TOP flange only. Supply "
                    "bottom_flange_brace_spacing, or the R-factor of Sect. "
                    "I6.2.1 for through-fastened panels."
                ),
            )
        )
    elif line.elastic_buckling is not None:
        eb = line.elastic_buckling
        res = beam_strength_dsm(
            Sf=sec.Sx,
            Fy=steel.Fy,
            Mcre=knm_to_kipin(eb.Mcre),
            Mcrl=knm_to_kipin(eb.Mcrl),
            Mcrd=knm_to_kipin(eb.Mcrd),
        )
        Mc = kipin_to_knm(res.design(basis))
        checks.append(
            _mk(
                "Flexure (DSM, Appendix 1)",
                f"AISI S100-16 App. 1 - {getattr(res, 'name', 'beam strength')}",
                forces.M_design,
                Mc,
                "kN.m",
                note=f"Mcre/Mcrl/Mcrd from {eb.source}",
            )
        )
    else:
        res = A.flexure_strength(sec, steel)
        Mc = kipin_to_knm(res.design(basis))
        checks.append(
            _mk(
                "Flexure (section strength only)",
                "AISI S100-16 Sect. F3.1",
                forces.M_design,
                Mc,
                "kN.m",
                note=(
                    "Global and distortional buckling NOT evaluated - no "
                    "Mcre/Mcrl/Mcrd supplied. Valid only where the "
                    "compression flange is continuously braced by the "
                    "sheeting (Sect. D6)."
                    if Lb == 0.0
                    else f"Unbraced length {Lb:.0f} mm; LTB not evaluated "
                    "without Mcr from a finite-strip analysis."
                ),
            )
        )

    # -- shear ---------------------------------------------------------------
    vres = A.shear_strength(sec, steel)
    Vc = kip_to_kn(vres.design(basis))
    checks.append(
        _mk("Shear", "AISI S100-16 Sect. G2", forces.V_max, Vc, "kN")
    )

    # -- combined bending and shear -----------------------------------------
    Mc_ok = next((c.capacity for c in checks if c.name.startswith("Flexure")), 0.0)
    if Mc_ok > 0 and Vc > 0:
        chk = A.combined_bending_shear(
            knm_to_kipin(forces.M_design),
            knm_to_kipin(Mc_ok),
            kn_to_kip(forces.V_max),
            kn_to_kip(Vc),
        )
        ratio = getattr(chk, "ratio", None)
        if ratio is None:
            ratio = math.hypot(forces.M_design / Mc_ok, forces.V_max / Vc)
        checks.append(
            MemberCheck(
                name="Combined bending and shear",
                reference="AISI S100-16 Sect. H2",
                demand=float(ratio),
                capacity=1.0,
                ratio=float(ratio),
                unit="-",
            )
        )

    # -- deflection ----------------------------------------------------------
    if not case.uplift and deflection_limit:
        allowed = forces.delta_span / deflection_limit
        checks.append(
            _mk(
                f"Deflection (span/{deflection_limit})",
                "Serviceability - IBC Table 1604.3, not an AISI strength limit",
                abs(forces.delta_max),
                allowed,
                "mm",
                note="Unfactored line load as supplied by the caller",
            )
        )

    return checks


def _mk(name, reference, demand, capacity, unit, note="") -> MemberCheck:
    ratio = demand / capacity if capacity > 0 else math.inf
    return MemberCheck(
        name=name,
        reference=reference,
        demand=demand,
        capacity=capacity,
        ratio=ratio,
        unit=unit,
        note=note,
    )
