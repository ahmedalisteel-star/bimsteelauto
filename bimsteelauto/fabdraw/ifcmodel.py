"""
Small, shared readers of the IFC model: marks, the fabrication Pset, which
parts a fastener connects. Used by every gate so that "what is a part" and
"what is its mark" have exactly one definition.
"""

from __future__ import annotations

import ifcopenshell
import ifcopenshell.util.element as ue

__all__ = ["PART_CLASSES", "PSET", "is_part", "parts", "pset_value", "mark_of",
           "assembly_parts", "fastener_connected_parts", "anchor_hole_parts", "connected_parts_map",
           "round_hole_openings", "rods", "empty_round_holes"]

#: What the pipeline calls a PART. The IDS and the clash sets use the same list.
PART_CLASSES = ("IfcBeam", "IfcColumn", "IfcMember", "IfcPlate")

#: The fabrication property set the IDS requires (approved 2026-10-26). No
#: standard IFC4 Pset carries a profile designation or a part mark.
PSET = "BSA_Fabrication"


def is_part(el) -> bool:
    return any(el.is_a(c) for c in PART_CLASSES)


def parts(model: ifcopenshell.file) -> list:
    # by_type includes subtypes (IfcBeamStandardCase ...); the four classes
    # are disjoint, so de-duplicating by id is all that is needed
    seen, out = set(), []
    for c in PART_CLASSES:
        for e in model.by_type(c):
            if e.id() not in seen:
                seen.add(e.id())
                out.append(e)
    return out


def pset_value(el, name: str, pset: str = PSET):
    """A property from the fabrication Pset, or ``None`` - never a default."""
    return (ue.get_psets(el).get(pset) or {}).get(name)


def mark_of(el) -> str:
    """The fabrication mark if there is one, else ``""``.

    Deliberately NOT falling back to ``Name``: a report that printed the
    name where the mark should be would hide exactly the missing-mark
    failure G2 exists to catch. Callers that want something to show print
    the GlobalId beside it.
    """
    key = "AssemblyMark" if el.is_a("IfcElementAssembly") else "PartMark"
    v = pset_value(el, key)
    return str(v) if v not in (None, "") else ""


def assembly_parts(asm) -> list:
    """The parts (not fasteners, not sub-assemblies) directly in an assembly."""
    return [e for e in ue.get_decomposition(asm, is_recursive=False) if is_part(e)]


def fastener_connected_parts(fastener) -> tuple[set[str], str]:
    """GlobalIds of the parts a fastener connects, and where that came from.

    1. ``IfcRelConnectsWithRealizingElements`` it realises: the relating
       and related element of each.
    2. Else the parts of the ``IfcElementAssembly`` it is aggregated in.

    Returns ``(set(), "")`` when neither exists. Needs no geometry; the
    gates use :func:`connected_parts_map`, which adds the anchor-bolt rule.
    """
    gids: set[str] = set()
    # IFC4 inverse of IfcRelConnectsWithRealizingElements.RealizingElements
    for rel in getattr(fastener, "IsConnectionRealization", None) or ():
        for end in (rel.RelatingElement, rel.RelatedElement):
            if end is not None and is_part(end):
                gids.add(end.GlobalId)
    if gids:
        return gids, "IfcRelConnectsWithRealizingElements"
    asm = ue.get_aggregate(fastener)
    if asm is not None and asm.is_a("IfcElementAssembly"):
        gids = {p.GlobalId for p in assembly_parts(asm)}
        if gids:
            return gids, f"assembly {mark_of(asm) or asm.GlobalId}"
    return set(), ""


def _overlapping(model: ifcopenshell.file, a: list, b: list) -> list[tuple]:
    """``(a element, b element)`` pairs whose solids overlap in volume.

    The same ifcopenshell.geom tree G1 uses, at tolerance 0: faces that only
    touch return nothing (measured in G1), overlapping volume does.
    """
    import multiprocessing

    import ifcopenshell.geom

    if not a or not b:
        return []
    tree = ifcopenshell.geom.tree()
    it = ifcopenshell.geom.iterator(ifcopenshell.geom.settings(), model, multiprocessing.cpu_count(),
                                    include=list({e.id(): e for e in a + b}.values()))
    if not it.initialize():
        return []
    while True:
        tree.add_element(it.get())
        if not it.next():
            break
    a_ids = {e.id() for e in a}
    out = []
    for r in tree.clash_intersection_many(a, b, tolerance=0.0, check_all=False):
        x, y = model.by_id(r.a.id()), model.by_id(r.b.id())
        if x.id() not in a_ids:
            x, y = y, x
        out.append((x, y))
    return out


def anchor_hole_parts(model: ifcopenshell.file) -> dict[str, set[str]]:
    """For each ANCHORBOLT: the parts whose hole openings its solid passes through.

    An anchor bolt connects a base plate to a foundation that is not in the
    model, so it has no realizing connection with TWO elements and no shop
    assembly. What ties it to the steel is geometry: it goes through the
    base plate's holes (user, 2026-10-26, decision 6).
    """
    anchors = [e for e in model.by_type("IfcMechanicalFastener") if e.PredefinedType == "ANCHORBOLT"]
    openings = [o for o in model.by_type("IfcOpeningElement") if o.VoidsElements]
    out: dict[str, set[str]] = {}
    for a, o in _overlapping(model, anchors, openings):
        host = o.VoidsElements[0].RelatingBuildingElement
        if is_part(host):
            out.setdefault(a.GlobalId, set()).add(host.GlobalId)
    return out


def _all_circular_extrusions(el) -> bool:
    rep = el.Representation
    items = [i for r in (rep.Representations if rep else ()) if r.RepresentationIdentifier == "Body" for i in r.Items]
    return bool(items) and all(i.is_a("IfcExtrudedAreaSolid") and i.SweptArea.is_a("IfcCircleProfileDef")
                               for i in items)


def round_hole_openings(model: ifcopenshell.file) -> list:
    """Openings that void a PART and are round holes (circular extrusions)."""
    return [o for o in model.by_type("IfcOpeningElement")
            if o.VoidsElements and is_part(o.VoidsElements[0].RelatingBuildingElement)
            and _all_circular_extrusions(o)]


def rods(model: ifcopenshell.file) -> list:
    """Linear parts whose body is only circular extrusions: rods, sag rods, round bars."""
    return [e for e in parts(model) if e.is_a("IfcMember") and _all_circular_extrusions(e)]


def empty_round_holes(model: ifcopenshell.file) -> list:
    """Round hole openings that no fastener and no rod passes through (rule S5)."""
    holes = round_hole_openings(model)
    filled = {o.id() for o, _ in _overlapping(model, holes, list(model.by_type("IfcMechanicalFastener")) + rods(model))}
    return [o for o in holes if o.id() not in filled]


def connected_parts_map(model: ifcopenshell.file) -> dict[str, tuple[set[str], str]]:
    """``{fastener GlobalId: (connected part GlobalIds, source)}`` for every
    IfcMechanicalFastener: realizing connection, else assembly, else - for an
    ANCHORBOLT only - the parts whose holes it passes through."""
    holes = None
    out = {}
    for fas in model.by_type("IfcMechanicalFastener"):
        gids, src = fastener_connected_parts(fas)
        if not gids and fas.PredefinedType == "ANCHORBOLT":
            if holes is None:
                holes = anchor_hole_parts(model)
            gids = holes.get(fas.GlobalId, set())
            src = "passes through a hole of the part" if gids else ""
        out[fas.GlobalId] = (gids, src)
    return out
