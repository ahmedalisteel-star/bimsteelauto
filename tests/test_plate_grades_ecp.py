"""ECP 205-LRFD plate grades St 37 / St 44 / St 52 (0.15.2, requested by Manafe3).

Hand values: ECP 205 Table 1.1 in t/cm2 (the ecp205-lrfd library's materials.STEEL_GRADES), x 98.0665:
    St 37  t <= 40: 2.40 / 3.70 -> 235.36 / 362.85;  40 < t <= 100: 2.15 / 3.40 -> 210.84 / 333.43
    St 44  t <= 40: 2.80 / 4.40 -> 274.59 / 431.49;  40 < t <= 100: 2.55 / 4.10 -> 250.07 / 402.07
    St 52  t <= 40: 3.60 / 5.20 -> 353.04 / 509.95;  40 < t <= 100: 3.35 / 4.90 -> 328.52 / 480.53
Negative control: 120 mm is beyond the table -> refused; an unknown name is refused.
"""

import pytest

from bimsteelauto_connections.bolts import GeometryError, GradeNotFound, get_grade


@pytest.mark.parametrize("name,t,fy,fu", [
    ("St 37", 20, 235.36, 362.85), ("St 37", 50, 210.84, 333.43),
    ("St 44", 40, 274.59, 431.49), ("St 44", 41, 250.07, 402.07),
    ("ST52", 25, 353.04, 509.95), ("st 52", 60, 328.52, 480.53),
])
def test_ecp_grades(name, t, fy, fu):
    assert get_grade(name, t) == (pytest.approx(fy, abs=0.005), pytest.approx(fu, abs=0.005))


def test_refusals():
    with pytest.raises(GeometryError):
        get_grade("St 37", 120)
    with pytest.raises(GradeNotFound):
        get_grade("St 60", 20)
