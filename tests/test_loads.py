"""Tests for bimsteelauto.loads.

Behaviour and clause arithmetic.  The comparison against a second ASCE 7-16
implementation lives in test_loads_verification.py.
"""

import math

import pytest

from bimsteelauto.analysis import (
    FrameAnalysis,
    FrameSections,
    LoadDirection,
    MemberSectionSpec,
    build_layout,
)
from bimsteelauto.loads import (
    CLADDING_CATALOGUE,
    AreaLoad,
    DeadLoadBuildUp,
    LoadError,
    RoofCompanion,
    Surface,
    apply_loads,
    asd_combinations,
    build_load_summary,
    combinations_for,
    frame_tributary_area,
    gravity_loads_for_frame,
    line_load,
    lrfd_combinations,
    roof_live_load,
    tributary_width,
)
from bimsteelauto.sections import WeldedI
from bimsteelauto.spec import DesignMethod, MemberType, PEBGeometry

pytest.importorskip("Pynite", reason="PyNiteFEA not installed")


def _geom(**kw):
    base = dict(
        span_widths=[30.0],
        bay_spacings=[6.0, 7.5, 7.5, 6.0],
        eave_height=7.0,
        roof_slope=0.10,
        purlin_spacing=1.5,
        girt_spacing=2.0,
    )
    base.update(kw)
    return PEBGeometry.from_m(**base)


def _layout(g=None):
    g = g or _geom()
    s = WeldedI.symmetric(d=600, tw=8, bf=250, tf=16)
    return build_layout(
        g,
        FrameSections(
            column=MemberSectionSpec.prismatic(s),
            rafter=MemberSectionSpec.prismatic(s),
        ),
    )


# ---------------------------------------------------------------------------
# dead load build-up
# ---------------------------------------------------------------------------


def test_build_up_totals_its_components():
    d = DeadLoadBuildUp(sheeting=0.12, insulation=0.02, purlins=0.06, services=0.10)
    assert d.total == pytest.approx(0.30)


def test_every_component_is_listed_including_zeros():
    """The sheet must show a zero, not omit the line.

    An omitted line reads as "not considered"; a zero reads as "considered
    and there is none".  They are different statements.
    """
    d = DeadLoadBuildUp(sheeting=0.12, purlins=0.06)
    labels = [lab for lab, _, _ in d.components]
    assert labels == [
        "Sheeting", "Insulation", "Liner", "Purlins / girts",
        "Services", "Collateral",
    ]
    assert len(d.components) == 6


def test_catalogue_lookup_records_what_was_chosen():
    d = DeadLoadBuildUp.from_catalogue("sandwich panel 50mm PUF", purlins=0.06)
    assert d.sheeting == pytest.approx(0.12)
    assert "sandwich panel 50mm PUF" in d.sheeting_description
    assert "50 mm PUF core" in d.sheeting_description


def test_unknown_cladding_is_rejected_with_the_options():
    with pytest.raises(LoadError, match="not in the cladding catalogue"):
        DeadLoadBuildUp.from_catalogue("unobtainium sheet", purlins=0.06)


def test_catalogue_values_are_plausible():
    for name, (w, _) in CLADDING_CATALOGUE.items():
        assert 0.02 <= w <= 0.30, f"{name} at {w} kN/m2 is not a roof sheet"


def test_negative_component_is_rejected():
    with pytest.raises(LoadError, match="negative"):
        DeadLoadBuildUp(sheeting=0.12, services=-0.05)


def test_zero_sheeting_is_rejected():
    with pytest.raises(LoadError, match="sheeting"):
        DeadLoadBuildUp(sheeting=0.0, purlins=0.06)


def test_dead_load_is_on_the_sloping_surface():
    d = DeadLoadBuildUp(sheeting=0.12, purlins=0.06)
    assert d.as_area_load().surface is Surface.SLOPING


def test_frame_self_weight_is_not_in_the_build_up():
    """It comes from the section properties in the analysis.

    Adding it here as well would count the main frame twice.
    """
    from dataclasses import fields

    names = {f.name for f in fields(DeadLoadBuildUp)}
    assert not any("frame" in n or "self" in n for n in names)


