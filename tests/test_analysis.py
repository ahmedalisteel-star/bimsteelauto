"""Layout and behaviour tests for bimsteelauto.analysis.

Relative checks and equilibrium.  Equilibrium is stronger than most relative
tests - a solver that violates it is definitely wrong - but it still cannot
confirm that the FORCES are right, only that they balance.  That is what
test_analysis_verification.py is for.
"""

import math

import pytest

from bimsteelauto.analysis import (
    VERIFIED,
    AnalysisError,
    BaseFixity,
    FrameAnalysis,
    FrameSections,
    LoadDirection,
    MemberSectionSpec,
    ModelSettings,
    build_layout,
)
from bimsteelauto.sections import WeldedI
from bimsteelauto.spec import FrameType, MemberType, PEBGeometry, RoofType

pytest.importorskip("Pynite", reason="PyNiteFEA not installed")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _geom(**kw):
    base = dict(
        span_widths=[30.0],
        bay_spacings=[6.0] * 5,
        eave_height=7.0,
        roof_slope=0.10,
        purlin_spacing=1.5,
        girt_spacing=2.0,
    )
    base.update(kw)
    return PEBGeometry.from_m(**base)


def _sections(*, tapered=True, interior=False):
    shallow = WeldedI.symmetric(d=400, tw=6, bf=200, tf=10)
    deep = WeldedI.symmetric(d=800, tw=6, bf=200, tf=10)
    if tapered:
        col = MemberSectionSpec(shallow, deep)
        raf = MemberSectionSpec(deep, shallow)
    else:
        col = MemberSectionSpec.prismatic(deep)
        raf = MemberSectionSpec.prismatic(deep)
    ic = MemberSectionSpec.prismatic(deep) if interior else None
    return FrameSections(column=col, rafter=raf, interior_column=ic)


@pytest.fixture
def layout():
    return build_layout(_geom(), _sections())


@pytest.fixture
def prismatic_layout():
    return build_layout(_geom(), _sections(tapered=False))


def _gravity(layout, w=1.8, direction=LoadDirection.GLOBAL_Y_PROJECTED):
    fa = FrameAnalysis(layout)
    for m in layout.members:
        if m.member_type is MemberType.RAFTER:
            fa.add_line_load("D", m.mark, w, direction)
    fa.add_combination("1.0D", {"D": 1.0})
    return fa


# ---------------------------------------------------------------------------
# layout geometry
# ---------------------------------------------------------------------------


def test_clear_span_layout(layout):
    assert layout.marks == ("C1", "C2", "R1", "R2")
    assert layout.base_nodes == ("B1", "B2")
    assert layout.eave_nodes == ("E1", "E2")
    assert layout.ridge_nodes == ("RG1",)


def test_node_coordinates(layout):
    assert layout.nodes["B1"] == (0.0, 0.0)
    assert layout.nodes["E1"] == (0.0, 7_000.0)
    assert layout.nodes["B2"] == (30_000.0, 0.0)
    assert layout.nodes["RG1"] == (15_000.0, 8_500.0)


def test_ridge_matches_geometry_ridge_height(layout):
    assert layout.nodes["RG1"][1] == pytest.approx(layout.geometry.ridge_height)


def test_rafter_length_is_sloped(layout):
    r1 = layout.member("R1")
    assert r1.length == pytest.approx(math.hypot(15_000.0, 1_500.0))
    assert r1.horizontal_projection == pytest.approx(15_000.0)


def test_column_length_is_eave_height(layout):
    assert layout.member("C1").length == pytest.approx(7_000.0)
    assert layout.member("C1").angle == pytest.approx(math.pi / 2)


def test_rafter_taper_runs_deep_end_first(layout):
    r1 = layout.member("R1")
    assert r1.taper.start.d > r1.taper.end.d  # haunch at the eave


def test_multi_span_layout():
    g = _geom(span_widths=[24.5, 24.5], frame_type=FrameType.MULTI_SPAN)
    lay = build_layout(g, _sections(interior=True))
    assert lay.marks == ("C1", "IC1", "C2", "R1", "R2", "R3", "R4")
    assert lay.base_nodes == ("B1", "B2", "B3")
    assert lay.ridge_nodes == ("RG1", "RG2")
    assert lay.nodes["B2"][0] == pytest.approx(24_500.0)


def test_multi_span_without_interior_section_is_rejected():
    g = _geom(span_widths=[24.5, 24.5], frame_type=FrameType.MULTI_SPAN)
    with pytest.raises(AnalysisError, match="interior_column"):
        build_layout(g, _sections(interior=False))


def test_monoslope_layout():
    g = _geom(span_widths=[8.0], roof_slope=0.10, roof_type=RoofType.MONOSLOPE)
    lay = build_layout(g, _sections())
    assert lay.marks == ("C1", "C2", "R1")
    assert lay.nodes["E2"][1] == pytest.approx(7_000.0 + 8_000.0 * 0.10)


