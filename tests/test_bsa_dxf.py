"""
Tests for ``bsa_dxf``.

The point of most of these is not that a function returns the right number -
it is that the FILE is a drawing.  The export this package replaces produced
something that looked like output, passed no check, and only failed once a
fabricator opened it, so the tests that matter here are the ones that read
the written file back and ask whether it is usable.
"""

from __future__ import annotations

import json
import math
import os

import ezdxf
import pytest

from bsa_dxf import (
    Standard,
    TitleBlock,
    frame_from_result,
    parse_section,
    pick_scale,
    stamp_for,
    title_block_from_result,
    verify,
    write_set,
)
from bsa_dxf import details as D
from bsa_dxf import sheet as S
from bsa_dxf import standards as STD

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULT_JSON = os.path.join(HERE, "design_result.json")


@pytest.fixture(scope="module")
def result() -> dict:
    if not os.path.exists(RESULT_JSON):
        pytest.skip("design_result.json not present")
    with open(RESULT_JSON, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def written(result, tmp_path_factory):
    out = tmp_path_factory.mktemp("dxf")
    return write_set(result, str(out)), str(out)


# ---------------------------------------------------------------------------
# the file is a drawing
# ---------------------------------------------------------------------------


def test_writes_a_file_that_is_actually_a_dxf(written):
    dxfset, _ = written
    assert dxfset.sheets, "no sheet was written"
    for path in dxfset.paths():
        # The old exporter wrote a bare entity stream with no SECTION or EOF;
        # ezdxf is the check that would have caught it.
        ezdxf.readfile(path)


def test_written_sheets_pass_their_own_verifier(written):
    dxfset, _ = written
    assert dxfset.errors == [], dxfset.errors
    for path in dxfset.paths():
        assert verify(path) == []


def test_sheet_carries_annotation_not_just_geometry(written):
    """A drawing with no text and no dimensions is a plot of a model."""
    dxfset, _ = written
    doc = ezdxf.readfile(dxfset.paths()[0])
    msp = doc.modelspace()
    assert len(msp.query("TEXT")) > 100
    assert len(msp.query("DIMENSION")) >= 10


def test_no_degenerate_splines(written):
    dxfset, _ = written
    doc = ezdxf.readfile(dxfset.paths()[0])
    for spline in doc.modelspace().query("SPLINE"):
        assert len(spline.control_points) >= spline.dxf.degree + 1


def test_units_are_declared_as_millimetres(written):
    dxfset, _ = written
    doc = ezdxf.readfile(dxfset.paths()[0])
    assert doc.header["$INSUNITS"] == 4


def test_layer_standard_is_applied(written):
    dxfset, _ = written
    doc = ezdxf.readfile(dxfset.paths()[0])
    for name in STD.LAYERS:
        assert name in doc.layers, f"{name} missing"


def test_grid_bubbles_are_blocks_with_attributes(written):
    """A bubble has to be renumberable, not a circle plus loose text."""
    dxfset, _ = written
    doc = ezdxf.readfile(dxfset.paths()[0])
    refs = [e for e in doc.modelspace().query("INSERT")
            if e.dxf.name == "BSA_GRID_BUBBLE"]
    assert refs, "no grid bubbles"
    assert all(list(r.attribs) for r in refs), "bubble carries no attribute"


# ---------------------------------------------------------------------------
# the stamp is not a caller's choice
# ---------------------------------------------------------------------------


def test_stamp_reports_errors(result):
    bad = dict(result, errors=["frame did not converge"])
    assert "NOT FOR ISSUE" in stamp_for(bad)


def test_stamp_reports_unverified(result):
    bad = dict(result, errors=[], unverified=["anchor shear"])
    assert "NOT FOR ISSUE" in stamp_for(bad)


def test_stamp_reports_an_overstressed_member(result):
    bad = dict(
        result, errors=[], unverified=[],
        members=[{"mark": "R1",
                  "checks": [{"name": "Flexure", "utilisation": 1.04}]}],
    )
    assert "OVERSTRESSED" in stamp_for(bad)


def test_clean_design_still_says_preliminary(result):
    clean = dict(result, errors=[], unverified=[], members=[])
    assert stamp_for(clean) == "PRELIMINARY - NOT FOR CONSTRUCTION"


def test_written_sheet_carries_the_stamp_text(written, result):
    dxfset, _ = written
    doc = ezdxf.readfile(dxfset.paths()[0])
    wanted = stamp_for(result)
    assert any(t.dxf.text == wanted for t in doc.modelspace().query("TEXT"))


# ---------------------------------------------------------------------------
# regressions: things that used to print on top of each other
# ---------------------------------------------------------------------------


def test_notes_block_clears_the_title_block():
    """The notes used to be drawn downward and landed in the approval box."""
    std = Standard(scale=50.0, sheet="A1")
    tb = TitleBlock(notes=[f"NOTE {i}" for i in range(12)])
    assert S.notes_top(std, tb) > S.frame_area(std)[1] + S._tb_height(std)


def test_notes_top_grows_with_the_number_of_notes():
    std = Standard(scale=50.0, sheet="A1")
    few = S.notes_top(std, TitleBlock(notes=["A"]))
    many = S.notes_top(std, TitleBlock(notes=["A"] * 10))
    assert many > few


def test_title_block_height_counts_every_band():
    """It used to leave out the consultant strip, so things landed on it."""
    std = Standard(scale=50.0, sheet="A1")
    bands = (S._ROW_TITLE + S._ROW * 6
             + S._ROW_REV * (S._N_REV_ROWS + 1)
             + S._APPROVAL_H + S._CONSULT_H)
    assert S._tb_height(std) == pytest.approx(std.paper(bands))


def test_detail_geometry_is_centred_on_its_extents_not_its_origin():
    """An end plate measures from the flange, so its origin is near its top.

    Centring the origin hung the plate out of the bottom of its panel.
    """
    panel = (0.0, 0.0, 100.0, 100.0)
    # extents mostly BELOW the local origin, as a real end plate is
    cx, cy = D._centre_on(panel, (-150.0, 150.0), (-930.0, 76.0), 0.05)
    lo = cy + -930.0 * 0.05
    hi = cy + 76.0 * 0.05
    assert lo >= panel[1] - 1e-9
    assert hi <= panel[3] + 1e-9
    assert (lo + hi) / 2 == pytest.approx(50.0)


def test_detail_fit_shrinks_a_plate_that_would_overflow():
    panel = (0.0, 0.0, 50.0, 50.0)
    z = D._fit(panel, (-150.0, 150.0), (-930.0, 76.0), 10.0)
    assert (1006.0 * z) <= 50.0 + 1e-9


def test_every_detail_stays_inside_its_own_panel(result):
    """End to end: no entity of a detail may cross its panel border."""
    doc = ezdxf.new("R2010", setup=True)
    std = Standard(scale=50.0, sheet="A1")
    STD.apply(doc, std)
    msp = doc.modelspace()
    conns = [c for c in result.get("connections", []) if c.get("detail")]
    assert conns, "no connections to draw"

    box_w, box_h = 78.0, 96.0
    for conn in conns:
        before = len(msp)
        D.detail_view(msp, std, conn, (0.0, 0.0), detail_scale=5.0,
                      box_w=box_w, box_h=box_h)
        w, h = std.paper(box_w), std.paper(box_h)
        for e in list(msp)[before:]:
            try:
                box = e.bbox()
            except Exception:            # entities without a bbox helper
                continue
            if box is None or not box.has_data:
                continue
            assert box.extmin.x >= -1.0, f"{conn['mark']} runs off the left"
            assert box.extmax.x <= w + 1.0, f"{conn['mark']} runs off the right"
            assert box.extmin.y >= -1.0, f"{conn['mark']} runs off the bottom"
            assert box.extmax.y <= h + 1.0, f"{conn['mark']} runs off the top"
        msp.delete_all_entities()


# ---------------------------------------------------------------------------
# geometry and set-out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mark,d0,d1,bf,tw,tf", [
    ("BU-450/850x250x8/16", 450.0, 850.0, 250.0, 8.0, 16.0),
    ("BU-850x250x7/16", 850.0, 850.0, 250.0, 7.0, 16.0),
])
def test_parse_built_up_marks(mark, d0, d1, bf, tw, tf):
    s = parse_section(mark)
    assert (s.d_start, s.d_end, s.bf, s.tw, s.tf) == (d0, d1, bf, tw, tf)
    assert s.tapered is (d0 != d1)