# ---------------------------------------------------------------------------
# surface conversion
# ---------------------------------------------------------------------------


def test_sloping_load_grows_when_projected():
    a = AreaLoad(0.30, Surface.SLOPING)
    assert a.on_projected(0.10) == pytest.approx(0.30 / math.cos(math.atan(0.10)))
    assert a.on_projected(0.10) > 0.30


def test_projected_load_shrinks_on_the_slope():
    a = AreaLoad(0.58, Surface.PROJECTED)
    assert a.on_sloping(0.40) == pytest.approx(0.58 * math.cos(math.atan(0.40)))
    assert a.on_sloping(0.40) < 0.58


def test_conversion_is_a_no_op_on_its_own_surface():
    assert AreaLoad(1.0, Surface.SLOPING).on_sloping(0.4) == pytest.approx(1.0)
    assert AreaLoad(1.0, Surface.PROJECTED).on_projected(0.4) == pytest.approx(1.0)


def test_round_trip_returns_the_original():
    a = AreaLoad(0.30, Surface.SLOPING)
    back = AreaLoad(a.on_projected(0.25), Surface.PROJECTED).on_sloping(0.25)
    assert back == pytest.approx(0.30)


# ---------------------------------------------------------------------------
# roof live load, ASCE 7-16 Eq. 4.8-1
# ---------------------------------------------------------------------------


def test_small_roof_gets_no_area_reduction():
    r = roof_live_load(tributary_area=15.0, slope=0.0)
    assert r.R1 == pytest.approx(1.0)
    assert r.Lr == pytest.approx(0.96)


def test_large_roof_is_reduced_to_the_floor():
    r = roof_live_load(tributary_area=400.0, slope=0.0)
    assert r.R1 == pytest.approx(0.6)
    assert r.Lr == pytest.approx(0.58)


def test_r1_transitions_at_the_stated_areas():
    """18.58 m2 = 200 ft2 and 55.74 m2 = 600 ft2, Sect. 4.8.2."""
    assert roof_live_load(18.58, 0.0).R1 == pytest.approx(1.0)
    assert roof_live_load(55.74, 0.0).R1 == pytest.approx(0.6, abs=1e-3)
    mid = roof_live_load(30.0, 0.0)
    assert mid.R1 == pytest.approx(1.2 - 0.011 * 30.0)


def test_F_is_rise_per_twelve_not_the_slope():
    """The Specification's F is rise per 12 units of run.

    A 1:10 roof is F = 1.2, not F = 0.10.  Passing the slope straight in
    would leave every pitched roof unreduced.
    """
    r = roof_live_load(20.0, 0.10)
    assert r.F == pytest.approx(1.2)


def test_shallow_roof_gets_no_slope_reduction():
    assert roof_live_load(15.0, 4.0 / 12.0).R2 == pytest.approx(1.0)


def test_steep_roof_is_reduced_to_the_floor():
    assert roof_live_load(15.0, 1.0).R2 == pytest.approx(0.6)


def test_lr_is_bounded_both_ways():
    assert roof_live_load(1000.0, 1.0).Lr == pytest.approx(0.58)
    assert roof_live_load(1.0, 0.0).Lr == pytest.approx(0.96)


def test_roof_live_is_on_the_projected_surface():
    """Sect. 4.8 defines Lr on the horizontal projection, unlike dead load."""
    assert roof_live_load(50.0, 0.10).as_area_load().surface is Surface.PROJECTED


def test_roof_live_keeps_its_working():
    r = roof_live_load(50.0, 0.10)
    labels = [s[0] for s in r.steps]
    assert any("R1" in x for x in labels)
    assert any("R2" in x for x in labels)
    assert "ASCE 7-16" in r.reference


@pytest.mark.parametrize("bad", [dict(tributary_area=-1.0, slope=0.1),
                                 dict(tributary_area=10.0, slope=-0.1)])
