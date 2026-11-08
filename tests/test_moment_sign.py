"""The moment sign at the PyNite boundary (fix/moment-sign, 2026-11-08).

PyNite's ``Mz`` is positive for tension on the member's local +y face; the
design layer reads ``Mz >= 0`` as "TOP flange in compression".  Before the fix
the PyNite value went through unchanged and the compression flange was chosen
backwards.  Checked here, each against physics that does not depend on this
package:

* a simply supported beam under gravity SAGS: PyNite says -wL^2/8; the design
  sign must be +wL^2/8 and the check must use the TOP flange's bracing
  (sagging compresses the top flange, the bottom one is in tension);
* a pinned portal under gravity HOGS at the haunch and puts the columns'
  OUTER flanges in tension: the design sign must be negative there on the
  rafter and on BOTH columns, and the check at the knee must use the BOTTOM
  (inner) flange's bracing - the long one;
* the factor table, including the one case where PyNite's local y is the
  bottom face: an exactly vertical right-hand column.
"""

from __future__ import annotations

import pytest

from bimsteelauto.analysis import (
    BaseFixity,
    FrameAnalysis,
    FrameSections,
    LoadDirection,
    MemberSectionSpec,
    ModelSettings,
    build_layout,
)
from bimsteelauto.analysis.frame import (
    FrameLayout,
    FrameMember,
    design_moment_factor,
)
from bimsteelauto.design.members import (
    BracingSpec,
    MemberDesignInput,
    check_member,
)
from bimsteelauto.sections import TaperedMember, WeldedI
from bimsteelauto.spec import Materials, MemberType, PEBGeometry

Pynite = pytest.importorskip("Pynite", reason="PyNiteFEA not installed")

MATS = Materials(
    Fy_builtup=345.0, Fu_builtup=450.0, Fy_cold_formed=345.0,
    Fu_cold_formed=450.0, bolt_grade="A325", anchor_grade="F1554 Gr.36",
    fc_concrete=30.0,
)
SEC = WeldedI.symmetric(d=500, tw=8, bf=200, tf=12)


def _geom():
    return PEBGeometry.from_m(
        span_widths=[24.0], bay_spacings=[6.0] * 4, eave_height=6.0,
        roof_slope=0.10, purlin_spacing=1.5, girt_spacing=1.5,
    )


def _flexure_note(design) -> str:
    return next(c.note for c in design.checks if c.name.startswith("Flexure"))


def test_pynite_itself_reports_sagging_negative():
    """The premise, straight from PyNite: no bimsteelauto code involved."""
    m = Pynite.FEModel3D()
    m.add_node("A", 0, 0, 0)
    m.add_node("B", 10000, 0, 0)
    m.add_material("S", 200000, 77000, 0.3, 7.85e-8)
    m.add_section("s", 5000, 1e7, 1e8, 1e6)
    m.add_member("M", "A", "B", "S", "s")
    m.def_support("A", True, True, True, True, False, False)
    m.def_support("B", False, True, True, False, False, False)
    m.add_member_dist_load("M", "FY", -10, -10, case="D")
    m.add_load_combo("D", {"D": 1.0})
    m.analyze_linear()
    assert m.members["M"].moment("Mz", 5000, "D") / 1e6 == pytest.approx(-125.0)


