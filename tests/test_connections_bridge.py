"""Tests for bimsteelauto.connections - the analysis-to-connection wire.

This module performs no engineering, so there is no second tool to compare
it against.  What it can get wrong is force SELECTION and ROUTING, and those
are checked against envelopes constructed by hand in the test, where the
right answer is known by inspection rather than by running the code.
"""

import math

import pytest

from bimsteelauto.analysis import (
    Envelope,
    FrameAnalysis,
    FrameSections,
    LoadDirection,
    MemberSectionSpec,
    StationForces,
    build_layout,
)
from bimsteelauto.connections import (
    COVERAGE_GAPS,
    VERIFIED,
    ConnectionBridgeError,
    ConnectionDetailing,
    JointForces,
    design_frame_connections,
    joint_forces,
    suggest_endplate_geometry,
)
from bimsteelauto.sections import WeldedI
from bimsteelauto.spec import (
    ConnectionType,
    Materials,
    MemberType,
    PEBGeometry,
)

pytest.importorskip("Pynite", reason="PyNiteFEA not installed")
pytest.importorskip("pyaisc360", reason="pyaisc360 not on sys.path")
conn = pytest.importorskip(
    "bimsteelauto_connections", reason="the connection package is not importable"
)


MATERIALS = Materials(
    Fy_builtup=345.0,
    Fu_builtup=450.0,
    Fy_cold_formed=345.0,
    Fu_cold_formed=450.0,
    bolt_grade="A325",
    anchor_grade="F1554-36",
    fc_concrete=30.0,
)

DETAILING = ConnectionDetailing(
    bolt_diameter=24.0,
    bolt_grade="A325",
    plate_grade="S355JR",
    anchor_grade="F1554-36",
    base_plate_N=500.0,
    base_plate_B=400.0,
    anchor_diameter=24.0,
    anchor_embedment=400.0,
    pedestal_depth=800.0,
    fc_prime=30.0,
)


def _geometry():
    return PEBGeometry.from_m(
        span_widths=[30.0],
        bay_spacings=[6.0] * 5,
        eave_height=7.0,
        roof_slope=0.10,
        purlin_spacing=1.5,
        girt_spacing=2.0,
    )


def _sections():
    return FrameSections(
        column=MemberSectionSpec(
            WeldedI.symmetric(d=400, tw=8, bf=250, tf=16),
            WeldedI.symmetric(d=800, tw=8, bf=250, tf=16),
        ),
        rafter=MemberSectionSpec(
            WeldedI.symmetric(d=800, tw=8, bf=250, tf=16),
            WeldedI.symmetric(d=400, tw=8, bf=250, tf=16),
        ),
    )


@pytest.fixture(scope="module")
def solved():
    layout = build_layout(_geometry(), _sections())
    fa = FrameAnalysis(layout)
    for m in layout.members:
        if m.member_type is MemberType.RAFTER:
            fa.add_line_load("D", m.mark, 6.0, LoadDirection.GLOBAL_Y_PROJECTED)
    fa.add_line_load("W", "C1", 3.0, LoadDirection.GLOBAL_X)
    fa.add_combination("1.2D+1.0W", {"D": 1.2, "W": 1.0})
    fa.add_combination("0.9D+1.0W", {"D": 0.9, "W": 1.0})
    return layout, fa.solve(p_delta=True)


@pytest.fixture(scope="module")
def designs(solved):
    layout, env = solved
    return design_frame_connections(
        layout, env, MATERIALS, DETAILING, strict=False
    )


# ---------------------------------------------------------------------------
# joint force extraction, against a hand-built envelope
# ---------------------------------------------------------------------------


def _fake_envelope(stations):
    return Envelope(
        stations=stations,
        reactions={},
        displacements={},
        combos=tuple(next(iter(stations.values())).keys()),
        p_delta=False,
    )


class _FakeMember:
    def __init__(self, mark, length):
        self.mark = mark
        self.length = length


def test_joint_takes_the_larger_of_the_two_member_ends():
    """A column and a rafter meet at the eave.  The bigger one wins."""
    env = _fake_envelope(
        {
            "C1": {"c1": (StationForces(7000.0, 100.0, 40.0, 250.0),)},
            "R1": {"c1": (StationForces(0.0, 90.0, 55.0, 310.0),)},
        }
    )
    jf = joint_forces(
        env, "E1", [(_FakeMember("C1", 7000.0), 7000.0), (_FakeMember("R1", 15000.0), 0.0)]
    )
    assert jf.M == pytest.approx(310.0)     # the rafter
    assert jf.V == pytest.approx(55.0)      # the rafter
    assert jf.P == pytest.approx(100.0)     # the column


