"""Independent verification of bimsteelauto.design.members.

Three independent references, none of them the module under test:

1. **Exact arithmetic** for the unit boundary.  25.4 and 4.4482216152605 are
   defined constants; the conversions can be checked against them by hand.
2. **A second, unrelated AISC 360-16 implementation** - the ``aisc360.py``
   script bundled with the ``aisc-steel-design`` skill.  Different author,
   different code, same Specification.  It is used for Cb, compression,
   flexure, shear and the H1 interaction.
3. **Closed-form clause arithmetic** written out longhand in the test, for
   the places where both implementations could share a misreading - Cb at
   the two cases the Specification states explicitly, and the F2-1 plastic
   moment.

Run 2026-09-01.

Marked ``verification`` in line with the connection layer's convention.
"""

from __future__ import annotations

import importlib.util
import math
import pathlib
import sys

import pytest

from bimsteelauto.design import BracingSpec, MemberDesignInput, cb_from_moments, check_member
from bimsteelauto.design.units import (
    KN_PER_KIP,
    MM_PER_IN,
    MPA_PER_KSI,
    kip_to_kn,
    kipin_to_knm,
    kn_to_kip,
    knm_to_kipin,
    mm2_to_in2,
    mm3_to_in3,
    mm4_to_in4,
    mm_to_in,
    mpa_to_ksi,
)
from bimsteelauto.sections import TaperedMember, WeldedI
from bimsteelauto.spec import Materials, MemberForces, MemberType

pytest.importorskip("pyaisc360", reason="pyaisc360 not on sys.path")

def _api_available() -> str:
    """Confirm the installed pyaisc360 exposes the API this module uses.

    Ahmed has pyaisc360 0.1.0.dev0 from pip; these modules were written
    against the copy bundled with the steel-design-toolkit skill.  A version
    whose API differs should SKIP with a readable reason, not bury the run in
    import errors that look like a defect in this package.
    """
    try:
        from pyaisc360 import Basis, SectionAdapter  # noqa: F401
        from pyaisc360.chapter_b import FlexuralElement, classify_flexural  # noqa: F401
        from pyaisc360.chapter_e import CompressionMember, compressive_strength  # noqa: F401
        from pyaisc360.chapter_f import (  # noqa: F401
            FlexuralMember,
            ShapeType,
            flexural_strength,
        )
        from pyaisc360.chapter_g import ShearMember, shear_strength  # noqa: F401
        from pyaisc360.chapter_h import combined_axial_flexure  # noqa: F401
        from pyaisc360.materials import Steel

        Steel(name="probe", Fy=50.0, Fu=65.0, standard="probe")
    except Exception as exc:  # noqa: BLE001 - any failure means "not this API"
        return f"installed pyaisc360 does not expose the expected API: {exc}"
    return ""


_API_PROBLEM = _api_available()
pytestmark = [
    pytest.mark.skipif(bool(_API_PROBLEM), reason=_API_PROBLEM or "ok"),
]

pytestmark.append(pytest.mark.verification)


# ---------------------------------------------------------------------------
# The second implementation
# ---------------------------------------------------------------------------


def _load_reference():
    """Import the aisc-steel-design skill's aisc360.py, wherever it lives."""
    # The VENDORED copy first: it is pinned beside the tests and cannot move
    # when a skill is re-synced. The skill folders are a fallback for a
    # machine that has not vendored it yet.
    here = pathlib.Path(__file__).resolve()
    roots = [
        here.parent.parent.parent / "vendor" / "reference",   # peb/vendor/reference
        pathlib.Path.cwd() / "vendor" / "reference",
        pathlib.Path.home() / ".claude" / "skills",
        pathlib.Path("/mnt/skills"),
        pathlib.Path.cwd(),
    ]
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("aisc-steel-design/scripts/aisc360.py"):
            spec = importlib.util.spec_from_file_location("ref_aisc360", p)
            mod = importlib.util.module_from_spec(spec)
            # Must be in sys.modules BEFORE exec: @dataclass resolves
            # annotations through sys.modules[cls.__module__] and raises an
            # opaque AttributeError on None if the module is not registered.
            sys.modules["ref_aisc360"] = mod
            spec.loader.exec_module(mod)
            return mod
    return None


