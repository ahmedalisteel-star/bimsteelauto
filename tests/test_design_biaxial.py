"""Biaxial flexure, weak-axis shear and torsion - the out-of-plane checks.

Until the 3D building model arrived nothing in this package produced an
``My``, a ``Vz`` or a ``T``, so ``design/members.py`` graded on P, Vy and Mz
and the other three components had nowhere to go.  These are the checks
that give them somewhere.

Every expected value here is worked LONGHAND from the clause, in SI, in the
test - not read back from the library and not re-run through the code under
test.  The formulas are short enough that this is possible, which is the
whole reason they can be verified this way::

    F6-1    Mn = min(Fy*Zy, 1.6*Fy*Sy)                    minor-axis yielding
    G6-1    Vn = 0.6*Fy*bf*tf*Cv2, PER FLANGE             weak-axis shear
    H3-8    Fn = 0.6*Fy                                   torsional shear yield
    H1-1a   Pr/Pc     + 8/9*(Mrx/Mcx + Mry/Mcy) <= 1.0
    H1-1b   Pr/(2*Pc) +     (Mrx/Mcx + Mry/Mcy) <= 1.0

The three failures worth guarding against, and each has a control:

1. **The minor-axis term is quietly dropped from H1.** Every uniaxial test
   in this suite passes either way. Killed by a member that PASSES
   uniaxially and FAILS biaxially.
2. **F6 reuses the major-axis compression flange.** That flange is chosen
   by the SIGN OF Mz; minor-axis bending compresses part of both, so the
   governing element is the more slender one and the sign must not matter.
   Killed by a singly-symmetric section checked under +Mz and -Mz.
3. **Weak-axis shear doubles one flange instead of summing two.** Identical
   on a symmetric section, wrong on every singly-symmetric one - and a PEB
   rafter is singly symmetric.
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto.design.members import VERIFIED, check_member
from bimsteelauto.design.members import (
    BracingSpec,
    MemberDesignInput,
    station_utilisations,
)
from bimsteelauto.sections import TaperedMember, WeldedI
from bimsteelauto.spec import (
    DesignMethod,
    Materials,
    MemberForces,
    MemberType,
)

FY = 345.0
E = 200_000.0

MATS = Materials(
    Fy_builtup=FY, Fu_builtup=450.0,
    Fy_cold_formed=FY, Fu_cold_formed=450.0,
    bolt_grade="A325", anchor_grade="F1554-36", fc_concrete=30.0,
)

#: A real PEB rafter end.
SYM = WeldedI.symmetric(d=850.0, tw=7.0, bf=250.0, tf=16.0)

#: Singly symmetric: a WIDE, THICK top flange and a narrow, thin bottom one.
#: The bottom is the more slender (b/2t = 12.5 against 6.25), so it governs
#: F6 - and the major-axis check picks its compression flange by the sign of
#: Mz, which is exactly the confusion these tests exist to catch.
SINGLY = WeldedI(
    d=850.0, tw=7.0,
    bf_top=300.0, tf_top=24.0,
    bf_bot=250.0, tf_bot=10.0,
)

LENGTH = 7000.0


def _design(section: WeldedI, **forces) -> list:
    """Run one station and return its checks."""
    tm = TaperedMember(start=section, end=section, length=LENGTH)
    st = MemberForces(station=LENGTH / 2.0, **forces)
    inp = MemberDesignInput(
        mark="X1", member_type=MemberType.COLUMN, taper=tm, materials=MATS,
        forces={"C1": (st,)},
        bracing=BracingSpec(
            top_flange=(0.0, LENGTH), bottom_flange=(0.0, LENGTH)
        ),
        method=DesignMethod.LRFD,
    )
    return list(check_member(inp).checks)


def _named(checks, prefix):
    """The check whose name STARTS with ``prefix``.

    Prefix, not substring: "Flexure (lateral-torsional buckling)" contains
    the word "torsion", and a substring match quietly returned the
    major-axis bending check when the test asked for the torsion one - and
    then asserted things about it that happened to be true.
    """
    hits = [c for c in checks if c.name.lower().startswith(prefix.lower())]
    return hits[0] if hits else None


# ----------------------------------------------------------------------
# The 2D path is untouched
# ----------------------------------------------------------------------

def test_a_plane_frame_station_gets_no_out_of_plane_checks() -> None:
    """A plane frame returns exactly zero for Vz, T and My - they are
    RESTRAINED OUT of that model, not computed small.

    So no minor-axis, weak-axis or torsion check is emitted, and the 2D
    calculation sheet reads exactly as it did. That is not working
    agreement (d) being dodged: (d) is about a check that could not be
    EVALUATED reading as a pass, and here there is nothing to evaluate.
    """
    checks = _design(SYM, P=200.0, Vy=150.0, Mz=400.0)
    assert _named(checks, "Minor-axis flexure") is None
    assert _named(checks, "Weak-axis shear") is None
    assert _named(checks, "Torsion, St Venant") is None
    assert _named(checks, "Combined axial + flexure") is not None
    assert _named(checks, "Combined axial + biaxial") is None


def test_the_out_of_plane_checks_appear_when_the_actions_do() -> None:
    checks = _design(SYM, P=200.0, Vy=150.0, Mz=400.0,
                     My=60.0, Vz=40.0, T=1.0)
    for want in ("Minor-axis flexure", "Weak-axis shear",
                 "Torsion, St Venant", "Combined axial + biaxial"):
        assert _named(checks, want) is not None, want


# ----------------------------------------------------------------------
# F6 - minor-axis flexure
# ----------------------------------------------------------------------

def test_minor_axis_capacity_matches_F6_1_longhand() -> None:
    """Eq. F6-1: ``Mn = Fy*Zy <= 1.6*Fy*Sy``.

    Worked in SI from the section's own Zy and Sy, then reduced by
    ``phi_b = 0.90``. Nothing is read back from the library.
    """
    checks = _design(SYM, P=200.0, Vy=10.0, Mz=100.0, My=60.0)
    c = _named(checks, "Minor-axis flexure")

    Mp_y = FY * SYM.Zy / 1.0e6                 # kN.m
    cap_16 = 1.6 * FY * SYM.Sy / 1.0e6
    Mn = min(Mp_y, cap_16)
    assert c.capacity == pytest.approx(0.90 * Mn, rel=1e-3)
    assert c.demand == pytest.approx(60.0)
    assert "F6" in c.reference


def test_for_a_real_welded_I_the_1_6_cap_does_not_govern() -> None:
    """Worth stating rather than assuming.

    Weak-axis bending of an I is dominated by its flanges, and a rectangle
    has Z/S = 1.5 - so Zy/Sy lands near 1.5 and the ``1.6*Fy*Sy`` cap never
    bites on a member anyone would build. A test suite made only of real
    sections therefore never reaches that branch, which is why the next
    test exists.
    """
    assert SYM.Zy / SYM.Sy == pytest.approx(1.529, abs=0.01)
    assert SYM.Zy / SYM.Sy < 1.6
    assert SINGLY.Zy / SINGLY.Sy < 1.6


def test_the_1_6_cap_is_applied_when_it_does_govern() -> None:
    """NEGATIVE CONTROL for Eq. F6-1's limit.

    A deliberately unrealistic section - a 40 mm web on a 300 mm depth -
    to push Zy/Sy above 1.6 and reach the branch. Nobody would fabricate
    it; the point is that the cap is APPLIED rather than being an
    unexercised line. Without this, dropping ``min(...)`` from F6 would
    pass every other test here.
    """
    fat = WeldedI.symmetric(d=300.0, tw=40.0, bf=200.0, tf=20.0)
    assert fat.Zy / fat.Sy > 1.6, "this control no longer reaches the branch"
    # The flange must stay COMPACT, or flange local buckling governs first
    # and the cap is still never reached. A 10 mm flange was tried and did
    # exactly that - the test passed nothing.
    assert fat.lambda_flange_top < 9.0

    checks = _design(fat, P=50.0, Vy=10.0, Mz=20.0, My=15.0)
    c = _named(checks, "Minor-axis flexure")
    cap_16 = 1.6 * FY * fat.Sy / 1.0e6
    Mp_y = FY * fat.Zy / 1.0e6
    assert cap_16 < Mp_y
    assert c.capacity == pytest.approx(0.90 * cap_16, rel=1e-3)


def test_F6_takes_the_more_slender_flange_not_the_major_axis_one() -> None:
    """CONTROL 2. The sign of Mz must not move the minor-axis capacity.

    The major-axis check picks its compression flange from the sign of Mz -
    top for sagging, bottom for hogging. Minor-axis bending compresses part
    of BOTH flanges, so F6 must classify the more slender one whichever way
    Mz acts. Reusing the major-axis flange would make a rafter's weak-axis
    capacity depend on the direction of the wind.
    """
    sag = _design(SINGLY, P=100.0, Vy=50.0, Mz=+300.0, My=40.0)
    hog = _design(SINGLY, P=100.0, Vy=50.0, Mz=-300.0, My=40.0)

    a, b = _named(sag, "Minor-axis flexure"), _named(hog, "Minor-axis flexure")
    assert a.capacity == pytest.approx(b.capacity, rel=1e-9)
    # And it really is the SLENDER flange that was named.
    assert SINGLY.lambda_flange_bot > SINGLY.lambda_flange_top
    assert "bottom flange governs" in a.note

    # The major-axis check, by contrast, MUST differ between the two.
    assert (_named(sag, "Flexure (").capacity
            != _named(hog, "Flexure (").capacity)


# ----------------------------------------------------------------------
# G6 - weak-axis shear
# ----------------------------------------------------------------------

def test_weak_axis_shear_matches_G6_1_longhand() -> None:
    """Eq. G6-1 ``Vn = 0.6*Fy*bf*tf*Cv2`` per flange, both flanges summed.

    ``Cv2 = 1.0`` here and it is checked rather than assumed: Sect. G2.2
    gives Cv2 = 1.0 while ``b/2t <= 1.10*sqrt(kv*E/Fy)`` at ``kv = 1.2``,
    which is 29.0 against this section's 7.81.
    """
    checks = _design(SYM, P=100.0, Vy=50.0, Mz=100.0, Vz=40.0)
    c = _named(checks, "Weak-axis shear")

    lim = 1.10 * math.sqrt(1.2 * E / FY)
    assert SYM.lambda_flange_top <= lim, "Cv2 is not 1.0 for this section"

    Vn = 2.0 * 0.6 * FY * SYM.bf_top * SYM.tf_top / 1000.0     # kN
    assert Vn == pytest.approx(1656.0, rel=1e-6)
    assert c.capacity == pytest.approx(0.90 * Vn, rel=1e-3)
    assert c.demand == pytest.approx(40.0)


def test_weak_axis_shear_sums_two_flanges_and_does_not_double_one() -> None:
    """CONTROL 3, and a PEB rafter is exactly this shape.

    Eq. G6-1 is stated per shear-resisting element. On the singly-symmetric
    section the two flanges differ by more than a factor of three in area,
    so ``2 x top`` and ``top + bottom`` are far apart - the error is
    invisible on a symmetric section and large on a real rafter.
    """
    checks = _design(SINGLY, P=100.0, Vy=50.0, Mz=100.0, Vz=40.0)
    c = _named(checks, "Weak-axis shear")

    k = 0.6 * FY / 1000.0
    top = k * SINGLY.bf_top * SINGLY.tf_top
    bot = k * SINGLY.bf_bot * SINGLY.tf_bot
    assert c.capacity == pytest.approx(0.90 * (top + bot), rel=1e-3)
    # The wrong answer, kept explicit so the test says what it is guarding.
    assert c.capacity != pytest.approx(0.90 * 2.0 * top, rel=1e-3)


def test_the_G2_phi_exception_does_not_reach_G6() -> None:
    """phi_v = 0.90, not 1.00.

    Sect. G2.1(a)'s ``phi_v = 1.00`` is a WEB provision for rolled I-shapes
    with ``h/tw <= 2.24 sqrt(E/Fy)``. Carrying it into G6 would overstate
    every weak-axis shear by 11%.
    """
    checks = _design(SYM, P=100.0, Vy=50.0, Mz=100.0, Vz=40.0)
    c = _named(checks, "Weak-axis shear")
    Vn = 2.0 * 0.6 * FY * SYM.bf_top * SYM.tf_top / 1000.0
    assert c.capacity == pytest.approx(0.90 * Vn, rel=1e-3)
    assert c.capacity != pytest.approx(1.00 * Vn, rel=1e-3)
    assert "0.90" in c.note


# ----------------------------------------------------------------------
# H3.3 - torsion
# ----------------------------------------------------------------------

def test_torsional_shear_stress_matches_longhand() -> None:
    """Uniform torsion: ``tau = T*t/J`` on the thickest element, against
    Eq. H3-8's ``0.6*Fy`` at ``phi_T = 0.90``."""
    T = 1.5                                    # kN.m
    checks = _design(SYM, P=100.0, Vy=50.0, Mz=100.0, T=T)
    c = _named(checks, "Torsion, St Venant")

    t_max = max(SYM.tf_top, SYM.tf_bot, SYM.tw)
    tau = T * 1.0e6 * t_max / SYM.J            # N.mm * mm / mm^4 = MPa
    assert c.demand == pytest.approx(tau, rel=1e-6)
    assert c.capacity == pytest.approx(0.90 * 0.6 * FY, rel=1e-3)
    assert "H3" in c.reference


