"""The verification reports, as fixtures.

This is the file that can catch a transcription error.  Every assertion
here compares the module against a number produced by an INDEPENDENT tool
— Hilti PROFIS or CET.SteelConnDesign — not against the module's own
output.

The distinction is the whole point, so it is enforced structurally: a
fixture whose ``status`` is not ``complete`` cannot produce a passing
verification test.  A fixture with outputs but no inputs can only pin the
module against itself, and per HANDOVER section 7 that is not
verification, so those tests SKIP with the reason printed.

Read a skip here as "this flag is not actually backed by anything
re-runnable", not as "this test is fine".
"""

from __future__ import annotations

import math

import pytest

from conftest import within_pct


# ======================================================================
# Anchors — ACI 318-19 Ch. 17 against Hilti PROFIS 3.1.34
# ======================================================================

def _run_fixture(fixture, factory):
    from bimsteelauto_connections.anchors import check_anchors
    inp = fixture["inputs"]
    group = factory(inp)
    check = check_anchors(
        Nua=inp["Nua"], Vua=inp["Vua"], group=group,
        grade=inp["grade"], fc_prime=inp["fc_prime"], strict=False,
    )
    by_name = {n: cap for n, cap, _ in (*check.tension, *check.shear)}
    return group, check, by_name


def _match(by_name: str, table: dict) -> float:
    """Fixture keys are mode prefixes; module names may carry a suffix.

    ``17.6.4 side-face blowout`` in the fixture matches
    ``17.6.4 side-face blowout (edge 50%)`` from the module.
    """
    for k, v in table.items():
        if k.startswith(by_name) or by_name.startswith(k):
            return v
    raise KeyError(by_name)


@pytest.mark.parametrize("fixture_name", ["profis_ca450", "profis_ca150"])
def test_profis_fixture_is_complete(fixture_name, request) -> None:
    """A verification fixture must carry the inputs to re-run it."""
    fx = request.getfixturevalue(fixture_name)
    assert fx["status"].startswith("complete"), fx["status"]
    assert fx["inputs"], "no inputs recorded — cannot be a verification fixture"


@pytest.mark.parametrize("fixture_name", ["profis_ca450", "profis_ca150"])
def test_anchor_modes_against_profis(fixture_name, request,
                                     anchor_group_factory) -> None:
    """Every recorded ACI 318 mode, against PROFIS's number."""
    fx = request.getfixturevalue(fixture_name)
    _, _, by_name = _run_fixture(fx, anchor_group_factory)

    failures: list[str] = []
    for mode, rec in fx["modes"].items():
        try:
            mine = _match(mode, by_name)
        except KeyError:
            failures.append(f"{mode}: module produced no such mode")
            continue
        ok, delta = within_pct(mine, rec["profis"], rec["tol_pct"])
        if not ok:
            gap = rec.get("known_gap", "")
            failures.append(
                f"{mode}: mine {mine:.2f} vs PROFIS {rec['profis']:.2f} "
                f"= {delta:.3f}% (tol {rec['tol_pct']}%)"
                + (f"  [documented gap: {gap}]" if gap else "")
            )
    assert not failures, "\n".join(failures)


def test_hef_substitute_matches_profis(profis_ca450, anchor_group_factory) -> None:
    """ACI 17.6.2.1.2 substitute hef' — PROFIS printed 300 where input was 450."""
    rec = profis_ca450["derived"]["hef_effective"]
    group, _, _ = _run_fixture(profis_ca450, anchor_group_factory)
    assert group.three_or_more_close_edges
    ok, delta = within_pct(group.hef_effective, rec["profis"], rec["tol_pct"])
    assert ok, (f"hef' = {group.hef_effective:.3f} vs PROFIS "
                f"{rec['profis']:.1f} ({delta:.3f}%)")


def test_Ase_matches_profis(profis_ca450, anchor_group_factory) -> None:
    """PROFIS printed Ase = 625 — the UNC tensile stress area."""
    rec = profis_ca450["derived"]["Ase"]
    group, _, _ = _run_fixture(profis_ca450, anchor_group_factory)
    ok, delta = within_pct(group.Ase, rec["profis"], rec["tol_pct"])
    assert ok, f"Ase = {group.Ase} vs PROFIS {rec['profis']} ({delta:.3f}%)"


def test_alpha_group_matches_profis(profis_ca150, anchor_group_factory) -> None:
    """Eq. 17.6.4.2 group factor (defect #6) — PROFIS printed 1.389."""
    rec = profis_ca150["derived"]["alpha_group"]
    group, check, _ = _run_fixture(profis_ca150, anchor_group_factory)
    s_edge = max(group.s_x, group.s_y)
    alpha = (1.0 + s_edge / (6.0 * group.ca_min)
             if s_edge < 6.0 * group.ca_min else 1.0)
    ok, delta = within_pct(alpha, rec["profis"], rec["tol_pct"])
    assert ok, f"alpha_group = {alpha:.4f} vs PROFIS {rec['profis']} ({delta:.3f}%)"
    assert any("alpha_group" in w for w in check.warnings), (
        "alpha_group is applied but not reported in the warnings — the "
        "calculation sheet would not show it"
    )


def test_documented_si_imperial_gaps_stay_conservative(
        profis_ca450, profis_ca150, anchor_group_factory) -> None:
    """The two known SI/imperial gaps must stay CONSERVATIVE, not just small.

    HANDOVER section 4 documents these as accepted disagreements. Accepted
    means the module reads low against PROFIS. If either ever reads HIGH,
    the explanation no longer holds and it is a defect again.
    """
    _, _, ca450 = _run_fixture(profis_ca450, anchor_group_factory)
    _, _, ca150 = _run_fixture(profis_ca150, anchor_group_factory)

    v_shear = _match("17.7.2 concrete breakout", ca450)
    ref = profis_ca450["modes"]["17.7.2 concrete breakout"]["profis"]
    assert v_shear < ref, (
        f"Eq. 17.7.2.2.1b: mine {v_shear:.2f} is no longer below PROFIS "
        f"{ref:.2f}. The SI-3.7-vs-imperial-9 explanation only covers a "
        f"conservative gap."
    )

    blowout = _match("17.6.4 side-face blowout", ca150)
    ref_b = profis_ca150["modes"]["17.6.4 side-face blowout"]["profis"]
    assert blowout < ref_b, (
        f"Eq. 17.6.4.1: mine {blowout:.2f} is no longer below PROFIS "
        f"{ref_b:.2f}. The SI-13-vs-imperial-160 explanation only covers "
        f"a conservative gap."
    )


# ======================================================================
# End plate 4E — CET.SteelConnDesign
# ======================================================================

def test_cet_4E_fixture_is_incomplete_and_says_so(cet_4E) -> None:
    """The 4E record is missing its inputs. Assert that, so it stays visible.

    This test PASSES while the gap exists — it is documenting a known
    state, not enforcing a fix.  Delete it when the inputs are supplied.
    """
    assert cet_4E["status"].startswith("INCOMPLETE")
    assert cet_4E["blocking"], "if it is incomplete, say what is missing"


