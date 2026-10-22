"""
The job sheet: does it read what it says it reads, and refuse what it says
it refuses?

The loader's whole value is that a bad sheet stops the run.  A test suite
that only checks the happy path would let every refusal quietly rot into a
silent default, which is the one failure mode that matters here - a design
produced from a number nobody typed.

So most of this file is about errors, and each one asserts the ROW NUMBER
is in the message.  "purlin.Se_top is required" sends a person hunting;
"[row 41] purlin.Se_top is required" does not.
"""

from __future__ import annotations

import csv
import io
import os

import pytest

from bsa import csvio

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _template(tmp_path):
    p = tmp_path / "job.csv"
    csvio.template(str(p))
    return p


def _rows(path):
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


def _edit(path, block, key, value, *, new_key=None, new_block=None):
    """Change one cell in place and rewrite the file."""
    rows = _rows(path)
    for r in rows:
        if len(r) >= 3 and r[0] == block and r[1] == key:
            if new_block is not None:
                r[0] = new_block
            if new_key is not None:
                r[1] = new_key
            r[2] = value
            break
    else:                                                   # pragma: no cover
        raise AssertionError(f"{block}.{key} is not in the template")
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)
    return path


def _problems(path):
    with pytest.raises(csvio.CsvError) as exc:
        csvio.load(str(path))
    return exc.value.problems


# ---------------------------------------------------------------------------
# The schema drives everything - so it must be self-consistent
# ---------------------------------------------------------------------------


def test_schema_has_no_duplicate_keys():
    seen = set()
    for b, k, *_ in csvio.SCHEMA:
        assert (b, k) not in seen, f"{b}.{k} appears twice in SCHEMA"
        seen.add((b, k))


def test_template_round_trips_through_the_reader(tmp_path):
    """Every row the template writes must be a row the reader accepts.

    These two drift apart the moment they stop sharing SCHEMA, and the
    symptom is a starter file that will not load - the worst possible first
    experience of the tool.
    """
    sheet = csvio.read_sheet(str(_template(tmp_path)))
    assert sheet.problems == []
    assert len(sheet.cells) == len(csvio.SCHEMA)


def test_template_is_a_complete_runnable_job(tmp_path):
    """It must produce engine objects, not just parse."""
    job = csvio.load(str(_template(tmp_path)))
    assert job.spec.job_number
    assert job.spec.geometry.n_frames == 7          # 6 bays -> 7 frame lines
    assert job.inputs.sections.column is not None
    assert job.inputs.bracing.braced_bays == (1, 3)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def test_geometry_is_read_as_metres(tmp_path):
    """The sheet says metres; the engine holds mm.  A factor of 1000 wrong
    here designs a building 30 m tall and would still 'pass' a smoke test."""
    job = csvio.load(str(_template(tmp_path)))
    assert job.spec.geometry.eave_height == pytest.approx(6000.0)
    assert job.spec.geometry.span_widths == (pytest.approx(30000.0),)


def test_unequal_bays_are_kept_apart(tmp_path):
    p = _edit(_template(tmp_path), "geometry", "bay_spacings", "6|7.5|7.5|6")
    job = csvio.load(str(p))
    assert job.spec.geometry.bay_spacings == pytest.approx(
        (6000.0, 7500.0, 7500.0, 6000.0)
    )


def test_commas_are_accepted_in_a_list(tmp_path):
    """A person who types '6, 6, 6' has not made a mistake worth an error."""
    p = _edit(_template(tmp_path), "geometry", "bay_spacings", "6, 6, 6, 6")
    _edit(p, "bracing", "braced_bays", "1|2")
    job = csvio.load(str(p))
    assert len(job.spec.geometry.bay_spacings) == 4


def test_tapered_and_prismatic_marks_both_load(tmp_path):
    p = _template(tmp_path)
    job = csvio.load(str(p))
    col = job.inputs.sections.column
    assert col.start.d == pytest.approx(250.0)
    assert col.end.d == pytest.approx(750.0)

    _edit(p, "sections", "column", "BU-600x200x8/12")
    job = csvio.load(str(p))
    assert job.inputs.sections.column.start.d == pytest.approx(600.0)
    assert job.inputs.sections.column.end.d == pytest.approx(600.0)