REF = _load_reference()
needs_ref = pytest.mark.skipif(
    REF is None, reason="the aisc-steel-design reference implementation is not present"
)


MATERIALS = Materials(
    Fy_builtup=345.0,
    Fu_builtup=450.0,
    Fy_cold_formed=345.0,
    Fu_cold_formed=450.0,
    bolt_grade="A325",
    anchor_grade="F1554 Gr.36",
    fc_concrete=30.0,
)

#: A compact, doubly-symmetric welded section, chosen so BOTH implementations
#: land in Sect. F2 and the comparison is like for like.  A slender-web plate
#: girder would put pyaisc360 in F5, which the reference script does not
#: implement - that would compare nothing.
COMPACT = WeldedI.symmetric(d=400, tw=12, bf=250, tf=20)


def _ref_section(s: WeldedI):
    sec = REF.Section(name=s.mark)
    sec.A = mm2_to_in2(s.A)
    sec.d = mm_to_in(s.d)
    sec.tw = mm_to_in(s.tw)
    sec.bf = mm_to_in(s.bf_top)
    sec.tf = mm_to_in(s.tf_top)
    sec.Ix = mm4_to_in4(s.Ix)
    sec.Iy = mm4_to_in4(s.Iy)
    sec.Sx = mm3_to_in3(s.Sx)
    sec.Zx = mm3_to_in3(s.Zx)
    sec.rx = mm_to_in(s.rx)
    sec.ry = mm_to_in(s.ry)
    sec.J = mm4_to_in4(s.J)
    sec.Cw = s.Cw / MM_PER_IN**6
    sec.ho = mm_to_in(s.ho)
    sec.bf_2tf = s.lambda_flange_top
    sec.h_tw = s.lambda_web
    # rts per Eq. F2-7, computed here rather than taken from either package.
    sec.rts = math.sqrt(math.sqrt(sec.Iy * sec.Cw) / sec.Sx)
    return sec


def _ref_steel():
    return REF.Steel("project", mpa_to_ksi(345.0), mpa_to_ksi(450.0))


def _run(section: WeldedI, *, P, V, M, Lb, n_bays: int = 4):
    """Check a prismatic member through this package at a KNOWN Lb.

    The member length is built as a whole multiple of ``Lb`` and the braces
    are placed at exact multiples of it.  The first version instead rounded
    a fixed 6000 mm length into brace bays, so asking for Lb = 4000 silently
    produced Lb = 3000 - and the comparison against the reference then
    "failed" by 15% for a reason that had nothing to do with either
    implementation of Chapter E.
    """
    length = Lb * n_bays
    taper = TaperedMember(start=section, end=section, length=length)
    braces = tuple(Lb * i for i in range(1, n_bays))
    stations = [
        MemberForces(P=P, Vy=V, Mz=M, station=length * i / 8) for i in range(9)
    ]
    return check_member(
        MemberDesignInput(
            mark="T1",
            member_type=MemberType.RAFTER,
            taper=taper,
            forces={"C": stations},
            bracing=BracingSpec(top_flange=braces, bottom_flange=braces),
            materials=MATERIALS,
        )
    )


def _capacity(design, prefix: str) -> float:
    for c in design.checks:
        if c.name.startswith(prefix):
            return c.capacity
    raise AssertionError(f"no check starting {prefix!r} in {[c.name for c in design.checks]}")


# ---------------------------------------------------------------------------
# 1. Unit boundary against exact arithmetic
# ---------------------------------------------------------------------------


def test_inch_is_exact():
    assert MM_PER_IN == 25.4


def test_kip_is_exact():
    """1 kip = 1000 lbf, 1 lbf = 4.4482216152605 N by definition."""
    assert KN_PER_KIP == 4.4482216152605


