"""AISC Design Guide 1, 2nd ed., Example 4.7 — Large Moment Base Plate.

This is the published worked example for the exact path defects #13 and
#14 sat in: large eccentricity, bearing length Y, anchor tension T, and
plate thickness from both the bearing and the tension interface.

READ THIS FIRST — it is NOT a blind check.  The worked solution arrived
in the same message as the inputs, so no prediction could be registered
before the answer was visible.  These tests therefore CONFIRM flags that
were already closed against the DG 1 text (HANDOVER section 7); they do
not close anything, and no module code was changed to match them.

The headline result: every quantity reproduces exactly EXCEPT Y and T,
and the reason is that Example 4.7 substitutes f = 9.5 in. two lines
after computing f = 8.5 in. for the same plate.
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto_connections import baseplate as bp
from conftest import load_fixture, within_pct

KIP = 4.4482216
KIPIN = 0.112984829
IN = 25.4
KSI = 6.894757


@pytest.fixture(scope="module")
def dg1_47() -> dict:
    return load_fixture("dg1_example_4_7_large_moment")


@pytest.fixture(scope="module")
def result():
    g = bp.BasePlateGeometry(N=20 * IN, B=20 * IN, d=12.7 * IN,
                             bf=12.2 * IN, f=8.5 * IN, tf=0.900 * IN)
    return bp.design_base_plate(Pu=376 * KIP, Mu=3600 * KIPIN, geom=g,
                                plate_grade="A36", fc_prime=4 * KSI,
                                strict=True)


# ----------------------------------------------------------------------
# What matches
# ----------------------------------------------------------------------

@pytest.mark.parametrize("name,mine_in,published,tol", [
    ("m",       lambda r, g: g.m / IN,            3.97, 0.2),
    ("n",       lambda r, g: g.n / IN,            5.12, 0.2),
    ("n_prime", lambda r, g: g.n_prime / IN,      3.11, 0.5),
])
def test_cantilever_dimensions_match_dg1(result, name, mine_in, published, tol):
    g = bp.BasePlateGeometry(N=20 * IN, B=20 * IN, d=12.7 * IN,
                             bf=12.2 * IN, f=8.5 * IN, tf=0.900 * IN)
    ok, delta = within_pct(mine_in(result, g), published, tol)
    assert ok, f"{name}: {mine_in(result, g):.4f} vs DG 1 {published} ({delta:.2f}%)"


def test_n_governs_as_dg1_says(result) -> None:
    """DG 1: 'n = 5.12 in. controls'."""
    assert result.governing_cantilever == "n"
    ok, d = within_pct(result.l_cantilever / IN, 5.12, 0.2)
    assert ok, d


def test_q_max_matches_dg1_exactly(result) -> None:
    """q_max = f_p(max) x B over the FULL width — DG 1 Eq. 3.3.4."""
    ok, d = within_pct(result.q_max * IN / KIP, 44.2, 0.1)
    assert ok, f"q_max {result.q_max * IN / KIP:.4f} vs 44.2 kip/in ({d:.3f}%)"


def test_eccentricity_and_critical_eccentricity_match(result) -> None:
    ok_e, de = within_pct(result.eccentricity / IN, 9.57, 0.1)
    ok_c, dc = within_pct(result.e_critical / IN, 5.75, 0.1)
    assert ok_e and ok_c, f"e {de:.3f}%  e_crit {dc:.3f}%"
    assert result.eccentricity > result.e_critical, "must be the large-moment case"


def test_peak_bearing_pressure_is_at_the_maximum(result) -> None:
    ok, d = within_pct(result.f_p / KSI, 2.21, 0.1)
    assert ok, f"f_p {result.f_p / KSI:.4f} vs 2.21 ksi ({d:.3f}%)"


def test_bearing_interface_thickness_matches_once_rounding_is_removed(result) -> None:
    """1.884 vs DG 1's 1.90 in., and the 0.84% is fully accounted for.

    DG 1 prints the rounded coefficient 1.5 for 2/sqrt(0.9) = 1.4907, and
    uses Fy = 36 ksi = 248.211 MPa where the module's A36 palette carries
    250 MPa.  Undo both and the numbers agree to four figures.  This test
    asserts the RECONSTRUCTION, not just a loose tolerance — a genuine
    formula error would not survive it.
    """
    reconstructed = 1.5 * 5.12 * math.sqrt(2.21 / 36.0)          # DG 1's own
    exact = reconstructed * (1.4907 / 1.5) * math.sqrt(248.211 / 250.0)
    ok_pub, d_pub = within_pct(reconstructed, 1.90, 0.5)
    ok_mine, d_mine = within_pct(result.t_required / IN, exact, 0.1)
    assert ok_pub, f"could not reproduce DG 1's own 1.90 in. ({d_pub:.2f}%)"
    assert ok_mine, (
        f"module {result.t_required / IN:.4f} in. vs reconstructed "
        f"{exact:.4f} in. ({d_mine:.3f}%)"
    )


def test_selected_plate_is_dg1s_2_inch(result) -> None:
    assert result.t_selected / IN == pytest.approx(1.969, abs=0.02)  # 50 mm


# ----------------------------------------------------------------------
# What does not match, and why
# ----------------------------------------------------------------------

def test_dg1_example_4_7_substitutes_the_wrong_f(dg1_47) -> None:
    """Example 4.7 uses f = 9.5 in. where it computed f = 8.5 in.

    The slip is visible inside a single inequality.  DG 1 prints, for the
    20x20 plate:

        (f + N/2)^2                 = (8.5 + 10)^2                 = 342
        2 Pu (e + f) / q_max        = (2)(376)(9.57 + 9.5) / 44.2  = 324

    f = 8.5 on the left, f = 9.5 on the right.  9.5 is N/2 from the FIRST
    iteration (the 19x19 trial).  This test proves the substitution by
    reproducing both of DG 1's printed numbers from the two f values.
    """
    Pu, Mu, N, B, d = 376.0, 3600.0, 20.0, 20.0, 12.7
    q = 2.21 * B
    e = Mu / Pu

    def solve(f: float) -> tuple[float, float, float, float]:
        half = f + N / 2.0
        term = 2 * Pu * (e + f) / q
        Y = half - math.sqrt(half ** 2 - term)
        return half ** 2, term, Y, q * Y - Pu

    half_sq_correct, _, Y_correct, T_correct = solve(8.5)
    _, term_stale, Y_stale, T_stale = solve(9.5)

    # DG 1's LEFT-hand side comes from f = 8.5
    assert half_sq_correct == pytest.approx(342, abs=0.5)
    # DG 1's RIGHT-hand side comes from f = 9.5
    assert term_stale == pytest.approx(324, abs=1.0)
    # and so do its Y and T
    assert Y_stale == pytest.approx(12.0, abs=0.05)
    assert T_stale == pytest.approx(156, abs=1.0)
    # while the example's own stated f gives what the module gives
    assert Y_correct == pytest.approx(12.606, abs=0.01)
    assert T_correct == pytest.approx(181.19, abs=0.05)


def test_module_reproduces_dg1_from_dg1s_own_stated_f(result) -> None:
    """With f = 8.5 in., the module lands on the hand calc to four figures.

    This is the load-bearing one.  It says the FORMULA is right; the
    published Y = 12.0 / T = 156 are not reproducible from the example's
    own stated geometry.
    """
    assert result.Y_bearing / IN == pytest.approx(12.606, abs=0.01)
    assert result.T_anchor / KIP == pytest.approx(181.19, abs=0.05)


def test_the_existence_check_still_passes_with_the_correct_f() -> None:
    """The mis-substitution does not change DG 1's 'a solution exists'."""
    Pu, N, B, e, f = 376.0, 20.0, 20.0, 3600.0 / 376.0, 8.5
    q = 2.21 * B
    assert 2 * Pu * (e + f) / q < (f + N / 2.0) ** 2


