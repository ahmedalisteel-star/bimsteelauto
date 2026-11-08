"""Elastic buckling of nonprismatic members (design/nonprismatic.py) - the arithmetic under the DG 25 application.

Independent checks, none of them the module's own code:

    closed forms   Euler (prismatic, in plane); the exactly solvable column I = I1 (x/a)^4, pinned, for which
                   x^4 w'' + mu^2 w = 0 becomes g'' + mu^2 g = 0 in t = 1/x, w = x g, so Pcr = pi^2 E sqrt(I1 I2)/L^2
                   (derived longhand, 2026-11-08); AISC 360-16 Eq. E4-3 / E4-4 / E4-5 for a singly symmetric
                   section (flexural-torsional, y = axis of symmetry); uniform-moment LTB, doubly symmetric
                   Me = (pi/L) sqrt(E Iy G J + (pi E / L)^2 Iy Cw), and singly symmetric
                   Me = (pi^2 E Iy / L^2) [bx/2 + sqrt((bx/2)^2 + Cw/Iy (1 + G J L^2 / (pi^2 E Cw)))], bx > 0 with
                   the LARGER flange in compression (Trahair / Galambos)
    two methods    cubic Hermite FE vs sine-series Ritz agree on tapered members (no closed form exists there)
    sanity         bx against the Kitipornchai-Trahair approximation 0.9 ho (2 rho - 1)(1 - (Iy/Ix)^2)

Negative controls: a prismatic stand-in (mid-length section) misses the I ~ x^4 column by > 20 %; the singly
symmetric beam's Me differs by > 10 % between the two bending directions (Wagner term doing work).
VERIFIED stays False (DG 25 application unconfirmed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from bimsteelauto.design.nonprismatic import (
    G_STEEL,
    VERIFIED,
    equivalent_unbraced_length,
    in_plane_buckling,
    out_of_plane_buckling,
    stability_properties,
)
from bimsteelauto.sections import TaperedMember, WeldedI
from bimsteelauto.spec import E_STEEL

E, G = E_STEEL, G_STEEL
SYM = WeldedI.symmetric(d=600, tw=6, bf=200, tf=10)
MONO = WeldedI(d=600, tw=6, bf_top=300, tf_top=12, bf_bot=150, tf_bot=8)
UNIT = lambda x: 1.0  # noqa: E731
METHODS = ("fe", "ritz")


def test_flags_all_false():
    assert not any(VERIFIED.values())


@pytest.mark.parametrize("method", METHODS)
def test_euler_in_plane(method):
    L = 8000.0
    lam = in_plane_buckling(lambda x: SYM, UNIT, 0.0, L, method=method)
    assert lam == pytest.approx(math.pi ** 2 * E * SYM.Ix / L ** 2, rel=1e-5)


@dataclass
class _Ix:
    Ix: float


@pytest.mark.parametrize("method", METHODS)
def test_exact_tapered_column_I_x4(method):
    a, L, I1 = 4000.0, 4000.0, 1.0e8
    I = lambda x: _Ix(I1 * ((a + x) / a) ** 4)                     # x measured from the small end
    I2 = I(L).Ix
    exact = math.pi ** 2 * E * math.sqrt(I1 * I2) / L ** 2
    lam = in_plane_buckling(I, UNIT, 0.0, L, method=method, n=60 if method == "fe" else 24)
    assert lam == pytest.approx(exact, rel=1e-3)
    stand_in = math.pi ** 2 * E * I(L / 2).Ix / L ** 2           # prismatic, mid-length section
    assert abs(stand_in / exact - 1) > 0.20                       # negative control


@pytest.mark.parametrize("method", METHODS)
def test_doubly_symmetric_axial_is_weak_axis_euler(method):
    L = 3000.0
    lam = out_of_plane_buckling(lambda x: SYM, 0.0, L, P=UNIT, method=method)
    assert lam == pytest.approx(math.pi ** 2 * E * SYM.Iy / L ** 2, rel=1e-5)


@pytest.mark.parametrize("method", METHODS)
def test_singly_symmetric_flexural_torsional_aisc_e4(method):
    L = 3000.0
    p = stability_properties(MONO)
    Fey = math.pi ** 2 * E / (L / math.sqrt(MONO.Iy / MONO.A)) ** 2
    Fez = (math.pi ** 2 * E * MONO.Cw / L ** 2 + G * MONO.J) / (MONO.A * p.ro2)
    H = 1 - p.yo ** 2 / p.ro2
    Fe = (Fey + Fez) / (2 * H) * (1 - math.sqrt(1 - 4 * Fey * Fez * H / (Fey + Fez) ** 2))
    lam = out_of_plane_buckling(lambda x: MONO, 0.0, L, P=UNIT, method=method)
    assert lam / MONO.A == pytest.approx(Fe, rel=1e-5)
    assert Fe < 0.97 * min(Fey, Fez)                               # the coupling matters here


@pytest.mark.parametrize("method", METHODS)
def test_ltb_uniform_moment_doubly_symmetric(method):
    L = 6000.0
    Me = math.pi / L * math.sqrt(E * SYM.Iy * G * SYM.J + (math.pi * E / L) ** 2 * SYM.Iy * SYM.Cw)
    lam = out_of_plane_buckling(lambda x: SYM, 0.0, L, M=UNIT, method=method)
    assert lam == pytest.approx(Me, rel=1e-5)


def _me_mono(s, L, bx):
    return math.pi ** 2 * E * s.Iy / L ** 2 * (bx / 2 + math.sqrt((bx / 2) ** 2 + s.Cw / s.Iy * (1 + G * s.J * L ** 2 / (math.pi ** 2 * E * s.Cw))))


@pytest.mark.parametrize("method", METHODS)
def test_ltb_uniform_moment_singly_symmetric_wagner(method):
    L = 6000.0
    p = stability_properties(MONO)
    # the larger (top) flange: bx > 0 when IT is compressed; Kitipornchai-Trahair approximation as a sanity check
    rho = MONO.Iyc / (MONO.Iyc + MONO.Iyt)
    approx = 0.9 * MONO.ho * (2 * rho - 1) * (1 - (MONO.Iy / MONO.Ix) ** 2)
    bx_top = -p.beta                                               # y up -> bx with the top flange in compression
    assert bx_top > 0 and bx_top == pytest.approx(approx, rel=0.10)
    up = out_of_plane_buckling(lambda x: MONO, 0.0, L, M=UNIT, method=method)         # + : top compressed
    down = out_of_plane_buckling(lambda x: MONO, 0.0, L, M=lambda x: -1.0, method=method)
    assert up == pytest.approx(_me_mono(MONO, L, bx_top), rel=1e-5)
    assert down == pytest.approx(_me_mono(MONO, L, -bx_top), rel=1e-5)
    assert up > 1.10 * down                                        # larger flange compressed is the stronger way


TAPER = TaperedMember(start=WeldedI(d=1000, tw=8, bf_top=250, tf_top=12, bf_bot=200, tf_bot=10),
                      end=WeldedI(d=450, tw=8, bf_top=250, tf_top=12, bf_bot=200, tf_bot=10), length=7000.0)


def test_tapered_two_methods_agree():
    at = TAPER.at
    L = TAPER.length
    P = lambda x: 1.0 + 0.3 * x / L                                # axial growing along the member
    M = lambda x: 1.0 - 1.6 * x / L                                # moment reversing (top -> bottom compressed)
    cases = {
        "in plane": lambda m, n=None: in_plane_buckling(at, P, 0.0, L, method=m, n=n),
        "axial out": lambda m, n=None: out_of_plane_buckling(at, 0.0, L, P=P, method=m, n=n),
        "ltb": lambda m, n=None: out_of_plane_buckling(at, 0.0, L, M=M, method=m, n=n),
        "ltb neg": lambda m, n=None: out_of_plane_buckling(at, 0.0, L, M=lambda x: -M(x), method=m, n=n),
    }
    for name, f in cases.items():
        fe, ritz = f("fe"), f("ritz")
        assert fe == pytest.approx(ritz, rel=5e-3), name
        assert f("fe", 80) == pytest.approx(fe, rel=1e-3), name            # converged (16 elements by default)
        assert f("ritz", 24) == pytest.approx(ritz, rel=2e-3), name


def test_tension_throughout_cannot_buckle():
    assert out_of_plane_buckling(lambda x: SYM, 0.0, 3000.0, P=lambda x: -1.0) == math.inf
    assert in_plane_buckling(lambda x: SYM, lambda x: -1.0, 0.0, 3000.0) == math.inf


@pytest.mark.parametrize("j_term", [0.0, 2.0e-6])
def test_equivalent_unbraced_length_inverts_the_aisc_form(j_term):
    r, Lb = 55.0, 4200.0
    Fe = math.pi ** 2 * E / (Lb / r) ** 2 * math.sqrt(1 + 0.078 * j_term * (Lb / r) ** 2)
    assert equivalent_unbraced_length(Fe, r, j_term=j_term) == pytest.approx(Lb, rel=1e-9)


# ---------------------------------------------------------------- point restraints (purlins / flange braces inside a segment)

def _me_sym(L):
    return math.pi / L * math.sqrt(E * SYM.Iy * G * SYM.J + (math.pi * E / L) ** 2 * SYM.Iy * SYM.Cw)


@pytest.mark.parametrize("method", METHODS)
def test_restraint_at_the_shear_centre_halves_the_length(method):
    from bimsteelauto.design.nonprismatic import flange_height  # noqa: F401
    L = 6000.0
    lam = out_of_plane_buckling(lambda x: SYM, 0.0, L, M=UNIT, method=method, restraints=[(L / 2, 0.0)])
    assert lam == pytest.approx(_me_sym(L / 2), rel=1e-3)
    # axial: sideways held at midspan, twist free - min(weak-axis Euler over L/2, torsional over L)
    p = stability_properties(SYM)
    Pey = math.pi ** 2 * E * SYM.Iy / (L / 2) ** 2
    Pez = (math.pi ** 2 * E * SYM.Cw / L ** 2 + G * SYM.J) / p.ro2
    lam = out_of_plane_buckling(lambda x: SYM, 0.0, L, P=UNIT, method=method, restraints=[(L / 2, 0.0)])
    assert lam == pytest.approx(min(Pey, Pez), rel=1e-3)


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_compression_flange_restraint_works_tension_flange_barely(method, sign):
    """Physics pins the sign convention: holding the COMPRESSION flange at midspan is (nearly) a full brace; holding the
    tension flange adds little.  If the restraint were applied at the mirrored point the two would swap."""
    from bimsteelauto.design.nonprismatic import flange_height
    L = 6000.0
    comp, tens = ("top", "bottom") if sign > 0 else ("bottom", "top")
    M = lambda x: sign * 1.0
    free = out_of_plane_buckling(lambda x: SYM, 0.0, L, M=M, method=method)
    c = out_of_plane_buckling(lambda x: SYM, 0.0, L, M=M, method=method, restraints=[(L / 2, flange_height(SYM, comp))])
    t = out_of_plane_buckling(lambda x: SYM, 0.0, L, M=M, method=method, restraints=[(L / 2, flange_height(SYM, tens))])
    assert free <= t < 1.05 * free
    assert c == pytest.approx(_me_sym(L / 2), rel=1e-3) and c > 3 * t


def test_penalty_does_not_matter(monkeypatch):
    import bimsteelauto.design.nonprismatic as npm
    from bimsteelauto.design.nonprismatic import flange_height
    L = 6000.0
    args = dict(M=lambda x: 1.0 - 1.5 * x / L, restraints=[(1500.0, flange_height(SYM, "top")), (4500.0, flange_height(SYM, "top"))])
    vals = []
    for pen in (1e4, 1e6, 1e8):                     # 1e6 is the module's value; 1e4 is a visibly soft spring
        monkeypatch.setattr(npm, "PENALTY", pen)
        vals.append((out_of_plane_buckling(lambda x: SYM, 0.0, L, method="fe", **args),
                     out_of_plane_buckling(lambda x: SYM, 0.0, L, method="ritz", **args)))
    (fe4, r4), (fe6, r6), (fe8, r8) = vals
    assert fe6 == pytest.approx(fe8, rel=1e-4) and r6 == pytest.approx(r8, rel=1e-4)     # rigid by 1e6
    assert fe4 < fe6 and fe4 == pytest.approx(fe6, rel=1e-2)                             # softer spring, lower load
    assert fe6 == pytest.approx(r6, rel=5e-3)                                            # the two methods agree
