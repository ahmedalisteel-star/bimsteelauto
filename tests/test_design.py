"""Behaviour tests for bimsteelauto.design - bracing, plumbing, error paths."""

import pytest

from bimsteelauto.design import (
    BracingSpec,
    DesignError,
    MemberDesignInput,
    check_member,
)
from bimsteelauto.sections import TaperedMember, WeldedI
from bimsteelauto.spec import DesignMethod, Materials, MemberForces, MemberType

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


MATERIALS = Materials(
    Fy_builtup=345.0,
    Fu_builtup=450.0,
    Fy_cold_formed=345.0,
    Fu_cold_formed=450.0,
    bolt_grade="A325",
    anchor_grade="F1554 Gr.36",
    fc_concrete=30.0,
)

SECTION = WeldedI.symmetric(d=500, tw=10, bf=250, tf=16)


# ---------------------------------------------------------------------------
# BracingSpec
# ---------------------------------------------------------------------------


def test_ends_are_always_brace_points():
    b = BracingSpec(top_flange=(3000.0,))
    assert b.points("top", 9000.0) == (0.0, 3000.0, 9000.0)


def test_points_outside_the_member_are_dropped():
    b = BracingSpec(top_flange=(-100.0, 3000.0, 99999.0))
    assert b.points("top", 9000.0) == (0.0, 3000.0, 9000.0)


def test_duplicate_end_points_are_not_repeated():
    b = BracingSpec(top_flange=(0.0, 9000.0, 3000.0))
    assert b.points("top", 9000.0) == (0.0, 3000.0, 9000.0)


def test_unbraced_member_is_one_segment():
    b = BracingSpec()
    assert b.points("top", 9000.0) == (0.0, 9000.0)
    assert b.segment_containing(4500.0, "top", 9000.0) == (0.0, 9000.0)


def test_segment_containing_finds_the_right_bay():
    b = BracingSpec(top_flange=(3000.0, 6000.0))
    assert b.segment_containing(1500.0, "top", 9000.0) == (0.0, 3000.0)
    assert b.segment_containing(4500.0, "top", 9000.0) == (3000.0, 6000.0)
    assert b.segment_containing(7500.0, "top", 9000.0) == (6000.0, 9000.0)


def test_segment_at_an_exact_brace_point_is_resolved():
    b = BracingSpec(top_flange=(3000.0,))
    a, c = b.segment_containing(3000.0, "top", 9000.0)
    assert (a, c) in {(0.0, 3000.0), (3000.0, 9000.0)}


def test_at_spacing_places_even_braces():
    b = BracingSpec.at_spacing(9000.0, 1500.0)
    assert b.points("top", 9000.0) == (0.0, 1500.0, 3000.0, 4500.0, 6000.0, 7500.0, 9000.0)


def test_at_spacing_without_bottom_leaves_bottom_unbraced():
    b = BracingSpec.at_spacing(9000.0, 1500.0)
    assert b.points("bottom", 9000.0) == (0.0, 9000.0)


def test_the_two_flanges_are_independent():
    b = BracingSpec.at_spacing(12000.0, 1500.0, 6000.0)
    assert len(b.points("top", 12000.0)) == 9
    assert b.points("bottom", 12000.0) == (0.0, 6000.0, 12000.0)


# ---------------------------------------------------------------------------
# check_member plumbing
# ---------------------------------------------------------------------------


def _input(**kw):
    taper = TaperedMember(start=SECTION, end=SECTION, length=9000.0)
    stations = [
        MemberForces(P=200.0, Vy=60.0, Mz=150.0, station=9000.0 * i / 8)
        for i in range(9)
    ]
    base = dict(
        mark="R1",
        member_type=MemberType.RAFTER,
        taper=taper,
        forces={"1.2D+1.6Lr": stations},
        bracing=BracingSpec.at_spacing(9000.0, 1500.0, 3000.0),
        materials=MATERIALS,
    )
    base.update(kw)
    return MemberDesignInput(**base)


def test_check_member_returns_a_populated_design():
    d = check_member(_input())
    assert d.mark == "R1"
    assert d.section_id == SECTION.mark
    assert d.governing_combination == "1.2D+1.6Lr"
    assert d.checks
    assert d.utilisation > 0.0
    assert d.weight > 0.0


def test_every_check_carries_a_citation():
    """A calc sheet without clause numbers is not reviewable."""
    d = check_member(_input())
    for c in d.checks:
        assert "AISC 360-16" in c.reference, c


