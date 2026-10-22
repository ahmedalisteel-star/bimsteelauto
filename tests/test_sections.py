"""Invariant and behaviour tests for bimsteelauto.sections.

These are RELATIVE checks - they test the module against itself and against
hand algebra.  They cannot verify a formula; that is what
test_sections_verification.py is for.  Kept separate on purpose, because
during the connection work eight real defects passed straight through
invariant tests of exactly this kind.
"""

import math

import pytest

from bimsteelauto.sections import (
    VERIFIED,
    SectionError,
    TaperedMember,
    WeldedI,
    parse_mark,
)


@pytest.fixture
def sym():
    return WeldedI.symmetric(d=600, tw=8, bf=200, tf=10)


@pytest.fixture
def mono():
    return WeldedI(d=800, tw=8, bf_top=250, tf_top=12, bf_bot=200, tf_bot=10)


@pytest.fixture
def taper(sym):
    end = WeldedI.symmetric(d=1200, tw=8, bf=200, tf=10)
    return TaperedMember(start=sym, end=end, length=12_000.0)


# ---------------------------------------------------------------------------
# geometry guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kw",
    [
        {"d": 0},
        {"tw": 0},
        {"bf_top": -200},
        {"tf_top": 0},
        {"tf_bot": -10},
    ],
)
def test_non_positive_dimensions_rejected(kw):
    base = {"d": 600, "tw": 8, "bf_top": 200, "tf_top": 10, "bf_bot": 200, "tf_bot": 10}
    base.update(kw)
    with pytest.raises(SectionError):
        WeldedI(**base)


def test_flanges_thicker_than_depth_rejected():
    with pytest.raises(SectionError, match="not deep enough"):
        WeldedI(d=25, tw=8, bf_top=200, tf_top=15, bf_bot=200, tf_bot=15)


def test_h_and_ho(sym):
    assert sym.h == pytest.approx(580.0)
    assert sym.ho == pytest.approx(590.0)


def test_ho_for_unequal_flanges(mono):
    assert mono.h == pytest.approx(800 - 12 - 10)
    assert mono.ho == pytest.approx(800 - 0.5 * (12 + 10))


# ---------------------------------------------------------------------------
# areas and centroid, by hand
# ---------------------------------------------------------------------------


def test_area_by_hand(sym):
    assert sym.A == pytest.approx(2 * 200 * 10 + 580 * 8)


def test_symmetric_centroid_is_mid_depth(sym):
    assert sym.y_bar == pytest.approx(sym.d / 2)


def test_mono_centroid_shifts_toward_heavy_flange(mono):
    """Top flange is bigger, so the centroid must sit above mid-depth."""
    assert mono.y_bar > mono.d / 2


def test_weight_per_m(sym):
    assert sym.weight_per_m == pytest.approx(sym.A * 1e-6 * 7850.0)


# ---------------------------------------------------------------------------
# section property relations
# ---------------------------------------------------------------------------


def test_symmetric_moduli_are_equal(sym):
    assert sym.Sx_top == pytest.approx(sym.Sx_bot)
    assert sym.Sx == pytest.approx(sym.Sx_top)


def test_mono_moduli_differ(mono):
    assert mono.Sx_top != pytest.approx(mono.Sx_bot)
    assert mono.Sx == min(mono.Sx_top, mono.Sx_bot)


def test_plastic_exceeds_elastic(sym, mono):
    """Zx > Sx always, and the shape factor for an I is roughly 1.1-1.2."""
    for s in (sym, mono):
        assert s.Zx > s.Sx
        assert 1.05 < s.Zx / s.Sx < 1.30


def test_radii_of_gyration(sym):
    assert sym.rx == pytest.approx(math.sqrt(sym.Ix / sym.A))
    assert sym.ry == pytest.approx(math.sqrt(sym.Iy / sym.A))
    assert sym.rx > sym.ry


def test_symmetric_pna_equals_centroid(sym):
    """For a doubly-symmetric section the two axes coincide."""
    assert sym.y_pna == pytest.approx(sym.y_bar)


def test_mono_pna_differs_from_centroid(mono):
    assert abs(mono.y_pna - mono.y_bar) > 1.0


