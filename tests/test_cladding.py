"""Behaviour and guards for bimsteelauto.loads.cladding.

The coefficients are checked against Struware in
``test_cladding_verification.py``.  What is checked here is the part no
table can settle: that a component is designed on a LARGER load than the
frame it hangs off, that both directions are produced, that a roof outside
Fig. 30.3-2A's range is refused rather than extrapolated, and that the zone
a member sits in is a choice the caller has to make rather than one this
module quietly makes for them.
"""

from __future__ import annotations

import pytest

from bimsteelauto.loads.cladding import (
    COVERAGE_GAPS,
    MAX_SLOPE_FIG_30_3_2A,
    MIN_CC_PRESSURE,
    CCZone,
    cladding_pressure,
    effective_wind_area,
    gcp,
    girt_pressures,
    governing_pressure,
    purlin_pressures,
)
from bimsteelauto.loads.gravity import LoadError
from bimsteelauto.loads.wind import envelope_pressures
from bimsteelauto.spec import (
    EnclosureClassification,
    ExposureCategory,
    PEBGeometry,
    ProjectSite,
    RiskCategory,
)


@pytest.fixture
def geometry() -> PEBGeometry:
    return PEBGeometry.from_m(
        span_widths=[30.0],
        bay_spacings=[6.0, 7.5, 7.5, 7.5, 6.0],
        eave_height=7.0,
        roof_slope=0.10,                       # 1:10 -> 5.71 deg
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


# ---------------------------------------------------------------------
# THE POINT OF THE MODULE
# ---------------------------------------------------------------------

def test_a_purlin_sees_more_than_the_frame_does(geometry, wind) -> None:
    """The whole reason Chapter 30 exists, asserted rather than asserted-to.

    The worst MWFRS roof suction on this building is zone 2E at
    GCpf = -1.07.  The worst C&C suction on a purlin in the roof EDGE zone
    is a good deal more than that, because a purlin sees a small patch of
    roof for a short time and a frame sees the average of the whole thing.

    Before this module existed the purlins were designed on the MWFRS
    numbers, which is the unconservative error this test now makes
    impossible to reintroduce quietly.
    """
    mwfrs = min(wind.pressures["W_A_pi_pos"].values())      # most negative
    cc = purlin_pressures(geometry, wind, zones=(CCZone.ROOF_EDGE,))
    assert cc.outward < mwfrs, (
        f"C&C {cc.outward:.3f} kN/m2 is not worse than MWFRS "
        f"{mwfrs:.3f} kN/m2 - Chapter 30 would be pointless"
    )
    assert cc.uplift / abs(mwfrs) > 1.2


def test_the_corner_zone_is_worse_than_the_edge_which_beats_the_field(
    geometry, wind
) -> None:
    """3 > 2 > 1 > 1', in suction, at any one area.

    The ordering is what the zones ARE.  A mapping that shuffled them would
    still produce plausible numbers on every purlin.
    """
    area = 18.75
    out = [
        cladding_pressure(wind, z, area).outward
        for z in (CCZone.ROOF_CORNER, CCZone.ROOF_EDGE, CCZone.ROOF_1,
                  CCZone.ROOF_INTERIOR)
    ]
    assert out[0] < out[1] < out[2] < out[3]


def test_the_wall_corner_is_worse_than_the_wall_field(geometry, wind) -> None:
    area = 25.0
    corner = cladding_pressure(wind, CCZone.WALL_CORNER, area).outward
    field = cladding_pressure(wind, CCZone.WALL_INTERIOR, area).outward
    assert corner < field


# ---------------------------------------------------------------------
# Both directions
# ---------------------------------------------------------------------

def test_every_zone_produces_an_inward_and_an_outward_pressure(
    geometry, wind
) -> None:
    """A girt is pushed in on one wall and sucked out on the other.

    The same member has to survive both, so both are returned together and
    neither can be forgotten at the call site.
    """
    for zone in CCZone.ROOF + CCZone.WALL:
        cp = cladding_pressure(wind, zone, 18.75)
        assert cp.inward > 0.0, zone
        assert cp.outward < 0.0, zone


def test_the_worst_pairing_of_internal_pressure_is_used(
    geometry, wind
) -> None:
    """Outward pairs -GCp with +GCpi; inward pairs +GCp with -GCpi.

    Pairing them the other way round gives two numbers that are both
    smaller and both entirely plausible - there is nothing on the face of
    the answer to say which pairing produced it.
    """
    cp = cladding_pressure(wind, CCZone.ROOF_EDGE, 18.75)
    gcpi_pos, gcpi_neg = wind.GCpi

    raw_out = wind.qh * (cp.gcp_negative - gcpi_pos)
    raw_in = wind.qh * (cp.gcp_positive - gcpi_neg)

    # Suction is far past the floor, so it comes through as computed.
    assert not cp.minimum_applied[1]
    assert cp.outward == pytest.approx(raw_out)

    # The inward case on this building is small (0.372 kN/m2) and the
    # Sect. 30.2.2 floor takes over. The pairing is still checked - on the
    # raw value - because the floor must not be allowed to hide it.
    assert cp.minimum_applied[0]
    assert cp.inward == pytest.approx(MIN_CC_PRESSURE)
    assert raw_in < MIN_CC_PRESSURE

    # The wrong pairings, shown to be milder in both directions.
    assert wind.qh * (cp.gcp_negative - gcpi_neg) > raw_out
    assert wind.qh * (cp.gcp_positive - gcpi_pos) < raw_in


# ---------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------

def test_a_roof_steeper_than_fig_30_3_2A_is_refused(site) -> None:
    """No extrapolation, because there is nothing safe to extrapolate to.

    Figs. 30.3-2B and 30.3-2C change the zone LAYOUT as well as the
    coefficients, so a value carried past 7 degrees would not merely be
    inaccurate - it would be answering a different question.
    """
    with pytest.raises(LoadError, match="30.3-2A"):
        gcp(CCZone.ROOF_EDGE, 18.75, theta_deg=MAX_SLOPE_FIG_30_3_2A + 0.1)


def test_a_1_in_10_peb_roof_is_inside_the_range(geometry) -> None:
    """5.71 degrees, comfortably under 7 - the common PEB case works."""
    assert geometry.slope_deg == pytest.approx(5.7106, abs=0.001)
    assert geometry.slope_deg < MAX_SLOPE_FIG_30_3_2A
    neg, pos = gcp(CCZone.ROOF_EDGE, 18.75, theta_deg=geometry.slope_deg)
    assert neg < 0 and pos > 0


def test_a_2_in_12_roof_is_refused_and_says_why(site) -> None:
    """9.46 degrees. A real PEB pitch that this module cannot do yet."""
    steep = PEBGeometry.from_m(
        span_widths=[30.0], bay_spacings=[6.0] * 5, eave_height=7.0,
        roof_slope=2.0 / 12.0, purlin_spacing=1.5, girt_spacing=2.0,
    )
    assert steep.slope_deg > MAX_SLOPE_FIG_30_3_2A
    w = envelope_pressures(steep, site)
    with pytest.raises(LoadError, match="30.3-2B"):
        purlin_pressures(steep, w)


def test_an_unknown_zone_is_refused(geometry, wind) -> None:
    for bad in ("6", "2E", "", "roof"):
        with pytest.raises(LoadError, match="not a Chapter 30 zone"):
            gcp(bad, 18.75, theta_deg=5.0)


def test_a_non_positive_area_or_span_is_refused() -> None:
    with pytest.raises(LoadError, match="must be positive"):
        gcp(CCZone.ROOF_EDGE, 0.0, theta_deg=5.0)
    with pytest.raises(LoadError, match="span must be positive"):
        effective_wind_area(0.0, 1500.0)
    with pytest.raises(LoadError, match="tributary width must be positive"):
        effective_wind_area(7500.0, 0.0)


def test_choosing_between_no_zones_is_refused(geometry, wind) -> None:
    """An empty zone list is a missing decision, not an empty answer."""
    with pytest.raises(LoadError, match="no zones"):
        governing_pressure(wind, (), 18.75)


# ---------------------------------------------------------------------
# The purlin and girt helpers
# ---------------------------------------------------------------------

def test_the_purlin_helper_uses_the_largest_bay(geometry, wind) -> None:
    """Non-uniform bays: 6.0 / 7.5 / 7.5 / 7.5 / 6.0 m.

    The largest bay governs the bending moment, which grows with span
    squared, and it also gives the largest effective area and hence the
    smallest GCp. The two pull opposite ways; the helper takes the largest
    span and says so, rather than averaging - which is the v6 defect in
    another costume.
    """
    cp = purlin_pressures(geometry, wind)
    expected_area = effective_wind_area(
        max(geometry.bay_spacings), geometry.purlin_spacing
    )
    assert cp.area == pytest.approx(expected_area)
    assert cp.area == pytest.approx(7.5 * 2.5)      # span/3 governs


def test_the_girt_helper_uses_the_girt_spacing_not_the_purlin_spacing(
    geometry, wind
) -> None:
    p = purlin_pressures(geometry, wind)
    g = girt_pressures(geometry, wind)
    # Both are on span/3 here, so the areas coincide - which is exactly why
    # a test that only compared areas would not catch a swapped spacing.
    assert p.area == pytest.approx(g.area)
    assert p.zone in CCZone.ROOF
    assert g.zone in CCZone.WALL


def test_a_close_spaced_girt_falls_back_to_span_over_three(
    geometry, wind
) -> None:
    """0.9 m girts on a 7.5 m bay: span/3 = 2.5 m still governs."""
    tight = PEBGeometry.from_m(
        span_widths=[30.0], bay_spacings=[7.5] * 4, eave_height=7.0,
        roof_slope=0.10, purlin_spacing=1.5, girt_spacing=0.9,
    )
    a = effective_wind_area(7500.0, 900.0)
    assert a == pytest.approx(7.5 * 2.5)


def test_the_governing_zone_is_named_not_just_its_number(
    geometry, wind
) -> None:
    """The sheet must be able to say WHICH zone sized the purlin.

    Returning a bare pressure would leave a reviewer unable to tell an
    edge-zone purlin from a corner one, which is the single most useful
    thing to know when the answer looks large.
    """
    cp = governing_pressure(
        wind, (CCZone.ROOF_INTERIOR, CCZone.ROOF_EDGE, CCZone.ROOF_CORNER),
        18.75,
    )
    assert cp.zone == CCZone.ROOF_CORNER
    assert cp.gcp_negative < 0


def test_the_governing_choice_is_made_on_suction(geometry, wind) -> None:
    """Uplift sizes a purlin: under it the bottom flange is unbraced."""
    zones = (CCZone.ROOF_INTERIOR, CCZone.ROOF_CORNER)
    chosen = governing_pressure(wind, zones, 18.75)
    others = [cladding_pressure(wind, z, 18.75) for z in zones]
    assert chosen.outward == min(o.outward for o in others)


# ---------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------

def test_the_result_carries_what_the_sheet_has_to_print(
    geometry, wind
) -> None:
    cp = purlin_pressures(geometry, wind)
    assert cp.qh == wind.qh
    assert cp.GCpi == wind.GCpi
    assert cp.area > 0
    assert "30.3-1" in cp.reference
    assert cp.uplift == abs(cp.outward)


def test_whether_the_minimum_was_applied_is_reported(geometry, wind) -> None:
    """A floored number and a computed one must not look the same.

    16 psf is a floor, not a result: a reader has to be able to see that
    the pressure they are looking at came from Sect. 30.2.2 rather than
    from the coefficient beside it.
    """
    cp = cladding_pressure(wind, CCZone.ROOF_INTERIOR, 92.903)
    assert isinstance(cp.minimum_applied, tuple)
    assert len(cp.minimum_applied) == 2
    # At this building's qh the inward case is small enough to be floored.
    assert cp.minimum_applied[0]
    assert cp.inward == pytest.approx(MIN_CC_PRESSURE)


def test_the_coverage_gaps_name_the_decisions_left_to_the_caller() -> None:
    joined = " ".join(COVERAGE_GAPS)
    assert "zone" in joined and "GEOMETRY decision" in joined
    assert "30.3-2B" in joined
    assert "overhang" in joined.lower()


def test_the_gaps_reach_the_calculation_sheet() -> None:
    from bimsteelauto.report import collect_coverage_gaps

    gaps = [
        g for g in collect_coverage_gaps()
        if g.startswith("[components and cladding]")
    ]
    assert len(gaps) == len(COVERAGE_GAPS)


def test_the_wind_module_no_longer_claims_chapter_30_is_missing() -> None:
    """Closing a gap means the module that had it stops claiming it.

    ``loads.wind`` used to say C&C pressures "need to be derived
    separately". They now exist, and its gap must point at them instead of
    at nothing.
    """
    from bimsteelauto.loads.wind import COVERAGE_GAPS as WIND_GAPS

    joined = " ".join(WIND_GAPS)
    assert "bimsteelauto.loads.cladding" in joined
    assert "UNDER-designed" in joined
