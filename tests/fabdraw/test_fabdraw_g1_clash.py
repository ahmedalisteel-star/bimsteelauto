"""G1 - clash gate.

The fixture's geometry is known by construction (fabdraw_models.py), so a
penetration depth is checked against the number it was built with, not
against what the clash engine happens to say.

CLASH_TOLERANCE_MM is 5 mm (user, 2026-10-26). Every test here is written
so it means the same thing for ANY tolerance below the fixture's shift
(TEST2_SHIFT_MM); if the tolerance ever reaches it, the tests fail loudly
rather than pass quietly.
"""

from __future__ import annotations

import json

import pytest

from . import fabdraw_models as fm
from bimsteelauto.fabdraw import gate_clash, ifcmodel

H1 = next(s for s in gate_clash.load_clash_sets()["sets"] if s["id"] == "H1")
H2 = next(s for s in gate_clash.load_clash_sets()["sets"] if s["id"] == "H2")


def _tol_below_fixture_shift():
    tol = gate_clash.CLASH_TOLERANCE_MM
    assert tol is None or tol < fm.TEST2_SHIFT_MM, (
        f"CLASH_TOLERANCE_MM = {tol} mm swallows the fixture's {fm.TEST2_SHIFT_MM} mm shift - the fixture, "
        "not the tolerance, must change for this test to mean anything")


# --------------------------------------------------------------- measurement
def test_touching_faces_are_not_penetrations(good_ifc):
    """Plate on beam end, bolt head on plate: touching, and the tree reports
    nothing at all - so no tolerance is needed to clear them."""
    assert gate_clash.measure(good_ifc, H1) == []
    assert gate_clash.measure(good_ifc, H2) == []


def test_shifted_plate_measured_at_its_built_depth(shifted_ifc):
    res = gate_clash.measure(shifted_ifc, H1)
    assert len(res) == 1
    r = res[0]
    assert {r["a_class"], r["b_class"]} == {"IfcBeam", "IfcPlate"}
    assert r["depth_mm"] == pytest.approx(fm.TEST2_SHIFT_MM, abs=1e-3)   # as built; metres -> mm done once


# --------------------------------------------------------------- the gate
def test_shifted_plate_fails_g1(g1_shifted):
    """Spec test 2: one plate pushed into the flange -> G1 fails, FOR THAT
    REASON. Shift 10 mm, not the spec's 5: see TEST2_SHIFT_MM."""
    _tol_below_fixture_shift()
    assert not g1_shifted.passed
    h1 = [f for f in g1_shifted.hard if f.rule == "H1"]
    assert len(h1) == 1
    assert h1[0].data["depth_mm"] == pytest.approx(fm.TEST2_SHIFT_MM, abs=1e-3)
    assert set(h1[0].marks) == {"b1", "p1"}
    assert all(len(g) == 22 for g in h1[0].global_ids)


def test_g1_cannot_pass_while_tolerance_unset(good_ifc, fabdir, monkeypatch):
    """Blanks fail: no tolerance, no pass - even on a clean model."""
    monkeypatch.setattr(gate_clash, "CLASH_TOLERANCE_MM", None)
    r = gate_clash.run_clash_gate(good_ifc)
    assert not r.passed
    assert any("CLASH_TOLERANCE_MM" in b for b in r.blockers)
    assert not r.hard                       # and the reason is the blank, not a clash


def test_overlap_under_the_tolerance_passes_g1(fabdir):
    """The user's decision, stated as a test: with CLASH_TOLERANCE_MM = 5,
    a plate 4 mm into the beam is counted, reported, and does NOT fail G1."""
    assert gate_clash.CLASH_TOLERANCE_MM == 5.0
    p = fabdir / "shift4.ifc"
    fm.beam_assembly_model(p, plate_shift_mm=4.0)
    r = gate_clash.run_clash_gate(p)
    assert r.passed, r.report()
    assert r.summary["H1 steel vs steel"] == "1 penetrations measured, 0 excluded, 1 within tolerance"


@pytest.mark.skipif(gate_clash.CLASH_TOLERANCE_MM is None,
                    reason="CLASH_TOLERANCE_MM not given yet (gate_clash.py) - G1 cannot pass without it")
def test_good_model_passes_g1(g1_good):
    """Spec test 1, G1 part."""
    assert g1_good.passed, g1_good.report()


def test_bolt_in_its_own_hole_is_excluded_and_listed(g1_shifted):
    """The shifted plate drags into its own bolts' heads. Those pairs are
    the fastener's OWN part, so they are excluded - and every one is listed."""
    assert not [f for f in g1_shifted.hard if f.rule == "H2"]
    excluded = [n for n in g1_shifted.notes if n.startswith("H2 excluded")]
    assert len(excluded) == 4
    assert all("from assembly B1" in n for n in excluded)


def test_bolt_through_a_part_it_does_not_connect_is_hard(fabdir):
    """Negative control for the exclusion: the second assembly sits 40 mm
    past the first and 30 mm across, so each assembly's end bolts go through
    the OTHER assembly's plate, beside its holes. Not connected -> hard."""
    _tol_below_fixture_shift()
    p = fabdir / "crossed.ifc"
    fm.two_assembly_model(p, origin2=(fm.DIMS.length + 40.0, 30.0, 0.0))
    r = gate_clash.run_clash_gate(p)
    h2 = [f for f in r.hard if f.rule == "H2"]
    assert len(h2) == 8                                  # 4 bolts each way
    assert all(f.data["depth_mm"] > 0.0 for f in h2)
    assert not [f for f in r.hard if f.rule == "H1"]     # the two end plates only touch


def test_realizing_connection_wins_over_the_assembly():
    f, e = fm.beam_assembly_model()
    bolt, plate, beam = e["bolts"][0], e["plates"][0], e["beam"]
    import ifcopenshell.guid
    f.createIfcRelConnectsWithRealizingElements(
        ifcopenshell.guid.new(), None, None, None, None, plate, beam, [bolt], None)
    gids, src = ifcmodel.fastener_connected_parts(bolt)
    assert gids == {plate.GlobalId, beam.GlobalId}
    assert src == "IfcRelConnectsWithRealizingElements"


# --------------------------------------------------------------- outputs
def test_reports_and_bcf_written(g1_shifted, fabdir):
    from bcf.v2.bcfxml import BcfXml

    d = fabdir / "g1_shifted"
    data = json.loads((d / "clash_report.json").read_text(encoding="utf-8"))
    assert data["gate"] == "G1 clash" and data["passed"] is False
    assert data["n_hard"] == len(g1_shifted.hard)
    assert "NOT FOR ISSUE" in (d / "clash_report.html").read_text(encoding="utf-8")
    assert len(BcfXml.load(str(d / "clash_report.bcf")).topics) == len(g1_shifted.hard)


def test_unevaluated_warning_sets_say_so(g1_good):
    """A warning set that did not run must not read as a pass."""
    assert any(n.startswith("W1") and "NOT EVALUATED" in n for n in g1_good.notes)
    assert any(n.startswith("W2") and "NOT EVALUATED" in n for n in g1_good.notes)


def test_edge_minimum_is_the_approved_rule():
    assert gate_clash.BOLT_EDGE_MIN_D == 1.25            # AISC J3.4, 1.25d (user, 2026-10-26)


def test_erection_tolerance_is_recorded():
    assert gate_clash.ERECTION_TOLERANCE_MM == 5.0       # user, 2026-10-26: "max ... 5 mm for erection"
