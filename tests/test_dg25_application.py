"""check_member(stability="dg25") - the AISC Design Guide 25 approach, NOT VERIFIED (2026-11-08).

What can be pinned without the guide:

    reduction     on a PRISMATIC member the approach must give AISC's own answer: uniform moment (Cb = 1) in every
                  LTB zone, and axial compression (E3 / E4 / E7) - to 0.1 %
    gradient      moment to zero on a prismatic beam: in the elastic LTB range it carries the exact moment-gradient
                  benefit (Cb ~ 1.75, against F1-1's 1.67); in the inelastic range it is never above AISC's
                  Cb x (F4-2) by more than that difference - the conservative side
    chain         on a TAPERED column the capacity at a station = 0.90 Fcr Ae, with gamma_e from the solver taken
                  directly, Fcr from E3 longhand, Ae from the (separately verified) E7 helper
    two methods   the governing station carries a Ritz cross-check within 1 %
    refusals      K_in_plane != 1, an unknown stability keyword
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto.design import BracingSpec, DesignError, MemberDesignInput, check_member
from bimsteelauto.design.members import _e7_effective_area
from bimsteelauto.design.nonprismatic import VERIFIED, in_plane_buckling, out_of_plane_buckling
from bimsteelauto.design.units import kip_to_kn, ksi_to_mpa, mpa_to_ksi
from bimsteelauto.sections import TaperedMember, WeldedI
from bimsteelauto.spec import Materials, MemberForces, MemberType

pytest.importorskip("pyaisc360", reason="pyaisc360 not on sys.path")

MAT = Materials(Fy_builtup=345.0, Fu_builtup=450.0, Fy_cold_formed=345.0, Fu_cold_formed=450.0,
                bolt_grade="A325", anchor_grade="F1554 Gr.36", fc_concrete=30.0)
E_KSI, G_KSI = 29000.0, 11200.0
FY = mpa_to_ksi(345.0)
SEC = WeldedI.symmetric(d=800, tw=8, bf=250, tf=12)              # noncompact web: F4


def _run(taper, stations, Lb, stability, **kw):
    L = taper.length
    n = max(1, int(round(L / Lb)))
    br = tuple(L * i / n for i in range(1, n))
    return check_member(MemberDesignInput(mark="M", member_type=MemberType.RAFTER, taper=taper, forces={"c": stations},
                                          bracing=BracingSpec(top_flange=br, bottom_flange=br), materials=MAT,
                                          stability=stability, **kw))


def _cap(d, prefix):
    return next(c for c in d.checks if c.name.startswith(prefix))


def test_flags_false_and_marked_on_the_sheet():
    assert not any(VERIFIED.values())
    pr = TaperedMember(start=SEC, end=SEC, length=6000.0)
    d = _run(pr, [MemberForces(P=100.0, Vy=1.0, Mz=100.0, station=6000.0 * i / 8) for i in range(9)], 3000.0, "dg25")
    assert "NOT VERIFIED" in _cap(d, "Flexure").reference and "NOT VERIFIED" in _cap(d, "Compression").reference


@pytest.mark.parametrize("Lb", [1000.0, 3000.0, 9000.0])            # below Lp, inelastic, elastic
def test_prismatic_uniform_moment_reduces_to_aisc(Lb):
    L = Lb * (2 if Lb == 9000.0 else 6)
    pr = TaperedMember(start=SEC, end=SEC, length=L)
    sts = [MemberForces(P=0.0, Vy=1.0, Mz=100.0, station=L * i / 8) for i in range(9)]
    a = _cap(_run(pr, sts, Lb, "prismatic"), "Flexure").capacity
    b = _cap(_run(pr, sts, Lb, "dg25"), "Flexure").capacity
    assert b == pytest.approx(a, rel=1e-3)


@pytest.mark.parametrize("sec", [WeldedI.symmetric(d=400, tw=10, bf=200, tf=14),          # compact web: F2
                                 WeldedI.symmetric(d=1000, tw=6, bf=250, tf=12),           # slender web: F5
                                 WeldedI(d=800, tw=8, bf_top=300, tf_top=12, bf_bot=200, tf_bot=10)],   # F4, singly
                         ids=["F2", "F5", "F4 singly"])
@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_prismatic_uniform_moment_reduces_to_aisc_every_branch(sec, sign):
    L = 9000.0
    pr = TaperedMember(start=sec, end=sec, length=L)
    sts = [MemberForces(P=0.0, Vy=1.0, Mz=sign * 100.0, station=L * i / 8) for i in range(9)]
    for Lb in (3000.0, 9000.0):
        a = _cap(_run(pr, sts, Lb, "prismatic"), "Flexure").capacity
        b = _cap(_run(pr, sts, Lb, "dg25"), "Flexure").capacity
        assert b == pytest.approx(a, rel=1e-3), Lb


@pytest.mark.parametrize("Lb", [1500.0, 3000.0, 6000.0])
def test_prismatic_axial_reduces_to_aisc(Lb):
    L = 6000.0
    pr = TaperedMember(start=SEC, end=SEC, length=L)
    sts = [MemberForces(P=300.0, Vy=1.0, Mz=1.0, station=L * i / 8) for i in range(9)]
    a = _cap(_run(pr, sts, Lb, "prismatic"), "Compression").capacity
    b = _cap(_run(pr, sts, Lb, "dg25"), "Compression").capacity
    assert b == pytest.approx(a, rel=1e-3)


@pytest.mark.parametrize("Lb,zone", [(12000.0, "elastic"), (3500.0, "inelastic")])
def test_moment_gradient_conservative_in_the_inelastic_range(Lb, zone):
    pr = TaperedMember(start=SEC, end=SEC, length=Lb)
    sts = [MemberForces(P=0.0, Vy=1.0, Mz=100.0 * (1 - i / 8), station=Lb * i / 8) for i in range(9)]
    # Utilisations, not capacities: under DG 25 the capacity varies along the member with fr (in the elastic range
    # every station has the same ratio 1/(phi gamma)), so the governing station need not be the peak moment.
    aisc = _cap(_run(pr, sts, Lb, "prismatic"), "Flexure").ratio          # Cb = 1.67 by F1-1
    dg = _cap(_run(pr, sts, Lb, "dg25"), "Flexure").ratio
    if zone == "elastic":
        assert 1.0 < aisc / dg < 1.15                                      # the exact gradient (Cb ~ 1.75-1.85)
    else:
        assert aisc / dg <= 1.08                                           # never much above AISC's Cb x (F4-2)


def test_tapered_column_chain_longhand():
    T = TaperedMember(start=WeldedI.symmetric(d=700, tw=6, bf=200, tf=10),
                      end=WeldedI.symmetric(d=300, tw=6, bf=200, tf=10), length=6000.0)
    L = T.length
    sts = [MemberForces(P=250.0, Vy=1.0, Mz=1.0, station=L * i / 8) for i in range(9)]
    d = _run(T, sts, 2000.0, "dg25")
    st = sts[0]                                                            # the deep end
    s = T.at(0.0)
    E, G = ksi_to_mpa(E_KSI), ksi_to_mpa(G_KSI)
    P = lambda x: 250.0e3
    gam = min(in_plane_buckling(T.at, P, 0.0, L, E=E), out_of_plane_buckling(T.at, 0.0, 2000.0, P=P, E=E, G=G))
    Fe = mpa_to_ksi(gam * st.P * 1e3 / s.A)
    Fcr = 0.658 ** (FY / Fe) * FY if FY / Fe <= 2.25 else 0.877 * Fe       # E3-2 / E3-3 longhand
    Ae, _ = _e7_effective_area(s, E_KSI, FY, Fcr)
    expected = kip_to_kn(0.90 * Fcr * Ae)
    from bimsteelauto.design.members import _Stability, _steel
    ctx = _Stability(MemberDesignInput(mark="M", member_type=MemberType.COLUMN, taper=T, forces={"c": sts},
                                       bracing=BracingSpec(), materials=MAT, stability="dg25"), _steel(MAT))
    ctx.bind("c", sts)
    from pyaisc360 import Basis
    c = ctx.compression(st, s, 0.0, 2000.0, E_KSI, FY, Basis.LRFD)
    assert c.capacity == pytest.approx(expected, rel=1e-6)
    # the taper is in it: in plane the tapered column is far from a prismatic one of the deep-end section
    lx = in_plane_buckling(T.at, P, 0.0, L, E=E)
    assert abs(lx / (math.pi ** 2 * E * s.Ix / L ** 2 / 250.0e3) - 1) > 0.10
    assert "DG 25" in _cap(d, "Compression").name                       # check_member took the same route


def test_ritz_cross_check_on_the_governing_station():
    T = TaperedMember(start=WeldedI.symmetric(d=1000, tw=8, bf=250, tf=12),
                      end=WeldedI.symmetric(d=450, tw=8, bf=250, tf=12), length=7000.0)
    L = T.length
    sts = [MemberForces(P=200.0, Vy=50.0, Mz=-400.0 * (1 - 1.2 * i / 8), station=L * i / 8) for i in range(9)]
    d = _run(T, sts, 3500.0, "dg25")
    for name in ("Flexure", "Compression"):
        c = _cap(d, name)
        assert "Ritz cross-check" in c.note, c.note
        pct = float(c.note.split("Ritz cross-check")[1].split("(")[1].split("%")[0])
        assert abs(pct) < 1.0


def test_refusals():
    pr = TaperedMember(start=SEC, end=SEC, length=6000.0)
    sts = [MemberForces(P=10.0, Vy=1.0, Mz=1.0, station=6000.0 * i / 8) for i in range(9)]
    with pytest.raises(DesignError, match="K = 1"):
        _run(pr, sts, 3000.0, "dg25", K_in_plane=1.2)
    with pytest.raises(DesignError, match="stability must be"):
        _run(pr, sts, 3000.0, "dg-25")