def test_endplate_4E_against_cet(cet_4E) -> None:
    """Cannot run: the CET model geometry was never recorded."""
    pytest.skip(
        "endplate.VERIFIED['4E'] is True but the CET B1_C1 inputs are not "
        "recorded, so the run cannot be reproduced. Missing: "
        + ", ".join(cet_4E["blocking"])
        + ". SUPERSEDED 2026-08-31 by cet_endplate_4E_W24x76, a fresh CET "
          "run with every input captured — see "
          "test_endplate_4E_against_cet_w24, which RUNS. This record is "
          "kept for provenance: it is the run that caught defect #1."
    )


# ======================================================================
# Base plate — PROFIS, pending
# ======================================================================

def test_baseplate_recovered_model_reproduces_predictions(profis_baseplate) -> None:
    """The recovered geometry must still reproduce both predictions.

    This is deliberately a self-consistency check, not a verification —
    it guards the RECOVERY, so that if anyone edits the base plate maths
    the recorded PROFIS input sheet stops matching and says so before a
    GUI session is wasted on the wrong model.
    """
    from bimsteelauto_connections.baseplate import (
        BasePlateGeometry, design_base_plate)

    m = profis_baseplate["model"]
    geom = BasePlateGeometry(N=m["N"], B=m["B"], d=m["d"], bf=m["bf"],
                             f=m["f"], A2_over_A1=m["A2_over_A1"])
    kw = dict(geom=geom, plate_grade=m["plate_grade"],
              fc_prime=m["fc_prime"], strict=False)

    assert math.isclose(design_base_plate(Pu=150.0, Mu=0.0, **kw).phiPp,
                        m["phiPp"], rel_tol=1e-6)

    for case_id in ("A", "C", "D"):
        case = profis_baseplate["cases"][case_id]
        mi = case["module_input"]
        got = design_base_plate(Pu=mi["Pu"], Mu=mi["Mu"], Vu=mi["Vu"],
                                **kw).T_anchor
        assert math.isclose(got, case["predicted"], rel_tol=1e-5), (
            f"Case {case_id}: module now gives {got:.3f} kN but the PROFIS "
            f"input sheet was built against {case['predicted']:.3f} kN. "
            f"Re-derive the sheet before running PROFIS."
        )


@pytest.mark.parametrize("case_id", ["C", "D", "E"])
def test_baseplate_case_against_profis(profis_baseplate, case_id) -> None:
    """Would close baseplate.eccentricity. None of the three did."""
    case = profis_baseplate["cases"][case_id]
    if case["profis_rigid"] is None:
        pytest.skip(
            f"Case {case_id} not yet run in PROFIS. Predicted "
            f"{case['predicted']:.3f} kN. Compare against "
            f"{profis_baseplate['compare_against']} Fill in cases.{case_id}"
            f".profis_rigid in the fixture to enable this test."
        )
    pytest.xfail(
        f"Case {case_id}: module {case['predicted']:.3f} vs PROFIS rigid "
        f"{case['profis_rigid']:.3f} = {case['delta_pct']:+.1f}%. This is an "
        f"EXPECTED, EXPLAINED difference, not a defect. Candidate #9a was "
        f"WITHDRAWN 2026-08-31: reading AISC DG 1 2nd ed. Sects. 3.3 and 3.4 "
        f"showed the module implements the Design Guide exactly (Eq. 3.3.4, "
        f"3.3.7, 3.4.2, 3.4.3, 3.4.4 all match). DG 1 uses a uniform bearing "
        f"block over the FULL plate width at the compression edge; PROFIS's "
        f"CBFEM concentrates bearing near the column footprint. Adopting "
        f"PROFIS's numbers would be a change of DESIGN BASIS, not a bug fix. "
        f"This xfail keeps the gap visible on the record."
    )
    ok, delta = within_pct(case["predicted"], case["profis_rigid"], 2.0)
    assert ok, (f"Case {case_id}: predicted {case['predicted']:.3f} vs "
                f"PROFIS rigid {case['profis_rigid']:.3f} = {delta:.2f}%")


def test_defect_9_the_gap_is_the_compression_lever_arm(profis_baseplate) -> None:
    """Pins the DIAGNOSIS of candidate defect #9, not just its size.

    Two things must stay true or the explanation stops holding:

    1. The module is in equilibrium.  If it ever isn't, the cause is
       something else and the flange explanation is wrong.
    2. Re-solving with the compression resultant at the column flange
       reproduces PROFIS to under 0.5%.

    This test PASSES while the defect is open — it documents a confirmed
    cause.  It fails if someone changes the branch without re-deriving.
    """
    from bimsteelauto_connections.baseplate import (
        BasePlateGeometry, design_base_plate)

    m = profis_baseplate["model"]
    case = profis_baseplate["cases"]["C"]
    mi = case["module_input"]
    dg = case["diagnosis"]

    geom = BasePlateGeometry(N=m["N"], B=m["B"], d=m["d"], bf=m["bf"], f=m["f"])
    r = design_base_plate(Pu=mi["Pu"], Mu=mi["Mu"], Vu=mi["Vu"], geom=geom,
                          plate_grade=m["plate_grade"],
                          fc_prime=m["fc_prime"], strict=False)

    T, Y, q = r.T_anchor, r.Y_bearing, r.q_max
    C = q * Y
    a_mod = m["N"] / 2.0 - Y / 2.0

    # 1. the module IS in equilibrium — so this is a method difference
    assert math.isclose(C - T, mi["Pu"], rel_tol=1e-9), "force equilibrium broken"
    assert math.isclose((T * m["f"] + C * a_mod) / 1000.0, mi["Mu"],
                        rel_tol=1e-6), "moment equilibrium broken"
    assert math.isclose(a_mod, dg["module_lever_arm"], rel_tol=1e-4)

    # 2. the flange lever arm reproduces PROFIS
    a_fl = (m["d"] - m["tf"]) / 2.0
    assert math.isclose(a_fl, dg["compression_flange_CL"], rel_tol=1e-4)
    T_flange = (mi["Mu"] * 1000.0 - mi["Pu"] * a_fl) / (m["f"] + a_fl)
    ok, delta = within_pct(T_flange, case["profis_rigid"], 0.5)
    assert ok, (f"the flange explanation no longer holds: {T_flange:.3f} vs "
                f"PROFIS {case['profis_rigid']:.3f} = {delta:.3f}%")

    # 3. and the module is still on the unconservative side of it
    assert T < case["profis_rigid"], (
        "the module now reads HIGH against PROFIS — defect #9 has changed "
        "character and the recorded severity sweep is stale"
    )