def test_bad_roof_live_inputs_are_rejected(bad):
    with pytest.raises(LoadError):
        roof_live_load(**bad)


def test_tributary_area_uses_the_frames_own_bay():
    g = _geom()
    # frame 2 sits between a 6.0 and a 7.5 m bay -> 6.75 m tributary
    assert frame_tributary_area(g, 1) == pytest.approx(30.0 * 6.75)


def test_tributary_area_rejects_a_bad_index():
    with pytest.raises(LoadError):
        frame_tributary_area(_geom(), 99)


# ---------------------------------------------------------------------------
# combinations
# ---------------------------------------------------------------------------


def test_lrfd_has_the_dead_only_case():
    names = [c.name for c in lrfd_combinations()]
    assert "1.4D" in names


def test_uplift_combination_uses_0_9_dead_and_a_positive_wind_factor():
    """0.9D + 1.0W IS the uplift case - W carries its own sign.

    An earlier version of this module also emitted 0.9D - 1.0W, reasoning
    that wind blows both ways.  That negates a suction into a pressure that
    does not exist and inflated the governing effect by 28%.  Direction is
    handled by separate wind CASES, not by flipping the factor.
    """
    names = [c.name for c in lrfd_combinations()]
    assert "0.9D+1.0W" in names
    assert "0.9D-1.0W" not in names
    assert all(v > 0 for c in lrfd_combinations() for v in c.factors.values())


def test_multiple_wind_cases_each_get_their_own_combination():
    """The four ASCE Ch. 28 envelope cases, enveloped properly."""
    cases = ("W_A_pos", "W_A_neg", "W_B_pos", "W_B_neg")
    combos = lrfd_combinations(wind_cases=cases)
    for w in cases:
        assert f"0.9D+1.0{w}" in [c.name for c in combos]
        assert f"1.2D+1.0{w}+0.5Lr" in [c.name for c in combos]
    # one wind-free combination set, plus three per wind case
    assert len(combos) == 2 + 3 * len(cases)


def test_no_combination_mixes_two_wind_cases():
    """They are alternatives, not companions."""
    cases = ("W_A_pos", "W_A_neg")
    for c in lrfd_combinations(wind_cases=cases):
        assert len(set(c.factors) & set(cases)) <= 1, c.name


def test_asd_uplift_uses_0_6_dead_not_0_9():
    """Sect. 2.4.1 comb. 7 is 0.6D, not the LRFD 0.9D.

    Using 0.9 here would overstate the restoring weight by half and
    under-design every anchor.
    """
    c = next(c for c in asd_combinations() if c.name == "0.6D+0.6W")
    assert c.factors["D"] == pytest.approx(0.6)
    assert c.factors["W"] == pytest.approx(0.6)


def test_every_combination_cites_its_clause():
    for c in list(lrfd_combinations()) + list(asd_combinations()):
        assert "ASCE 7-16" in c.reference


def test_roof_companion_is_selectable():
    snow = lrfd_combinations(roof_companion=RoofCompanion.SNOW)
    assert any("S" in c.factors for c in snow)
    assert not any("Lr" in c.factors for c in snow)


def test_floor_live_is_off_by_default():
    assert not any("L" in c.factors for c in lrfd_combinations())
    assert any("L" in c.factors for c in lrfd_combinations(include_floor_live=True))


def test_reduced_L_factor_is_off_by_default():
    on = lrfd_combinations(include_floor_live=True, L_factor_reduced=True)
    off = lrfd_combinations(include_floor_live=True, L_factor_reduced=False)
    c_on = next(c for c in on if c.name.startswith("1.2D+1.0W+"))
    c_off = next(c for c in off if c.name.startswith("1.2D+1.0W+"))
    assert c_on.factors["L"] == pytest.approx(0.5)
    assert c_off.factors["L"] == pytest.approx(1.0)


def test_combinations_for_dispatches_on_method():
    assert combinations_for(DesignMethod.LRFD)[0].name == "1.4D"
    assert combinations_for(DesignMethod.ASD)[0].name == "1.0D"