def test_flexure_shear_compression_and_interaction_are_all_present():
    d = check_member(_input())
    names = " ".join(c.name for c in d.checks)
    for expected in ("Flexure", "Shear", "Compression", "Combined"):
        assert expected in names, names


def test_governing_check_is_the_worst_ratio():
    d = check_member(_input())
    assert d.governing_check.ratio == max(c.ratio for c in d.checks)
    assert d.utilisation == d.governing_check.ratio


def test_note_records_the_governing_station():
    d = check_member(_input())
    assert "governing section" in d.note
    assert "mm" in d.note


def test_tension_uses_chapter_d_not_chapter_e():
    stations = [
        MemberForces(P=-300.0, Vy=10.0, Mz=50.0, station=9000.0 * i / 8)
        for i in range(9)
    ]
    d = check_member(_input(forces={"C": stations}))
    names = " ".join(c.name for c in d.checks)
    assert "Tension" in names
    assert "Compression" not in names


def test_zero_axial_skips_the_axial_check():
    stations = [
        MemberForces(P=0.0, Vy=10.0, Mz=50.0, station=9000.0 * i / 8)
        for i in range(9)
    ]
    d = check_member(_input(forces={"C": stations}))
    names = " ".join(c.name for c in d.checks)
    assert "Compression" not in names and "Tension" not in names


def test_asd_gives_a_lower_capacity_than_lrfd():
    lrfd = check_member(_input(method=DesignMethod.LRFD))
    asd = check_member(_input(method=DesignMethod.ASD))
    lm = next(c for c in lrfd.checks if c.name.startswith("Flexure")).capacity
    am = next(c for c in asd.checks if c.name.startswith("Flexure")).capacity
    assert am < lm
    # phi = 0.90 vs Omega = 1.67 -> the ratio is 0.90 * 1.67 = 1.503
    assert lm / am == pytest.approx(0.90 * 1.67, rel=0.01)


def test_more_combinations_take_the_worst():
    mild = [MemberForces(P=50.0, Vy=10.0, Mz=40.0, station=9000.0 * i / 8) for i in range(9)]
    harsh = [MemberForces(P=200.0, Vy=60.0, Mz=300.0, station=9000.0 * i / 8) for i in range(9)]
    d = check_member(_input(forces={"mild": mild, "harsh": harsh}))
    assert d.governing_combination == "harsh"


def test_empty_forces_is_an_error():
    with pytest.raises(DesignError, match="no force results"):
        check_member(_input(forces={}))


def test_tapered_member_reports_the_governing_station_section():
    taper = TaperedMember(
        start=WeldedI.symmetric(d=900, tw=8, bf=250, tf=16),
        end=WeldedI.symmetric(d=400, tw=8, bf=250, tf=16),
        length=9000.0,
    )
    # Constant moment along a tapering member: the SHALLOW end must govern.
    stations = [
        MemberForces(P=50.0, Vy=20.0, Mz=200.0, station=9000.0 * i / 8)
        for i in range(9)
    ]
    d = check_member(_input(taper=taper, forces={"C": stations}))
    assert "BU-4" in d.note or "x = 9000" in d.note


# ======================================================================
# Table B4.1b Case 16 - the lambda_p cap
# ======================================================================

def test_a_singly_symmetric_welded_I_can_be_designed_at_all() -> None:
    """DEFECT, found 2026-09-06: two thirds of them raised instead.

    Case 16 limits ``lam_p`` to ``lam_r``, and on a singly-symmetric welded
    I with a stocky web and a modest Mp/My the expression exceeds ``lam_r``
    and is capped - so the two come back EQUAL. ``pyaisc360``'s validator
    reads equal limits as transposed arguments and raises, and the whole
    member design went with it.

    It never showed up because the example building and every fixture use
    ``WeldedI.symmetric``, which takes the Case 15 branch. A PEB rafter with
    a wide top flange over a narrow bottom one - completely ordinary - took
    the Case 16 branch and crashed.

    Sweeping realistic plate girders: 40 of 120 designed before the fix,
    120 of 120 after.
    """
    from bimsteelauto.design.members import (
        BracingSpec,
        MemberDesignInput,
        check_member,
    )
    from bimsteelauto.sections import TaperedMember, WeldedI
    from bimsteelauto.spec import (
        DesignMethod,
        Materials,
        MemberForces,
        MemberType,
    )

    mats = Materials(
        Fy_builtup=345.0, Fu_builtup=450.0,
        Fy_cold_formed=345.0, Fu_cold_formed=450.0,
        bolt_grade="A325", anchor_grade="F1554-36", fc_concrete=30.0,
    )
    section = WeldedI(
        d=850.0, tw=7.0,
        bf_top=300.0, tf_top=24.0,
        bf_bot=250.0, tf_bot=10.0,
    )
    assert not section.is_symmetric, "this must take the Case 16 branch"

    tm = TaperedMember(start=section, end=section, length=7000.0)
    inp = MemberDesignInput(
        mark="R1", member_type=MemberType.RAFTER, taper=tm, materials=mats,
        forces={"C1": (MemberForces(P=100.0, Vy=50.0, Mz=300.0,
                                    station=3500.0),)},
        bracing=BracingSpec(top_flange=(0.0, 7000.0),
                            bottom_flange=(0.0, 7000.0)),
        method=DesignMethod.LRFD,
    )
    design = check_member(inp)
    assert design.checks
    assert 0.0 < max(c.ratio for c in design.checks) < 10.0


