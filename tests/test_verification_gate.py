"""The verification gate itself.

The gate is the reason the seven defects were found rather than shipped.
These tests protect the gate: that it refuses unverified work under
``strict=True``, that a flag cannot be flipped without something behind
it, and that ``ready_for_issue()`` stays honest.

The load-bearing test here is
``test_no_flag_is_true_without_a_verification_record``.  The failure mode
it guards against is not a bug — it is someone (or some future session)
flipping a flag to make a red suite go green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bimsteelauto_connections as bc
from bimsteelauto_connections import anchors, baseplate, endplate, secondary

FIXTURES = Path(__file__).resolve().parent / "fixtures"

MODULES = {"endplate": endplate, "baseplate": baseplate,
           "anchors": anchors, "secondary": secondary}


# ----------------------------------------------------------------------
# strict=True must refuse
# ----------------------------------------------------------------------

def test_endplate_strict_now_RUNS_because_all_three_configs_are_verified() -> None:
    """All three end-plate configs closed 2026-08-31 — the gate steps aside.

    It refused for two days and cost three defects to satisfy (#1, #10,
    #11). Now that every config is backed by a printed CET expression, it
    must stop blocking, or it becomes bureaucracy.
    """
    assert all(endplate.VERIFIED.values()), endplate.VERIFIED
    g = endplate.EndPlateGeometry(bp=250.0, g=140.0, pfo=50.0, pfi=50.0,
                                  h_rows=(780.0, 720.0, 600.0, 540.0),
                                  de=45.0, pb=60.0)
    d = endplate.design_endplate(Mu=250.0, geom=g, plate_grade="S355JR",
                                 config=endplate.EndPlateConfig.EIGHT_ES,
                                 strict=True)
    assert d.t_plate > 0


def test_baseplate_strict_now_RUNS_after_the_dg1_reading() -> None:
    """Closed 2026-08-31 against DG 1 2nd ed. — the gate steps aside.

    It held for two days and four PROFIS runs, and was only satisfied by
    the governing document. It cost two defects to satisfy (#13, #14) and
    withdrew one candidate (#9a) as a method difference.
    """
    assert all(baseplate.VERIFIED.values()), baseplate.VERIFIED
    g = baseplate.BasePlateGeometry(N=450.0, B=450.0, d=300.0, bf=150.0,
                                    f=175.0, tf=10.7)
    r = baseplate.design_base_plate(Pu=150.0, Mu=120.0, geom=g,
                                    plate_grade="S355JR", fc_prime=30.0,
                                    strict=True)
    assert r.t_selected > 0 and r.T_anchor > 0


def test_anchors_strict_runs_because_all_eight_are_verified() -> None:
    """The gate must also get OUT OF THE WAY once a module is closed."""
    assert all(anchors.VERIFIED.values()), anchors.VERIFIED
    g = anchors.AnchorGroup(d=31.75, hef=450.0, s_x=350.0, s_y=350.0,
                            ca_x1=450.0, ca_x2=450.0, ca_y1=450.0,
                            ca_y2=450.0, ha=800.0)
    r = anchors.check_anchors(Nua=364.096, Vua=60.0, group=g, fc_prime=30.0,
                              strict=True)
    assert r.phiNn > 0


# ----------------------------------------------------------------------
# Flag hygiene
# ----------------------------------------------------------------------

EXPECTED_UNVERIFIED: set[str] = set({
    # EMPTY as of 2026-08-31. All sixteen flags across four modules are
    # closed. The history, so nobody has to reconstruct it:
    #   anchors    8 flags  - Hilti PROFIS, two edge-distance cases
    #   endplate   3 flags  - CET; cost defects #1, #10, #11
    #   baseplate  4 flags  - AISC DG 1 2nd ed. Sects. 3.3/3.4 themselves;
    #                         cost defects #13, #14; candidate #9a
    #                         withdrawn as a method difference, and two
    #                         errors found in DG 1's own Example 4.7
    #   secondary  2 flags  - CET; cost defects #12, #15, and the gusset
    #                         required diagnosing an unconservative error
    #                         in CET's printed buckling capacity
    #
    # An EMPTY set is not the end of verification. ready_for_issue() now
    # returns True, which means every relation has been checked against
    # an independent source -- NOT that every limit state a real
    # connection needs is implemented. The coverage gaps are recorded in
    # each module's VERIFIED comment and asserted below.
    #
    # (built with set({...}) so the empty literal is not a dict.)
})


def test_unverified_set_matches_the_handover() -> None:
    """No open flags, as of 2026-08-31.

    If this fails because a flag was CLOSED, that is good news — update
    the set and say in the commit which report closed it.  If it fails
    because a flag was OPENED, something regressed.
    """
    assert set(bc.unverified()) == EXPECTED_UNVERIFIED, (
        f"opened: {set(bc.unverified()) - EXPECTED_UNVERIFIED}\n"
        f"closed: {EXPECTED_UNVERIFIED - set(bc.unverified())}"
    )


def test_ready_for_issue_is_true_now_that_every_flag_is_closed() -> None:
    """The gate's whole purpose is to eventually step aside."""
    assert not bc.unverified(), bc.unverified()
    assert bc.ready_for_issue() is True


def test_closing_every_flag_does_not_mean_complete_coverage() -> None:
    """The one thing a green gate must never be read as saying.

    ready_for_issue() means every IMPLEMENTED relation was checked
    against an independent source. It does not mean the module performs
    every check a real connection needs. These gaps are live:
    """
    gaps = {
        "secondary.cleat":  ["flexural yield/rupture of the plate",
                             "weld of the plate to the support"],
        "secondary.gusset": ["gusset-to-beam and gusset-to-column welds",
                             "Thornton interface-force distribution",
                             "K is a modelling input, not a verified formula"],
    }
    src = (Path(__file__).resolve().parent.parent
           / "bimsteelauto_connections" / "secondary.py").read_text()
    assert "COVERAGE GAP" in src, "the gaps must stay written in the module"
    assert "Thornton" in src
    assert gaps  # documented here so a reader of the suite sees them too


def test_ready_for_issue_is_exactly_the_negation_of_unverified() -> None:
    assert bc.ready_for_issue() == (not bc.unverified())


def test_verification_status_covers_every_module_with_a_gate() -> None:
    status = bc.verification_status()
    assert set(status) == set(MODULES)
    for name, mod in MODULES.items():
        assert status[name] == dict(mod.VERIFIED)


def test_flags_are_booleans() -> None:
    """A truthy string would pass the gate and read as verified."""
    for name, mod in MODULES.items():
        for k, v in mod.VERIFIED.items():
            assert isinstance(v, bool), f"{name}.{k} = {v!r} is not a bool"


# ----------------------------------------------------------------------
# The load-bearing one
# ----------------------------------------------------------------------

def _fixture_index() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8"))
            for p in FIXTURES.glob("*.json")}


# Which fixture backs which closed flag.  A flag may only be True if the
# named fixture exists AND is complete enough to re-run.
FLAG_EVIDENCE: dict[str, str] = {
    "anchors.tension_steel":    "profis_anchors_ca450",
    "anchors.tension_breakout": "profis_anchors_ca450",
    "anchors.pullout":          "profis_anchors_ca450",
    "anchors.shear_steel":      "profis_anchors_ca450",
    "anchors.shear_breakout":   "profis_anchors_ca450",
    "anchors.pryout":           "profis_anchors_ca450",
    "anchors.side_blowout":     "profis_anchors_ca150",
    "anchors.interaction":      "profis_anchors_ca450",
    # 4E's original B1_C1 record could not be re-run; superseded by a
    # fresh CET run with every input captured.
    "endplate.4E":              "cet_endplate_4E_W24x76",
    # 4ES: run 1 supplied the corrected formula, run 2 verified it. The
    # evidence that EARNS the flag is run 2 — citing run 1 would be citing
    # the source of the fix as its own proof.
    "endplate.4ES":             "cet_endplate_4ES_W30x90",
    # 8ES: run 1 found the pb^2 term, run 2 could not test it (same pb),
    # run 3 discriminated it by varying pb 3.0 -> 5.0. Run 3 is the
    # evidence; citing run 1 would cite the source of the fix as its proof.
    "endplate.8ES":             "cet_endplate_8ES_W36x150_pb5",
    # The base plate flags rest on the Design Guide itself rather than on
    # a tool. Four PROFIS runs could show two implementations differ but
    # never which was right; one reading of Sects. 3.3 and 3.4 did.
    "baseplate.bearing":        "dg1_2nd_ed_baseplate",
    "baseplate.thickness":      "dg1_2nd_ed_baseplate",
    "baseplate.lambda":         "dg1_2nd_ed_baseplate",
    "baseplate.eccentricity":   "dg1_2nd_ed_baseplate",
    "secondary.cleat":          "cet_cleat_singleplate_W18x50",
    # The gusset rests on the SAME CET run, but on a different part of
    # it: the Whitmore width (exact) and the Chapter E buckling chain,
    # whose Fcr agrees with CET's printed Fcr to seven figures. CET's
    # final product is wrong (Fy substituted for Fcr) and the record says
    # so; the module was NOT changed to match it.
    "secondary.gusset":         "cet_gusset_HSS5x5_brace",
}


def test_no_flag_is_true_without_a_verification_record() -> None:
    """Every closed flag must name a fixture. No fixture, no flag.

    This is the structural guard against the failure HANDOVER section 7
    warns about: flipping a flag on a circular check, or on nothing at all.
    """
    fixtures = _fixture_index()
    problems: list[str] = []
    for mod_name, mod in MODULES.items():
        for flag, is_verified in mod.VERIFIED.items():
            if not is_verified:
                continue
            key = f"{mod_name}.{flag}"
            evidence = FLAG_EVIDENCE.get(key)
            if evidence is None:
                problems.append(
                    f"{key} is True but names no verification record. Add it "
                    f"to FLAG_EVIDENCE with the report that closed it, or "
                    f"set the flag back to False."
                )
            elif evidence not in fixtures:
                problems.append(f"{key} cites '{evidence}', which is not in "
                                f"tests/fixtures/")
    assert not problems, "\n".join(problems)


def test_flags_backed_by_incomplete_records_are_visible() -> None:
    """A flag backed by an un-reproducible record is a known weak point.

    endplate.4E is currently in this state: CET's four output numbers were
    recorded, its input geometry was not.  The finding is real (CET printed
    the formula, and that is what exposed defect #1), but the run cannot be
    re-executed, so the flag rests on a record no test can check.

    This test PASSES and reports.  It is a standing note, not a failure —
    turn it into an assertion once the inputs are supplied.
    """
    fixtures = _fixture_index()
    weak = [
        f"{flag} <- {ev} ({fixtures[ev].get('status', 'no status')})"
        for flag, ev in FLAG_EVIDENCE.items()
        if ev in fixtures
        and not str(fixtures[ev].get("status", "")).startswith("complete")
    ]
    if weak:
        pytest.skip("flags resting on un-reproducible records:\n  "
                    + "\n  ".join(weak))


def test_open_flags_have_a_stated_route_to_closing() -> None:
    """An open flag with no plan behind it never closes."""
    bp = _fixture_index()["profis_baseplate_cases"]
    assert "flags_this_can_close" in bp
    assert "flags_this_cannot_close" in bp
    covered = set(bp["flags_this_can_close"]) | set(bp["flags_this_cannot_close"])
    baseplate_flags = {f"baseplate.{k}" for k in baseplate.VERIFIED}
    assert baseplate_flags <= covered, (
        f"no stated route for: {baseplate_flags - covered}"
    )