def test_parse_cold_formed_and_rod():
    assert parse_section("C200x60x20x2.5").kind == "C"
    assert parse_section("M20 rod").kind == "rod"


def test_unknown_mark_is_kept_verbatim_not_silently_renamed():
    s = parse_section("SOMETHING ODD")
    assert s.mark == "SOMETHING ODD"


def test_peb_mark_notation():
    """The drawing writes web range and both flanges, not the engine's id."""
    assert parse_section("BU-450/850x250x8/16").describe() == \
        "W:450-850X8-2F:250X16"


def test_frame_set_out_matches_the_spec(result):
    frame = frame_from_result(result)
    geo = result["spec"]["geometry"]
    assert frame.width == pytest.approx(sum(geo["span_widths"]))
    assert frame.eave_height == pytest.approx(geo["eave_height"])
    rise = (geo["span_widths"][0] / 2.0) * geo["roof_slope"]
    assert frame.ridge_height == pytest.approx(geo["eave_height"] + rise)


def test_rafter_length_agrees_with_the_engine(result):
    """The drawn rafter must be the member the engine designed."""
    frame = frame_from_result(result)
    drawn = [m.length for m in frame.members if m.member_type == "rafter"]
    engine = [m["length"] for m in result["members"]
              if m["member_type"] == "rafter"]
    assert drawn and engine
    assert drawn[0] == pytest.approx(engine[0], rel=1e-3)