def test_ksi_is_derived_not_typed():
    """1 ksi = 6.894757293168361 MPa, to the published value."""
    assert MPA_PER_KSI == pytest.approx(6.894757293168361, rel=1e-12)


def test_round_trips_are_exact():
    for v in (1.0, 25.4, 345.0, 1e6):
        assert kip_to_kn(kn_to_kip(v)) == pytest.approx(v, rel=1e-12)
        assert kipin_to_knm(knm_to_kipin(v)) == pytest.approx(v, rel=1e-12)


def test_moment_conversion_by_hand():
    """1 kN.m = 8.850745791327185 kip.in - worked out independently.

    1 kN.m = (1/4.4482216152605) kip * (1000/25.4) in
    """
    expected = (1.0 / 4.4482216152605) * (1000.0 / 25.4)
    assert knm_to_kipin(1.0) == pytest.approx(expected, rel=1e-12)
    assert knm_to_kipin(1.0) == pytest.approx(8.850745791327185, rel=1e-10)


def test_a_known_grade_converts_correctly():
    """345 MPa is 50.0 ksi to three figures - the standard plate grade."""
    assert mpa_to_ksi(345.0) == pytest.approx(50.04, abs=0.01)


# ---------------------------------------------------------------------------
# 2. Cb against the Specification's own stated cases
# ---------------------------------------------------------------------------


def test_cb_uniform_moment_is_one():
    """Constant moment over the segment: Cb = 1.0 exactly, by Eq. F1-1."""
    assert cb_from_moments([100.0] * 9) == pytest.approx(1.0, rel=1e-12)


def test_cb_simple_beam_udl_is_1_14():
    """A parabolic diagram over the whole span gives the textbook 1.14."""
    n = 33
    m = [1.0 - (2.0 * i / (n - 1) - 1.0) ** 2 for i in range(n)]
    assert cb_from_moments(m) == pytest.approx(1.14, abs=0.01)


def test_cb_linear_reversal_is_2_27():
    """Equal and opposite end moments, the classic Cb = 2.27 case."""
    n = 33
    m = [1.0 - 2.0 * i / (n - 1) for i in range(n)]
    assert cb_from_moments(m) == pytest.approx(2.27, abs=0.02)


def test_cb_is_capped_at_three():
    m = [1.0] + [0.0] * 8
    assert cb_from_moments(m) <= 3.0


def test_cb_zero_moment_is_one():
    assert cb_from_moments([0.0] * 5) == 1.0