def test_the_torsion_check_says_what_it_leaves_out() -> None:
    """The gap, on the check itself.

    On an open section the WARPING stresses are usually larger than the
    uniform-torsion ones, and they are not computed - they need the DG 9
    solution with the member's end conditions. A reader of one check must
    be able to see that from the check, not only from a coverage list at
    the back of the sheet.
    """
    checks = _design(SYM, P=100.0, Vy=50.0, Mz=100.0, T=1.5)
    c = _named(checks, "Torsion, St Venant")
    assert "UNIFORM TORSION ONLY" in c.note
    assert "warping" in c.note.lower()
    assert "St Venant" in c.name

    from bimsteelauto.design.members import COVERAGE_GAPS

    assert any("ST VENANT" in g and "LOWER BOUND" in g for g in COVERAGE_GAPS)


def test_an_open_section_has_almost_no_torsional_capacity() -> None:
    """Sanity, and it is the reason torsion matters at all here.

    ``J`` for a 850 x 250 welded I is under a millionth of ``Ix``. A
    torsion a beam would shrug off in bending governs it completely, and a
    check that came out comfortable for a large T would be wrong.
    """
    assert SYM.J / SYM.Ix < 1.0e-3
    checks = _design(SYM, P=100.0, Vy=50.0, Mz=100.0, T=12.0)
    assert _named(checks, "Torsion, St Venant").ratio > 1.0