def test_joint_scans_every_combination():
    env = _fake_envelope(
        {
            "C1": {
                "gravity": {StationForces(7000.0, 200.0, 30.0, 180.0)},
                "wind": (StationForces(7000.0, 40.0, 70.0, 420.0),),
            }
        }
    )
    env.stations["C1"]["gravity"] = (StationForces(7000.0, 200.0, 30.0, 180.0),)
    jf = joint_forces(env, "E1", [(_FakeMember("C1", 7000.0), 7000.0)])
    assert jf.M == pytest.approx(420.0)
    assert jf.combos["M"] == "wind"
    assert jf.P == pytest.approx(200.0)
    assert jf.combos["P"] == "gravity"


def test_joint_moment_is_taken_on_magnitude_not_sign():
    """A hogging eave moment is as real as a sagging one."""
    env = _fake_envelope(
        {"C1": {"c": (StationForces(7000.0, 10.0, 5.0, -480.0),)}}
    )
    jf = joint_forces(env, "E1", [(_FakeMember("C1", 7000.0), 7000.0)])
    assert jf.M == pytest.approx(480.0)


def test_axial_keeps_its_sign_so_uplift_survives():
    """abs() here would turn a base in uplift into a base in compression."""
    env = _fake_envelope(
        {"C1": {"c": (StationForces(0.0, -95.0, 5.0, 20.0),)}}
    )
    jf = joint_forces(env, "B1", [(_FakeMember("C1", 7000.0), 0.0)])
    assert jf.P == pytest.approx(-95.0)
    assert jf.uplift is True


def test_joint_records_which_members_it_read():
    env = _fake_envelope(
        {
            "C1": {"c": (StationForces(7000.0, 1.0, 1.0, 1.0),)},
            "R1": {"c": (StationForces(0.0, 1.0, 1.0, 1.0),)},
        }
    )
    jf = joint_forces(
        env, "E1", [(_FakeMember("C1", 7000.0), 7000.0), (_FakeMember("R1", 15000.0), 0.0)]
    )
    assert jf.sources == (("C1", 7000.0), ("R1", 0.0))


def test_missing_member_results_are_skipped_not_crashed():
    env = _fake_envelope({"C1": {"c": (StationForces(0.0, 1.0, 1.0, 1.0),)}})
    jf = joint_forces(
        env, "E1", [(_FakeMember("C1", 7000.0), 0.0), (_FakeMember("GHOST", 1.0), 0.0)]
    )
    assert jf.M == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# the connections that come out
# ---------------------------------------------------------------------------


def test_every_joint_gets_a_connection(designs):
    marks = [c.mark for c in designs]
    assert marks == ["EC1", "EC2", "AP1", "BP1", "BP2"]


def test_connection_types_are_right(designs):
    by_mark = {c.mark: c.connection_type for c in designs}
    assert by_mark["EC1"] is ConnectionType.EAVE_END_PLATE
    assert by_mark["AP1"] is ConnectionType.APEX_END_PLATE
    assert by_mark["BP1"] is ConnectionType.BASE_PLATE


def test_each_connection_names_its_members(designs):
    by_mark = {c.mark: c.member_marks for c in designs}
    assert set(by_mark["EC1"]) == {"C1", "R1"}
    assert set(by_mark["AP1"]) == {"R1", "R2"}
    assert by_mark["BP1"] == ("C1",)


def test_every_connection_has_limit_states(designs):
    """Regression: the first converter found none and reported U = 0.000.

    A connection sheet that says PASS while checking nothing is the worst
    possible output of this pipeline, so it gets its own test.
    """
    for c in designs:
        states = c.detail["limit_states"]
        assert states, f"{c.mark} has no limit states"
        assert all(s["capacity"] > 0 for s in states), c.mark
        assert c.utilisation > 0.0, f"{c.mark} reported zero utilisation"


def test_every_limit_state_carries_a_clause(designs):
    for c in designs:
        for s in c.detail["limit_states"]:
            assert s["clause"], f"{c.mark}: {s['name']} has no clause"


def test_end_plates_check_both_strength_and_thickness(designs):
    ec1 = next(c for c in designs if c.mark == "EC1")
    names = [s["name"] for s in ec1.detail["limit_states"]]
    assert any("flexural" in n for n in names)
    assert any("thickness" in n for n in names)


def test_base_plate_covers_plate_and_anchors_together(designs):
    """One base connection, not two - so a good plate cannot hide a bad bolt."""
    bp1 = next(c for c in designs if c.mark == "BP1")
    names = " ".join(s["name"] for s in bp1.detail["limit_states"])
    assert "bearing" in names
    assert "thickness" in names
    assert "Anchor tension" in names
    assert "Anchor shear" in names
    assert "interaction" in names


def test_utilisation_is_the_worst_limit_state(designs):
    for c in designs:
        worst = max(s["utilisation"] for s in c.detail["limit_states"])
        assert c.utilisation == pytest.approx(worst, rel=1e-9)