# ---------------------------------------------------------------------------
# area -> line load
# ---------------------------------------------------------------------------


def test_tributary_width_is_in_metres():
    g = _geom()
    assert tributary_width(g, 0) == pytest.approx(3.0)
    assert tributary_width(g, 2) == pytest.approx(7.5)


def test_tributary_widths_are_not_averaged():
    """The v6 defect: non-uniform bays must stay non-uniform."""
    g = _geom()
    ws = [tributary_width(g, i) for i in range(g.n_frames)]
    assert ws == pytest.approx([3.0, 6.75, 7.5, 6.75, 3.0])


def test_line_load_is_pressure_times_width():
    g = _geom()
    a = AreaLoad(0.30, Surface.SLOPING)
    assert line_load(a, g, 2) == pytest.approx(0.30 * 7.5)


def test_line_load_does_not_convert_the_surface():
    """The cos(theta) belongs to the analysis, and must be applied once.

    If this function converted as well, a sloping dead load would pick up
    the factor twice.
    """
    g = _geom(roof_slope=0.40)
    a = AreaLoad(1.0, Surface.SLOPING)
    assert line_load(a, g, 2) == pytest.approx(7.5)


def test_sloping_maps_to_global_y_and_projected_to_projected():
    """The one place this pairing is decided.  Pinned so it cannot drift."""
    lay = _layout()
    dead = AreaLoad(0.30, Surface.SLOPING, "dead")
    live = AreaLoad(0.58, Surface.PROJECTED, "live")
    fls = gravity_loads_for_frame(lay, 2, dead=dead, roof_live=live)
    by_case = {f.case: f.direction for f in fls}
    assert by_case["D"] is LoadDirection.GLOBAL_Y
    assert by_case["Lr"] is LoadDirection.GLOBAL_Y_PROJECTED


def test_gravity_loads_land_on_the_rafters():
    lay = _layout()
    fls = gravity_loads_for_frame(lay, 2, dead=AreaLoad(0.3, Surface.SLOPING))
    rafters = {m.mark for m in lay.members if m.member_type is MemberType.RAFTER}
    assert {f.member_mark for f in fls} == rafters


def test_wall_dead_lands_on_the_columns():
    lay = _layout()
    fls = gravity_loads_for_frame(
        lay, 2,
        dead=AreaLoad(0.3, Surface.SLOPING),
        wall_dead=AreaLoad(0.15, Surface.SLOPING, "wall cladding"),
    )
    cols = {m.mark for m in lay.members if m.member_type is MemberType.COLUMN}
    on_cols = {f.member_mark for f in fls if "wall" in f.origin}
    assert on_cols == cols


def test_every_line_load_records_where_it_came_from():
    lay = _layout()
    fls = gravity_loads_for_frame(
        lay, 2, dead=AreaLoad(0.30, Surface.SLOPING, "Roof dead load")
    )
    for f in fls:
        assert "kN/m2" in f.origin
        assert "tributary" in f.origin


# ---------------------------------------------------------------------------
# wiring into the analysis
# ---------------------------------------------------------------------------


def test_a_combination_naming_an_absent_case_is_skipped_not_truncated():
    """The defect this test exists for.

    With only dead load applied, ``1.2D+1.0W+0.5Lr`` must not be solved as
    ``1.2D`` and printed under its full name.  It is skipped, by name, with
    the missing cases stated.
    """
    lay = _layout()
    fa = FrameAnalysis(lay)
    fls = gravity_loads_for_frame(lay, 2, dead=AreaLoad(0.30, Surface.SLOPING))
    applied = apply_loads(fa, fls, lrfd_combinations())

    assert "1.4D" in fa._combos
    assert "1.2D+1.0W+0.5Lr" not in fa._combos
    assert all("W" not in c.factors for c in applied.combinations)
    assert all("Lr" not in c.factors for c in applied.combinations)

    skipped = dict(applied.skipped)
    assert "1.2D+1.0W+0.5Lr" in skipped
    assert set(skipped["1.2D+1.0W+0.5Lr"]) == {"Lr", "W"}


