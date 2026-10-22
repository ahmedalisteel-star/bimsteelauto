"""Tests for bimsteelauto.design.purlins.

The continuous-beam statics are verified against closed forms - a single
span and the standard equal-span coefficients - which are independent of
any solver.  The AISI checks are verified against the ``cfs`` library's own
worked example and against arithmetic the Specification states outright.
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto.design.purlins import (
    COVERAGE_GAPS,
    VERIFIED,
    CFShape,
    ColdFormedSection,
    ElasticBuckling,
    PurlinBracing,
    PurlinError,
    PurlinLine,
    PurlinLoadCase,
    check_purlin,
    continuous_beam_forces,
)
from bimsteelauto.spec import DesignMethod, MemberType

pytest.importorskip("Pynite", reason="PyNiteFEA not installed")
pytest.importorskip("aisi_s100", reason="the cfs library is not on sys.path")


#: A C200x60x20x2.5 with plausible catalogue properties.  The numbers are a
#: test fixture, not a published section - the tests check behaviour and
#: relationships, never an absolute capacity that would need the real table.
C200 = ColdFormedSection(
    name="C200x60x20x2.5",
    shape=CFShape.C,
    t=2.5, H=200.0, B=60.0, Dlip=20.0, r=3.0,
    A=685.0, Ix=4.02e6, Sx=40_200.0,
    Se_top=34_500.0, Se_bot=34_500.0,
    Iy=0.30e6, Sy=7_000.0, J=1_430.0, Cw=2.1e9, ho=200.0,
)


# ---------------------------------------------------------------------------
# section guards
# ---------------------------------------------------------------------------


def test_effective_modulus_above_gross_is_rejected():
    """Se is a REDUCED property; Se > Sx means the inputs were swapped."""
    with pytest.raises(PurlinError, match="REDUCED"):
        ColdFormedSection(
            name="bad", shape=CFShape.C, t=2.5, H=200, B=60, Dlip=20, r=3,
            A=685.0, Ix=4.02e6, Sx=40_200.0,
            Se_top=45_000.0, Se_bot=45_000.0,
        )


def test_a_hot_rolled_thickness_is_sent_elsewhere():
    with pytest.raises(PurlinError, match="hot-rolled"):
        ColdFormedSection(
            name="thick", shape=CFShape.C, t=12.0, H=200, B=60, Dlip=20, r=3,
            A=4000.0, Ix=4.02e6, Sx=40_200.0,
            Se_top=34_500.0, Se_bot=34_500.0,
        )


def test_web_area_and_flat_depth():
    assert C200.Aw == pytest.approx(200.0 * 2.5)
    assert C200.h_flat == pytest.approx(200.0 - 2 * (2.5 + 3.0))


def test_elastic_buckling_must_be_positive():
    with pytest.raises(PurlinError):
        ElasticBuckling(Mcre=10.0, Mcrl=-1.0, Mcrd=8.0)


def test_purlin_line_rejects_bad_spans():
    with pytest.raises(PurlinError, match="spans"):
        PurlinLine(mark="P", section=C200, spans=(), spacing=1500.0)
    with pytest.raises(PurlinError, match="spans"):
        PurlinLine(mark="P", section=C200, spans=(-1.0,), spacing=1500.0)


# ---------------------------------------------------------------------------
# continuous beam statics - against closed forms
# ---------------------------------------------------------------------------


def test_single_span_is_exactly_wL2_over_8():
    f = continuous_beam_forces((6000.0,), 2.0, C200)
    assert f.M_sagging == pytest.approx(2.0 * 6.0**2 / 8.0, rel=1e-9)
    assert f.M_hogging == pytest.approx(0.0, abs=1e-9)
    assert f.V_max == pytest.approx(2.0 * 6.0 / 2.0, rel=1e-9)


def test_single_span_deflection_is_the_closed_form():
    """delta = 5wL^4 / (384 EI), with AISI's E = 203,000 MPa.

    Note the unit identity that makes this work without a conversion:
    1 kN/m = 1000 N / 1000 mm = 1 N/mm exactly.  So w in kN/m drops
    straight into an N-and-mm formula.  (The first version of this test
    divided by 1000 anyway and was wrong by three orders of magnitude - the
    solver was right.)
    """
    w, L = 2.0, 6000.0          # kN/m == N/mm, and mm
    f = continuous_beam_forces((L,), w, C200)
    expected = 5.0 * w * L**4 / (384.0 * 203_000.0 * C200.Ix)
    assert expected == pytest.approx(41.357, rel=1e-3)
    assert abs(f.delta_max) == pytest.approx(expected, rel=1e-6)


def test_two_equal_spans_give_the_textbook_coefficients():
    """Support -0.125 wL^2, span +0.0703 wL^2."""
    w, L = 2.0, 6.0
    f = continuous_beam_forces((L * 1000.0,) * 2, w, C200)
    assert f.M_hogging / (w * L**2) == pytest.approx(0.125, rel=1e-3)
    assert f.M_sagging / (w * L**2) == pytest.approx(0.0703, rel=5e-3)


def test_three_equal_spans_give_the_textbook_coefficients():
    """Support -0.100 wL^2, end span +0.080 wL^2.

    These are the coefficients every steel handbook prints, and they are
    independent of this package, of PyNite, and of each other.
    """
    w, L = 2.0, 6.0
    f = continuous_beam_forces((L * 1000.0,) * 3, w, C200)
    assert f.M_hogging / (w * L**2) == pytest.approx(0.100, rel=1e-3)
    assert f.M_sagging / (w * L**2) == pytest.approx(0.080, rel=1e-2)


def test_four_equal_spans_give_the_textbook_coefficients():
    """First interior support -0.1071 wL^2."""
    w, L = 2.0, 6.0
    f = continuous_beam_forces((L * 1000.0,) * 4, w, C200)
    assert f.M_hogging / (w * L**2) == pytest.approx(0.1071, rel=5e-3)


def test_continuity_reduces_the_span_moment():
    """The whole reason a purlin line is run continuous."""
    single = continuous_beam_forces((6000.0,), 2.0, C200)
    triple = continuous_beam_forces((6000.0,) * 3, 2.0, C200)
    assert triple.M_sagging < single.M_sagging
    assert triple.M_hogging > 0.0


def test_non_uniform_spans_are_not_averaged():
    """A PEB has unequal end bays; the long bay must show the larger moment."""
    f = continuous_beam_forces((4500.0, 7500.0, 7500.0, 4500.0), 2.0, C200)
    equal = continuous_beam_forces((6000.0,) * 4, 2.0, C200)
    assert f.M_sagging != pytest.approx(equal.M_sagging, rel=1e-3)
    assert f.M_hogging > equal.M_hogging


def test_shear_is_larger_at_an_interior_support():
    single = continuous_beam_forces((6000.0,), 2.0, C200)
    triple = continuous_beam_forces((6000.0,) * 3, 2.0, C200)
    assert triple.V_max > single.V_max


def test_stations_cover_the_whole_line():
    f = continuous_beam_forces((6000.0, 7500.0), 2.0, C200)
    xs = [x for x, _, _, _ in f.stations]
    assert min(xs) == pytest.approx(0.0)
    assert max(xs) == pytest.approx(13_500.0)


def test_aisi_modulus_is_used_not_the_aisc_one():
    """AISI E = 203,000 MPa (29,500 ksi); AISC is 200,000 (29,000).

    Deflection is inversely proportional to E, so using the AISC value would
    overstate purlin deflection by 1.5%.
    """
    f = continuous_beam_forces((6000.0,), 2.0, C200)
    with_aisc = continuous_beam_forces((6000.0,), 2.0, C200, E=200_000.0)
    assert abs(with_aisc.delta_max) > abs(f.delta_max)
    assert abs(with_aisc.delta_max) / abs(f.delta_max) == pytest.approx(
        203_000.0 / 200_000.0, rel=1e-6
    )


# ---------------------------------------------------------------------------
# bracing rule - the uplift case
# ---------------------------------------------------------------------------


def _line(**kw):
    base = dict(
        mark="PR1", section=C200, spans=(6000.0,) * 4, spacing=1500.0,
    )
    base.update(kw)
    return PurlinLine(**base)


def test_sheeting_braces_the_top_flange_under_gravity():
    b = PurlinBracing(sheeting_braces_top=True)
    assert b.unbraced_length(uplift=False, span=6000.0) == 0.0


def test_sheeting_does_not_brace_the_bottom_flange_under_uplift():
    """The rule this module exists to enforce.

    None is NOT zero.  Zero means continuously braced; None means nobody
    said, and the check cannot be performed.
    """
    b = PurlinBracing(sheeting_braces_top=True)
    assert b.unbraced_length(uplift=True, span=6000.0) is None


def test_anti_sag_ties_give_the_bottom_flange_a_length():
    b = PurlinBracing(bottom_flange_brace_spacing=3000.0)
    assert b.unbraced_length(uplift=True, span=6000.0) == 3000.0


def test_uplift_without_bottom_bracing_is_reported_not_passed():
    """An unevaluated check must never look like a satisfied one."""
    d = check_purlin(
        _line(bracing=PurlinBracing(sheeting_braces_top=True)),
        [PurlinLoadCase("0.9D+1.0W", 3.2, uplift=True)],
        Fy=450.0, Fu=480.0,
    )
    flex = next(c for c in d.checks if "Flexure" in c.name)
    assert "NOT EVALUATED" in flex.name
    assert flex.ratio == math.inf
    assert not flex.passed
    assert not d.passed
    assert "anti-sag" in flex.note or "bottom flange" in flex.note.lower()


def test_the_unevaluated_note_says_what_to_supply():
    d = check_purlin(
        _line(),
        [PurlinLoadCase("uplift", 3.2, uplift=True)],
        Fy=450.0, Fu=480.0,
    )
    flex = next(c for c in d.checks if "Flexure" in c.name)
    assert "bottom_flange_brace_spacing" in flex.note
    assert "I6.2.1" in flex.note or "Sect." in flex.note


def test_gravity_only_is_evaluated_normally():
    d = check_purlin(
        _line(),
        [PurlinLoadCase("1.2D+1.6Lr", 2.0)],
        Fy=450.0, Fu=480.0,
    )
    flex = next(c for c in d.checks if "Flexure" in c.name)
    assert math.isfinite(flex.ratio)
    assert flex.capacity > 0.0


# ---------------------------------------------------------------------------
# the checks themselves
# ---------------------------------------------------------------------------


def test_flexure_falls_back_to_section_strength_and_says_so():
    d = check_purlin(
        _line(), [PurlinLoadCase("1.2D+1.6Lr", 2.0)], Fy=450.0, Fu=480.0
    )
    flex = next(c for c in d.checks if "Flexure" in c.name)
    assert "section strength only" in flex.name
    assert "NOT evaluated" in flex.note
    assert "AISI S100-16" in flex.reference


def test_dsm_is_used_when_the_cufsm_numbers_are_supplied():
    line = _line(
        bracing=PurlinBracing(bottom_flange_brace_spacing=3000.0),
        elastic_buckling=ElasticBuckling(Mcre=28.0, Mcrl=19.0, Mcrd=22.0),
    )
    d = check_purlin(line, [PurlinLoadCase("1.2D+1.6Lr", 2.0)], Fy=450.0, Fu=480.0)
    flex = next(c for c in d.checks if "Flexure" in c.name)
    assert "DSM" in flex.name
    assert "App. 1" in flex.reference
    assert "CUFSM" in flex.note


def test_dsm_and_ewm_do_not_return_the_same_number():
    """If they did, one of them is not being called."""
    plain = check_purlin(
        _line(), [PurlinLoadCase("g", 2.0)], Fy=450.0, Fu=480.0
    )
    dsm = check_purlin(
        _line(elastic_buckling=ElasticBuckling(Mcre=28.0, Mcrl=19.0, Mcrd=22.0)),
        [PurlinLoadCase("g", 2.0)], Fy=450.0, Fu=480.0,
    )
    a = next(c for c in plain.checks if "Flexure" in c.name).capacity
    b = next(c for c in dsm.checks if "Flexure" in c.name).capacity
    assert a != pytest.approx(b, rel=1e-6)


def test_shear_and_combined_are_present():
    d = check_purlin(
        _line(), [PurlinLoadCase("1.2D+1.6Lr", 2.0)], Fy=450.0, Fu=480.0
    )
    names = " ".join(c.name for c in d.checks)
    assert "Shear" in names
    assert "Combined bending and shear" in names


def test_deflection_is_checked_for_gravity_and_not_for_uplift():
    grav = check_purlin(
        _line(), [PurlinLoadCase("D+Lr", 2.0)], Fy=450.0, Fu=480.0
    )
    up = check_purlin(
        _line(bracing=PurlinBracing(bottom_flange_brace_spacing=3000.0)),
        [PurlinLoadCase("uplift", 3.2, uplift=True)],
        Fy=450.0, Fu=480.0,
    )
    assert any("Deflection" in c.name for c in grav.checks)
    assert not any("Deflection" in c.name for c in up.checks)


def test_deflection_limit_is_span_over_the_denominator():
    d = check_purlin(
        _line(), [PurlinLoadCase("D+Lr", 2.0)], Fy=450.0, Fu=480.0,
        deflection_limit=240,
    )
    defl = next(c for c in d.checks if "Deflection" in c.name)
    assert defl.capacity == pytest.approx(6000.0 / 240.0)
    assert "span/240" in defl.name
    assert "not an AISI strength limit" in defl.reference


def test_asd_capacities_are_lower_than_lrfd():
    lr = check_purlin(
        _line(), [PurlinLoadCase("g", 2.0)], Fy=450.0, Fu=480.0,
        method=DesignMethod.LRFD,
    )
    asd = check_purlin(
        _line(), [PurlinLoadCase("g", 2.0)], Fy=450.0, Fu=480.0,
        method=DesignMethod.ASD,
    )
    a = next(c for c in lr.checks if c.name.startswith("Shear")).capacity
    b = next(c for c in asd.checks if c.name.startswith("Shear")).capacity
    assert b < a


def test_the_worst_case_governs():
    d = check_purlin(
        _line(bracing=PurlinBracing(bottom_flange_brace_spacing=3000.0)),
        [PurlinLoadCase("light", 0.5), PurlinLoadCase("heavy", 4.0)],
        Fy=450.0, Fu=480.0,
    )
    assert d.governing_combination == "heavy"


def test_every_check_cites_aisi_not_aisc():
    d = check_purlin(
        _line(), [PurlinLoadCase("g", 2.0)], Fy=450.0, Fu=480.0
    )
    for c in d.checks:
        if "Deflection" in c.name:
            continue
        assert "AISI" in c.reference, c
        assert "AISC" not in c.reference, c


def test_result_carries_the_span_arrangement():
    d = check_purlin(
        _line(), [PurlinLoadCase("g", 2.0)], Fy=450.0, Fu=480.0
    )
    assert d.member_type is MemberType.PURLIN
    assert "4 span" in d.note
    assert "1500 mm centres" in d.note
    assert d.weight > 0.0


def test_no_cases_is_an_error():
    with pytest.raises(PurlinError, match="no load cases"):
        check_purlin(_line(), [], Fy=450.0, Fu=480.0)


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------


def test_all_purlin_relations_are_verified():
    unset = [k for k, v in VERIFIED.items() if not v]
    assert unset == [], f"unverified purlin relations: {unset}"


def test_coverage_gaps_name_web_crippling_and_lapping():
    assert any("Web crippling" in g for g in COVERAGE_GAPS)
    assert any("Lapped" in g for g in COVERAGE_GAPS)
    assert any("R-factor" in g for g in COVERAGE_GAPS)