def test_simple_beam_sags_top_flange_in_compression():
    """A 10 m beam pinned at both ends, 10 kN/m: midspan +125 kN.m (wL^2/8)
    in the design sign; the flexure check reports the TOP flange's Lb (1 m
    purlins), not the unbraced bottom flange."""
    L = 10000.0
    taper = TaperedMember(SEC, SEC, L)
    lay = FrameLayout(
        members=(FrameMember("B1", MemberType.RAFTER, taper, (0.0, 0.0), (L, 0.0)),),
        nodes={"A": (0.0, 0.0), "B": (L, 0.0)},
        base_nodes=("A", "B"),
        eave_nodes=(),
        ridge_nodes=(),
        geometry=_geom(),
        settings=ModelSettings(base_fixity=BaseFixity.PINNED),
    )
    fa = FrameAnalysis(lay)
    fa.add_line_load("D", "B1", 10.0, LoadDirection.GLOBAL_Y)
    fa.add_combination("1.0D", {"D": 1.0})
    env = fa.solve(p_delta=False)
    sts = env.stations["B1"]["1.0D"]
    mid = min(sts, key=lambda s: abs(s.x - L / 2))
    assert mid.M == pytest.approx(125.0, rel=1e-3)
    design = check_member(MemberDesignInput(
        mark="B1", member_type=MemberType.RAFTER, taper=taper,
        forces={"1.0D": [s.to_member_forces() for s in sts]},
        bracing=BracingSpec.at_spacing(L, 1000.0, None), materials=MATS,
    ))
    assert "(top flange)" in _flexure_note(design)
    assert "Lb = 1000 mm" in _flexure_note(design)


def test_haunch_under_gravity_bottom_flange_in_compression():
    """Pinned portal, gravity on the rafters: hogging at the haunch and the
    knee - the rafter's bottom flange and each column's inner flange are in
    compression, so M < 0 there on the rafter and on BOTH columns; the check
    at the knee uses the BOTTOM flange's bracing (fly braces at L/3), not the
    purlins'."""
    deep = WeldedI.symmetric(d=800, tw=8, bf=250, tf=12)
    shallow = WeldedI.symmetric(d=400, tw=8, bf=250, tf=12)
    lay = build_layout(_geom(), FrameSections(
        column=MemberSectionSpec(shallow, deep), rafter=MemberSectionSpec(deep, shallow)))
    fa = FrameAnalysis(lay)
    for m in lay.members:
        if m.member_type is MemberType.RAFTER:
            fa.add_line_load("D", m.mark, 8.0, LoadDirection.GLOBAL_Y_PROJECTED)
    fa.add_combination("1.0D", {"D": 1.0})
    env = fa.solve(p_delta=False)
    by_type = {}
    for m in lay.members:
        by_type.setdefault(m.member_type, []).append(m)
    for r in by_type[MemberType.RAFTER]:
        sts = env.stations[r.mark]["1.0D"]
        knee_end = min(sts, key=lambda s: s.x)                 # rafters run eave -> ridge
        assert knee_end.M < 0, r.mark
        assert max(s.M for s in sts) > 0, r.mark                # and they sag toward the ridge
    for c in by_type[MemberType.COLUMN]:
        top = max(env.stations[c.mark]["1.0D"], key=lambda s: s.x)
        assert top.M < 0, c.mark                                # left AND right column
    r = by_type[MemberType.RAFTER][0]
    L = r.length
    design = check_member(MemberDesignInput(
        mark=r.mark, member_type=MemberType.RAFTER, taper=r.taper,
        forces={"1.0D": [s.to_member_forces() for s in env.stations[r.mark]["1.0D"]]},
        bracing=BracingSpec.at_spacing(L, 1500.0, L / 3.0), materials=MATS,
    ))
    assert design.forces.Mz < 0                                 # the governing station hogs
    assert "(bottom flange)" in _flexure_note(design)


@pytest.mark.parametrize("mtype, i, j, expected", [
    (MemberType.RAFTER, (0.0, 0.0), (10.0, 1.0), -1.0),         # local y up = top
    (MemberType.RAFTER, (10.0, 1.0), (0.0, 0.0), -1.0),         # drawn backwards: still up
    (MemberType.COLUMN, (0.0, 0.0), (0.0, 6.0), -1.0),          # left, vertical: y = -X = outer
    (MemberType.COLUMN, (24.0, 0.0), (24.0, 6.0), 1.0),         # right, vertical: y = -X = INNER
    (MemberType.COLUMN, (24.2, 0.0), (24.0, 6.0), -1.0),        # right, leaning in: y up-ish = +X = outer
    (MemberType.INTERIOR_COLUMN, (12.0, 0.0), (12.0, 6.0), -1.0),
])
def test_factor_table(mtype, i, j, expected):
    assert design_moment_factor(mtype, i, j, 24.0) == expected
