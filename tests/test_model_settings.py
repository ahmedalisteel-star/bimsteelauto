"""ModelSettings.stiffness_factor and ModelSettings.min_segments (feature/stiffness-factor, 2026-11-08).

stiffness_factor - every stiffness times k: a frame scaled uniformly carries the SAME forces and moves exactly 1/k as
far (linear), so the factor reached every element and nothing else changed.

min_segments - P-delta.  A braced, pin-ended column under axial load P and a small uniform lateral load w: the
midheight moment is amplified by the member's own curvature (P-delta; there is no sway, so no P-Delta).  Independent
hand values:

    first order     M1 = w L^2 / 8
    AISC B1         B1 = Cm / (1 - P/Pe1), Cm = 1.0 (transverse load), Pe1 = pi^2 k E I / L^2   (App. 8, Eq. A-8-3)
    exact           M = M1 * 2 (sec u - 1) / u^2,  u = (pi / 2) sqrt(P / Pe1)   (beam-column, uniform load)

At P/Pe1 = 0.30: u = 0.86036, sec u = 1.53336, exact factor 2 x 0.53336 / 0.74022 = 1.4411; B1 = 1.4286 (0.9 % apart).
Measured (PyNite 3.0, this column): 1 element 1.225, 2 -> 1.355, 4 -> 1.433, 8 -> 1.441.  With 4 elements the moment
must land within 2 % of B1 (and 1 % of exact); ONE element under-predicts by 15 % - the negative control (PyNite's own
member moment adds part of the curvature, so it is not 1.0, but it is far short).
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto.analysis import BaseFixity, FrameAnalysis, LoadDirection, ModelSettings
from bimsteelauto.analysis.frame import FrameLayout, FrameMember, build_layout, FrameSections, MemberSectionSpec
from bimsteelauto.sections import TaperedMember, WeldedI
from bimsteelauto.spec import E_STEEL, MemberType, PEBGeometry

pytest.importorskip("Pynite", reason="PyNiteFEA not installed")

L = 6000.0
SEC = WeldedI.symmetric(d=300, tw=8, bf=200, tf=12)
RIGID = WeldedI.symmetric(d=1500, tw=60, bf=800, tf=80)


def _geom():
    return PEBGeometry.from_m(span_widths=[12.0], bay_spacings=[6.0], eave_height=6.0, roof_slope=0.1,
                              purlin_spacing=1.5, girt_spacing=1.5)


def _column(settings: ModelSettings) -> FrameLayout:
    """Column C (B -> T), pinned base, pin top (INTERIOR_COLUMN: its top is released); braced at T by a rigid beam T -> R,
    pinned at R. A gravity load on the beam reaches the column as an axial force w Lb / 2, exactly."""
    col = FrameMember("C", MemberType.INTERIOR_COLUMN, TaperedMember(SEC, SEC, L), (0.0, 0.0), (0.0, L))
    beam = FrameMember("BM", MemberType.RAFTER, TaperedMember(RIGID, RIGID, L), (0.0, L), (L, L))
    return FrameLayout(members=(col, beam), nodes={"B": (0.0, 0.0), "T": (0.0, L), "R": (L, L)}, base_nodes=("B", "R"),
                       eave_nodes=("T",), ridge_nodes=(), geometry=_geom(), settings=settings)


def _amplified(settings: ModelSettings, p_over_pe: float, w_lat: float = 0.5):
    Pe = math.pi ** 2 * settings.stiffness_factor * E_STEEL * SEC.Ix / L ** 2 / 1000.0     # kN
    P = p_over_pe * Pe
    fa = FrameAnalysis(_column(settings))
    fa.add_line_load("G", "BM", 2.0 * P / (L / 1000.0), LoadDirection.GLOBAL_Y)           # kN/m -> P at T
    fa.add_line_load("W", "C", w_lat, LoadDirection.GLOBAL_X)
    fa.add_combination("c", {"G": 1.0, "W": 1.0})
    env = fa.solve(p_delta=True)
    sts = env.stations["C"]["c"]
    mid = min(sts, key=lambda s: abs(s.x - L / 2))
    top = max(sts, key=lambda s: s.x)
    M1 = w_lat * (L / 1000.0) ** 2 / 8.0
    return abs(mid.M), M1, P, top.P


@pytest.mark.parametrize("k", [1.0, 0.8])
def test_p_delta_with_four_elements_matches_b1(k):
    M, M1, P, P_top = _amplified(ModelSettings(base_fixity=BaseFixity.PINNED, min_segments=4, stiffness_factor=k), 0.30)
    assert P_top == pytest.approx(P, rel=1e-3)                    # the axial load arrived as built
    B1 = 1.0 / (1.0 - 0.30)
    u = math.pi / 2 * math.sqrt(0.30)
    exact = 2 * (1 / math.cos(u) - 1) / u ** 2
    assert exact == pytest.approx(1.4411, abs=1e-4) and B1 == pytest.approx(1.42857, abs=1e-5)
    assert M == pytest.approx(B1 * M1, rel=0.02)
    assert M == pytest.approx(exact * M1, rel=0.01)


def test_one_element_misses_p_delta_negative_control():
    M, M1, _, _ = _amplified(ModelSettings(base_fixity=BaseFixity.PINNED, min_segments=1), 0.30)
    assert M < 0.90 * 1.4411 * M1                                 # more than 10 % short of the exact amplification


def test_stiffness_factor_scales_movement_not_forces():
    deep = WeldedI.symmetric(d=800, tw=8, bf=250, tf=12)
    shallow = WeldedI.symmetric(d=400, tw=8, bf=250, tf=12)
    secs = FrameSections(column=MemberSectionSpec(shallow, deep), rafter=MemberSectionSpec(deep, shallow))
    out = {}
    for k in (1.0, 0.8):
        lay = build_layout(_geom(), secs, ModelSettings(stiffness_factor=k))
        fa = FrameAnalysis(lay)
        col = next(m for m in lay.members if m.member_type is MemberType.COLUMN)
        fa.add_line_load("W", col.mark, 2.0, LoadDirection.GLOBAL_X)
        fa.add_combination("W", {"W": 1.0})
        out[k] = (lay, fa.solve(p_delta=False))
    (lay, e1), (_, e8) = out[1.0], out[0.8]
    for n in lay.nodes:
        for i in (0, 1):
            assert e8.displacements[n]["W"][i] == pytest.approx(e1.displacements[n]["W"][i] / 0.8, rel=1e-6, abs=1e-12)
    for b in lay.base_nodes:
        for i in (0, 1):
            assert e8.reactions[b]["W"][i] == pytest.approx(e1.reactions[b]["W"][i], rel=1e-6, abs=1e-9)


def test_settings_validated_and_defaults_unchanged():
    s = ModelSettings()
    assert s.stiffness_factor == 1.0 and s.min_segments == 1 and s.problems() == []
    assert "stiffness_factor must lie in (0, 1]" in ModelSettings(stiffness_factor=0.0).problems()
    assert "stiffness_factor must lie in (0, 1]" in ModelSettings(stiffness_factor=1.2).problems()
    assert "min_segments must be at least 1" in ModelSettings(min_segments=0).problems()
    lay = _column(ModelSettings(min_segments=4))
    fa = FrameAnalysis(lay)
    fa.add_line_load("W", "C", 1.0, LoadDirection.GLOBAL_X)
    fa.add_combination("W", {"W": 1.0})
    fa.solve(p_delta=False)
    assert len(fa._segments["C"]) == 4 and len(fa._segments["BM"]) == 4     # constant members split too
