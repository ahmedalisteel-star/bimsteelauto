"""G2 - data gate (IDS + S1-S3).

Every rule gets a NEGATIVE CONTROL: a model that is correct in every way
except the one thing that rule checks, which must fail on that rule and no
other. A rule that passes the good model proves nothing on its own.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import ifcopenshell
import pytest

from . import fabdraw_models as fm
from bimsteelauto.fabdraw import gate_ids

PEB = Path(__file__).resolve().parents[2]


def _run(f, tmp_path, name="m.ifc"):
    p = tmp_path / name
    f.write(str(p))
    return gate_ids.run_ids_gate(p)


def _failing_rules(r):
    return {f.rule for f in r.hard}


# --------------------------------------------------------------- positive
def test_good_model_passes_g2_at_100_percent(good_ifc, fabdir):
    r = gate_ids.run_ids_gate(good_ifc, fabdir / "g2_good")
    assert r.passed, r.report()
    assert not r.blockers
    for rule in ("FR-01", "FR-02", "FR-03", "FR-04", "FR-05", "FR-06"):
        assert r.summary[rule].startswith("PASS")
    data = json.loads((fabdir / "g2_good" / "ids_report.json").read_text(encoding="utf-8"))
    assert data["passed"] is True


def test_every_spec_actually_applied_to_something(good_ifc):
    """No vacuous passes: each FR rule saw at least one element."""
    r = gate_ids.run_ids_gate(good_ifc)
    for rule in ("FR-01", "FR-02", "FR-03", "FR-04", "FR-05", "FR-06"):
        n = int(r.summary[rule].split("/")[1].split()[0])
        assert n > 0, rule


# --------------------------------------------------------------- spec test 3
def test_deleted_part_mark_fails_g2(nomark_ifc):
    r = gate_ids.run_ids_gate(nomark_ifc)
    assert not r.passed
    assert _failing_rules(r) == {"FR-03"}
    (f,) = r.hard
    assert "PartMark" in f.message
    model = ifcopenshell.open(str(nomark_ifc))
    el = model.by_guid(f.global_ids[0])
    assert el.is_a("IfcPlate")
    assert f.marks == ("",)                  # no mark, and NOT the Name standing in for one


# --------------------------------------------------------------- negative controls, IDS
def test_fr01_missing_profile(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.remove_property(e["beam"], "Profile")
    assert _failing_rules(_run(f, tmp_path)) == {"FR-01"}


def test_fr02_missing_thickness(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.remove_property(e["plates"][1], "Thickness")
    assert _failing_rules(_run(f, tmp_path)) == {"FR-02"}


def test_fr03_missing_material(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.remove_material(f, e["plates"][0])
    assert _failing_rules(_run(f, tmp_path)) == {"FR-03"}


def test_fr03_part_not_in_an_assembly(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.take_out_of_assembly(f, e["plates"][0])
    assert _failing_rules(_run(f, tmp_path)) == {"FR-03"}


def test_fr04_missing_assembly_mark(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.remove_property(e["assembly"], "AssemblyMark")
    assert _failing_rules(_run(f, tmp_path)) == {"FR-04"}


def test_fr04_a_model_with_no_assembly_fails(tmp_path):
    """The trap both of our exporters fall into: zero assemblies must not
    pass FR-04 vacuously."""
    f, e = fm.beam_assembly_model()
    asm = e["assembly"]
    for el in [e["beam"], *e["plates"], *e["bolts"]]:
        fm.take_out_of_assembly(f, el)
    ifcopenshell.api.run("root.remove_product", f, product=asm)
    r = _run(f, tmp_path)
    assert "FR-04" in _failing_rules(r)
    assert any(x.rule == "FR-04" and "no applicable element" in x.message for x in r.hard)


def test_fr05_missing_nominal_diameter(tmp_path):
    f, e = fm.beam_assembly_model()
    e["bolts"][3].NominalDiameter = None
    assert _failing_rules(_run(f, tmp_path)) == {"FR-05"}


def test_fr05_missing_bolt_grade(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.remove_material(f, e["bolts"][0])
    assert _failing_rules(_run(f, tmp_path)) == {"FR-05"}


def test_fr06_opening_that_voids_no_steel_part(tmp_path):
    f, _ = fm.beam_assembly_model()
    fm.void_a_wall(f)
    assert _failing_rules(_run(f, tmp_path)) == {"FR-06"}


# --------------------------------------------------------------- negative controls, S-rules
def test_s1_two_main_parts(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.set_main_part(f, e["plates"][0], True)
    assert _failing_rules(_run(f, tmp_path)) == {"S1"}


def test_s1_no_main_part(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.set_main_part(f, e["beam"], False)
    assert _failing_rules(_run(f, tmp_path)) == {"S1"}


def test_s2_fastener_connecting_nothing(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.take_out_of_assembly(f, e["bolts"][0])
    assert _failing_rules(_run(f, tmp_path)) == {"S2"}


def test_s3_hole_baked_into_the_body(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.bake_hole_into_body(f, e["plates"][0])
    r = _run(f, tmp_path)
    assert _failing_rules(r) == {"S3"}
    assert r.hard[0].global_ids == (e["plates"][0].GlobalId,)


def test_s3_allows_a_half_space_end_cut(tmp_path):
    """A plane clip is a fitting, not a hole - S3 must not fail it."""
    f, e = fm.beam_assembly_model()
    fm.bake_hole_into_body(f, e["plates"][0], half_space=True)
    assert _run(f, tmp_path).passed


def test_s4_faceted_brep_body(tmp_path):
    """What the FreeCAD export writes: a hole in it would be invisible to S3."""
    f, e = fm.beam_assembly_model()
    fm.mesh_body(f, e["plates"][0], "faceted")
    r = _run(f, tmp_path)
    assert _failing_rules(r) == {"S4"}
    assert r.hard[0].global_ids == (e["plates"][0].GlobalId,)
    assert r.hard[0].data["items"] == ["IfcFacetedBrep"]


def test_s4_triangulated_face_set_body(tmp_path):
    f, e = fm.beam_assembly_model()
    fm.mesh_body(f, e["beam"], "triangulated")
    r = _run(f, tmp_path)
    assert _failing_rules(r) == {"S4"}
    assert r.hard[0].data["items"] == ["IfcTriangulatedFaceSet"]


def test_s4_finds_a_mesh_inside_a_boolean(tmp_path):
    """The top item is a boolean of an extrusion - only looking THROUGH it
    finds the mesh."""
    f, e = fm.beam_assembly_model()
    fm.mesh_body(f, e["plates"][1], "faceted", inside_boolean=True)
    assert e["plates"][1].Representation.Representations[0].Items[0].is_a("IfcBooleanResult")
    assert _failing_rules(_run(f, tmp_path)) == {"S4"}


def test_s4_part_without_a_body(tmp_path):
    """Nothing to read is not a pass."""
    f, e = fm.beam_assembly_model()
    e["plates"][0].Representation.Representations[0].RepresentationIdentifier = "Axis"
    r = _run(f, tmp_path)
    assert _failing_rules(r) == {"S4"}
    assert "no Body representation" in r.hard[0].message


def test_s4_passes_extrusions_with_openings_and_clips(good_ifc):
    """The good model: extrusions, an I-profile, openings, and no mesh."""
    assert gate_ids.run_ids_gate(good_ifc).summary["S4"] == "PASS"


def test_s5_a_hole_with_no_bolt(tmp_path):
    """The JOB-018 failure in miniature: one bolt never placed."""
    f, e = fm.beam_assembly_model()
    fm.remove_bolt(f, e["bolts"][5])                       # 2nd bolt of the far-end plate
    r = _run(f, tmp_path)
    assert _failing_rules(r) == {"S5"}
    (x,) = r.hard
    assert x.global_ids == (e["plates"][1].GlobalId,)
    assert x.data["n_empty"] == 1 and "1 of 4" in x.message


def test_s5_a_rod_through_the_hole_fills_it(tmp_path):
    """Positive control: a round bar counts as well as a bolt."""
    f, e = fm.beam_assembly_model()
    fm.replace_bolt_with_rod(f, e["bolts"][0])
    r = _run(f, tmp_path)
    assert r.passed, r.report()
    assert r.summary["S5"] == "PASS"


def test_s5_does_not_treat_the_cope_as_a_hole(good_ifc):
    from bimsteelauto.fabdraw import ifcmodel

    model = ifcopenshell.open(str(good_ifc))
    holes = ifcmodel.round_hole_openings(model)
    assert len(holes) == 8                                  # 4 per end plate; the cope is not round
    assert all(h.Name.startswith("hole-") for h in holes)


# --------------------------------------------------------------- schema
def test_non_ifc4_model_is_a_blocker(tmp_path):
    f = ifcopenshell.file(schema="IFC2X3")
    f.create_entity("IfcProject", GlobalId=ifcopenshell.guid.new(), Name="old")
    p = tmp_path / "old.ifc"
    f.write(str(p))
    r = gate_ids.run_ids_gate(p)
    assert not r.passed
    assert any("IFC2X3" in b for b in r.blockers)


# --------------------------------------------------------------- layering
def test_engine_init_does_not_import_fabdraw():
    """fabdraw needs ifcopenshell/ifcclash/ifctester; the rest of the engine
    must not, so bimsteelauto/__init__.py must never import it."""
    tree = ast.parse((PEB / "bimsteelauto" / "__init__.py").read_text(encoding="utf-8"))
    names = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            names.append((n.module or "") + ":" + ",".join(a.name for a in n.names))
        elif isinstance(n, ast.Import):
            names += [a.name for a in n.names]
    assert not [x for x in names if "fabdraw" in x], names


def test_ids_file_is_schema_valid():
    from ifctester import ids

    s = ids.open(str(gate_ids.IDS_PATH), validate=True)
    assert [sp.name.split()[0] for sp in s.specifications] == [
        "FR-01", "FR-02", "FR-03", "FR-04", "FR-05", "FR-06"]
