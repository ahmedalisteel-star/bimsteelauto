"""Behaviour and guards for bimsteelauto.loads.wind.

The arithmetic is checked in ``test_wind_verification.py`` against two
outside references.  What is checked here is everything that surrounds the
arithmetic: the guards that refuse a bad input rather than returning a
plausible number, the zone-to-member mapping, and the one thing no
coefficient table can tell you - whether the load, once applied, pushes the
frame the way the wind actually pushes it.

That last one is the load-bearing test in this file.  A sign error in
``wind_loads_for_frame`` would leave every printed pressure correct and the
frame designed for wind blowing the wrong way, which is exactly the class of
defect the ASCE-sign disagreement recorded in the handover was.  So it is
pinned by solving the frame and checking the base reactions against
longhand statics, not by re-reading the mapping.
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto.analysis import (
    FrameAnalysis,
    FrameSections,
    LoadDirection,
    MemberSectionSpec,
    build_layout,
)
from bimsteelauto.loads import combinations_for
from bimsteelauto.loads.gravity import LoadError
from bimsteelauto.loads.wind import (
    COVERAGE_GAPS,
    ZoneRole,
    end_zone_width,
    envelope_pressures,
    frame_is_in_end_zone,
    gcpf_load_case_a,
    longitudinal_endwall_pressure,
    minimum_design_wind_load,
    velocity_pressure,
    velocity_pressure_coefficient,
    wind_case_names,
    wind_loads_for_frame,
    zone_map_for_frame,
)
from bimsteelauto.sections import WeldedI
from bimsteelauto.spec import (
    DesignMethod,
    EnclosureClassification,
    ExposureCategory,
    ExposureCategory as EC,
    PEBGeometry,
    ProjectSite,
    RiskCategory,
    RoofType,
)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture
def geometry() -> PEBGeometry:
    """30 m clear span, 27 m long on deliberately non-uniform bays, 1:10."""
    return PEBGeometry.from_m(
        span_widths=[30.0],
        bay_spacings=[6.0, 7.5, 7.5, 6.0],
        eave_height=7.0,
        roof_slope=0.10,
        purlin_spacing=1.5,
        girt_spacing=2.0,
    )


@pytest.fixture
def site() -> ProjectSite:
    return ProjectSite(
        basic_wind_speed=45.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
    )


@pytest.fixture
def wind(geometry, site):
    return envelope_pressures(geometry, site)


@pytest.fixture
def layout(geometry):
    col = WeldedI.symmetric(d=600.0, tw=8.0, bf=200.0, tf=12.0)
    raf = WeldedI.symmetric(d=550.0, tw=7.0, bf=180.0, tf=10.0)
    return build_layout(
        geometry,
        FrameSections(
            column=MemberSectionSpec.prismatic(col),
            rafter=MemberSectionSpec.prismatic(raf),
        ),
    )


# ---------------------------------------------------------------------
# Guards - a bad input must fail loudly, not return a plausible number
# ---------------------------------------------------------------------

def test_negative_roof_angle_is_refused() -> None:
    with pytest.raises(LoadError, match="must not be negative"):
        gcpf_load_case_a(-1.0)


def test_zero_and_negative_wind_speed_are_refused() -> None:
    for V in (0.0, -45.0):
        with pytest.raises(LoadError, match="wind speed must be positive"):
            velocity_pressure(V, 7.0, ExposureCategory.C)


def test_zero_and_negative_height_are_refused() -> None:
    for z in (0.0, -3.0):
        with pytest.raises(LoadError, match="height must be positive"):
            velocity_pressure_coefficient(z, ExposureCategory.C)


def test_frame_index_outside_the_building_is_refused(geometry, wind) -> None:
    """A frame line that does not exist must not silently answer False."""
    n = geometry.n_frames
    for bad in (-1, n, n + 3):
        with pytest.raises(LoadError, match="outside"):
            frame_is_in_end_zone(geometry, bad, wind.end_zone_a)


def test_a_case_name_that_is_not_one_of_the_four_is_refused(
    layout, wind
) -> None:
    """``_gcpi_for`` must not fall through to a default sign.

    Reached through the public path: a WindResult whose case names have
    been tampered with is exactly how a silently-wrong internal pressure
    would get in.
    """
    from bimsteelauto.loads.wind import _gcpi_for

    with pytest.raises(LoadError, match="not one of"):
        _gcpi_for("W_A", wind.GCpi)


# ---------------------------------------------------------------------
# The four cases, and the contract with combinations_for
# ---------------------------------------------------------------------

def test_wind_case_names_are_the_four_chapter_28_requires() -> None:
    assert wind_case_names() == (
        "W_A_pi_pos", "W_A_pi_neg", "W_B_pi_pos", "W_B_pi_neg"
    )


@pytest.mark.parametrize("method", [DesignMethod.LRFD, DesignMethod.ASD])
def test_every_case_name_reaches_the_load_combinations(method) -> None:
    """The join that has to hold, checked rather than assumed.

    ``combinations_for(wind_cases=...)`` takes these names verbatim.  If
    this module ever renames a case, the combinations would still be built
    - naming a case nothing applies a load to - and ``apply_loads`` would
    skip them.  The design would then be issued having never been checked
    against wind, with only a line in the warnings to say so.
    """
    combos = combinations_for(method, wind_cases=wind_case_names())
    named = {k for c in combos for k in c.factors}
    for case in wind_case_names():
        assert case in named, f"{case} appears in no combination"


def test_envelope_produces_all_four_cases(wind) -> None:
    assert set(wind.pressures) == set(wind_case_names())
    assert set(wind.gcpf) == set(wind_case_names())


def test_load_case_a_has_eight_zones_and_b_has_twelve(wind) -> None:
    """B carries the two endwall zones A does not have."""
    assert set(wind.pressures["W_A_pi_pos"]) == {
        "1", "2", "3", "4", "1E", "2E", "3E", "4E"
    }
    assert set(wind.pressures["W_B_pi_pos"]) == {
        "1", "2", "3", "4", "5", "6", "1E", "2E", "3E", "4E", "5E", "6E"
    }


def test_pressures_and_coefficients_agree_with_eq_28_3_1(wind) -> None:
    """``gcpf`` is carried alongside ``pressures`` for the sheet.

    It must be the coefficient that actually produced the pressure beside
    it, or the sheet shows a reviewer a derivation that did not happen.
    """
    for case, zones in wind.pressures.items():
        gcpi = wind.GCpi[0] if case.endswith("_pi_pos") else wind.GCpi[1]
        for zone, p in zones.items():
            expected = wind.qh * (wind.gcpf[case][zone] - gcpi)
            assert p == pytest.approx(expected, rel=1e-12), f"{case}/{zone}"


def test_the_steps_line_up_with_the_result(wind) -> None:
    """What the sheet prints must be what was used."""
    steps = dict((name, value) for name, value, _ in wind.steps)
    assert steps["qh (Eq. 26.10-1)"] == wind.qh
    assert steps["Kz at mean roof height"] == wind.Kz
    assert steps["end zone width a (Fig. 28.3-1 note 9)"] == wind.end_zone_a


def test_qh_is_evaluated_at_mean_roof_height_not_eave(site) -> None:
    """Sect. 26.3: above 10 degrees h is the eave/ridge average.

    On a steep roof that is metres higher than the eave, and qh rises with
    it.  Pinned because taking the eave height would be unconservative and
    would look entirely reasonable.
    """
    steep = PEBGeometry.from_m(
        span_widths=[30.0], bay_spacings=[6.0] * 4, eave_height=7.0,
        roof_slope=0.5, purlin_spacing=1.5, girt_spacing=2.0,
    )
    assert steep.slope_deg > 10.0
    assert steep.mean_roof_height > steep.eave_height
    w = envelope_pressures(steep, site)
    at_eave = velocity_pressure(
        site.basic_wind_speed, steep.eave_height / 1000.0,
        site.exposure_category, Kzt=site.Kzt, Kd=site.Kd,
    )
    assert w.qh > at_eave


# ---------------------------------------------------------------------
# End zones
# ---------------------------------------------------------------------

def test_the_end_frames_are_in_the_end_zone(geometry, wind) -> None:
    a = wind.end_zone_a
    assert frame_is_in_end_zone(geometry, 0, a)
    assert frame_is_in_end_zone(geometry, geometry.n_frames - 1, a)


def test_the_end_zone_is_measured_from_BOTH_ends(geometry) -> None:
    """Wind blows from either end, so both ends carry the E coefficients.

    With a small ``a`` only the two extreme frames qualify; the test states
    it as a symmetry so a one-sided implementation fails whichever end it
    forgot.
    """
    a = 1000.0                                    # 1 m, so 2a = 2 m
    flags = [
        frame_is_in_end_zone(geometry, i, a) for i in range(geometry.n_frames)
    ]
    assert flags == flags[::-1], flags
    assert flags[0] and flags[-1]
    assert not any(flags[1:-1])


def test_a_large_end_zone_swallows_the_whole_building(geometry) -> None:
    a = geometry.length
    assert all(
        frame_is_in_end_zone(geometry, i, a) for i in range(geometry.n_frames)
    )


def test_end_zone_coefficients_are_the_more_onerous_ones(wind) -> None:
    """The reason the E set exists: it is worse everywhere.

    On the windward wall the E pressure is larger; on every suction zone it
    is more negative.  If a mapping ever handed an interior frame the E set
    or the reverse, this is the property that makes it matter.
    """
    p = wind.pressures["W_A_pi_pos"]
    assert p["1E"] > p["1"]
    for zone in ("2", "3", "4"):
        assert p[zone + "E"] < p[zone], zone


# ---------------------------------------------------------------------
# Zone -> member mapping
# ---------------------------------------------------------------------

def test_zone_map_puts_the_four_zones_on_the_four_members(layout) -> None:
    """1 = windward wall, 2 = windward roof, 3 = leeward roof, 4 = leeward."""
    zones = zone_map_for_frame(layout)
    assert zones == {
        "C1": ZoneRole.WINDWARD_WALL,
        "R1": ZoneRole.WINDWARD_ROOF,
        "R2": ZoneRole.LEEWARD_ROOF,
        "C2": ZoneRole.LEEWARD_WALL,
    }


def test_wind_from_the_other_side_mirrors_the_map(layout) -> None:
    """Symmetric frame, mirrored zones - and the windward roof moves.

    Not cosmetic: zone 2 is far heavier than zone 3, so which rafter it
    lands on decides which rafter is designed for the larger uplift.
    """
    left = zone_map_for_frame(layout, from_left=True)
    right = zone_map_for_frame(layout, from_left=False)
    assert right == {
        "C2": ZoneRole.WINDWARD_WALL,
        "R2": ZoneRole.WINDWARD_ROOF,
        "R1": ZoneRole.LEEWARD_ROOF,
        "C1": ZoneRole.LEEWARD_WALL,
    }
    assert left != right


def test_a_multi_span_frame_is_refused_rather_than_guessed() -> None:
    """Fig. 28.3-1 numbers its zones around ONE ridge.

    Refusing is the point.  A two-ridge frame given the single-ridge zones
    would produce a complete, plausible, wrong answer, and nothing on the
    sheet would show it.
    """
    g = PEBGeometry.from_m(
        span_widths=[15.0, 15.0], bay_spacings=[6.0] * 4, eave_height=7.0,
        roof_slope=0.10, purlin_spacing=1.5, girt_spacing=2.0,
    )
    col = WeldedI.symmetric(d=600.0, tw=8.0, bf=200.0, tf=12.0)
    lay = build_layout(
        g,
        FrameSections(
            column=MemberSectionSpec.prismatic(col),
            rafter=MemberSectionSpec.prismatic(col),
            interior_column=MemberSectionSpec.prismatic(col),
        ),
    )
    with pytest.raises(LoadError, match="single-span gable"):
        zone_map_for_frame(lay)


def test_a_monoslope_frame_is_refused_rather_than_guessed() -> None:
    """No ridge means no place to put the zone 2/3 boundary."""
    g = PEBGeometry.from_m(
        span_widths=[20.0], bay_spacings=[6.0] * 4, eave_height=7.0,
        roof_slope=0.10, purlin_spacing=1.5, girt_spacing=2.0,
        roof_type=RoofType.MONOSLOPE,
    )
    col = WeldedI.symmetric(d=600.0, tw=8.0, bf=200.0, tf=12.0)
    lay = build_layout(
        g,
        FrameSections(
            column=MemberSectionSpec.prismatic(col),
            rafter=MemberSectionSpec.prismatic(col),
        ),
    )
    with pytest.raises(LoadError, match="gable roof"):
        zone_map_for_frame(lay)


# ---------------------------------------------------------------------
# Zone pressures -> frame line loads
# ---------------------------------------------------------------------

def test_sixteen_loads_four_members_by_four_cases(layout, wind) -> None:
    loads = wind_loads_for_frame(layout, 2, wind)
    assert len(loads) == 16
    assert {fl.case for fl in loads} == set(wind_case_names())
    assert {fl.member_mark for fl in loads} == {"C1", "C2", "R1", "R2"}


def test_every_wind_load_is_NORMAL_to_its_surface(layout, wind) -> None:
    """Wind presses on the surface it meets, not downward.

    Applying it as GLOBAL_Y would put roof suction straight up in plan
    rather than perpendicular to the slope, and would lose the horizontal
    component entirely on a steep roof.
    """
    for fl in wind_loads_for_frame(layout, 2, wind):
        assert fl.direction is LoadDirection.NORMAL


def test_an_interior_frame_takes_interior_zones_and_an_end_frame_takes_E(
    layout, wind
) -> None:
    interior = wind_loads_for_frame(layout, 2, wind)
    end = wind_loads_for_frame(layout, 0, wind)
    assert all("interior zone" in fl.origin for fl in interior)
    assert all("end zone" in fl.origin for fl in end)


def test_line_load_is_the_pressure_times_the_tributary_width(
    layout, wind, geometry
) -> None:
    """The one conversion this function makes, checked on its own.

    No cos(theta) belongs anywhere in it: the tributary width is measured
    along the building, perpendicular to the frame, and is the same width
    for a wall as for a roof.

    Run with the Sect. 28.3.4 minimum OFF, so this tests the one conversion
    it is about. The scaling has its own tests below.
    """
    frame_index = 2
    trib = geometry.tributary_widths()[frame_index] / 1000.0
    loads = {
        (fl.case, fl.member_mark): fl
        for fl in wind_loads_for_frame(
            layout, frame_index, wind, enforce_minimum=False
        )
    }
    p = wind.pressures["W_A_pi_pos"]
    assert loads[("W_A_pi_pos", "R1")].w == pytest.approx(p["2"] * trib)
    assert loads[("W_A_pi_pos", "C1")].w == pytest.approx(p["1"] * trib)
    # The leeward column is the one whose exterior faces +X, so its NORMAL
    # sign is flipped - see _normal_sign.
    assert loads[("W_A_pi_pos", "C2")].w == pytest.approx(-p["4"] * trib)


def test_non_uniform_bays_stay_non_uniform(layout, wind, geometry) -> None:
    """The v6 defect, guarded again on the wind path.

    Bays of 6.0 / 7.5 / 7.5 / 6.0 m give tributary widths of 3.0 / 6.75 /
    7.5 / 6.75 / 3.0 m.  Averaging them would understate the middle frame
    by 11%.
    """
    expected = [3.0, 6.75, 7.5, 6.75, 3.0]
    ratios = []
    for i, trib in enumerate(expected):
        loads = {
            fl.member_mark: fl
            for fl in wind_loads_for_frame(layout, i, wind)
            if fl.case == "W_A_pi_pos"
        }
        ratios.append(loads["R1"].w / trib)
    interior = ratios[1:4]
    assert all(r == pytest.approx(interior[0]) for r in interior)


def test_every_load_carries_its_zone_and_coefficients_in_the_origin(
    layout, wind
) -> None:
    """A number on the sheet a reviewer cannot trace is a number they
    have to take on trust."""
    for fl in wind_loads_for_frame(layout, 2, wind):
        assert "Fig. 28.3-1 zone" in fl.origin
        assert "GCpf" in fl.origin and "GCpi" in fl.origin
        assert "tributary" in fl.origin


def test_wind_loads_can_be_applied_to_the_analysis(layout, wind) -> None:
    """The whole point: these go in beside the gravity loads unchanged."""
    fa = FrameAnalysis(layout)
    for fl in wind_loads_for_frame(layout, 2, wind):
        fa.add_line_load(fl.case, fl.member_mark, fl.w, fl.direction)
    assert set(fa.cases) == set(wind_case_names())


# ---------------------------------------------------------------------
# THE SIGN, pinned by statics
# ---------------------------------------------------------------------

def _solve_one_case(layout, wind, case, frame_index=2):
    fa = FrameAnalysis(layout)
    loads = [
        fl for fl in wind_loads_for_frame(layout, frame_index, wind)
        if fl.case == case
    ]
    for fl in loads:
        fa.add_line_load(fl.case, fl.member_mark, fl.w, fl.direction)
    fa.add_combination(case, {case: 1.0})
    env = fa.solve(p_delta=False)
    by_mark = {fl.member_mark: fl.w for fl in loads}
    return env, by_mark


def test_wind_from_the_left_pushes_the_frame_to_the_right(layout, wind) -> None:
    """Both walls drive the frame the same way, and it is +X.

    The windward wall is pushed inward and the leeward wall is sucked
    outward; both are +X when the wind blows left to right.  A reaction is
    the negative of what is applied, so the base shears sum NEGATIVE.

    This is the test that would have caught the reversed-wind defect the
    handover describes, and it cannot be satisfied by a coefficient table
    alone.
    """
    env, _ = _solve_one_case(layout, wind, "W_A_pi_pos")
    fx = sum(
        env.base_reaction(b, "W_A_pi_pos")[0] for b in layout.base_nodes
    )
    assert fx < 0.0, f"sum of base FX reactions came out {fx:+.3f} kN"


def test_wind_from_the_right_pushes_the_frame_to_the_left(layout, wind) -> None:
    """The mirror, which is the only thing that proves the first was a sign
    and not an accident of magnitude."""
    fa = FrameAnalysis(layout)
    for fl in wind_loads_for_frame(layout, 2, wind, from_left=False):
        if fl.case == "W_A_pi_pos":
            fa.add_line_load(fl.case, fl.member_mark, fl.w, fl.direction)
    fa.add_combination("W", {"W_A_pi_pos": 1.0})
    env = fa.solve(p_delta=False)
    fx = sum(env.base_reaction(b, "W")[0] for b in layout.base_nodes)
    assert fx > 0.0, f"sum of base FX reactions came out {fx:+.3f} kN"


def test_the_positive_GCpi_case_lifts_the_frame_off_its_bases(
    layout, wind
) -> None:
    """Net uplift, which is what the holding-down bolts are for.

    On a 1:10 roof both zone 2 and zone 3 are in suction, so with the
    interior pressurised the frame is being pulled up.  An applied upward
    force makes the vertical base reactions sum NEGATIVE.
    """
    env, _ = _solve_one_case(layout, wind, "W_A_pi_pos")
    fy = sum(
        env.base_reaction(b, "W_A_pi_pos")[1] for b in layout.base_nodes
    )
    assert fy < 0.0, f"sum of base FY reactions came out {fy:+.3f} kN"


def test_vertical_equilibrium_matches_longhand_statics(
    layout, wind, geometry
) -> None:
    """The uplift, to nine figures, against arithmetic done by hand.

    A NORMAL load ``w`` on a rafter has vertical component ``w cos(theta)``
    per metre along the member, and the member's length times cos(theta) is
    its horizontal projection - half the span.  So the total vertical force
    is ``(w_R1 + w_R2) x B/2``, with no trigonometry surviving into the
    answer, and the reaction is its negative.

    That cancellation is the whole reason to test it: it is where a
    cos(theta) applied twice, or not at all, would show up.
    """
    env, w = _solve_one_case(layout, wind, "W_A_pi_pos")
    fy = sum(
        env.base_reaction(b, "W_A_pi_pos")[1] for b in layout.base_nodes
    )
    half_span = geometry.width / 2.0 / 1000.0        # m
    expected = (w["R1"] + w["R2"]) * half_span
    assert fy == pytest.approx(expected, rel=1e-9)


def test_horizontal_equilibrium_matches_longhand_statics(
    layout, wind, geometry
) -> None:
    """The base shear, likewise, and it needs all four members.

    Each column contributes ``w x H`` in +X.  Each rafter contributes its
    NORMAL load's horizontal component, ``w x L sin(theta)`` = ``w x rise``,
    and the two rafters lean opposite ways so the second one subtracts.
    """
    env, w = _solve_one_case(layout, wind, "W_A_pi_pos")
    fx = sum(
        env.base_reaction(b, "W_A_pi_pos")[0] for b in layout.base_nodes
    )
    H = geometry.eave_height / 1000.0
    rise = geometry.ridge_rise / 1000.0
    applied = (w["C1"] + w["C2"]) * H + (w["R1"] - w["R2"]) * rise
    assert fx == pytest.approx(-applied, rel=1e-9)


def test_both_columns_are_driven_the_same_way(layout, wind) -> None:
    """Windward pressure and leeward suction are not opposites.

    They add.  Getting the leeward wall's sign wrong would roughly halve
    the base shear while leaving every printed pressure correct - the
    single most plausible way for this module to be quietly wrong.
    """
    loads = {
        fl.member_mark: fl.w
        for fl in wind_loads_for_frame(layout, 2, wind)
        if fl.case == "W_A_pi_pos"
    }
    assert loads["C1"] > 0.0
    assert loads["C2"] > 0.0


def test_the_leeward_wall_pressure_is_suction_despite_the_positive_load(
    layout, wind
) -> None:
    """The sign flip is in the load, not in the pressure.

    ``pressures`` stays in ASCE's convention - negative is suction - and
    only the line load is flipped, because NORMAL means something different
    on the two columns.  Conflating the two is how the flip would leak into
    the sheet and misreport the leeward wall as being in pressure.
    """
    assert wind.pressures["W_A_pi_pos"]["4"] < 0.0
    loads = {
        fl.member_mark: fl.w
        for fl in wind_loads_for_frame(layout, 2, wind)
        if fl.case == "W_A_pi_pos"
    }
    assert loads["C2"] > 0.0


def test_the_negative_GCpi_case_is_the_milder_one_on_the_roof(
    layout, wind
) -> None:
    """Both cases must still be run, and the sheet must show which governs."""
    env_pos, _ = _solve_one_case(layout, wind, "W_A_pi_pos")
    env_neg, _ = _solve_one_case(layout, wind, "W_A_pi_neg")
    up_pos = sum(
        env_pos.base_reaction(b, "W_A_pi_pos")[1] for b in layout.base_nodes
    )
    up_neg = sum(
        env_neg.base_reaction(b, "W_A_pi_neg")[1] for b in layout.base_nodes
    )
    assert up_pos < up_neg


# ---------------------------------------------------------------------
# Sect. 28.3.4, the minimum design wind loading
# ---------------------------------------------------------------------

def geometry_of(layout):
    return layout.geometry


def test_the_minimum_governs_this_building_and_the_end_zone_escapes_it(
    geometry, wind
) -> None:
    """Both answers in one place, because the contrast is the point.

    At V = 45 m/s the interior-zone coefficients fall short of the 16/8 psf
    floor and are scaled up by 1.363.  The END-ZONE coefficients are heavier
    than the floor on their own and are not touched.

    A check that scaled everything by one factor would be wrong in the
    direction nobody notices - it would inflate the end frames, which are
    already the worst-loaded ones, and hide that the interior frames were
    the ones the clause was protecting.
    """
    interior = minimum_design_wind_load(geometry, wind, end_zone=False)
    end = minimum_design_wind_load(geometry, wind, end_zone=True)

    assert interior.governs
    assert interior.scale == pytest.approx(1.3626, abs=0.001)
    assert not end.governs
    assert end.scale == 1.0


def test_the_minimum_is_applied_to_load_case_A_and_not_to_B(
    layout, wind
) -> None:
    """Load Case B has no net transverse force for the minimum to bite on.

    Its two sidewall zones are both -0.45: equal, opposite in effect, and
    summing to zero across the frame.  Scaling it by a transverse minimum
    would be inventing a force the clause never asked for.
    """
    scaled = {
        (fl.case, fl.member_mark): fl.w
        for fl in wind_loads_for_frame(layout, 2, wind, enforce_minimum=True)
    }
    plain = {
        (fl.case, fl.member_mark): fl.w
        for fl in wind_loads_for_frame(layout, 2, wind, enforce_minimum=False)
    }
    m = minimum_design_wind_load(geometry_of(layout), wind)
    for mark in ("C1", "C2", "R1", "R2"):
        assert scaled[("W_A_pi_pos", mark)] == pytest.approx(
            plain[("W_A_pi_pos", mark)] * m.scale
        )
        assert scaled[("W_B_pi_pos", mark)] == pytest.approx(
            plain[("W_B_pi_pos", mark)]
        )


def test_a_scaled_load_says_so_in_its_origin(layout, wind) -> None:
    """A number that was raised must show that it was raised.

    Silently scaling the wind is exactly the kind of change that makes a
    sheet unreviewable: the printed GCpf and p would no longer multiply out
    to the printed line load, and a checker would think the arithmetic was
    wrong rather than that a clause had been applied.
    """
    loads = wind_loads_for_frame(layout, 2, wind)
    case_a = [fl for fl in loads if fl.case.startswith("W_A")]
    case_b = [fl for fl in loads if fl.case.startswith("W_B")]
    assert all("Sect. 28.3.4 minimum governs" in fl.origin for fl in case_a)
    assert all("28.3.4" not in fl.origin for fl in case_b)


def test_the_scaled_frame_actually_delivers_the_minimum_force(
    layout, wind, geometry
) -> None:
    """The check that proves the scaling did what the clause asks.

    Solve the frame with the minimum enforced, take the base shear, and
    compare it against the clause minimum apportioned to this frame's
    tributary width.  They must match - not approximately, to 1e-9 - or the
    scale factor was computed on a different force than the one the
    analysis actually produces.
    """
    env, _ = _solve_one_case(layout, wind, "W_A_pi_pos")
    fx = sum(
        env.base_reaction(b, "W_A_pi_pos")[0] for b in layout.base_nodes
    )
    m = minimum_design_wind_load(geometry, wind)
    assert m.governs
    trib = geometry.tributary_widths()[2] / 1000.0
    L = geometry.length / 1000.0
    expected = m.F_minimum * trib / L
    assert -fx == pytest.approx(expected, rel=1e-9)


def test_turning_the_minimum_off_leaves_the_raw_envelope_pressures(
    layout, wind, geometry
) -> None:
    """The comparison run must be available, and must be honest."""
    plain = {
        fl.member_mark: fl.w
        for fl in wind_loads_for_frame(layout, 2, wind, enforce_minimum=False)
        if fl.case == "W_A_pi_pos"
    }
    trib = geometry.tributary_widths()[2] / 1000.0
    assert plain["R1"] == pytest.approx(
        wind.pressures["W_A_pi_pos"]["2"] * trib
    )


def test_an_open_building_has_no_minimum(geometry) -> None:
    """Sect. 28.3.4 applies to enclosed and partially enclosed buildings.

    An open building is outside the clause, and the check must return
    applicable=False rather than quietly scaling it anyway.
    """
    site = ProjectSite(
        basic_wind_speed=45.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.OPEN,
        risk_category=RiskCategory.II,
    )
    w = envelope_pressures(geometry, site)
    m = minimum_design_wind_load(geometry, w)
    assert not m.applicable
    assert not m.governs
    assert m.scale == 1.0
    assert "does not apply" in m.note


def test_a_hand_built_WindResult_cannot_silently_skip_the_clause(
    geometry, wind
) -> None:
    """No enclosure means the clause cannot be decided, and it says so.

    Not applying a minimum because the information is missing is a
    different thing from not applying it because it does not apply, and a
    reader has to be able to tell them apart.
    """
    import dataclasses

    bare = dataclasses.replace(wind, enclosure=None)
    m = minimum_design_wind_load(geometry, bare)
    assert not m.applicable
    assert "could not be decided" in m.note


def test_a_high_wind_site_is_not_scaled_at_all(geometry) -> None:
    """The minimum is a floor, never a multiplier.

    At V = 65 m/s the computed pressures are far above the floor, and the
    scale must be exactly 1.0 - not 0.9-something, which is what a
    ``F_minimum / F_computed`` written without the ``max`` would give, and
    which would quietly REDUCE the design wind.
    """
    site = ProjectSite(
        basic_wind_speed=65.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
    )
    w = envelope_pressures(geometry, site)
    m = minimum_design_wind_load(geometry, w)
    assert not m.governs
    assert m.scale == 1.0
    assert m.F_computed > m.F_minimum


def test_the_longitudinal_endwall_pressure_comes_from_load_case_B(
    geometry, wind
) -> None:
    """Zones 5 and 6 ARE the endwalls, so their difference is the pressure.

    This is what feeds the longitudinal bracing system. Taking it from the
    wind module rather than typing a number is the difference between a
    derived bracing design and an assumed one.
    """
    raw, _ = longitudinal_endwall_pressure(geometry, wind, apply_minimum=False)
    g = wind.gcpf["W_B_pi_pos"]
    assert raw == pytest.approx(wind.qh * (g["5"] - g["6"]))
    assert raw > 0.0


def test_the_longitudinal_direction_has_its_own_minimum(
    geometry, wind
) -> None:
    """Applied over the ENDWALL projection, which is the gable triangle.

    The transverse check uses L x rise, a rectangle. The longitudinal one
    uses B x rise / 2, the gable silhouette. Using the transverse area here
    would overstate the roof share by the building's whole length.
    """
    raw, _ = longitudinal_endwall_pressure(geometry, wind, apply_minimum=False)
    floored, check = longitudinal_endwall_pressure(geometry, wind)
    assert check is not None and check.governs
    assert floored > raw
    assert floored == pytest.approx(raw * check.scale)
    B = geometry.width / 1000.0
    rise = geometry.ridge_rise / 1000.0
    assert check.A_roof == pytest.approx(0.5 * B * rise)


# ---------------------------------------------------------------------
# Coverage gaps
# ---------------------------------------------------------------------

def test_the_coverage_gaps_are_declared_and_specific() -> None:
    """A gap is a check that is ABSENT - not a formula that is unchecked.

    Both block issue and both print on the sheet, but they mean different
    things and must not be collapsed into one another.
    """
    assert COVERAGE_GAPS
    joined = " ".join(COVERAGE_GAPS)
    assert "28.3.4" in joined, "the minimum wind load must stay declared"
    assert "Torsional" in joined
    assert "Multi-span" in joined


def test_the_gaps_reach_the_calculation_sheet() -> None:
    """A gap nobody can see on the sheet is not a declared gap."""
    from bimsteelauto.report import collect_coverage_gaps

    gaps = collect_coverage_gaps()
    wind_gaps = [g for g in gaps if g.startswith("[wind loads]")]
    assert len(wind_gaps) == len(COVERAGE_GAPS)
    assert any("28.3.4" in g for g in wind_gaps)


def test_the_minimum_load_is_no_longer_a_gap_but_its_limits_still_are(
) -> None:
    """Closing a gap means narrowing what it claims, not deleting it.

    Sect. 28.3.4 IS applied now, so no gap may say it is not. What remains
    genuinely absent is narrower and must still be stated: the clause is
    enforced on the transverse direction only, and it is enforced by
    scaling, which is the wrong direction for a combination in which wind
    relieves gravity.
    """
    joined = " ".join(COVERAGE_GAPS)
    assert "28.3.4 minimum design wind loading is NOT applied" not in joined
    gaps = [g for g in COVERAGE_GAPS if "28.3.4" in g]
    assert len(gaps) == 2, gaps
    assert any("TRANSVERSE" in g for g in gaps)
    assert any("RELIEVES" in g for g in gaps)


def test_the_clause_values_are_reachable_as_named_constants() -> None:
    """An engineer checking by hand needs the numbers, not a clause number.

    They moved out of the gap text and into the module, where the check
    itself uses them - so the number on the sheet and the number in the
    prose can no longer drift apart.
    """
    from bimsteelauto.loads.wind import MIN_ROOF_PRESSURE, MIN_WALL_PRESSURE

    assert MIN_WALL_PRESSURE == pytest.approx(0.766084, abs=1e-6)
    assert MIN_ROOF_PRESSURE == pytest.approx(0.383042, abs=1e-6)
