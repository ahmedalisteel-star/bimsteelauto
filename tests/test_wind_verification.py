"""Independent verification of bimsteelauto.loads.wind.

ASCE 7-16 Chapter 28 Part 1, the envelope procedure.  Nothing else in the
toolkit implements it - the vendored ``asce7-loads`` scripts are Chapter 27
directional and say so in their own docstring - so the references here are
external, and both are recorded in ``tests/fixtures/``.

Reference 1 - Struware CS2024 (Ver 2024-03-08), a commercial ASCE 7-16
program written by a third party.  Its published example set prints the
whole of Fig. 28.3-1 at theta = 1.2 deg: both load cases, every zone, and
every zone again with each sign of internal pressure.  It also prints Kz,
qh and the end-zone width.  Fixture:
``struware_asce716_envelope_theta1p2.json``, with the source PDF beside it.

Reference 2 - a published hand calculation at theta = 26.57 deg, which is
the only one of the two that exercises the angle interpolation.  Source:
https://static1.squarespace.com/static/5cf0081b6e2a210001dbde1d/t/601a03539375f37369be3517/1612317523915/ASCE+7-10+Wind+Loads+-+Envelope+Proced+Part+1+-+MWFRS.pdf

Reference 3 - ASCE 7-16 Table 26.10-1 itself, transcribed here, against
which the Kz equation is swept at 48 points.

Why two references and not one.  Struware sits at 1.2 deg, inside the flat
0-5 degree band, so it exercises no interpolation whatsoever; the hand
calculation sits at 26.57 deg and pins nothing about the flat band.  Each
closes what the other cannot, and the flat band is the one that matters,
because a PEB roof is 1:10 (5.7 deg) or flatter.

Run 2026-09-02.
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

from bimsteelauto.loads.wind import (
    GCPF_LOAD_CASE_A,
    GCPF_LOAD_CASE_B,
    MIN_ROOF_PRESSURE,
    MIN_WALL_PRESSURE,
    VERIFIED,
    end_zone_width,
    envelope_pressures,
    gcpf_load_case_a,
    gcpf_load_case_b,
    minimum_design_wind_load,
    net_horizontal_pressures,
    velocity_pressure,
    velocity_pressure_coefficient,
)
from bimsteelauto.spec import (
    EnclosureClassification,
    ExposureCategory,
    PEBGeometry,
    ProjectSite,
    RiskCategory,
)

pytestmark = pytest.mark.verification

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"

FT = 0.3048           # m per ft, exact
MPH = 0.44704         # m/s per mph, exact
PSF = 47.880259       # Pa per psf


@pytest.fixture(scope="module")
def struware() -> dict:
    return json.loads(
        (FIXTURES / "struware_asce716_envelope_theta1p2.json").read_text(
            encoding="utf-8"
        )
    )


def _geometry_m(*, width, length, mean_roof_height, slope=0.02):
    """A geometry with a stated least dimension and mean roof height.

    Below 10 degrees ``mean_roof_height`` IS the eave height (ASCE 7-16
    Sect. 26.3), so a low slope lets h be set exactly.  ``end_zone_width``
    reads only width, length and h, so nothing else here matters.
    """
    assert math.degrees(math.atan(slope)) <= 10.0
    return PEBGeometry.from_m(
        span_widths=[width],
        bay_spacings=[length / 4.0] * 4,
        eave_height=mean_roof_height,
        roof_slope=slope,
        purlin_spacing=1.5,
        girt_spacing=2.0,
    )


# =====================================================================
# 1.  The GCpf table, against Struware  ->  gcpf_table
# =====================================================================

def test_load_case_a_table_matches_struware_at_1p2_deg(struware) -> None:
    """All eight Load Case A coefficients, against a second implementation.

    theta = 1.2 deg lands in the flat 0-5 degree band, which is the row a
    PEB actually uses: a 1:10 roof is 5.71 deg and a 0.5:12 roof is 2.39.
    Before this run that row rested on nothing but a transcription.
    """
    printed = struware["printed_outputs"]["GCpf_case_A"]
    mine = gcpf_load_case_a(struware["inputs"]["roof_angle_deg"])
    assert set(mine) == set(printed)
    for zone, expected in printed.items():
        assert mine[zone] == pytest.approx(expected, abs=1e-12), zone


def test_load_case_b_table_matches_struware(struware) -> None:
    """All twelve Load Case B coefficients, including the end-wall zones.

    Zones 5 and 6 are the endwalls.  They are not on a transverse frame,
    but they are half the tuple, and a slip in them would silently corrupt
    the longitudinal system that does use them.
    """
    printed = struware["printed_outputs"]["GCpf_case_B"]
    mine = gcpf_load_case_b()
    assert set(mine) == set(printed)
    for zone, expected in printed.items():
        assert mine[zone] == pytest.approx(expected, abs=1e-12), zone


def test_load_case_b_reproduces_the_flat_load_case_a_row() -> None:
    """A cross-check the figure's own structure demands.

    Fig. 28.3-1 directs that the longitudinal direction be taken at
    theta = 0 with the zone 2/3 boundary at mid-length.  So Load Case B's
    roof zones must equal Load Case A's theta = 0 roof zones, and its
    endwall zones 5 and 6 must equal Load Case A's windward and leeward
    wall zones 1 and 4.

    The two tables are separately written literals in the module.  Their
    agreeing on all eight values is a genuine second transcription of the
    same row, not a restatement of the first.
    """
    a0 = gcpf_load_case_a(0.0)
    b = gcpf_load_case_b()
    for a_zone, b_zone in (
        ("1", "5"), ("2", "2"), ("3", "3"), ("4", "6"),
        ("1E", "5E"), ("2E", "2E"), ("3E", "3E"), ("4E", "6E"),
    ):
        assert a0[a_zone] == pytest.approx(b[b_zone], abs=1e-12), (
            f"A zone {a_zone} = {a0[a_zone]} but B zone {b_zone} = {b[b_zone]}"
        )


def test_the_90_degree_row_satisfies_its_limiting_case() -> None:
    """At theta = 90 the roof IS the wall, and the table must say so.

    A vertical "roof" is a windward and a leeward wall, so zone 2 must
    equal zone 1 and zone 3 must equal zone 4, in the interior set and the
    end set alike.  That is a physical identity, not a reading of the
    figure, so it checks the row without consulting the source it came
    from.  The row exists only to terminate the interpolation - no PEB has
    a 90 degree roof - but a slip in it would leak into every angle above
    45 degrees.
    """
    v = gcpf_load_case_a(90.0)
    assert v["2"] == pytest.approx(v["1"])
    assert v["3"] == pytest.approx(v["4"])
    assert v["2E"] == pytest.approx(v["1E"])
    assert v["3E"] == pytest.approx(v["4E"])


def test_the_45_degree_row_is_the_30_degree_row() -> None:
    """Fig. 28.3-1 gives one band for ">30 to 45", so the rows are equal.

    Pinned because a "correction" that made them differ would silently
    introduce interpolation across a band the figure holds flat.
    """
    assert GCPF_LOAD_CASE_A[45.0] == GCPF_LOAD_CASE_A[30.0]
    assert GCPF_LOAD_CASE_A[5.0] == GCPF_LOAD_CASE_A[0.0]


def test_the_table_has_the_shape_the_figure_has() -> None:
    assert all(len(v) == 8 for v in GCPF_LOAD_CASE_A.values())
    assert len(GCPF_LOAD_CASE_B) == 12


# =====================================================================
# 2.  The angle interpolation, against the 26.57 deg example
# =====================================================================

#: The eight values the published example prints, in zone order.  Its
#: building: 30 x 60 ft, He 30 ft, Hmr 34 ft, V 135 mph, Exposure B,
#: enclosed, Kzt 1.0, Kd 0.85, qh 27.76 psf, a = 3.00 ft.
WORKED_EXAMPLE_26p57 = {
    "1": 0.55, "2": -0.10, "3": -0.45, "4": -0.39,
    "1E": 0.73, "2E": -0.19, "3E": -0.58, "4E": -0.53,
}


def test_interpolation_reproduces_the_worked_example_at_26p57_deg() -> None:
    """All eight zones, to the two decimals the example prints.

    theta = 26.57 deg is f = 0.657 of the way from the 20 degree row to
    the 30 degree row, and zone 2 crosses zero over that interval
    (-0.69 to +0.21), so a wrong interpolation could not hide in it.
    """
    mine = gcpf_load_case_a(26.57)
    for zone, expected in WORKED_EXAMPLE_26p57.items():
        assert round(mine[zone], 2) == pytest.approx(expected, abs=1e-12), (
            f"zone {zone}: mine {mine[zone]:.4f} rounds to "
            f"{round(mine[zone], 2)}, example prints {expected}"
        )


def test_the_interpolation_fraction_is_the_one_the_example_used() -> None:
    """f = 0.657, stated so the agreement above can be re-derived by hand."""
    f = (26.57 - 20.0) / (30.0 - 20.0)
    assert f == pytest.approx(0.657)
    lo = GCPF_LOAD_CASE_A[20.0]
    hi = GCPF_LOAD_CASE_A[30.0]
    by_hand = tuple(lo[i] + f * (hi[i] - lo[i]) for i in range(8))
    mine = gcpf_load_case_a(26.57)
    for value, zone in zip(by_hand, ("1", "2", "3", "4", "1E", "2E", "3E", "4E")):
        assert mine[zone] == pytest.approx(value, abs=1e-12)


def test_interpolation_is_held_flat_outside_the_tabulated_range() -> None:
    assert gcpf_load_case_a(0.0) == gcpf_load_case_a(3.0)
    assert gcpf_load_case_a(90.0) == gcpf_load_case_a(120.0)


def test_interpolation_is_monotone_where_the_table_is() -> None:
    """Zone 2 climbs from -0.69 to +0.21 between 20 and 30 degrees.

    Sampled across the interval so a transposed pair in either row would
    show up as a non-monotone step rather than as one wrong endpoint.
    """
    values = [gcpf_load_case_a(20.0 + i)["2"] for i in range(11)]
    assert values[0] == pytest.approx(-0.69)
    assert values[-1] == pytest.approx(0.21)
    assert all(b > a for a, b in zip(values, values[1:]))


# =====================================================================
# 3.  End-zone width  ->  end_zone_width
# =====================================================================

def test_end_zone_width_matches_struware(struware) -> None:
    """a = 17.5 ft on a 175 x 300 ft building at h = 62 ft.

    This is the "smaller of" branch with 0.1B governing: 0.1 x 175 = 17.5
    against 0.4h = 24.8.  Struware prints both a and 2a, so the doubling
    the end zone uses is confirmed too.
    """
    inp = struware["inputs"]
    out = struware["printed_outputs"]
    g = _geometry_m(
        width=inp["least_width_ft"] * FT,
        length=inp["building_length_ft"] * FT,
        mean_roof_height=inp["mean_roof_height_ft"] * FT,
    )
    a_mm = end_zone_width(g)
    assert a_mm / 1000.0 / FT == pytest.approx(out["edge_strip_a_ft"], abs=0.01)
    assert 2.0 * a_mm / 1000.0 / FT == pytest.approx(
        out["end_zone_2a_ft"], abs=0.02
    )


def test_end_zone_width_matches_the_26p57_example() -> None:
    """a = 3.00 ft on a 30 x 60 ft building at Hmr = 34 ft.

    A second run of the same branch, three orders of magnitude smaller, and
    one where the 3 ft floor sits within 2% of the answer without taking
    it - so it also shows the floor is not being applied when it should
    not be.
    """
    g = _geometry_m(
        width=30.0 * FT, length=60.0 * FT, mean_roof_height=34.0 * FT
    )
    assert end_zone_width(g) / 1000.0 / FT == pytest.approx(3.00, abs=0.01)


def test_the_0p4h_branch_governs_on_a_tall_narrow_building() -> None:
    """The "smaller of" cap, exercised the other way round.

    0.4h governs only in the window 0.1L < h < 0.25L: below it 0.1L is
    smaller, above it the 4% floor takes back over.  Least dimension 50 m,
    h = 8 m: 0.1L = 5.0, 0.4h = 3.2, floors 2.0 and 0.9.  Answer 3.2 m.

    Dropping the "smaller of" would give 5.0 m here - a 56% wider end zone,
    which is wrong in the UNCONSERVATIVE direction, because it would move
    interior frames into the far heavier E coefficients only by accident
    and would misplace the boundary for the frames that matter.
    """
    g = _geometry_m(width=50.0, length=200.0, mean_roof_height=8.0)
    assert end_zone_width(g) == pytest.approx(3200.0)


def test_the_four_percent_floor_governs_on_a_squat_wide_building() -> None:
    """least = 100 m, h = 5 m: min(10.0, 2.0) = 2.0, floored to 0.04L = 4.0.

    Both halves of the rule fire in one calculation: the cap pulls the
    answer down to 2.0 and the floor pushes it back up to 4.0.  Dropping
    the floor would halve the end zone.
    """
    g = _geometry_m(width=100.0, length=300.0, mean_roof_height=5.0)
    assert end_zone_width(g) == pytest.approx(4000.0)


def test_the_absolute_floor_governs_on_a_very_small_building() -> None:
    """least = 15 m, h = 1.5 m: min(1.5, 0.6) = 0.6, floored to 0.9 m.

    0.9 m is ASCE's own soft conversion of 3 ft, printed as "3 ft (0.9 m)"
    in the figure.  3 ft is 0.9144 m, so the SI floor is 1.6% below the
    imperial one.  That gap is the Standard's, not this module's, and it is
    pinned here so nobody "fixes" it into 0.9144 without deciding to.
    """
    g = _geometry_m(width=15.0, length=60.0, mean_roof_height=1.5)
    assert end_zone_width(g) == pytest.approx(900.0)


def test_end_zone_width_uses_the_LEAST_horizontal_dimension() -> None:
    """Swapping width and length must not change a.

    The figure says least horizontal dimension, not width; a building
    longer than it is wide and one wider than it is long get the same a.
    """
    tall = _geometry_m(width=20.0, length=90.0, mean_roof_height=30.0)
    wide = _geometry_m(width=90.0, length=20.0, mean_roof_height=30.0)
    assert end_zone_width(tall) == pytest.approx(end_zone_width(wide))


# =====================================================================
# 4.  Velocity pressure  ->  velocity_pressure
# =====================================================================

def test_Kz_matches_struware_at_62ft_exposure_C(struware) -> None:
    """Kz = 1.144, against a program rather than against the table.

    The equation IS what Table 26.10-1 is tabulated from, so checking it
    against the table is only a check on the transcription.  Struware is a
    separate implementation of the same clause, and agrees to four
    significant figures.
    """
    inp, out = struware["inputs"], struware["printed_outputs"]
    z = inp["mean_roof_height_ft"] * FT
    mine = velocity_pressure_coefficient(z, ExposureCategory.C)
    assert mine == pytest.approx(out["Kz_eq_Kh"], abs=0.0005)


def test_qh_matches_struware(struware) -> None:
    """qh = 35.9 psf, reached two ways.

    First longhand in ASCE's imperial form, 0.00256 Kz Kzt Kd Ke V^2, which
    reproduces the printed value.  Then through this module's SI form,
    0.613 ... V^2, which lands 0.06% low - because ASCE's two constants are
    each rounded and are not exactly equivalent, not because either
    calculation is wrong.  The gap is pinned, so if it ever grows it is a
    defect rather than a rounding.
    """
    inp, out = struware["inputs"], struware["printed_outputs"]
    z_ft = inp["mean_roof_height_ft"]
    V_mph = inp["V_ultimate_mph"]
    Kz = velocity_pressure_coefficient(z_ft * FT, ExposureCategory.C)

    imperial_psf = (
        0.00256 * Kz * inp["Kzt"] * inp["Kd"] * inp["Ke"] * V_mph**2
    )
    assert imperial_psf == pytest.approx(out["qh_psf"], abs=0.05)

    mine_kpa = velocity_pressure(
        V_mph * MPH, z_ft * FT, ExposureCategory.C,
        Kzt=inp["Kzt"], Kd=inp["Kd"], Ke=inp["Ke"],
    )
    imperial_kpa = imperial_psf * PSF / 1000.0
    assert mine_kpa == pytest.approx(imperial_kpa, rel=0.001)
    assert mine_kpa < imperial_kpa, (
        "the SI constant 0.613 is slightly below the imperial 0.00256; if "
        "that ever reverses, one of them has been changed"
    )


#: ASCE 7-16 Table 26.10-1, Case 2 (all heights), transcribed.  Height in
#: feet -> Kz, for Exposures B, C and D.
TABLE_26_10_1 = {
    "B": {15: 0.57, 20: 0.62, 25: 0.66, 30: 0.70, 40: 0.76, 50: 0.81,
          60: 0.85, 70: 0.89, 80: 0.93, 90: 0.96, 100: 0.99, 120: 1.04,
          140: 1.09, 160: 1.13, 180: 1.17, 200: 1.20},
    "C": {15: 0.85, 20: 0.90, 25: 0.94, 30: 0.98, 40: 1.04, 50: 1.09,
          60: 1.13, 70: 1.17, 80: 1.21, 90: 1.24, 100: 1.26, 120: 1.31,
          140: 1.36, 160: 1.39, 180: 1.43, 200: 1.46},
    "D": {15: 1.03, 20: 1.08, 25: 1.12, 30: 1.16, 40: 1.22, 50: 1.27,
          60: 1.31, 70: 1.34, 80: 1.38, 90: 1.40, 100: 1.43, 120: 1.48,
          140: 1.52, 160: 1.55, 180: 1.58, 200: 1.61},
}


@pytest.mark.parametrize("exposure", ["B", "C", "D"])
def test_Kz_equation_reproduces_table_26_10_1(exposure) -> None:
    """48 points, three exposures, 15 to 200 ft.  Max deviation 0.0073.

    The table is printed to two decimals, so agreement closer than 0.005
    cannot be expected everywhere; what is asserted is that every point
    lands within one printed digit.
    """
    cat = getattr(ExposureCategory, exposure)
    for z_ft, tabulated in TABLE_26_10_1[exposure].items():
        mine = velocity_pressure_coefficient(z_ft * FT, cat)
        assert mine == pytest.approx(tabulated, abs=0.01), (
            f"Exposure {exposure} at {z_ft} ft: equation {mine:.4f}, "
            f"table {tabulated}"
        )


def test_the_table_is_a_rounding_of_the_equation_in_both_directions() -> None:
    """The whole 48-point sweep, as a deviation band: -0.0028 to +0.0073.

    Stated as a band, and two-sided, because it is: 12 of the 48 tabulated
    values sit ABOVE the equation and 36 below.  That is what an ordinary
    two-decimal rounding looks like, and it is the evidence that the table
    and the equation are the same relation.

    An earlier version of this test asserted the deviations were all
    positive - that the table truncated rather than rounded, so that using
    the equation was always the conservative side.  That was wrong; the
    sweep it came from had taken an absolute value.  It is recorded here
    because the false version would have read as a licence to prefer the
    equation on safety grounds, and there is no such licence: the two agree
    to within one printed digit and neither is systematically the safer.
    """
    deviations = [
        velocity_pressure_coefficient(z_ft * FT, getattr(ExposureCategory, e))
        - tabulated
        for e, rows in TABLE_26_10_1.items()
        for z_ft, tabulated in rows.items()
    ]
    assert len(deviations) == 48
    assert min(deviations) == pytest.approx(-0.0028, abs=0.0002)
    assert max(deviations) == pytest.approx(+0.0073, abs=0.0002)
    assert sum(1 for d in deviations if d < 0) == 12


def test_Kz_is_1_at_10m_in_exposure_C() -> None:
    """The anchor that comes from the definition, not from the table.

    The basic wind speed V is defined as the 3-second gust at 10 m in
    Exposure C.  So Kz there must be 1.00 by construction, whatever the
    table says.  The equation gives 1.0009 - the only check here that owes
    nothing at all to a transcription.
    """
    assert velocity_pressure_coefficient(10.0, ExposureCategory.C) == (
        pytest.approx(1.0, abs=0.002)
    )


def test_Kz_is_held_constant_below_15ft() -> None:
    """Table 26.10-1's first row is a band, 0 to 15 ft, not a point."""
    at_15 = velocity_pressure_coefficient(15.0 * FT, ExposureCategory.C)
    for z in (0.5, 2.0, 4.0, 4.5, 4.572):
        assert velocity_pressure_coefficient(z, ExposureCategory.C) == (
            pytest.approx(at_15)
        )
    assert velocity_pressure_coefficient(6.0, ExposureCategory.C) > at_15


