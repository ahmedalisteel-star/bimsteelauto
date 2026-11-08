"""AISC 360-16 Sect. E7 in every compression check (fix/e7-slender, 2026-11-08).

Until 0.14.3 ``_check_station`` built ``CompressionMember`` with no plate
elements, and pyaisc360 then takes every element as nonslender.  A PEB web
at h/tw ~ 97 is far past lambda_r = 1.49 sqrt(E/Fy) ~ 36, so the compressive
capacity was overstated.

Independent check: Sect. E3, E4 and E7 written out LONGHAND below (ksi,
in.) from the section's dimensions - none of it calls pyaisc360 or the
module under test:

    E3   Fe = pi^2 E / (Lc/r)^2; Fcr = 0.658^(Fy/Fe) Fy or 0.877 Fe      (E3-2/3/4)
    E4   Fe = (pi^2 E Cw / Lcz^2 + G J) / (Ix + Iy), doubly symmetric      (E4-2)
    E7   lambda_r: web 1.49 sqrt(E/Fy) (case 5); flange 0.64 sqrt(kc E/Fy),
         kc = 4/sqrt(h/tw) in [0.35, 0.76] (case 2)
         threshold lambda_r sqrt(Fy/Fcr)                                    (E7-2)
         Fel = (c2 lambda_r / lambda)^2 Fy                                  (E7-5)
         be = b (1 - c1 sqrt(Fel/Fcr)) sqrt(Fel/Fcr)                        (E7-3)
         c1 = 0.18 stiffened (web), 0.22 other (flanges)  (Table E7.1)
         c2 = (1 - sqrt(1 - 4 c1)) / (2 c1)                                 (E7-4)
         Ae = Ag - sum (b - be) t,  Pn = Fcr Ae,  phi = 0.90               (E7-1)

Negative control: the PEB web section must LOSE capacity against the old
value 0.90 Fcr Ag.  Two more controls: a stocky section is unchanged, and a
long column whose web is slender by Table B4.1a (lambda > lambda_r) but
under the E7-2 threshold keeps its full area.
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto.design import BracingSpec, MemberDesignInput, check_member
from bimsteelauto.design.units import kip_to_kn, mm2_to_in2, mm4_to_in4, mm_to_in, mpa_to_ksi
from bimsteelauto.sections import TaperedMember, WeldedI
from bimsteelauto.spec import Materials, MemberForces, MemberType

pytest.importorskip("pyaisc360", reason="pyaisc360 not on sys.path")

E, G = 29000.0, 11200.0
FY_MPA = 345.0
FY = mpa_to_ksi(FY_MPA)
MATERIALS = Materials(Fy_builtup=FY_MPA, Fu_builtup=450.0, Fy_cold_formed=345.0, Fu_cold_formed=450.0,
                      bolt_grade="A325", anchor_grade="F1554 Gr.36", fc_concrete=30.0)

PEB_WEB = WeldedI.symmetric(d=800, tw=8, bf=250, tf=12)        # h/tw = 776/8 = 97
STOCKY = WeldedI.symmetric(d=400, tw=12, bf=200, tf=16)        # h/tw = 30.7, b/t = 6.25
MODERATE = WeldedI.symmetric(d=400, tw=8, bf=200, tf=16)       # h/tw = 46 > lambda_r = 35.9


def _check(section, L, Lb):
    taper = TaperedMember(start=section, end=section, length=L)
    n = int(round(L / Lb))
    braces = tuple(Lb * i for i in range(1, n))
    stations = [MemberForces(P=100.0, Vy=1.0, Mz=1.0, station=L * i / 8) for i in range(9)]
    d = check_member(MemberDesignInput(mark="C1", member_type=MemberType.COLUMN, taper=taper, forces={"c": stations},
                                       bracing=BracingSpec(top_flange=braces, bottom_flange=braces),
                                       materials=MATERIALS))
    return next(c for c in d.checks if c.name.startswith("Compression"))


def _fcr(Fe):
    return 0.658 ** (FY / Fe) * FY if FY / Fe <= 2.25 else 0.877 * Fe


def longhand(s: WeldedI, Lcx, Lcy):
    """(phi Pn with E7, phi Fcr Ag without it), kN.  Properties from the plate dimensions."""
    d, tw, bf, tf = (mm_to_in(v) for v in (s.d, s.tw, s.bf_top, s.tf_top))
    h = d - 2 * tf
    Ag = 2 * bf * tf + h * tw
    Ix = tw * h ** 3 / 12 + 2 * (bf * tf ** 3 / 12 + bf * tf * ((h + tf) / 2) ** 2)
    Iy = 2 * tf * bf ** 3 / 12 + h * tw ** 3 / 12
    ho = h + tf
    Cw = Iy * ho ** 2 / 4
    rx, ry = math.sqrt(Ix / Ag), math.sqrt(Iy / Ag)
    # the longhand properties are the module's to within rounding (else the test compares different sections)
    assert Ag == pytest.approx(mm2_to_in2(s.A), rel=1e-9) and Iy == pytest.approx(mm4_to_in4(s.Iy), rel=1e-3)
    assert Ix == pytest.approx(mm4_to_in4(s.Ix), rel=1e-3)
    J = mm4_to_in4(s.J)                                      # torsion constant convention: the module's
    sl = max(mm_to_in(Lcx) / rx, mm_to_in(Lcy) / ry)
    Fcr_flex = _fcr(math.pi ** 2 * E / sl ** 2)
    Fcr_tors = _fcr((math.pi ** 2 * E * Cw / mm_to_in(Lcy) ** 2 + G * J) / (Ix + Iy))
    Fcr = min(Fcr_flex, Fcr_tors)

    def be(b, t, lam_r, c1):
        lam = b / t
        if lam <= lam_r * math.sqrt(FY / Fcr):
            return b
        c2 = (1 - math.sqrt(1 - 4 * c1)) / (2 * c1)
        Fel = (c2 * lam_r / lam) ** 2 * FY
        r = math.sqrt(Fel / Fcr)
        return b * (1 - c1 * r) * r

    kc = min(0.76, max(0.35, 4 / math.sqrt(h / tw)))
    lr_web = 1.49 * math.sqrt(E / FY)
    lr_fl = 0.64 * math.sqrt(kc * E / FY)
    Ae = Ag - (h - be(h, tw, lr_web, 0.18)) * tw - 2 * (bf / 2 - be(bf / 2, tf, lr_fl, 0.22)) * tf
    return kip_to_kn(0.90 * Fcr * Ae), kip_to_kn(0.90 * Fcr * Ag), Ae / Ag


def test_table_e7_1_c2_reproduced():
    for c1, c2 in ((0.18, 1.31), (0.22, 1.49)):
        assert (1 - math.sqrt(1 - 4 * c1)) / (2 * c1) == pytest.approx(c2, abs=0.005)


@pytest.mark.parametrize("L,Lb", [(6000.0, 1500.0), (6000.0, 3000.0)])
def test_peb_web_loses_capacity_negative_control(L, Lb):
    c = _check(PEB_WEB, L, Lb)
    new, old, ratio = longhand(PEB_WEB, L, Lb)
    assert c.capacity == pytest.approx(new, rel=1e-3)
    assert ratio < 0.85                                       # a real E7 reduction, not rounding
    assert c.capacity < 0.85 * old                            # the pre-0.14.3 value overstated it
    assert "E7-1" in c.reference and "Ae/Ag" in c.note


def test_stocky_section_unchanged():
    c = _check(STOCKY, 6000.0, 1500.0)
    new, old, ratio = longhand(STOCKY, 6000.0, 1500.0)
    assert ratio == 1.0 and c.capacity == pytest.approx(old, rel=1e-3)
    assert "fully effective" in c.note


def test_slender_by_b4_but_under_the_e7_threshold_keeps_full_area():
    lam_r = 1.49 * math.sqrt(E / FY)
    assert MODERATE.lambda_web > lam_r                        # slender by Table B4.1a
    c = _check(MODERATE, 12000.0, 6000.0)                     # long column: Fcr well below Fy
    new, old, ratio = longhand(MODERATE, 12000.0, 6000.0)
    assert ratio == 1.0 and c.capacity == pytest.approx(old, rel=1e-3)
    # ... and the same section short DOES lose area (the threshold is what saved it)
    short = _check(MODERATE, 1500.0, 500.0)
    new_s, old_s, ratio_s = longhand(MODERATE, 1500.0, 500.0)
    assert ratio_s < 1.0 and short.capacity == pytest.approx(new_s, rel=1e-3)


def test_singly_symmetric_flanges_taken_separately():
    s = WeldedI(d=800, tw=8, bf_top=300, tf_top=10, bf_bot=200, tf_bot=12)
    c = _check(s, 6000.0, 1500.0)
    assert "top flange" in c.note                             # b/t = 15: slender; bottom 8.3 is not
    assert "bottom flange" not in c.note
