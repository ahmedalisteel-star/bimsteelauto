"""Shop geometry - the layout arithmetic, checked longhand.

This module produces no engineering, so "verified" cannot mean "a second
program agrees on a capacity".  What it CAN get wrong is arithmetic and
closure, and both are checked here the only way that is worth anything:
by deriving the expected numbers from the flange faces BY HAND, in the
test, and comparing.  Re-running ``end_plate_layout`` and asserting it
equals itself would pass on every one of the bugs below.

The four things that would actually go wrong:

1. **The 1/cos is dropped**, or applied to the depth but not the flanges.
   Every bolt row then sits a few millimetres off the flange it exists to
   pick up.  Caught by deriving both the normal-cut and the inclined-cut
   layouts longhand and requiring that the ONLY difference between them is
   the factor.

2. **A row is drawn hanging over the plate edge.**  Caught by a NEGATIVE
   CONTROL: an edge distance small enough that the outer row must be
   dropped.  A test built only on DG 4 proportions never fires that branch,
   because those proportions always keep the row on - so it would pass
   whether or not the check existed.

3. **The gauge drifts off the web centreline.**

4. **The anchors are laid out on a plate they do not fit.**
"""

from __future__ import annotations

import json
import math

import pytest

from bimsteelauto.detailing import (
    COVERAGE_GAPS,
    VERIFIED,
    BasePlateLayout,
    DetailingError,
    EndPlateLayout,
    base_plate_layout,
    bolt_gauge,
    cleat_layout,
    end_plate_layout,
    gusset_layout,
)
from bimsteelauto.sections import WeldedI

#: A real PEB rafter end: 700 deep, 200 flanges, 12 mm plate.
RAFTER = WeldedI(d=700.0, tw=6.0, bf_top=200.0, tf_top=12.0,
                 bf_bot=200.0, tf_bot=12.0)

#: 1:12 roof.  cos = 12 / sqrt(145) - written this way on purpose, so the
#: expected values below are algebra rather than a second call into the code
#: under test.
SLOPE = 1.0 / 12.0
COS = 12.0 / math.sqrt(145.0)

DG4 = dict(thickness=20.0, bolt_diameter=24.0, width=250.0, gauge_g=100.0,
           e_out=40.0, e_in=40.0, de=36.0, rows_in=1)


# ----------------------------------------------------------------------
# Bolt set-out
# ----------------------------------------------------------------------

def test_a_single_bolt_sits_on_the_centreline() -> None:
    assert bolt_gauge(1, 100.0) == (0.0,)


def test_the_gauge_is_symmetric_and_spans_n_minus_one_pitches() -> None:
    """Written out by hand, not generated."""
    assert bolt_gauge(2, 100.0) == (-50.0, 50.0)
    assert bolt_gauge(4, 100.0) == (-150.0, -50.0, 50.0, 150.0)
    g = bolt_gauge(3, 80.0)
    assert g == (-80.0, 0.0, 80.0)
    assert g[-1] - g[0] == pytest.approx(2 * 80.0)
    assert sum(g) == pytest.approx(0.0)


def test_a_row_that_does_not_fit_the_plate_raises_rather_than_shuffling() -> None:
    """Moving the bolts to make them fit is how a bolt ends up off the steel."""
    with pytest.raises(DetailingError, match="does not fit"):
        bolt_gauge(4, 100.0, width=280.0)


def test_a_multi_bolt_row_needs_a_pitch() -> None:
    with pytest.raises(DetailingError):
        bolt_gauge(2, 0.0)


# ----------------------------------------------------------------------
# End plate - the row positions, derived by hand
# ----------------------------------------------------------------------