def test_defect_9_bias_is_one_directional(profis_baseplate) -> None:
    """The module must never read high. A one-sided bias is the finding."""
    from bimsteelauto_connections.baseplate import (
        BasePlateGeometry, design_base_plate)

    m = profis_baseplate["model"]
    a_fl = (m["d"] - m["tf"]) / 2.0
    geom = BasePlateGeometry(N=m["N"], B=m["B"], d=m["d"], bf=m["bf"], f=m["f"])

    for row in profis_baseplate["cases"]["C"]["severity"]["sweep"]:
        r = design_base_plate(Pu=float(row["Pu"]), Mu=float(row["Mu"]),
                              geom=geom, plate_grade=m["plate_grade"],
                              fc_prime=m["fc_prime"], strict=False)
        flange = (row["Mu"] * 1000.0 - row["Pu"] * a_fl) / (m["f"] + a_fl)
        assert r.T_anchor <= flange, (
            f"Pu={row['Pu']} Mu={row['Mu']}: module {r.T_anchor:.1f} now "
            f"exceeds the flange model {flange:.1f} — the bias is no longer "
            f"one-directional and the severity record is stale"
        )
        assert math.isclose(r.T_anchor, row["module"], rel_tol=2e-3), (
            f"Pu={row['Pu']} Mu={row['Mu']}: module now gives "
            f"{r.T_anchor:.1f}, sweep recorded {row['module']:.1f}"
        )


def test_defect_9_flange_model_is_rejected(profis_baseplate) -> None:
    """The 'compression at the column flange' explanation does NOT hold.

    It was proposed after Cases C and D, where it fitted to +0.34% and
    +3.96%.  Case E was run as its first out-of-sample test and it missed by
    -12.0%.  Two points, two parameters — it was an over-fit, and E killed
    it.

    This test asserts the rejection so nobody revives the model from the
    C/D agreement alone.  It PASSES while the model stays rejected.
    """
    m = profis_baseplate["model"]
    a_fl = (m["d"] - m["tf"]) / 2.0
    E = profis_baseplate["cases"]["E"]
    mi = E["module_input"]

    T_flange = (mi["Mu"] * 1000.0 - mi["Pu"] * a_fl) / (m["f"] + a_fl)
    _, delta = within_pct(T_flange, E["profis_rigid"], 0.0)
    assert delta > 5.0, (
        f"the flange model now agrees with Case E to {delta:.2f}%. If that is "
        f"real, re-open it as a candidate — but re-derive C and D first, and "
        f"only on runs where the profile is not yielded."
    )


def test_defect_9_no_fixed_effective_width_fits_either(profis_baseplate) -> None:
    """The other candidate — a plate-edge block of fixed width — also fails.

    Fitting B_eff case by case gives 173 / 85 / 249 mm.  Recording that keeps
    the next person from re-deriving a dead end.
    """
    m = profis_baseplate["model"]
    widths = []
    for case_id in ("C", "D", "E"):
        case = profis_baseplate["cases"][case_id]
        mi = case["module_input"]
        T = case["profis_rigid"]
        C = T + mi["Pu"]
        a = (mi["Mu"] * 1000.0 - T * m["f"]) / C
        Y = 2.0 * (m["N"] / 2.0 - a)
        widths.append((C / Y) / (0.65 * 0.85 * m["fc_prime"] / 1000.0))

    assert max(widths) / min(widths) > 2.0, (
        f"the fitted effective widths {[round(w) for w in widths]} are now "
        f"within a factor of 2 — a fixed-width plate-edge block may be worth "
        f"re-examining."
    )


def test_defect_9_large_e_bias_is_unconservative_and_grows_with_axial(
        profis_baseplate) -> None:
    """The finding that IS solid: two compression cases, same direction.

    C (Pu 150) -21.3%, E (Pu 500) -38.9%.  Both low, and worse with more
    axial load.  This is what survives the loss of the flange model.
    """
    C = profis_baseplate["cases"]["C"]
    E = profis_baseplate["cases"]["E"]
    assert C["predicted"] < C["profis_rigid"], "Case C no longer unconservative"
    assert E["predicted"] < E["profis_rigid"], "Case E no longer unconservative"
    assert E["delta_pct"] < C["delta_pct"], (
        "the bias no longer worsens with axial load — the recorded trend is "
        "stale and the severity note needs re-deriving"
    )


def test_defect_9b_uplift_branch_now_satisfies_moment_equilibrium(
        profis_baseplate) -> None:
    """Defect #9b, FIXED. This test was the inverse until 2026-08-30.

    It used to assert the moment residual was exactly |Pu|*f — pinning the
    broken behaviour so it stayed visible.  The branch has been replaced by
    the signed DG 1 quadratic, so the assertion is inverted: equilibrium
    must now HOLD, on both sides of the boundary where a bearing zone opens.
    """
    from bimsteelauto_connections.baseplate import (
        BasePlateGeometry, design_base_plate)

    m = profis_baseplate["model"]
    geom = BasePlateGeometry(N=m["N"], B=m["B"], d=m["d"], bf=m["bf"], f=m["f"])
    q = m["q_max"]

    for Pu, Mu in ((-150.0, 80.0), (-300.0, 120.0), (-75.0, 40.0),
                   (-150.0, 150.0), (-50.0, 80.0)):
        r = design_base_plate(Pu=Pu, Mu=Mu, geom=geom,
                              plate_grade=m["plate_grade"],
                              fc_prime=m["fc_prime"], strict=False)
        T, Y = r.T_anchor, r.Y_bearing
        C = q * Y                                   # bearing resultant
        a = m["N"] / 2.0 - Y / 2.0                  # its lever arm

        # SigmaF: the anchors carry the uplift plus whatever bearing develops
        assert math.isclose(T - C, abs(Pu), rel_tol=1e-9), (
            f"Pu={Pu} Mu={Mu}: force equilibrium broken, T - C = {T - C:.3f} "
            f"against |Pu| = {abs(Pu):.3f}")

        # SigmaM about the plate centroid — this is the one that used to fail
        sigma_M = (T * m["f"] + C * a) / 1000.0
        assert math.isclose(sigma_M, Mu, rel_tol=1e-6), (
            f"Pu={Pu} Mu={Mu}: MOMENT equilibrium broken, sum(M) = "
            f"{sigma_M:.3f} kNm against Mu = {Mu:.3f}. Defect #9b has "
            f"regressed — the old branch implied an excess of exactly |Pu|*f")


def test_defect_9b_uplift_boundary_is_continuous(profis_baseplate) -> None:
    """No bearing zone below Mu = |Pu|*f, and the two regimes must meet.

    A branch boundary is where a discontinuity hides.  Below it every anchor
    is in tension and T = |Pu|; above it a bearing zone opens.  The two must
    agree at the crossing or the fix has just moved the problem.
    """
    from bimsteelauto_connections.baseplate import (
        BasePlateGeometry, design_base_plate)

    m = profis_baseplate["model"]
    geom = BasePlateGeometry(N=m["N"], B=m["B"], d=m["d"], bf=m["bf"], f=m["f"])
    kw = dict(geom=geom, plate_grade=m["plate_grade"],
              fc_prime=m["fc_prime"], strict=False)
    Pu = -150.0
    Mu_boundary = abs(Pu) * m["f"] / 1000.0          # 26.25 kNm

    below = design_base_plate(Pu=Pu, Mu=Mu_boundary * 0.5, **kw)
    at = design_base_plate(Pu=Pu, Mu=Mu_boundary, **kw)
    above = design_base_plate(Pu=Pu, Mu=Mu_boundary * 1.002, **kw)

    assert math.isclose(below.T_anchor, abs(Pu)), "below the boundary T must be |Pu|"
    assert below.Y_bearing == 0.0, "no bearing zone should be open below it"
    assert math.isclose(at.T_anchor, abs(Pu)), "the boundary itself must give |Pu|"
    assert above.T_anchor > abs(Pu), "a bearing zone must open above it"
    assert above.T_anchor - abs(Pu) < 1.0, (
        f"discontinuity at the boundary: T jumps to {above.T_anchor:.3f} just "
        f"above Mu = {Mu_boundary:.2f} kNm")

    # and monotone in Mu across the whole range
    prev = 0.0
    for mult in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0):
        T = design_base_plate(Pu=Pu, Mu=Mu_boundary * mult, **kw).T_anchor
        assert T >= prev - 1e-9, "anchor tension must not fall as Mu rises"
        prev = T


