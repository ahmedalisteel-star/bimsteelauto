"""One test per defect the verification process caught.

HANDOVER section 4 lists seven.  Every one of them passed all ~70 relative
invariant tests — they were found only because an independent tool printed
a number or a formula.  That makes them the most valuable tests in the
suite: they are the errors this codebase has actually demonstrated it can
make.

Each test below is written to FAIL if the fix is reverted, and each names
the defect number and the tool that exposed it.  Where possible the
assertion is absolute (a specific value, a specific term's presence)
rather than relative, because relative assertions are exactly what these
defects walked past.
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto_connections import anchors as A
from bimsteelauto_connections import endplate as E


# ======================================================================
# #1 — 4E Yp missing a 1/s term and a -1/2          [CET.SteelConnDesign]
# ======================================================================

def test_defect_1_4E_Yp_has_the_1_over_s_term_and_the_minus_half() -> None:
    """Yp = (bp/2)[h1(1/pfi + 1/s) + h0(1/pfo) - 1/2] + (2/g)[h1(pfi + s)]

    Reconstructed independently here from the expression CET printed, and
    compared term by term.  Dropping either the ``1/s`` or the ``-1/2``
    changes the result, so this fails if the fix is reverted — and it does
    not need the (unrecorded) CET input geometry to do it.
    """
    g = E.EndPlateGeometry(bp=250.0, g=140.0, pfo=50.0, pfi=50.0,
                           h_rows=(720.0, 600.0), de=45.0)
    yl = E.yield_line_Yp(E.EndPlateConfig.FOUR_E, g)

    bp, gg, pfo, pfi, s = g.bp, g.g, g.pfo, min(g.pfi, g.s), g.s
    h0, h1 = g.h_rows
    expected = (bp / 2.0) * (h1 * (1.0 / pfi + 1.0 / s)
                             + h0 * (1.0 / pfo) - 0.5) \
        + (2.0 / gg) * (h1 * (pfi + s))
    assert math.isclose(yl.Yp, expected, rel_tol=1e-12), (
        f"Yp = {yl.Yp:.4f}, DG 4 Eq. 3.10 gives {expected:.4f}"
    )

    # And each dropped term must actually move the answer, so the test
    # above is not vacuously satisfied by a coincidence of geometry.
    without_1_over_s = (bp / 2.0) * (h1 * (1.0 / pfi)
                                     + h0 * (1.0 / pfo) - 0.5) \
        + (2.0 / gg) * (h1 * (pfi + s))
    without_half = (bp / 2.0) * (h1 * (1.0 / pfi + 1.0 / s)
                                 + h0 * (1.0 / pfo)) \
        + (2.0 / gg) * (h1 * (pfi + s))
    assert not math.isclose(yl.Yp, without_1_over_s, rel_tol=1e-6)
    assert not math.isclose(yl.Yp, without_half, rel_tol=1e-6)


# ======================================================================
# #2 — ACI 17.6.2.1.2 substitute hef' absent entirely        [PROFIS]
# ======================================================================

def test_defect_2_hef_substitute_is_applied() -> None:
    """PROFIS reported hef = 300 mm where the input was 450."""
    g = A.AnchorGroup(d=31.75, hef=450.0, s_x=350.0, s_y=350.0,
                      ca_x1=450.0, ca_x2=450.0, ca_y1=450.0, ca_y2=450.0,
                      ha=800.0)
    assert g.three_or_more_close_edges
    assert math.isclose(g.hef_effective, 300.0, rel_tol=1e-12), (
        f"hef' = {g.hef_effective}, ACI 17.6.2.1.2 gives "
        f"max(ca_max/1.5, s_max/3) = max(300.0, 116.67) = 300.0"
    )
    assert g.hef_effective < g.hef, "the substitute is not being applied at all"
    assert math.isclose(g.ANco, 9.0 * 300.0 ** 2), "ANco must use hef', not hef"


def test_defect_2_hef_substitute_not_applied_when_fewer_than_three_edges() -> None:
    """The clause must not fire on a wide pedestal."""
    g = A.AnchorGroup(d=31.75, hef=450.0, s_x=350.0, s_y=350.0,
                      ca_x1=450.0, ca_x2=10_000.0,
                      ca_y1=450.0, ca_y2=10_000.0, ha=800.0)
    assert not g.three_or_more_close_edges
    assert g.hef_effective == g.hef


# ======================================================================
# #3 — psi_c,V ignored edge reinforcement (Table 17.7.2.5.1)
# ======================================================================

def test_defect_3_psi_c_V_honours_edge_reinforcement() -> None:
    """Returning a flat 1.0 for every cracked case ran shear breakout 20% low."""
    assert A.psi_c_V(cracked=True) == 1.0
    assert A.psi_c_V(cracked=True, edge_reinforcement=True) == 1.2
    assert A.psi_c_V(cracked=True, stirrups_at_100mm=True) == 1.4
    assert A.psi_c_V(cracked=False) == 1.4


def test_defect_3_edge_reinforcement_reaches_the_shear_breakout_result() -> None:
    """The factor must be wired through, not merely defined."""
    base = dict(d=31.75, hef=450.0, s_x=350.0, s_y=350.0, ca_x1=150.0,
                ca_x2=150.0, ca_y1=150.0, ca_y2=150.0, ha=800.0)
    plain = A.check_anchors(Nua=100.0, Vua=60.0, fc_prime=30.0, strict=False,
                            group=A.AnchorGroup(**base))
    reinf = A.check_anchors(Nua=100.0, Vua=60.0, fc_prime=30.0, strict=False,
                            group=A.AnchorGroup(**base, edge_reinforcement=True))

    def breakout(c):
        return next(cap for n, cap, _ in c.shear if n.startswith("17.7.2"))

    assert math.isclose(breakout(reinf) / breakout(plain), 1.2, rel_tol=1e-9), (
        f"edge reinforcement gave {breakout(reinf) / breakout(plain):.4f}x, "
        f"expected exactly 1.2x"
    )


# ======================================================================
# #4 — AVc not capped by member depth: a thin slab computed STRONGER
# ======================================================================

def test_defect_4_thin_member_is_weaker_not_stronger_in_shear_breakout() -> None:
    """The signature of the defect: monotonicity in ha was INVERTED.

    psi_h,V (>= 1.0) magnifies as the member gets thinner.  Without the
    Sect. 17.7.2.1 cap of AVc by the member depth, nothing shrinks, so a
    thinner slab computed a HIGHER capacity.  Deep-to-shallow must be
    monotonically non-increasing.
    """
    def breakout(ha):
        g = A.AnchorGroup(d=31.75, hef=450.0, s_x=350.0, s_y=350.0,
                          ca_x1=400.0, ca_x2=400.0, ca_y1=400.0,
                          ca_y2=400.0, ha=ha)
        c = A.check_anchors(Nua=100.0, Vua=60.0, group=g, fc_prime=30.0,
                            strict=False)
        return next(cap for n, cap, _ in c.shear if n.startswith("17.7.2"))

    depths = [2000.0, 1000.0, 800.0, 600.0, 400.0, 200.0, 100.0]
    vals = [breakout(h) for h in depths]
    for (h1, v1), (h2, v2) in zip(zip(depths, vals), zip(depths[1:], vals[1:])):
        assert v2 <= v1 + 1e-9, (
            f"thinning the member from {h1:.0f} to {h2:.0f} mm RAISED shear "
            f"breakout, {v1:.2f} -> {v2:.2f} kN. AVc is not being capped by "
            f"the member depth (ACI 318-19 Sect. 17.7.2.1)."
        )
    assert vals[-1] < vals[0], "member depth has no effect at all on shear breakout"


def test_defect_4_unknown_ha_warns_rather_than_assuming_depth() -> None:
    """``ha=None`` must say so — silently assuming a deep member is the trap."""
    g = A.AnchorGroup(d=31.75, hef=450.0, s_x=350.0, s_y=350.0,
                      ca_x1=400.0, ca_x2=400.0, ca_y1=400.0, ca_y2=400.0)
    assert g.ha is None
    c = A.check_anchors(Nua=100.0, Vua=60.0, group=g, fc_prime=30.0,
                        strict=False)
    assert any("psi_h,V" in w and "1.0" in w for w in c.warnings), (
        "ha=None produced no warning about psi_h,V being assumed 1.0"
    )


# ======================================================================
# #5 — imperial thread areas shifted a row                   [PROFIS]
# ======================================================================

def test_defect_5_thread_areas_are_tensile_stress_areas_not_shank_areas() -> None:
    """1-1/4"-7 must read 625.2 mm2 (UNC tensile stress area).

    It read 806.5 — the nominal shank area, one row off — and steel
    strength ran 29% high.  PROFIS printed ``Ase = 625`` and exposed it.
    """
    g = A.AnchorGroup(d=31.75, hef=450.0, s_x=350.0, s_y=350.0)
    assert math.isclose(g.Ase, 625.2, rel_tol=1e-9), (
        f"Ase(1-1/4\") = {g.Ase}, expected 625.2 mm2"
    )
    shank = math.pi * 31.75 ** 2 / 4.0
    assert not math.isclose(g.Ase, shank, rel_tol=1e-2), (
        f"Ase has reverted to the nominal shank area {shank:.1f} mm2"
    )
    # The stress area is necessarily below the shank area, for every entry.
    for d, area in A.THREAD_AREA.items():
        assert area < math.pi * d ** 2 / 4.0, (
            f"THREAD_AREA[{d}] = {area} exceeds the shank area "
            f"{math.pi * d ** 2 / 4.0:.1f} mm2 — the table has shifted again"
        )


def test_defect_5_thread_area_table_is_monotonic() -> None:
    """A row shift shows up as a break in monotonicity."""
    items = sorted(A.THREAD_AREA.items())
    for (d1, a1), (d2, a2) in zip(items, items[1:]):
        assert a2 > a1, f"THREAD_AREA not monotonic between {d1} and {d2}"


# ======================================================================
# #6 — alpha_group missing (Eq. 17.6.4.2)                    [PROFIS]
# ======================================================================

def test_defect_6_alpha_group_is_applied_to_side_face_blowout() -> None:
    """Omitting it dropped a 39% group increase on the ca=150 model."""
    g = A.AnchorGroup(d=31.75, hef=450.0, s_x=350.0, s_y=350.0,
                      ca_x1=150.0, ca_x2=150.0, ca_y1=150.0, ca_y2=150.0,
                      ha=800.0)
    c = A.check_anchors(Nua=364.096, Vua=60.0, group=g, fc_prime=30.0,
                        strict=False)
    blowout = next((cap for n, cap, _ in c.tension if n.startswith("17.6.4")),
                   None)
    assert blowout is not None, "side-face blowout did not engage at hef > 2.5*ca"

    # Rebuild Nsb without the group factor and confirm the module is above it.
    sqrt_fc = math.sqrt(30.0)
    Nsb = 13.0 * g.ca_min * math.sqrt(g.Abrg) * 1.0 * sqrt_fc / 1000.0
    alpha = 1.0 + max(g.s_x, g.s_y) / (6.0 * g.ca_min)
    assert math.isclose(alpha, 1.3889, rel_tol=1e-3), f"alpha_group = {alpha:.4f}"
    assert blowout > 0.70 * Nsb * 1.05, (
        f"blowout {blowout:.2f} kN looks like phi*Nsb without alpha_group "
        f"({0.70 * Nsb:.2f} kN) — Eq. 17.6.4.2 has been dropped again"
    )


def test_defect_6_alpha_group_switches_off_beyond_6ca() -> None:
    """The factor applies only while s < 6*ca1."""
    wide = A.AnchorGroup(d=31.75, hef=450.0, s_x=1200.0, s_y=1200.0,
                         ca_x1=150.0, ca_x2=150.0, ca_y1=150.0,
                         ca_y2=150.0, ha=800.0)
    assert max(wide.s_x, wide.s_y) >= 6.0 * wide.ca_min
    c = A.check_anchors(Nua=364.096, Vua=60.0, group=wide, fc_prime=30.0,
                        strict=False)
    assert any("alpha_group = 1.000" in w for w in c.warnings), (
        "alpha_group did not fall back to 1.0 at s >= 6*ca1"
    )


# ======================================================================
# #7 — blowout demand checked against the whole group load   [PROFIS]
# ======================================================================

def test_defect_7_blowout_demand_is_the_edge_row_not_the_whole_group() -> None:
    """Blowout is a LOCAL edge failure — only the anchors near that edge load it."""
    Nua = 364.096
    g = A.AnchorGroup(d=31.75, hef=450.0, n_x=2, n_y=2, s_x=350.0, s_y=350.0,
                      ca_x1=150.0, ca_x2=150.0, ca_y1=150.0, ca_y2=150.0,
                      ha=800.0)
    c = A.check_anchors(Nua=Nua, Vua=60.0, group=g, fc_prime=30.0,
                        strict=False)
    # AnchorCheck stores (name, capacity, UTILISATION) — recover the demand.
    name, cap, util = next(t for t in c.tension if t[0].startswith("17.6.4"))
    demand = util * cap

    assert math.isclose(demand, Nua * 0.5, rel_tol=1e-6), (
        f"blowout demand {demand:.3f} kN; the edge row of a 2x2 group "
        f"carries half of {Nua}, i.e. {Nua * 0.5:.3f} kN. A demand equal to "
        f"{Nua} means the whole group load is being used again."
    )
    assert "edge 50%" in name, f"the edge fraction is not stated in '{name}'"
    assert any("not the full group load" in w for w in c.warnings)


def test_defect_7_edge_fraction_tracks_the_group_shape() -> None:
    """A 2x4 group loaded on a long edge shares differently than a 2x2."""
    g = A.AnchorGroup(d=31.75, hef=450.0, n_x=4, n_y=2, s_x=200.0, s_y=350.0,
                      ca_x1=10_000.0, ca_x2=10_000.0,
                      ca_y1=150.0, ca_y2=10_000.0, ha=800.0)
    c = A.check_anchors(Nua=400.0, Vua=60.0, group=g, fc_prime=30.0,
                        strict=False)
    row = next((t for t in c.tension if t[0].startswith("17.6.4")), None)
    assert row is not None
    _, cap, util = row
    demand = util * cap
    assert demand < 400.0, "still using the whole group load"
    assert demand > 0.0


# ======================================================================
# Cross-cutting: the class of error these all belong to
# ======================================================================

@pytest.mark.parametrize("defect", [1, 2, 3, 4, 5, 6, 7])
def test_every_documented_defect_has_a_regression_test(defect: int) -> None:
    """Structural: HANDOVER lists seven, so seven must be covered here."""
    import test_defect_regressions as self_mod
    names = [n for n in dir(self_mod) if n.startswith(f"test_defect_{defect}_")]
    assert names, f"defect #{defect} in HANDOVER section 4 has no regression test"