def test_row_positions_on_a_normal_cut_match_longhand() -> None:
    """A splice-type plate, cut normal to the member: no 1/cos anywhere.

    By hand, offsets DOWN from the top flange outer face:

        outer top    = -pfo                    = -40
        inner top    = tf + pfi                = 12 + 40   =  52
        inner bottom = d - tf - pfi            = 700-12-40 = 648
        outer bottom = d + pfo                 = 700 + 40  = 740
    """
    ep = end_plate_layout(RAFTER, cos_cut=1.0, **DG4)
    assert ep.row_offsets == (-40.0, 52.0, 648.0, 740.0)
    assert ep.depth_on_cut == pytest.approx(700.0)
    assert ep.height == pytest.approx(76.0 + 700.0 + 76.0)
    assert not ep.dropped


def test_row_positions_on_an_inclined_cut_match_longhand() -> None:
    """A plate cut VERTICALLY across a 1:12 rafter.

    Everything the plate sees of the section is stretched by 1/cos, and the
    expected values are written as algebra on sqrt(145) so this is a
    derivation and not a re-run:

        tf / cos = 12 * sqrt(145) / 12 = sqrt(145)
        d  / cos = 700 * sqrt(145) / 12
    """
    ep = end_plate_layout(RAFTER, cos_cut=COS, **DG4)

    tf_v = math.sqrt(145.0)
    dv = 700.0 * math.sqrt(145.0) / 12.0
    expected = (
        -40.0,
        round(tf_v + 40.0, 3),
        round(dv - tf_v - 40.0, 3),
        round(dv + 40.0, 3),
    )
    assert ep.row_offsets == expected
    assert ep.depth_on_cut == pytest.approx(dv)
    assert ep.tf_top_on_cut == pytest.approx(tf_v)
    assert ep.tf_bot_on_cut == pytest.approx(tf_v)


def test_the_incline_changes_the_depth_and_the_flanges_and_nothing_else() -> None:
    """The failure this pins is a 1/cos applied to the depth alone.

    That is the plausible half-fix - the plate comes out the right height
    and every inner row is still wrong, because the inner rows are measured
    from the FLANGE FACES. So the two layouts are compared term by term.
    """
    flat = end_plate_layout(RAFTER, cos_cut=1.0, **DG4)
    slope = end_plate_layout(RAFTER, cos_cut=COS, **DG4)

    k = 1.0 / COS
    assert slope.depth_on_cut == pytest.approx(flat.depth_on_cut * k)
    assert slope.tf_top_on_cut == pytest.approx(flat.tf_top_on_cut * k)

    # The outer rows are measured from the flange OUTER faces, which are the
    # plate datum and the section depth - so the top one does not move and
    # the bottom one moves by the full stretch.
    #
    # Row offsets are published rounded to 0.001 mm - a fabrication
    # tolerance, not a floating-point one - so these compare to that, which
    # is still two orders finer than any of the movements being checked.
    assert slope.row_offsets[0] == flat.row_offsets[0]
    assert slope.row_offsets[-1] - flat.row_offsets[-1] == pytest.approx(
        700.0 * (k - 1.0), abs=1e-3
    )
    # The inner rows move by the flange stretch, each in its own direction.
    assert slope.row_offsets[1] - flat.row_offsets[1] == pytest.approx(
        12.0 * (k - 1.0), abs=1e-3
    )
    assert slope.row_offsets[2] - flat.row_offsets[2] == pytest.approx(
        (700.0 - 12.0) * (k - 1.0), abs=1e-3
    )
    # Extensions are a shop dimension, not a section one: they do not stretch.
    assert slope.ext_top == flat.ext_top
    assert slope.ext_bot == flat.ext_bot


def test_the_holes_are_the_rows_crossed_with_the_gauge() -> None:
    ep = end_plate_layout(RAFTER, cos_cut=1.0, **DG4)
    assert ep.gauge == (-50.0, 50.0)
    assert ep.n_holes == 2 * len(ep.row_offsets)
    assert ep.hole_diameter == pytest.approx(24.0 + 2.0)
    # v is measured UP from the datum, the offsets DOWN - so they negate.
    assert (-50.0, -52.0) in ep.holes
    assert (50.0, 40.0) in ep.holes          # the outer row, above the datum