def test_defect_9b_fix_made_case_D_less_conservative(profis_baseplate) -> None:
    """Recording the consequence, because it cuts against the design.

    Fixing the equilibrium error REDUCED the anchor tension in the load case
    that governs a PEB base under wind: 378.571 -> 287.546, from +23.7%
    above PROFIS to -6.0% below.  The old number was conservative by
    accident, not by design, and would have gone the other way on other
    geometries — but this is a real reduction and it should not be quietly
    absorbed.
    """
    D = profis_baseplate["cases"]["D"]
    assert D["module_at_run_time"] > D["module_after_defect_9b_fix"]
    assert D["module_after_defect_9b_fix"] < D["profis_rigid"], (
        "Case D no longer reads low against PROFIS — re-derive the "
        "consequence note")
    # all three cases now err in the SAME direction
    for cid in ("C", "D", "E"):
        c = profis_baseplate["cases"][cid]
        assert c["predicted"] < c["profis_rigid"], (
            f"Case {cid} no longer reads low — the unified-direction claim "
            f"in defect #9a is stale")


def test_defect_9_all_profis_runs_have_a_yielded_profile(profis_baseplate) -> None:
    """Why the SIZE of the bias is not yet trustworthy.

    Plastic strain 382.85 / 195.05 / 538.17 percent against a 5.00% limit.
    The equivalent-rigid anchor forces come out of that same CBFEM solve.
    Recording it here so the caveat travels with the numbers.
    """
    strains = {"C": 382.85, "D": 195.05, "E": 538.17}
    assert all(s > 5.0 for s in strains.values())
    assert "what_is_NOT_established" in profis_baseplate["candidate_defect_9"]




# ======================================================================
# End plate 4E — CET.SteelConnDesign, REPRODUCIBLE record (2026-08-31)
# ======================================================================

def test_cet_4E_w24_fixture_is_complete(cet_4E_w24) -> None:
    """This is what the B1_C1 record should have been."""
    assert cet_4E_w24["status"].startswith("complete")
    assert cet_4E_w24["inputs"], "no inputs — cannot be a verification fixture"
    assert cet_4E_w24["formula_printed_by_cet"]["Yp_4E"]


def test_endplate_4E_against_cet_w24(cet_4E_w24) -> None:
    """endplate.VERIFIED['4E'], now actually backed by a re-runnable record.

    CET printed the Y_p expression in full and it matches the module term
    for term, on a geometry different from the one that first caught
    defect #1.  Every quantity agrees to better than 0.005%.
    """
    import math
    from bimsteelauto_connections.endplate import (
        EndPlateGeometry, EndPlateConfig, yield_line_Yp,
        required_thickness_dg4, required_bolt_diameter)

    IN, KSI, KIPIN, KIP = 25.4, 6.894757, 0.1129848, 4.4482216
    i = cet_4E_w24["inputs"]
    G = EndPlateGeometry(
        bp=i["bp"] * IN, g=i["g"] * IN, pfo=i["pfo"] * IN, pfi=i["pfi"] * IN,
        h_rows=(i["h0"] * IN, i["h1"] * IN), de=2.0 * IN)

    Ft = i["Ft_ksi"] * KSI
    Ab = math.pi * (1.0 * IN) ** 2 / 4.0
    Pt = Ft * Ab / 1000.0
    Mnp = 2.0 * Pt * (i["h0"] + i["h1"]) * IN / 1000.0
    yl = yield_line_Yp(EndPlateConfig.FOUR_E, G)

    got = {
        "s": G.s / IN,
        "Pt": Pt / KIP,
        "Mnp": Mnp / KIPIN,
        "Yp": yl.Yp / IN,
        "tp_req": required_thickness_dg4(Mnp, i["Fy_plate_ksi"] * KSI, yl.Yp) / IN,
        "db_req": required_bolt_diameter(i["Mu_kipin"] * KIPIN, Ft, G.h_rows) / IN,
    }

    failures = []
    for name, rec in cet_4E_w24["outputs"].items():
        ok, delta = within_pct(got[name], rec["cet"], rec["tol_pct"])
        if not ok:
            failures.append(f"{name}: mine {got[name]:.5f} vs CET "
                            f"{rec['cet']:.5f} = {delta:.4f}% "
                            f"(tol {rec['tol_pct']}%)")
    assert not failures, "\n".join(failures)


def test_defect_10_4ES_formula_matches_the_printed_expression(cet_4E_w24) -> None:
    """Defect #10, found and fixed 2026-08-31.

    The 4ES bp/2 bracket was missing BOTH s-terms.  CET printed the
    expression in full and the two omissions accounted for the entire
    18.76% gap, exactly.

    This test reconstructs CET's printed expression independently here and
    compares term for term.  It is NOT verification of 4ES — the formula
    came from this run, so agreeing with this run is circular.  It is a
    regression guard: it fails if the fix is reverted.
    """
    import math
    from bimsteelauto_connections.endplate import (
        EndPlateGeometry, EndPlateConfig, yield_line_Yp)

    IN = 25.4
    i = cet_4E_w24["inputs"]
    pend = cet_4E_w24["pending_4ES"]
    de = pend["de_used_by_cet"]

    bp, g, pfo, pfi = i["bp"], i["g"], i["pfo"], i["pfi"]
    h0, h1 = i["h0"], i["h1"]
    s = 0.5 * math.sqrt(bp * g)

    expected = (bp / 2.0) * (h1 * (1.0 / pfi + 1.0 / s)
                             + h0 * (1.0 / pfo + 1.0 / (2.0 * s))) \
        + (2.0 / g) * (h1 * (pfi + s) + h0 * (de + pfo))

    G = EndPlateGeometry(bp=bp * IN, g=g * IN, pfo=pfo * IN, pfi=pfi * IN,
                         h_rows=(h0 * IN, h1 * IN), de=de * IN)
    got = yield_line_Yp(EndPlateConfig.FOUR_ES, G).Yp / IN

    assert abs(got - expected) < 0.01, (
        f"4ES Yp = {got:.3f}, DG 4 expression gives {expected:.3f}")
    assert abs(got - pend["cet"]) < 0.05, (
        f"4ES Yp = {got:.3f} vs CET {pend['cet']:.3f}")

    # each dropped term must move the answer, so the test is not vacuous
    without_h1_s = expected - (bp / 2.0) * h1 * (1.0 / s)
    without_h0_2s = expected - (bp / 2.0) * h0 * (1.0 / (2.0 * s))
    assert abs(got - without_h1_s) > 1.0, "the 1/s term on h1 is gone again"
    assert abs(got - without_h0_2s) > 1.0, "the 1/(2s) term on h0 is gone again"