def test_enum_matching_is_forgiving_about_case_and_spacing(tmp_path):
    p = _edit(_template(tmp_path), "geometry", "roof_type", "GABLE")
    assert csvio.load(str(p)).spec.geometry.roof_type.value == "gable"
    _edit(p, "geometry", "endwall_type", "Post and Beam")
    assert csvio.load(str(p)).spec.geometry.endwall_type.value == "post_and_beam"


def test_girt_defaults_to_the_purlin_section(tmp_path):
    job = csvio.load(str(_template(tmp_path)))
    assert job.inputs.secondary.girt_section is job.inputs.secondary.purlin_section


# ---------------------------------------------------------------------------
# Refusals.  Each must name the row.
# ---------------------------------------------------------------------------


def test_missing_required_value_names_the_row(tmp_path):
    p = _edit(_template(tmp_path), "geometry", "eave_height", "")
    problems = _problems(p)
    assert any("eave_height" in m and m.startswith("[row ") for m in problems)


def test_unknown_key_is_an_error_not_a_silent_skip(tmp_path):
    p = _edit(_template(tmp_path), "dead", "sheeting", "0.06",
              new_key="sheting")
    problems = _problems(p)
    assert any("sheting" in m for m in problems)


def test_unknown_block_is_an_error(tmp_path):
    p = _edit(_template(tmp_path), "geometry", "purlin_spacing", "1.25",
              new_block="geomety")
    assert any("geomety" in m for m in _problems(p))


def test_a_close_key_gets_a_did_you_mean(tmp_path):
    p = _edit(_template(tmp_path), "site", "Kzt", "1.0", new_key="KZT")
    assert any("Did you mean" in m for m in _problems(p))


def test_non_numeric_value_is_refused(tmp_path):
    p = _edit(_template(tmp_path), "materials", "Fy_builtup", "three forty five")
    assert any("is not a number" in m for m in _problems(p))


def test_bad_enum_lists_what_is_allowed(tmp_path):
    p = _edit(_template(tmp_path), "site", "exposure_category", "Q")
    assert any("B | C | D" in m for m in _problems(p))


def test_a_non_canonical_section_mark_is_refused(tmp_path):
    p = _edit(_template(tmp_path), "sections", "column", "W250x750")
    assert any("canonical built-up mark" in m for m in _problems(p))


def test_a_tapered_strut_is_refused(tmp_path):
    """The eave strut is checked as a prismatic compression member.  A taper
    would be silently checked at one end only."""
    p = _edit(_template(tmp_path), "sections", "strut", "BU-250/400x150x6/8")
    assert any("must be prismatic" in m for m in _problems(p))


def test_a_braced_bay_that_does_not_exist_is_refused(tmp_path):
    p = _edit(_template(tmp_path), "bracing", "braced_bays", "1|9")
    assert any("does not exist" in m for m in _problems(p))


def test_an_angle_brace_is_refused_with_the_reason(tmp_path):
    """A name cannot supply Ag, An_effective and r_min, so it is refused
    rather than approximated from the leg dimensions."""
    p = _edit(_template(tmp_path), "bracing", "roof_brace", "L70x70x7 angle")
    assert any("is not a rod" in m for m in _problems(p))


def test_an_untabulated_rod_size_lists_the_sizes(tmp_path):
    p = _edit(_template(tmp_path), "bracing", "roof_brace", "M19 rod")
    assert any("Sizes are:" in m for m in _problems(p))


def test_negative_where_positive_is_required(tmp_path):
    p = _edit(_template(tmp_path), "detailing", "bolt_diameter", "-24")
    assert any("greater than zero" in m for m in _problems(p))


def test_multi_span_without_an_interior_column_is_refused(tmp_path):
    p = _template(tmp_path)
    _edit(p, "geometry", "span_widths", "15|15")
    _edit(p, "geometry", "frame_type", "multi_span")
    assert any("interior column" in m for m in _problems(p))


def test_multi_span_spans_without_the_frame_type_is_refused(tmp_path):
    """Two spans with frame_type left at clear_span is the trap the engine
    warns about in ``new_project.py``.  It must not be readable as one."""
    p = _edit(_template(tmp_path), "geometry", "span_widths", "15|15")
    assert any("multi_span" in m for m in _problems(p))


