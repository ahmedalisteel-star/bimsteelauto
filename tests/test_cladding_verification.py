"""Independent verification of bimsteelauto.loads.cladding.

ASCE 7-16 Chapter 30 Part 1 - components and cladding.

Reference: Struware CS2024, a commercial ASCE 7-16 implementation by a third
party, at theta = 1.2 deg.  It prints both C&C figures in full - GCp at four
effective wind areas and the resulting pressure at eight - for every zone of
Fig. 30.3-2A and Fig. 30.3-1.  Fixture:
``struware_asce716_cladding.json``, with the source PDF beside it.

One caveat, recorded in the fixture and repeated here because it decides
what these tests may claim: Struware's building is h = 62 ft, so its sheet
is headed "Alternate design 60'<h<90'" rather than Part 1.  The GCp values
it prints are nonetheless the Fig. 30.3-1 and 30.3-2A values - that is what
the alternate route re-uses - so this run verifies THE TABLES AND THE
INTERPOLATION, which is what this package implements.  It cannot verify
anything peculiar to the h <= 60 ft route that is not the figures.

Run 2026-09-02.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

from bimsteelauto.loads.cladding import (
    GCP_ROOF_LOW_SLOPE,
    GCP_WALL,
    MIN_CC_PRESSURE,
    VERIFIED,
    CCZone,
    cladding_pressure,
    effective_wind_area,
    gcp,
)
from bimsteelauto.loads.wind import MIN_WALL_PRESSURE, envelope_pressures
from bimsteelauto.spec import (
    EnclosureClassification,
    ExposureCategory,
    PEBGeometry,
    ProjectSite,
    RiskCategory,
)

pytestmark = pytest.mark.verification

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

FT = 0.3048
SQFT = FT ** 2
MPH = 0.44704
PSF = 47.880259          # Pa per psf


@pytest.fixture(scope="module")
def ref() -> dict:
    return json.loads(
        (FIXTURES / "struware_asce716_cladding.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture(scope="module")
def wind(ref):
    """A WindResult on Struware's own inputs.

    Only qh, GCpi and theta are read out of it by Chapter 30, so the
    geometry only has to reproduce the mean roof height and the slope.
    """
    inp = ref["inputs"]
    slope = math.tan(math.radians(inp["roof_angle_deg"]))
    g = PEBGeometry.from_m(
        span_widths=[inp["least_width_ft"] * FT],
        bay_spacings=[inp["building_length_ft"] * FT / 4.0] * 4,
        eave_height=inp["mean_roof_height_ft"] * FT,
        roof_slope=slope,
        purlin_spacing=1.5,
        girt_spacing=2.0,
    )
    site = ProjectSite(
        basic_wind_speed=inp["V_ultimate_mph"] * MPH,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
        Kzt=inp["Kzt"],
    )
    return envelope_pressures(g, site, Ke=inp["Ke"])


def _psf(kpa: float) -> float:
    return kpa * 1000.0 / PSF


# =====================================================================
# 1.  qh, so the pressures below rest on the right base
# =====================================================================

def test_qh_reproduces_struwares_base_pressure(ref, wind) -> None:
    """35.858 psf longhand against the 35.9 psf Struware rounds it to.

    Everything in this file is qh multiplied by a coefficient, so if qh
    were wrong every comparison would fail together and none of them would
    say why.  Checked once, first, on its own.
    """
    assert _psf(wind.qh) == pytest.approx(
        ref["inputs"]["qh_psf_exact_longhand"], abs=0.05
    )


# =====================================================================
# 2.  Fig. 30.3-2A, the roof  ->  gcp_roof_table, log_interpolation
# =====================================================================

@pytest.mark.parametrize("zone", ["1'", "1", "2", "3", "+"])
def test_roof_gcp_matches_struware(ref, wind, zone) -> None:
    """GCp at 10, 100, 500 and 1000 ft^2, against a second implementation.

    The 100 ft^2 column is the load-bearing one: it is an INTERIOR point of
    every curve, so it tests the interpolation and not merely the two
    endpoints anyone could transcribe.
    """
    block = ref["roof_fig_30_3_2A"]
    areas = block["gcp_areas_sqft"]
    printed = block["gcp"][zone]
    for a_sqft, expected in zip(areas, printed):
        neg, pos = gcp("1", a_sqft * SQFT, theta_deg=1.2)
        if zone == "+":
            mine = pos
        else:
            mine, _ = gcp(zone, a_sqft * SQFT, theta_deg=1.2)
        assert mine == pytest.approx(expected, abs=0.005), (
            f"zone {zone} at {a_sqft} sf: mine {mine:.4f}, "
            f"Struware {expected}"
        )


@pytest.mark.parametrize("zone", ["1'", "1", "2", "3", "+"])
def test_roof_pressures_match_struware(ref, wind, zone) -> None:
    """All 32 printed roof pressures, through Eq. 30.3-1.

    This is the end-to-end check: table, interpolation, internal-pressure
    pairing and the Sect. 30.2.2 floor, all at once, against numbers this
    package did not produce.
    """
    block = ref["roof_fig_30_3_2A"]
    for a_sqft, expected in zip(
        block["pressure_areas_sqft"], block["pressure_psf"][zone]
    ):
        area = a_sqft * SQFT
        cp = cladding_pressure(wind, "1" if zone == "+" else zone, area,
                               theta_deg=1.2)
        mine = _psf(cp.inward if zone == "+" else cp.outward)
        assert mine == pytest.approx(expected, abs=0.15), (
            f"zone {zone} at {a_sqft} sf: mine {mine:.2f} psf, "
            f"Struware {expected} psf"
        )


def test_zone_1_prime_is_flat_then_falls(ref) -> None:
    """The one curve a single straight line would get wrong.

    Zone 1' holds -0.9 out to 100 ft^2 and only then falls away to -0.4 at
    1000.  Struware's 500 and 1000 ft^2 columns (-0.55 and -0.40) are what
    prove the kink is there: a straight log-line from 10 ft^2 to 1000 ft^2
    would read -0.475 at 500, and the printed -0.55 rejects it.

    The direction matters as much as the size.  The straight line gives
    LESS suction than the real curve, so collapsing zone 1' to two points
    would under-design every purlin in the roof field - and by 14%, which
    is small enough to look like rounding.
    """
    bps = GCP_ROOF_LOW_SLOPE["1'"]
    assert len(bps) == 3
    at_10, _ = gcp("1'", 10 * SQFT, theta_deg=1.2)
    at_100, _ = gcp("1'", 100 * SQFT, theta_deg=1.2)
    at_500, _ = gcp("1'", 500 * SQFT, theta_deg=1.2)
    assert at_10 == pytest.approx(-0.9)
    assert at_100 == pytest.approx(-0.9)
    assert at_500 == pytest.approx(-0.55, abs=0.005)

    straight = -0.9 + (-0.4 + 0.9) * math.log10(50.0) / math.log10(100.0)
    assert straight == pytest.approx(-0.4753, abs=0.001)
    assert straight > at_500                       # less suction: unsafe
    assert abs(at_500 - straight) > 0.07


def test_the_interpolation_is_logarithmic_not_linear() -> None:
    """A linear-in-area reading is rejected by the printed table.

    Roof zone 1 runs -1.7 at 10 ft^2 to -1.0 at 500.  Interpolated on
    log(A) it gives -1.288 at 100 ft^2, which is Struware's -1.29.  Linear
    in A it would give -1.577, which is nothing like it.
    """
    mine, _ = gcp("1", 100 * SQFT, theta_deg=1.2)
    assert mine == pytest.approx(-1.29, abs=0.005)

    f_linear = (100.0 - 10.0) / (500.0 - 10.0)
    linear = -1.7 + f_linear * (-1.0 + 1.7)
    assert linear == pytest.approx(-1.5714, abs=0.001)
    assert abs(mine - linear) > 0.25


# =====================================================================
# 3.  Fig. 30.3-1, the walls
#     ->  gcp_wall_table, theta_10_deg_wall_reduction
# =====================================================================

@pytest.mark.parametrize("zone", ["4", "5", "+"])
def test_wall_net_coefficients_match_struware(ref, wind, zone) -> None:
    """Struware prints (GCp - GCpi) for the walls, not GCp.

    Its column is headed "Walls GCp +/- GCpi", and the values only make
    sense that way: -1.17 at 10 ft^2 is -1.1 x 0.9 - 0.18, not a GCp.
    Reading it as GCp would put every wall pressure 18% out.
    """
    block = ref["wall_fig_30_3_1"]
    for a_sqft, expected in zip(
        block["areas_sqft"], block["gcp_minus_gcpi"][zone]
    ):
        neg, pos = gcp("4" if zone == "+" else zone, a_sqft * SQFT,
                       theta_deg=1.2)
        gcpi_pos, gcpi_neg = wind.GCpi
        mine = (pos - gcpi_neg) if zone == "+" else (neg - gcpi_pos)
        assert mine == pytest.approx(expected, abs=0.005), (
            f"wall zone {zone} at {a_sqft} sf"
        )


@pytest.mark.parametrize("zone", ["4", "5", "+"])
def test_wall_pressures_match_struware(ref, wind, zone) -> None:
    block = ref["wall_fig_30_3_1"]
    for a_sqft, expected in zip(
        block["areas_sqft"], block["pressure_psf"][zone]
    ):
        cp = cladding_pressure(wind, "4" if zone == "+" else zone,
                               a_sqft * SQFT, theta_deg=1.2)
        mine = _psf(cp.inward if zone == "+" else cp.outward)
        assert mine == pytest.approx(expected, abs=0.15), (
            f"wall zone {zone} at {a_sqft} sf"
        )


def test_the_ten_percent_wall_reduction_is_applied_below_10_degrees(
    ref,
) -> None:
    """Fig. 30.3-1's footnote, which Struware prints AND applies.

    Without the 0.9 factor every wall value is 10% too large.  That is
    conservative, so it would never show up as a failure - it would just
    quietly oversize every girt in the building, and a reviewer comparing
    against a program would find a 10% gap with no obvious cause.
    """
    printed = ref["wall_fig_30_3_1"]["footnote_printed_by_struware"]
    assert "10%" in printed and "10 deg" in printed

    shallow, _ = gcp("4", 10 * SQFT, theta_deg=5.0)
    steep, _ = gcp("4", 10 * SQFT, theta_deg=15.0)
    assert shallow == pytest.approx(-0.99)          # -1.1 x 0.9
    assert steep == pytest.approx(-1.10)            # the raw figure value
    assert shallow / steep == pytest.approx(0.90)


def test_the_reduction_switches_exactly_at_10_degrees() -> None:
    """A footnote boundary, pinned so it cannot drift by a degree."""
    at_10, _ = gcp("4", 10 * SQFT, theta_deg=10.0)
    just_over, _ = gcp("4", 10 * SQFT, theta_deg=10.0001)
    assert at_10 == pytest.approx(-0.99)
    assert just_over == pytest.approx(-1.10)


def test_the_wall_reduction_does_not_touch_the_roof() -> None:
    """The footnote is on Fig. 30.3-1 only.

    Struware's roof values are the raw figure values (-1.7, -2.3, -3.2,
    -0.9, +0.3) with no factor, which is how we know the note sits under
    the wall table and not over both.
    """
    for zone, expected in (("1", -1.7), ("2", -2.3), ("3", -3.2),
                           ("1'", -0.9)):
        mine, _ = gcp(zone, 10 * SQFT, theta_deg=1.2)
        assert mine == pytest.approx(expected)


# =====================================================================
# 4.  Sect. 30.2.2  ->  minimum_cc_pressure
# =====================================================================

def test_the_16psf_floor_is_visible_in_struwares_own_output(ref, wind) -> None:
    """The positive-roof row: 17.2, 16.1, then 16.0 all the way out.

    The first two are computed and the rest are the floor.  A reference
    that only ever printed computed values could not verify a minimum at
    all; this one exercises it in public.
    """
    block = ref["roof_fig_30_3_2A"]
    row = block["pressure_psf"]["+"]
    areas = block["pressure_areas_sqft"]

    floored = [
        cladding_pressure(wind, "1", a * SQFT, theta_deg=1.2)
        for a in areas
    ]
    assert not floored[0].minimum_applied[0]      # 17.2 psf, computed
    assert not floored[1].minimum_applied[0]      # 16.1 psf, computed
    assert all(cp.minimum_applied[0] for cp in floored[2:])
    for cp, expected in zip(floored, row):
        assert _psf(cp.inward) == pytest.approx(expected, abs=0.15)


def test_the_floor_does_not_touch_the_suction_rows(ref, wind) -> None:
    """The free negative control.

    Every printed suction is past 16 psf in magnitude, so the floor must
    stay out of the way. A minimum applied to the magnitude without keeping
    the sign would turn the smallest of them (-20.8 psf) into a pressure.
    """
    block = ref["roof_fig_30_3_2A"]
    for zone in ("1'", "1", "2", "3"):
        for a in block["pressure_areas_sqft"]:
            cp = cladding_pressure(wind, zone, a * SQFT, theta_deg=1.2)
            assert not cp.minimum_applied[1], f"{zone} at {a} sf"
            assert cp.outward < 0.0


def test_the_floor_keeps_the_sign_it_was_given() -> None:
    """"In either direction normal to the surface" - Sect. 30.2.2.

    A tiny suction is raised to a 16 psf SUCTION, not converted into a
    16 psf pressure.  Pinned directly on the helper because the reference
    above never produces a suction small enough to exercise it.
    """
    from bimsteelauto.loads.cladding import _apply_minimum

    up, applied = _apply_minimum(-0.01)
    assert applied and up == pytest.approx(-MIN_CC_PRESSURE)
    down, applied = _apply_minimum(0.01)
    assert applied and down == pytest.approx(+MIN_CC_PRESSURE)
    big, applied = _apply_minimum(-2.0)
    assert not applied and big == -2.0


def test_the_two_16psf_clauses_have_not_drifted_apart() -> None:
    """Sect. 28.3.4 and Sect. 30.2.2 are different clauses, same number.

    They are written out separately, in the modules that own them, so that
    each carries its own citation.  This is the test that stops the two
    definitions diverging by a rounding.
    """
    assert MIN_CC_PRESSURE == pytest.approx(MIN_WALL_PRESSURE, rel=1e-12)
    assert MIN_CC_PRESSURE == pytest.approx(0.766084, abs=1e-6)


# =====================================================================
# 5.  Sect. 26.2  ->  effective_wind_area
# =====================================================================

def test_effective_wind_area_uses_span_over_three_when_it_governs() -> None:
    """The rule Struware cannot verify, so it is done longhand.

    Struware tabulates against area as an INPUT - it never derives one - so
    Sect. 26.2 is closed by arithmetic instead, on both branches.

    A purlin at 1.5 m centres spanning 7.5 m: span/3 = 2.5 m beats the 1.5 m
    spacing, so A = 7.5 x 2.5 = 18.75 m2 and NOT 7.5 x 1.5 = 11.25.
    Ignoring the rule would put the purlin on a 40% smaller area, and a
    smaller area means a LARGER GCp - so it is unconservative to skip and
    oversizes nothing.
    """
    a = effective_wind_area(span=7500.0, tributary_width=1500.0)
    assert a == pytest.approx(18.75)
    naive = 7.5 * 1.5
    assert naive == pytest.approx(11.25)
    assert a > naive


def test_effective_wind_area_keeps_a_wide_tributary_width() -> None:
    """It is a floor on the area, never a cap.

    A member at 4 m centres spanning 6 m keeps 4 m, because span/3 = 2 m is
    smaller. Reading the rule as "use span/3" rather than "not less than
    span/3" would halve the area here.
    """
    a = effective_wind_area(span=6000.0, tributary_width=4000.0)
    assert a == pytest.approx(24.0)


def test_a_larger_effective_area_gives_a_smaller_coefficient() -> None:
    """The physical reason the rule exists: gusts do not peak everywhere.

    Monotone across every zone of both figures, so a member that got its
    area wrong is always wrong in a predictable direction.
    """
    for zone in ("1'", "1", "2", "3", "4", "5"):
        small, _ = gcp(zone, 1.0, theta_deg=1.2)
        large, _ = gcp(zone, 50.0, theta_deg=1.2)
        assert abs(large) <= abs(small), zone


# =====================================================================
# 6.  The gate
# =====================================================================

def test_every_flag_is_closed_and_the_record_is_present() -> None:
    assert all(VERIFIED.values()), VERIFIED
    assert (FIXTURES / "struware_asce716_cladding.json").exists()
    # The source Struware PDF is kept privately, not in the public repo;
    # the JSON fixture above is the published record of that run.


def test_flags_are_booleans() -> None:
    for k, v in VERIFIED.items():
        assert isinstance(v, bool), f"{k} = {v!r} is not a bool"


def test_the_fixture_states_what_it_cannot_close(ref) -> None:
    """And what it cannot close is the effective wind area.

    Saying so is what stops a later reader citing this run for Sect. 26.2,
    which it never exercises.
    """
    cannot = " ".join(ref["flags_this_cannot_close"])
    assert "effective_wind_area" in cannot
    assert "READ_THIS_FIRST" in ref
    assert "60'<h<90'" in ref["READ_THIS_FIRST"]["which_procedure"]


def test_the_tables_have_the_shape_the_figures_have() -> None:
    assert set(GCP_WALL) == {"4", "5", "+"}
    assert set(GCP_ROOF_LOW_SLOPE) == {"1'", "1", "2", "3", "+"}
    for table in (GCP_WALL, GCP_ROOF_LOW_SLOPE):
        for zone, bps in table.items():
            areas = [a for a, _ in bps]
            assert areas == sorted(areas), zone