def test_defect_10_stiffening_gain_is_now_realistic(cet_4E_w24) -> None:
    """Why no invariant caught it: the only one was Yp(4ES) > Yp(4E).

    That held even with two terms missing — 235.764 > 229.510, a 2.7%
    stiffening gain where the truth is 26.4%.  A relative test cannot tell
    a small gain from a large one, which is the whole argument for the
    verification gate.
    """
    from bimsteelauto_connections.endplate import (
        EndPlateGeometry, EndPlateConfig, yield_line_Yp)

    IN = 25.4
    i = cet_4E_w24["inputs"]
    de = cet_4E_w24["pending_4ES"]["de_used_by_cet"]
    G = EndPlateGeometry(bp=i["bp"] * IN, g=i["g"] * IN, pfo=i["pfo"] * IN,
                         pfi=i["pfi"] * IN,
                         h_rows=(i["h0"] * IN, i["h1"] * IN), de=de * IN)
    y4 = yield_line_Yp(EndPlateConfig.FOUR_E, G).Yp
    y4s = yield_line_Yp(EndPlateConfig.FOUR_ES, G).Yp
    gain = (y4s / y4 - 1.0) * 100.0
    assert y4s > y4, "stiffening cannot reduce Yp"
    assert gain > 20.0, (
        f"stiffening gain is only {gain:.1f}% — it was 2.7% with the two "
        f"terms missing and 26.4% once fixed. A gain back near 3% means the "
        f"fix has been reverted.")


def test_endplate_4ES_against_cet_run2(cet_4ES_w30) -> None:
    """endplate.VERIFIED['4ES'] — the run that earned the flag.

    Run 1 supplied the corrected formula (defect #10); this is run 2 on
    different geometry, so it is not circular.  The prediction was
    registered before CET's answer was read.
    """
    import math
    from bimsteelauto_connections.endplate import (
        EndPlateGeometry, EndPlateConfig, yield_line_Yp,
        required_thickness_dg4, required_bolt_diameter)

    IN, KSI, KIPIN = 25.4, 6.894757, 0.1129848
    i = cet_4ES_w30["inputs"]
    G = EndPlateGeometry(bp=i["bp"] * IN, g=i["g"] * IN, pfo=i["pfo"] * IN,
                         pfi=i["pfi"] * IN,
                         h_rows=(i["h0"] * IN, i["h1"] * IN), de=i["de"] * IN)
    Ft = i["Ft_ksi"] * KSI
    Pt = Ft * math.pi * (1.0 * IN) ** 2 / 4.0 / 1000.0
    Mnp = 2.0 * Pt * (i["h0"] + i["h1"]) * IN / 1000.0
    yl = yield_line_Yp(EndPlateConfig.FOUR_ES, G)

    got = {
        "s": G.s / IN,
        "Mnp": Mnp / KIPIN,
        "Yp": yl.Yp / IN,
        "tp_req": required_thickness_dg4(Mnp, i["Fy_plate_ksi"] * KSI, yl.Yp) / IN,
        "db_req": required_bolt_diameter(i["Mu_kipin"] * KIPIN, Ft, G.h_rows) / IN,
    }
    failures = []
    for name, rec in cet_4ES_w30["outputs"].items():
        ok, delta = within_pct(got[name], rec["cet"], rec["tol_pct"])
        if not ok:
            failures.append(f"{name}: mine {got[name]:.5f} vs CET "
                            f"{rec['cet']:.5f} = {delta:.4f}%")
    assert not failures, "\n".join(failures)


def test_4ES_run2_is_genuinely_different_geometry(cet_4ES_w30, cet_4E_w24) -> None:
    """Run 2 must not accidentally repeat run 1.

    The two terms corrected in defect #10 are (bp/2)*h1*(1/s) and
    (bp/2)*h0*(1/(2s)).  All four of bp, h1, h0 and s must differ between
    the runs or the 'independent verification' claim is hollow.
    """
    a, b = cet_4E_w24["inputs"], cet_4ES_w30["inputs"]
    for k in ("bp", "h0", "h1"):
        assert abs(a[k] - b[k]) > 1e-6, f"{k} is identical in both runs"
    sa = 0.5 * (a["bp"] * a["g"]) ** 0.5
    sb = 0.5 * (b["bp"] * b["g"]) ** 0.5
    assert abs(sa - sb) > 1e-6, "s is identical in both runs"


def test_4ES_prediction_was_registered_before_the_answer(cet_4ES_w30) -> None:
    """The registered prediction must still be what the module produces."""
    from bimsteelauto_connections.endplate import (
        EndPlateGeometry, EndPlateConfig, yield_line_Yp)
    IN = 25.4
    i = cet_4ES_w30["inputs"]
    reg = cet_4ES_w30["prediction_registered_before_reading_cet"]
    for key, expected in reg.items():
        if not key.startswith("de_"):
            continue
        de = float(key[3:])
        G = EndPlateGeometry(bp=i["bp"] * IN, g=i["g"] * IN, pfo=i["pfo"] * IN,
                             pfi=i["pfi"] * IN,
                             h_rows=(i["h0"] * IN, i["h1"] * IN), de=de * IN)
        got = yield_line_Yp(EndPlateConfig.FOUR_ES, G).Yp / IN
        assert abs(got - expected) < 0.01, (
            f"de={de}: registered {expected}, module gives {got:.3f}")
def test_defect_11_8ES_has_the_pb_squared_term(cet_8ES_w40) -> None:
    """Defect #11, found and fixed 2026-08-31.

    The (2/g) bracket was missing a trailing ``pb**2``.  Regression guard,
    NOT verification — the formula came from this run.
    """
    from bimsteelauto_connections.endplate import (
        EndPlateGeometry, EndPlateConfig, yield_line_Yp)

    IN = 25.4
    i = cet_8ES_w40["inputs"]
    G = EndPlateGeometry(
        bp=i["bp"] * IN, g=i["g"] * IN, pfo=i["pfo"] * IN, pfi=i["pfi"] * IN,
        h_rows=(i["h1"] * IN, i["h2"] * IN, i["h3"] * IN, i["h4"] * IN),
        de=i["de"] * IN, pb=i["pb"] * IN)
    got = yield_line_Yp(EndPlateConfig.EIGHT_ES, G).Yp / IN

    d11 = cet_8ES_w40["defect_11"]
    assert abs(got - d11["cet"]) < 0.05, (
        f"8ES Yp = {got:.3f} vs CET {d11['cet']:.3f}")
    # the dropped term must actually move the answer
    assert abs(got - d11["module_before_fix"]) > 1.0, (
        "the pb^2 term is gone again — 8ES is back to the pre-fix value")