# ----------------------------------------------------------------------
# H1 - the biaxial interaction
# ----------------------------------------------------------------------

def _h1_longhand(Pr, Pc, Mrx, Mcx, Mry, Mcy):
    """Eqs. H1-1a / H1-1b, written out."""
    r = Pr / Pc
    m = Mrx / Mcx + (Mry / Mcy if Mcy else 0.0)
    return r + (8.0 / 9.0) * m if r >= 0.2 else r / 2.0 + m


def test_the_biaxial_interaction_matches_H1_longhand() -> None:
    checks = _design(SYM, P=200.0, Vy=150.0, Mz=400.0, My=60.0)
    h = _named(checks, "Combined axial + biaxial")
    Pc = _named(checks, "Compression").capacity
    Mcx = _named(checks, "Flexure (").capacity
    Mcy = _named(checks, "Minor-axis flexure").capacity

    expected = _h1_longhand(200.0, Pc, 400.0, Mcx, 60.0, Mcy)
    assert h.ratio == pytest.approx(expected, rel=1e-4)
    assert 200.0 / Pc < 0.2, "this case should be on the H1-1b branch"


def test_the_interaction_is_checked_on_both_branches() -> None:
    """Pr/Pc = 0.2 is a real branch and both sides must be exercised.

    A test that only ever lands on H1-1b would pass with H1-1a wrong.
    """
    low = _design(SYM, P=100.0, Vy=50.0, Mz=300.0, My=40.0)
    high = _design(SYM, P=1000.0, Vy=50.0, Mz=300.0, My=40.0)

    for checks, want_branch in ((low, "b"), (high, "a")):
        h = _named(checks, "Combined axial + biaxial")
        Pc = _named(checks, "Compression").capacity
        Mcx = _named(checks, "Flexure (").capacity
        Mcy = _named(checks, "Minor-axis flexure").capacity
        Pr = 100.0 if want_branch == "b" else 1000.0
        assert (Pr / Pc >= 0.2) == (want_branch == "a"), (
            f"this case is not on branch H1-1{want_branch}"
        )
        assert h.ratio == pytest.approx(
            _h1_longhand(Pr, Pc, 300.0, Mcx, 40.0, Mcy), rel=1e-4
        )