def test_exposure_D_is_always_windier_than_C_which_beats_B() -> None:
    """A physical ordering: rougher terrain, lower pressure at a height."""
    for z in (5.0, 10.0, 20.0, 40.0, 60.0):
        b = velocity_pressure_coefficient(z, ExposureCategory.B)
        c = velocity_pressure_coefficient(z, ExposureCategory.C)
        d = velocity_pressure_coefficient(z, ExposureCategory.D)
        assert b < c < d, z


# =====================================================================
# 5.  The internal pressure sign  ->  internal_pressure_sign
# =====================================================================

def _struware_wind(struware):
    """A WindResult on Struware's own inputs."""
    inp = struware["inputs"]
    g = _geometry_m(
        width=inp["least_width_ft"] * FT,
        length=inp["building_length_ft"] * FT,
        mean_roof_height=inp["mean_roof_height_ft"] * FT,
        slope=math.tan(math.radians(inp["roof_angle_deg"])),
    )
    site = ProjectSite(
        basic_wind_speed=inp["V_ultimate_mph"] * MPH,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
        Kzt=inp["Kzt"],
    )
    return envelope_pressures(g, site, Ke=inp["Ke"])


@pytest.mark.parametrize(
    "case,printed_key",
    [
        ("W_A_pi_pos", "case_A_with_plus_GCpi"),
        ("W_A_pi_neg", "case_A_with_minus_GCpi"),
        ("W_B_pi_pos", "case_B_with_plus_GCpi"),
        ("W_B_pi_neg", "case_B_with_minus_GCpi"),
    ],
)
def test_eq_28_3_1_matches_struware_term_by_term(
    struware, case, printed_key
) -> None:
    """40 zone/case values, each against a printed (GCpf - GCpi).

    Struware prints every zone twice, once with each sign of internal
    pressure.  That is what makes it able to verify the SIGN and not just
    the magnitude: an implementation that subtracted GCpi the wrong way
    round would match one column and fail the other.
    """
    w = _struware_wind(struware)
    printed = struware["printed_outputs"][printed_key]
    mine = w.pressures[case]
    assert set(mine) == set(printed)
    for zone, expected in printed.items():
        # p / qh is (GCpf - GCpi), which is what Struware tabulates.
        assert mine[zone] / w.qh == pytest.approx(expected, abs=0.005), zone


