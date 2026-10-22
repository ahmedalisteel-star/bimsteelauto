"""
bimsteelauto.design.members
===========================

AISC 360-16 member checks for the PEB main frame, driven by ``pyaisc360``.

    Envelope (kN, kN.m)  +  WeldedI sections  ->  MemberDesign (utilisation)

What is checked
---------------
At every result station of every design member, for every load combination:

    Chapter E   axial compression
    Chapter D   axial tension
    Chapter F   flexure, with LTB
    Chapter G   web shear
    Chapter H   combined axial and flexure, Eq. H1-1a / H1-1b

The section used at a station is the section AT that station, so a haunch is
checked where it is deep and the apex where it is shallow.

How a tapered member is treated - and the limit of it
-----------------------------------------------------
Each station is checked as though it were a prismatic member with that
station's cross-section.  Unbraced lengths come from the brace points, and
``Cb`` is computed from the real moment diagram of the unbraced segment
containing the station, via Eq. F1-1.

This is the station-wise prismatic idealisation.  It is standard practice
and it is what a PEB is normally checked by, but it is NOT the same as AISC
Design Guide 25, which treats a web-tapered member's stability directly with
its own gamma factors.  Where the taper is steep, DG 25 gives a different -
usually higher - LTB capacity than this method, so this module is on the
conservative side for flexure.  That is a COVERAGE GAP, recorded in
:data:`COVERAGE_GAPS`, not a verification failure: the checks that are
performed are correct, and the ones that are missing are named.

Effective lengths
-----------------
``K = 1.0`` in plane.  That is only legitimate because the analysis module
runs a second-order (P-Delta) solution, which is AISC 360-16 Chapter C's
Direct Analysis Method route.  The DAM also requires reduced stiffness
(0.8 tau_b EI) and notional loads; neither is applied yet - see
:data:`COVERAGE_GAPS`.  Until they are, an in-plane stability answer from
this module is provisional.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..sections import TaperedMember, WeldedI
from ..spec import (
    DesignMethod,
    Materials,
    MemberCheck,
    MemberDesign,
    MemberForces,
    MemberType,
)
from .units import (
    kn_to_kip,
    knm_to_kipin,
    kipin_to_knm,
    kip_to_kn,
    mm2_to_in2,
    mm3_to_in3,
    mm4_to_in4,
    mm6_to_in6,
    ksi_to_mpa,
    mm_to_in,
    mpa_to_ksi,
)

__all__ = [
    "VERIFIED",
    "COVERAGE_GAPS",
    "DesignError",
    "BracingSpec",
    "MemberDesignInput",
    "AvailableStrengths",
    "available_strengths",
    "cb_from_moments",
    "check_member",
    "station_utilisations",
]


class DesignError(RuntimeError):
    """A member could not be checked."""


# ---------------------------------------------------------------------------
# Verification gate
# ---------------------------------------------------------------------------

#: False until matched by an INDEPENDENT source.
#:
#: CLOSED 2026-09-01 against three references, none of them this module:
#: exact defined-constant arithmetic for the unit boundary; the ``aisc360.py``
#: script bundled with the ``aisc-steel-design`` skill, which is a separately
#: written AISC 360-16 implementation; and longhand clause arithmetic for the
#: cases the Specification states outright.
#:
#:     unit_conversion   inch and kip are exact by definition; ksi derived,
#:                       not typed; round trips to 1e-12
#:     cb_equation       1.00 uniform, 1.14 parabolic, 2.27 reversal - the
#:                       three textbook values - and equal to the reference
#:                       Eq. F1-1 to 1e-9
#:     flexure_F_ltb     within 2% of the reference across all three LTB
#:                       zones (Lb < Lp, Lp < Lb < Lr, Lb > Lr)
#:     compression_E3    within 2% of the reference at two slendernesses
#:     shear_G2          NOMINAL Vn within 2%; the AVAILABLE strengths
#:                       deliberately differ - see below
#:     interaction_H1    equal to the reference to 1e-6 on both branches,
#:                       and the branch switches at Pr/Pc = 0.2 as stated
#:     section_adapter   exercised by every check above; the section
#:                       properties themselves are verified in sections.py
#:
#: KNOWN, EXPLAINED DIFFERENCE - shear resistance factor.  Sect. G2.1(a)
#: allows phi_v = 1.00 only for "webs of ROLLED I-shaped members" with
#: h/tw <= 2.24 sqrt(E/Fy).  A welded plate girder does not qualify and takes
#: phi_v = 0.90.  The reference script hardcodes the rolled case, so its
#: available shear is 11.1% higher than this module's.  This module is the
#: conservative and correct one for a PEB; the gap is pinned by
#: ``test_welded_girder_does_not_get_the_rolled_shape_phi`` so it cannot
#: quietly close.
VERIFIED: dict[str, bool] = {
    "unit_conversion": True,
    "section_adapter": True,
    "cb_equation": True,
    "compression_E3": True,
    "flexure_F_ltb": True,
    "shear_G2": True,
    "interaction_H1": True,
    # Added 2026-09-06 with the 3D building model, which is the first thing
    # in this package to produce My, Vz and T. Each is closed by longhand
    # arithmetic on the clause, in tests/test_design_biaxial.py.
    #
    #     flexure_F6_minor     Mn = min(Mp = Fy Zy <= 1.6 Fy Sy, FLB) for a
    #                          compact and a noncompact flange, both worked
    #                          by hand; and the 1.6 Fy Sy cap is shown to
    #                          BITE on a wide-flange section, which is the
    #                          branch a compact-only test never reaches
    #     shear_G6_weak        Vn = 0.6 Fy bf tf Cv2 summed over BOTH
    #                          flanges, against longhand, with the
    #                          singly-symmetric case where doubling one
    #                          flange gives the wrong answer
    #     torsion_H3_stvenant  tau = T t / J against longhand, and the
    #                          available stress against 0.6 Fy phi
    #     interaction_H1_biaxial
    #                          H1-1a and H1-1b each expanded by hand with
    #                          both bending terms, on both sides of the
    #                          Pr/Pc = 0.2 branch, and the minor-axis term
    #                          shown to CHANGE the answer - a biaxial
    #                          interaction that quietly dropped Mry would
    #                          pass every uniaxial test in this suite
    "flexure_F6_minor": True,
    "shear_G6_weak": True,
    "torsion_H3_stvenant": True,
    "interaction_H1_biaxial": True,
}

#: Things this module does NOT do.  Distinct from VERIFIED: a gap is a check
#: that is absent, not a formula that is unchecked.  Both block issue, but
#: they are closed in different ways and must not be confused.
COVERAGE_GAPS: tuple[str, ...] = (
    "Web-tapered stability per AISC Design Guide 25 is not implemented; "
    "tapered members are checked station-wise as prismatic, which is "
    "conservative for LTB.",
    "Direct Analysis Method stiffness reduction (0.8 tau_b EI) and notional "
    "loads are not applied in the analysis, so K = 1.0 in plane is not yet "
    "fully justified.",
    "Web local yielding, crippling and sidesway buckling at load points are "
    "not checked here; the connection layer covers them at the connections "
    "only (AISC J10).",
    "Serviceability deflection and drift limits are not checked in this "
    "module.",
    "Flange and web local buckling of slender welded elements relies on "
    "pyaisc360 Chapter F and E7; plate girder tension field action (G2.2) "
    "is not invoked.",
    "TORSION IS CHECKED FOR UNIFORM (ST VENANT) SHEAR ONLY. The warping "
    "normal stress and the warping shear stress are NOT computed: they "
    "need the solution of the torsion differential equation with the "
    "member's own end conditions (AISC Design Guide 9), and the analysis "
    "does not produce those end conditions. On an open section the warping "
    "term is usually the larger of the two, so the reported torsion ratio "
    "is a LOWER BOUND. Every torsion check says so in its own note.",
    "Lateral-torsional buckling under combined major-axis bending and "
    "torsion is not checked. Sect. H1 covers axial with flexure and Sect. "
    "H3.3 covers the torsional stress; their interaction on a "
    "non-HSS member is left to the engineer.",
    "Sect. H2 (the linear stress interaction, fra/Fca + frbw/Fcbw + "
    "frbz/Fcbz) is not run as an alternative to H1. It is permitted for "
    "any shape and is generally more conservative; H1 with both bending "
    "terms is what this module reports.",
)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BracingSpec:
    """Where the member is braced, measured in mm from its i end.

    Two flanges are tracked separately because that is the physical reality
    of a PEB rafter: purlins brace the top flange at close centres, and the
    bottom flange is braced only where a fly brace is actually fitted.  Under
    wind uplift the bottom flange is the compression flange, and its longer
    unbraced length is usually what governs the whole design.

    The member ends are always brace points and are added automatically.
    """

    top_flange: tuple[float, ...] = ()
    bottom_flange: tuple[float, ...] = ()

    @classmethod
    def at_spacing(
        cls, length: float, top_spacing: float, bottom_spacing: float | None = None
    ) -> "BracingSpec":
        """Evenly spaced braces, e.g. purlins at 1.5 m on a rafter."""

        def pts(sp: float | None) -> tuple[float, ...]:
            if not sp or sp <= 0:
                return ()
            n = max(1, int(round(length / sp)))
            return tuple(length * i / n for i in range(1, n))

        return cls(top_flange=pts(top_spacing), bottom_flange=pts(bottom_spacing))

    def points(self, which: str, length: float) -> tuple[float, ...]:
        raw = self.top_flange if which == "top" else self.bottom_flange
        pts = sorted({0.0, length, *(p for p in raw if 0.0 < p < length)})
        return tuple(pts)

    def segment_containing(
        self, x: float, which: str, length: float
    ) -> tuple[float, float]:
        """The unbraced segment (x_start, x_end) that station ``x`` lies in."""
        pts = self.points(which, length)
        for a, b in zip(pts, pts[1:]):
            if a - 1e-9 <= x <= b + 1e-9:
                return a, b
        return pts[0], pts[-1]  # pragma: no cover - x is always in range


@dataclass(frozen=True)
class MemberDesignInput:
    """Everything needed to check one design member."""

    mark: str
    member_type: MemberType
    taper: TaperedMember
    #: ``forces[combo]`` is the ordered station list from the Envelope.
    forces: dict[str, Sequence[MemberForces]]
    bracing: BracingSpec
    materials: Materials
    method: DesignMethod = DesignMethod.LRFD
    #: In-plane effective length factor.  1.0 under the Direct Analysis
    #: Method; stated here rather than assumed inside the check.
    K_in_plane: float = 1.0


# ---------------------------------------------------------------------------
# Cb - AISC 360-16 Eq. F1-1
# ---------------------------------------------------------------------------


def cb_from_moments(moments: Sequence[float]) -> float:
    """Lateral-torsional buckling modification factor, Eq. F1-1.

    ``Cb = 12.5 Mmax / (2.5 Mmax + 3 MA + 4 MB + 3 MC)``, where MA, MB, MC
    are the absolute moments at the quarter, mid and three-quarter points of
    the UNBRACED SEGMENT and Mmax is the largest absolute moment in it.

    ``moments`` is the moment at equally spaced stations along that segment,
    ends included.  The quarter points are interpolated, so the caller does
    not have to sample at exactly five stations.

    Returns 1.0 - always permitted, always conservative - when the segment
    carries no moment, and is capped at 3.0 per the Specification.
    """
    if len(moments) < 2:
        return 1.0
    m = [abs(v) for v in moments]
    m_max = max(m)
    if m_max <= 0.0:
        return 1.0

    def at(frac: float) -> float:
        pos = frac * (len(m) - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(m) - 1)
        t = pos - lo
        return m[lo] * (1.0 - t) + m[hi] * t

    mA, mB, mC = at(0.25), at(0.50), at(0.75)
    denom = 2.5 * m_max + 3.0 * mA + 4.0 * mB + 3.0 * mC
    if denom <= 0.0:  # pragma: no cover - guarded by m_max above
        return 1.0
    return min(3.0, 12.5 * m_max / denom)


# ---------------------------------------------------------------------------
# Section adapter
# ---------------------------------------------------------------------------


class _ImperialSection:
    """A :class:`WeldedI` restated in inches for ``pyaisc360``.

    A plain attribute carrier rather than a dataclass, because
    ``SectionAdapter`` discovers properties by name and the set it wants is
    fixed by the Specification, not by this package.
    """

    def __init__(self, s: WeldedI, name: str = "") -> None:
        self.name = name or s.mark
        self.A = mm2_to_in2(s.A)
        self.d = mm_to_in(s.d)
        self.tw = mm_to_in(s.tw)
        self.bf = mm_to_in(max(s.bf_top, s.bf_bot))
        self.tf = mm_to_in(min(s.tf_top, s.tf_bot))
        self.ho = mm_to_in(s.ho)
        self.Ix = mm4_to_in4(s.Ix)
        self.Iy = mm4_to_in4(s.Iy)
        self.Sx = mm3_to_in3(s.Sx)
        self.Sy = mm3_to_in3(s.Sy)
        self.Zx = mm3_to_in3(s.Zx)
        self.Zy = mm3_to_in3(s.Zy)
        self.rx = mm_to_in(s.rx)
        self.ry = mm_to_in(s.ry)
        self.J = mm4_to_in4(s.J)
        self.Cw = mm6_to_in6(s.Cw)
        # Ratios are dimensionless - they must NOT be converted.
        self.bf_2tf = s.lambda_flange_top
        self.h_tw = s.lambda_web
        self.kdes = mm_to_in(min(s.tf_top, s.tf_bot))
        self.h = mm_to_in(s.h)


def _adapt(section: WeldedI, name: str = ""):
    from pyaisc360 import SectionAdapter

    return SectionAdapter(_ImperialSection(section, name), name=name or section.mark)


def _steel(materials: Materials, cold_formed: bool = False):
    """A pyaisc360 Steel from the project materials, in ksi."""
    from pyaisc360.materials import Steel

    fy = materials.Fy_cold_formed if cold_formed else materials.Fy_builtup
    fu = materials.Fu_cold_formed if cold_formed else materials.Fu_builtup
    return Steel(
        name=f"Fy {fy:.0f} MPa",
        Fy=mpa_to_ksi(fy),
        Fu=mpa_to_ksi(fu),
        standard=(
            "AISI S100-16 grade" if cold_formed else "project plate grade"
        ),
        note=(
            f"Fy = {fy:.0f} MPa, Fu = {fu:.0f} MPa as stated in the project "
            "materials; converted to ksi at this boundary only"
        ),
    )


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def check_member(inp: MemberDesignInput) -> MemberDesign:
    """Run every AISC check at every station and return the worst.

    The returned :class:`MemberDesign` carries the governing station's forces
    and one :class:`MemberCheck` per limit state, each with its equation
    number, so the calculation sheet can print the whole story.
    """
    try:
        from pyaisc360 import Basis
        from pyaisc360.chapter_e import CompressionMember, compressive_strength
        from pyaisc360.chapter_f import (
            FlexuralMember,
            ShapeType,
            flexural_strength,
        )
        from pyaisc360.chapter_g import ShearMember, shear_strength
        from pyaisc360.chapter_h import combined_axial_flexure
    except ImportError as exc:  # pragma: no cover - environment issue
        raise DesignError(
            "pyaisc360 is required for member design. Put the library "
            "folder on sys.path."
        ) from exc

    basis = Basis.LRFD if inp.method is DesignMethod.LRFD else Basis.ASD
    steel = _steel(inp.materials)
    L = inp.taper.length

    worst_ratio = -1.0
    worst: dict[str, Any] | None = None

    for combo, stations in inp.forces.items():
        if not stations:
            continue
        for st in stations:
            section = inp.taper.at(min(max(st.station, 0.0), L))
            result = _check_station(
                section=section,
                st=st,
                inp=inp,
                stations=stations,
                steel=steel,
                basis=basis,
                classes=(
                    CompressionMember,
                    compressive_strength,
                    FlexuralMember,
                    ShapeType,
                    flexural_strength,
                    ShearMember,
                    shear_strength,
                    combined_axial_flexure,
                ),
            )
            ratio = max((c.ratio for c in result), default=0.0)
            if ratio > worst_ratio:
                worst_ratio = ratio
                worst = {
                    "combo": combo,
                    "station": st,
                    "section": section,
                    "checks": result,
                }

    if worst is None:
        raise DesignError(f"member {inp.mark} has no force results to check")

    return MemberDesign(
        mark=inp.mark,
        member_type=inp.member_type,
        section_id=inp.taper.mark,
        length=L,
        governing_combination=worst["combo"],
        forces=worst["station"],
        checks=tuple(worst["checks"]),
        weight=inp.taper.weight,
        note=f"governing section {worst['section'].mark} at "
        f"x = {worst['station'].station:.0f} mm",
    )


def _classify_singly_symmetric_web(
    classify_flexural, FlexuralElement, *, lam, E_ksi, Fy_ksi,
    Mp_over_My, hc_over_hp,
):
    """Table B4.1b Case 16, including the case where lambda_p CAPS OUT.

    Case 16 is::

        lam_p = (hc/hp)*sqrt(E/Fy) / (0.54*Mp/My - 0.09)^2  <=  lam_r
        lam_r = 5.70*sqrt(E/Fy)

    The ``<= lam_r`` is AISC's own limit and it BITES on ordinary sections:
    a singly-symmetric welded I with a stocky web and a modest Mp/My drives
    the expression above ``lam_r``, it is capped, and ``lam_p`` and
    ``lam_r`` come back equal.  ``pyaisc360`` then refuses the pair - its
    validator reads equal limits as transposed arguments - and the design
    of that member raises instead of returning an answer.

    That is not a hypothetical.  Sweeping realistic PEB plate girders, two
    thirds of the SINGLY-SYMMETRIC ones hit it, and a singly-symmetric
    rafter (a wide top flange over a narrow bottom one) is completely
    ordinary.  It never showed up because the example building and every
    fixture use ``WeldedI.symmetric``, which takes the Case 15 branch.

    When the two limits coincide the classification is not ambiguous, it is
    simply degenerate: the noncompact band is empty, so the web is compact
    at ``lam <= lam_p`` and slender above ``lam_r``.  That is what this
    returns, and only in that case - any other error is re-raised, because
    a blanket except here would swallow a real geometry problem.
    """
    from pyaisc360.chapter_b import FlexuralSlenderness
    from pyaisc360.core.exceptions import AISC360Error

    try:
        return classify_flexural(
            FlexuralElement.SINGLY_SYMMETRIC_I_WEB,
            lam, E_ksi, Fy_ksi,
            Mp_over_My=Mp_over_My,
            hc_over_hp=hc_over_hp,
        )
    except AISC360Error as exc:
        if "transposed" not in str(exc):
            raise
    lam_r = 5.70 * math.sqrt(E_ksi / Fy_ksi)
    web_class = (
        FlexuralSlenderness.COMPACT if lam <= lam_r
        else FlexuralSlenderness.SLENDER
    )
    return web_class, lam_r, lam_r


def _check_station(
    *,
    section: WeldedI,
    st: MemberForces,
    inp: MemberDesignInput,
    stations: Sequence[MemberForces],
    steel,
    basis,
    classes,
) -> list[MemberCheck]:
    (
        CompressionMember,
        compressive_strength,
        FlexuralMember,
        ShapeType,
        flexural_strength,
        ShearMember,
        shear_strength,
        combined_axial_flexure,
    ) = classes

    L = inp.taper.length
    adapted = _adapt(section)
    checks: list[MemberCheck] = []

    # Which flange is in compression decides which brace set applies.  A
    # sagging moment compresses the top flange; a hogging moment - which is
    # what uplift and the eave haunch produce - compresses the bottom, where
    # the braces are further apart.  Getting this backwards is the classic
    # way to pass a rafter that fails under wind.
    which = "top" if st.Mz >= 0 else "bottom"
    a, b = inp.bracing.segment_containing(st.station, which, L)
    Lb = b - a

    seg_moments = [s.Mz for s in stations if a - 1e-6 <= s.station <= b + 1e-6]
    Cb = cb_from_moments(seg_moments)

    # -- Chapter B, element classification -----------------------------------
    # Chapter F dispatches to F2 / F3 / F4 / F5 on the flange and web
    # classes, so they have to be established first.  BUILT_UP_I_FLANGE is
    # the right element for a welded girder: its limits carry the kc term,
    # which a rolled shape's do not.
    from pyaisc360.chapter_b import (
        FlexuralElement,
        classify_flexural,
    )

    E_ksi = steel.E
    Fy_ksi = steel.Fy
    Fy_mpa = inp.materials.Fy_builtup
    comp_flange = "top" if st.Mz >= 0 else "bottom"

    # Case 11 needs h/tw (for kc) and FL from Eq. F4-6a/F4-6b.  FL is NOT Fy;
    # substituting Fy overstates lambda_r by up to 20% and would classify a
    # slender flange as noncompact.
    lam_flange = (
        section.lambda_flange_top
        if comp_flange == "top"
        else section.lambda_flange_bot
    )
    flange_class, lam_pf, lam_rf = classify_flexural(
        FlexuralElement.BUILT_UP_I_FLANGE,
        lam_flange,
        E_ksi,
        Fy_ksi,
        h_over_tw=section.lambda_web,
        FL=mpa_to_ksi(section.FL(Fy_mpa, comp_flange)),
    )

    if section.is_symmetric:
        web_class, lam_pw, lam_rw = classify_flexural(
            FlexuralElement.I_WEB, section.lambda_web, E_ksi, Fy_ksi
        )
    else:
        # Case 16 wants Mp/My and hc/hp.  Both are dimensionless, so they
        # cross the unit boundary untouched.
        Sx = section.Sx_top if comp_flange == "top" else section.Sx_bot
        web_class, lam_pw, lam_rw = _classify_singly_symmetric_web(
            classify_flexural,
            FlexuralElement,
            lam=section.lambda_web,
            E_ksi=E_ksi,
            Fy_ksi=Fy_ksi,
            Mp_over_My=section.Zx / Sx,
            hc_over_hp=section.hc(comp_flange) / section.hp(comp_flange),
        )

    # -- Chapter F, flexure --------------------------------------------------
    fm = FlexuralMember(
        section=adapted,
        steel=steel,
        shape=ShapeType.BUILT_UP_I,
        Lb=mm_to_in(Lb),
        Cb=Cb,
    )
    # F2 / F3 / F4 / F5 each want a different argument set, and the library
    # refuses to guess.  One superset is assembled here and the dispatcher
    # takes what its branch needs; nothing is invented - every value comes
    # from the section or from Chapter B above.
    if comp_flange == "top":
        Sxc, Sxt = section.Sx_top, section.Sx_bot
        bfc, tfc = section.bf_top, section.tf_top
        Iyc = section.Iyc
    else:
        Sxc, Sxt = section.Sx_bot, section.Sx_top
        bfc, tfc = section.bf_bot, section.tf_bot
        Iyc = section.Iyt

    section_kwargs = dict(
        flange_class=flange_class,
        web_class=web_class,
        lam=lam_flange,
        lam_f=lam_flange,
        lam_pf=lam_pf,
        lam_rf=lam_rf,
        lam_w=section.lambda_web,
        lam_pw=lam_pw,
        lam_rw=lam_rw,
        h_over_tw=section.lambda_web,
        Sxc=mm3_to_in3(Sxc),
        Sxt=mm3_to_in3(Sxt),
        hc=mm_to_in(section.hc(comp_flange)),
        bfc=mm_to_in(bfc),
        tfc=mm_to_in(tfc),
        tw=mm_to_in(section.tw),
        ho=mm_to_in(section.ho),
        Iyc_over_Iy=Iyc / section.Iy,
    )
    f_res = _dispatch_flexure(flexural_strength, fm, basis, section_kwargs)
    Mc = kipin_to_knm(f_res.available)
    checks.append(
        _mk(
            f"Flexure ({f_res.governing.limit_state.value})",
            _cite(f_res),
            abs(st.Mz),
            Mc,
            "kN.m",
            note=f"Lb = {Lb:.0f} mm ({which} flange), Cb = {Cb:.3f}",
        )
    )

    # -- Chapter G, shear ----------------------------------------------------
    # ``rolled=False`` matters: the G2.1(a) phi = 1.00 exception applies to
    # rolled I-shapes with h/tw <= 2.24 sqrt(E/Fy), not to a welded girder,
    # which takes phi = 0.90.  ``h_over_tw`` must be passed explicitly - the
    # library defaults it to zero rather than guessing from the section.
    sm = ShearMember(
        section=adapted,
        steel=steel,
        rolled=False,
        h_over_tw=section.lambda_web,
    )
    g_res = shear_strength(sm, basis=basis)
    Vc = kip_to_kn(g_res.available)
    checks.append(
        _mk(
            f"Shear ({g_res.governing.limit_state.value})",
            _cite(g_res),
            abs(st.Vy),
            Vc,
            "kN",
        )
    )

    # -- Chapter F6 / G6 / H3, the out-of-plane actions ----------------------
    #
    # A plane frame produces P, Vy and Mz and NOTHING ELSE - the other three
    # components are identically zero, so none of these blocks runs and the
    # 2D calculation sheet is unchanged.  They exist for the 3D building
    # model, which does produce them.
    #
    # Each is emitted only when its action is actually present.  That is not
    # a shortcut past working agreement (d): (d) is about a check that COULD
    # NOT BE EVALUATED reading as a pass.  Here there is nothing to evaluate
    # - the action is zero - and printing "Torsion 0.000 / 27.0 MPa, ratio
    # 0.000" on every station of every plane-frame member would bury the
    # checks that mean something.
    Mcy = 0.0
    if abs(st.My) > _ACTION_TOL:
        from pyaisc360.chapter_f import f6_strength

        # MINOR-axis bending puts BOTH flanges partly in compression, so the
        # governing element is the more slender of the two - not the one the
        # major-axis check chose, which depends on the sign of Mz.
        lam_y = max(section.lambda_flange_top, section.lambda_flange_bot)
        which_y = (
            "top" if section.lambda_flange_top >= section.lambda_flange_bot
            else "bottom"
        )
        fclass_y, lpf_y, lrf_y = classify_flexural(
            FlexuralElement.BUILT_UP_I_FLANGE,
            lam_y,
            E_ksi,
            Fy_ksi,
            h_over_tw=section.lambda_web,
            FL=mpa_to_ksi(section.FL(Fy_mpa, which_y)),
        )
        fmy = FlexuralMember(
            section=adapted, steel=steel, shape=ShapeType.BUILT_UP_I,
            Lb=0.0, Cb=1.0,
        )
        # Sect. F6 has no lateral-torsional buckling limit state - a member
        # already bent about its weak axis has nothing weaker to buckle
        # into - so Lb and Cb do not enter it and are passed inert.
        f6 = f6_strength(
            fmy, flange_class=fclass_y, lam=lam_y, lam_pf=lpf_y, lam_rf=lrf_y
        )
        gov_y = min(f6, key=lambda r: r.nominal)
        Mny = kipin_to_knm(gov_y.nominal)
        Mcy = 0.90 * Mny if basis.name == "LRFD" else Mny / 1.67
        checks.append(
            _mk(
                f"Minor-axis flexure ({gov_y.limit_state.value})",
                str(gov_y.citation),
                abs(st.My),
                Mcy,
                "kN.m",
                note=f"{which_y} flange governs, b/2t = {lam_y:.2f}",
            )
        )

    if abs(st.Vz) > _ACTION_TOL:
        from pyaisc360.chapter_g import OMEGA_V, PHI_V, g6_strength

        # Eq. G6-1 is stated PER SHEAR-RESISTING ELEMENT and an I-shape has
        # two flanges, so the two are summed rather than one doubled - on a
        # singly-symmetric section they are not the same element.
        Vn_y = 0.0
        for bf, tf in (
            (section.bf_top, section.tf_top),
            (section.bf_bot, section.tf_bot),
        ):
            v_el, _cv2 = g6_strength(
                Fy_ksi, mm_to_in(bf), mm_to_in(tf), E_ksi
            )
            Vn_y += v_el
        Vcz = kip_to_kn(
            PHI_V * Vn_y if basis.name == "LRFD" else Vn_y / OMEGA_V
        )
        checks.append(
            _mk(
                "Weak-axis shear (flange shear yielding)",
                "AISC 360-16, Eq. G6-1 (Sect. G6), p. 16.1-75",
                abs(st.Vz),
                Vcz,
                "kN",
                note="both flanges, phi_v = 0.90 - the G2.1(a) phi = 1.00 "
                     "exception is a WEB provision and does not reach G6",
            )
        )

    if abs(st.T) > _ACTION_TOL:
        from pyaisc360.chapter_h import non_hss_torsional_stress

        # ST VENANT ONLY.  See COVERAGE_GAPS: the warping normal and warping
        # shear stresses need the solution of the torsion differential
        # equation with this member's end conditions (AISC Design Guide 9),
        # and the analysis does not produce the end conditions to solve it
        # with.  What IS computed is exact: the uniform-torsion shear stress
        # in the thickest element,
        #
        #     tau = T * t / J
        #
        # checked against Sect. H3.3's available torsional stress.  The note
        # says what is missing, on every station where it is missing.
        t_max = max(section.tf_top, section.tf_bot, section.tw)
        tau_mpa = abs(st.T) * 1.0e6 * t_max / section.J    # N.mm x mm / mm^4
        h3 = non_hss_torsional_stress(Fy_ksi)
        Fn_mpa = ksi_to_mpa(h3.available)
        checks.append(
            _mk(
                f"Torsion, St Venant ({h3.governing.limit_state.value})",
                str(h3.governing.citation),
                tau_mpa,
                Fn_mpa,
                "MPa",
                note=(
                    f"tau = T.t/J on t = {t_max:.0f} mm, J = {section.J:.0f} "
                    f"mm4. UNIFORM TORSION ONLY - warping stresses are not "
                    f"included and need the DG 9 solution"
                ),
            )
        )

    # -- Chapter E or D, axial ----------------------------------------------
    Pc_kn = 0.0
    if st.P > 0.0:  # compression
        # Out-of-plane unbraced length is the compression-flange brace
        # spacing; in-plane is the whole member with K from the input.
        Lcy = Lb
        cm = CompressionMember(
            section=adapted,
            steel=steel,
            Lcx=mm_to_in(inp.K_in_plane * L),
            Lcy=mm_to_in(Lcy),
        )
        e_res = compressive_strength(cm, basis=basis)
        Pc_kn = kip_to_kn(e_res.available)
        checks.append(
            _mk(
                f"Compression ({e_res.governing.limit_state.value})",
                _cite(e_res),
                st.P,
                Pc_kn,
                "kN",
                note=f"Lcx = {inp.K_in_plane * L:.0f} mm, Lcy = {Lcy:.0f} mm",
            )
        )
    elif st.P < 0.0:  # tension
        Pn = section.A * inp.materials.Fy_builtup / 1000.0     # kN, D2-1
        Pc_kn = 0.90 * Pn if basis.name == "LRFD" else Pn / 1.67
        checks.append(
            _mk(
                "Tension (yielding on the gross section)",
                "AISC 360-16 Eq. D2-1",
                abs(st.P),
                Pc_kn,
                "kN",
            )
        )

    # -- Chapter H, interaction ---------------------------------------------
    #
    # BIAXIAL. Eqs. H1-1a/b carry both bending terms and always did; the
    # minor-axis one was zero because a plane frame has no My. It is now
    # passed whenever the 3D model produced one, and dropped - not
    # guessed - when Mcy could not be established.
    if Pc_kn > 0.0 and Mc > 0.0:
        biaxial = abs(st.My) > _ACTION_TOL and Mcy > 0.0
        h_res = combined_axial_flexure(
            Pr=kn_to_kip(abs(st.P)),
            Pc=kn_to_kip(Pc_kn),
            Mrx=knm_to_kipin(abs(st.Mz)),
            Mcx=knm_to_kipin(Mc),
            Mry=knm_to_kipin(abs(st.My)) if biaxial else 0.0,
            Mcy=knm_to_kipin(Mcy) if biaxial else 0.0,
        )
        note = f"Pr/Pc = {abs(st.P) / Pc_kn:.3f}"
        if biaxial:
            note += (
                f", Mrx/Mcx = {abs(st.Mz) / Mc:.3f}, "
                f"Mry/Mcy = {abs(st.My) / Mcy:.3f}"
            )
        elif abs(st.My) > _ACTION_TOL:
            # An My with no Mcy would silently drop out of H1-1 and the
            # member would read as passing on a demand nobody checked.
            note += (
                f"; My = {abs(st.My):.2f} kN.m is NOT in this interaction - "
                f"no minor-axis capacity was established"
            )
        checks.append(
            MemberCheck(
                name=(
                    f"Combined axial + biaxial flexure "
                    f"({h_res.limit_state.value})"
                    if biaxial else
                    f"Combined axial + flexure ({h_res.limit_state.value})"
                ),
                reference=str(h_res.citation),
                demand=h_res.ratio,
                capacity=1.0,
                ratio=h_res.ratio,
                unit="-",
                note=note,
            )
        )

    return checks


def _dispatch_flexure(flexural_strength, member, basis, kwargs: dict[str, Any]):
    """Call ``flexural_strength`` with only the arguments its branch accepts.

    ``flexural_strength`` forwards ``**kwargs`` straight to F2, F3, F4 or F5,
    and those functions reject anything they do not declare.  Rather than
    reimplement the branch table - which would be a second copy of the
    library's own dispatch logic, guaranteed to drift - the superset is
    offered and the rejected names are dropped based on the error the library
    itself raises.
    """
    remaining = dict(kwargs)
    for _ in range(len(kwargs) + 1):
        try:
            return flexural_strength(member, basis=basis, **remaining)
        except TypeError as exc:
            msg = str(exc)
            dropped = [k for k in remaining if f"'{k}'" in msg and "unexpected" in msg]
            if not dropped:
                raise
            for k in dropped:
                remaining.pop(k)
    raise DesignError(  # pragma: no cover - loop bound is generous
        "could not settle the Chapter F argument set"
    )


#: Below this an action is TAKEN AS ABSENT, in kN or kN.m.
#:
#: A plane frame returns exactly 0.0 for Vz, T and My - they are restrained
#: out of the model, not computed small - so any positive threshold works
#: for it. What this guards is the 3D model, where an out-of-plane action
#: on a symmetric building comes back as solver noise around 1e-12 rather
#: than as a clean zero. 1e-9 kN is a nanonewton: far below noise, and far
#: below anything that could matter to a plate girder.
_ACTION_TOL = 1.0e-9


def _mk(
    name: str, reference: str, demand: float, capacity: float, unit: str,
    note: str = "",
) -> MemberCheck:
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


def _cite(result: Any) -> str:
    """The citation string of the governing limit state.

    pyaisc360 renders a full citation - specification, equation, section and
    page - so it is carried through verbatim rather than reformatted.  A
    reviewer can then find the clause without trusting this package's
    paraphrase of it.
    """
    gov = getattr(result, "governing", None)
    cit = getattr(gov, "citation", None)
    return str(cit) if cit is not None else "AISC 360-16"


# ---------------------------------------------------------------------------
# Available strengths at a station, without a demand
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AvailableStrengths:
    """phi*Rn (LRFD) or Rn/Omega (ASD) at one station, kN and kN.m.

    ``check_member`` needs a demand before it can report anything.  The
    connection layer needs the CAPACITY of the connected member with no
    demand attached, because the design force is the larger of the analysis
    force and a fraction of that capacity.  This exposes the same numbers,
    computed by the same code path, without inventing a demand to get them.
    """

    Pc: float          # axial compression
    Mc: float          # flexure
    Vc: float          # shear
    Cb: float
    Lb: float
    citations: dict[str, str] = field(default_factory=dict)


def available_strengths(
    section: WeldedI,
    materials: Materials,
    *,
    Lb: float,
    Lc: float | None = None,
    Cb: float = 1.0,
    method: DesignMethod = DesignMethod.LRFD,
    compression_flange: str = "top",
) -> AvailableStrengths:
    """Available flexural, shear and compressive strength of one section.

    ``Lb`` is the unbraced length for LTB and for weak-axis buckling; ``Lc``
    is the in-plane effective length, defaulting to ``Lb`` when the caller
    has nothing better.  ``Cb`` defaults to the always-permitted 1.0 rather
    than to a guess.
    """
    taper = TaperedMember(start=section, end=section, length=max(Lc or Lb, 1.0))
    n = max(1, int(round(taper.length / Lb))) if Lb > 0 else 1
    braces = tuple(taper.length * i / n for i in range(1, n))
    sign = 1.0 if compression_flange == "top" else -1.0

    # A unit moment of the right sign selects the correct flange's braces;
    # a unit axial keeps the compression branch alive.  The DEMANDS are
    # irrelevant here - only the capacities are read back.
    stations = [
        MemberForces(P=1.0, Vy=1.0, Mz=sign * 1.0, station=taper.length * i / 8)
        for i in range(9)
    ]
    design = check_member(
        MemberDesignInput(
            mark="_cap",
            member_type=MemberType.RAFTER,
            taper=taper,
            forces={"_": stations},
            bracing=BracingSpec(top_flange=braces, bottom_flange=braces),
            materials=materials,
            method=method,
        )
    )

    out = {"Compression": 0.0, "Flexure": 0.0, "Shear": 0.0}
    cites: dict[str, str] = {}
    used_cb, used_lb = Cb, Lb
    for c in design.checks:
        for key in out:
            if c.name.startswith(key):
                out[key] = c.capacity
                cites[key] = c.reference

    # Cb scales the LTB branch linearly up to Mp; applying it here rather
    # than inside the loop keeps the Cb = 1.0 result recoverable.
    Mp = materials.Fy_builtup * section.Zx * 1e-6
    phi = 0.90 if method is DesignMethod.LRFD else 1.0 / 1.67
    Mc = min(out["Flexure"] * Cb, phi * Mp)

    return AvailableStrengths(
        Pc=out["Compression"],
        Mc=Mc,
        Vc=out["Shear"],
        Cb=used_cb,
        Lb=used_lb,
        citations=cites,
    )


# ---------------------------------------------------------------------------
# The same sweep, kept rather than collapsed
# ---------------------------------------------------------------------------


def station_utilisations(inp: MemberDesignInput, combo: str) -> tuple[float, ...]:
    """The worst AISC 360-16 ratio at EVERY station of one combination.

    :func:`check_member` runs this same sweep and keeps only its governing
    station, because that is the design answer.  A contour needs the sweep
    itself: a member is not one number, and the picture is drawn to show
    where along it the demand actually sits.

    The two cannot drift apart - both call :func:`_check_station`, so
    ``max(station_utilisations(inp, combo))`` is by construction the ratio
    :func:`check_member` reports when that combination governs.

    Returns one ratio per station, in the order the stations were given.
    """
    try:
        from pyaisc360 import Basis
        from pyaisc360.chapter_e import CompressionMember, compressive_strength
        from pyaisc360.chapter_f import (
            FlexuralMember,
            ShapeType,
            flexural_strength,
        )
        from pyaisc360.chapter_g import ShearMember, shear_strength
        from pyaisc360.chapter_h import combined_axial_flexure
    except ImportError as exc:  # pragma: no cover - environment issue
        raise DesignError(
            "pyaisc360 is required for member design. Put the library "
            "folder on sys.path."
        ) from exc

    stations = list(inp.forces.get(combo, ()))
    if not stations:
        raise DesignError(
            f"member {inp.mark} has no stations for combination {combo!r}"
        )

    basis = Basis.LRFD if inp.method is DesignMethod.LRFD else Basis.ASD
    steel = _steel(inp.materials)
    L = inp.taper.length
    classes = (
        CompressionMember,
        compressive_strength,
        FlexuralMember,
        ShapeType,
        flexural_strength,
        ShearMember,
        shear_strength,
        combined_axial_flexure,
    )

    out: list[float] = []
    for st in stations:
        section = inp.taper.at(min(max(st.station, 0.0), L))
        checks = _check_station(
            section=section, st=st, inp=inp, stations=stations,
            steel=steel, basis=basis, classes=classes,
        )
        out.append(max((c.ratio for c in checks), default=0.0))
    return tuple(out)