def test_every_hole_stays_on_the_plate() -> None:
    """The closure condition. A hole centre half a diameter from an edge is
    not a detail anyone fabricates."""
    for cos_cut in (1.0, COS):
        ep = end_plate_layout(RAFTER, cos_cut=cos_cut, **DG4)
        r = ep.hole_diameter / 2.0
        v_top, v_bot = ep.ext_top, -(ep.depth_on_cut + ep.ext_bot)
        for u, v in ep.holes:
            assert abs(u) + r <= ep.width / 2.0, (u, v)
            assert v + r <= v_top and v - r >= v_bot, (u, v)


def test_eight_bolt_configurations_add_a_second_inner_row_each_end() -> None:
    ep = end_plate_layout(RAFTER, cos_cut=1.0, rows_in=2, pitch=72.0,
                          **{k: v for k, v in DG4.items() if k != "rows_in"})
    assert ep.row_offsets == (-40.0, 52.0, 124.0, 576.0, 648.0, 740.0)


def test_two_inner_rows_without_a_pitch_raises() -> None:
    with pytest.raises(DetailingError, match="pitch"):
        end_plate_layout(RAFTER, cos_cut=1.0, rows_in=2,
                         **{k: v for k, v in DG4.items() if k != "rows_in"})


# ----------------------------------------------------------------------
# NEGATIVE CONTROL - the branch DG 4 proportions never reach
# ----------------------------------------------------------------------

def test_an_outer_row_that_would_hang_over_the_edge_is_dropped_and_named() -> None:
    """The control this module needed.

    With DG 4 proportions ``de`` = 1.5 db = 36 mm and half a hole is 13 mm,
    so the outer row ALWAYS stays on and the guard never fires. A suite
    built only on those proportions passes whether the guard exists or not.
    Here ``de`` = 10 mm, which puts the row 10 mm from an edge it needs 13
    to clear, and the row must disappear from the layout AND be reported.
    """
    kw = dict(DG4, de=10.0)
    ep = end_plate_layout(RAFTER, cos_cut=1.0, **kw)

    assert ep.ext_top == pytest.approx(50.0)
    assert -40.0 not in ep.row_offsets
    assert 740.0 not in ep.row_offsets
    assert ep.row_offsets == (52.0, 648.0)
    assert len(ep.dropped) == 2
    assert all("extension" in d for d in ep.dropped)


def test_a_flush_plate_has_no_outer_rows_and_is_only_as_deep_as_the_section() -> None:
    ep = end_plate_layout(RAFTER, cos_cut=1.0, extended_top=False,
                          extended_bot=False, **DG4)
    assert ep.ext_top == 0.0 and ep.ext_bot == 0.0
    assert ep.height == pytest.approx(700.0)
    assert ep.row_offsets == (52.0, 648.0)


def test_a_layout_with_no_row_left_on_the_plate_raises() -> None:
    """Blank over a plate with no bolts in it.

    A 40 mm deep section with 12 mm flanges and pfi = 40: the inner rows
    work out at 52 mm below the top flange and 12 mm ABOVE it, and a flush
    plate only runs 0 to 40. Both are off the plate, so there is nothing
    left to bolt. Returning a plate with no holes would be worse than
    raising - it would be drawn, scheduled and welded on.
    """
    tiny = WeldedI(d=40.0, tw=6.0, bf_top=200.0, tf_top=12.0,
                   bf_bot=200.0, tf_bot=12.0)
    with pytest.raises(DetailingError, match="no bolt row"):
        end_plate_layout(tiny, cos_cut=1.0, **dict(DG4, e_in=40.0,
                                                   extended_top=False,
                                                   extended_bot=False))


def test_the_cut_cosine_is_range_checked() -> None:
    for bad in (0.0, -0.5, 1.5):
        with pytest.raises(DetailingError, match="cos_cut"):
            end_plate_layout(RAFTER, cos_cut=bad, **DG4)


# ----------------------------------------------------------------------
# Mass - the number the schedule will believe
# ----------------------------------------------------------------------