def test_positive_GCpi_governs_uplift_on_every_roof_zone(struware) -> None:
    """The rule that sizes the holding-down bolts.

    A pressurised interior pushes up on its own roof, so on a roof already
    in suction the POSITIVE GCpi case is the more negative one.  It reads
    backwards - a positive internal pressure making the answer more
    negative - which is why it is asserted rather than assumed.

    Struware's own two columns confirm it independently: on zone 2 it
    prints -0.51 with -GCpi and -0.87 with +GCpi.
    """
    w = _struware_wind(struware)
    roof_zones = ("2", "3", "2E", "3E")
    for zone in roof_zones:
        assert w.pressures["W_A_pi_pos"][zone] < w.pressures["W_A_pi_neg"][zone]
        assert w.pressures["W_B_pi_pos"][zone] < w.pressures["W_B_pi_neg"][zone]

    printed_pos = struware["printed_outputs"]["case_A_with_plus_GCpi"]
    printed_neg = struware["printed_outputs"]["case_A_with_minus_GCpi"]
    for zone in roof_zones:
        assert printed_pos[zone] < printed_neg[zone], zone


def test_the_uplift_case_this_module_names_is_a_positive_GCpi_case(
    struware,
) -> None:
    """``roof_uplift_case()`` must point at the case the rule above picks."""
    w = _struware_wind(struware)
    assert w.roof_uplift_case().endswith("_pi_pos")