def test_skipped_combinations_become_warnings_for_the_sheet():
    lay = _layout()
    fa = FrameAnalysis(lay)
    fls = gravity_loads_for_frame(lay, 2, dead=AreaLoad(0.30, Surface.SLOPING))
    applied = apply_loads(fa, fls, lrfd_combinations())
    assert applied.warnings
    assert any("was NOT analysed" in w for w in applied.warnings)
    assert any("0.9D+1.0W" in w for w in applied.warnings)


def test_a_full_load_set_skips_nothing():
    lay = _layout()
    fa = FrameAnalysis(lay)
    fls = list(gravity_loads_for_frame(
        lay, 2,
        dead=AreaLoad(0.30, Surface.SLOPING),
        roof_live=AreaLoad(0.58, Surface.PROJECTED),
    ))
    from bimsteelauto.loads import FrameLoad
    fls.append(FrameLoad("W", "C1", 2.0, LoadDirection.GLOBAL_X, "wind"))
    applied = apply_loads(fa, fls, lrfd_combinations())
    assert applied.skipped == ()
    assert len(applied.combinations) == len(lrfd_combinations())


def test_apply_loads_errors_when_nothing_matches():
    lay = _layout()
    fa = FrameAnalysis(lay)
    from bimsteelauto.spec import LoadCombination

    with pytest.raises(LoadError, match="no combination could be analysed"):
        apply_loads(fa, (), (LoadCombination("x", {"W": 1.0}),))


def test_end_to_end_total_equals_pressure_times_area():
    """The whole chain, checked by hand.

    0.30 kN/m2 of SLOPING roof on a 30 m span with a 7.5 m tributary width
    is 0.30 x 7.5 = 2.25 kN/m along each rafter, and the rafters together are
    sqrt(15^2 + 1.5^2) x 2 = 30.149 m long.
    """
    g = _geom()
    lay = _layout(g)
    fa = FrameAnalysis(lay)
    dead = AreaLoad(0.30, Surface.SLOPING, "dead")
    fls = gravity_loads_for_frame(lay, 2, dead=dead)
    apply_loads(fa, fls, lrfd_combinations())
    env = fa.solve(p_delta=False)

    total = sum(env.base_reaction(b, "1.4D")[1] for b in lay.base_nodes)
    sloped = sum(
        m.length for m in lay.members if m.member_type is MemberType.RAFTER
    ) / 1000.0
    assert total == pytest.approx(1.4 * 0.30 * 7.5 * sloped, rel=1e-9)


def test_roof_live_total_uses_the_plan_area_not_the_sloped_one():
    g = _geom()
    lay = _layout(g)
    fa = FrameAnalysis(lay)
    live = AreaLoad(0.58, Surface.PROJECTED, "Lr")
    fls = gravity_loads_for_frame(
        lay, 2, dead=AreaLoad(0.30, Surface.SLOPING), roof_live=live
    )
    apply_loads(fa, fls, lrfd_combinations())
    env = fa.solve(p_delta=False)

    d_only = sum(env.base_reaction(b, "1.4D")[1] for b in lay.base_nodes) / 1.4
    both = sum(env.base_reaction(b, "1.2D+1.6Lr")[1] for b in lay.base_nodes)
    lr_part = both - 1.2 * d_only
    assert lr_part == pytest.approx(1.6 * 0.58 * 7.5 * 30.0, rel=1e-9)


def test_load_summary_traces_every_line_load(capsys):
    lay = _layout()
    fls = gravity_loads_for_frame(lay, 2, dead=AreaLoad(0.30, Surface.SLOPING))
    fa = FrameAnalysis(lay)
    applied = apply_loads(fa, fls, lrfd_combinations())
    summary = build_load_summary(fls, applied)
    assert "R1/D" in summary.frame_line_loads
    assert summary.frame_line_loads["R1/D"] == pytest.approx(2.25)
    # The sheet can only print what was actually solved.
    assert all("W" not in c.factors for c in summary.combinations)
    assert all("Lr" not in c.factors for c in summary.combinations)
