"""Tests for the longitudinal bracing system.

The statics here are closed-form, so every number is checked against
arithmetic written out in the test rather than against a tool.  That is the
strongest form of verification available for a determinate system, and it is
why this module was written without a solver.
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto.analysis.bracing import (
    VERIFIED as A_VERIFIED,
    BracedBay,
    BracingError,
    BracingPlane,
    LongitudinalSystem,
    endwall_force,
    solve_longitudinal,
)
from bimsteelauto.spec import MemberType, PEBGeometry

design = pytest.importorskip(
    "bimsteelauto.design.bracing", reason="pyaisc360 not available"
)
pytest.importorskip("pyaisc360", reason="pyaisc360 not on sys.path")

from bimsteelauto.design.bracing import (  # noqa: E402
    COVERAGE_GAPS,
    ROD_ROOT_AREA,
    VERIFIED as D_VERIFIED,
    BraceDesignError,
    BraceMember,
    BraceType,
    check_brace,
    design_bracing,
)

WIDTH, EAVE, SLOPE, BAY, N_BAYS = 30.0, 7.0, 0.10, 6.0, 8
PRESSURE = 0.9          # kN/m^2


def _geom(**kw):
    base = dict(
        span_widths=[WIDTH],
        bay_spacings=[BAY] * N_BAYS,
        eave_height=EAVE,
        roof_slope=SLOPE,
        purlin_spacing=1.5,
        girt_spacing=2.0,
    )
    base.update(kw)
    return PEBGeometry.from_m(**base)


# ---------------------------------------------------------------------------
# the endwall force, by hand
# ---------------------------------------------------------------------------


def test_endwall_area_is_the_rectangle_plus_the_gable():
    """30 x 7 = 210 m2, plus a 30 x 1.5 triangle = 22.5 m2."""
    g = _geom()
    F_eave, F_base = endwall_force(g, PRESSURE)
    total_area = WIDTH * EAVE + 0.5 * WIDTH * (0.5 * WIDTH * SLOPE)
    assert total_area == pytest.approx(232.5)
    assert F_eave + F_base == pytest.approx(PRESSURE * total_area, rel=1e-9)


def test_the_gable_load_all_arrives_at_eave_level_or_above():
    """There is no structure below the gable to take half of it.

    Splitting the gable 50/50 like the rectangle would send 10 kN of this
    endwall's load to a floor slab that is not under it.
    """
    g = _geom()
    F_eave, F_base = endwall_force(g, PRESSURE)
    rect = PRESSURE * WIDTH * EAVE
    gable = PRESSURE * 0.5 * WIDTH * (0.5 * WIDTH * SLOPE)
    assert F_eave == pytest.approx(0.5 * rect + gable, rel=1e-9)
    assert F_base == pytest.approx(0.5 * rect, rel=1e-9)
    assert F_eave > F_base


def test_a_propped_post_changes_the_split():
    g = _geom()
    half = endwall_force(g, PRESSURE, eave_share=0.5)[0]
    more = endwall_force(g, PRESSURE, eave_share=0.75)[0]
    assert more > half


def test_eave_share_is_bounded():
    with pytest.raises(BracingError):
        endwall_force(_geom(), PRESSURE, eave_share=1.5)


def test_a_flat_roof_has_no_gable_term():
    g = _geom(roof_slope=0.001)
    F_eave, F_base = endwall_force(g, PRESSURE)
    assert F_eave == pytest.approx(F_base, rel=1e-2)


# ---------------------------------------------------------------------------
# the system must exist
# ---------------------------------------------------------------------------


def test_a_building_with_no_bracing_is_refused():
    """Not a zero-force design - a missing one."""
    with pytest.raises(BracingError, match="no load path"):
        LongitudinalSystem(geometry=_geom(), braced_bays=())


def test_a_bay_index_outside_the_building_is_refused():
    with pytest.raises(BracingError, match="outside"):
        LongitudinalSystem(geometry=_geom(), braced_bays=(0, 99))


def test_a_repeated_bay_is_refused():
    with pytest.raises(BracingError, match="more than once"):
        LongitudinalSystem(geometry=_geom(), braced_bays=(2, 2))


# ---------------------------------------------------------------------------
# distribution and resolution
# ---------------------------------------------------------------------------


def test_each_braced_bay_takes_an_equal_share():
    sysm = LongitudinalSystem(geometry=_geom(), braced_bays=(2, 5))
    f = solve_longitudinal(sysm, PRESSURE)
    assert f.per_braced_bay == pytest.approx(f.F_eave / 2.0, rel=1e-9)


def test_more_braced_bays_means_less_per_bay():
    two = solve_longitudinal(
        LongitudinalSystem(geometry=_geom(), braced_bays=(2, 5)), PRESSURE
    )
    four = solve_longitudinal(
        LongitudinalSystem(geometry=_geom(), braced_bays=(1, 3, 5, 7)), PRESSURE
    )
    assert four.per_braced_bay == pytest.approx(two.per_braced_bay / 2.0, rel=1e-9)


def test_the_diagonal_force_is_the_panel_shear_over_cos():
    bay = BracedBay(index=0, plane=BracingPlane.SIDEWALL, width=6000.0, depth=7000.0)
    L = math.hypot(6000.0, 7000.0)
    assert bay.diagonal_length == pytest.approx(L)
    assert bay.cos_theta == pytest.approx(6000.0 / L)
    assert bay.diagonal_force(100.0) == pytest.approx(100.0 * L / 6000.0)


def test_a_flatter_diagonal_carries_more():
    """The amplification a designer has to see.

    A 6 m bay against a 15 m panel depth gives cos = 0.37: the diagonal
    carries 2.7 times the shear it is resisting.
    """
    steep = BracedBay(0, BracingPlane.ROOF, width=6000.0, depth=6000.0)
    flat = BracedBay(0, BracingPlane.ROOF, width=6000.0, depth=15_000.0)
    assert flat.diagonal_force(100.0) > steep.diagonal_force(100.0)
    assert 1.0 / flat.cos_theta == pytest.approx(2.693, rel=1e-3)


def test_the_roof_panel_depth_can_be_split():
    """Halving the panel depth roughly halves the diagonal force."""
    g = _geom()
    full = solve_longitudinal(
        LongitudinalSystem(geometry=g, braced_bays=(2, 5)), PRESSURE
    )
    half = solve_longitudinal(
        LongitudinalSystem(
            geometry=g, braced_bays=(2, 5),
            roof_panel_depth=g.rafter_length / 2.0,
        ),
        PRESSURE,
    )
    assert half.roof_diagonal < full.roof_diagonal
    assert half.roof_diagonal / full.roof_diagonal == pytest.approx(0.57, abs=0.05)


def test_one_braced_wall_doubles_the_wall_diagonal():
    g = _geom()
    two = solve_longitudinal(
        LongitudinalSystem(geometry=g, braced_bays=(2,), n_braced_walls=2), PRESSURE
    )
    one = solve_longitudinal(
        LongitudinalSystem(geometry=g, braced_bays=(2,), n_braced_walls=1), PRESSURE
    )
    assert one.wall_diagonal == pytest.approx(2.0 * two.wall_diagonal, rel=1e-9)


def test_a_negative_pressure_is_refused():
    with pytest.raises(BracingError, match="positive magnitude"):
        solve_longitudinal(
            LongitudinalSystem(geometry=_geom(), braced_bays=(0,)), -1.0
        )


# ---------------------------------------------------------------------------
# the drag strut
# ---------------------------------------------------------------------------


def test_bracing_the_end_bays_leaves_the_strut_with_nothing():
    """The endwall force goes straight into the adjacent brace."""
    f = solve_longitudinal(
        LongitudinalSystem(geometry=_geom(), braced_bays=(0, N_BAYS - 1)), PRESSURE
    )
    assert f.max_strut_force == pytest.approx(0.0)


def test_bracing_inboard_puts_the_end_force_into_the_strut():
    """Each end delivers F_eave/2, which the strut drags to the brace."""
    f = solve_longitudinal(
        LongitudinalSystem(geometry=_geom(), braced_bays=(2, 5)), PRESSURE
    )
    per_end = f.F_eave / 2.0
    forces = dict(f.strut_forces)
    assert forces[0] == pytest.approx(per_end, rel=1e-9)
    assert forces[1] == pytest.approx(per_end, rel=1e-9)
    assert forces[2] == pytest.approx(0.0)
    assert forces[6] == pytest.approx(per_end, rel=1e-9)
    assert f.max_strut_force == pytest.approx(per_end, rel=1e-9)


def test_the_strut_force_is_not_uniform_along_the_building():
    """A single strut size must be checked against the maximum."""
    f = solve_longitudinal(
        LongitudinalSystem(geometry=_geom(), braced_bays=(2, 5)), PRESSURE
    )
    values = {round(v, 6) for _, v in f.strut_forces}
    assert len(values) > 1


def test_every_bay_is_reported():
    f = solve_longitudinal(
        LongitudinalSystem(geometry=_geom(), braced_bays=(2, 5)), PRESSURE
    )
    assert [i for i, _ in f.strut_forces] == list(range(N_BAYS))


# ---------------------------------------------------------------------------
# rod areas - the 22% that gets missed
# ---------------------------------------------------------------------------


def test_rod_root_area_is_well_below_the_nominal():
    """An M20 has 314 mm2 of shank but 245 mm2 at the thread."""
    rod = BraceMember.rod(20.0)
    assert rod.Ag == pytest.approx(math.pi * 20.0**2 / 4.0)
    assert rod.An_effective == pytest.approx(245.0)
    assert rod.An_effective / rod.Ag == pytest.approx(0.78, abs=0.01)


@pytest.mark.parametrize("d", sorted(ROD_ROOT_AREA))
def test_every_tabulated_rod_is_between_70_and_85_percent_of_gross(d):
    """A sanity band on the whole table.

    The ISO tensile stress area of a coarse thread sits near 0.78 of the
    nominal area across the range; a value outside 0.70-0.85 is a typo.
    """
    rod = BraceMember.rod(d)
    assert 0.70 <= rod.An_effective / rod.Ag <= 0.85


def test_an_untabulated_rod_is_refused_with_the_options():
    with pytest.raises(BraceDesignError, match="not in the rod table"):
        BraceMember.rod(19.0)


def test_rod_radius_of_gyration_is_d_over_4():
    assert BraceMember.rod(24.0).r_min == pytest.approx(6.0)


def test_net_area_above_gross_is_refused():
    with pytest.raises(BraceDesignError, match="REDUCED"):
        BraceMember(
            name="bad", brace_type=BraceType.ANGLE,
            Ag=500.0, An_effective=600.0, r_min=10.0,
        )


# ---------------------------------------------------------------------------
# the brace check
# ---------------------------------------------------------------------------


def _forces(**kw):
    return solve_longitudinal(
        LongitudinalSystem(geometry=_geom(), braced_bays=(2, 5), **kw), PRESSURE
    )


def test_brace_check_reports_yielding_or_rupture():
    f = _forces()
    d = check_brace(
        BraceMember.rod(24.0), f.wall_bay, f.wall_diagonal,
        Fy=248.0, Fu=400.0, mark="WB1",
    )
    tension = next(c for c in d.checks if c.name.startswith("Tension"))
    assert "yielding" in tension.name or "rupture" in tension.name
    assert "AISC 360-16" in tension.reference
    assert tension.capacity > 0.0


def test_yielding_governs_a_rod_of_mild_steel_by_hand():
    """phi Ag Fy = 0.9 x 452 x 248 = 100.9 kN, against
    phi An Fu = 0.75 x 353 x 400 = 105.9 kN - so yielding controls.
    """
    f = _forces()
    d = check_brace(
        BraceMember.rod(24.0), f.wall_bay, f.wall_diagonal,
        Fy=248.0, Fu=400.0, mark="WB1",
    )
    tension = next(c for c in d.checks if c.name.startswith("Tension"))
    assert tension.capacity == pytest.approx(
        0.9 * (math.pi * 24.0**2 / 4.0) * 248.0 / 1000.0, rel=0.01
    )
    assert "yielding" in tension.name


def test_a_high_strength_rod_switches_to_rupture():
    """Raise Fy toward Fu and the net section takes over."""
    f = _forces()
    d = check_brace(
        BraceMember.rod(24.0), f.wall_bay, f.wall_diagonal,
        Fy=640.0, Fu=800.0, mark="WB1",
    )
    tension = next(c for c in d.checks if c.name.startswith("Tension"))
    assert "rupture" in tension.name


def test_the_note_shows_the_net_to_gross_ratio():
    f = _forces()
    d = check_brace(
        BraceMember.rod(20.0), f.wall_bay, f.wall_diagonal,
        Fy=248.0, Fu=400.0, mark="WB1",
    )
    tension = next(c for c in d.checks if c.name.startswith("Tension"))
    assert "78% of gross" in tension.note


def test_slenderness_is_advisory_and_never_fails_a_rod():
    """Sect. D1's L/r <= 300 is a recommendation, and rods are exempt."""
    f = _forces()
    d = check_brace(
        BraceMember.rod(20.0), f.wall_bay, f.wall_diagonal,
        Fy=248.0, Fu=400.0, mark="WB1",
    )
    sl = next(c for c in d.checks if "Slenderness" in c.name)
    assert sl.ratio == 0.0
    assert "advisory" in sl.name
    assert "recommendation" in sl.reference
    assert "pretension" in sl.note


