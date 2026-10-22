"""Tests for bimsteelauto.report - the calculation sheet.

A renderer has no engineering to verify, but it has one job that matters
more than looking right: it must not let a blocked design read as an
issuable one, and it must not drop a limit state, a clause, an assumption or
a coverage gap on the way to the page.  Those are what is tested here.
"""

import re

import pytest

from bimsteelauto.report import (
    DISCLAIMER,
    collect_coverage_gaps,
    render_html,
    render_text,
)
from bimsteelauto.spec import (
    ConnectionDesign,
    ConnectionType,
    DesignResult,
    EnclosureClassification,
    ExposureCategory,
    LoadCase,
    LoadCombination,
    LoadSummary,
    Materials,
    MemberCheck,
    MemberDesign,
    MemberForces,
    MemberType,
    PEBGeometry,
    ProjectSite,
    ProjectSpec,
    RiskCategory,
    SupportReaction,
)


@pytest.fixture
def spec():
    return ProjectSpec(
        project_name="Demo PEB Warehouse",
        job_number="DEMO-2026-001",
        site=ProjectSite(
            basic_wind_speed=45.0,
            exposure_category=ExposureCategory.C,
            enclosure_classification=EnclosureClassification.ENCLOSED,
            risk_category=RiskCategory.II,
        ),
        geometry=PEBGeometry.from_m(
            span_widths=[30.0],
            bay_spacings=[6.0, 7.5, 7.5, 6.0],
            eave_height=7.0,
            roof_slope=0.10,
            purlin_spacing=1.5,
            girt_spacing=2.0,
        ),
        materials=Materials(
            Fy_builtup=345.0,
            Fu_builtup=450.0,
            Fy_cold_formed=345.0,
            Fu_cold_formed=450.0,
            bolt_grade="A325",
            anchor_grade="F1554-36",
            fc_concrete=30.0,
        ),
    )


@pytest.fixture
def good_member():
    return MemberDesign(
        mark="R1",
        member_type=MemberType.RAFTER,
        section_id="BU-800/400x250x8/16",
        length=15_075.0,
        governing_combination="1.2D+1.0W",
        forces=MemberForces(P=41.2, Vy=88.5, Mz=-306.7, station=0.0),
        checks=(
            MemberCheck(
                "Flexure (lateral-torsional buckling)",
                "AISC 360-16, Eq. F2-2 (Sect. F2), p. 16.1-47",
                306.7, 812.4, 0.377, "kN.m", note="Lb = 1508 mm, Cb = 1.243",
            ),
            MemberCheck(
                "Shear (shear yielding)",
                "AISC 360-16, Eq. G2-1 (Sect. G2), p. 16.1-70",
                88.5, 794.2, 0.111, "kN",
            ),
        ),
        weight=1087.0,
        note="governing section BU-800x250x8/16 at x = 0 mm",
    )


@pytest.fixture
def bad_member():
    return MemberDesign(
        mark="C1",
        member_type=MemberType.COLUMN,
        section_id="BU-400/800x250x8/16",
        length=7_000.0,
        governing_combination="0.9D+1.0W",
        forces=MemberForces(P=210.0, Vy=60.0, Mz=520.0, station=7000.0),
        checks=(
            MemberCheck(
                "Combined axial + flexure (H1-1a)",
                "AISC 360-16, Eq. H1-1a (Sect. H1), p. 16.1-77",
                1.42, 1.0, 1.42, "-",
            ),
        ),
        weight=980.0,
    )


@pytest.fixture
def connection():
    return ConnectionDesign(
        mark="BP1",
        connection_type=ConnectionType.BASE_PLATE,
        member_marks=("C1",),
        design_force_basis="analysis",
        utilisation=2.571,
        verified=True,
        detail={
            "limit_states": [
                {
                    "name": "Anchor shear (governing mode)",
                    "demand": 80.4, "capacity": 31.27, "utilisation": 2.571,
                    "clause": "ACI 318-19 Ch. 17 - concrete breakout", "unit": "kN",
                },
                {
                    "name": "Concrete bearing under the plate",
                    "demand": 110.5, "capacity": 3315.0, "utilisation": 0.033,
                    "clause": "AISC Design Guide 1, Eq. 3.1.1", "unit": "kN",
                },
            ],
            "selections": {"plate thickness": "12 mm (required 5.9)"},
            "warnings": ["End plate geometry SUGGESTED from AISC DG 4"],
            "joint": {"node": "B1", "Nua_uplift": 0.0, "combination_uplift": "0.9D+1.0W"},
        },
    )


