"""Tests for bimsteelauto.pipeline - the single entry point.

The pipeline performs no engineering of its own; every number it returns is
produced by a module that has its own verified tests.  What it can get wrong
is ORDER, ROUTING and the CONTRACT it offers Layer 2, and those are what is
tested here.

The load-bearing test in this file is
``test_the_adapter_can_rebuild_every_member_from_the_result_alone``.  It is
the one that keeps the architecture honest: if a DesignResult is not enough
to build geometry from, the FreeCAD adapter has to reach back into the
engine, and the layer boundary is gone.
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto.analysis import FrameSections, MemberSectionSpec
from bimsteelauto.connections import ConnectionDetailing
from bimsteelauto.design import CFShape, ColdFormedSection
from bimsteelauto.design.bracing import ROD_ROOT_AREA, BraceMember, BraceType
from bimsteelauto.loads import AreaLoad, DeadLoadBuildUp, Surface
from bimsteelauto.pipeline import (
    ENGINE_MODULES,
    BracingInputs,
    FrameInputs,
    PipelineError,
    SecondaryInputs,
    design_frame,
    open_verification_flags,
    run_frame,
)
from bimsteelauto.sections import TaperedMember, WeldedI, parse_mark
from bimsteelauto.spec import (
    SCHEMA_VERSION,
    BracingArrangement,
    DesignResult,
    DesignSettings,
    EnclosureClassification,
    ExposureCategory,
    Materials,
    MemberType,
    PEBGeometry,
    ProjectSite,
    ProjectSpec,
    RiskCategory,
)

pytest.importorskip("Pynite", reason="PyNiteFEA not installed")
pytest.importorskip("pyaisc360", reason="pyaisc360 not on sys.path")


C200 = ColdFormedSection(
    name="C200x60x20x2.5", shape=CFShape.C,
    t=2.5, H=200.0, B=60.0, Dlip=20.0, r=3.0,
    A=685.0, Ix=4.02e6, Sx=40_200.0,
    Se_top=34_500.0, Se_bot=34_500.0,
    Iy=0.30e6, Sy=7_000.0, J=1_430.0, Cw=2.1e9, ho=200.0,
)


@pytest.fixture(scope="module")
def spec() -> ProjectSpec:
    return ProjectSpec(
        project_name="Pipeline test frame",
        job_number="TEST-001",
        site=ProjectSite(
            basic_wind_speed=45.0,
            exposure_category=ExposureCategory.C,
            enclosure_classification=EnclosureClassification.ENCLOSED,
            risk_category=RiskCategory.II,
        ),
        geometry=PEBGeometry.from_m(
            span_widths=[30.0],
            bay_spacings=[6.0, 7.5, 7.5, 7.5, 6.0],
            eave_height=7.0, roof_slope=0.10,
            purlin_spacing=1.5, girt_spacing=2.0,
        ),
        materials=Materials(
            Fy_builtup=345.0, Fu_builtup=450.0,
            Fy_cold_formed=345.0, Fu_cold_formed=450.0,
            bolt_grade="A325", anchor_grade="F1554-36", fc_concrete=30.0,
        ),
        settings=DesignSettings(),
    )


def _inputs(**kw) -> FrameInputs:
    base = dict(
        sections=FrameSections(
            column=MemberSectionSpec(
                start=WeldedI.symmetric(d=450.0, tw=8.0, bf=250.0, tf=16.0),
                end=WeldedI.symmetric(d=850.0, tw=8.0, bf=250.0, tf=16.0),
            ),
            rafter=MemberSectionSpec(
                start=WeldedI.symmetric(d=850.0, tw=7.0, bf=250.0, tf=16.0),
                end=WeldedI.symmetric(d=450.0, tw=7.0, bf=250.0, tf=16.0),
            ),
        ),
        dead=DeadLoadBuildUp(
            sheeting=0.06, insulation=0.01, liner=0.04, purlins=0.08,
            services=0.10, collateral=0.15,
        ),
        wall_dead=AreaLoad(value=0.09, surface=Surface.SLOPING,
                           label="Wall cladding and girts"),
        secondary=SecondaryInputs(purlin_section=C200, girt_section=C200),
        bracing=BracingInputs(
            braced_bays=(1, 3),
            roof_brace=BraceMember(
                name="M20 rod", brace_type=BraceType.ROD,
                Ag=math.pi / 4.0 * 400.0, An_effective=ROD_ROOT_AREA[20.0],
                r_min=5.0, diameter=20.0,
            ),
            wall_brace=BraceMember(
                name="M24 rod", brace_type=BraceType.ROD,
                Ag=math.pi / 4.0 * 576.0, An_effective=ROD_ROOT_AREA[24.0],
                r_min=6.0, diameter=24.0,
            ),
            strut_section=WeldedI.symmetric(d=250.0, tw=6.0, bf=150.0, tf=8.0),
        ),
        detailing=ConnectionDetailing(
            bolt_diameter=24.0, bolt_grade="A325", plate_grade="S355JR",
            anchor_grade="F1554-36",
            base_plate_N=600.0, base_plate_B=450.0,
            anchor_diameter=30.0, anchor_embedment=450.0,
            anchor_spacing_x=350.0, anchor_spacing_y=350.0, anchor_edge=250.0,
            pedestal_depth=1200.0, fc_prime=30.0,
        ),
    )
    base.update(kw)
    return FrameInputs(**base)


@pytest.fixture(scope="module")
def run(spec):
    return design_frame(spec, _inputs(frame_index=2))


# ---------------------------------------------------------------------
# THE CONTRACT WITH LAYER 2
# ---------------------------------------------------------------------

def test_run_frame_returns_only_a_DesignResult(spec) -> None:
    """The adapter's whole input, and nothing beside it."""
    out = run_frame(spec, _inputs(frame_index=2))
    assert isinstance(out, DesignResult)