def test_the_anchor_rod_conclusion_would_change(dg1_47) -> None:
    """Recorded because it is the part that matters on a drawing.

    DG 1 sizes the rods on T = 156 kips: three rods per face, 52 kips
    each, against phiRn = 57.7 kips for a 1-1/2 in. Grade 36 rod.  At
    T = 181.2 kips the demand is 60.4 kips per rod, which exceeds 57.7.
    """
    assert 156.0 / 3 == pytest.approx(52.0, abs=0.1)
    assert 156.0 / 3 < 57.7
    assert 181.19 / 3 > 57.7


# ----------------------------------------------------------------------
# The one thing this example leaves open
# ----------------------------------------------------------------------

def test_tension_lever_arm_the_module_is_right_and_4_7_is_wrong() -> None:
    """RESOLVED 2026-08-31 against the printed Eq. 3.4.6.

        x = f - d/2 + tf/2                                    (3.4.6)

    and the prose: the cantilever span is "the distance from the rod
    centerline to the CENTER OF THE COLUMN FLANGE."

    Example 4.7 takes x = N/2 - d/2 - 1.5 = f - d/2, which lands on the
    OUTER FACE and is short by tf/2.  That is the SECOND error in that
    example.  The module conforms to the equation; the tf/2 term stays.

    This was left open for one exchange rather than settled by changing
    the code to match a worked example — the example had already been
    shown to mis-substitute f, and it turned out to be wrong here too.
    """
    f, d, tf, N = 8.5, 12.7, 0.900, 20.0
    x_eq_3_4_6 = f - d / 2.0 + tf / 2.0
    x_example_4_7 = N / 2.0 - d / 2.0 - 1.5

    assert x_eq_3_4_6 == pytest.approx(2.60, abs=0.01)
    assert x_example_4_7 == pytest.approx(2.15, abs=0.01)
    assert x_example_4_7 == pytest.approx(x_eq_3_4_6 - tf / 2.0, abs=1e-9), (
        "the example's arm must be exactly tf/2 short of Eq. 3.4.6"
    )