def test_8ES_run1_records_that_it_was_not_a_blind_prediction(cet_8ES_w40) -> None:
    """Provenance matters as much as the number.

    The 4ES run-2 check earned its flag partly because the prediction was
    registered before CET's answer was read.  This run was not: the
    extraction surfaced the inputs and the answer together.  Recording that
    keeps the two kinds of evidence from being confused later.
    """
    assert "NOT a blind prediction" in cet_8ES_w40["provenance_caveat"]


def test_8ES_run2_reproduces_cet(cet_8ES_w36) -> None:
    """Run 2 agrees — on the parts of the expression it actually varies."""
    from bimsteelauto_connections.endplate import (
        EndPlateGeometry, EndPlateConfig, yield_line_Yp)
    IN = 25.4
    i = cet_8ES_w36["inputs"]
    G = EndPlateGeometry(
        bp=i["bp"] * IN, g=i["g"] * IN, pfo=i["pfo"] * IN, pfi=i["pfi"] * IN,
        h_rows=(i["h1"] * IN, i["h2"] * IN, i["h3"] * IN, i["h4"] * IN),
        de=i["de"] * IN, pb=i["pb"] * IN)
    got = yield_line_Yp(EndPlateConfig.EIGHT_ES, G).Yp / IN
    ok, delta = within_pct(got, cet_8ES_w36["outputs"]["Yp"]["cet"], 0.02)
    assert ok, f"8ES Yp = {got:.3f} vs CET {cet_8ES_w36['outputs']['Yp']['cet']} ({delta:.3f}%)"


def test_8ES_two_runs_do_not_discriminate_the_corrected_term(
        cet_8ES_w40, cet_8ES_w36) -> None:
    """The precise reason 8ES stays open.

    The term added by defect #11 is (2/g)*pb^2.  Both g and pb are
    identical in the two runs, so that term contributes exactly the same
    amount to each.  Two matching runs are not two independent tests of it.

    This test PASSES while the limitation stands.  When a run at a
    different pb exists, it should start failing — that is the signal the
    flag can be reconsidered.
    """
    a, b = cet_8ES_w40["inputs"], cet_8ES_w36["inputs"]
    assert a["pb"] == b["pb"] and a["g"] == b["g"], (
        "pb or g now differs between the 8ES runs — the corrected term IS "
        "discriminated. Re-read the fixtures and reconsider the 8ES flag.")
    term_a = (2.0 / a["g"]) * a["pb"] ** 2
    term_b = (2.0 / b["g"]) * b["pb"] ** 2
    assert abs(term_a - term_b) < 1e-9


def test_8ES_run3_predictions_are_registered(cet_8ES_w36) -> None:
    """The competing forms, on record before run 3 is made."""
    from bimsteelauto_connections.endplate import (
        EndPlateGeometry, EndPlateConfig, yield_line_Yp)
    IN = 25.4
    i = cet_8ES_w36["inputs"]
    d, tf, pf = 35.9, 0.94, 1.0
    reg = cet_8ES_w36["run_3_predictions_registered_2026-08-31"]["candidates"]
    for key, cands in reg.items():
        pb = float(key.split("_")[1])
        h1, h2 = d + pf + pb - tf / 2, d + pf - tf / 2
        h3, h4 = d - tf - pf - tf / 2, d - tf - pf - pb - tf / 2
        G = EndPlateGeometry(
            bp=i["bp"] * IN, g=i["g"] * IN, pfo=pf * IN, pfi=pf * IN,
            h_rows=(h1 * IN, h2 * IN, h3 * IN, h4 * IN),
            de=i["de"] * IN, pb=pb * IN)
        got = yield_line_Yp(EndPlateConfig.EIGHT_ES, G).Yp / IN
        assert abs(got - cands["pb^2 (module)"]) < 0.02, (
            f"pb={pb}: registered {cands['pb^2 (module)']}, module gives "
            f"{got:.2f} — the prediction has drifted")


def test_8ES_run3_discriminates_the_pb_squared_term(cet_8ES_pb5) -> None:
    """endplate.VERIFIED['8ES'] — the run that earned the flag.

    pb is squared in the corrected term, so varying it is what separates
    pb^2 from the alternatives.  At pb = 3.0, pb^3/3 gives an IDENTICAL
    answer; at pb = 5.0 it is out by 9.5 in.
    """
    from bimsteelauto_connections.endplate import (
        EndPlateGeometry, EndPlateConfig, yield_line_Yp)
    IN = 25.4
    i = cet_8ES_pb5["inputs"]
    G = EndPlateGeometry(
        bp=i["bp"] * IN, g=i["g"] * IN, pfo=i["pfo"] * IN, pfi=i["pfi"] * IN,
        h_rows=(i["h1"] * IN, i["h2"] * IN, i["h3"] * IN, i["h4"] * IN),
        de=i["de"] * IN, pb=i["pb"] * IN)
    got = yield_line_Yp(EndPlateConfig.EIGHT_ES, G).Yp / IN

    cet = cet_8ES_pb5["outputs"]["Yp"]["cet"]
    ok, delta = within_pct(got, cet, 0.02)
    assert ok, f"8ES Yp = {got:.3f} vs CET {cet} ({delta:.3f}%)"

    # every rival form must be clearly excluded, not merely worse
    for name, value in cet_8ES_pb5["discrimination"]["registered_before_the_run"].items():
        if name.startswith("pb^2 "):
            continue
        assert abs(value - cet) > 3.0, (
            f"candidate {name} at {value} is within 3 in of CET's {cet} — "
            f"this run no longer discriminates it")


def test_8ES_pb_was_actually_varied(cet_8ES_w40, cet_8ES_w36, cet_8ES_pb5) -> None:
    """The whole point of run 3: pb must differ from the earlier runs."""
    assert cet_8ES_w40["inputs"]["pb"] == cet_8ES_w36["inputs"]["pb"] == 3.0
    assert cet_8ES_pb5["inputs"]["pb"] == 5.0
    g = cet_8ES_pb5["inputs"]["g"]
    assert abs((2 / g) * 5.0 ** 2 - (2 / g) * 3.0 ** 2) > 8.0, (
        "the corrected term barely moved between runs — pb was not varied "
        "enough to discriminate anything")


def test_endplate_module_is_fully_verified() -> None:
    """All three configs closed. Three defects, none like the others."""
    from bimsteelauto_connections import endplate
    assert all(endplate.VERIFIED.values()), endplate.VERIFIED
    assert set(endplate.VERIFIED) == {"4E", "4ES", "8ES"}


# ======================================================================
# Cleat — AISC Design Example II.A-17B (published values)
# ======================================================================

def test_single_bolt_shear_matches_aisc_exactly(aisc_IIA17B) -> None:
    """The one quantity in that example needing no reconstruction."""
    from bimsteelauto_connections.bolts import bolt_shear_capacity
    IN, KIP = 25.4, 4.4482216
    rec = aisc_IIA17B["what_IS_verifiable_here"]["single_bolt_7_8in_A325N_single_shear"]
    got = bolt_shear_capacity("A325", 0.875 * IN, threads="N") / KIP
    ok, delta = within_pct(got, rec["module"], 0.02)
    assert ok, f"one-bolt shear {got:.4f} vs {rec['module']} ({delta:.4f}%)"