def test_an_open_building_has_no_internal_pressure_difference() -> None:
    """GCpi = 0 both ways, so the two signs collapse onto each other.

    A degenerate case worth pinning: it is the one where an error in the
    sign would leave no trace at all, so it must not be the case anyone
    reasons from.
    """
    g = _geometry_m(width=30.0, length=40.0, mean_roof_height=7.0)
    site = ProjectSite(
        basic_wind_speed=45.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.OPEN,
        risk_category=RiskCategory.II,
    )
    w = envelope_pressures(g, site)
    assert w.GCpi == (0.0, 0.0)
    assert w.pressures["W_A_pi_pos"] == w.pressures["W_A_pi_neg"]


def test_partially_enclosed_is_three_times_the_enclosed_internal_pressure(
) -> None:
    """Table 26.13-1: +/-0.55 against +/-0.18.

    Not a wind-module formula - it is read from ``ProjectSite`` - but the
    pressures depend on it, and it is the input most often left at the
    enclosed default when the building is not enclosed.
    """
    g = _geometry_m(width=30.0, length=40.0, mean_roof_height=7.0)
    common = dict(
        basic_wind_speed=45.0,
        exposure_category=ExposureCategory.C,
        risk_category=RiskCategory.II,
    )
    enc = envelope_pressures(
        g,
        ProjectSite(
            enclosure_classification=EnclosureClassification.ENCLOSED, **common
        ),
    )
    par = envelope_pressures(
        g,
        ProjectSite(
            enclosure_classification=(
                EnclosureClassification.PARTIALLY_ENCLOSED
            ),
            **common,
        ),
    )
    assert enc.GCpi == (0.18, -0.18)
    assert par.GCpi == (0.55, -0.55)
    uplift_enc = enc.pressures["W_A_pi_pos"]["2"]
    uplift_par = par.pressures["W_A_pi_pos"]["2"]
    assert uplift_par < uplift_enc
    assert uplift_enc - uplift_par == pytest.approx(enc.qh * 0.37, rel=1e-9)


