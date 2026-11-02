"""
IFC fixtures for the fabdraw gates, built with ifcopenshell so that every
dimension is known BY CONSTRUCTION.

No file here is exported from anywhere else. A fixture that came out of
Tekla or FreeCAD would carry that tool's rounding and its idea of a part
mark, and a test on it would prove the gate agrees with the tool, not with
the geometry.

The model (all mm, file units mm)::

    beam  B1/b1   welded I 300 x 150 x 7.1 x 10.7, L = 4000, along +X
                  origin at the start end, section centred on the X axis
    plate p1 x2   180 (Y) x 340 (Z) x 20, one on each end face,
                  touching the beam end - a TOUCHING face, never a clash
    holes 4/plate d 22 at Y = +/-50, Z = +/-115, IfcOpeningElement each
    bolts 4/plate M20 x 50, shank d 20 through the d 22 hole, head d 30 x 13
                  on the beam side, touching the plate face
    cope          top flange at the far end, 120 long x 60 deep, an
                  IfcOpeningElement voiding the beam

Every part carries ``BSA_Fabrication`` (PartMark, Profile or Thickness,
IsMainPart) and a material; the assembly carries ``AssemblyMark``; the
bolts are aggregated in the assembly, which is how G1 knows which parts
they connect.
"""

from __future__ import annotations

from dataclasses import dataclass

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid
import ifcopenshell.util.element

PSET = "BSA_Fabrication"


@dataclass(frozen=True)
class BeamAssemblyDims:
    """Every dimension of the fixture, in mm. Tests read these back."""

    length: float = 4000.0
    depth: float = 300.0
    width: float = 150.0
    tw: float = 7.1
    tf: float = 10.7
    plate_t: float = 20.0
    plate_w: float = 180.0
    plate_h: float = 340.0
    hole_d: float = 22.0
    hole_y: float = 50.0          # +/- from the web
    hole_z: float = 115.0         # +/- from the section centre
    bolt_d: float = 20.0
    bolt_len: float = 50.0
    head_d: float = 30.0
    head_h: float = 13.0
    cope_len: float = 120.0
    cope_depth: float = 60.0

    @property
    def holes(self) -> list[tuple[float, float]]:
        return [(y, z) for z in (self.hole_z, -self.hole_z)
                for y in (-self.hole_y, self.hole_y)]


DIMS = BeamAssemblyDims()

#: Spec test 2's plate shift, mm. The spec said 5 mm; CLASH_TOLERANCE_MM
#: became 5 mm (user, 2026-10-26) and a 5.000 mm overlap is not MORE than
#: 5, so the shift went to 10 mm (user asked for it to be bumped). Twice
#: the tolerance, and still inside the 13 mm bolt head, so the own-bolt
#: exclusion is still exercised.
TEST2_SHIFT_MM = 10.0