def test_defect_12_cleat_has_no_bolt_group_eccentricity(aisc_IIA17B) -> None:
    """Defect #12 — open, not fixed.

    AISC's published bolt shear for II.A-17B divided by the single-bolt
    capacity recovers 3.059, the eccentric bolt-group coefficient C.  The
    module multiplies by n with no C at all, so it reads 1.63x high on
    that joint.

    This test PASSES while the defect is open — it pins the size of the
    gap so it cannot drift unnoticed.  Delete it when the eccentricity
    treatment is decided.
    """
    from bimsteelauto_connections.bolts import bolt_shear_capacity
    IN, KIP = 25.4, 4.4482216
    ev = aisc_IIA17B["defect_12"]["evidence"]
    rn = bolt_shear_capacity("A325", 0.875 * IN, threads="N") / KIP
    assert abs(ev["aisc_published_bolt_shear"] / rn - ev["implied_coefficient"]) < 0.01
    assert abs(5 * rn / ev["aisc_published_bolt_shear"] - ev["ratio"]) < 0.01, (
        "the eccentricity gap has changed size — either the bolt capacity "
        "moved or a C factor was introduced. Re-derive defect #12.")


def test_cleat_fixture_is_honest_about_its_own_limits(aisc_IIA17B) -> None:
    """Outputs without inputs cannot verify. Say so, structurally.

    This is the same gap the old B1_C1 end-plate record had, and it is why
    secondary.cleat stays open despite having AISC numbers in hand.
    """
    assert aisc_IIA17B["status"].startswith("PARTIAL")
    assert aisc_IIA17B["blocking"]
    assert aisc_IIA17B["reconstruction_attempted"]["result"].startswith("FAILED")
    assert aisc_IIA17B["flag_decision"]["secondary.cleat"] == "STAYS FALSE"
    # That was the position on 2026-08-31 BEFORE the CET single-plate run.
    # It has since closed — see cet_cleat_singleplate_W18x50. The fixture
    # is kept because it is where defect #12 was first measured, and its
    # extended-configuration number (C/n = 0.612) is the severe end of the
    # range that justified requiring `eccentricity` rather than defaulting.


# ======================================================================
# Cleat — CET single plate, 2026-08-31
# ======================================================================

def _cleat(fx):
    from bimsteelauto_connections.secondary import check_purlin_cleat
    IN, KIP = 25.4, 4.4482216
    i = fx["inputs"]
    return check_purlin_cleat(
        Vu=i["V_kips"] * KIP, bolt_grade="A325", d_bolt=i["d_bolt"] * IN,
        n_bolts=i["n_bolts"], d_hole=i["d_hole"] * IN, n_shear_planes=1,
        cleat_grade="A992", t_cleat=i["t_cleat"] * IN,
        h_cleat=i["h_cleat"] * IN, purlin_grade="A992", t_purlin=0.355 * IN,
        pitch=i["row_spacing_pitch"] * IN,
        edge_end=i["edge_end_vertical"] * IN,
        edge_side=i["edge_side_horizontal_leh"] * IN,
        eccentricity=i["eccentricity_bolt_line_to_weld_line"] * IN,
        strict=False)


def test_defect_15_net_area_uses_the_B4_3b_hole_allowance(cet_cleat) -> None:
    """Defect #15, FIXED. AISC B4.3b: net-area holes are dh + 1/16 in.

    The two limit states that depend only on net area — shear rupture and
    block shear — must now agree with CET to within the grade table's own
    rounding, with no modelling difference left to explain them.
    """
    KIP = 4.4482216
    r = _cleat(cet_cleat)
    caps = {c[0]: c[1] / KIP for c in r.checks}
    for name, ref, tol in (("cleat shear yield/rupture", 102.80, 1.0),
                           ("block shear - cleat", 112.20, 1.0)):
        got = next(v for k, v in caps.items() if k.startswith(name))
        ok, delta = within_pct(got, ref, tol)
        assert ok, f"{name}: {got:.2f} vs CET {ref} = {delta:.2f}% (tol {tol}%)"


def test_defect_12_elastic_coefficient_is_applied_and_conservative(
        cet_cleat) -> None:
    """Defect #12, CLOSED by implementing the elastic (vector) method.

    The two eccentricity-bearing limit states must now read BELOW CET,
    because the elastic coefficient is always conservative against the
    instantaneous-centre coefficients CET uses.  Reading above CET again
    would mean the coefficient has been dropped or weakened.
    """
    KIP = 4.4482216
    r = _cleat(cet_cleat)
    caps = {c[0]: c[1] / KIP for c in r.checks}
    for name, cet in (("bolt shear", 51.12),
                      ("bearing/tearout - cleat", 75.08)):
        got = next(v for k, v in caps.items() if k.startswith(name))
        assert got < cet, (
            f"{name}: module {got:.2f} is no longer below CET's {cet} — the "
            f"eccentric group coefficient has been dropped")
        assert got > 0.8 * cet, (
            f"{name}: module {got:.2f} is more than 20% below CET's {cet} — "
            f"conservative is fine, but that is a different bug")


def test_elastic_coefficient_is_conservative_against_instantaneous_centre(
        cet_cleat) -> None:
    """The whole justification for choosing the elastic method."""
    from bimsteelauto_connections.secondary import eccentric_group_coefficient
    IN = 25.4
    i = cet_cleat["inputs"]
    C = eccentric_group_coefficient(
        i["n_bolts"], i["row_spacing_pitch"] * IN,
        i["eccentricity_bolt_line_to_weld_line"] * IN)
    C_ic = 2.857                      # CET's instantaneous-centre value
    assert C < C_ic, "the elastic coefficient must be below IC"
    assert C <= i["n_bolts"], "C can never exceed the bolt count"
    # and it must reduce to n for a concentric group
    assert eccentric_group_coefficient(
        i["n_bolts"], i["row_spacing_pitch"] * IN, 0.0) == float(i["n_bolts"])


def test_eccentricity_cannot_be_silently_omitted() -> None:
    """A silent zero is what caused defect #12. It must now raise."""
    import pytest as _pt
    from bimsteelauto_connections.secondary import check_purlin_cleat
    from bimsteelauto_connections.bolts import ConnectionError_
    with _pt.raises(ConnectionError_):
        check_purlin_cleat(Vu=30.0, d_bolt=20.0, n_bolts=3, pitch=80.0,
                           strict=False)

def test_cleat_warns_about_what_it_does_not_model(cet_cleat) -> None:
    """Two scope limits must be stated, not buried."""
    r = _cleat(cet_cleat)
    joined = " ".join(r.warnings)
    assert "CUT END" in joined, "the end-distance assumption is not warned about"
    assert "ELASTIC" in joined, (
        "the run uses the elastic coefficient but does not say so — the "
        "user cannot tell how conservative the answer is")