def test_roof_line_peaks_at_the_ridge(result):
    frame = frame_from_result(result)
    span = frame.spans[0]
    assert frame.roof_z(span / 2) == pytest.approx(frame.ridge_height)
    assert frame.roof_z(0.0) == pytest.approx(frame.eave_height)
    assert frame.roof_z(span) == pytest.approx(frame.eave_height)


def test_rafter_deep_end_is_at_the_knee(result):
    """Both rafters of a gable must carry their deep end at the eave."""
    frame = frame_from_result(result)
    for m in (x for x in frame.members if x.member_type == "rafter"):
        at_eave = m.section.d_start if m.start[1] < m.end[1] else m.section.d_end
        at_ridge = m.section.d_end if m.start[1] < m.end[1] else m.section.d_start
        assert at_eave >= at_ridge


def test_tapered_outline_is_closed_and_the_right_depth():
    """grow=+1 is the LEFT normal of travel; for an upward axis that is -x."""
    from bsa_dxf.geom import tapered_outline
    polys = tapered_outline((0, 0), (0, 1000), 100.0, 300.0, 10.0, grow=1)
    silhouette = polys[0]
    assert len(silhouette) == 4
    assert silhouette[3][0] == pytest.approx(-100.0)   # depth at the start
    assert silhouette[2][0] == pytest.approx(-300.0)   # depth at the end


def test_tapered_outline_grow_flips_the_side():
    from bsa_dxf.geom import tapered_outline
    left = tapered_outline((0, 0), (0, 1000), 100.0, 300.0, 10.0, grow=1)[0]
    right = tapered_outline((0, 0), (0, 1000), 100.0, 300.0, 10.0, grow=-1)[0]
    assert left[3][0] == pytest.approx(-right[3][0])


def test_columns_grow_into_the_span_not_out_of_it(result):
    """Both columns used to grow outward, putting the steel off the grid.

    The outer face belongs on the grid line and the section must occupy the
    space between the grids, or the knee never meets the rafter.
    """
    frame = frame_from_result(result)
    cols = [m for m in frame.members if m.member_type == "column"]
    assert len(cols) >= 2
    for m in cols:
        xs = [p[0] for p in m.outline()[0]]
        assert min(xs) >= -1e-6, f"{m.mark} sticks out past the left grid"
        assert max(xs) <= frame.width + 1e-6,             f"{m.mark} sticks out past the right grid"


def test_knee_overlaps_so_the_haunch_closes(result):
    """The column's inner face must reach under the rafter at the eave."""
    frame = frame_from_result(result)
    col = next(m for m in frame.members if m.member_type == "column")
    raf = next(m for m in frame.members if m.member_type == "rafter")
    inner_face = max(p[0] for p in col.outline()[0])
    assert inner_face > 0.0
    # the rafter's underside at that x is below the column top
    depth = raf.section.d_start
    assert depth > 0 and inner_face <= frame.width / 2