class _Model:
    """A minimal IFC4 project with mm units and one storey."""

    def __init__(self):
        f = self.f = ifcopenshell.file(schema="IFC4")
        self.project = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name="fabdraw fixture")
        units = f.createIfcUnitAssignment([
            f.createIfcSIUnit(None, "LENGTHUNIT", "MILLI", "METRE"),
            f.createIfcSIUnit(None, "AREAUNIT", None, "SQUARE_METRE"),
            f.createIfcSIUnit(None, "VOLUMEUNIT", None, "CUBIC_METRE"),
            f.createIfcSIUnit(None, "PLANEANGLEUNIT", None, "RADIAN"),
        ])
        self.project.UnitsInContext = units
        model = ifcopenshell.api.run("context.add_context", f, context_type="Model")
        self.body = ifcopenshell.api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                                         target_view="MODEL_VIEW", parent=model)
        site = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
        bld = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
        self.storey = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Steel")
        ifcopenshell.api.run("aggregate.assign_object", f, products=[site], relating_object=self.project)
        ifcopenshell.api.run("aggregate.assign_object", f, products=[bld], relating_object=site)
        ifcopenshell.api.run("aggregate.assign_object", f, products=[self.storey], relating_object=bld)
        self.materials: dict[str, ifcopenshell.entity_instance] = {}

    # ------------------------------------------------------------ geometry
    def placement(self, origin, z=(0.0, 0.0, 1.0), x=(1.0, 0.0, 0.0), rel=None):
        f = self.f
        ax = f.createIfcAxis2Placement3D(
            f.createIfcCartesianPoint([float(v) for v in origin]),
            f.createIfcDirection([float(v) for v in z]),
            f.createIfcDirection([float(v) for v in x]))
        return f.createIfcLocalPlacement(rel, ax)

    def extrusion(self, profile, depth, origin=(0.0, 0.0, 0.0)):
        f = self.f
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([float(v) for v in origin]), None, None)
        return f.createIfcExtrudedAreaSolid(profile, pos, f.createIfcDirection([0.0, 0.0, 1.0]), float(depth))

    def rect(self, x, y, centre=(0.0, 0.0)):
        f = self.f
        pos = f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([float(v) for v in centre]), None)
        return f.createIfcRectangleProfileDef("AREA", None, pos, float(x), float(y))

    def circle(self, d, centre=(0.0, 0.0)):
        f = self.f
        pos = f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([float(v) for v in centre]), None)
        return f.createIfcCircleProfileDef("AREA", None, pos, float(d) / 2.0)

    def shape(self, *items):
        f = self.f
        rep = f.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", list(items))
        return f.createIfcProductDefinitionShape(None, None, [rep])

    # ------------------------------------------------------------ data
    def pset(self, element, name, props: dict):
        f = self.f
        vals = [f.createIfcPropertySingleValue(k, None, v, None) for k, v in props.items()]
        ps = f.createIfcPropertySet(ifcopenshell.guid.new(), None, name, None, vals)
        f.createIfcRelDefinesByProperties(ifcopenshell.guid.new(), None, None, None, [element], ps)
        return ps

    def material(self, element, name):
        if name not in self.materials:
            self.materials[name] = ifcopenshell.api.run("material.add_material", self.f, name=name, category="steel")
        ifcopenshell.api.run("material.assign_material", self.f, products=[element], material=self.materials[name])

    def element(self, ifc_class, name, placement, shape, **attrs):
        el = ifcopenshell.api.run("root.create_entity", self.f, ifc_class=ifc_class, name=name)
        el.ObjectPlacement = placement
        el.Representation = shape
        for k, v in attrs.items():
            setattr(el, k, v)
        return el

    def opening(self, host, placement, shape, name):
        f = self.f
        op = self.element("IfcOpeningElement", name, placement, shape, PredefinedType="OPENING")
        f.createIfcRelVoidsElement(ifcopenshell.guid.new(), None, None, None, host, op)
        return op