def test_pna_splits_area_in_half(mono):
    """Whatever branch it lands in, equal areas is the defining property."""
    y = mono.y_pna
    below = 0.0
    for b, y0, y1 in (
        (mono.bf_bot, 0.0, mono.tf_bot),
        (mono.tw, mono.tf_bot, mono.tf_bot + mono.h),
        (mono.bf_top, mono.d - mono.tf_top, mono.d),
    ):
        lo, hi = y0, min(y1, y)
        if hi > lo:
            below += b * (hi - lo)
    assert below == pytest.approx(mono.A / 2.0)


def test_pna_in_flange_branch():
    """A section with a very heavy bottom flange puts the PNA inside it."""
    s = WeldedI(d=400, tw=6, bf_top=100, tf_top=6, bf_bot=400, tf_bot=40)
    assert s.y_pna < s.tf_bot
    assert s.Zx > s.Sx


def test_cw_reduces_to_the_aisc_symmetric_form(sym):
    """For equal flanges the general expression must approach Iy * ho^2 / 4.

    Not exactly equal, and the difference is real rather than sloppy.  The
    general form ``Iyc*Iyt/(Iyc+Iyt) * ho^2`` uses the FLANGES only, which is
    what AISC intends - the web is neglected throughout the warping
    treatment.  ``Iy`` as this module reports it is the true gross value and
    does include the web, so ``Iy*ho^2/4`` is larger by exactly the web's
    weak-axis share.

    That share is tiny (a 580x8 web is 0.19% of Iy on this section) and the
    mesh integration in the verification suite agreed with the flange-only
    form to 0.163%, so the flange-only form is the one kept.
    """
    aisc_symmetric = sym.Iy * sym.ho**2 / 4.0
    assert sym.Cw == pytest.approx(aisc_symmetric, rel=0.005)
    assert sym.Cw < aisc_symmetric  # the web term is what separates them

    web_share = (sym.h * sym.tw**3 / 12.0) / sym.Iy
    assert (aisc_symmetric - sym.Cw) / aisc_symmetric == pytest.approx(
        web_share, rel=1e-9
    )


def test_cw_general_form_differs_for_mono(mono):
    assert mono.Cw != pytest.approx(mono.Iy * mono.ho**2 / 4.0)


def test_slenderness_ratios(sym):
    assert sym.lambda_flange_top == pytest.approx(100.0 / 10.0)
    assert sym.lambda_web == pytest.approx(580.0 / 8.0)


def test_kc_is_bounded():
    thin = WeldedI.symmetric(d=2000, tw=5, bf=200, tf=12)   # h/tw = 395
    thick = WeldedI.symmetric(d=200, tw=20, bf=200, tf=12)  # h/tw = 8.8
    assert thin.kc == pytest.approx(0.35)
    assert thick.kc == pytest.approx(0.76)


def test_deeper_section_is_stiffer(sym):
    deeper = WeldedI.symmetric(d=900, tw=8, bf=200, tf=10)
    assert deeper.Ix > sym.Ix
    assert deeper.Iy > sym.Iy  # deeper web adds a little weak-axis too
    assert deeper.rx > sym.rx


# ---------------------------------------------------------------------------
# marks
# ---------------------------------------------------------------------------


def test_symmetric_mark(sym):
    assert sym.mark == "BU-600x200x8/10"


def test_mono_mark(mono):
    assert mono.mark == "BU-800x250/200x8/12/10"


def test_tapered_mark(taper):
    assert taper.mark == "BU-600/1200x200x8/10"


def test_mark_round_trip_symmetric(sym):
    assert parse_mark(sym.mark) == sym


def test_mark_round_trip_mono(mono):
    assert parse_mark(mono.mark) == mono


def test_mark_round_trip_tapered(taper):
    back = parse_mark(taper.mark, length=taper.length)
    assert back == taper


def test_tapered_mark_without_length_is_rejected(taper):
    with pytest.raises(SectionError, match="length is required"):
        parse_mark(taper.mark)


def test_bad_mark_rejected():
    with pytest.raises(SectionError, match="not a canonical"):
        parse_mark("W24X76")