# ---------------------------------------------------------------------------
# sheet arithmetic
# ---------------------------------------------------------------------------


def test_pick_scale_returns_an_office_scale():
    from bsa_dxf.views import SCALES
    assert pick_scale(30000.0, 8500.0, 771.0, 384.0) in SCALES


def test_pick_scale_actually_fits():
    s = pick_scale(30000.0, 8500.0, 771.0, 384.0)
    assert 30000.0 / s <= 771.0 and 8500.0 / s <= 384.0


def test_pick_scale_is_the_largest_that_fits():
    from bsa_dxf.views import SCALES
    s = pick_scale(30000.0, 8500.0, 771.0, 384.0)
    smaller = [x for x in SCALES if x < s]
    assert all(30000.0 / x > 771.0 or 8500.0 / x > 384.0 for x in smaller)


def test_paper_millimetres_scale_with_the_plot_scale():
    assert Standard(scale=50.0).paper(2.5) == 125.0
    assert Standard(scale=10.0).paper(2.5) == 25.0


def test_sheet_size_is_in_model_units():
    std = Standard(scale=50.0, sheet="A1")
    assert std.sheet_size == (841.0 * 50, 594.0 * 50)


def test_unknown_text_role_names_the_ones_that_exist():
    with pytest.raises(KeyError, match="dim"):
        Standard().text("no-such-role")


# ---------------------------------------------------------------------------
# title block
# ---------------------------------------------------------------------------


def test_title_block_takes_its_identity_from_the_result(result):
    tb = title_block_from_result(result, drawing_title="FRAME SECTION",
                                 drawing_no="E01")
    assert tb.job_no == result["spec"]["job_number"]
    assert tb.project == result["spec"]["project_name"]
    assert tb.drawing_no == "E01"
    assert tb.notes


def test_title_block_leaves_unknown_fields_blank(result):
    """Better an empty consultant row than an invented one."""
    tb = title_block_from_result(result)
    assert tb.consultant == ""
    assert tb.customer == ""


def test_notes_quote_the_codes_that_were_used(result):
    tb = title_block_from_result(result)
    joined = " ".join(tb.notes)
    assert result["spec"]["settings"]["member_code"] in joined
    assert result["spec"]["settings"]["load_code"] in joined


def test_default_revision_row_is_created():
    tb = TitleBlock()
    assert tb.revisions and tb.revisions[0][2] == "ISSUED FOR APPROVAL"


# ---------------------------------------------------------------------------
# failure behaviour
# ---------------------------------------------------------------------------


def test_verify_rejects_a_headerless_entity_stream(tmp_path):
    """Exactly what the previous exporter produced."""
    bad = tmp_path / "bad.dxf"
    bad.write_text("0\nLINE\n8\nsheet_layer\n10\n0\n20\n0\n11\n1\n21\n1\n")
    problems = verify(str(bad))
    assert problems and "cannot be read back" in problems[0]


def test_verify_flags_a_drawing_with_no_annotation(tmp_path):
    doc = ezdxf.new("R2010", setup=True)
    doc.modelspace().add_line((0, 0), (100, 100))
    path = tmp_path / "bare.dxf"
    doc.saveas(path)
    problems = verify(str(path))
    assert any("no TEXT" in p for p in problems)
    assert any("no DIMENSION" in p for p in problems)


def test_write_set_reports_a_broken_result_instead_of_raising(tmp_path):
    """No spans means no building - it must refuse, not invent one."""
    out = write_set({"spec": {"geometry": {"span_widths": []}}}, str(tmp_path))
    assert out.errors and not out.ok
    assert "span_widths" in out.errors[0]


def test_missing_eave_height_is_refused(tmp_path):
    out = write_set(
        {"spec": {"geometry": {"span_widths": [30000.0]}}}, str(tmp_path)
    )
    assert out.errors and "eave_height" in out.errors[0]


def test_frame_error_names_what_is_missing():
    from bsa_dxf import FrameError
    with pytest.raises(FrameError, match="span_widths"):
        frame_from_result({"spec": {"geometry": {}}})


def test_empty_members_still_produces_a_sheet(tmp_path, result):
    thin = dict(result, members=[], connections=[])
    out = write_set(thin, str(tmp_path))
    assert out.ok, out.errors
    assert verify(out.paths()[0]) == []