def test_module_uses_equation_3_4_6_verbatim() -> None:
    """The module's x_arm must BE Eq. 3.4.6, not merely close to it.

    Driven through the public API: two designs identical except for tf.
    The tension-interface thickness scales as sqrt(x), so the ratio of
    the two required thicknesses pins the arm exactly.
    """
    def design(tf_mm):
        g = bp.BasePlateGeometry(N=20 * IN, B=20 * IN, d=12.7 * IN,
                                 bf=12.2 * IN, f=8.5 * IN, tf=tf_mm)
        # narrow plate in the palette is irrelevant; read t_required
        return bp.design_base_plate(Pu=376 * KIP, Mu=3600 * KIPIN, geom=g,
                                    plate_grade="A36", fc_prime=4 * KSI,
                                    strict=True)

    f, d = 8.5, 12.7
    for tf_in in (0.900, 2.0, 3.0):
        r = design(tf_in * IN)
        T = r.T_anchor / KIP
        x_expected = f - d / 2.0 + tf_in / 2.0
        # Fy: the module's A36 palette carries 250 MPa
        Fy_ksi = 250.0 / KSI
        t_up = 2.108 * math.sqrt(T * x_expected / (20.0 * Fy_ksi))
        if t_up > 1.885:          # only meaningful once it governs
            ok, delta = within_pct(r.t_required / IN, t_up, 0.2)
            assert ok, (f"tf={tf_in}: t_req {r.t_required / IN:.4f} vs "
                        f"Eq. 3.4.6 prediction {t_up:.4f} ({delta:.3f}%)")


def test_omitting_tf_is_flagged_as_unconservative() -> None:
    """The warning must fire, and must be honest about the direction."""
    g = bp.BasePlateGeometry(N=20 * IN, B=20 * IN, d=12.7 * IN,
                             bf=12.2 * IN, f=8.5 * IN, tf=None)
    r = bp.design_base_plate(Pu=376 * KIP, Mu=3600 * KIPIN, geom=g,
                             plate_grade="A36", fc_prime=4 * KSI,
                             strict=True)
    hits = [w for w in r.warnings if "3.4.6" in w and "UNCONSERVATIVE" in w]
    assert hits, f"no lever-arm warning raised; got {r.warnings}"


def test_sect_3_4_transcription_agrees_with_the_first_reading() -> None:
    """Sect. 3.4 was read twice, a day apart. The readings must agree.

    The first reading closed the four base-plate flags; this is the
    independent re-transcription. Line-for-line agreement is what makes
    the transcription risk recorded in baseplate.py acceptable.
    """
    txt = load_fixture("dg1_2nd_ed_sect_3_4_text")
    eqs = txt["equations_verbatim"]
    assert eqs["3.4.6"] == "x = f - d/2 + tf/2"
    assert "1/2" in eqs["3.4.5a"] or "NO factor" in eqs["3.4.5a"]
    assert "N/2 - Pr/(2*q_max)" in eqs["3.3.7"]
    for key, rec in txt["module_conformance"].items():
        assert rec["match"].startswith("exact") or "EXACT" in rec["match"], (
            f"{key}: {rec['match']}"
        )
    assert txt["flags_this_can_close"] == []


def test_this_record_closes_no_flag(dg1_47) -> None:
    """It was not a blind check. It confirms; it does not certify."""
    assert dg1_47["blind"] is False
    assert dg1_47["flags_this_can_close"] == []
    assert "secondary.gusset" in dg1_47["flags_this_cannot_close"]
