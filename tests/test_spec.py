"""Tests for bimsteelauto.spec — the layer contract."""

import json
import math

import pytest

from bimsteelauto.spec import (
    CAPACITY_FRACTION,
    SCHEMA_VERSION,
    ConnectionDesign,
    ConnectionType,
    DesignResult,
    DesignSettings,
    EnclosureClassification,
    EndwallType,
    ExposureCategory,
    FrameType,
    LoadCase,
    LoadCombination,
    LoadSummary,
    Materials,
    MemberCheck,
    MemberDesign,
    MemberForces,
    MemberType,
    PEBGeometry,
    ProjectSite,
    ProjectSpec,
    RiskCategory,
    RoofType,
    SpecError,
    SupportReaction,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def site():
    return ProjectSite(
        basic_wind_speed=45.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
        Kzt=1.0,
    )


@pytest.fixture
def geometry():
    # 30 m clear span, 5 bays of 6 m, 7 m eave, 1:10 roof
    return PEBGeometry.from_m(
        span_widths=[30.0],
        bay_spacings=[6.0] * 5,
        eave_height=7.0,
        roof_slope=0.10,
        purlin_spacing=1.5,
        girt_spacing=2.0,
    )


@pytest.fixture
def materials():
    return Materials(
        Fy_builtup=345.0,
        Fu_builtup=450.0,
        Fy_cold_formed=345.0,
        Fu_cold_formed=450.0,
        bolt_grade="A325",
        anchor_grade="F1554 Gr.36",
        fc_concrete=30.0,
    )


@pytest.fixture
def spec(site, geometry, materials):
    return ProjectSpec(
        project_name="Test PEB",
        job_number="JOB-001",
        site=site,
        geometry=geometry,
        materials=materials,
    )


@pytest.fixture
def passing_member():
    return MemberDesign(
        mark="R1",
        member_type=MemberType.RAFTER,
        section_id="BU-600/400x200x8/10",
        length=15_075.0,
        governing_combination="1.2D + 1.0W + 0.5Lr",
        forces=MemberForces(P=120.0, Vy=85.0, Mz=310.0, station=0.0),
        checks=(
            MemberCheck("Flexure LTB", "AISC 360-16 Eq. F4-2", 310.0, 420.0, 0.738, "kN.m"),
            MemberCheck("Shear", "AISC 360-16 Eq. G2-1", 85.0, 260.0, 0.327, "kN"),
        ),
        weight=1120.0,
    )


# ---------------------------------------------------------------------------
# ProjectSite
# ---------------------------------------------------------------------------


def test_site_kd_and_gcpi(site):
    assert site.Kd == 0.85
    assert site.GCpi == (0.18, -0.18)


def test_site_gcpi_partially_enclosed():
    s = ProjectSite(
        basic_wind_speed=45.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.PARTIALLY_ENCLOSED,
        risk_category=RiskCategory.II,
    )
    assert s.GCpi == (0.55, -0.55)


def test_site_gcpi_open_is_zero():
    s = ProjectSite(
        basic_wind_speed=45.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.OPEN,
        risk_category=RiskCategory.II,
    )
    assert s.GCpi == (0.0, 0.0)


def test_site_rejects_mph_looking_speed():
    s = ProjectSite(
        basic_wind_speed=115.0,  # clearly mph
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
    )
    assert any("mph" in p for p in s.problems())


def test_site_rejects_kzt_below_one():
    s = ProjectSite(
        basic_wind_speed=45.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
        Kzt=0.8,
    )
    assert any("Kzt" in p for p in s.problems())


def test_site_has_exactly_five_user_inputs():
    """The five wind variables and nothing else."""
    from dataclasses import fields as dc_fields

    names = {f.name for f in dc_fields(ProjectSite)}
    assert names == {
        "basic_wind_speed",
        "exposure_category",
        "enclosure_classification",
        "risk_category",
        "Kzt",
    }


# ---------------------------------------------------------------------------
# PEBGeometry
# ---------------------------------------------------------------------------


def test_from_m_converts_to_mm(geometry):
    assert geometry.width == pytest.approx(30_000.0)
    assert geometry.length == pytest.approx(30_000.0)
    assert geometry.eave_height == pytest.approx(7_000.0)
    assert geometry.purlin_spacing == pytest.approx(1_500.0)


def test_bay_counts(geometry):
    assert geometry.n_bays == 5
    assert geometry.n_frames == 6


def test_ridge_geometry(geometry):
    # gable, 30 m span -> 15 m half width, 1:10 -> 1.5 m rise
    assert geometry.ridge_rise == pytest.approx(1_500.0)
    assert geometry.ridge_height == pytest.approx(8_500.0)
    assert geometry.slope_deg == pytest.approx(math.degrees(math.atan(0.10)))


def test_mean_roof_height_uses_eave_below_10deg(geometry):
    assert geometry.slope_deg < 10.0
    assert geometry.mean_roof_height == pytest.approx(geometry.eave_height)


def test_mean_roof_height_averages_above_10deg():
    g = PEBGeometry.from_m(
        span_widths=[30.0],
        bay_spacings=[6.0] * 5,
        eave_height=7.0,
        roof_slope=0.30,  # 16.7 deg
        purlin_spacing=1.5,
        girt_spacing=2.0,
    )
    assert g.slope_deg > 10.0
    assert g.mean_roof_height == pytest.approx(0.5 * (g.eave_height + g.ridge_height))


def test_rafter_length(geometry):
    assert geometry.rafter_length == pytest.approx(math.hypot(15_000.0, 1_500.0))


def test_monoslope_uses_full_width():
    g = PEBGeometry.from_m(
        span_widths=[8.0],
        bay_spacings=[6.0] * 3,
        eave_height=4.0,
        roof_slope=0.10,
        purlin_spacing=1.5,
        girt_spacing=2.0,
        roof_type=RoofType.MONOSLOPE,
    )
    assert g.ridge_rise == pytest.approx(800.0)


def test_tributary_widths_uniform(geometry):
    t = geometry.tributary_widths()
    assert len(t) == geometry.n_frames
    assert t[0] == pytest.approx(3_000.0)
    assert t[-1] == pytest.approx(3_000.0)
    assert all(v == pytest.approx(6_000.0) for v in t[1:-1])


def test_tributary_widths_sum_to_length(geometry):
    assert sum(geometry.tributary_widths()) == pytest.approx(geometry.length)


def test_tributary_widths_non_uniform_not_averaged():
    """Non-uniform bay spacing was a documented v6 defect — hold the line."""
    g = PEBGeometry.from_m(
        span_widths=[24.0],
        bay_spacings=[4.5, 7.5, 7.5, 6.0],
        eave_height=6.0,
        roof_slope=0.10,
        purlin_spacing=1.5,
        girt_spacing=2.0,
    )
    t = g.tributary_widths()
    assert t == pytest.approx((2_250.0, 6_000.0, 7_500.0, 6_750.0, 3_000.0))
    assert sum(t) == pytest.approx(g.length)


def test_multi_span_width_is_sum():
    g = PEBGeometry.from_m(
        span_widths=[24.5, 24.5],
        bay_spacings=[6.0] * 4,
        eave_height=8.0,
        roof_slope=0.10,
        purlin_spacing=1.5,
        girt_spacing=2.0,
        frame_type=FrameType.MULTI_SPAN,
    )
    assert g.width == pytest.approx(49_000.0)
    assert g.problems() == []


def test_clear_span_with_two_spans_is_rejected():
    g = PEBGeometry.from_m(
        span_widths=[20.0, 20.0],
        bay_spacings=[6.0],
        eave_height=6.0,
        roof_slope=0.10,
        purlin_spacing=1.5,
        girt_spacing=2.0,
        frame_type=FrameType.CLEAR_SPAN,
    )
    assert any("clear_span" in p for p in g.problems())


def test_negative_geometry_rejected():
    g = PEBGeometry(
        span_widths=(-1.0,),
        bay_spacings=(6000.0,),
        eave_height=0.0,
        roof_slope=0.0,
        purlin_spacing=-5.0,
        girt_spacing=0.0,
    )
    p = g.problems()
    assert len(p) >= 4


# ---------------------------------------------------------------------------
# Materials / settings
# ---------------------------------------------------------------------------


def test_materials_reject_fu_below_fy():
    m = Materials(
        Fy_builtup=450.0,
        Fu_builtup=345.0,
        Fy_cold_formed=345.0,
        Fu_cold_formed=450.0,
        bolt_grade="A325",
        anchor_grade="F1554 Gr.36",
        fc_concrete=30.0,
    )
    assert any("must exceed" in p for p in m.problems())


def test_materials_reject_blank_grades():
    m = Materials(
        Fy_builtup=345.0,
        Fu_builtup=450.0,
        Fy_cold_formed=345.0,
        Fu_cold_formed=450.0,
        bolt_grade="   ",
        anchor_grade="",
        fc_concrete=30.0,
    )
    p = m.problems()
    assert any("bolt_grade" in x for x in p)
    assert any("anchor_grade" in x for x in p)


def test_default_capacity_fraction_is_half():
    assert DesignSettings().connection_capacity_fraction == CAPACITY_FRACTION == 0.50


def test_settings_reject_capacity_fraction_above_one():
    s = DesignSettings(connection_capacity_fraction=1.5)
    assert s.problems()


# ---------------------------------------------------------------------------
# ProjectSpec
# ---------------------------------------------------------------------------


def test_valid_spec_has_no_problems(spec):
    assert spec.problems() == []
    assert spec.is_valid
    assert spec.validate() is spec


def test_blank_identity_fields_rejected(site, geometry, materials):
    s = ProjectSpec(
        project_name="  ",
        job_number="",
        site=site,
        geometry=geometry,
        materials=materials,
    )
    p = s.problems()
    assert any("project_name" in x for x in p)
    assert any("job_number" in x for x in p)


def test_validate_reports_every_problem_at_once(site, geometry, materials):
    bad_site = ProjectSite(
        basic_wind_speed=150.0,
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
        Kzt=0.5,
    )
    s = ProjectSpec(
        project_name="",
        job_number="",
        site=bad_site,
        geometry=geometry,
        materials=materials,
    )
    with pytest.raises(SpecError) as exc:
        s.validate()
    assert len(exc.value.problems) >= 4


def test_spec_carries_schema_version(spec):
    assert spec.schema_version == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# JSON round trip
# ---------------------------------------------------------------------------


def test_spec_json_round_trip(spec):
    back = ProjectSpec.from_json(spec.to_json())
    assert back == spec


def test_spec_json_is_plain_types(spec):
    d = json.loads(spec.to_json())
    assert d["site"]["exposure_category"] == "C"
    assert d["geometry"]["frame_type"] == "clear_span"
    assert isinstance(d["geometry"]["bay_spacings"], list)


def test_result_json_round_trip(spec, passing_member):
    r = DesignResult(
        spec=spec,
        loads=LoadSummary(
            cases=(LoadCase("D", "dead", "self weight"),),
            combinations=(LoadCombination("1.2D+1.6Lr", {"D": 1.2, "Lr": 1.6}),),
            frame_line_loads={"F2/W_A_pos/zone2": -3.14},
            qh=1.02,
            end_zone_a=3_000.0,
        ),
        members=(passing_member,),
        connections=(
            ConnectionDesign(
                mark="EC1",
                connection_type=ConnectionType.EAVE_END_PLATE,
                member_marks=("C1", "R1"),
                design_force_basis="capacity_fraction",
                utilisation=0.81,
                verified=True,
                detail={"Yp": 1234.5},
            ),
        ),
        reactions=(SupportReaction("N1", "1.2D+1.0W", Fx=-42.0, Fz=88.0),),
        engine_version="0.8.0",
    )
    back = DesignResult.from_json(r.to_json())
    assert back == r
    assert back.members[0].checks[0].reference == "AISC 360-16 Eq. F4-2"
    assert back.loads.frame_line_loads["F2/W_A_pos/zone2"] == pytest.approx(-3.14)
    assert back.connections[0].connection_type is ConnectionType.EAVE_END_PLATE


def test_generated_at_is_stamped(spec):
    r = DesignResult(spec=spec)
    assert r.generated_at


# ---------------------------------------------------------------------------
# Roll-ups and issue gating
# ---------------------------------------------------------------------------


def test_member_utilisation_is_max_check(passing_member):
    assert passing_member.utilisation == pytest.approx(0.738)
    assert passing_member.governing_check.name == "Flexure LTB"
    assert passing_member.passed


def test_member_with_no_checks_has_zero_utilisation():
    m = MemberDesign(
        mark="X",
        member_type=MemberType.PURLIN,
        section_id="C200x60x20x2.5",
        length=6_000.0,
        governing_combination="-",
        forces=MemberForces(),
    )
    assert m.utilisation == 0.0
    assert m.passed  # vacuously — the issue gate catches empty designs


def test_failing_member_blocks_issue(spec, passing_member):
    bad = MemberDesign(
        mark="C1",
        member_type=MemberType.COLUMN,
        section_id="BU-800/400x200x8/12",
        length=7_000.0,
        governing_combination="1.2D+1.0W",
        forces=MemberForces(P=400.0),
        checks=(MemberCheck("Interaction", "AISC 360-16 Eq. H1-1a", 1.0, 1.0, 1.24),),
    )
    r = DesignResult(spec=spec, members=(passing_member, bad))
    assert not r.passed
    assert not r.ready_for_issue()
    assert any("C1" in b for b in r.issue_blockers())
    assert r.max_member_utilisation == pytest.approx(1.24)


def test_unverified_flag_blocks_issue(spec, passing_member):
    r = DesignResult(spec=spec, members=(passing_member,), unverified=("endplate_8ES",))
    assert r.passed  # numbers are fine
    assert not r.ready_for_issue()  # but it is NOT FOR ISSUE
    assert any("endplate_8ES" in b for b in r.issue_blockers())


def test_empty_result_is_not_ready(spec):
    r = DesignResult(spec=spec)
    assert not r.ready_for_issue()
    assert any("no members" in b for b in r.issue_blockers())


def test_invalid_spec_blocks_issue(site, geometry, materials, passing_member):
    bad_spec = ProjectSpec(
        project_name="",
        job_number="J",
        site=site,
        geometry=geometry,
        materials=materials,
    )
    r = DesignResult(spec=bad_spec, members=(passing_member,))
    assert not r.ready_for_issue()
    assert any("project_name" in b for b in r.issue_blockers())


def test_clean_result_is_ready(spec, passing_member):
    r = DesignResult(spec=spec, members=(passing_member,))
    assert r.ready_for_issue()
    assert r.issue_blockers() == []


def test_total_weight(spec, passing_member):
    r = DesignResult(spec=spec, members=(passing_member, passing_member))
    assert r.total_weight == pytest.approx(2_240.0)


def test_connection_utilisation_counts_toward_max(spec, passing_member):
    c = ConnectionDesign(
        mark="AP1",
        connection_type=ConnectionType.APEX_END_PLATE,
        member_marks=("R1", "R2"),
        design_force_basis="analysis",
        utilisation=0.95,
        verified=True,
    )
    r = DesignResult(spec=spec, members=(passing_member,), connections=(c,))
    assert r.max_utilisation == pytest.approx(0.95)


def test_errors_block_issue(spec, passing_member):
    r = DesignResult(spec=spec, members=(passing_member,), errors=("solver did not converge",))
    assert not r.passed
    assert any("solver" in b for b in r.issue_blockers())


# ---------------------------------------------------------------------------
# ConnectionDesign.from_result — the bridge to bimsteelauto_connections
# ---------------------------------------------------------------------------


class _FakeLimitState:
    def __init__(self, name, capacity, demand, clause="", unit="kN"):
        self.name = name
        self.capacity = capacity
        self.demand = demand
        self.clause = clause
        self.unit = unit

    @property
    def utilization(self):
        return self.demand / self.capacity if self.capacity > 0 else float("inf")

    @property
    def passed(self):
        return self.utilization <= 1.0


class _FakeCriterion:
    def __init__(self, value):
        self.value = value


class _FakeForce:
    def __init__(self, label, value, analysis, capacity_based, fraction, criterion):
        self.label = label
        self.value = value
        self.analysis = analysis
        self.capacity_based = capacity_based
        self.fraction = fraction
        self.criterion = _FakeCriterion(criterion)
        self.unit = "kNm"


class _FakeConnectionResult:
    """Shaped exactly like bimsteelauto_connections.core.ConnectionResult."""

    def __init__(self, verified=True, states=None, forces=()):
        self.mark = "EC1"
        self.kind = "eave"
        self.citation = "AISC Design Guide 4"
        self.limit_states = tuple(
            states
            or (
                _FakeLimitState("End plate flexure", 100.0, 81.0, "DG4 Eq. 3-11", "kNm"),
                _FakeLimitState("Bolt tension", 400.0, 120.0, "AISC J3-1"),
            )
        )
        self.forces = tuple(forces)
        self.geometry = {"tp": 20.0}
        self.selections = {"config": "4E"}
        self.warnings = ()
        self.verified = verified

    @property
    def governing(self):
        return max(self.limit_states, key=lambda ls: ls.utilization, default=None)

    @property
    def utilization(self):
        g = self.governing
        return g.utilization if g else 0.0


def test_from_result_flattens_limit_states():
    cd = ConnectionDesign.from_result(
        _FakeConnectionResult(), ConnectionType.EAVE_END_PLATE, ["C1", "R1"]
    )
    assert cd.mark == "EC1"
    assert cd.utilisation == pytest.approx(0.81)
    assert cd.verified is True
    assert cd.member_marks == ("C1", "R1")
    assert len(cd.detail["limit_states"]) == 2
    assert cd.detail["limit_states"][0]["clause"] == "DG4 Eq. 3-11"
    assert cd.detail["selections"]["config"] == "4E"


def test_from_result_detects_capacity_basis():
    f = _FakeForce("Mu", 250.0, 180.0, 250.0, 0.50, "% of member capacity")
    cd = ConnectionDesign.from_result(
        _FakeConnectionResult(forces=(f,)), ConnectionType.APEX_END_PLATE, ["R1", "R2"]
    )
    assert cd.design_force_basis == "capacity_fraction"
    assert cd.detail["forces"][0]["fraction"] == pytest.approx(0.50)


def test_from_result_detects_analysis_basis():
    f = _FakeForce("Mu", 180.0, 180.0, 125.0, 0.50, "analysis force")
    cd = ConnectionDesign.from_result(
        _FakeConnectionResult(forces=(f,)), ConnectionType.APEX_END_PLATE, ["R1", "R2"]
    )
    assert cd.design_force_basis == "analysis"


def test_from_result_survives_json_round_trip(spec, passing_member):
    cd = ConnectionDesign.from_result(
        _FakeConnectionResult(), ConnectionType.EAVE_END_PLATE, ["C1", "R1"]
    )
    r = DesignResult(spec=spec, members=(passing_member,), connections=(cd,))
    back = DesignResult.from_json(r.to_json())
    assert back.connections[0].detail["limit_states"][1]["name"] == "Bolt tension"


def test_unverified_connection_propagates_to_issue_gate(spec, passing_member):
    cd = ConnectionDesign.from_result(
        _FakeConnectionResult(verified=False), ConnectionType.EAVE_END_PLATE, ["C1", "R1"]
    )
    unverified = () if cd.verified else (f"connection {cd.mark}",)
    r = DesignResult(
        spec=spec, members=(passing_member,), connections=(cd,), unverified=unverified
    )
    assert not r.ready_for_issue()


# ---------------------------------------------------------------------------
# Layer discipline
# ---------------------------------------------------------------------------


def test_spec_module_imports_nothing_heavy():
    """Layer 1 must import in FreeCAD's Python and in a bare env alike."""
    import ast
    import pathlib

    import bimsteelauto.spec as m

    src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])

    banned = {"FreeCAD", "FreeCADGui", "Part", "numpy", "scipy", "PySide6", "PyQt6", "matplotlib"}
    assert not (roots & banned), f"spec.py must not import {roots & banned}"