def test_the_adapter_can_rebuild_every_member_from_the_result_alone(
    run,
) -> None:
    """The architecture, asserted.

    ``DesignResult`` carries ``section_id`` (a canonical BU- mark) and
    ``length``.  ``parse_mark`` turns those back into the real
    ``TaperedMember``.  So Layer 2 can build geometry without reaching into
    the engine for the FrameSections it was given - which is what keeps the
    layer boundary a boundary rather than a suggestion.

    If this ever fails, do NOT fix it by passing FrameInputs to the adapter.
    Fix it by putting what is missing onto the DesignResult.
    """
    built = 0
    for m in run.result.members:
        if not m.section_id.startswith("BU-"):
            continue                       # cold-formed, catalogue-named
        section = parse_mark(m.section_id, m.length)
        assert isinstance(section, (WeldedI, TaperedMember))
        if isinstance(section, TaperedMember):
            assert section.length == pytest.approx(m.length)
        built += 1
    assert built >= 5, "expected the frame members and the strut"


def test_the_rebuilt_section_is_the_one_that_was_designed(run) -> None:
    """Round-trip, not merely parseable.

    A mark that parsed into a DIFFERENT section would satisfy the test above
    and produce a model that does not match the calculation.
    """
    layout_by_mark = {m.mark: m for m in run.layout.members}
    for md in run.result.members:
        fm = layout_by_mark.get(md.mark)
        if fm is None:
            continue
        rebuilt = parse_mark(md.section_id, md.length)
        assert isinstance(rebuilt, TaperedMember)
        assert rebuilt.start.d == pytest.approx(fm.taper.start.d)
        assert rebuilt.end.d == pytest.approx(fm.taper.end.d)
        assert rebuilt.start.tw == pytest.approx(fm.taper.start.tw)
        assert rebuilt.start.bf_top == pytest.approx(fm.taper.start.bf_top)
        assert rebuilt.start.tf_top == pytest.approx(fm.taper.start.tf_top)


def test_the_result_serialises_and_comes_back(run) -> None:
    """The contract says JSON out. It has to survive the trip."""
    data = run.result.to_dict()
    back = DesignResult.from_dict(data)
    assert back.spec.job_number == run.result.spec.job_number
    assert len(back.members) == len(run.result.members)
    assert back.max_utilisation == pytest.approx(run.result.max_utilisation)


# ---------------------------------------------------------------------
# What the pipeline produced
# ---------------------------------------------------------------------

def test_the_whole_building_is_designed_not_just_the_frame(run) -> None:
    """Four frame members, a purlin, a girt, two braces and a strut."""
    marks = {m.mark for m in run.result.members}
    assert {"C1", "C2", "R1", "R2"} <= marks          # main frame
    assert {"P1", "G1"} <= marks                      # secondary
    assert {"RB1", "WB1", "ES1"} <= marks             # longitudinal
    types = {m.member_type for m in run.result.members}
    assert MemberType.PURLIN in types
    assert MemberType.GIRT in types