def test_every_problem_is_reported_at_once(tmp_path):
    """Fixing a sheet one exception per run is how a person gives up."""
    p = _template(tmp_path)
    _edit(p, "geometry", "eave_height", "")
    _edit(p, "site", "exposure_category", "Q")
    _edit(p, "materials", "Fy_builtup", "abc")
    _edit(p, "detailing", "bolt_diameter", "-24")
    assert len(_problems(p)) >= 4


def test_problems_are_reported_in_file_order(tmp_path):
    p = _template(tmp_path)
    _edit(p, "detailing", "bolt_diameter", "-24")
    _edit(p, "geometry", "eave_height", "")
    rows = [int(m.split("]")[0][5:])
            for m in sorted(_problems(p), key=csvio._problem_order)
            if m.startswith("[row ")]
    assert rows == sorted(rows)


def test_a_duplicate_row_is_refused_once_not_twice(tmp_path):
    """The second report ('required, but empty') would be the same fault
    said again, and a doubled error list reads as a broken tool."""
    p = _template(tmp_path)
    rows = _rows(p)
    rows.append(["geometry", "eave_height", "9.0", "m", ""])
    with io.open(p, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)
    problems = _problems(p)
    assert sum("eave_height" in m for m in problems) == 1
    assert any("set twice" in m for m in problems)


# ---------------------------------------------------------------------------
# export - lossy, and it must say so
# ---------------------------------------------------------------------------


REAL_JOB = os.path.join(HERE, "jobs", "DEMO-2026-014", "design_result.json")


@pytest.mark.skipif(not os.path.exists(REAL_JOB), reason="no job to export")
def test_export_recovers_what_the_result_carries(tmp_path):
    out = tmp_path / "recovered.csv"
    _, n_got, n_todo = csvio.export(REAL_JOB, str(out))
    assert n_got > 20 and n_todo > 0

    cells = {(r[0], r[1]): r[2] for r in _rows(out)[1:] if len(r) >= 3}
    assert cells[("job", "job_number")] == "DEMO-2026-014"
    assert cells[("sections", "column")].startswith("BU-")
    # the unequal bays must survive - averaging them is a silent 25% error
    assert "7.5" in cells[("geometry", "bay_spacings")]


@pytest.mark.skipif(not os.path.exists(REAL_JOB), reason="no job to export")
def test_an_exported_sheet_will_not_run_until_it_is_filled_in(tmp_path):
    """The point of the marker.  A 90%-filled sheet that refuses to run is
    useful; one that runs on invented numbers is dangerous."""
    out = tmp_path / "recovered.csv"
    csvio.export(REAL_JOB, str(out))
    problems = _problems(out)
    assert any(csvio.TODO in m for m in problems)


@pytest.mark.skipif(not os.path.exists(REAL_JOB), reason="no job to export")
def test_export_does_not_ask_for_the_girt_catalogue_twice(tmp_path):
    """Both names came back equal, so the sheet should say same_as_purlin."""
    out = tmp_path / "recovered.csv"
    csvio.export(REAL_JOB, str(out))
    cells = {(r[0], r[1]): r[2] for r in _rows(out)[1:] if len(r) >= 3}
    assert cells[("girt", "same_as_purlin")] == "yes"
    assert cells[("girt", "shape")] == ""


def test_the_todo_marker_is_refused_wherever_it_appears(tmp_path):
    p = _edit(_template(tmp_path), "purlin", "Se_top", csvio.TODO)
    problems = _problems(p)
    assert any(csvio.TODO in m and "Se_top" in m for m in problems)
    assert sum("Se_top" in m for m in problems) == 1


# ---------------------------------------------------------------------------
# File-level shape
# ---------------------------------------------------------------------------


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(csvio.CsvError) as exc:
        csvio.load(str(tmp_path / "nope.csv"))
    assert "not found" in exc.value.report()


def test_comments_and_blank_lines_are_skipped(tmp_path):
    p = _template(tmp_path)
    rows = _rows(p)
    rows.insert(1, ["# this is a note to myself"])
    rows.insert(2, [])
    with io.open(p, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)
    assert csvio.load(str(p)).spec.job_number


def test_a_missing_header_is_reported(tmp_path):
    p = _template(tmp_path)
    rows = _rows(p)[1:]
    with io.open(p, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)
    assert any("header" in m for m in _problems(p))