def test_a_failing_anchor_is_reported_not_hidden(designs):
    """The default anchor layout fails in shear on this frame.

    That is the correct answer for a 150 mm edge distance, and the point of
    the test is that it SURFACES rather than being smoothed away.
    """
    bp = [c for c in designs if c.connection_type is ConnectionType.BASE_PLATE]
    assert any(not c.passed for c in bp)


# ---------------------------------------------------------------------------
# the capacity-fraction rule
# ---------------------------------------------------------------------------


def test_joint_detail_records_the_design_force_decision(designs):
    ec1 = next(c for c in designs if c.mark == "EC1")
    j = ec1.detail["joint"]
    assert j["capacity_fraction"] == conn.CAPACITY_FRACTION == 0.50
    assert j["member_capacity_Mc"] > 0.0
    assert j["design_moment"] >= j["M_analysis"]
    assert j["criterion"] in {"analysis force", "% of member capacity"}


def test_design_moment_is_the_larger_of_the_two(designs):
    for c in designs:
        j = c.detail.get("joint", {})
        if "design_moment" not in j:
            continue
        expected = max(j["M_analysis"], 0.50 * j["member_capacity_Mc"])
        assert j["design_moment"] == pytest.approx(expected, rel=1e-9)


def test_capacity_governs_when_the_analysis_force_is_small(solved):
    """Halve nothing, but design a joint whose analysis moment is tiny.

    With a light frame the 50% capacity rule must take over - that is what
    the rule is for, and if it never fires it is not implemented.
    """
    layout, _ = solved
    fa = FrameAnalysis(layout)
    for m in layout.members:
        if m.member_type is MemberType.RAFTER:
            fa.add_line_load("D", m.mark, 0.05, LoadDirection.GLOBAL_Y_PROJECTED)
    fa.add_combination("light", {"D": 1.0})
    env = fa.solve(p_delta=False)

    designs = design_frame_connections(
        layout, env, MATERIALS, DETAILING, strict=False
    )
    ec1 = next(c for c in designs if c.mark == "EC1")
    j = ec1.detail["joint"]
    assert j["criterion"] == "% of member capacity"
    assert j["design_moment"] == pytest.approx(0.50 * j["member_capacity_Mc"])
    assert ec1.design_force_basis == "capacity_fraction"


def test_base_uses_the_uplift_combination_not_the_compression_one(designs):
    bp1 = next(c for c in designs if c.mark == "BP1")
    j = bp1.detail["joint"]
    assert "combination_uplift" in j
    assert "combination_compression" in j
    assert j["Nua_uplift"] >= 0.0
    assert j["Pu_compression"] >= 0.0


# ---------------------------------------------------------------------------
# suggested geometry is labelled as such
# ---------------------------------------------------------------------------


def test_suggested_geometry_is_marked_as_a_suggestion(designs):
    ec1 = next(c for c in designs if c.mark == "EC1")
    note = ec1.detail["joint"]["geometry_note"]
    assert "SUGGESTED" in note
    assert "Not a detailed layout" in note


def test_the_suggestion_reaches_the_result_warnings(designs):
    ec1 = next(c for c in designs if c.mark == "EC1")
    assert any("SUGGESTED" in w for w in ec1.detail["warnings"])


def test_suggested_geometry_follows_the_stated_proportions():
    section = WeldedI.symmetric(d=600, tw=8, bf=250, tf=16)
    geom, note = suggest_endplate_geometry(section, DETAILING, config="4E")
    assert geom.bp == pytest.approx(300.0)          # 250 + 50, rounded to 10
    assert geom.g == pytest.approx(100.0)           # M24 <= 24
    assert geom.pfo == pytest.approx(40.0)          # max(40, 24 + 12)
    assert geom.de == pytest.approx(36.0)           # 1.5 x 24
    assert "M24" in note


def test_larger_bolts_widen_the_gauge():
    section = WeldedI.symmetric(d=600, tw=8, bf=250, tf=16)
    big = ConnectionDetailing(
        **{**DETAILING.__dict__, "bolt_diameter": 30.0}
    )
    geom, _ = suggest_endplate_geometry(section, big, config="4E")
    assert geom.g == pytest.approx(140.0)
    assert geom.pfo == pytest.approx(42.0)


def test_eight_es_adds_a_second_bolt_row():
    section = WeldedI.symmetric(d=900, tw=8, bf=250, tf=16)
    four, _ = suggest_endplate_geometry(section, DETAILING, config="4E")
    eight, _ = suggest_endplate_geometry(section, DETAILING, config="8ES")
    assert len(eight.h_rows) > len(four.h_rows)
    assert eight.pb is not None
    assert four.pb is None


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------


def test_all_bridge_relations_are_verified():
    unset = [k for k, v in VERIFIED.items() if not v]
    assert unset == [], f"unverified bridge relations: {unset}"


def test_coverage_gaps_are_declared():
    assert COVERAGE_GAPS
    assert any("Detailing geometry" in g for g in COVERAGE_GAPS)