def test_the_plate_mass_is_the_gross_plate_less_its_holes() -> None:
    """Longhand: 250 x 852 x 20 minus eight 26 dia holes, at 7850 kg/m3."""
    ep = end_plate_layout(RAFTER, cos_cut=1.0, **DG4)
    gross = 250.0 * 852.0
    holes = 8 * math.pi * 13.0 ** 2
    expected = (gross - holes) * 20.0 * 7850.0 / 1.0e9
    assert ep.height == pytest.approx(852.0)
    assert ep.mass_kg() == pytest.approx(expected, rel=1e-9)
    # Net, not gross - a plate reported at its gross mass is 2% heavy here,
    # and that error runs straight into the MTO.
    assert ep.mass_kg() < gross * 20.0 * 7850.0 / 1.0e9


# ----------------------------------------------------------------------
# Base plate
# ----------------------------------------------------------------------

BP = dict(N=600.0, B=450.0, thickness=12.0, anchor_diameter=24.0,
          n_x=2, n_y=2, s_x=300.0, s_y=300.0, anchor_embedment=450.0)


def test_the_anchor_grid_is_symmetric_about_the_column() -> None:
    bp = base_plate_layout(**BP)
    assert sorted(bp.holes) == [(-150.0, -150.0), (-150.0, 150.0),
                                (150.0, -150.0), (150.0, 150.0)]
    assert bp.n_anchors == 4
    assert sum(x for x, _ in bp.holes) == pytest.approx(0.0)
    assert sum(y for _, y in bp.holes) == pytest.approx(0.0)


def test_the_edge_distances_are_the_plate_less_the_grid() -> None:
    """Longhand: (450 - 300) / 2 across, (600 - 300) / 2 along."""
    bp = base_plate_layout(**BP)
    assert bp.edge_x == pytest.approx(75.0)
    assert bp.edge_y == pytest.approx(150.0)
    assert not bp.dropped


def test_an_anchor_too_close_to_the_plate_edge_is_reported() -> None:
    """NEGATIVE CONTROL for the base plate: a grid that does not fit.

    400 mm across a 450 mm plate leaves 25 mm of edge, and the hole alone is
    28 mm across. The layout is still returned - the anchors are where the
    ACI check put them - but the plate is called out as too small.
    """
    bp = base_plate_layout(**dict(BP, s_x=400.0))
    assert bp.edge_x == pytest.approx(25.0)
    assert bp.dropped and "too small" in bp.dropped[0]


def test_the_anchor_length_is_the_stack_it_passes_through() -> None:
    """Longhand: 450 embedment + 25 grout + 12 plate + 75 projection."""
    bp = base_plate_layout(**BP)
    assert bp.anchor_length == pytest.approx(562.0)


def test_the_base_plate_mass_is_net_of_the_anchor_holes() -> None:
    bp = base_plate_layout(**BP)
    expected = (450.0 * 600.0 - 4 * math.pi * 14.0 ** 2) * 12.0 * 7850.0 / 1.0e9
    assert bp.mass_kg() == pytest.approx(expected, rel=1e-9)


def test_a_plate_needs_at_least_one_anchor_each_way() -> None:
    with pytest.raises(DetailingError):
        base_plate_layout(**dict(BP, n_x=0))


# ----------------------------------------------------------------------
# Cleat and gusset
# ----------------------------------------------------------------------

def test_the_cleat_is_laid_out_on_the_values_its_check_ran_on() -> None:
    """2 x M12 at 60 pitch, 30 end distance, on a 200 deep purlin.

    The bolt line runs UP the web, so 60 + 2 x 30 is the HEIGHT. The width
    has no input and is 2 x the same edge distance - said in the notes,
    because a reader has to be able to tell a checked dimension from a
    stated one.
    """
    c = cleat_layout(thickness=6.0, bolt_diameter=12.0, n_bolts=2,
                     pitch=60.0, edge_end=30.0, purlin_depth=200.0)
    assert c.holes == ((0.0, -30.0), (0.0, 30.0))
    assert c.height == pytest.approx(120.0)
    assert c.width == pytest.approx(60.0)
    assert c.hole_diameter == pytest.approx(14.0)
    assert any("rather than a width being invented" in n for n in c.notes)
    assert not c.dropped


