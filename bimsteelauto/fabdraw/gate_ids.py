"""
G2 - the data gate. The model must carry every value a fabrication drawing
needs, so that nothing downstream has to guess.

Two layers of rule, reported in one format:

* ``fabrication_ready.ids`` (IDS 1.0, approved 2026-10-26), run with
  IfcTester: FR-01 .. FR-06.
* Five rules IDS 1.0 cannot express, checked here (S1-S3 approved the same
  day; S4 and S5 approved 2026-10-26, each after a blind spot was found):

  S1  every IfcElementAssembly has EXACTLY ONE part with
      ``BSA_Fabrication.IsMainPart = True`` - IDS cannot count.
  S2  every IfcMechanicalFastener has parts it connects (a realizing
      connection, or an assembly with parts, or - ANCHORBOLT only, user
      2026-10-26 - the part whose hole it passes through) - G1's fastener
      exclusion depends on it, and IDS has no facet for
      IfcRelConnectsWithRealizingElements.
  S3  no hole or cut is subtracted straight into a part's body: a body
      ``IfcBooleanResult`` DIFFERENCE whose second operand is not a
      half-space. Without S3, FR-06 ("every opening voids a part") passes
      on a model that has holes but no openings - which is exactly what
      both of our current exporters write. A half-space clip is a plane
      end cut (a fitting), not a hole, and is allowed.
  S4  every part body is PARAMETRIC: its ``Body`` representation is built
      only from extrusions (``IfcExtrudedAreaSolid`` and its tapered
      subtype), combined by booleans or mapped items, with half-spaces
      allowed as clip operands. A mesh (faceted brep, triangulated or
      polygonal face set, ...) is refused. S3 cannot see a hole baked
      into a mesh - the FreeCAD export is all 3-point loops with no inner
      bounds - and extract.py needs a profile and a length to read, not
      triangles. Deliberately strict: a swept-disk or revolved solid also
      fails until someone decides it should not.
  S5  every ROUND hole opening in a part has a fastener or a rod through
      it (its solid overlaps the opening's volume). Without S5 a model
      whose bolts were never placed passes every other rule: JOB-018
      exported 892 of 1012 bolts short and passed G2. A rod is an
      IfcMember whose body is only circular extrusions. One finding per
      part, listing its empty holes. Non-round openings (copes, cuts) are
      not holes and are not checked.

"Exactly one assembly per part" needs no rule of its own: FR-03 requires
the part to be aggregated in an IfcElementAssembly, and the IFC schema
allows an object to be aggregated only once.

The gate passes only at 100 %. Each failure names the element by GlobalId
and mark. A model in a schema other than IFC4 is a BLOCKER: the IDS is
written for IFC4 and would otherwise silently not apply.
"""

from __future__ import annotations

import collections
from pathlib import Path

import ifcopenshell

from . import ifcmodel
from .core import Finding, GateResult, write_report

__all__ = ["IDS_PATH", "REQUIRED_SCHEMA", "run_ids_gate", "baked_cuts", "non_parametric_items"]

IDS_PATH = Path(__file__).with_name("fabrication_ready.ids")
REQUIRED_SCHEMA = "IFC4"


def _rule_id(spec) -> str:
    # ifctester 0.8.5 does not read @identifier back from the file, so the
    # rule id is the first word of the spec name ("FR-03 part is ...").
    return getattr(spec, "identifier", None) or spec.name.split()[0]


def _items(item):
    """Yield a representation item and everything it is built from."""
    yield item
    if item.is_a("IfcBooleanResult"):
        yield from _items(item.FirstOperand)
        yield from _items(item.SecondOperand)
    elif item.is_a("IfcMappedItem"):
        for sub in item.MappingSource.MappedRepresentation.Items:
            yield from _items(sub)


def baked_cuts(part) -> int:
    """Number of non-half-space DIFFERENCE operations in a part's shape."""
    n = 0
    rep = part.Representation
    for r in (rep.Representations if rep else ()):
        for top in r.Items:
            for it in _items(top):
                if (it.is_a("IfcBooleanResult") and it.Operator == "DIFFERENCE"
                        and not it.SecondOperand.is_a("IfcHalfSpaceSolid")):
                    n += 1
    return n


#: Containers S4 looks THROUGH, and the only leaves it accepts. The half-space
#: is a leaf only as a clip operand, which is the only place IFC puts one.
_S4_CONTAINERS = ("IfcBooleanResult", "IfcMappedItem")
_S4_LEAVES = ("IfcExtrudedAreaSolid", "IfcHalfSpaceSolid")


def non_parametric_items(part) -> list[str]:
    """Classes of the leaves of a part's ``Body`` that are not extrusions.

    Empty means parametric. A part with no Body representation at all
    returns ``["no Body representation"]`` - nothing to read is not a pass.
    """
    rep = part.Representation
    bodies = [r for r in (rep.Representations if rep else ()) if r.RepresentationIdentifier == "Body"]
    if not bodies:
        return ["no Body representation"]
    bad = []
    for r in bodies:
        for top in r.Items:
            for it in _items(top):
                if not any(it.is_a(c) for c in _S4_CONTAINERS + _S4_LEAVES):
                    bad.append(it.is_a())
    return bad


