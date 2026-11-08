"""
bimsteelauto.design.nonprismatic
================================

Elastic buckling of nonprismatic (web-tapered) welded I-members, and its use
in the member check the way AISC Design Guide 25 (2nd ed., 2011) does it:
the member's own elastic buckling load, not that of a prismatic stand-in.

    NOT VERIFIED.  NOT FOR ISSUE.

Every flag in :data:`VERIFIED` is False.  The user has no copy of DG 25 yet
(2026-11-08), so the application below is Claude's reading of the method.
It is to be confirmed equation by equation against the printed guide, and
until then nothing here may be issued.  What IS checked (tests/
test_nonprismatic.py) is the elastic buckling arithmetic:
- two independent discretisations agree on tapered members;
- each reproduces closed forms: the Euler column, the exactly solvable
  column with I ~ x^4 (Pcr = pi^2 E sqrt(I1 I2) / L^2), AISC E4 for a
  singly-symmetric section, and the uniform-moment LTB of a doubly and of a
  singly symmetric beam (Wagner term).

Elastic buckling
----------------
A member segment between two brace points, simply supported for each
buckling mode there: no displacement and no twist, but free to rotate and
free to warp.  The loads act along the shear-centre axis.  Total potential,
in N and mm (Trahair, *Flexural-Torsional Buckling of Structures*):

    out of plane (u, phi), under an axial force P(x) (compression +) and a
    moment M(x) (+ = top flange in compression), times a load factor lambda:

        U = 1/2 int [ E Iy u''^2 + E Cw phi''^2 + G J phi'^2 ] dx
        V = -lambda/2 int [ P (u'^2 + 2 yo u' phi' + ro^2 phi'^2)
                            - 2 M u'' phi + M beta phi'^2 ] dx

    in plane (v), under P(x):   U = 1/2 int E Ix v''^2,   V = -lambda/2 int P v'^2

where yo is the shear centre above the centroid, ro^2 = yo^2 + (Ix + Iy)/A,
and beta = (1/Ix) int y (x^2 + y^2) dA - 2 yo, both with y measured UP.
beta is the Wagner term. The sign convention: positive beta (y up) with a
positive M is destabilising. The test with the larger flange in compression
pins the sign against the closed form.

lambda_cr, the smallest positive lambda with K q = lambda G q, comes from
two independent methods:
- "fe": cubic Hermite finite elements, with 4-point Gauss quadrature of the
  varying section in each element;
- "ritz": a sine series u, phi = sum a_n sin(n pi xi), with composite Gauss
  quadrature over the segment.

They share the energy functional and nothing else: different shape
functions, different assembly, different quadrature.

Application (Claude's reading of DG 25 - TO BE CONFIRMED)
---------------------------------------------------------
Compression.  gamma_e = min(lambda_cr in plane over the whole member (K = 1,
the Direct Analysis Method), lambda_cr out of plane over the segment).  At
each station:
- fr = Pr / Ag;
- Fe = gamma_e fr;
- Fcr from AISC E3 with that Fe;
- Pn = Fcr Ae, with Sect. E7 at that Fcr.

Every station is checked, so the critical section is found rather than
assumed.

Flexure.  gamma_LTB = lambda_cr of the segment under its actual moment
diagram.  The moment gradient is in it, so Cb = 1.  At each station:
- fr = Mr / Sxc;
- Fe,LTB = gamma_LTB fr, times kappa (below);
- this is converted to the unbraced length Lb,eq that gives the same elastic
  LTB stress in the section's own AISC equation (F2-4, F4-5 or F5-4, with
  Cb = 1);
- Mn is taken from Chapter F with Lb,eq and Cb = 1.

kappa = Fe,AISC / Fe,exact for the station's own section over the segment
length under uniform moment. The exact value comes from the closed form with
the Wagner term. The numerical Fe is exact, while F4-5 (rt counts a sixth of
the web) and F5-4 (no J) are deliberately lower. kappa keeps AISC's margin:
- kappa = 1 in F2, whose equation is the exact solution;
- without kappa, F4 would come out about 2 % above AISC.

Results, all pinned by tests/test_dg25_application.py:
- prismatic member, uniform moment: AISC exactly in F2, F4 (singly
  symmetric, both bending directions) and F5;
- prismatic member, axial load: AISC E3/E4/E7 exactly;
- moment going linearly to zero: in the elastic range the exact gradient
  benefit (equivalent Cb ~ 1.85, against F1-1's 1.67); in the inelastic range
  about 3 % below AISC's Cb x (F4-2), the conservative side.

Under DG 25 the capacity varies along the member with fr, so compare
UTILISATIONS, not the capacity printed for the governing station.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Sequence

import numpy as np
from scipy.linalg import eigh

from ..sections import WeldedI
from ..spec import E_STEEL

__all__ = [
    "VERIFIED",
    "G_STEEL",
    "SectionStability",
    "stability_properties",
    "in_plane_buckling",
    "out_of_plane_buckling",
    "equivalent_unbraced_length",
    "flange_height",
]

#: All False: no independent source for the APPLICATION yet (DG 25 is to be
#: confirmed with the user against the printed guide).  The elastic buckling
#: arithmetic itself is matched to closed forms and across two methods in
#: tests/test_nonprismatic.py, but it stays False with the rest until then.
VERIFIED: dict[str, bool] = {
    "elastic_buckling_fe": False,
    "elastic_buckling_ritz": False,
    "dg25_compression": False,
    "dg25_ltb": False,
}

#: Shear modulus, MPa (AISC 360-16 Sect. B: G = 77 200 MPa).
G_STEEL = 77_200.0

#: A point restraint is a spring of PENALTY x E Iy / L^3 of the segment
#: (rigid for practical purposes; the test against the half-length closed
#: form fixes the tolerance).  Scaled on the physical lateral stiffness, not
#: on K's diagonal: the dofs mix mm and radians, and a penalty taken from the
#: largest diagonal ill-conditions the eigenproblem.
PENALTY = 1.0e6
#: Sideways movement of a point e above the shear centre = u + RESTRAINT_SIGN
#: e phi, in this module's convention for phi.  Pinned by the physics test:
#: restraining the COMPRESSION flange must help more than the tension flange.
RESTRAINT_SIGN = 1.0


# ---------------------------------------------------------------------------
# Section properties for stability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionStability:
    """mm, mm^2, mm^4, mm^6.  y measured UP from the centroid."""

    A: float
    Ix: float
    Iy: float
    Cw: float
    J: float
    yo: float        # shear centre above the centroid
    beta: float      # Wagner term (1/Ix) int y (x^2 + y^2) dA - 2 yo, y up

    @property
    def ro2(self) -> float:
        return self.yo ** 2 + (self.Ix + self.Iy) / self.A


@lru_cache(maxsize=8192)
def stability_properties(s: WeldedI) -> SectionStability:
    """Shear centre and Wagner term of a welded I from its three plates.

    Shear centre on the web line, at ho * Iyt / (Iyc + Iyt) below the top
    flange's centroid (the flanges carry the warping; the web's Iy is
    neglected, as in Cw).  The beta integral is taken exactly over the three
    rectangles: for a rectangle b wide and t high, centred at height yi,

        int y x^2 dA = yi t b^3 / 12,    int y^3 dA = b (yi^3 t + yi t^3 / 4).
    """
    yc = s.y_bar
    y_top = s.d - 0.5 * s.tf_top
    ys = y_top - s.ho * s.Iyt / (s.Iyc + s.Iyt)
    yo = ys - yc
    parts = (
        (s.bf_top, s.tf_top, y_top - yc),
        (s.tw, s.h, s.tf_bot + 0.5 * s.h - yc),
        (s.bf_bot, s.tf_bot, 0.5 * s.tf_bot - yc),
    )
    integral = sum(yi * t * b ** 3 / 12.0 + b * (yi ** 3 * t + yi * t ** 3 / 4.0) for b, t, yi in parts)
    return SectionStability(A=s.A, Ix=s.Ix, Iy=s.Iy, Cw=s.Cw, J=s.J, yo=yo, beta=integral / s.Ix - 2.0 * yo)


# ---------------------------------------------------------------------------
# The energy densities
# ---------------------------------------------------------------------------

# Coefficients at one x, for the two-field (u, phi) problem:
#   K:  ku2 u''u''   kp2 phi''phi''   kp1 phi'phi'
#   G:  gu1 u'u'     gup u'phi' (x2)  gp1 phi'phi'   gu2p u''phi (x2)
_Coef = Callable[[float], tuple[float, float, float, float, float, float, float]]


def _out_of_plane_coef(section_at, P, M, E, G) -> _Coef:
    def f(x):
        p = stability_properties(section_at(x))
        Px, Mx = P(x), M(x)
        return (E * p.Iy, E * p.Cw, G * p.J,
                Px, Px * p.yo, Px * p.ro2 + Mx * p.beta, Mx)
    return f


def _in_plane_coef(section_at, P, E) -> _Coef:
    def f(x):
        s = section_at(x)
        # phi is a dummy field here: stiff, unloaded - it carries no buckling mode
        return (E * s.Ix, 1.0, 1.0, P(x), 0.0, 0.0, 0.0)
    return f


# ---------------------------------------------------------------------------
# Method 1: cubic Hermite finite elements
# ---------------------------------------------------------------------------


def _hermite(xi: np.ndarray, h: float):
    """Cubic Hermite shape functions and their x-derivatives at xi in [0, 1]; each (len(xi), 4)."""
    N = np.stack([1 - 3 * xi ** 2 + 2 * xi ** 3, h * (xi - 2 * xi ** 2 + xi ** 3),
                  3 * xi ** 2 - 2 * xi ** 3, h * (-xi ** 2 + xi ** 3)], axis=1)
    d1 = np.stack([-6 * xi + 6 * xi ** 2, h * (1 - 4 * xi + 3 * xi ** 2),
                   6 * xi - 6 * xi ** 2, h * (-2 * xi + 3 * xi ** 2)], axis=1) / h
    d2 = np.stack([-6 + 12 * xi, h * (-4 + 6 * xi), 6 - 12 * xi, h * (-2 + 6 * xi)], axis=1) / h ** 2
    return N, d1, d2


def _solve_fe(coef: _Coef, a: float, b: float, n: int, restraints=()) -> float:
    gp, gw = np.polynomial.legendre.leggauss(4)
    h = (b - a) / n
    xi = 0.5 * (gp + 1.0)
    N, D1, D2 = _hermite(xi, h)                                       # (g, 4)
    xs = a + (np.arange(n)[:, None] + xi[None, :]) * h               # (e, g)
    C = np.array([[coef(float(x)) for x in row] for row in xs])      # (e, g, 7)
    jw = 0.5 * gw * h

    def blk(c, A, B):                                                # sum over Gauss points, per element
        return np.einsum("eg,g,gi,gj->eij", c, jw, A, B)

    ke = np.zeros((n, 8, 8))
    ge = np.zeros((n, 8, 8))
    ke[:, :4, :4] = blk(C[..., 0], D2, D2)
    ke[:, 4:, 4:] = blk(C[..., 1], D2, D2) + blk(C[..., 2], D1, D1)
    ge[:, :4, :4] = blk(C[..., 3], D1, D1)
    ge[:, 4:, 4:] = blk(C[..., 5], D1, D1)
    cross = blk(C[..., 4], D1, D1) - blk(C[..., 6], D2, N)
    ge[:, :4, 4:] = cross
    ge[:, 4:, :4] = cross.transpose(0, 2, 1)
    e4 = 4 * np.arange(n)[:, None]
    idx = e4 + np.array([0, 1, 4, 5, 2, 3, 6, 7])[None, :]           # per node: u, u', phi, phi'
    ndof = 4 * (n + 1)
    K = np.zeros((ndof, ndof))
    Gm = np.zeros((ndof, ndof))
    np.add.at(K, (idx[:, :, None], idx[:, None, :]), ke)
    np.add.at(Gm, (idx[:, :, None], idx[:, None, :]), ge)
    if restraints:
        k = PENALTY * coef(0.5 * (a + b))[0] / (b - a) ** 3          # x the segment's E Iy / L^3
        for x, e in restraints:
            el = min(int((x - a) / h), n - 1)
            Nx, _, _ = _hermite(np.array([(x - a) / h - el]), h)
            c = np.zeros(ndof)
            c[idx[el, :4]] = Nx[0]
            c[idx[el, 4:]] = RESTRAINT_SIGN * e * Nx[0]
            K += k * np.outer(c, c)
    fixed = {0, 2, 4 * n, 4 * n + 2}                                 # u = phi = 0 at both ends
    free = np.array([i for i in range(ndof) if i not in fixed])
    return _lowest_positive(K[np.ix_(free, free)], Gm[np.ix_(free, free)])


# ---------------------------------------------------------------------------
# Method 2: Rayleigh-Ritz, sine series
# ---------------------------------------------------------------------------


def _solve_ritz(coef: _Coef, a: float, b: float, n: int, restraints=()) -> float:
    L = b - a
    gp, gw = np.polynomial.legendre.leggauss(8)
    pieces = max(32, 4 * n)
    hh = L / pieces
    xs = (a + (np.arange(pieces)[:, None] + 0.5 * (gp[None, :] + 1.0)) * hh).ravel()
    ws = np.tile(0.5 * gw * hh, pieces)
    t = np.arange(1, n + 1) * math.pi / L
    arg = np.outer(xs - a, t)                                        # (p, n)
    F0 = np.sin(arg)
    F1 = t * np.cos(arg)
    F2 = -(t ** 2) * F0
    C = np.array([coef(float(x)) for x in xs])                       # (p, 7)

    def blk(c, A, B):
        return np.einsum("p,pi,pj->ij", ws * c, A, B)

    K = np.zeros((2 * n, 2 * n))
    Gm = np.zeros((2 * n, 2 * n))
    K[:n, :n] = blk(C[:, 0], F2, F2)
    K[n:, n:] = blk(C[:, 1], F2, F2) + blk(C[:, 2], F1, F1)
    Gm[:n, :n] = blk(C[:, 3], F1, F1)
    Gm[n:, n:] = blk(C[:, 5], F1, F1)
    cross = blk(C[:, 4], F1, F1) - blk(C[:, 6], F2, F0)
    Gm[:n, n:] = cross
    Gm[n:, :n] = cross.T
    if restraints:
        k = PENALTY * coef(0.5 * (a + b))[0] / (b - a) ** 3          # x the segment's E Iy / L^3
        for x, e in restraints:
            sx = np.sin(t * (x - a))
            c = np.concatenate([sx, RESTRAINT_SIGN * e * sx])
            K += k * np.outer(c, c)
    return _lowest_positive(K, Gm)


def _lowest_positive(K: np.ndarray, Gm: np.ndarray) -> float:
    """Smallest positive lambda of K q = lambda G q (K positive definite).

    eigh(G, K) gives mu = 1/lambda; the largest positive mu is the critical
    mode.  No positive mu (the load cannot buckle the member - e.g. tension
    throughout) returns infinity, never a made-up number.
    """
    mu = eigh(Gm, K, eigvals_only=True)
    top = float(np.max(mu))
    return math.inf if top <= 1e-14 * max(1.0, float(np.max(np.abs(mu)))) else 1.0 / top


def _solve(coef, a, b, method, n, restraints=()):
    if b <= a:
        raise ValueError(f"segment must have positive length, got [{a}, {b}]")
    restraints = tuple((x, e) for x, e in restraints if a + 1e-6 < x < b - 1e-6)
    if method == "fe":
        return _solve_fe(coef, a, b, n or 16, restraints)
    if method == "ritz":
        return _solve_ritz(coef, a, b, n or 16, restraints)
    raise ValueError(f"method must be 'fe' or 'ritz', got {method!r}")


# ---------------------------------------------------------------------------
# Public solvers (N, mm)
# ---------------------------------------------------------------------------


def in_plane_buckling(section_at: Callable[[float], WeldedI], P: Callable[[float], float], a: float, b: float, *,
                      E: float = E_STEEL, method: str = "fe", n: int | None = None) -> float:
    """lambda_cr for in-plane flexural buckling of [a, b], pinned at both ends (N; compression +)."""
    return _solve(_in_plane_coef(section_at, P, E), a, b, method, n)


def out_of_plane_buckling(section_at: Callable[[float], WeldedI], a: float, b: float, *,
                          P: Callable[[float], float] | None = None, M: Callable[[float], float] | None = None,
                          E: float = E_STEEL, G: float = G_STEEL, method: str = "fe", n: int | None = None,
                          restraints: Sequence[tuple[float, float]] = ()) -> float:
    """lambda_cr for out-of-plane (flexural-torsional / lateral-torsional) buckling of [a, b].

    P in N (compression +), M in N.mm (+ = top flange in compression).  Ends
    simply supported for u and phi, free to warp.  ``restraints``: interior
    lateral restraints as (x, e) - rigid against sideways movement of the
    point e mm ABOVE the shear centre (a purlin on the top flange: e =
    flange_height(section, "top")); modelled by a penalty in both methods.
    """
    zero = lambda x: 0.0  # noqa: E731
    return _solve(_out_of_plane_coef(section_at, P or zero, M or zero, E, G), a, b, method, n, restraints)


def flange_height(s: WeldedI, which: str) -> float:
    """Height of a flange's centroid above the shear centre, mm (negative for the bottom flange)."""
    if which == "top":
        return s.ho * s.Iyt / (s.Iyc + s.Iyt)
    return -s.ho * s.Iyc / (s.Iyc + s.Iyt)