# =====================================================================
# 6.  Sect. 28.3.4, the minimum design wind loading
#     ->  minimum_design_load
# =====================================================================

def test_the_minimum_constants_are_exact_conversions_of_16_and_8_psf() -> None:
    """No measurement in this, only defined constants.

    1 lbf = 4.4482216152605 N and 1 ft = 0.3048 m are both DEFINED, so one
    pound per square foot is exactly 47.880259 Pa and the two clause values
    follow to as many figures as anyone wants.  That is the strongest kind
    of independent check there is - it cannot be wrong unless arithmetic is.
    """
    psf_to_pa = 4.4482216152605 / (0.3048 ** 2)
    assert psf_to_pa == pytest.approx(47.880259, abs=1e-6)
    assert MIN_WALL_PRESSURE == pytest.approx(16.0 * psf_to_pa / 1000.0, rel=1e-12)
    assert MIN_ROOF_PRESSURE == pytest.approx(8.0 * psf_to_pa / 1000.0, rel=1e-12)
    assert MIN_WALL_PRESSURE == pytest.approx(0.766084, abs=1e-6)
    assert MIN_ROOF_PRESSURE == pytest.approx(0.383042, abs=1e-6)
    assert MIN_WALL_PRESSURE == pytest.approx(2.0 * MIN_ROOF_PRESSURE, rel=1e-12)