def test_a_member_that_passes_uniaxially_can_fail_biaxially() -> None:
    """CONTROL 1, and the load-bearing test in this file.

    An implementation that computed a minor-axis capacity, printed a
    minor-axis check, and then quietly left ``Mry`` out of Eq. H1-1 would
    pass every other test here. This one cannot be satisfied that way: the
    SAME station passes at 0.67 without My and fails at 1.05 with it, and
    the difference is exactly the Mry/Mcy term.
    """
    without = _design(SYM, P=200.0, Vy=150.0, Mz=400.0)
    with_my = _design(SYM, P=200.0, Vy=150.0, Mz=400.0, My=60.0)

    a = _named(without, "Combined axial + flexure").ratio
    b = _named(with_my, "Combined axial + biaxial").ratio
    assert a < 1.0 < b, f"uniaxial {a:.3f}, biaxial {b:.3f}"

    Mcy = _named(with_my, "Minor-axis flexure").capacity
    # H1-1b, so the minor-axis term enters with a factor of one.
    assert b - a == pytest.approx(60.0 / Mcy, rel=1e-3)


def test_the_biaxial_note_reports_both_terms(_=None) -> None:
    checks = _design(SYM, P=200.0, Vy=150.0, Mz=400.0, My=60.0)
    note = _named(checks, "Combined axial + biaxial").note
    assert "Mrx/Mcx" in note and "Mry/Mcy" in note