def test_a_cleat_taller_than_its_purlin_web_is_reported() -> None:
    """NEGATIVE CONTROL for the cleat: a bolt line that does not fit.

    A 4-bolt line at 60 pitch stands 240 mm tall, which will not go on a
    200 mm purlin. The layout is still returned - those are the bolts that
    were checked - but the clash is named.
    """
    c = cleat_layout(thickness=6.0, bolt_diameter=12.0, n_bolts=4,
                     pitch=60.0, edge_end=30.0, purlin_depth=200.0)
    assert c.height == pytest.approx(240.0)
    assert c.dropped and "does not fit the purlin" in c.dropped[0]


def test_the_gusset_spreads_its_bolts_over_the_checked_connection_length() -> None:
    """4 bolts over 300 mm is a 100 mm pitch - the block-shear assumption."""
    g = gusset_layout(thickness=12.0, bolt_diameter=20.0, n_bolts=4,
                      l_connection=300.0, w_first_row=100.0,
                      b_available=600.0, whitmore_width=446.41)
    assert [u for u, _ in g.holes] == [-150.0, -50.0, 50.0, 150.0]
    assert g.width == pytest.approx(500.0)
    assert g.height == pytest.approx(600.0)
    assert any("Whitmore" in n for n in g.notes)


def test_a_gusset_needs_two_bolts_to_have_a_connection_length() -> None:
    with pytest.raises(DetailingError):
        gusset_layout(thickness=12.0, bolt_diameter=20.0, n_bolts=1,
                      l_connection=300.0, w_first_row=100.0, b_available=600.0)


# ----------------------------------------------------------------------
# The boundary
# ----------------------------------------------------------------------

def test_every_layout_survives_json_unchanged() -> None:
    """This dict IS the layer boundary - it has to go through json.dumps.

    A tuple, a numpy float or a dataclass in there would raise inside the
    engine's writer, or worse, arrive in FreeCAD as a string.
    """
    layouts = [
        end_plate_layout(RAFTER, cos_cut=COS, **DG4),
        base_plate_layout(**BP),
        cleat_layout(thickness=6.0, bolt_diameter=12.0, n_bolts=2,
                     pitch=60.0, edge_end=30.0, purlin_depth=200.0),
        gusset_layout(thickness=12.0, bolt_diameter=20.0, n_bolts=4,
                      l_connection=300.0, w_first_row=100.0,
                      b_available=600.0),
    ]
    for layout in layouts:
        d = layout.to_dict()
        back = json.loads(json.dumps(d))
        assert back == d
        assert isinstance(back["holes_uv_mm"], list)
        assert all(isinstance(v, list) and len(v) == 2
                   for v in back["holes_uv_mm"])
        assert back["n_holes"] == len(back["holes_uv_mm"])


def test_the_gate_is_closed_and_the_gaps_are_declared() -> None:
    assert all(VERIFIED.values()), VERIFIED
    assert COVERAGE_GAPS
    assert any("Weld" in g for g in COVERAGE_GAPS)


def test_nothing_here_invents_a_dimension() -> None:
    """Working agreement (c): blanks over assumptions.

    Every driving dimension is a required keyword. A default thickness or a
    default bolt size would be an invented design that no check covered, and
    it would be invisible on the sheet.
    """
    import inspect

    for fn in (end_plate_layout, base_plate_layout, cleat_layout,
               gusset_layout):
        sig = inspect.signature(fn)
        for name in ("thickness", "bolt_diameter", "anchor_diameter"):
            p = sig.parameters.get(name)
            if p is not None:
                assert p.default is inspect.Parameter.empty, (fn.__name__, name)