def test_asces_printed_SI_values_are_soft_conversions_and_one_is_low() -> None:
    """The reason this module does not use 0.77 and 0.38.

    ASCE prints "16 lb/ft2 (0.77 kN/m2)" and "8 lb/ft2 (0.38 kN/m2)".  The
    first rounds UP by 0.5% and the second rounds DOWN by 0.8%.  Rounding a
    MINIMUM down is the wrong direction, so the exact values are used.

    Pinned as a fact about the Standard, not as a preference: if someone
    later switches these constants to ASCE's printed pair, this test says
    what they are giving up.
    """
    assert 0.77 > MIN_WALL_PRESSURE
    assert 0.38 < MIN_ROOF_PRESSURE
    assert (0.38 - MIN_ROOF_PRESSURE) / MIN_ROOF_PRESSURE == pytest.approx(
        -0.0079, abs=0.001
    )


@pytest.mark.parametrize(
    "end_zone,wall_key,roof_key",
    [
        (False, "interior_zone_wall", "interior_zone_roof"),
        (True, "end_zone_wall", "end_zone_roof"),
    ],
)
def test_net_horizontal_pressures_match_struware(
    struware, end_zone, wall_key, roof_key
) -> None:
    """Struware's four printed "simple diaphragm" pressures, reproduced.

    These are the exact quantity Sect. 28.3.4's minimum is compared
    against - windward zone minus leeward zone, internal pressure
    cancelling - so verifying the check means verifying these.  All four
    land within 0.1 psf of the printed values, the residual being
    Struware's own rounding of qh to 35.9 psf.
    """
    w = _struware_wind(struware)
    printed = struware["printed_outputs"][
        "horizontal_mwfrs_simple_diaphragm_psf"
    ]
    wall, roof = net_horizontal_pressures(w, end_zone=end_zone)
    assert wall * 1000.0 / PSF == pytest.approx(printed[wall_key], abs=0.1)
    assert roof * 1000.0 / PSF == pytest.approx(printed[roof_key], abs=0.1)


