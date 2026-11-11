"""ISO 898-1 property class 4.6 anchor rods (0.15.2, requested by Manafe3).

Hand values (not recomputed by the code under test):
    4.6: fya 240, futa 400 MPa; ACI 318-19 17.6.1.2 cap min(1.9 x 240 = 456, 862) = 456 >= 400 -> futa 400 kept.
    Not ductile (no reduction-of-area minimum in ISO 898-1): phi 0.65 steel tension, 0.60 steel shear (17.5.3).
    M20, Ase 245 mm^2, one rod (n = 1):
        Nsa = 245 x 400 = 98.0 kN;          phi Nsa = 0.65 x 98.0 = 63.70 kN
        Vsa = 0.6 x 245 x 400 = 58.8 kN;    phi Vsa = 0.60 x 58.8 = 35.28 kN   (no grout pad)
Negative control: F1554 Gr 36 (ductile) keeps phi 0.75: 0.75 x 245 x 400 = 73.50 kN.
"""

import pytest

from bimsteelauto_connections.anchors import (ANCHOR_GRADES, GRADE_SOURCES, AnchorGroup, check_anchors,
                                              get_anchor_grade)


def _single(grade, Nua=10.0, Vua=10.0):
    g = AnchorGroup(d=20.0, hef=300.0, n_x=1, n_y=1)          # no edges: the steel modes are what is read
    return check_anchors(Nua=Nua, Vua=Vua, group=g, grade=grade, fc_prime=30.0)


def _mode(rows, word):
    return next(c for n, c, u in rows if word in n.lower())


def test_grade_values_and_source():
    g = get_anchor_grade("4.6")
    assert (g.Fy, g.futa, g.ductile) == (240.0, 400.0, False)
    assert "4.6" in ANCHOR_GRADES and GRADE_SOURCES["4.6"]["checked_against_source"] is False


def test_steel_modes_by_hand():
    r = _single("4.6")
    assert _mode(r.tension, "steel") == pytest.approx(63.70, abs=0.01)
    assert _mode(r.shear, "steel") == pytest.approx(35.28, abs=0.01)


def test_negative_control_f1554_stays_ductile():
    r = _single("F1554-36")
    assert _mode(r.tension, "steel") == pytest.approx(73.50, abs=0.01)