def test_layout_weight_is_sum_of_members(layout):
    assert layout.steel_weight == pytest.approx(
        sum(m.taper.weight for m in layout.members)
    )


def test_bad_settings_rejected():
    with pytest.raises(AnalysisError):
        build_layout(_geom(), _sections(), ModelSettings(stations_per_member=2))


# ---------------------------------------------------------------------------
# equilibrium - the strongest cheap check there is
# ---------------------------------------------------------------------------


def test_projected_load_total_is_exact(layout):
    """w kN/m of horizontal projection over B metres must give w*B kN."""
    w = 1.8
    env = _gravity(layout, w).solve(p_delta=False)
    total = sum(env.base_reaction(b, "1.0D")[1] for b in layout.base_nodes)
    assert total == pytest.approx(w * 30.0, rel=1e-9)


def test_along_member_load_total_is_exact(layout):
    """The same number per metre ALONG the rafter gives a larger total."""
    w = 1.8
    env = _gravity(layout, w, LoadDirection.GLOBAL_Y).solve(p_delta=False)
    sloped = sum(
        m.length for m in layout.members if m.member_type is MemberType.RAFTER
    )
    total = sum(env.base_reaction(b, "1.0D")[1] for b in layout.base_nodes)
    assert total == pytest.approx(w * sloped / 1000.0, rel=1e-9)


def test_the_two_vertical_readings_differ_by_one_over_cos(layout):
    """The trap this enum exists to close.  At 1:10 it is 0.5%."""
    w = 1.8
    a = sum(
        _gravity(layout, w, LoadDirection.GLOBAL_Y_PROJECTED)
        .solve(p_delta=False)
        .base_reaction(b, "1.0D")[1]
        for b in layout.base_nodes
    )
    b_ = sum(
        _gravity(layout, w, LoadDirection.GLOBAL_Y)
        .solve(p_delta=False)
        .base_reaction(b, "1.0D")[1]
        for b in layout.base_nodes
    )
    r1 = layout.member("R1")
    cos_theta = r1.horizontal_projection / r1.length
    assert a / b_ == pytest.approx(cos_theta, rel=1e-9)


def test_symmetric_gravity_gives_symmetric_reactions(layout):
    env = _gravity(layout).solve(p_delta=False)
    left = env.base_reaction("B1", "1.0D")
    right = env.base_reaction("B2", "1.0D")
    assert left[1] == pytest.approx(right[1], rel=1e-9)
    assert left[0] == pytest.approx(-right[0], rel=1e-9)


def test_symmetric_gravity_has_no_net_thrust(layout):
    env = _gravity(layout).solve(p_delta=False)
    net = sum(env.base_reaction(b, "1.0D")[0] for b in layout.base_nodes)
    assert net == pytest.approx(0.0, abs=1e-6)


def test_horizontal_equilibrium_under_wind(layout):
    fa = FrameAnalysis(layout)
    fa.add_line_load("W", "C1", 2.0, LoadDirection.GLOBAL_X)
    fa.add_combination("1.0W", {"W": 1.0})
    env = fa.solve(p_delta=False)
    applied = 2.0 * 7.0  # kN/m along the 7 m column
    net = sum(env.base_reaction(b, "1.0W")[0] for b in layout.base_nodes)
    assert net == pytest.approx(-applied, rel=1e-6)


def test_pinned_bases_carry_no_moment(layout):
    env = _gravity(layout).solve(p_delta=False)
    for b in layout.base_nodes:
        assert env.base_reaction(b, "1.0D")[2] == pytest.approx(0.0, abs=1e-9)


def test_fixed_bases_do_carry_moment():
    lay = build_layout(
        _geom(), _sections(), ModelSettings(base_fixity=BaseFixity.FIXED)
    )
    env = _gravity(lay).solve(p_delta=False)
    assert abs(env.base_reaction("B1", "1.0D")[2]) > 1.0


def test_fixed_base_reduces_eave_drift():
    """A fixed base is stiffer; the frame must sway less."""
    drifts = {}
    for fixity in (BaseFixity.PINNED, BaseFixity.FIXED):
        lay = build_layout(_geom(), _sections(), ModelSettings(base_fixity=fixity))
        fa = FrameAnalysis(lay)
        fa.add_line_load("W", "C1", 2.0, LoadDirection.GLOBAL_X)
        fa.add_combination("1.0W", {"W": 1.0})
        drifts[fixity] = fa.solve(p_delta=False).eave_drift("1.0W")
    assert drifts[BaseFixity.FIXED] < drifts[BaseFixity.PINNED]


# ---------------------------------------------------------------------------
# results plumbing
# ---------------------------------------------------------------------------


def test_governing_returns_a_real_station(layout):
    env = _gravity(layout).solve(p_delta=False)
    combo, st = env.governing("R1")
    assert combo == "1.0D"
    assert 0.0 <= st.x <= layout.member("R1").length