def _result(spec, members=(), connections=(), **kw):
    base = dict(
        spec=spec,
        members=tuple(members),
        connections=tuple(connections),
        engine_version="bimsteelauto 0.8.0.dev0",
    )
    base.update(kw)
    return DesignResult(**base)


# ---------------------------------------------------------------------------
# the banner - the one thing that must never be wrong
# ---------------------------------------------------------------------------


def test_failing_design_says_not_for_issue(spec, good_member, bad_member):
    html = render_html(_result(spec, [good_member, bad_member]))
    assert "NOT FOR ISSUE" in html
    assert "READY FOR ISSUE" not in html


def test_the_banner_lists_every_blocker(spec, good_member, bad_member):
    html = render_html(_result(spec, [good_member, bad_member]))
    assert "C1" in html
    assert "1.420" in html or "1.42" in html


def test_unverified_relation_blocks_issue_even_when_everything_passes(
    spec, good_member
):
    """Numbers can all be fine and the sheet still must not be issuable."""
    html = render_html(
        _result(spec, [good_member], unverified=("endplate_8ES",))
    )
    assert "NOT FOR ISSUE" in html
    assert "endplate_8ES" in html


def test_a_clean_design_says_ready_for_issue(spec, good_member):
    html = render_html(_result(spec, [good_member]))
    assert "READY FOR ISSUE" in html
    assert ">NOT FOR ISSUE<" not in html


def test_an_empty_design_is_never_ready(spec):
    html = render_html(_result(spec))
    assert "NOT FOR ISSUE" in html
    assert "no members" in html


def test_a_failing_connection_blocks_a_passing_frame(
    spec, good_member, connection
):
    html = render_html(_result(spec, [good_member], [connection]))
    assert "NOT FOR ISSUE" in html
    assert "BP1" in html


# ---------------------------------------------------------------------------
# nothing may be dropped on the way to the page
# ---------------------------------------------------------------------------


def test_every_member_appears(spec, good_member, bad_member):
    html = render_html(_result(spec, [good_member, bad_member]))
    assert "R1" in html and "C1" in html


def test_every_limit_state_appears_with_its_clause(spec, good_member):
    html = render_html(_result(spec, [good_member]))
    for c in good_member.checks:
        assert c.name in html
        assert c.reference in html


def test_check_notes_reach_the_page(spec, good_member):
    """Lb and Cb are how a reviewer re-does the LTB check by hand."""
    html = render_html(_result(spec, [good_member]))
    assert "Lb = 1508 mm, Cb = 1.243" in html


def test_connection_limit_states_appear_with_clauses(spec, good_member, connection):
    html = render_html(_result(spec, [good_member], [connection]))
    assert "Anchor shear (governing mode)" in html
    assert "ACI 318-19 Ch. 17 - concrete breakout" in html


def test_connection_warnings_reach_the_page(spec, good_member, connection):
    html = render_html(_result(spec, [good_member], [connection]))
    assert "SUGGESTED" in html


def test_assumptions_are_printed(spec, good_member):
    a = (
        "Pedestal depth assumed 800 mm solely so the anchor check has a "
        "member to sit in; the foundation is a separate civil package."
    )
    html = render_html(_result(spec, [good_member], assumptions=(a,)))
    assert "Pedestal depth assumed 800 mm" in html
    assert "Stated assumptions" in html


def test_coverage_gaps_are_printed(spec, good_member):
    gaps = collect_coverage_gaps()
    html = render_html(_result(spec, [good_member], coverage_gaps=gaps))
    assert "Checks NOT performed" in html
    if gaps:
        assert "Design Guide 25" in html


def test_coverage_gaps_are_collected_when_not_supplied(spec, good_member):
    """A sheet with no gap list must not silently claim full coverage."""
    html = render_html(_result(spec, [good_member]))
    assert "Checks NOT performed" in html