def add_beam_assembly(m: _Model, *, origin=(0.0, 0.0, 0.0), assembly_name="B1",
                      dims: BeamAssemblyDims = DIMS, plate_shift_mm: float = 0.0,
                      drop_part_mark: bool = False):
    """Add one beam assembly; return a dict of the entities, for tests to poke.

    ``plate_shift_mm`` moves the START plate along +X, i.e. INTO the beam -
    its holes go with it (they are placed relative to the plate), its bolts
    do not. ``drop_part_mark`` removes PartMark from that same plate.
    """
    f, d = m.f, dims
    ox, oy, oz = origin
    L = d.length

    # ---- beam: profile in the YZ plane, extruded along +X
    prof = f.createIfcIShapeProfileDef("AREA", None, None, d.width, d.depth, d.tw, d.tf, None, None, None)
    beam_pl = m.placement((ox, oy, oz), z=(1, 0, 0), x=(0, 1, 0))
    beam = m.element("IfcBeam", "b1", beam_pl, m.shape(m.extrusion(prof, L)), PredefinedType="BEAM")
    m.pset(beam, PSET, {"PartMark": f.createIfcLabel("b1"),
                        "Profile": f.createIfcLabel("I300x150x7.1x10.7"),
                        "IsMainPart": f.createIfcBoolean(True)})
    m.material(beam, "S275")

    # cope: a box at the far end, top flange, placed in the beam's frame
    # (local x = Y, local y = Z, local z = X along the beam)
    cope_shape = m.shape(m.extrusion(
        m.rect(d.width + 20.0, d.cope_depth + 1.0, centre=(0.0, d.depth / 2 - d.cope_depth / 2 + 0.5)),
        d.cope_len + 1.0, origin=(0.0, 0.0, L - d.cope_len)))
    cope = m.opening(beam, m.placement((0, 0, 0), rel=beam_pl), cope_shape, "cope")

    parts = [beam]
    plates, holes, bolts = [], [], []
    for end, x0 in (("start", -d.plate_t + plate_shift_mm), ("end", L)):
        pl = m.placement((ox + x0, oy, oz), z=(1, 0, 0), x=(0, 1, 0))
        plate = m.element("IfcPlate", "p1", pl, m.shape(m.extrusion(m.rect(d.plate_w, d.plate_h), d.plate_t)),
                          PredefinedType="SHEET")
        props = {"PartMark": f.createIfcLabel("p1"),
                 "Thickness": f.createIfcPositiveLengthMeasure(d.plate_t),
                 "IsMainPart": f.createIfcBoolean(False)}
        if drop_part_mark and end == "start":
            del props["PartMark"]
        m.pset(plate, PSET, props)
        m.material(plate, "S275")
        plates.append(plate)
        parts.append(plate)

        for i, (y, z) in enumerate(d.holes):
            hole_shape = m.shape(m.extrusion(m.circle(d.hole_d, (y, z)), d.plate_t + 2.0, origin=(0, 0, -1.0)))
            holes.append(m.opening(plate, m.placement((0, 0, 0), rel=pl), hole_shape, f"hole-{end}-{i + 1}"))

            # bolt: head on the beam side, shank out through the plate
            # (the head sits on the beam end face, x = 0 or x = L)
            face, axis = (0.0, (-1, 0, 0)) if end == "start" else (L, (1, 0, 0))
            bolt_pl = m.placement((ox + face, oy + y, oz + z), z=axis, x=(0, 1, 0))
            shank = m.extrusion(m.circle(d.bolt_d), d.bolt_len)
            head = m.extrusion(m.circle(d.head_d), d.head_h, origin=(0, 0, -d.head_h))
            bolt = m.element("IfcMechanicalFastener", "M20", bolt_pl, m.shape(shank, head),
                             PredefinedType="BOLT", NominalDiameter=d.bolt_d, NominalLength=d.bolt_len)
            m.material(bolt, "8.8")
            bolts.append(bolt)

    asm = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcElementAssembly", name=assembly_name)
    asm.ObjectPlacement = m.placement((ox, oy, oz))
    asm.AssemblyPlace = "FACTORY"
    asm.PredefinedType = "GIRDER"
    m.pset(asm, PSET, {"AssemblyMark": f.createIfcLabel(assembly_name)})
    ifcopenshell.api.run("aggregate.assign_object", f, products=parts + bolts, relating_object=asm)
    ifcopenshell.api.run("spatial.assign_container", f, products=[asm], relating_structure=m.storey)
    return {"assembly": asm, "beam": beam, "plates": plates, "holes": holes, "bolts": bolts, "cope": cope}


def beam_assembly_model(path=None, **kw):
    """One beam assembly in its own file. Returns (file, entities)."""
    m = _Model()
    ents = add_beam_assembly(m, **kw)
    if path is not None:
        m.f.write(str(path))
    return m.f, ents


def two_assembly_model(path=None, *, origin2=(0.0, 0.0, 0.0), name2="B2"):
    """Two copies of the beam assembly; the second at ``origin2``. Returns (file, [ents1, ents2])."""
    m = _Model()
    e1 = add_beam_assembly(m)
    e2 = add_beam_assembly(m, origin=origin2, assembly_name=name2)
    if path is not None:
        m.f.write(str(path))
    return m.f, [e1, e2]


# ---------------------------------------------------------------- mutations
# Each breaks exactly one thing, so a test that expects a gate to fail can
# check it fails for THAT reason.

def _pset(el, name=PSET):
    for rel in el.IsDefinedBy or ():
        if rel.is_a("IfcRelDefinesByProperties") and rel.RelatingPropertyDefinition.Name == name:
            return rel.RelatingPropertyDefinition
    raise KeyError(f"{el} has no {name}")


def remove_property(el, prop: str) -> None:
    ps = _pset(el)
    ps.HasProperties = tuple(p for p in ps.HasProperties if p.Name != prop)


def set_main_part(f, el, value: bool) -> None:
    for p in _pset(el).HasProperties:
        if p.Name == "IsMainPart":
            p.NominalValue = f.createIfcBoolean(value)


def remove_material(f, el) -> None:
    for rel in list(el.HasAssociations or ()):
        if rel.is_a("IfcRelAssociatesMaterial"):
            ifcopenshell.api.run("material.unassign_material", f, products=[el])


def take_out_of_assembly(f, el) -> None:
    ifcopenshell.api.run("aggregate.unassign_object", f, products=[el])


