"""The 3D building model.

A 3D frame model cannot be checked by re-deriving its arithmetic - PyNite
does the arithmetic, and it is the same solver the plane frame already
trusts.  What a 3D model gets wrong is MODELLING, and every mistake in the
list below produces a model that assembles, solves cleanly, and looks like
a building:

1. **A local axis rolled the wrong way.**  ``add_section`` takes
   ``(A, Iy, Iz, J)`` and PyNite bends about local z.  Swap the two and the
   columns bend about their weak axis; roll a girt wrong and it resists
   wind on an axis forty times too flexible.
2. **A tapered member modelled prismatic.**  This one was MEASURED during
   development: one prismatic member on the start section put the interior
   frame 36% away from the plane frame, because it makes the column too
   flexible and the rafter too stiff at once and the errors add.
3. **A secondary member floating beside the frame instead of attached to
   it.**  A purlin line that shares no node with the rafters restrains
   nothing.
4. **Both diagonals of an X working.**  A building twice as stiff along its
   length as the real one.

The four tests that matter here each kill one of those, and each needs an
answer known WITHOUT running this module: the verified plane frame, the
symmetry of the building, the closed-form statics in ``analysis/bracing.py``,
and simple equilibrium.
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto.analysis import FrameAnalysis, build_layout
from bimsteelauto.analysis.frame import FrameSections, MemberSectionSpec
from bimsteelauto.analysis.space import (
    COVERAGE_GAPS,
    VERIFIED,
    SectionProps,
    SpaceAnalysis,
    SpaceError,
    build_space_layout,
    open_section_J,
)
from bimsteelauto.design import CFShape, ColdFormedSection
from bimsteelauto.sections import WeldedI
from bimsteelauto.spec import MemberType, PEBGeometry

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ----------------------------------------------------------------------
# A small, symmetric test building
# ----------------------------------------------------------------------
#
# Deliberately SYMMETRIC in both directions: equal bays, a gable roof, the
# same column both sides.  Symmetry is what makes the negative controls
# possible - a symmetric building under a symmetric load has no torsion,
# and there is no way to know that except by symmetry.

GEOM = PEBGeometry.from_m(
    span_widths=[24.0],
    bay_spacings=[6.0, 6.0, 6.0],
    eave_height=6.0,
    roof_slope=0.10,
    purlin_spacing=3.0,          # coarse on purpose - this is a test model
    girt_spacing=3.0,
)

SECTIONS = FrameSections(
    column=MemberSectionSpec(
        start=WeldedI.symmetric(d=400.0, tw=8.0, bf=220.0, tf=14.0),
        end=WeldedI.symmetric(d=700.0, tw=8.0, bf=220.0, tf=14.0),
    ),
    rafter=MemberSectionSpec(
        start=WeldedI.symmetric(d=700.0, tw=7.0, bf=220.0, tf=14.0),
        end=WeldedI.symmetric(d=400.0, tw=7.0, bf=220.0, tf=14.0),
    ),
)

PURLIN = ColdFormedSection(
    name="C200x60x20x2.5", shape=CFShape.C,
    t=2.5, H=200.0, B=60.0, Dlip=20.0, r=3.0,
    A=685.0, Ix=4.02e6, Sx=40_200.0,
    Se_top=34_500.0, Se_bot=34_500.0,
    Iy=0.30e6, Sy=7_000.0, J=1_430.0, Cw=2.1e9, ho=200.0,
)

STRUT = WeldedI.symmetric(d=250.0, tw=6.0, bf=150.0, tf=8.0)

W_RAFTER = 3.0        # kN/m on each rafter, both models


@pytest.fixture(scope="module")
def layout():
    return build_space_layout(
        GEOM, SECTIONS,
        purlin_section=PURLIN, girt_section=PURLIN,
        eave_strut_section=STRUT,
        brace_diameter=20.0, braced_bays=(1,),
    )


@pytest.fixture(scope="module")
def gravity(layout):
    """Symmetric gravity on every frame line."""
    sa = SpaceAnalysis(layout)
    for m in layout.plane.members:
        if m.member_type is MemberType.RAFTER:
            sa.add_frame_line_load("D", m.mark, W_RAFTER)
    sa.add_combination("1.0D", {"D": 1.0})
    return sa.solve()


@pytest.fixture(scope="module")
def plane_gravity():
    """The SAME load on the verified plane frame."""
    plane = build_layout(GEOM, SECTIONS)
    fa = FrameAnalysis(plane)
    for m in plane.members:
        if m.member_type is MemberType.RAFTER:
            fa.add_line_load("D", m.mark, W_RAFTER)
    fa.add_combination("1.0D", {"D": 1.0})
    return plane, fa.solve(p_delta=False)


# ----------------------------------------------------------------------
# The layout
# ----------------------------------------------------------------------

def test_the_building_is_grown_from_the_designed_frame(layout) -> None:
    """The 3D model must not be laid out on a different frame.

    Both go through ``build_layout``, so the plane frame, the 3D model and
    the FreeCAD adapter cannot disagree about where a column is.
    """
    assert layout.frame_lines == (0.0, 6000.0, 12000.0, 18000.0)
    assert len(layout.plane.members) == 4          # 2 columns, 2 rafters
    for fm in layout.plane.members:
        for line, z in enumerate(layout.frame_lines):
            key = f"{fm.mark}#{line}"
            assert key in layout.segments
            first = layout.member(layout.segments[key][0][0])
            assert first.i_point == (fm.i_point[0], fm.i_point[1], z)


def test_the_taper_is_sliced_not_smeared(layout) -> None:
    """The 36% test, as a structural assertion.

    A tapered member modelled as one prismatic member is the mistake that
    was actually made here, and it is invisible in the output. So the
    slicing is asserted directly: more than one segment, and the sections
    at the two ends differ.
    """
    entries = layout.segments["C1#0"]
    assert len(entries) > 1, "a tapered column was modelled prismatic"
    first = layout.member(entries[0][0]).section
    last = layout.member(entries[-1][0]).section
    assert last.Iz > 2.0 * first.Iz, (
        "the segments all have the same stiffness - the taper was smeared"
    )


def test_every_secondary_member_shares_a_node_with_the_frame(layout) -> None:
    """A purlin bolted to nothing restrains nothing.

    The failure this catches is a purlin line laid out at the right
    coordinates on nodes of its own: geometrically perfect, structurally a
    free-floating chain, and the model may still solve.
    """
    frame_nodes = set()
    for m in layout.members:
        if m.is_primary:
            frame_nodes.add(m.i_point)
            frame_nodes.add(m.j_point)

    secondary = [
        m for m in layout.members
        if m.member_type in (MemberType.PURLIN, MemberType.GIRT,
                             MemberType.EAVE_STRUT)
    ]
    assert secondary
    for m in secondary:
        assert m.i_point in frame_nodes and m.j_point in frame_nodes, (
            f"{m.mark} does not land on a frame node"
        )


def test_the_girts_are_rolled_and_the_purlins_follow_the_roof(layout) -> None:
    """The roll angles, checked as numbers rather than trusted.

    A girt left at zero roll resists wind about its WEAK axis.
    """
    girt = next(m for m in layout.members
                if m.member_type is MemberType.GIRT)
    purlin = next(m for m in layout.members
                  if m.member_type is MemberType.PURLIN)
    assert girt.rotation == pytest.approx(math.pi / 2.0)
    assert purlin.rotation == pytest.approx(math.atan(0.10))
    strut = next(m for m in layout.members
                 if m.member_type is MemberType.EAVE_STRUT)
    assert strut.rotation == 0.0


def test_the_strong_axis_goes_in_as_Iz(layout) -> None:
    """PyNite bends about local z. For every member in this model the
    strong axis is local z, so ``Iz > Iy`` everywhere except the rods."""
    for m in layout.members:
        if m.member_type in (MemberType.ROOF_BRACING, MemberType.WALL_BRACING):
            assert m.section.Iy == pytest.approx(m.section.Iz)   # a round bar
        else:
            assert m.section.Iz > m.section.Iy, m.mark


def test_a_building_with_no_bracing_says_so(layout) -> None:
    bare = build_space_layout(GEOM, SECTIONS, purlin_section=PURLIN)
    assert any("NO BRACING" in n for n in bare.notes)
    assert bare.braced_bays == ()


def test_a_cold_formed_section_with_no_stiffness_is_refused() -> None:
    """Blanks over assumptions: a guessed Iy would change every force
    around that member."""
    bad = ColdFormedSection(
        name="X", shape=CFShape.C, t=2.0, H=200.0, B=60.0, Dlip=20.0, r=3.0,
        A=600.0, Ix=3.0e6, Sx=30_000.0, Se_top=30_000.0, Se_bot=30_000.0,
        Iy=0.0, Sy=0.0,
    )
    with pytest.raises(SpaceError, match="no weak-axis stiffness"):
        SectionProps.from_cold_formed("X", bad)


def test_the_catalogue_torsion_constant_beats_the_formula() -> None:
    """A supplier value is used where there is one; the thin open-section
    estimate is the fallback and only that."""
    props = SectionProps.from_cold_formed("purlin", PURLIN)
    assert props.J == pytest.approx(1_430.0)
    # And the fallback is the standard sum(b t^3)/3.
    assert open_section_J((100.0, 2.0), (50.0, 2.0)) == pytest.approx(
        (100.0 * 8.0 + 50.0 * 8.0) / 3.0
    )


# ----------------------------------------------------------------------
# CONTROL 1 - the 3D model must reproduce the verified plane frame
# ----------------------------------------------------------------------

def test_an_interior_frame_reproduces_the_plane_frame(
    gravity, plane_gravity
) -> None:
    """The load-bearing test in this file.

    Two independent models of the same building under the same load. The
    plane frame is verified against longhand statics; if the 3D model
    agrees with it on P, V and M at an interior frame line, then no axis is
    rolled wrongly, no section is swapped and no load is going in on the
    wrong plane - because every one of those errors would show up here.

    1% is the tolerance and it is not slack: the 3D model splits the taper
    at the purlin and girt points as well as at the taper's own breaks, so
    it is very slightly FINER than the plane frame and is expected to
    differ by a fraction of a percent. It comes out at 0.08%.
    """
    plane, env2 = plane_gravity
    worst = 0.0
    for fm in plane.members:
        for a2, a3 in (("P", "P"), ("V", "Vy"), ("M", "Mz")):
            want = max(abs(getattr(s, a2))
                       for s in env2.stations[fm.mark]["1.0D"])
            got = max(abs(getattr(s, a3))
                      for s in gravity.stations[f"{fm.mark}#1"]["1.0D"])
            assert want > 1.0, f"{fm.mark} {a2} is too small to compare"
            worst = max(worst, abs(got - want) / want)
    assert worst < 0.01, f"3D and plane frame differ by {worst * 100:.2f}%"


def test_the_base_reactions_agree_with_the_plane_frame(
    gravity, plane_gravity
) -> None:
    """Thrust is the number a plane frame is easiest to get wrong on, and
    the one the base plate is designed for."""
    plane, env2 = plane_gravity
    for b in plane.base_nodes:
        fx2, fy2, mz2 = env2.reactions[b]["1.0D"]
        r3 = gravity.reactions[f"{b}#1"]["1.0D"]
        assert r3[0] == pytest.approx(fx2, rel=0.01)      # thrust
        assert r3[1] == pytest.approx(fy2, rel=0.01)      # vertical
        assert r3[5] == pytest.approx(mz2, abs=1.0)       # base moment


def test_the_eave_drift_agrees_with_the_plane_frame(
    gravity, plane_gravity
) -> None:
    plane, env2 = plane_gravity
    e = plane.eave_nodes[0]
    dx2, dy2 = env2.displacements[e]["1.0D"]
    d3 = gravity.displacements[f"{e}#1"]["1.0D"]
    assert d3[0] == pytest.approx(dx2, rel=0.02)
    assert d3[1] == pytest.approx(dy2, rel=0.02)


# ----------------------------------------------------------------------
# CONTROL 2 - the NEGATIVE control on the local axes
# ----------------------------------------------------------------------

def test_a_symmetric_building_under_symmetric_load_has_no_torsion(
    gravity,
) -> None:
    """The negative control, and the reason the test building is symmetric.

    There is no way to know the right answer for a torsion diagram except
    by symmetry, and symmetry gives it exactly: zero. Any member rolled the
    wrong way, any load applied out of plane, any section swapped - all of
    them put something out of plane and this fails.

    A test that merely asserted "torsion was computed" would pass on every
    one of those.
    """
    for attr in ("Vz", "T", "My"):
        mark, combo, value = gravity.worst(attr)
        assert abs(value) < 1e-3, (
            f"symmetric gravity produced {attr} = {value:.6f} in {mark} "
            f"under {combo} - something is out of plane"
        )


def test_symmetric_gravity_moves_nothing_along_the_building(gravity) -> None:
    """The displacement half of the same control."""
    worst = max(
        abs(d[2]) for per in gravity.displacements.values()
        for d in per.values()
    )
    assert worst < 1e-3, f"{worst:.6f} mm of longitudinal drift under gravity"


def test_both_sidewalls_carry_the_same_gravity_reaction(gravity) -> None:
    """Symmetry across the span, which a swapped column section breaks."""
    for line in range(4):
        a = gravity.reactions[f"B1#{line}"]["1.0D"]
        b = gravity.reactions[f"B2#{line}"]["1.0D"]
        assert a[1] == pytest.approx(b[1], rel=1e-6)      # vertical
        assert a[0] == pytest.approx(-b[0], rel=1e-6)     # equal and opposite


# ----------------------------------------------------------------------
# CONTROL 3 and 4 - the longitudinal path, and tension-only bracing
# ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def longitudinal(layout):
    """A known force along the building, applied at the eaves."""
    sa = SpaceAnalysis(layout)
    for line in (0, len(layout.frame_lines) - 1):
        for e in layout.plane.eave_nodes:
            sa.add_node_load("Wl", f"{e}#{line}", fz=25.0)
    sa.add_combination("1.0Wl", {"Wl": 1.0})
    return sa.solve()


def test_everything_applied_along_the_building_reaches_the_bases(
    longitudinal,
) -> None:
    """Equilibrium, which needs no model to know: 4 x 25 = 100 kN in."""
    total = sum(
        longitudinal.reactions[b]["1.0Wl"][2]
        for b in longitudinal.reactions
    )
    assert total == pytest.approx(-100.0, abs=1e-3)


def test_the_compression_diagonal_drops_out(longitudinal, layout) -> None:
    """A tension-only X: exactly half the diagonals work.

    If both worked the building would be twice as stiff along its length as
    the real one, and the diagonal force would be half of what it is. This
    is the assumption ``analysis/bracing.py`` makes by hand; here the
    solver has to arrive at it.
    """
    walls = [
        m for m in layout.members
        if m.member_type is MemberType.WALL_BRACING
    ]
    assert walls, "no wall bracing was built"
    forces = {
        m.mark: longitudinal.stations[m.mark]["1.0Wl"][0].P for m in walls
    }
    # P is positive in COMPRESSION, so a working diagonal is negative.
    working = [k for k, v in forces.items() if v < -1e-6]
    idle = [k for k, v in forces.items() if abs(v) <= 1e-6]
    compressed = [k for k, v in forces.items() if v > 1e-6]

    assert not compressed, f"a diagonal is in compression: {compressed}"
    assert len(working) == len(walls) // 2, forces
    assert len(idle) == len(walls) // 2, forces


def test_the_load_goes_through_the_bracing_and_not_the_frames(
    longitudinal, layout
) -> None:
    """CONTROL 3: the load path, against the hand calculation's assumption.

    ``analysis/bracing.py`` assumes the diagonals take ALL the panel shear
    and the frames take none - conservative for the diagonal, and it cannot
    say by how much. This model can: the Z components of the working
    diagonals must add up to nearly all of the applied force, and the
    remainder must be small enough that ignoring it is conservative rather
    than wrong.
    """
    applied = 100.0
    through_braces = 0.0
    for m in layout.members:
        if m.member_type is not MemberType.WALL_BRACING:
            continue
        P = longitudinal.stations[m.mark]["1.0Wl"][0].P
        dz = abs(m.j_point[2] - m.i_point[2]) / m.length
        through_braces += -P * dz

    fraction = through_braces / applied
    assert 0.85 <= fraction <= 1.0, (
        f"the diagonals carry {fraction:.1%} of the longitudinal force - "
        f"the hand calculation assumes 100% and would not be conservative "
        f"if this were above it"
    )


def test_longitudinal_load_does_not_sway_the_building_across(
    longitudinal, layout
) -> None:
    """Load along Z must not move the building along X. It would if a
    frame member were rolled into the wrong plane."""
    e = layout.plane.eave_nodes[0]
    for line in range(len(layout.frame_lines)):
        across, along = longitudinal.drift(f"{e}#{line}", "1.0Wl")
        assert abs(across) < 0.05, f"line {line} swayed {across:.3f} mm across"
        assert along > 0.1, f"line {line} did not move along the building"


# ----------------------------------------------------------------------
# The envelope's own contract
# ----------------------------------------------------------------------

def test_every_station_carries_all_six_components(gravity, layout) -> None:
    for key in layout.segments:
        sts = gravity.stations[key]["1.0D"]
        assert sts
        for st in sts:
            for attr in ("P", "Vy", "Vz", "T", "My", "Mz"):
                assert isinstance(getattr(st, attr), float)


def test_stations_run_from_the_design_members_own_i_end(gravity, layout) -> None:
    """``x`` is measured along the DESIGN member, not along a slice - the
    same contract FrameAnalysis gives, so the two can be laid side by
    side."""
    key = "C1#1"
    sts = gravity.stations[key]["1.0D"]
    xs = [s.x for s in sts]
    assert xs == sorted(xs)
    assert xs[0] == pytest.approx(0.0)
    assert xs[-1] == pytest.approx(GEOM.eave_height, abs=1.0)


def test_a_station_converts_to_the_spec_MemberForces(gravity) -> None:
    st = gravity.stations["R1#1"]["1.0D"][0]
    mf = st.to_member_forces()
    assert mf.P == st.P and mf.Mz == st.Mz and mf.T == st.T


def test_the_envelope_records_how_it_was_solved(gravity) -> None:
    """A run that could not do what was asked must say so, not quietly give
    a different answer."""
    assert gravity.p_delta is False
    assert gravity.tension_only is True
    assert any("P-Delta" in n for n in gravity.notes)


def test_a_solve_with_no_combinations_is_refused(layout) -> None:
    with pytest.raises(SpaceError, match="no load combinations"):
        SpaceAnalysis(layout).solve()


def test_an_unknown_member_or_node_is_refused(layout) -> None:
    sa = SpaceAnalysis(layout)
    with pytest.raises(SpaceError):
        sa.add_frame_line_load("D", "NOPE", 1.0)
    with pytest.raises(SpaceError):
        sa.add_node_load("W", "NOPE", fz=1.0)


# ----------------------------------------------------------------------
# The gate
# ----------------------------------------------------------------------

def test_the_gaps_are_declared() -> None:
    assert COVERAGE_GAPS
    assert any("purlins carry none" in g for g in COVERAGE_GAPS)
    assert any("Endwall" in g for g in COVERAGE_GAPS)
    # The calculation sheet still comes from the plane frame, and the
    # torsion grading is uniform-torsion only - both must stay declared.
    assert any("still written from the plane frame" in g
               for g in COVERAGE_GAPS)
    assert any("LOWER BOUND" in g for g in COVERAGE_GAPS)


def test_the_verified_flags_have_a_test_each() -> None:
    """Every flag names the control that closes it, and the control is in
    this file. A flag with no test behind it is the failure the gate
    exists to prevent."""
    for flag, closer in (
        ("agrees_with_the_plane_frame",
         test_an_interior_frame_reproduces_the_plane_frame),
        ("symmetric_load_gives_no_torsion",
         test_a_symmetric_building_under_symmetric_load_has_no_torsion),
        ("longitudinal_path_matches_the_hand_calculation",
         test_the_load_goes_through_the_bracing_and_not_the_frames),
        ("compression_diagonal_drops_out",
         test_the_compression_diagonal_drops_out),
    ):
        assert flag in VERIFIED
        assert callable(closer)


# ----------------------------------------------------------------------
# The contour set - the file the viewer draws
# ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def contours(layout, gravity):
    from bimsteelauto.analysis.results import build_space_contours

    return build_space_contours(
        layout, gravity, project_name="T", job_number="T1",
    )


def test_the_contour_set_covers_every_design_member(contours, layout) -> None:
    assert {m.mark for m in contours.members} == set(layout.segments)
    assert contours.is_3d


def test_every_station_carries_its_own_Z(contours) -> None:
    """The difference between a 2D set and a 3D one.

    A 2D member has no Z and lies in its frame's plane; a 3D member carries
    Z on every station. A viewer that read an empty Z as zero would pile
    the whole building onto grid line 1, so the fallback is ``frame_y`` and
    it is tested rather than trusted.
    """
    for m in contours.members:
        assert m.is_3d
        assert len(m.Z) == m.n_stations
    zs = {round(z, 3) for m in contours.members for z in m.Z}
    assert len(zs) > 1, "every member is at the same Z - this is not 3D"


def test_a_two_dimensional_member_falls_back_to_its_frame_line(contours) -> None:
    from dataclasses import replace

    flat = replace(contours.members[0], Z=())
    assert flat.Z_at(12345.0) == tuple(12345.0 for _ in flat.x)
    assert not flat.is_3d


def test_the_out_of_plane_components_are_zero_not_missing(contours) -> None:
    """Asking a 2D result for a torsion diagram must give a flat line.

    Zero IS the plane frame's answer, and an exception would read as "not
    implemented" when the truth is "restrained to zero by the model".
    """
    from dataclasses import replace

    flat = replace(contours.members[0], T={}, Vz={}, My={})
    combo = contours.combos[0]
    for name in ("T", "Vz", "My"):
        assert flat.component(name, combo) == tuple(0.0 for _ in flat.x)


def test_a_secondary_member_has_no_stress_contour_and_says_so(
    contours,
) -> None:
    """A lipped C's extreme fibre is not at d/2, so its stress is NOT
    COMPUTED - and the notes say that, rather than a zero being read as
    zero stress."""
    combo = contours.combos[0]
    purlin = next(m for m in contours.members if m.mark.startswith("PL_"))
    assert all(v == 0.0 for v in purlin.sigma[combo])
    assert any("NOT COMPUTED" in n for n in contours.check_notes)


def test_without_a_run_nothing_is_graded_and_the_reason_is_recorded(
    contours,
) -> None:
    """``build_space_contours`` needs ``run=`` for the materials, the design
    method and the brace points.

    Without it a member cannot be graded, and its ratio is ABSENT rather
    than zero - working agreement (d). On a contour a zero utilisation is
    the calmest colour on the picture, so "not graded" must not be able to
    look like "fine".
    """
    from bimsteelauto.analysis.results import ContourError, Quantity

    combo = contours.combos[0]
    for m in contours.members:
        assert m.ratio == {}
        with pytest.raises(ContourError, match="not graded"):
            m.values(Quantity.UTILISATION, combo)
    assert any("NOT graded" in n for n in contours.check_notes)


def test_with_a_run_every_primary_member_is_graded(layout, gravity) -> None:
    """The point of the biaxial and torsion work: My and T become a number.

    The stub carries only what ``_space_utilisations`` reads - materials,
    design method and the geometry the brace points come from. The DESIGN
    itself is verified in ``tests/test_design_biaxial.py``; what is checked
    here is that the six-component stations reach it and the ratio comes
    back onto the contour.
    """
    from types import SimpleNamespace

    from bimsteelauto.analysis.results import Quantity, build_space_contours
    from bimsteelauto.design import CFShape  # noqa: F401  (import shape)
    from bimsteelauto.spec import DesignMethod, Materials

    run = SimpleNamespace(
        layout=SimpleNamespace(geometry=GEOM),
        spec=SimpleNamespace(
            materials=Materials(
                Fy_builtup=345.0, Fu_builtup=450.0,
                Fy_cold_formed=345.0, Fu_cold_formed=450.0,
                bolt_grade="A325", anchor_grade="F1554-36",
                fc_concrete=30.0,
            ),
            settings=SimpleNamespace(design_method=DesignMethod.LRFD),
        ),
    )
    cs = build_space_contours(layout, gravity, run=run)
    combo = cs.combos[0]

    primary = [m for m in cs.members
               if m.member_type in ("column", "rafter")]
    secondary = [m for m in cs.members
                 if m.member_type in ("purlin", "girt", "eave_strut",
                                      "wall_bracing", "roof_bracing")]
    assert primary and secondary

    for m in primary:
        vals = m.values(Quantity.UTILISATION, combo)
        assert len(vals) == m.n_stations
        assert max(vals) > 0.0

    # A purlin is designed to AISI S100 and a rod by design/bracing.py -
    # neither belongs in an AISC 360 station sweep, so neither is graded,
    # and neither is quietly coloured green.
    for m in secondary:
        assert m.ratio == {}

    assert any("ALL SIX components" in n for n in cs.check_notes)


def test_every_force_component_can_be_asked_for_by_name(contours) -> None:
    from bimsteelauto.analysis.results import Quantity

    combo = contours.combos[0]
    for q in Quantity.FORCES:
        vals = [v for m in contours.members for v in m.values(q, combo)]
        assert len(vals) > 100
    # And the ones a plane frame cannot produce are still zero HERE too,
    # because this is symmetric gravity.
    for q in (Quantity.TORSION, Quantity.SHEAR_Z, Quantity.MOMENT_Y):
        assert max(abs(v) for m in contours.members
                   for v in m.values(q, combo)) < 1e-3


def test_the_contour_set_round_trips_through_json(contours, tmp_path) -> None:
    from bimsteelauto.analysis.results import load_contours, write_contours

    path = write_contours(contours, tmp_path / "c3.json")
    back = load_contours(path)
    assert back.is_3d
    assert len(back.members) == len(contours.members)
    a, b = contours.members[0], back.members[0]
    assert b.Z == a.Z
    combo = contours.combos[0]
    assert b.component("T", combo) == a.component("T", combo)


def test_a_schema_1_file_still_loads(contours, tmp_path) -> None:
    """The additive promise, tested rather than asserted in a comment.

    A /1 file has no Z, no uZ and none of the out-of-plane components. It
    must load, report itself as 2D, and answer a torsion question with
    zero.
    """
    import json

    from bimsteelauto.analysis.results import ContourSet, load_contours

    d = contours.to_dict()
    d["schema"] = "bimsteelauto.contours/1"
    d.pop("is_3d", None)
    for m in d["members"]:
        for key in ("Z", "uZ", "Vz", "T", "My"):
            m.pop(key, None)
    path = tmp_path / "c1.json"
    path.write_text(json.dumps(d), encoding="utf-8")

    back = load_contours(path)
    assert isinstance(back, ContourSet)
    assert back.is_3d is False
    m = back.members[0]
    assert m.Z == () and not m.is_3d
    assert m.component("T", back.combos[0]) == tuple(0.0 for _ in m.x)


def test_a_file_from_a_future_schema_is_refused(contours, tmp_path) -> None:
    import json

    from bimsteelauto.analysis.results import ContourError, load_contours

    d = contours.to_dict()
    d["schema"] = "bimsteelauto.contours/99"
    path = tmp_path / "c99.json"
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ContourError, match="different version"):
        load_contours(path)


def test_the_deflected_shape_is_three_dimensional(contours) -> None:
    combo = contours.combos[0]
    m = next(mm for mm in contours.members if mm.mark.startswith("R1#"))
    pts = m.deflected3(combo, 1.0)
    assert len(pts) == m.n_stations
    assert all(len(p) == 3 for p in pts)
    # Undeformed Z, because symmetric gravity does not move anything along
    # the building.
    assert pts[0][2] == pytest.approx(m.Z[0], abs=1e-3)