def test_an_angle_does_carry_the_slenderness_advice():
    f = _forces()
    angle = BraceMember(
        name="L65x65x6", brace_type=BraceType.ANGLE,
        Ag=749.0, An_effective=0.85 * 640.0, r_min=12.6,
    )
    d = check_brace(angle, f.wall_bay, f.wall_diagonal, Fy=248.0, Fu=400.0, mark="WB1")
    sl = next(c for c in d.checks if "Slenderness" in c.name)
    assert sl.ratio > 0.0


def test_the_design_records_the_tension_only_amplification():
    f = _forces()
    d = check_brace(
        BraceMember.rod(24.0), f.roof_bay, f.roof_diagonal,
        Fy=248.0, Fu=400.0, mark="RB1",
    )
    assert "tension-only" in d.note
    assert "1/cos" in d.note
    assert d.member_type is MemberType.ROOF_BRACING


def test_tension_is_recorded_as_negative_axial():
    """The package convention is compression positive."""
    f = _forces()
    d = check_brace(
        BraceMember.rod(24.0), f.wall_bay, f.wall_diagonal,
        Fy=248.0, Fu=400.0, mark="WB1",
    )
    assert d.forces.P < 0.0
    assert abs(d.forces.P) == pytest.approx(f.wall_diagonal)


def test_design_bracing_returns_both_planes():
    f = _forces()
    out = design_bracing(
        f, roof_brace=BraceMember.rod(24.0), wall_brace=BraceMember.rod(20.0),
        Fy=248.0, Fu=400.0,
    )
    assert [d.mark for d in out] == ["RB1", "WB1"]
    assert out[0].member_type is MemberType.ROOF_BRACING
    assert out[1].member_type is MemberType.WALL_BRACING


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------


def test_all_bracing_relations_are_verified():
    unset = [f"analysis.{k}" for k, v in A_VERIFIED.items() if not v]
    unset += [f"design.{k}" for k, v in D_VERIFIED.items() if not v]
    assert unset == [], f"unverified bracing relations: {unset}"


def test_coverage_gaps_name_the_end_connection_and_the_strut():
    assert any("end connection" in g for g in COVERAGE_GAPS)
    assert any("strut" in g for g in COVERAGE_GAPS)
    assert any("seismic" in g for g in COVERAGE_GAPS)