def test_every_connection_type_is_present(run) -> None:
    kinds = {c.connection_type.value for c in run.result.connections}
    assert kinds == {
        "eave_end_plate", "apex_end_plate", "base_plate",
        "purlin_cleat", "bracing_gusset",
    }


def test_the_result_carries_reactions_for_every_base_and_combination(
    run,
) -> None:
    n = len(run.layout.base_nodes) * len(run.envelope.combos)
    assert len(run.result.reactions) == n


def test_the_intermediates_are_reachable_for_a_reporting_caller(run) -> None:
    """``design_frame`` exists so a caller can explain the numbers."""
    assert run.wind.qh > 0
    assert run.brace_forces.F_eave > 0
    assert run.purlin_cladding.zone
    assert run.girt_cladding.zone
    assert run.endwall_pressure > 0
    assert run.roof_live.Lr > 0


# ---------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------

def test_an_invalid_spec_is_refused_before_anything_is_designed(spec) -> None:
    """Fail on the input, not three modules later on a derived number."""
    bad = ProjectSpec(
        project_name="  ", job_number="", site=spec.site,
        geometry=spec.geometry, materials=spec.materials,
    )
    with pytest.raises(PipelineError, match="before anything is designed"):
        run_frame(bad, _inputs())


def test_a_building_with_no_bracing_is_refused(spec) -> None:
    """A missing load path, not a zero one."""
    with pytest.raises(PipelineError, match="no load path"):
        run_frame(spec, _inputs(bracing=BracingInputs(
            braced_bays=(),
            roof_brace=_inputs().bracing.roof_brace,
            wall_brace=_inputs().bracing.wall_brace,
            strut_section=_inputs().bracing.strut_section,
        )))


def test_a_purlin_with_no_antisag_ties_is_refused(spec) -> None:
    """Because the uplift check would report inf and read as unevaluated.

    Working agreement (d): an unevaluated check must never read as a pass.
    Here the pipeline refuses the arrangement outright instead, because the
    engineer can fix it by adding a tie.
    """
    with pytest.raises(PipelineError, match="at least 1"):
        run_frame(spec, _inputs(secondary=SecondaryInputs(
            purlin_section=C200, girt_section=C200, antisag_ties_per_bay=0,
        )))


def test_a_frame_index_outside_the_building_is_refused(spec) -> None:
    with pytest.raises(PipelineError, match="outside"):
        run_frame(spec, _inputs(frame_index=99))


def test_an_empty_zone_list_is_refused(spec) -> None:
    with pytest.raises(PipelineError, match="no zone to design for"):
        run_frame(spec, _inputs(secondary=SecondaryInputs(
            purlin_section=C200, girt_section=C200, purlin_zones=(),
        )))


# ---------------------------------------------------------------------
# The derived frame index
# ---------------------------------------------------------------------

def test_the_default_frame_is_the_one_with_the_widest_tributary(
    spec,
) -> None:
    """Derived by measurement, not assumed to be the middle.

    Bays 6.0 / 7.5 / 7.5 / 7.5 / 6.0 give tributary widths
    3.0 / 6.75 / 7.5 / 7.5 / 6.75 / 3.0 m. The widest is index 2 (tied with
    3), and ``max`` takes the first - so the default lands on a genuinely
    governing frame rather than on an end frame.
    """
    run = design_frame(spec, _inputs(frame_index=None))
    widths = spec.geometry.tributary_widths()
    assert widths[run.frame_index] == pytest.approx(max(widths))
    assert run.frame_index == 2


def test_naming_the_frame_explicitly_overrides_the_default(spec) -> None:
    run = design_frame(spec, _inputs(frame_index=1))
    assert run.frame_index == 1


# ---------------------------------------------------------------------
# The verification roll-up
# ---------------------------------------------------------------------