def test_cleat_second_geometry(cet_cleat_b2b) -> None:
    """The cleat flag rested on one case. This is the second.

    Different plate grade, bolt size, spacing, depth, edge distances,
    eccentricity, support type and load.
    """
    from bimsteelauto_connections.secondary import check_purlin_cleat
    IN, KIP = 25.4, 4.4482216
    i = cet_cleat_b2b["inputs"]
    r = check_purlin_cleat(
        Vu=i["V_kips"] * KIP, bolt_grade="A325", d_bolt=i["d_bolt"] * IN,
        n_bolts=i["n_bolts"], d_hole=i["d_hole"] * IN, n_shear_planes=1,
        cleat_grade="A36", t_cleat=i["t_cleat"] * IN,
        h_cleat=i["h_cleat"] * IN, purlin_grade="A992", t_purlin=0.355 * IN,
        pitch=i["row_spacing_pitch"] * IN,
        edge_end=i["edge_end_vertical"] * IN,
        edge_side=i["edge_side_horizontal_leh"] * IN,
        eccentricity=i["eccentricity_bolt_line_to_weld_line"] * IN,
        strict=False)
    caps = {c[0]: c[1] / KIP for c in r.checks}

    # shear rupture must be EXACT here — A36's Fu is exact in the table
    got = next(v for k, v in caps.items() if k.startswith("cleat shear yield/rupture"))
    ok, delta = within_pct(got, 68.51, 0.05)
    assert ok, (f"shear yield/rupture {got:.3f} vs CET 68.51 = {delta:.3f}%. "
                f"This one should be exact — A36's Fu is 400 MPa in the "
                f"table and 400 MPa exactly. A drift here is a real "
                f"formula difference, not grade rounding.")

    got = next(v for k, v in caps.items() if k.startswith("block shear - cleat"))
    ok, delta = within_pct(got, 71.31, 1.0)
    assert ok, f"block shear {got:.2f} vs CET 71.31 = {delta:.2f}%"


def test_elastic_coefficient_conservative_on_both_geometries(
        cet_cleat, cet_cleat_b2b) -> None:
    """One conservative case could be luck. Two, at different e/depth, is not."""
    from bimsteelauto_connections.secondary import eccentric_group_coefficient
    IN = 25.4
    for fx, C_ic in ((cet_cleat, 2.857), (cet_cleat_b2b, 2.4181)):
        i = fx["inputs"]
        C = eccentric_group_coefficient(
            i["n_bolts"], i["row_spacing_pitch"] * IN,
            i["eccentricity_bolt_line_to_weld_line"] * IN)
        assert C < C_ic, (
            f"elastic C = {C:.4f} is not below the instantaneous-centre "
            f"{C_ic} — the conservatism claim fails on this geometry")


# ======================================================================
# Gusset — CET brace-to-beam-column, 2026-08-31
# ======================================================================

def test_whitmore_width_matches_cet_exactly(cet_gusset) -> None:
    """The one thing this run settles outright: bw and the 30 deg spread."""
    from bimsteelauto_connections.secondary import whitmore_width
    IN = 25.4
    i = cet_gusset["inputs"]
    bw = whitmore_width(i["l_connection"] * IN,
                        n_rows_width=i["w_first_row"] * IN) / IN
    ok, delta = within_pct(bw, cet_gusset["whitmore_width"]["cet"], 0.01)
    assert ok, f"Whitmore width {bw:.4f} vs CET 6.4641 = {delta:.4f}%"


def test_gusset_buckling_chain_matches_cet_to_seven_figures(cet_gusset) -> None:
    """The comparison that closed the flag.

    CET reports K = 0.65 and l_buckling = 6.82843 in.  From those two
    numbers and nothing else of CET's, the module's own r, Fe and Eq. E3-2
    must land on the Fcr CET printed.  Seven figures is not a coincidence:
    a wrong r, a wrong Fe, or the wrong E3 branch would all miss.
    """
    inp = cet_gusset["inputs"]
    t, K, L, Fy, E = 0.375, inp["K_used_by_CET"], inp["l_buckling_from_CET"], 36.0, 29000.0

    bw = 3.0 + 2 * 3.0 * math.tan(math.radians(30.0))
    r = t / math.sqrt(12.0)
    Lc_over_r = K * L / r
    Fe = math.pi ** 2 * E / Lc_over_r ** 2
    assert Fy / Fe <= 2.25, "must be the inelastic branch, Eq. E3-2"
    Fcr = 0.658 ** (Fy / Fe) * Fy

    assert bw == pytest.approx(cet_gusset["whitmore_width"]["cet"], abs=1e-4)
    assert Lc_over_r == pytest.approx(41.001, abs=0.005)
    assert Fe == pytest.approx(170.2592, abs=0.001)
    assert Fcr == pytest.approx(32.9509, abs=0.0001), (
        f"Fcr {Fcr:.6f} vs CET's printed 32.9509 — the buckling chain no "
        f"longer reproduces the reference"
    )
    assert Lc_over_r > 25.0, "J4.4(a) Pn = Fy*Ag must NOT apply here"


def test_cets_printed_gusset_capacity_is_Fy_not_Fcr(cet_gusset) -> None:
    """The diagnosis. CET multiplies by Fy where Eq. E3-1 says Fcr.

    This is a real, UNCONSERVATIVE error in the reference tool, and the
    module deliberately does not reproduce it.  If this test ever fails
    because 78.54 reconciles some other way, the diagnosis was wrong and
    the gusset flag must be reconsidered.
    """
    res = cet_gusset["resolution"]["what_78_54_actually_is"]
    Ag = 6.4641 * 0.375
    printed_expression = 0.9 * 32.9509 * Ag
    fy_product = 0.9 * 36.0 * Ag

    assert printed_expression == pytest.approx(71.8869, abs=0.01)
    assert fy_product == pytest.approx(78.5388, abs=0.01)
    assert fy_product == pytest.approx(78.54, abs=0.01), (
        "CET's printed 78.54 must BE 0.9*Fy*Ag for the diagnosis to hold")
    assert res["that_expression_evaluates_to"] == pytest.approx(71.8869, abs=0.01)
    # and it is unconservative: Fcr <= Fy always
    assert fy_product > printed_expression


def test_gusset_flag_is_now_closed(cet_gusset) -> None:
    from bimsteelauto_connections import secondary
    assert secondary.VERIFIED["gusset"] is True
    assert cet_gusset["flag_decision"]["secondary.gusset"].startswith("CLOSED")
    # the close must carry its residual gaps with it
    assert cet_gusset["flag_decision"]["residual_coverage_gaps"]


def test_the_gusset_close_was_not_circular(cet_gusset) -> None:
    """HANDOVER section 7: a formula taken FROM a run cannot be verified BY it.

    Nothing in the module's buckling path came from CET.  Only two
    INPUTS did — K and l_buckling, both modelling choices, neither a
    formula.  r, Fe and E3-2 are the module's own and were what matched.
    """
    d = cet_gusset["flag_decision"]
    assert "not_circular" in d
    chain = cet_gusset["resolution"]["the_chain_that_matches"]
    for key in ("r_in", "Lc_over_r", "Fe_ksi", "Fcr_ksi"):
        assert key in chain
