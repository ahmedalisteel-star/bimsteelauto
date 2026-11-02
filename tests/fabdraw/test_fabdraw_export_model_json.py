"""The steel-project skill's model_to_ifc.py (rewritten 2026-10-26) against G2.

The exporter lives in the skill folder, not in this repo (decision 1), so
these tests run it as a subprocess on a small model.json built here with
every dimension known, and are skipped where the skill is not installed.

    column C1 x2   built-up 400 x 200 x 6/10, H 3000, on a 20 mm base plate
                   (4 holes d 26) with a 20 mm top plate (2 holes d 22)
    rafter R1 x2   IPE 200 on a 20 mm plate whose 2 holes are coaxial with
                   its column's top-plate holes -> 4 bolts, group EV9
    anchor AB1 x8  M20 rod + washer + nut through every base-plate hole, so
                   every round hole is filled (S5)
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ue
import ifcopenshell.util.shape as us
import pytest

from bimsteelauto.fabdraw import gate_ids

SKILL = Path.home() / ".claude" / "skills" / "steel-project" / "scripts"
pytestmark = pytest.mark.skipif(not (SKILL / "model_to_ifc.py").is_file(),
                                reason="steel-project skill not installed on this machine")


def _box(x0, x1, y0, y1, z0, z1):
    return {"hexa": [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                     [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]}


def _hole(x, y, z0, z1, d):
    return {"p0": [x, y, z0], "p1": [x, y, z1], "d": d, "t": z1 - z0}


def _asm(mark, piece, ifc, name, grade, parts, profile=""):
    return {"mark": mark, "piece": piece, "ifc": ifc, "name": name, "grade": grade, "phase": "test", "seq": 1,
            "line": 1, "profile": profile, "length": 0.0, "kg": 1.0, "paint_m2": 0.0, "parts": parts}


def _column(y, piece):
    return _asm("C1", piece, "IfcColumn", "column", "St 52", [
        {"kind": "flange", "name": "PL10x200 outer flange", "solid": _box(0, 10, y - 100, y + 100, 20, 3000)},
        {"kind": "flange", "name": "PL10x200 inner flange", "solid": _box(390, 400, y - 100, y + 100, 20, 3000)},
        {"kind": "web", "name": "PL6 web", "solid": _box(10, 390, y - 3, y + 3, 20, 3000)},
        {"kind": "plate", "name": "PL20 base plate", "mark": "BP1", "solid": _box(-50, 450, y - 150, y + 150, 0, 20),
         "holes": [_hole(x, y + dy, 0, 20, 26) for x in (-25, 425) for dy in (-100, 100)]},
        {"kind": "plate", "name": "PL20 top plate", "mark": "EV9", "solid": _box(0, 400, y - 100, y + 100, 3000, 3020),
         "holes": [_hole(x, y, 3000, 3020, 22) for x in (100, 300)]},
    ], profile="BU-400x200x6/10")


def _ipe200_profile():
    h, b, tw, tf = 200, 100, 5.6, 8.5
    return [(-b / 2, 0), (b / 2, 0), (b / 2, tf), (tw / 2, tf), (tw / 2, h - tf), (b / 2, h - tf), (b / 2, h),
            (-b / 2, h), (-b / 2, h - tf), (-tw / 2, h - tf), (-tw / 2, tf), (-b / 2, tf)]


def _rafter(y, piece):
    return _asm("R1", piece, "IfcBeam", "rafter", "St 37", [
        {"kind": "I", "name": "IPE 200", "solid": {"prism": {
            "profile": _ipe200_profile(), "origin": [0, y, 3040], "u": [0, 1, 0], "v": [0, 0, 1], "w": [1, 0, 0],
            "length": 400}}},
        {"kind": "plate", "name": "PL20 end plate", "mark": "EV9", "solid": _box(0, 400, y - 100, y + 100, 3020, 3040),
         "holes": [_hole(x, y, 3020, 3040, 22) for x in (100, 300)]},
    ], profile="IPE 200")


def _anchor(x, y, piece):
    return _asm("AB1", piece, "IfcMechanicalFastener", "anchor", "4.6", [
        {"kind": "rod", "name": "M20 anchor rod", "solid": {"cyl": {"p0": [x, y, -300], "p1": [x, y, 60], "d": 20}}},
        {"kind": "washer", "name": "plate washer", "solid": {"cyl": {"p0": [x, y, 20], "p1": [x, y, 23], "d": 48}}},
        {"kind": "nut", "name": "nut", "solid": {"cyl": {"p0": [x, y, 23], "p1": [x, y, 39], "d": 32}}},
    ], profile="M20x360")


def base_model(anchor_x=-25.0, bolt_qty=4):
    """``anchor_x`` moves the FIRST anchor only (the one at y = -100)."""
    spots = [(x, yc + dy) for yc in (0, 6000) for x in (-25, 425) for dy in (-100, 100)]
    anchors = [_anchor(anchor_x if i == 0 else x, y, f"AB1-{i + 1}") for i, (x, y) in enumerate(spots)]
    return {"schema": "test", "job": "T-1", "title": "fixture", "units": "mm",
            "assemblies": [_column(0, "C1-1"), _column(6000, "C1-2"), _rafter(0, "R1-1"), _rafter(6000, "R1-2"),
                           *anchors],
            "bolts": [{"group": "EV9", "size": "M20", "grade": "8.8", "qty": bolt_qty, "length": 40, "where": "", "at": []}],
            "cladding": [], "concrete": [], "grids": {}, "levels": {}, "notes": [], "total_kg": 0.0}


def _volume_mm3(el):
    # keep the shape object alive while its geometry is read: reading
    # ``create_shape(...).geometry`` in one expression returns volume 0.0
    shape = ifcopenshell.geom.create_shape(ifcopenshell.geom.settings(), el)
    return us.get_volume(shape.geometry) * 1e9


def export(tmp_path, model, name="t"):
    mj = tmp_path / f"{name}.json"
    mj.write_text(json.dumps(model), encoding="utf-8")
    out = tmp_path / f"{name}.ifc"
    run = subprocess.run([sys.executable, str(SKILL / "model_to_ifc.py"), str(mj), str(out)],
                         capture_output=True, text=True)
    return run, out


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    d = tmp_path_factory.mktemp("skill_export")
    run, out = export(d, base_model())
    assert run.returncode == 0, run.stderr
    return out, run.stdout


def test_export_passes_g2(exported):
    out, _ = exported
    r = gate_ids.run_ids_gate(out)
    assert r.passed, r.report()


def test_built_up_member_is_one_parametric_main_part_of_known_volume(exported):
    """Flanges + web -> ONE IfcColumn whose body is ONE fused solid: a UNION
    of three extrusions (loose touching items gave the clash tree a false
    8 mm penetration on JOB-018 C2a). Its volume is the closed form."""
    f = ifcopenshell.open(str(exported[0]))
    cols = f.by_type("IfcColumn")
    assert len(cols) == 2
    c = cols[0]
    (top,) = c.Representation.Representations[0].Items
    assert top.is_a("IfcBooleanResult") and top.Operator == "UNION"
    leaves, stack = [], [top]
    while stack:
        it = stack.pop()
        if it.is_a("IfcBooleanResult"):
            stack += [it.FirstOperand, it.SecondOperand]
        else:
            leaves.append(it.is_a())
    assert leaves == ["IfcExtrudedAreaSolid"] * 3
    assert ue.get_psets(c)["BSA_Fabrication"]["Profile"] == "BU-400x200x6/10"
    vol_mm3 = _volume_mm3(c)
    closed = 2 * 200 * 10 * 2980 + 380 * 6 * 2980
    assert vol_mm3 == pytest.approx(closed, rel=1e-9)


def test_plate_thickness_and_holes_as_openings(exported):
    f = ifcopenshell.open(str(exported[0]))
    base = [p for p in f.by_type("IfcPlate") if "base plate" in p.Description][0]
    assert ue.get_psets(base)["BSA_Fabrication"]["Thickness"] == pytest.approx(20.0)
    assert len(base.HasOpenings) == 4
    vol = _volume_mm3(base)
    gross, holes = 500 * 300 * 20, 4 * 3.141592653589793 * 13 ** 2 * 20
    assert vol == pytest.approx(gross - holes, rel=2e-3)          # round holes are facetted


def test_bolts_placed_from_coaxial_holes_and_realize_the_connection(exported):
    f = ifcopenshell.open(str(exported[0]))
    bolts = [b for b in f.by_type("IfcMechanicalFastener") if b.PredefinedType == "BOLT"]
    assert len(bolts) == 4
    for b in bolts:
        assert (b.NominalDiameter, b.NominalLength) == (20.0, 40.0)
        (rel,) = b.IsConnectionRealization
        ends = {rel.RelatingElement.Description, rel.RelatedElement.Description}
        assert ends == {"PL20 top plate", "PL20 end plate"}


def test_anchor_is_one_fastener_connected_through_the_base_plate_hole(exported):
    f = ifcopenshell.open(str(exported[0]))
    anchors = [b for b in f.by_type("IfcMechanicalFastener") if b.PredefinedType == "ANCHORBOLT"]
    assert len(anchors) == 8
    from bimsteelauto.fabdraw import ifcmodel
    cmap = ifcmodel.connected_parts_map(f)
    for a in anchors:
        assert (a.NominalDiameter, a.NominalLength) == (20.0, 360.0)
        gids, src = cmap[a.GlobalId]
        assert len(gids) == 1 and "base plate" in f.by_guid(next(iter(gids))).Description
        assert "hole" in src


def test_identical_parts_share_a_mark_and_different_ones_do_not(exported):
    f = ifcopenshell.open(str(exported[0]))
    mark = {}
    for p in f.by_type("IfcPlate") + f.by_type("IfcColumn") + f.by_type("IfcBeam"):
        asm = ue.get_aggregate(p).Name
        mark[(asm, p.Description)] = ue.get_psets(p)["BSA_Fabrication"]["PartMark"]
    assert mark[("C1-1", "PL20 base plate")] == mark[("C1-2", "PL20 base plate")]
    assert mark[("C1-1", "PL20 top plate")] == mark[("C1-2", "PL20 top plate")]
    # same 400 x 200 x 20 plate with the same holes, but St 37 not St 52 -> not the same part
    assert mark[("R1-1", "PL20 end plate")] != mark[("C1-1", "PL20 top plate")]
    assert mark[("C1-1", "PL20 base plate")].startswith("p")
    assert mark[("C1-1", "PL10x200 outer flange + PL10x200 inner flange + PL6 web")].startswith("m")


def test_bolt_group_that_does_not_match_the_holes_is_not_guessed(tmp_path):
    run, out = export(tmp_path, base_model(bolt_qty=3))
    assert run.returncode == 0, run.stderr
    assert "3 BOLTS NOT PLACED" in run.stdout
    f = ifcopenshell.open(str(out))
    assert not [b for b in f.by_type("IfcMechanicalFastener") if b.PredefinedType == "BOLT"]
    (row,) = json.loads(out.with_suffix(".unplaced_bolts.json").read_text(encoding="utf-8"))
    assert row["group"] == "EV9" and "4 coaxial hole stacks" in row["why"]
    r = gate_ids.run_ids_gate(out)                       # and S5 now sees the empty holes
    assert {x.rule for x in r.hard} == {"S5"}
    assert sum(x.data["n_empty"] for x in r.hard) == 8   # 2 per plate, 4 plates


def test_a_hexa_that_is_not_a_prism_is_refused(tmp_path):
    m = base_model()
    m["assemblies"][0]["parts"][3]["solid"]["hexa"][6][2] += 5.0       # lift one top corner of the base plate
    run, _ = export(tmp_path, m)
    assert run.returncode != 0
    assert "not a translational prism" in run.stderr


def test_s2_fails_an_anchor_that_misses_every_hole(tmp_path):
    """Negative control for decision 6: one anchor 175 mm outside the plate.
    It connects nothing (S2), and the hole it should be in is empty (S5)."""
    run, out = export(tmp_path, base_model(anchor_x=-200.0))
    assert run.returncode == 0, run.stderr
    r = gate_ids.run_ids_gate(out)
    assert {f.rule for f in r.hard} == {"S2", "S5"}
    (s2,) = [f for f in r.hard if f.rule == "S2"]
    assert "hole" in s2.message
    (s5,) = [f for f in r.hard if f.rule == "S5"]
    assert s5.data["n_empty"] == 1