def test_every_module_with_a_gate_is_scanned() -> None:
    """The list is explicit, so a module cannot fall off it silently.

    A module dropped from ENGINE_MODULES would take its open flags off the
    calculation sheet, and the sheet would read as MORE verified than it is.
    That is the failure this test exists for - it was a real bug in the
    example script, which never scanned loads.cladding.

    ``bimsteelauto.analysis`` and ``bimsteelauto.design`` are excluded by
    IDENTITY, not by name: their packages re-export a submodule's VERIFIED,
    so the dict is literally the same object and scanning both would
    double-count. Anything with a dict of its own must be on the list.
    """
    import pkgutil

    import bimsteelauto

    already = [
        id(getattr(__import__(n, fromlist=["VERIFIED"]), "VERIFIED", None))
        for n in ENGINE_MODULES
    ]
    scanned = set(ENGINE_MODULES)
    missing = []
    for mod in pkgutil.walk_packages(
        bimsteelauto.__path__, prefix="bimsteelauto."
    ):
        try:
            m = __import__(mod.name, fromlist=["VERIFIED"])
        except Exception:                       # noqa: BLE001
            continue
        flags = getattr(m, "VERIFIED", None)
        if flags is None or mod.name in scanned:
            continue
        if id(flags) in already:
            continue                            # a re-export, not a gate
        missing.append(mod.name)
    assert not missing, f"modules with a VERIFIED gate that are NOT scanned: {missing}"


def test_the_open_flags_reach_the_result(run) -> None:
    assert run.result.unverified == open_verification_flags()


def test_the_engine_is_fully_verified_today() -> None:
    """Zero open flags as of 2026-09-02.

    If this fails because a flag was OPENED, something regressed. If it
    fails because a module was ADDED with an open flag, that is correct and
    expected - close it, then update this test.
    """
    assert open_verification_flags() == ()


def test_coverage_gaps_are_carried_but_do_not_block_on_their_own(run) -> None:
    """Gaps print; they are not the same thing as unverified relations."""
    assert run.result.coverage_gaps
    assert not run.result.unverified
    # The sheet is still blocked - by real failures, not by the gaps.
    assert not run.result.ready_for_issue()
    assert any("fails" in b for b in run.result.issue_blockers())


# ---------------------------------------------------------------------
# The bracing arrangement - schema 1.1.0
# ---------------------------------------------------------------------

def test_the_result_records_where_the_bracing_went(run) -> None:
    """The forces were always there; the LAYOUT was not.

    Without it the FreeCAD adapter cannot draw a brace in the right bay -
    it drew them in the end bays and warned that it was guessing, which is
    honest and still wrong on any building braced elsewhere.
    """
    b = run.result.bracing
    assert b is not None
    assert b.braced_bays == (1, 3)
    assert b.n_braced_walls == 2
    assert b.roof_panel_splits == 2
    assert b.n_braced == 2
    assert b.is_stated


def test_the_arrangement_matches_what_was_designed(run) -> None:
    """The model must not be able to show a different building.

    Pinned against the LongitudinalSystem the engine actually solved, not
    against the inputs - so a pipeline that solved one arrangement and
    reported another fails here.
    """
    assert run.result.bracing.braced_bays == tuple(
        run.longitudinal.braced_bays
    )
    assert run.result.bracing.n_braced == run.longitudinal.n_braced


def test_the_arrangement_survives_the_json_round_trip(run) -> None:
    """It has to, or the adapter never sees it."""
    back = DesignResult.from_dict(run.result.to_dict())
    assert back.bracing == run.result.bracing


def test_an_older_result_without_the_field_still_loads(run) -> None:
    """Additive, so schema 1.0.0 documents keep working.

    ``_from_jsonable`` skips keys a document does not carry, so the field
    comes back as None - which is DIFFERENT from an empty tuple, and the
    adapter has to tell them apart.
    """
    data = run.result.to_dict()
    del data["bracing"]
    data["schema_version"] = "1.0.0"
    back = DesignResult.from_dict(data)
    assert back.bracing is None
    assert back.members


def test_no_bracing_and_unrecorded_bracing_are_different_things() -> None:
    """The distinction the adapter turns on.

    An EMPTY braced_bays on a current result means the building genuinely
    has none - no load path for endwall wind, which is a missing design.
    A MISSING arrangement means nobody wrote it down. Only the second is a
    case for the adapter to guess, and conflating them would let a real
    absence of bracing be silently drawn in as if it were there.
    """
    assert not BracingArrangement().is_stated
    assert not BracingArrangement(braced_bays=()).is_stated
    assert BracingArrangement(braced_bays=(0,)).is_stated


def test_the_schema_version_was_bumped_for_the_new_field() -> None:
    """An additive change moves the minor, and the result carries it.

    1.1.0 added ``DesignResult.bracing``; 1.2.0 added the shop geometry on
    ``ConnectionDesign.detail["geometry"]``. Both are additive, so this
    tracks the CURRENT version rather than freezing an old one - what it
    guards is that the version moved AT ALL when a field was added, which
    is what tells the adapter whether to expect the field.
    """
    assert SCHEMA_VERSION == "1.2.0"
