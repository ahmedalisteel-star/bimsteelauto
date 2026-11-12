"""Fillet weld sizing, T-joint mode (0.15.3, requested by Manafe3).

``size_fillet_weld(joint="tee")``: the weld lies on the FACE of the through part (a flange or a web on an end plate),
so the AISC 360-16 Sect. J2.2b maximum (t - 2 mm along an EDGE) does not apply. ``min_leg_by="thinner"``: the Table
J2.4 minimum from the thinner part joined. Both default to the old behaviour ("edge", "thicker").

Hand values (not recomputed by the code under test); E70 (482 MPa), phi 0.75, Fnw = 0.60 FEXX (1 + 0.50 sin^1.5 theta),
throat 0.707 w; S355JR in this library: Fy 355, Fu 490 MPa; ONE weld line of a double fillet:
    A  a 6 mm web on a 20 mm plate, 100 kN along 300 mm (theta 0), 50 kN per line:
       per mm of leg 0.75 x 0.60 x 482 x 0.707 x 300 = 46 004.5 N -> 46.0045 kN / mm
       minimum by the thinner part (6 mm) = 3 mm: 3 x 46.0045 = 138.01 kN >= 50  -> leg 3, U = 50 / 138.01 = 0.3623
       minimum by the thicker part (20 mm) = 8 mm: 8 x 46.0045 = 368.04 kN       -> leg 8
       base metal 0.75 x 0.60 x 490 x 6 x 300 = 396.90 kN
       edge mode (the default): minimum 8 (thicker part) > maximum 6 - 2 = 4 mm  -> refused (the negative control);
       edge mode with the minimum by the thinner part: 3 <= 4                    -> leg 3 (it was the minimum that refused)
    B  a 200 x 8 flange (Fy 355) at full strength on a 20 mm plate, theta 90: 355 x 200 x 8 = 568 kN, 284 kN per line:
       per mm of leg 0.75 x 0.60 x 482 x 0.707 x 200 x 1.5 = 46.0045 kN / mm; 284 / 46.0045 = 6.173 -> leg 8, 368.04 kN
       base metal 0.75 x 0.60 x 490 x 8 x 200 = 352.80 kN governs the capacity: U = 284 / 352.80 = 0.8050
       edge mode: maximum 8 - 2 = 6 mm: 6 x 46.0045 = 276.03 < 284               -> refused with either minimum
    C  a 300 x 25 flange at full strength, 2662.5 kN, 1331.25 kN per line, palette up to 12 mm:
       12 x 0.75 x 0.60 x 482 x 0.707 x 300 x 1.5 = 828.08 kN < 1331.25          -> refused (a groove weld)
"""

import pytest

from bimsteelauto_connections.bolts import ConnectionError_, GeometryError
from bimsteelauto_connections.welds import size_fillet_weld

LEGS = (3.0, 5.0, 6.0, 8.0, 10.0, 12.0)
WEB = dict(demand=100.0, length=300.0, t_part1=6.0, t_part2=20.0, base_grade="S355JR", theta=0.0, palette=LEGS)
FLANGE = dict(demand=568.0, length=200.0, t_part1=8.0, t_part2=20.0, base_grade="S355JR", theta=90.0, palette=LEGS)


def test_tee_thin_web_on_thick_plate_by_hand():
    w = size_fillet_weld(joint="tee", min_leg_by="thinner", **WEB)
    assert w.leg == 3.0 and w.passed and w.demand == pytest.approx(50.0)
    assert w.capacity_weld == pytest.approx(138.01, abs=0.01)
    assert w.capacity_base == pytest.approx(396.90, abs=0.01)
    assert w.utilization == pytest.approx(0.3623, abs=1e-4) and w.governing == "weld metal"
    thick = size_fillet_weld(joint="tee", **WEB)                      # minimum still from the thicker part
    assert thick.leg == 8.0 and thick.capacity_weld == pytest.approx(368.04, abs=0.01)


def test_tee_full_strength_flange_by_hand():
    w = size_fillet_weld(joint="tee", min_leg_by="thinner", **FLANGE)
    assert w.leg == 8.0 and w.directional_factor == pytest.approx(1.5)
    assert w.capacity_weld == pytest.approx(368.04, abs=0.01)
    assert w.capacity_base == pytest.approx(352.80, abs=0.01) and w.governing == "base metal"
    assert w.capacity == pytest.approx(352.80, abs=0.01) and w.utilization == pytest.approx(0.8050, abs=1e-4)


def test_edge_mode_still_refuses_negative_control():
    for case in (WEB, FLANGE):
        with pytest.raises(GeometryError):
            size_fillet_weld(**case)                                   # the default: edge maximum t - 2, thicker-part minimum
    with pytest.raises(GeometryError):
        size_fillet_weld(joint="edge", min_leg_by="thinner", **FLANGE)  # the edge maximum itself: 6 mm is not enough
    assert size_fillet_weld(joint="edge", min_leg_by="thinner", **WEB).leg == 3.0


def test_tee_palette_is_the_upper_limit():
    with pytest.raises(GeometryError, match="palette"):
        size_fillet_weld(demand=2662.5, length=300.0, t_part1=25.0, t_part2=30.0, base_grade="S355JR", theta=90.0,
                         palette=LEGS, joint="tee", min_leg_by="thinner")


def test_defaults_unchanged_and_bad_options_refused():
    kw = dict(demand=300.0, length=400.0, t_part1=20.0, t_part2=12.0, base_grade="S355JR")
    assert size_fillet_weld(**kw) == size_fillet_weld(joint="edge", min_leg_by="thicker", **kw)
    assert size_fillet_weld(**kw).leg <= 12.0 - 2.0                    # J2.2b still applied by default
    with pytest.raises(ConnectionError_):
        size_fillet_weld(joint="lap", **kw)
    with pytest.raises(ConnectionError_):
        size_fillet_weld(min_leg_by="both", **kw)