def test_the_internal_pressure_cancels_out_of_the_net_pressures(
    struware,
) -> None:
    """Which is why the minimum is checked once, not four times.

    ``GCpi`` pushes on the windward and leeward faces at the same instant,
    so it vanishes from the difference.  Asserted by computing the net
    pressures from each internal-pressure case and requiring they agree
    exactly - if they ever stopped agreeing, the subtraction would be
    picking up something it should not.
    """
    w = _struware_wind(struware)
    wall, roof = net_horizontal_pressures(w)
    for case in ("W_A_pi_pos", "W_A_pi_neg"):
        p = w.pressures[case]
        assert p["1"] - p["4"] == pytest.approx(wall, rel=1e-12)
        assert p["2"] - p["3"] == pytest.approx(roof, rel=1e-12)


def test_the_minimum_does_NOT_govern_struwares_building(struware) -> None:
    """The negative control, and the reason this flag is worth having.

    At 24.7 psf on the wall and 11.5 on the roof, Struware's building is
    comfortably above the 16 and 8 psf floors, and the check must say so.

    A minimum-load routine that returned "governs" unconditionally would
    satisfy every confirmation test that only ever looked at a building
    where the minimum DOES bite.  This is the test it fails.
    """
    w = _struware_wind(struware)
    inp = struware["inputs"]
    g = _geometry_m(
        width=inp["least_width_ft"] * FT,
        length=inp["building_length_ft"] * FT,
        mean_roof_height=inp["mean_roof_height_ft"] * FT,
        slope=math.tan(math.radians(inp["roof_angle_deg"])),
    )
    m = minimum_design_wind_load(g, w)
    assert m.applicable
    assert not m.governs, (
        f"computed {m.F_computed:.1f} kN against minimum {m.F_minimum:.1f} kN"
    )
    assert m.scale == 1.0
    wall_psf = m.p_wall * 1000.0 / PSF
    assert wall_psf > 16.0
    assert abs(m.p_roof) * 1000.0 / PSF > 8.0