def _s_rules(model) -> list[Finding]:
    out: list[Finding] = []
    for asm in model.by_type("IfcElementAssembly"):
        mains = [p for p in ifcmodel.assembly_parts(asm) if ifcmodel.pset_value(p, "IsMainPart") is True]
        if len(mains) != 1:
            gids = (asm.GlobalId,) + tuple(p.GlobalId for p in mains)
            marks = (ifcmodel.mark_of(asm),) + tuple(ifcmodel.mark_of(p) for p in mains)
            out.append(Finding("S1", "hard", f"assembly has {len(mains)} main parts, needs exactly 1", gids, marks))
    connected = ifcmodel.connected_parts_map(model)
    for fas in model.by_type("IfcMechanicalFastener"):
        if not connected[fas.GlobalId][0]:
            out.append(Finding("S2", "hard",
                               "fastener connects no part: no IfcRelConnectsWithRealizingElements, not in an "
                               "assembly with parts" + (", and passes through no part's hole"
                                                        if fas.PredefinedType == "ANCHORBOLT" else ""),
                               (fas.GlobalId,), (fas.Name or "",)))
    for p in ifcmodel.parts(model):
        n = baked_cuts(p)
        if n:
            out.append(Finding("S3", "hard",
                               f"{n} hole/cut subtracted into the body; it must be an IfcOpeningElement voiding the "
                               "part", (p.GlobalId,), (ifcmodel.mark_of(p),), {"n_cuts": n}))
        bad = non_parametric_items(p)
        if bad:
            kinds = sorted(set(bad))
            out.append(Finding("S4", "hard",
                               f"body is not parametric ({', '.join(kinds)}); it must be built from extrusions",
                               (p.GlobalId,), (ifcmodel.mark_of(p),), {"items": kinds, "n_items": len(bad)}))
    n_round = collections.Counter(o.VoidsElements[0].RelatingBuildingElement.id()
                                  for o in ifcmodel.round_hole_openings(model))
    empty: dict[int, list] = {}
    for o in ifcmodel.empty_round_holes(model):
        empty.setdefault(o.VoidsElements[0].RelatingBuildingElement.id(), []).append(o)
    for host_id, ops in empty.items():
        host = model.by_id(host_id)
        n_all = n_round[host_id]
        out.append(Finding("S5", "hard",
                           f"{len(ops)} of {n_all} round holes have no fastener or rod through them",
                           (host.GlobalId,), (ifcmodel.mark_of(host),),
                           {"n_empty": len(ops), "openings": [o.GlobalId for o in ops]}))
    return out


def run_ids_gate(ifc_path, out_dir=None, *, ids_path=IDS_PATH) -> GateResult:
    """G2. Writes ``ids_report.json/.html`` to ``out_dir``."""
    from ifctester import ids

    ifc_path = str(ifc_path)
    model = ifcopenshell.open(ifc_path)
    findings: list[Finding] = []
    blockers: list[str] = []
    summary: dict = {"schema": model.schema}
    if model.schema != REQUIRED_SCHEMA:
        blockers.append(f"the model is {model.schema}; {Path(ids_path).name} is written for {REQUIRED_SCHEMA} "
                        "and does not apply to it")

    spec_set = ids.open(str(ids_path), validate=True)
    spec_set.validate(model)
    for sp in spec_set.specifications:
        rule = _rule_id(sp)
        n_app = len(sp.applicable_entities)
        n_fail = len(sp.failed_entities)
        summary[rule] = f"{'PASS' if sp.status else 'FAIL'}  {n_app - n_fail}/{n_app} pass"
        if sp.minOccurs != 0 and not n_app:
            findings.append(Finding(rule, "hard", f"{sp.name}: the model has no applicable element, and at "
                                                  "least one is required"))
        for facet in sp.requirements:
            for fail in facet.failures:
                el = fail["element"]
                findings.append(Finding(
                    rule, "hard", f"{sp.name}: {facet.to_string('requirement', sp, facet)} - {fail['reason']}",
                    (el.GlobalId,), (ifcmodel.mark_of(el),)))

    s = _s_rules(model)
    findings += s
    for r in ("S1", "S2", "S3", "S4", "S5"):
        summary[r] = "PASS" if not any(f.rule == r for f in s) else f"FAIL  {sum(f.rule == r for f in s)} findings"

    result = GateResult(gate="G2 data", ifc_path=ifc_path, findings=tuple(findings), blockers=tuple(blockers),
                        summary=summary,
                        citation=f"IDS 1.0 {Path(ids_path).name} via IfcTester, plus S1-S5 (gate_ids.py)")
    if out_dir is not None:
        write_report(result, out_dir, "ids_report")
    return result