def bake_hole_into_body(f, el, d=22.0, half_space=False) -> None:
    """Replace the body by ``body - cylinder`` (a baked hole), or by a
    half-space clip (an end cut, which S3 must allow)."""
    rep = el.Representation.Representations[0]
    body = rep.Items[0]
    if half_space:
        plane = f.createIfcPlane(f.createIfcAxis2Placement3D(
            f.createIfcCartesianPoint([0.0, 0.0, 10.0]), f.createIfcDirection([0.0, 0.0, 1.0]), None))
        cut = f.createIfcBooleanClippingResult("DIFFERENCE", body, f.createIfcHalfSpaceSolid(plane, False))
    else:
        pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint([0.0, 0.0, -1.0]), None, None)
        prof = f.createIfcCircleProfileDef("AREA", None, None, d / 2.0)
        cyl = f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection([0.0, 0.0, 1.0]), 100.0)
        cut = f.createIfcBooleanResult("DIFFERENCE", body, cyl)
    rep.Items = [cut]


def _box_points(f, x=180.0, y=340.0, z=20.0):
    c = [(0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0), (0, 0, z), (x, 0, z), (x, y, z), (0, y, z)]
    return c, [f.createIfcCartesianPoint([float(v) for v in p]) for p in c]


def mesh_body(f, el, kind="faceted", inside_boolean=False) -> None:
    """Replace a part's body by a mesh box: ``faceted`` (IfcFacetedBrep, what
    the FreeCAD export writes) or ``triangulated`` (IfcTriangulatedFaceSet).
    ``inside_boolean`` keeps the extrusion and UNIONs the mesh onto it, so
    the mesh is one level down, where S4 has to look for it."""
    rep = el.Representation.Representations[0]
    if kind == "faceted":
        _, pts = _box_points(f)
        quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        faces = [f.createIfcFace([f.createIfcFaceOuterBound(f.createIfcPolyLoop([pts[i] for i in q]), True)])
                 for q in quads]
        mesh = f.createIfcFacetedBrep(f.createIfcClosedShell(faces))
    else:
        c, _ = _box_points(f)
        pl = f.createIfcCartesianPointList3D([[float(v) for v in p] for p in c])
        tris = [(1, 4, 3), (1, 3, 2), (5, 6, 7), (5, 7, 8), (1, 2, 6), (1, 6, 5),
                (2, 3, 7), (2, 7, 6), (3, 4, 8), (3, 8, 7), (4, 1, 5), (4, 5, 8)]
        mesh = f.createIfcTriangulatedFaceSet(pl, None, True, tris, None)
    if inside_boolean:
        rep.Items = [f.createIfcBooleanResult("UNION", rep.Items[0], mesh)]
    else:
        rep.Items = [mesh]
        rep.RepresentationType = "Brep" if kind == "faceted" else "Tessellation"


def remove_bolt(f, bolt) -> None:
    ifcopenshell.api.run("root.remove_product", f, product=bolt)


def replace_bolt_with_rod(f, bolt):
    """Swap a bolt for a round-bar IfcMember of the same solid, fully data'd
    and in the same assembly - a rod through the hole instead of a bolt."""
    asm = ifcopenshell.util.element.get_aggregate(bolt)
    rod = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcMember", name="rod")
    rod.ObjectPlacement, rod.Representation = bolt.ObjectPlacement, bolt.Representation
    vals = [f.createIfcPropertySingleValue("PartMark", None, f.createIfcLabel("r1"), None),
            f.createIfcPropertySingleValue("Profile", None, f.createIfcLabel("RD20"), None),
            f.createIfcPropertySingleValue("IsMainPart", None, f.createIfcBoolean(False), None)]
    ps = f.createIfcPropertySet(ifcopenshell.guid.new(), None, PSET, None, vals)
    f.createIfcRelDefinesByProperties(ifcopenshell.guid.new(), None, None, None, [rod], ps)
    mat = ifcopenshell.util.element.get_material(bolt)
    ifcopenshell.api.run("material.assign_material", f, products=[rod], material=mat)
    bolt.Representation = bolt.ObjectPlacement = None      # the rod owns them now
    ifcopenshell.api.run("root.remove_product", f, product=bolt)
    ifcopenshell.api.run("aggregate.assign_object", f, products=[rod], relating_object=asm)
    return rod


def void_a_wall(f, m_storey=None) -> ifcopenshell.entity_instance:
    """An IfcOpeningElement voiding an IfcWall - not a steel part (FR-06)."""
    wall = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcWall", name="wall")
    op = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcOpeningElement", name="wall-hole")
    f.createIfcRelVoidsElement(ifcopenshell.guid.new(), None, None, None, wall, op)
    return op