# ---------------------------------------------------------------------------
# Fe,LTB -> the Lb that gives it in the section's own AISC equation
# ---------------------------------------------------------------------------


def equivalent_unbraced_length(Fe: float, r: float, *, E: float = E_STEEL, j_term: float = 0.0) -> float:
    """Lb (mm) with  Fe = pi^2 E / (Lb/r)^2 * sqrt(1 + 0.078 j_term (Lb/r)^2),  Cb = 1.

    F2-4 (r = rts, j_term = J c / (Sx ho)), F4-5 (r = rt, j_term = J / (Sxc ho),
    0 when Iyc/Iy <= 0.23), F5-4 (r = rt, j_term = 0).  With s = (Lb/r)^2:
    Fe^2 s^2 - pi^4 E^2 c s - pi^4 E^2 = 0, c = 0.078 j_term; the positive root.
    """
    if Fe <= 0.0 or not math.isfinite(Fe):
        return 0.0 if Fe > 0.0 else math.inf
    k = math.pi ** 4 * E ** 2
    c = 0.078 * j_term
    s = (k * c + math.sqrt((k * c) ** 2 + 4.0 * Fe ** 2 * k)) / (2.0 * Fe ** 2)
    return r * math.sqrt(s)


def piecewise(xs: Sequence[float], vs: Sequence[float]) -> Callable[[float], float]:
    """Linear interpolation through station values (held flat beyond the ends)."""
    xa = np.asarray(xs, dtype=float)
    va = np.asarray(vs, dtype=float)
    order = np.argsort(xa)
    xa, va = xa[order], va[order]
    return lambda x: float(np.interp(x, xa, va))
