"""Thin PEB webs in flexure and shear: AISC 360-16 F4, F5 and G2.1 (no tension field), longhand.

The verification of 2026-09-01 compared a COMPACT section only (Sect. F2), because the second implementation it used
has no F4 / F5.  A PEB web is never compact: h/tw ~ 100 is NONCOMPACT (F4, 3.76 < h/tw / sqrt(E/Fy) <= 5.70) and
h/tw ~ 160 is SLENDER (F5).  Each is written out here longhand in ksi / in. from the plate dimensions, with no call to
pyaisc360 or to the module under test, and compared with ``check_member`` (2026-11-08, Manafe3 Phase 4 step 3):

    F4   Myc = Fy Sxc; Mp = min(Fy Zx, 1.6 Fy Sx); Rpc (F4-9b); FL = 0.7 Fy (F4-6a); aw, rt (F4-12, F4-11)
         Lp = 1.1 rt sqrt(E/Fy) (F4-7); Lr (F4-8); LTB F4-2 / F4-3 with Fcr F4-5; FLB F4-13 / F4-14; Mn = min
    F5   Rpg (F5-6, aw <= 10); CFY Rpg Fy Sxc (F5-1); Lr = pi rt sqrt(E / 0.7 Fy) (F5-5); LTB F5-3 / F5-4;
         FLB F5-8 / F5-9
    G2.1 Vn = 0.6 Fy Aw Cv1, Aw = d tw; kv = 5.34 (no stiffeners); Cv1 (G2-3 / G2-4); phi = 0.90 (welded)

Negative controls: the slender-web section's flexural capacity is far below Mp (the F2 plastic value a compact-only
check would give), and the web shear capacity is below 0.6 Fy Aw (shear buckling governs, not yielding).
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto.design import BracingSpec, MemberDesignInput, check_member
from bimsteelauto.design.units import kip_to_kn, kipin_to_knm, mm_to_in, mpa_to_ksi
from bimsteelauto.sections import TaperedMember, WeldedI
from bimsteelauto.spec import Materials, MemberForces, MemberType

pytest.importorskip("pyaisc360", reason="pyaisc360 not on sys.path")

E = 29000.0
FY_MPA = 345.0
FY = mpa_to_ksi(FY_MPA)
MATERIALS = Materials(Fy_builtup=FY_MPA, Fu_builtup=450.0, Fy_cold_formed=345.0, Fu_cold_formed=450.0,
                      bolt_grade="A325", anchor_grade="F1554 Gr.36", fc_concrete=30.0)

NONCOMPACT = WeldedI.symmetric(d=800, tw=8, bf=250, tf=12)     # h/tw = 97.0: F4
SLENDER = WeldedI.symmetric(d=1000, tw=6, bf=250, tf=12)       # h/tw = 162.7: F5


def _design(section, L, Lb, M=50.0, V=50.0):
    taper = TaperedMember(start=section, end=section, length=L)
    n = int(round(L / Lb))
    braces = tuple(Lb * i for i in range(1, n))
    sts = [MemberForces(P=0.0, Vy=V, Mz=M, station=L * i / 8) for i in range(9)]   # uniform moment: Cb = 1
    return check_member(MemberDesignInput(mark="R1", member_type=MemberType.RAFTER, taper=taper, forces={"c": sts},
                                          bracing=BracingSpec(top_flange=braces, bottom_flange=braces),
                                          materials=MATERIALS))


def _cap(d, prefix):
    return next(c for c in d.checks if c.name.startswith(prefix))


def props(s: WeldedI):
    """Doubly symmetric welded I, from the plates (in.)."""
    d, tw, bf, tf = (mm_to_in(v) for v in (s.d, s.tw, s.bf_top, s.tf_top))
    h = d - 2 * tf
    ho = d - tf
    Ix = tw * h ** 3 / 12 + 2 * (bf * tf ** 3 / 12 + bf * tf * (ho / 2) ** 2)
    Sx = Ix / (d / 2)
    Zx = 2 * bf * tf * ho / 2 + tw * h ** 2 / 4
    Iy = 2 * tf * bf ** 3 / 12 + h * tw ** 3 / 12
    J = (2 * bf * tf ** 3 + ho * tw ** 3) / 3
    return dict(d=d, tw=tw, bf=bf, tf=tf, h=h, ho=ho, Sx=Sx, Zx=Zx, Iy=Iy, J=J)


def _flb_classes(p):
    lam_f = p["bf"] / (2 * p["tf"])
    kc = min(0.76, max(0.35, 4 / math.sqrt(p["h"] / p["tw"])))
    return lam_f, kc, 0.38 * math.sqrt(E / FY), 0.95 * math.sqrt(kc * E / (0.7 * FY))


def f4_longhand(s, Lb):
    p = props(s)
    Sx, hc, tw, bfc, tfc = p["Sx"], p["h"], p["tw"], p["bf"], p["tf"]
    lam_w = hc / tw
    lam_pw, lam_rw = 3.76 * math.sqrt(E / FY), 5.70 * math.sqrt(E / FY)
    assert lam_pw < lam_w <= lam_rw                                    # noncompact web: F4 is the right section
    Myc = FY * Sx
    Mp = min(FY * p["Zx"], 1.6 * FY * Sx)
    Rpc = min(Mp / Myc - (Mp / Myc - 1) * (lam_w - lam_pw) / (lam_rw - lam_pw), Mp / Myc)   # F4-9b
    FL = 0.7 * FY
    aw = hc * tw / (bfc * tfc)
    rt = bfc / math.sqrt(12 * (1 + aw / 6))
    Lp = 1.1 * rt * math.sqrt(E / FY)
    jj = p["J"] / (Sx * p["ho"])
    Lr = 1.95 * rt * E / FL * math.sqrt(jj + math.sqrt(jj ** 2 + 6.76 * (FL / E) ** 2))
    Lb = mm_to_in(Lb)
    cands = {"CFY": Rpc * Myc}
    if Lb > Lp:
        if Lb <= Lr:
            cands["LTB"] = min(Rpc * Myc - (Rpc * Myc - FL * Sx) * (Lb - Lp) / (Lr - Lp), Rpc * Myc)
        else:
            Fcr = math.pi ** 2 * E / (Lb / rt) ** 2 * math.sqrt(1 + 0.078 * jj * (Lb / rt) ** 2)
            cands["LTB"] = min(Fcr * Sx, Rpc * Myc)
    lam_f, kc, lpf, lrf = _flb_classes(p)
    if lam_f > lpf:
        cands["FLB"] = (Rpc * Myc - (Rpc * Myc - FL * Sx) * (lam_f - lpf) / (lrf - lpf) if lam_f <= lrf
                        else 0.9 * E * kc * Sx / lam_f ** 2)
    return cands, dict(Lp=Lp, Lr=Lr, Rpc=Rpc, Mp=Mp)


def f5_longhand(s, Lb):
    p = props(s)
    Sx, hc, tw, bfc, tfc = p["Sx"], p["h"], p["tw"], p["bf"], p["tf"]
    assert hc / tw > 5.70 * math.sqrt(E / FY)                          # slender web: F5
    aw = min(hc * tw / (bfc * tfc), 10.0)
    rt = bfc / math.sqrt(12 * (1 + hc * tw / (bfc * tfc) / 6))
    Rpg = min(1.0, 1 - aw / (1200 + 300 * aw) * (hc / tw - 5.7 * math.sqrt(E / FY)))
    cands = {"CFY": Rpg * FY * Sx}
    Lp = 1.1 * rt * math.sqrt(E / FY)
    Lr = math.pi * rt * math.sqrt(E / (0.7 * FY))
    Lb = mm_to_in(Lb)
    if Lb > Lp:
        Fcr = min(FY - 0.3 * FY * (Lb - Lp) / (Lr - Lp), FY) if Lb <= Lr else min(math.pi ** 2 * E / (Lb / rt) ** 2, FY)
        cands["LTB"] = Rpg * Fcr * Sx
    lam_f, kc, lpf, lrf = _flb_classes(p)
    if lam_f > lpf:
        Fcr = FY - 0.3 * FY * (lam_f - lpf) / (lrf - lpf) if lam_f <= lrf else 0.9 * E * kc / lam_f ** 2
        cands["FLB"] = Rpg * Fcr * Sx
    return cands, dict(Lp=Lp, Lr=Lr, Rpg=Rpg, Mp=min(FY * p["Zx"], 1.6 * FY * Sx))


def g2_longhand(s):
    p = props(s)
    kv = 5.34
    lim = 1.10 * math.sqrt(kv * E / FY)
    lam = p["h"] / p["tw"]
    Cv1 = 1.0 if lam <= lim else lim / lam
    return kip_to_kn(0.90 * 0.6 * FY * p["d"] * p["tw"] * Cv1), Cv1, kip_to_kn(0.90 * 0.6 * FY * p["d"] * p["tw"])


@pytest.mark.parametrize("Lb", [1000.0, 3000.0, 9000.0])               # Lb < Lp, Lp < Lb <= Lr, Lb > Lr
def test_noncompact_web_f4_longhand(Lb):
    cands, info = f4_longhand(NONCOMPACT, Lb)
    zone = "below Lp" if mm_to_in(Lb) <= info["Lp"] else ("inelastic" if mm_to_in(Lb) <= info["Lr"] else "elastic")
    c = _cap(_design(NONCOMPACT, 9000.0 if Lb != 9000.0 else 18000.0, Lb), "Flexure")
    assert "F4" in c.reference, c.reference
    assert c.capacity == pytest.approx(0.90 * kipin_to_knm(min(cands.values())), rel=2e-3), (zone, cands)


def test_f4_zones_are_the_ones_intended():
    _, info = f4_longhand(NONCOMPACT, 1000.0)
    assert mm_to_in(1000.0) < info["Lp"] < mm_to_in(3000.0) < info["Lr"] < mm_to_in(9000.0)


@pytest.mark.parametrize("Lb", [1000.0, 3000.0, 9000.0])
def test_slender_web_f5_longhand(Lb):
    cands, info = f5_longhand(SLENDER, Lb)
    c = _cap(_design(SLENDER, 9000.0 if Lb != 9000.0 else 18000.0, Lb), "Flexure")
    assert "F5" in c.reference, c.reference
    assert c.capacity == pytest.approx(0.90 * kipin_to_knm(min(cands.values())), rel=2e-3), cands


def test_f5_zones_and_rpg():
    _, info = f5_longhand(SLENDER, 1000.0)
    assert mm_to_in(1000.0) < info["Lp"] < mm_to_in(3000.0) < info["Lr"] < mm_to_in(9000.0)
    assert 0.9 < info["Rpg"] < 1.0                                     # Rpg bites (a real reduction)


def test_slender_web_negative_control_far_below_plastic():
    """A compact-only (F2) check gives Mp at Lb < Lp; the slender web cannot reach it."""
    _, info = f5_longhand(SLENDER, 1000.0)
    c = _cap(_design(SLENDER, 9000.0, 1000.0), "Flexure")
    assert c.capacity < 0.90 * 0.90 * kipin_to_knm(info["Mp"])


@pytest.mark.parametrize("section", [NONCOMPACT, SLENDER], ids=["h/tw 97", "h/tw 163"])
def test_web_shear_g21_without_tension_field(section):
    Vc, Cv1, Vy = g2_longhand(section)
    c = _cap(_design(section, 6000.0, 1500.0), "Shear")
    assert Cv1 < 1.0                                                   # shear buckling governs
    assert c.capacity == pytest.approx(Vc, rel=2e-3)
    assert c.capacity < 0.99 * Vy                                      # negative control: not web yielding
    assert "G2-1" in c.reference or "G2.1" in c.reference, c.reference