# ----------------------------------------------------------------------
# It reaches the utilisation
# ----------------------------------------------------------------------

def test_the_new_checks_reach_the_governing_utilisation() -> None:
    """The point of the exercise: My and T stop being a picture.

    A station with a torsion an open section cannot take must GOVERN, not
    sit in a list beside a comfortable bending ratio.
    """
    checks = _design(SYM, P=100.0, Vy=50.0, Mz=100.0, T=12.0)
    worst = max(checks, key=lambda c: c.ratio)
    assert "torsion" in worst.name.lower()
    assert worst.ratio > 1.0


def test_station_utilisations_sees_them_too() -> None:
    """``check_member`` and ``station_utilisations`` both call
    ``_check_station``, so they cannot disagree - pinned here because the
    contour reads the second one."""
    tm = TaperedMember(start=SYM, end=SYM, length=LENGTH)
    sts = tuple(
        MemberForces(P=100.0, Vy=50.0, Mz=100.0, My=40.0, T=6.0, station=x)
        for x in (0.0, LENGTH / 2.0, LENGTH)
    )
    inp = MemberDesignInput(
        mark="X1", member_type=MemberType.COLUMN, taper=tm, materials=MATS,
        forces={"C1": sts},
        bracing=BracingSpec(
            top_flange=(0.0, LENGTH), bottom_flange=(0.0, LENGTH)
        ),
        method=DesignMethod.LRFD,
    )
    ratios = station_utilisations(inp, "C1")
    assert len(ratios) == 3
    assert max(ratios) == pytest.approx(
        max(c.ratio for c in check_member(inp).checks), rel=1e-9
    )
    assert max(ratios) > 0.5


# ----------------------------------------------------------------------
# The gate
# ----------------------------------------------------------------------

def test_the_new_flags_are_closed() -> None:
    for flag in ("flexure_F6_minor", "shear_G6_weak", "torsion_H3_stvenant",
                 "interaction_H1_biaxial"):
        assert VERIFIED[flag] is True, flag