def test_rafter_is_in_compression_under_gravity(layout):
    """Portal action pushes the rafter into compression, not tension."""
    env = _gravity(layout).solve(p_delta=False)
    _, st = env.governing("R1")
    assert st.P > 0.0


def test_axial_sign_is_compression_positive(layout):
    """Columns under gravity must read positive."""
    env = _gravity(layout).solve(p_delta=False)
    _, st = env.governing("C1")
    assert st.P > 0.0


def test_station_forces_convert_to_member_forces(layout):
    env = _gravity(layout).solve(p_delta=False)
    _, st = env.governing("R1")
    mf = st.to_member_forces()
    assert mf.P == st.P
    assert mf.Mz == st.M
    assert mf.station == st.x


def test_stations_are_ordered_and_span_the_member(layout):
    env = _gravity(layout).solve(p_delta=False)
    sts = env.stations["R1"]["1.0D"]
    xs = [s.x for s in sts]
    assert xs == sorted(xs)
    assert xs[0] == pytest.approx(0.0)
    assert xs[-1] == pytest.approx(layout.member("R1").length)


def test_uplift_is_reported_positive():
    lay = build_layout(_geom(), _sections())
    fa = FrameAnalysis(lay)
    for m in ("R1", "R2"):
        fa.add_line_load("W", m, -3.0, LoadDirection.GLOBAL_Y_PROJECTED)  # suction
    fa.add_combination("0.9D+1.0W", {"W": 1.0})
    env = fa.solve(p_delta=False)
    assert env.max_uplift > 0.0


def test_no_combination_is_an_error(layout):
    fa = FrameAnalysis(layout)
    fa.add_line_load("D", "R1", 1.0)
    with pytest.raises(AnalysisError, match="no load combinations"):
        fa.solve()


def test_unknown_member_is_rejected(layout):
    fa = FrameAnalysis(layout)
    with pytest.raises(AnalysisError, match="unknown member"):
        fa.add_line_load("D", "R99", 1.0)


def test_empty_combination_is_rejected(layout):
    fa = FrameAnalysis(layout)
    with pytest.raises(AnalysisError, match="no factors"):
        fa.add_combination("bad", {})


# ---------------------------------------------------------------------------
# taper handling
# ---------------------------------------------------------------------------


def test_taper_is_discretised_into_several_segments(layout):
    fa = _gravity(layout)
    fa.build()
    assert len(fa._segments["R1"]) > 1
    assert len(fa._segments["C1"]) > 1


def test_prismatic_member_is_one_segment(prismatic_layout):
    fa = _gravity(prismatic_layout)
    fa.build()
    assert len(fa._segments["R1"]) == 1


def test_finer_taper_step_makes_more_segments():
    coarse = build_layout(_geom(), _sections(), ModelSettings(max_taper_step=0.5))
    fine = build_layout(_geom(), _sections(), ModelSettings(max_taper_step=0.05))
    fa_c, fa_f = _gravity(coarse), _gravity(fine)
    fa_c.build()
    fa_f.build()
    assert len(fa_f._segments["R1"]) > len(fa_c._segments["R1"])


def test_taper_carries_more_moment_at_the_haunch(layout):
    """The deep end attracts moment - that is the point of haunching."""
    env = _gravity(layout).solve(p_delta=False)
    sts = env.stations["R1"]["1.0D"]
    assert abs(sts[0].M) > abs(sts[-1].M)


# ---------------------------------------------------------------------------
# P-Delta
# ---------------------------------------------------------------------------


def test_p_delta_increases_sway_moment(layout):
    fa1 = FrameAnalysis(layout)
    fa2 = FrameAnalysis(layout)
    for fa in (fa1, fa2):
        fa.add_line_load("W", "C1", 3.0, LoadDirection.GLOBAL_X)
        for m in ("R1", "R2"):
            fa.add_line_load("D", m, 4.0, LoadDirection.GLOBAL_Y_PROJECTED)
        fa.add_combination("1.2D+1.0W", {"D": 1.2, "W": 1.0})
    first = fa1.solve(p_delta=False)
    second = fa2.solve(p_delta=True)
    assert second.max_abs("C1", "M") > first.max_abs("C1", "M")


def test_p_delta_flag_is_recorded(layout):
    assert _gravity(layout).solve(p_delta=True).p_delta is True
    assert _gravity(layout).solve(p_delta=False).p_delta is False


def test_p_delta_is_the_default(layout):
    assert _gravity(layout).solve().p_delta is True


# ---------------------------------------------------------------------------
# verification gate
# ---------------------------------------------------------------------------


def test_all_analysis_relations_are_verified():
    unset = [k for k, v in VERIFIED.items() if not v]
    assert unset == [], f"unverified analysis relations: {unset}"