def test_non_uniform_bay_spacing_is_shown_not_averaged(spec, good_member):
    html = render_html(_result(spec, [good_member]))
    assert "6 + 7.5 + 7.5 + 6" in html


def test_the_five_wind_inputs_are_all_shown(spec, good_member):
    html = render_html(_result(spec, [good_member]))
    for token in ("45", "Exposure category", "enclosed", "Risk category", "Kzt"):
        assert token in html


def test_loads_appear_when_supplied(spec, good_member):
    loads = LoadSummary(
        cases=(LoadCase("D", "dead", "Roof dead load", "self weight + cladding"),),
        combinations=(
            LoadCombination("1.2D+1.0W", {"D": 1.2, "W": 1.0}, "ASCE 7-16 Sect. 2.3.1"),
        ),
        qh=1.024,
        end_zone_a=3000.0,
    )
    html = render_html(_result(spec, [good_member], loads=loads))
    assert "1.2D+1.0W" in html
    assert "ASCE 7-16 Sect. 2.3.1" in html
    assert "1.024" in html


def test_reactions_appear_and_explain_the_uplift_sign(spec, good_member):
    r = (SupportReaction("B1", "0.9D+1.0W", Fx=-18.9, Fy=-42.0, Mz=0.0),)
    html = render_html(_result(spec, [good_member], reactions=r))
    assert "-42.00" in html
    assert "net uplift" in html


def test_the_disclaimer_is_always_present(spec, good_member):
    assert DISCLAIMER in render_html(_result(spec, [good_member]))
    assert DISCLAIMER in render_text(_result(spec, [good_member]))


# ---------------------------------------------------------------------------
# the document itself
# ---------------------------------------------------------------------------


def test_output_is_a_complete_html_document(spec, good_member):
    html = render_html(_result(spec, [good_member]))
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert "<style>" in html


def test_the_sheet_is_self_contained(spec, good_member):
    """No external CSS, fonts, images or scripts.

    It has to open from a network share, from an email attachment, and from
    inside FreeCAD - none of which can be relied on to reach the internet.
    """
    html = render_html(_result(spec, [good_member]))
    assert "<script" not in html.lower()
    assert "http://" not in html
    assert "https://" not in html
    assert not re.search(r"<link[^>]+stylesheet", html, re.I)
    assert "<img" not in html.lower()


def test_text_is_escaped_not_injected(spec):
    """A project name is user input and reaches the page.

    Not a security question here so much as a correctness one: an ampersand
    in a job title must not silently break the document.
    """
    nasty = ProjectSpec(
        project_name="Smith & Sons <script>alert(1)</script>",
        job_number="A&B-001",
        site=spec.site,
        geometry=spec.geometry,
        materials=spec.materials,
    )
    html = render_html(_result(nasty))
    assert "<script>alert(1)</script>" not in html
    assert "&amp;" in html
    assert "&lt;script&gt;" in html


def test_designer_and_checker_are_carried(spec, good_member):
    html = render_html(
        _result(spec, [good_member]), designer="Ahmed Ali", checker="A N Other"
    )
    assert "Ahmed Ali" in html
    assert "A N Other" in html


def test_design_basis_states_the_capacity_rule(spec, good_member):
    html = render_html(_result(spec, [good_member]))
    assert "50%" in html
    assert "AISC 360-16" in html
    assert "LRFD" in html


# ---------------------------------------------------------------------------
# text renderer
# ---------------------------------------------------------------------------


def test_text_renderer_flags_a_blocked_design(spec, good_member, bad_member):
    txt = render_text(_result(spec, [good_member, bad_member]))
    assert "NOT FOR ISSUE" in txt
    assert "C1" in txt


def test_text_renderer_sorts_worst_first(spec, good_member, bad_member):
    txt = render_text(_result(spec, [good_member, bad_member]))
    assert txt.index("C1") < txt.index("R1")


def test_text_renderer_reports_totals(spec, good_member, bad_member):
    txt = render_text(_result(spec, [good_member, bad_member]))
    assert "max utilisation" in txt
    assert "2,067 kg" in txt