@needs_ref
def test_cb_matches_the_reference_implementation():
    """Same four ordinates, two independently written Eq. F1-1s."""
    n = 33
    m = [1.0 - (2.0 * i / (n - 1) - 1.0) ** 2 for i in range(n)]
    mine = cb_from_moments(m)
    theirs = REF.Cb_factor(
        Mmax=max(m), MA=m[(n - 1) // 4], MB=m[(n - 1) // 2], MC=m[3 * (n - 1) // 4]
    ).Rn
    assert mine == pytest.approx(theirs, rel=1e-9)


# ---------------------------------------------------------------------------
# 3. Member strengths against the second implementation
# ---------------------------------------------------------------------------


@needs_ref
def test_plastic_moment_by_hand():
    """Mp = Fy Zx, Eq. F2-1, in kN.m straight from the section."""
    design = _run(COMPACT, P=0.0, V=1.0, M=1.0, Lb=1000.0)
    Mc = _capacity(design, "Flexure")
    Mp = 345.0 * COMPACT.Zx * 1e-6          # MPa * mm^3 -> kN.m
    assert Mc == pytest.approx(0.90 * Mp, rel=0.005), (
        f"short unbraced length must yield: phi*Mp = {0.9 * Mp:.2f} kN.m"
    )


@needs_ref
@pytest.mark.parametrize("Lb", [1500.0, 3000.0, 6000.0])
def test_flexural_strength_matches_reference(Lb):
    """LTB across all three zones: Lb < Lp, Lp < Lb < Lr, and Lb > Lr."""
    design = _run(COMPACT, P=0.0, V=1.0, M=100.0, Lb=Lb)
    mine = _capacity(design, "Flexure")

    sec, steel = _ref_section(COMPACT), _ref_steel()
    cb = next(c for c in design.checks if c.name.startswith("Flexure"))
    cb_used = float(cb.note.split("Cb = ")[1])
    r = REF.flexure_strength(sec, steel, Lb=mm_to_in(Lb), Cb=cb_used)
    theirs = kipin_to_knm(r.design("LRFD"))

    assert mine == pytest.approx(theirs, rel=0.02), (
        f"Lb = {Lb} mm: this package {mine:.2f} vs reference {theirs:.2f} kN.m"
    )


@needs_ref
def test_shear_nominal_strength_matches_reference():
    """Vn = 0.6 Fy Aw Cv1 must agree.  The RESISTANCE FACTOR must not.

    AISC 360-16 Sect. G2.1(a) gives phi_v = 1.00 only for "webs of ROLLED
    I-shaped members" with h/tw <= 2.24 sqrt(E/Fy).  A welded plate girder
    does not qualify and takes phi_v = 0.90.

    The reference script hardcodes the rolled case - its own docstring says
    "Shear strength of a ROLLED I-shape web" - so its available strength is
    11.1% high for this section.  The nominal strengths are compared here
    because that is the part both implementations are in scope for.
    """
    design = _run(COMPACT, P=0.0, V=100.0, M=1.0, Lb=1500.0)
    mine_available = _capacity(design, "Shear")

    r = REF.shear_strength(_ref_section(COMPACT), _ref_steel())
    theirs_nominal = kip_to_kn(r.Rn)

    # Nominal strengths: the physics, which must match.
    assert mine_available / 0.90 == pytest.approx(theirs_nominal, rel=0.02), (
        f"nominal shear: this package {mine_available / 0.90:.2f} vs "
        f"reference {theirs_nominal:.2f} kN"
    )

    # And the reference did indeed take the rolled-shape phi.
    assert r.phi == pytest.approx(1.00)
    assert COMPACT.lambda_web <= 2.24 * math.sqrt(29000.0 / mpa_to_ksi(345.0))


@needs_ref
def test_welded_girder_does_not_get_the_rolled_shape_phi():
    """Guards the 11% that separates this package from the reference.

    If a future edit ever lets a welded section take phi_v = 1.00, this
    fails.  The difference is unconservative, so it must not drift.
    """
    design = _run(COMPACT, P=0.0, V=100.0, M=1.0, Lb=1500.0)
    mine = _capacity(design, "Shear")
    r = REF.shear_strength(_ref_section(COMPACT), _ref_steel())
    rolled_available = kip_to_kn(r.design("LRFD"))

    assert mine < rolled_available
    assert mine / rolled_available == pytest.approx(0.90, rel=1e-3)


@needs_ref
@pytest.mark.parametrize("Lb", [1500.0, 4000.0])
def test_compressive_strength_matches_reference(Lb):
    n_bays = 4
    design = _run(COMPACT, P=500.0, V=1.0, M=1.0, Lb=Lb, n_bays=n_bays)
    mine = _capacity(design, "Compression")

    sec, steel = _ref_section(COMPACT), _ref_steel()
    # The same effective lengths this package used: Lcx over the whole
    # member, Lcy over one brace bay, both with K = 1.
    r = REF.compression_strength(
        sec, steel, KxLx=mm_to_in(Lb * n_bays), KyLy=mm_to_in(Lb)
    )
    theirs = kip_to_kn(r.design("LRFD"))

    assert mine == pytest.approx(theirs, rel=0.02), (
        f"Lb = {Lb} mm: this package {mine:.2f} vs reference {theirs:.2f} kN"
    )


@needs_ref
@pytest.mark.parametrize("P,M", [(150.0, 60.0), (900.0, 40.0), (60.0, 180.0)])
def test_interaction_matches_reference(P, M):
    """H1-1a and H1-1b, both branches of the 0.2 threshold."""
    design = _run(COMPACT, P=P, V=10.0, M=M, Lb=2000.0)
    Pc = _capacity(design, "Compression")
    Mc = _capacity(design, "Flexure")
    mine = next(c for c in design.checks if c.name.startswith("Combined")).ratio

    r = REF.beam_column_interaction(
        Pr=kn_to_kip(P), Pc=kn_to_kip(Pc), Mrx=knm_to_kipin(M), Mcx=knm_to_kipin(Mc)
    )
    # The reference returns the interaction VALUE as Rn with phi = 1.
    theirs = r.Rn

    assert mine == pytest.approx(theirs, rel=1e-6), (
        f"P = {P} kN, M = {M} kN.m: {mine:.4f} vs {theirs:.4f}"
    )


@needs_ref
def test_the_h1_branch_switches_at_the_stated_threshold():
    """Eq. H1-1a above Pr/Pc = 0.2, Eq. H1-1b below it.  Sect. H1.1."""
    low = _run(COMPACT, P=50.0, V=10.0, M=100.0, Lb=2000.0)
    high = _run(COMPACT, P=1500.0, V=10.0, M=100.0, Lb=2000.0)

    def eq(d):
        c = next(x for x in d.checks if x.name.startswith("Combined"))
        return "H1-1a" if "H1-1a" in c.reference else "H1-1b"

    Pc_low = _capacity(low, "Compression")
    assert 50.0 / Pc_low < 0.2
    assert eq(low) == "H1-1b"
    Pc_high = _capacity(high, "Compression")
    assert 1500.0 / Pc_high > 0.2
    assert eq(high) == "H1-1a"


# ---------------------------------------------------------------------------
# 4. Behaviour that both implementations must agree on physically
# ---------------------------------------------------------------------------


def test_longer_unbraced_length_reduces_flexural_capacity():
    caps = [
        _capacity(_run(COMPACT, P=0.0, V=1.0, M=100.0, Lb=Lb), "Flexure")
        for Lb in (1000.0, 2000.0, 4000.0, 6000.0)
    ]
    assert caps == sorted(caps, reverse=True), caps


def test_hogging_moment_uses_the_bottom_flange_braces():
    """The check that stops a rafter passing under uplift by accident.

    Same member, same moment magnitude, opposite sign.  With the bottom
    flange braced further apart, the hogging case must come out weaker.
    """
    taper = TaperedMember(start=COMPACT, end=COMPACT, length=12000.0)
    bracing = BracingSpec(
        top_flange=tuple(1500.0 * i for i in range(1, 8)),      # purlins
        bottom_flange=(6000.0,),                                # one fly brace
    )

    def cap(sign: float) -> float:
        stations = [
            MemberForces(P=0.0, Vy=1.0, Mz=sign * 100.0, station=12000.0 * i / 8)
            for i in range(9)
        ]
        d = check_member(
            MemberDesignInput(
                mark="T",
                member_type=MemberType.RAFTER,
                taper=taper,
                forces={"C": stations},
                bracing=bracing,
                materials=MATERIALS,
            )
        )
        return _capacity(d, "Flexure")

    assert cap(-1.0) < cap(+1.0)


# ---------------------------------------------------------------------------
# 5. Verification gate
# ---------------------------------------------------------------------------


def test_all_design_relations_are_verified():
    from bimsteelauto.design import VERIFIED

    unset = [k for k, v in VERIFIED.items() if not v]
    assert unset == [], f"unverified design relations: {unset}"


def test_coverage_gaps_are_declared_and_non_empty():
    """A gap is not a failure, but an UNDECLARED gap is.

    The calculation sheet prints this list; an empty list would claim the
    module checks everything, which it does not.
    """
    from bimsteelauto.design import COVERAGE_GAPS

    assert COVERAGE_GAPS
    assert all(isinstance(g, str) and len(g) > 20 for g in COVERAGE_GAPS)
    assert any("Design Guide 25" in g for g in COVERAGE_GAPS)
    assert any("Direct Analysis" in g for g in COVERAGE_GAPS)