def test_decimal_thickness_survives_round_trip():
    s = WeldedI.symmetric(d=450, tw=4.5, bf=150, tf=9.5)
    assert s.mark == "BU-450x150x4.5/9.5"
    assert parse_mark(s.mark) == s


# ---------------------------------------------------------------------------
# taper
# ---------------------------------------------------------------------------


def test_taper_rejects_changing_flange(sym):
    end = WeldedI.symmetric(d=1200, tw=8, bf=250, tf=10)
    with pytest.raises(SectionError, match="only in depth"):
        TaperedMember(start=sym, end=end, length=12_000.0)


def test_taper_rejects_changing_web_thickness(sym):
    end = WeldedI.symmetric(d=1200, tw=10, bf=200, tf=10)
    with pytest.raises(SectionError, match="only in depth"):
        TaperedMember(start=sym, end=end, length=12_000.0)


def test_taper_rejects_zero_length(sym):
    with pytest.raises(SectionError):
        TaperedMember(start=sym, end=sym, length=0.0)


def test_depth_is_linear(taper):
    assert taper.depth_at(0.0) == pytest.approx(600.0)
    assert taper.depth_at(taper.length) == pytest.approx(1200.0)
    assert taper.depth_at(taper.length / 2) == pytest.approx(900.0)
    assert taper.depth_at(taper.length / 4) == pytest.approx(750.0)


def test_depth_outside_length_is_rejected(taper):
    with pytest.raises(SectionError):
        taper.depth_at(-1.0)
    with pytest.raises(SectionError):
        taper.depth_at(taper.length + 1.0)


def test_at_keeps_plate_sizes(taper):
    mid = taper.at(taper.length / 2)
    assert mid.tw == taper.start.tw
    assert mid.bf_top == taper.start.bf_top
    assert mid.tf_bot == taper.start.tf_bot


def test_taper_ratio(taper):
    assert taper.taper_ratio == pytest.approx(2.0)


def test_prismatic_detection(sym):
    m = TaperedMember(start=sym, end=sym, length=6_000.0)
    assert m.is_prismatic
    assert m.taper_ratio == pytest.approx(1.0)
    assert m.suggested_segments() == 1
    assert len(m.discretise(m.suggested_segments())) == 1


def test_discretise_covers_the_length(taper):
    segs = taper.discretise(7)
    assert len(segs) == 7
    assert segs[0].x_start == 0.0
    assert segs[-1].x_end == pytest.approx(taper.length)
    assert sum(s.length for s in segs) == pytest.approx(taper.length)


def test_discretise_uses_mid_station(taper):
    segs = taper.discretise(4)
    first = segs[0]
    assert first.section.d == pytest.approx(taper.depth_at(first.x_mid))


def test_discretise_depths_increase(taper):
    segs = taper.discretise(10)
    depths = [s.section.d for s in segs]
    assert depths == sorted(depths)


def test_discretise_rejects_zero(taper):
    with pytest.raises(SectionError):
        taper.discretise(0)


def test_suggested_segments_tracks_taper(sym):
    gentle = TaperedMember(sym, WeldedI.symmetric(d=660, tw=8, bf=200, tf=10), 6_000.0)
    steep = TaperedMember(sym, WeldedI.symmetric(d=1800, tw=8, bf=200, tf=10), 6_000.0)
    assert gentle.suggested_segments() < steep.suggested_segments()
    assert gentle.suggested_segments() >= 1


def test_stations_includes_both_ends(taper):
    st = list(taper.stations(4))
    assert len(st) == 5
    assert st[0][0] == 0.0
    assert st[-1][0] == pytest.approx(taper.length)
    assert st[-1][1].d == pytest.approx(taper.end.d)


# ---------------------------------------------------------------------------
# verification gate
# ---------------------------------------------------------------------------


def test_all_section_relations_are_verified():
    """Every flag closed 2026-09-01 against sectionproperties 3.10.2.

    If a future edit adds a relation, this fails until it is checked against
    an independent source - which is the point.
    """
    unset = [k for k, v in VERIFIED.items() if not v]
    assert unset == [], f"unverified section relations: {unset}"