def test_the_capped_case_16_web_is_classified_not_guessed() -> None:
    """When the band is empty the answer is still definite.

    ``lam_p == lam_r`` leaves no noncompact band, so the web is compact up
    to the limit and slender above it. That is Table B4.1b read literally -
    it is not a fallback and it is not an approximation, and the helper
    returns the two limits equal so the caller can see which case it is in.
    """
    import math

    from pyaisc360.chapter_b import (
        FlexuralElement,
        FlexuralSlenderness,
        classify_flexural,
    )

    from bimsteelauto.design.members import _classify_singly_symmetric_web
    from bimsteelauto.design.units import mpa_to_ksi

    E_ksi, Fy_ksi = mpa_to_ksi(200_000.0), mpa_to_ksi(345.0)
    lam_r = 5.70 * math.sqrt(E_ksi / Fy_ksi)

    # Below the limit -> compact; above it -> slender.
    for lam, want in (
        (0.5 * lam_r, FlexuralSlenderness.COMPACT),
        (2.0 * lam_r, FlexuralSlenderness.SLENDER),
    ):
        cls, lp, lr = _classify_singly_symmetric_web(
            classify_flexural, FlexuralElement,
            lam=lam, E_ksi=E_ksi, Fy_ksi=Fy_ksi,
            Mp_over_My=1.05, hc_over_hp=1.6,
        )
        assert cls is want
        assert lp == pytest.approx(lr)
        assert lr == pytest.approx(lam_r, rel=1e-6)


def test_an_ordinary_case_16_section_still_goes_through_the_library() -> None:
    """The guard must not swallow the normal path.

    A section whose ``lam_p`` does NOT cap is classified by pyaisc360 as
    before, with a real noncompact band between the two limits. A blanket
    try/except here would have quietly taken over every singly-symmetric
    web in the package.
    """
    from pyaisc360.chapter_b import FlexuralElement, classify_flexural

    from bimsteelauto.design.members import _classify_singly_symmetric_web
    from bimsteelauto.design.units import mpa_to_ksi

    E_ksi, Fy_ksi = mpa_to_ksi(200_000.0), mpa_to_ksi(345.0)
    _cls, lp, lr = _classify_singly_symmetric_web(
        classify_flexural, FlexuralElement,
        lam=80.0, E_ksi=E_ksi, Fy_ksi=Fy_ksi,
        Mp_over_My=1.25, hc_over_hp=1.0,
    )
    assert lp < lr, "the cap was applied where it should not have been"


def test_a_real_geometry_error_is_still_raised() -> None:
    """The guard is narrow on purpose: only the equal-limits case is
    handled, and anything else is re-raised."""
    from pyaisc360.chapter_b import FlexuralElement, classify_flexural
    from pyaisc360.core.exceptions import AISC360Error

    from bimsteelauto.design.members import _classify_singly_symmetric_web
    from bimsteelauto.design.units import mpa_to_ksi

    with pytest.raises(AISC360Error):
        _classify_singly_symmetric_web(
            classify_flexural, FlexuralElement,
            lam=-5.0,                       # a negative width-to-thickness
            E_ksi=mpa_to_ksi(200_000.0), Fy_ksi=mpa_to_ksi(345.0),
            Mp_over_My=1.25, hc_over_hp=1.0,
        )