def test_struware_prints_the_rule_this_module_implements(struware) -> None:
    """The clause text, in the reference's own words.

    Recorded so the implementation rests on a stated rule rather than on a
    memory of one: 16 psf on the wall area PLUS 8 psf on the vertical
    projection of the roof.
    """
    printed = struware["printed_outputs"][
        "minimum_load_note_printed_by_struware"
    ]
    assert "16 psf" in printed and "8 psf" in printed
    assert "wall area" in printed
    assert "vertical projection of the roof" in printed


def test_the_scale_factor_does_not_depend_on_building_length() -> None:
    """Both forces are proportional to L, so the ratio is not.

    That is what lets the whole-building check be applied to a single
    frame line without re-deriving it per tributary width - and it is
    worth pinning, because a length that failed to cancel would make the
    answer depend on how many bays someone typed.
    """
    site = ProjectSite(
        basic_wind_speed=45.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
    )
    short = PEBGeometry.from_m(
        span_widths=[30.0], bay_spacings=[6.0] * 4, eave_height=7.0,
        roof_slope=0.10, purlin_spacing=1.5, girt_spacing=2.0,
    )
    long = PEBGeometry.from_m(
        span_widths=[30.0], bay_spacings=[6.0] * 20, eave_height=7.0,
        roof_slope=0.10, purlin_spacing=1.5, girt_spacing=2.0,
    )
    s = minimum_design_wind_load(short, envelope_pressures(short, site))
    t = minimum_design_wind_load(long, envelope_pressures(long, site))
    assert s.scale == pytest.approx(t.scale, rel=1e-12)
    assert s.governs and t.governs
    assert t.F_computed > s.F_computed        # but the forces do differ


def test_the_projected_roof_area_is_a_rectangle_not_the_gable_triangle(
) -> None:
    """The projection that is easy to get wrong, and costs a factor of two.

    For TRANSVERSE wind the plane normal to the wind runs along the
    building, and both roof planes project onto the same strip between eave
    and ridge - so the projected roof area is L x rise.  The gable triangle
    (B x rise / 2) is the LONGITUDINAL projection and belongs to the
    endwall check.
    """
    site = ProjectSite(
        basic_wind_speed=45.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
    )
    g = PEBGeometry.from_m(
        span_widths=[30.0], bay_spacings=[6.0] * 5, eave_height=7.0,
        roof_slope=0.10, purlin_spacing=1.5, girt_spacing=2.0,
    )
    m = minimum_design_wind_load(g, envelope_pressures(g, site))
    L = g.length / 1000.0
    rise = g.ridge_rise / 1000.0
    assert m.A_roof == pytest.approx(L * rise)
    assert m.A_wall == pytest.approx(L * g.eave_height / 1000.0)
    assert m.A_roof != pytest.approx(0.5 * (g.width / 1000.0) * rise)


# =====================================================================
# 7.  The gate
# =====================================================================

def test_every_flag_is_closed_and_backed_by_a_record() -> None:
    """All six, as of 2026-09-02.

    If this fails because a flag was OPENED, something regressed.  The
    provenance for each is written in the VERIFIED comment block in
    ``loads/wind.py``, and the records it names are asserted present here.
    """
    assert all(VERIFIED.values()), VERIFIED
    assert (FIXTURES / "struware_asce716_envelope_theta1p2.json").exists()
    # The source Struware PDF is kept privately, not in the public repo;
    # the JSON fixture above is the published record of that run.


def test_flags_are_booleans() -> None:
    """A truthy string would pass the gate and read as verified."""
    for k, v in VERIFIED.items():
        assert isinstance(v, bool), f"{k} = {v!r} is not a bool"


def test_the_fixture_says_which_flags_it_can_and_cannot_close(
    struware,
) -> None:
    """An honest record names its own limits.

    The Struware run cannot close ``angle_interpolation`` - it sits at a
    single angle inside the flat band.  Saying so in the fixture is what
    stops a later reader citing it for something it never showed.
    """
    assert "flags_this_can_close" in struware
    assert "flags_this_cannot_close" in struware
    cannot = " ".join(struware["flags_this_cannot_close"])
    assert "angle_interpolation" in cannot
