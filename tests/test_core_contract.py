"""The ConnectionResult contract, and the design-force rule behind it.

Six modules were collapsed onto one result shape so that one renderer
produces every calculation sheet.  These tests hold that shape still — a
renderer is only as safe as the contract it consumes.
"""

from __future__ import annotations

import math

import pytest

from bimsteelauto_connections import (
    CAPACITY_FRACTION, ConnectionError_, ConnectionResult, DesignForce,
    ForceCriterion, GeometryError, LimitState, design_force, frame_summary,
    from_checks,
)


# ----------------------------------------------------------------------
# Task #4 — design force
# ----------------------------------------------------------------------

def test_capacity_fraction_is_the_stated_project_basis() -> None:
    from bimsteelauto_connections import CAPACITY_FRACTION_SOURCE
    assert CAPACITY_FRACTION == 0.50
    assert "50%" in CAPACITY_FRACTION_SOURCE
    assert CAPACITY_FRACTION_SOURCE.strip(), "the basis must be stated, not implied"


def test_capacity_governs_when_larger() -> None:
    f = design_force(analysis=250.0, member_capacity=520.0,
                     fraction=CAPACITY_FRACTION, label="Mu", unit="kNm")
    assert f.value == 260.0
    assert f.criterion is ForceCriterion.CAPACITY


def test_analysis_governs_when_larger() -> None:
    f = design_force(analysis=400.0, member_capacity=520.0,
                     fraction=CAPACITY_FRACTION)
    assert f.value == 400.0
    assert f.criterion is ForceCriterion.ANALYSIS
    assert f.capacity_based == 260.0, "the losing number is still carried"


def test_no_capacity_given_says_so_rather_than_assuming() -> None:
    f = design_force(analysis=45.0)
    assert f.value == 45.0
    assert f.capacity_based is None
    assert "no capacity minimum" in f.report()


def test_half_and_half_must_be_given_together() -> None:
    with pytest.raises(ConnectionError_):
        design_force(analysis=100.0, member_capacity=500.0)
    with pytest.raises(ConnectionError_):
        design_force(analysis=100.0, fraction=0.5)


@pytest.mark.parametrize("bad", [-0.1, 0.0, 1.5])
def test_fraction_out_of_range_rejected(bad: float) -> None:
    with pytest.raises(GeometryError):
        design_force(analysis=100.0, member_capacity=500.0, fraction=bad)


def test_negative_analysis_force_rejected() -> None:
    with pytest.raises(GeometryError):
        design_force(analysis=-1.0)


def test_report_shows_both_numbers_and_which_won() -> None:
    """The sheet must show why, not just what."""
    r = design_force(analysis=250.0, member_capacity=520.0,
                     fraction=CAPACITY_FRACTION, label="Mu", unit="kNm").report()
    assert "250" in r and "260" in r and "50%" in r
    assert ForceCriterion.CAPACITY.value in r


def test_the_open_question_in_handover_section_6_is_still_open() -> None:
    """The 50% rule applied to shear takes 45 kN to 190 kN — a 4.2x jump.

    HANDOVER section 6 item 3 flags this as unresolved: does the rule apply
    to all actions or to moment only?  This test documents the magnitude so
    the question does not get quietly forgotten, and will need updating
    when the clause is read.
    """
    f = design_force(analysis=45.0, member_capacity=380.0,
                     fraction=CAPACITY_FRACTION, label="Vu")
    assert f.value == 190.0
    assert math.isclose(f.value / f.analysis, 4.222, rel_tol=1e-3)


# ----------------------------------------------------------------------
# LimitState / ConnectionResult
# ----------------------------------------------------------------------

def test_limit_state_utilization_and_pass() -> None:
    ls = LimitState(name="bolt tension", capacity=200.0, demand=150.0)
    assert math.isclose(ls.utilization, 0.75)
    assert ls.passed


def test_zero_capacity_is_infinite_utilization_not_a_crash() -> None:
    ls = LimitState(name="degenerate", capacity=0.0, demand=10.0)
    assert ls.utilization == math.inf
    assert not ls.passed


def test_governing_is_the_worst_limit_state() -> None:
    r = ConnectionResult(
        mark="EAVE-1", kind="eave",
        limit_states=(
            LimitState("a", 200.0, 100.0),
            LimitState("b", 120.0, 100.0),
            LimitState("c", 500.0, 100.0),
        ),
    )
    assert r.governing.name == "b"
    assert math.isclose(r.utilization, 100.0 / 120.0)
    assert r.passed


def test_result_fails_if_any_limit_state_fails() -> None:
    r = ConnectionResult(
        mark="M1", kind="base plate",
        limit_states=(LimitState("ok", 200.0, 100.0),
                      LimitState("over", 50.0, 100.0)),
    )
    assert not r.passed
    assert r.governing.name == "over"


def test_empty_result_has_no_governing_state() -> None:
    r = ConnectionResult(mark="X", kind="none", limit_states=())
    assert r.governing is None
    assert r.utilization == 0.0
    assert r.passed


def test_unverified_result_says_not_for_issue() -> None:
    """The single most important line on an unverified calculation sheet."""
    r = ConnectionResult(mark="M1", kind="base plate",
                         limit_states=(LimitState("bearing", 200.0, 100.0),),
                         verified=False)
    assert "UNVERIFIED" in r.report()
    assert "not for issue" in r.report()

    v = ConnectionResult(mark="M1", kind="base plate",
                         limit_states=(LimitState("bearing", 200.0, 100.0),),
                         verified=True)
    assert "UNVERIFIED" not in v.report()


def test_result_is_immutable() -> None:
    r = ConnectionResult(mark="M1", kind="k", limit_states=())
    with pytest.raises((AttributeError, TypeError)):
        r.mark = "M2"          # type: ignore[misc]


def test_from_checks_builds_a_result() -> None:
    r = from_checks(mark="C1", kind="cleat",
                    checks=[("shear", 300.0, 0.5), ("bearing", 250.0, 0.6)],
                    demand=150.0)
    assert len(r.limit_states) == 2
    assert all(ls.demand == 150.0 for ls in r.limit_states)
    assert r.verified is False, "results must default to unverified"


def test_frame_summary_sorts_worst_first_and_counts_honestly() -> None:
    rows = [
        ConnectionResult(mark="A", kind="eave",
                         limit_states=(LimitState("x", 100.0, 20.0),),
                         verified=True),
        ConnectionResult(mark="B", kind="base plate",
                         limit_states=(LimitState("y", 100.0, 150.0),)),
    ]
    out = frame_summary(rows)
    marks = [ln.split()[0] for ln in out.splitlines()
             if ln[:1] in ("A", "B")]
    assert marks == ["B", "A"], f"worst utilisation must sort first, got {marks}"
    assert "1 failing" in out
    assert "1 unverified" in out


def test_frame_summary_handles_no_connections() -> None:
    assert frame_summary([]) == "no connections"


def test_design_force_is_immutable() -> None:
    f = design_force(analysis=10.0)
    with pytest.raises((AttributeError, TypeError)):
        f.value = 20.0        # type: ignore[misc]
    assert isinstance(f, DesignForce)
