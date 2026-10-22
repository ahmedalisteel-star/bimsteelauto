"""
bimsteelauto_fc.builder
=======================

``DesignResult``  ->  a FreeCAD document full of real solids.

What this builds
----------------
Every primary frame member on every frame line, as a lofted tapered
I-section solid; the purlins, girts and eave struts running along the
building; and the bracing diagonals in the braced bays.  Each one becomes an
``Arch`` Structure carrying its mark, member type, profile, length, weight
and utilisation as editable properties, and an ``IfcType`` so it exports as
the right IFC class.

Each piece is placed where it is BUILT, not on its analysis line: columns
outer flange on the grid with a constant-depth knee zone, rafters starting
at the end plate face, purlins standing on their cleats, girts and eave
struts outside the columns on clips, rods stopping at the web faces. The
conventions and their reasons are in :mod:`bimsteelauto_fc.fabrication`.
A centreline model - every solid on its analysis line - is the right
picture of the ANALYSIS and the wrong one of the building: on REF-2026-001
it had 652 hard clashes.

What this does NOT build - and why that is a scope line, not a bug
------------------------------------------------------------------
Shop detailing: bolt holes, haunch cuts, cleats, stiffeners, the real end
plate outline.  Those live in the ``peb_building_param_v8`` macro, which has
ten thousand lines of GUI-verified detailing geometry, and merging it is a
separate job.  What is built here is the BIM/analytical model - the thing
that carries the design, exports to IFC, and drives the schedule and the
drawings.  See :data:`COVERAGE_GAPS`.

Coordinates
-----------
::

    X  across the frame (the span)
    Y  along the building (the length)
    Z  up

The engine's 2D frame lives in its own ``(x, y)`` plane with ``y`` up, so a
layout node ``(x, y)`` becomes ``(x, Y_frame, y)``.  That mapping happens in
exactly one place, :func:`_node_xyz`, so it cannot drift.

Where the geometry comes from
-----------------------------
``DesignResult`` carries each member's canonical mark and length, and
``bimsteelauto.sections.parse_mark`` turns those back into the real
``TaperedMember``.  Node positions come from
``bimsteelauto.analysis.build_layout``, which is pure geometry - no solver,
no design - so calling it re-uses the verified layout rather than
re-deriving node coordinates here and getting them subtly different from the
ones that were analysed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import FreeCAD as App
import Part

from bimsteelauto.analysis import (
    FrameSections,
    MemberSectionSpec,
    build_layout,
)
from bimsteelauto.sections import SectionError, TaperedMember, WeldedI, parse_mark
from bimsteelauto.spec import ConnectionType, DesignResult, MemberType

__all__ = [
    "COVERAGE_GAPS",
    "DETAIL_IFC_FOR",
    "BuildError",
    "BuildReport",
    "build_model",
    "build_from_json",
    "load_result",
    "frame_line_positions",
    "i_section_wire",
    "member_solid",
]

#: What the model does not contain.  Printed by the caller, and mirrored on
#: the calculation sheet's coverage list.
COVERAGE_GAPS: tuple[str, ...] = (
    "Shop detailing is PARTIAL. End plates, base plates with anchors, "
    "purlin cleats and bracing gussets are built as real drilled plates "
    "from the layout on the DesignResult (schema 1.2.0). Not built: haunch "
    "cuts, stiffeners, welds, and the matching holes in the member the "
    "plate bolts to - so a plate and its member are not yet a fabricable "
    "pair. See bimsteelauto_fc.detailing.COVERAGE_GAPS for the rest.",
    "A result written before schema 1.2.0 carries no shop geometry at all. "
    "Its connections are on the calculation sheet and there is nothing to "
    "build, which is reported rather than filled in with a guessed plate.",
    "Cold-formed purlins and girts are modelled from their catalogue NAME "
    "(the C/Z HxBxDxt pattern), as the real lipped profile, because "
    "DesignResult carries the section as a name rather than as dimensions. "
    "A name that does not parse is SKIPPED and reported in "
    "BuildReport.skipped - never guessed at.",
    "Pieces are placed where they are built, not on the analysis lines, so "
    "the model is clash-free; the WEIGHTS are still the design's, taken on "
    "the analysis lengths, while Length_mm is the built length. Each "
    "purlin, girt and eave strut is one full-length run - laps and splices "
    "are not drawn. Girt and eave-strut clips reuse the purlin cleat plate, "
    "and a threaded-rod brace is built on a rod plate sized to the rod by "
    "set-out rule (no limit state ran on it) instead of the engine's bolted "
    "gusset, which a rod brace does not have.",
    "Endwall framing (posts, endwall girts, gable columns) is not built. "
    "The engine designs a transverse frame and the longitudinal system; the "
    "endwall is its own frame and is out of the current scope.",
)


class BuildError(RuntimeError):
    """The model could not be built from this result."""


def load_result(path) -> DesignResult:
    """Read a ``DesignResult`` back from the JSON the engine wrote.

    This is the real layer boundary, and it is worth being literal about
    it: the engine runs wherever its dependencies are (PyNite, pyaisc360,
    the cfs library), writes JSON, and the adapter reads that JSON inside
    FreeCAD - which has none of those installed and does not need them.

    Everything the adapter imports from the engine is pure Python:
    ``spec``, ``sections`` and ``analysis.build_layout``. Nothing here
    imports a solver.
    """
    import json
    import pathlib

    text = pathlib.Path(path).read_text(encoding="utf-8")
    return DesignResult.from_dict(json.loads(text))


def build_from_json(path, doc=None, **kw) -> "BuildReport":
    """``load_result`` then :func:`build_model`, which is the usual call."""
    return build_model(load_result(path), doc, **kw)


@dataclass
class BuildReport:
    """What was built, and what was not.

    ``skipped`` is not a detail.  A member that silently failed to appear
    would leave a model that looks complete and weighs less than the design
    - so anything not built is named, with the reason.
    """

    document: object = None
    objects: list = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def n_built(self) -> int:
        return len(self.objects)

    @property
    def steel_kg(self) -> float:
        return sum(
            getattr(o, "Weight_kg", 0.0) or 0.0 for o in self.objects
        )

    def summary(self) -> str:
        lines = [
            f"built {self.n_built} objects, {self.steel_kg:,.1f} kg",
        ]
        for mark, why in self.skipped:
            lines.append(f"  SKIPPED {mark}: {why}")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------


def i_section_wire(
    section: WeldedI,
    origin: App.Vector,
    u_dir: App.Vector,
    v_dir: App.Vector,
) -> Part.Wire:
    """The closed outline of one welded I, as a wire in 3D.

    ``u_dir`` runs across the flange width, ``v_dir`` up the depth, and
    ``origin`` is the section's centroid-ish mid-depth point on the member
    axis.  Twelve points, counter-clockwise, starting at the bottom left.

    Singly-symmetric sections are handled: the two flanges may differ in
    width and thickness, which is common on a PEB rafter.
    """
    d, tw = section.d, section.tw
    bt, tt = section.bf_top, section.tf_top
    bb, tb = section.bf_bot, section.tf_bot
    hd = d / 2.0

    pts2d = [
        (-bb / 2.0, -hd),
        (+bb / 2.0, -hd),
        (+bb / 2.0, -hd + tb),
        (+tw / 2.0, -hd + tb),
        (+tw / 2.0, +hd - tt),
        (+bt / 2.0, +hd - tt),
        (+bt / 2.0, +hd),
        (-bt / 2.0, +hd),
        (-bt / 2.0, +hd - tt),
        (-tw / 2.0, +hd - tt),
        (-tw / 2.0, -hd + tb),
        (-bb / 2.0, -hd + tb),
    ]
    # App.Vector arithmetic only - never ``.multiply()``, which mutates the
    # vector it is called on and would corrupt u_dir/v_dir for every point
    # after the first.
    pts = [origin + App.Vector(u_dir) * u + App.Vector(v_dir) * v
           for u, v in pts2d]
    pts.append(pts[0])
    return Part.makePolygon(pts)


def _axes(p1: App.Vector, p2: App.Vector) -> tuple[App.Vector, App.Vector, App.Vector]:
    """``(axis, u_dir, v_dir)`` for a member running p1 -> p2.

    ``u`` is the flange-width direction and ``v`` the depth direction.

    A PEB has members running two ways and they need different bases:

    * **In the frame's plane** - columns and rafters.  Their webs are IN
      that plane, so the flange width runs along the building and
      ``u = global Y``.
    * **Along the building** - purlins, girts and the eave strut.  Global Y
      is now the member's own axis and cannot also be its flange direction,
      so ``u`` falls back to global X.

    Getting this wrong does not raise - it silently rotates a member 90
    degrees about its own axis, which looks almost right in a viewport and
    is completely wrong in a fabrication drawing.  So the fallback is
    explicit rather than a try/except.

    ``v`` is then fixed by the cross product and flipped, where it can be,
    to point upward - so "the top of the beam is the top", the same
    convention the analysis layer uses.
    """
    axis = p2.sub(p1)
    L = axis.Length
    if L <= 1e-9:
        raise BuildError(f"zero-length member from {p1} to {p2}")
    axis = App.Vector(axis).multiply(1.0 / L)

    u_dir = App.Vector(0, 1, 0)
    if abs(axis.dot(u_dir)) > 0.999:          # runs along the building
        u_dir = App.Vector(1, 0, 0)

    v_dir = axis.cross(u_dir)
    if v_dir.Length < 1e-9:                    # pragma: no cover - guarded above
        raise BuildError(f"cannot orient a member along {axis}")
    v_dir = v_dir.normalize()
    if v_dir.z < -1e-9:
        v_dir = v_dir.multiply(-1.0)
        u_dir = App.Vector(u_dir).multiply(-1.0)
    return axis, App.Vector(u_dir), App.Vector(v_dir)


def member_solid(
    taper: TaperedMember, p1: App.Vector, p2: App.Vector
) -> Part.Shape:
    """A tapered I-section solid between two points.

    Lofted between the start and end outlines.  Only the DEPTH varies along
    a tapered member - the plate sizes are fixed, which is what
    ``TaperedMember`` enforces - so the loft is between two similar
    twelve-sided polygons and is always well behaved.
    """
    axis, u_dir, v_dir = _axes(p1, p2)
    w0 = i_section_wire(taper.start, p1, u_dir, v_dir)
    w1 = i_section_wire(taper.end, p2, u_dir, v_dir)
    solid = Part.makeLoft([w0, w1], True)
    if not solid.isValid():
        solid = solid.removeSplitter()
    if not solid.isValid():
        raise BuildError(f"lofted an invalid solid for {taper.start.mark}")
    return solid


def _box_solid(
    p1: App.Vector, p2: App.Vector, width: float, depth: float
) -> Part.Shape:
    """A prismatic rectangular solid - used for cold-formed members.

    ``width`` is across the building, ``depth`` is in the member's ``v``
    direction.  A C-purlin is modelled as its bounding rectangle: the shape
    is not the real profile, and the schedule takes its weight from the
    DESIGN rather than from this solid, so nothing downstream depends on the
    approximation.
    """
    axis, u_dir, v_dir = _axes(p1, p2)
    pts2d = [
        (-width / 2.0, -depth / 2.0),
        (+width / 2.0, -depth / 2.0),
        (+width / 2.0, +depth / 2.0),
        (-width / 2.0, +depth / 2.0),
    ]
    ring = [origin_pt(p1, u_dir, v_dir, u, v) for u, v in pts2d]
    ring.append(ring[0])
    # Extruding the WIRE gives an open tube - four faces, no end caps, no
    # volume. It looks right in a viewport, exports as a surface rather than
    # a solid, weighs nothing, and makes Arch.makeStructure crawl. Extrude
    # the FACE instead, which caps both ends and gives a real solid.
    solid = Part.Face(Part.makePolygon(ring)).extrude(p2.sub(p1))
    if not solid.Solids:
        raise BuildError("the extruded secondary member is not a solid")
    return solid


def origin_pt(base, u_dir, v_dir, u, v) -> App.Vector:
    """``base`` offset by ``u`` across and ``v`` up, without mutating either
    direction vector - see the note in :func:`i_section_wire`."""
    return base + App.Vector(u_dir) * u + App.Vector(v_dir) * v


#: ``C200x60x20x2.5`` / ``Z250x75x20x3.0`` - depth, flange, lip, thickness.
_CF_NAME = re.compile(
    r"^(?P<shape>[CZ])(?P<H>[\d.]+)x(?P<B>[\d.]+)x(?P<D>[\d.]+)x(?P<t>[\d.]+)$",
    re.IGNORECASE,
)


def _cold_formed_dims(name: str) -> tuple[float, float] | None:
    """``(depth, width)`` in mm from a catalogue name, or ``None``.

    ``None`` means the name did not parse, and the caller must SKIP the
    member and report it.  Guessing a size here would put a member in the
    model that is not the one that was designed.
    """
    m = _CF_NAME.match(name.strip())
    if not m:
        return None
    return float(m.group("H")), float(m.group("B"))


# ---------------------------------------------------------------------------
# Placing the building
# ---------------------------------------------------------------------------


def frame_line_positions(geometry) -> tuple[float, ...]:
    """Y of every transverse frame line, mm, from the left endwall."""
    ys = [0.0]
    for bay in geometry.bay_spacings:
        ys.append(ys[-1] + bay)
    return tuple(ys)


def _node_xyz(node_xy: tuple[float, float], y: float) -> App.Vector:
    """The ONE place the engine's 2D frame maps into the model's 3D.

    Engine ``(x, y)`` is across-the-span and up.  The model is
    ``(X across, Y along, Z up)``.
    """
    return App.Vector(node_xy[0], y, node_xy[1])


def _sections_from_result(result: DesignResult) -> FrameSections:
    """Rebuild the plate sizes from the marks on the DesignResult.

    This is the step that keeps Layer 2 honest: the adapter is handed a
    ``DesignResult`` and nothing else, so the sections have to come back out
    of it rather than being passed in alongside.
    """
    by_type: dict[MemberType, TaperedMember] = {}
    for m in result.members:
        if m.member_type not in (
            MemberType.COLUMN, MemberType.RAFTER, MemberType.INTERIOR_COLUMN
        ):
            continue
        if m.member_type in by_type:
            continue
        try:
            sec = parse_mark(m.section_id, m.length)
        except SectionError as exc:
            raise BuildError(
                f"member {m.mark} carries section_id {m.section_id!r}, which "
                f"is not a canonical built-up mark: {exc}"
            ) from exc
        if isinstance(sec, WeldedI):
            sec = TaperedMember(start=sec, end=sec, length=m.length)
        by_type[m.member_type] = sec

    for needed in (MemberType.COLUMN, MemberType.RAFTER):
        if needed not in by_type:
            raise BuildError(
                f"the result has no {needed.value} to take a section from"
            )

    def spec(t: MemberType) -> MemberSectionSpec:
        tm = by_type[t]
        return MemberSectionSpec(start=tm.start, end=tm.end)

    interior = (
        spec(MemberType.INTERIOR_COLUMN)
        if MemberType.INTERIOR_COLUMN in by_type
        else None
    )
    return FrameSections(
        column=spec(MemberType.COLUMN),
        rafter=spec(MemberType.RAFTER),
        interior_column=interior,
    )


_IFC_FOR = {
    MemberType.COLUMN: "Column",
    MemberType.INTERIOR_COLUMN: "Column",
    MemberType.RAFTER: "Beam",
    MemberType.PURLIN: "Beam",
    MemberType.GIRT: "Beam",
    MemberType.EAVE_STRUT: "Beam",
    MemberType.ROOF_BRACING: "Member",
    MemberType.WALL_BRACING: "Member",
}

#: The two bracing planes.  There is no single ``MemberType.BRACE`` - the
#: engine distinguishes them because they carry different forces and sit in
#: different planes, and the model has to keep them apart too.
_BRACING = (MemberType.ROOF_BRACING, MemberType.WALL_BRACING)


def _structure(doc, shape, name):
    """An Arch Structure that IS the solid, with no base object behind it.

    ``Arch.makeStructure(base)`` keeps the base: a ``Part::Feature`` holding
    the same solid, hidden in the GUI and very much present in the
    document. Every member was in the model TWICE - a clash check over the
    document found each one clashing with its own base, 231 extra hard
    clashes on REF-2026-001 before any real one - and anything that walks
    ``doc.Objects`` (a clash check, a viewer, a take-off script) sees a
    second building.

    So the structure is made without a base and handed the solid. With no
    ``Base`` and a zero ``Length`` the Arch structure has no extrusion to
    rebuild from, and its ``execute`` leaves the shape alone - on recompute
    and on reopening the file alike.
    """
    import Arch

    obj = Arch.makeStructure(None, name=name)
    obj.Length = 0.0
    obj.Shape = shape
    return obj


def _add_member(
    doc, shape, *, name, mark, member_type, profile, length, weight,
    utilisation, group=None,
):
    """One Arch Structure with the properties the whole pipeline needs.

    ``Arch.makeStructure`` is used rather than a bare ``Part::Feature``
    because it is what makes the object an IFC element - ``ifc.py`` exports
    Arch objects and would otherwise see nothing.
    """
    obj = _structure(doc, shape, name)
    obj.Label = mark

    # Checked, not just assigned - see _set_ifc_type. Older/newer FreeCAD
    # spells some of these differently, and a spelling that is not in the
    # enumeration is dropped in silence.
    _set_ifc_type(obj, _IFC_FOR.get(member_type, "Member"))

    # Arch.makeStructure already defines some of these - notably ``Profile``
    # - so adding is guarded and the value is set either way.  A blind
    # addProperty raises and takes the whole build down with it.
    for prop, kind, value in (
        ("Mark", "App::PropertyString", mark),
        ("MemberType", "App::PropertyString", member_type.value),
        ("Profile", "App::PropertyString", profile),
        ("Length_mm", "App::PropertyFloat", float(length)),
        ("Weight_kg", "App::PropertyFloat", float(weight)),
        ("Utilisation", "App::PropertyFloat", float(utilisation)),
    ):
        if prop not in obj.PropertiesList:
            obj.addProperty(kind, prop, "BIMSteelAuto", prop)
        try:
            setattr(obj, prop, value)
        except (TypeError, ValueError):
            # An Arch property of a different type owns the name. Keep the
            # value under our own prefix rather than dropping it, so the
            # schedule still finds it.
            alt = "BSA_" + prop
            if alt not in obj.PropertiesList:
                obj.addProperty(kind, alt, "BIMSteelAuto", prop)
            setattr(obj, alt, value)

    if obj.ViewObject is not None:                      # pragma: no cover
        obj.ViewObject.Proxy = 0                        # or it renders nothing
    if group is not None:
        group.addObject(obj)
    return obj


#: IFC class per connection kind.  A plate is IfcPlate and an anchor is
#: IfcMechanicalFastener - not IfcMember - so a receiving BIM tool files
#: them where a detailer expects to find them.
#:
#: THE SPELLING IS THE ARCH ENUM SPELLING, spaces and all.  Assigning a
#: value that is not in the enumeration DOES NOT RAISE - FreeCAD keeps the
#: previous value, and ``Arch.makeStructure`` defaults it to "Column".  So
#: "MechanicalFastener" (no space) silently exported twelve anchor sets as
#: twelve IfcColumns, in a model where every count was right. The read-back
#: in :func:`_set_ifc_type` is there because of exactly that.
DETAIL_IFC_FOR = {
    "end_plate": "Plate",
    "base_plate": "Plate",
    "purlin_cleat": "Plate",
    "girt_clip": "Plate",
    "bracing_gusset": "Plate",
    "anchor": "Mechanical Fastener",
}


def _set_ifc_type(obj, ifc, report=None, mark="") -> bool:
    """Set ``IfcType`` and CHECK IT TOOK.

    An out-of-enumeration assignment is silently ignored by FreeCAD, which
    leaves the object carrying the Arch default. Nothing raises, nothing
    warns, and the object exports as the wrong IFC class while looking
    perfect in the tree - the same failure mode as every geometry bug in
    this adapter: the right number of objects, quietly wrong.
    """
    try:
        obj.IfcType = ifc
    except Exception:                                   # noqa: BLE001
        try:
            obj.Role = ifc
            return True
        except Exception:                               # noqa: BLE001
            pass
    got = str(getattr(obj, "IfcType", "")) or str(getattr(obj, "Role", ""))
    if got == ifc:
        return True
    if report is not None:
        # One line per BAD SPELLING, not one per object: the same wrong
        # value on 120 cleats is one fact, and 120 copies of it would bury
        # everything else in the report.
        msg = (
            f"IfcType {ifc!r} is not in this FreeCAD's enumeration - "
            f"objects like {mark or obj.Name} export as {got!r} instead"
        )
        if msg not in report.warnings:
            report.warnings.append(msg)
    return False


def _add_plate(doc, shape, *, name, mark, kind, profile, mass, length=0.0,
               utilisation=0.0, group=None, report=None, basis=None):
    """One connection plate as an Arch Structure, carrying the same
    properties every member carries.

    Deliberately the SAME property set as ``_add_member``: the schedule
    groups on ``MemberType`` and ``Profile`` and reads ``Weight_kg``, so a
    plate that carried different property names would silently weigh nothing
    in the MTO while still appearing in the model - which is the exact shape
    of the bug the handover's rule (h) is about.
    """
    obj = _structure(doc, shape, name)
    obj.Label = mark

    _set_ifc_type(obj, DETAIL_IFC_FOR.get(kind, "Plate"), report, mark)

    for prop, ptype, value in (
        ("Mark", "App::PropertyString", mark),
        ("MemberType", "App::PropertyString", kind),
        ("Profile", "App::PropertyString", profile),
        ("Length_mm", "App::PropertyFloat", float(length)),
        ("Weight_kg", "App::PropertyFloat", float(mass)),
        ("Utilisation", "App::PropertyFloat", float(utilisation)),
    ):
        if prop not in obj.PropertiesList:
            obj.addProperty(ptype, prop, "BIMSteelAuto", prop)
        try:
            setattr(obj, prop, value)
        except (TypeError, ValueError):
            alt = "BSA_" + prop
            if alt not in obj.PropertiesList:
                obj.addProperty(ptype, alt, "BIMSteelAuto", prop)
            setattr(obj, alt, value)

    # The plate's own (u, v) frame, in model coordinates. Stamped because
    # the DRAWING needs it: a hole position is laid out in (u, v) and a
    # dimension has to be placed in the view, and a rectangle does not carry
    # the sign of v. Without this the bolt set-out on a part sheet can only
    # be reconstructed by guessing which way up the plate is.
    if basis is not None:
        origin, e_u, e_v = basis
        # UNIT vectors, always. A brace diagonal's direction arrives here as
        # the rod itself - seven metres long - and storing that unnormalised
        # scales every hole position by the length of the brace, which puts
        # the bolt set-out eight hundred metres off the sheet. It did.
        def _unit(v):
            v = App.Vector(v)
            n = v.Length
            return App.Vector(v.x / n, v.y / n, v.z / n) if n > 1e-12 else v

        for prop, value in (
            ("PlateOrigin", App.Vector(origin)),
            ("PlateU", _unit(e_u)),
            ("PlateV", _unit(e_v)),
        ):
            if prop not in obj.PropertiesList:
                obj.addProperty("App::PropertyVector", prop, "BIMSteelAuto",
                                prop)
            try:
                setattr(obj, prop, App.Vector(value))
            except (TypeError, ValueError):              # pragma: no cover
                pass

    if obj.ViewObject is not None:                      # pragma: no cover
        obj.ViewObject.Proxy = 0
    if group is not None:
        group.addObject(obj)
    return obj


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


def build_model(
    result: DesignResult,
    doc=None,
    *,
    document_name: str | None = None,
    build_secondary: bool = True,
    build_bracing: bool = True,
    build_detailing: bool = True,
) -> BuildReport:
    """Build the whole building from one ``DesignResult``.

    Every transverse frame line gets the designed frame; the purlins, girts
    and eave struts run the length of the building; the bracing goes in the
    bays the design named.

    The DESIGN is one frame line - that is what the engine solves - so every
    frame line is built to it.  That is the same assumption the calculation
    sheet states, and it is correct for a repetitive PEB with uniform
    sections; a building with different frames needs one run per frame type.

    Every piece is placed where it is BUILT, not on its analysis line - see
    :mod:`bimsteelauto_fc.fabrication` for the conventions. Lengths stamped
    on the objects are the built lengths; weights are the design's.
    """
    from . import fabrication as fb

    if not result.members:
        raise BuildError("this DesignResult has no members to build")

    geometry = result.spec.geometry
    report = BuildReport()

    if doc is None:
        name = document_name or (
            re.sub(r"[^A-Za-z0-9_]", "_", result.spec.job_number) or "BIMSteelAuto"
        )
        doc = App.newDocument(name)
    report.document = doc

    sections = _sections_from_result(result)
    layout = build_layout(geometry, sections)
    ys = frame_line_positions(geometry)
    plan = fb.plan_frame(result, layout)
    report.warnings.extend(plan.notes)
    cols = {c.mark: c for c in plan.columns}
    rafs = {r.mark: r for r in plan.rafters}

    by_mark = {m.mark: m for m in result.members}
    frames = doc.addObject("App::DocumentObjectGroup", "Frames")
    frames.Label = "Main frames"

    # -- primary frames, one per frame line ---------------------------------
    for i, y in enumerate(ys):
        grp = doc.addObject("App::DocumentObjectGroup", f"Frame_{i + 1}")
        grp.Label = f"Frame line {i + 1}"
        frames.addObject(grp)
        for fm in layout.members:
            md = by_mark.get(fm.mark)
            if md is None:
                report.skipped.append(
                    (fm.mark, "no MemberDesign on the result")
                )
                continue
            try:
                if fm.mark in cols:
                    piece = cols[fm.mark]
                    shape = fb.column_solid(piece, y)
                else:
                    piece = rafs[fm.mark]
                    shape = fb.rafter_solid(piece, y)
                if not shape.isValid() or not shape.Solids:
                    raise BuildError(f"built an invalid solid for {fm.mark}")
            except (BuildError, KeyError, Part.OCCError) as exc:
                report.skipped.append((f"{fm.mark}@F{i + 1}", str(exc)))
                continue
            report.objects.append(
                _add_member(
                    doc, shape,
                    name=f"{fm.mark}_F{i + 1}", mark=f"{fm.mark}-F{i + 1}",
                    member_type=fm.member_type, profile=md.section_id,
                    length=piece.length, weight=fm.taper.weight,
                    utilisation=md.utilisation, group=grp,
                )
            )

    # -- secondary members --------------------------------------------------
    if build_secondary:
        _build_secondary(doc, result, plan, ys, report)

    # -- bracing ------------------------------------------------------------
    rod_ends: list = []
    if build_bracing:
        _build_bracing(doc, result, plan, ys, report, rod_ends)

    # -- shop detailing -----------------------------------------------------
    # LAST, and after the bracing, because the rod plates sit on the rods
    # the bracing pass just placed - taking their ends from that pass rather
    # than re-deriving them is what stops a plate appearing at a corner
    # where no brace was actually built.
    if build_detailing:
        _build_details(doc, result, layout, plan, ys, report, rod_ends)

    doc.recompute()
    return report


def _run_lines(layout, geometry) -> list[tuple[str, App.Vector, App.Vector]]:
    """Where the purlins and girts are SET OUT, on the analysis lines.

    Purlins march up each rafter at ``purlin_spacing`` measured ALONG THE
    SLOPE, which is what a PEB drawing dimensions; girts march up each
    column at ``girt_spacing``.  Both are returned as a point in the frame
    plane.  This is the set-out, not the built position: the builder stands
    each purlin on its cleat and hangs each girt outside its column, at
    these same ``s`` values (see :func:`bimsteelauto_fc.fabrication.setout`,
    which both use).
    """
    from .fabrication import setout

    out = []
    for fm in layout.members:
        if fm.member_type is MemberType.RAFTER:
            kind, spacing = "purlin", geometry.purlin_spacing
        elif fm.member_type in (MemberType.COLUMN, MemberType.INTERIOR_COLUMN):
            kind, spacing = "girt", geometry.girt_spacing
        else:
            continue
        ax = App.Vector(
            fm.j_point[0] - fm.i_point[0], 0.0, fm.j_point[1] - fm.i_point[1]
        )
        ax = App.Vector(ax).multiply(1.0 / ax.Length)
        start = App.Vector(fm.i_point[0], 0.0, fm.i_point[1])
        for s in setout(fm.taper.length, spacing):
            # ``Vector.multiply`` mutates IN PLACE and returns self, so
            # ``ax.multiply(s)`` would rescale the axis on every pass and
            # send the positions off exponentially. Copy first, always.
            base = start + App.Vector(ax).multiply(s)
            out.append((kind, base, App.Vector(ax)))
    return out


def _purlin_lines(plan, geometry, report=None):
    """``(rafter_plan, s)`` for every purlin line: ``s`` is the slope
    position of the purlin web face that bears on the cleat."""
    from .fabrication import setout

    out = []
    for rp in plan.rafters:
        for s in setout(rp.taper.length, geometry.purlin_spacing):
            out.append((rp, s))
    return out


def _girt_lines(plan, geometry):
    """``(column_plan, z)`` for every girt line, exterior columns only.

    An interior column line is not a wall and carries no girts; the
    centreline model put girts on it anyway, through the interior columns.
    """
    from .fabrication import setout

    out = []
    for col in plan.exterior_columns():
        for s in setout(col.taper.length, geometry.girt_spacing):
            out.append((col, col.z_base + s))
    return out


def _cleat_geom(result):
    """The purlin cleat's geometry, or ``None``. The same plate carries the
    girts and eave struts, the way the REF-2026-001 shop drawings do it."""
    from . import detailing as dt

    for c in getattr(result, "connections", ()) or ():
        if c.connection_type is ConnectionType.PURLIN_CLEAT:
            return c, dt.geometry_of(c)
    return None, None


def _profile_outline(section_id: str):
    """``(kind, outline, width_up, depth_out)`` of a secondary member, from
    its catalogue name. ``kind`` is ``"cf"`` or ``"bu"``; ``None`` if the
    name parses as neither."""
    from . import fabrication as fb

    cf = fb.cf_dims(section_id)
    if cf is not None:
        shape, H, B, D, t = cf
        return "cf", shape, fb.cf_outline(shape, H, B, D, t), B, H, t
    try:
        sec = parse_mark(section_id, 1000.0)
    except SectionError:
        return None
    if isinstance(sec, TaperedMember):
        sec = sec.start
    bf = max(sec.bf_top, sec.bf_bot)
    outline = [(u + bf / 2.0, v) for u, v in fb.i_outline(
        sec.d, sec.tw, sec.bf_top, sec.tf_top, sec.bf_bot, sec.tf_bot)]
    return "bu", "I", outline, bf, sec.d, sec.tw


def _build_secondary(doc, result, plan, ys, report) -> None:
    """Purlins, girts and the eave struts, running the building's length."""
    from . import fabrication as fb

    geometry = result.spec.geometry
    grp = doc.addObject("App::DocumentObjectGroup", "Secondary")
    grp.Label = "Purlins, girts and struts"

    designs = {
        MemberType.PURLIN: next(
            (m for m in result.members if m.member_type is MemberType.PURLIN),
            None,
        ),
        MemberType.GIRT: next(
            (m for m in result.members if m.member_type is MemberType.GIRT),
            None,
        ),
    }
    dims: dict[MemberType, tuple] = {}
    for mt, md in designs.items():
        if md is None:
            report.warnings.append(
                f"no {mt.value} on the result - none were built"
            )
            continue
        d = fb.cf_dims(md.section_id)
        if d is None:
            report.skipped.append(
                (md.mark,
                 f"section name {md.section_id!r} does not match the "
                 "C/Z HxBxDxt pattern, so its size is unknown - not guessed")
            )
            continue
        dims[mt] = d

    _conn, cleat = _cleat_geom(result)
    t_cleat = float(cleat["thickness_mm"]) if cleat else 0.0
    h_cleat = float(cleat["height_mm"]) if cleat else 0.0

    y0, y1 = ys[0], ys[-1]
    n = 0

    def add(shape, md, mt, length):
        nonlocal n
        n += 1
        # CAREFUL: for a purlin line the engine reports ``length`` as the
        # governing SPAN but ``weight`` as the whole RUN - two different
        # bases on one object. Each line built here is one full-length run,
        # so md.weight is exactly this object's mass; md.length is not its
        # length. Getting that backwards would multiply the secondary steel
        # by the number of bays.
        report.objects.append(
            _add_member(
                doc, shape,
                name=f"{md.mark}_{n}", mark=f"{md.mark}-{n}",
                member_type=mt, profile=md.section_id,
                length=length, weight=md.weight,
                utilisation=md.utilisation, group=grp,
            )
        )

    # -- purlins: standing on the cleats, web against the cleat -------------
    if MemberType.PURLIN in dims:
        shape_kind, H, B, D, t = dims[MemberType.PURLIN]
        outline = fb.cf_outline(shape_kind, H, B, D, t)
        md = designs[MemberType.PURLIN]
        s_lo = min(s for s, _n in outline)
        s_hi = max(s for s, _n in outline)
        if cleat and h_cleat > H - t - 1.0 and shape_kind == "Z":
            report.warnings.append(
                f"the {h_cleat:.0f} mm cleat is taller than the clear web "
                f"under the downhill flange of {md.section_id} - they clash"
            )
        for rp, s in _purlin_lines(plan, geometry):
            lo = rp.s_of_x(rp.xa) + fb.GAP
            hi = rp.s_of_x(rp.xb) - fb.GAP
            if s + min(s_lo, -t_cleat) < lo or s + s_hi > hi:
                report.warnings.append(
                    f"{md.mark} at s = {s:.0f} on {rp.mark} is within its "
                    "own width of an end plate - built at the design set-out, "
                    "check the clash"
                )
            try:
                shape = fb.purlin_solid(rp, s, outline, y0, y1)
            except (ValueError, Part.OCCError) as exc:  # pragma: no cover
                report.skipped.append((f"{md.mark}-{n + 1}", str(exc)))
                continue
            add(shape, md, MemberType.PURLIN, y1 - y0)

    # -- eave struts: outside the column, web horizontal, top at the eave ---
    strut = next(
        (m for m in result.members if m.member_type is MemberType.EAVE_STRUT),
        None,
    )
    strut_bottom: dict[str, float] = {}
    if strut is not None:
        prof = _profile_outline(strut.section_id)
        weight = None
        if prof is not None and prof[0] == "bu":
            try:
                taper = parse_mark(strut.section_id, y1 - y0)
                if isinstance(taper, WeldedI):
                    taper = TaperedMember(start=taper, end=taper,
                                          length=y1 - y0)
                weight = taper.weight
            except SectionError:                        # pragma: no cover
                prof = None
        elif prof is not None:
            weight = strut.weight
        if prof is None:
            report.skipped.append(
                (strut.mark, f"section_id {strut.section_id!r} did not parse")
            )
        else:
            _k, _shape, outline, width_up, _depth, _t = prof
            for k, col in enumerate(plan.exterior_columns(), start=1):
                z_eave = plan_eave_z(plan, col)
                z0 = z_eave - width_up
                strut_bottom[col.mark] = z0 - t_cleat
                try:
                    shape = fb.wall_solid(col, z0, outline, y0, y1)
                except (ValueError, Part.OCCError) as exc:  # pragma: no cover
                    report.skipped.append((f"{strut.mark}-{k}", str(exc)))
                    continue
                report.objects.append(
                    _add_member(
                        doc, shape,
                        name=f"{strut.mark}_{k}", mark=f"{strut.mark}-{k}",
                        member_type=MemberType.EAVE_STRUT,
                        profile=strut.section_id, length=(y1 - y0),
                        weight=weight, utilisation=strut.utilisation,
                        group=grp,
                    )
                )

    # -- girts: outside the column, web horizontal --------------------------
    if MemberType.GIRT in dims:
        shape_kind, H, B, D, t = dims[MemberType.GIRT]
        outline = fb.cf_outline(shape_kind, H, B, D, t)
        md = designs[MemberType.GIRT]
        up = max(s for s, _n in outline)
        for col, z in _girt_lines(plan, geometry):
            z0 = z - (B / 2.0 if shape_kind == "C" else 0.0)
            top = strut_bottom.get(col.mark)
            if top is not None and z0 + up > top - fb.GAP:
                report.warnings.append(
                    f"{md.mark} at z = {z:.0f} on {col.mark} runs into the "
                    "eave strut above it - built at the design set-out, "
                    "check the clash"
                )
            try:
                shape = fb.wall_solid(col, z0, outline, y0, y1)
            except (ValueError, Part.OCCError) as exc:  # pragma: no cover
                report.skipped.append((f"{md.mark}-{n + 1}", str(exc)))
                continue
            add(shape, md, MemberType.GIRT, y1 - y0)
        n_interior = len([c for c in plan.columns if not c.exterior])
        if n_interior:
            report.warnings.append(
                f"{n_interior} interior column line(s) carry no girts - an "
                "interior line is not a wall"
            )


def plan_eave_z(plan, col) -> float:
    """Top of steel at an exterior column's grid line - the eave height."""
    for rp in plan.rafters:
        if col.node_top in (rp.node_i, rp.node_j):
            return rp.z_top(col.x_grid)
    return col.z_top                                    # pragma: no cover


def _brace_radius(section_id: str, default: float = 10.0) -> float:
    """Rod radius from a mark like ``M20 rod``.  Half the nominal diameter.

    A rod is drawn on its NOMINAL diameter, not its thread root - the root
    is a strength property and the shank is what you see.
    """
    m = re.search(r"M(\d+(?:\.\d+)?)", section_id)
    return float(m.group(1)) / 2.0 if m else default


def _is_rod(section_id: str) -> bool:
    return bool(re.search(r"M\d+(?:\.\d+)?\s*rod", str(section_id), re.I))


def _build_bracing(doc, result, plan, ys, report, rod_ends=None) -> None:
    """The bracing diagonals, drawn as X panels in the braced bays.

    Roof bracing lies in the plane of a roof slope, running from near the
    eave on one frame to a point part-way up the rafter on the next; wall
    bracing is an X in the sidewall between base and eave.  Both stop at
    the web faces they pass through: the rod goes through a hole in the web
    and is nutted on a rod plate on the far side, so the BUILT rod runs
    plate face to plate face. The two diagonals of an X are offset by a rod
    diameter and a millimetre, so they pass each other.

    WHICH bays are braced is not carried on a pre-1.1.0 ``DesignResult`` -
    it is an arrangement the engineer chose, and the result records the
    forces it produced rather than the layout. Rather than guess, the panels
    go in the END bays and the choice is reported as a placeholder.
    """
    from . import fabrication as fb

    braces = [m for m in result.members if m.member_type in _BRACING]
    if not braces:
        return

    n_bays = len(ys) - 1
    if n_bays < 1:
        return

    grp = doc.addObject("App::DocumentObjectGroup", "Bracing")
    grp.Label = "Longitudinal bracing"

    # Schema 1.1.0 carries the arrangement. An older result does not, and
    # the difference matters: an EMPTY braced_bays on a 1.1.0 result means
    # the building genuinely has no bracing, while a MISSING one means
    # nobody recorded it. Only the second is a case for guessing.
    arrangement = getattr(result, "bracing", None)
    splits = 2.0
    if arrangement is not None and arrangement.is_stated:
        bays = [b for b in arrangement.braced_bays if 0 <= b < n_bays]
        splits = float(max(1, arrangement.roof_panel_splits))
        dropped = set(arrangement.braced_bays) - set(bays)
        if dropped:
            report.warnings.append(
                f"braced bays {sorted(dropped)} are outside this building's "
                f"0..{n_bays - 1} and were not drawn"
            )
    else:
        bays = sorted({0, n_bays - 1})
        report.warnings.append(
            "this DesignResult carries no bracing arrangement (schema "
            f"{getattr(result, 'schema_version', '?')} predates 1.1.0), so "
            "the panels are drawn in the END bays as a placeholder. The "
            "FORCES on the calculation sheet are the designed ones; these "
            "positions are a guess."
        )
    if not bays:
        report.warnings.append(
            "the design records NO braced bays - there is no longitudinal "
            "load path, and nothing was drawn"
        )
        return

    V = App.Vector
    Y = V(0, 1, 0)
    n = 0

    def x_pair(md, corners, tw, e_u, e_v):
        """One X from ``corners`` = ((pa, pb), (qa, qb)): rod 1 from ``pa``
        on frame a to ``pb`` on frame b, rod 2 from ``qb`` on frame b to
        ``qa`` on frame a. Each point is (x, z) on the web face; the rods
        start at the far face of their rod plates."""
        nonlocal n
        r = _brace_radius(md.section_id)
        is_rod = _is_rod(md.section_id)
        tp = (fb.rod_plate_geometry(2.0 * r)["thickness_mm"]
              if is_rod else 0.0)
        for (pa, ya, sa), (pb, yb, sb) in corners:
            # sa/sb: +1 when the plate is on the +Y face of that web.
            fa = ya + sa * (tw / 2.0 + tp)
            fb_ = yb + sb * (tw / 2.0 + tp)
            p = V(pa[0], fa, pa[1])
            q = V(pb[0], fb_, pb[1])
            d = q.sub(p)
            clear = fb.disc_clearance(d, Y, r)
            try:
                shape, length = fb.rod_shape(p, q, r, (clear, clear))
            except Exception as exc:                    # noqa: BLE001
                report.skipped.append((f"{md.mark}-{n + 1}", str(exc)))
                continue
            n += 1
            if rod_ends is not None:
                for (x, z), yy, sg in ((pa, ya, sa), (pb, yb, sb)):
                    rod_ends.append(fb.RodEnd(
                        rod_mark=md.mark, rod_diameter=2.0 * r,
                        centre=V(x, yy + sg * (tw / 2.0 + tp / 2.0), z),
                        e_u=V(e_u), e_v=V(e_v),
                    ))
            report.objects.append(
                _add_member(
                    doc, shape,
                    name=f"{md.mark}_{n}", mark=f"{md.mark}-{n}",
                    member_type=md.member_type, profile=md.section_id,
                    length=length, weight=md.weight,
                    utilisation=md.utilisation, group=grp,
                )
            )

    walls = plan.exterior_columns()
    for b in bays:
        ya, yb = ys[b], ys[b + 1]
        for md in braces:
            r = _brace_radius(md.section_id)
            side = fb.rod_plate_geometry(2.0 * r)["width_mm"]
            off = r + 1.0
            if md.member_type is MemberType.WALL_BRACING:
                # An X in each braced sidewall, base to eave, in the plane
                # of the column webs.  Only the sidewalls the design
                # actually braced: a building built against something next
                # door has one, and drawing two would put steel in the model
                # that was never designed.
                n_walls = 2
                if arrangement is not None and arrangement.is_stated:
                    n_walls = arrangement.n_braced_walls
                for col in walls[:n_walls]:
                    xw = col.x_grid + col.sigma * col.taper.start.d / 2.0
                    z_lo = col.z_base + fb.ROD_CLEAR
                    z_hi = plan_eave_z(plan, col) - fb.ROD_CLEAR
                    _check_web_fit(report, md, col, xw, off, side, z_lo, z_hi)
                    corners = (
                        (((xw - off, z_lo), ya, +1), ((xw - off, z_hi), yb, -1)),
                        (((xw + off, z_hi), ya, +1), ((xw + off, z_lo), yb, -1)),
                    )
                    x_pair(md, corners, col.tw, V(1, 0, 0), V(0, 0, 1))
            else:
                # An X in each roof slope, from the eave end of the rafter
                # to the panel point the design used, below the top flange.
                for rp in plan.rafters:
                    tf = rp.taper.start.tf_top
                    s_lo = rp.s_of_x(rp.xa) + fb.ROD_CLEAR
                    s_hi = min(rp.taper.length / splits,
                               rp.s_of_x(rp.xb) - fb.ROD_CLEAR)
                    n_a = -(tf + 10.0 + side / 2.0)
                    n_b = n_a - 2.0 * off
                    _check_rafter_fit(report, md, rp, (s_lo, s_hi),
                                      n_b - side / 2.0)
                    pa, pb = rp.point(s_lo, n_a), rp.point(s_hi, n_a)
                    qa, qb = rp.point(s_hi, n_b), rp.point(s_lo, n_b)
                    corners = (
                        ((pa, ya, +1), (pb, yb, -1)),
                        ((qa, ya, +1), (qb, yb, -1)),
                    )
                    ax, az = rp.a
                    nx, nz = rp.n
                    x_pair(md, corners, rp.taper.start.tw,
                           V(ax, 0, az), V(nx, 0, nz))


def _check_web_fit(report, md, col, xw, off, side, z_lo, z_hi) -> None:
    """A rod plate has to fit on the column web between the flanges."""
    _bo, to = col.outer
    _bi, ti = col.inner
    clear = col.depth_at(z_lo) - to - ti
    need = 2.0 * off + side + 2.0
    if need > clear:
        msg = (f"{md.mark}: the rod plates need {need:.0f} mm of web and "
               f"{col.mark} has {clear:.0f} mm at its base - they clash "
               "with the flanges")
        if msg not in report.warnings:
            report.warnings.append(msg)


def _check_rafter_fit(report, md, rp, s_range, n_bottom) -> None:
    for s in s_range:
        x = rp.x_of_s(s)
        depth = rp.depth_at(x)
        if -n_bottom > depth - rp.taper.start.tf_bot - 5.0:
            msg = (f"{md.mark}: the rod plates do not fit in the web of "
                   f"{rp.mark} at s = {s:.0f} ({depth:.0f} mm deep)")
            if msg not in report.warnings:
                report.warnings.append(msg)


# ---------------------------------------------------------------------------
# Shop detailing
# ---------------------------------------------------------------------------

#: The ONE place a plate's (u, v) frame maps into the model, per connection
#: type.  Written out rather than derived, because each mapping is a claim
#: about how a real joint is put together and it should be readable as one:
#:
#:   end plate     u = along the building, v = up.  Its normal is therefore
#:                 across the span, which is what "cut vertically across the
#:                 rafter" means and why the layout carried a cos.
#:   base plate    u = across the frame (B), v = along the building (N), so
#:                 the plate lies flat and its normal is up.
#:   purlin cleat  u = along the building, v = up the purlin web (the
#:                 rafter's own normal), so the plate stands square to the
#:                 roof and the bolts run along the slope through the web.
#:   girt clip     u = along the building, v = outward from the column, so
#:                 the plate lies flat under the girt's horizontal web.
#:   rod plate     u, v in the plane of the web the rod passes through.
_UP = (0.0, 0.0, 1.0)
_ALONG_BUILDING = (0.0, 1.0, 0.0)
_ACROSS_FRAME = (1.0, 0.0, 0.0)


def _plate_profile(kind: str, geom: dict) -> str:
    """``PL 20 x 300 x 1006`` - thickness, width, length, the way a shop
    reads a plate.  This string is the schedule's grouping key, so two
    plates of the same size land on one line and two of different sizes
    cannot."""
    if kind == "anchor":
        return (
            f"M{geom.get('anchor_diameter_mm', 0):.0f} x "
            f"{geom.get('anchor_length_mm', 0):.0f} anchor"
        )
    return (
        f"PL {geom['thickness_mm']:.0f} x {geom['width_mm']:.0f} x "
        f"{geom['height_mm']:.0f}"
    )


def _detail_report(report, conn, geom) -> None:
    """Anything the LAYOUT could not close, carried into the build report.

    A dropped bolt row is an engineering statement, and it was made in the
    engine where it could be tested.  It must not stop at the JSON: the
    person looking at the model is the person who needs to know that the
    outer row was left off.
    """
    for line in geom.get("dropped", ()):
        report.warnings.append(f"{conn.mark}: {line}")


def _build_details(doc, result, layout, plan, ys, report, rod_ends) -> None:
    """Every connection on the result that carries shop geometry.

    The geometry is NOT derived here.  It is read off the ``ConnectionDesign``
    where the engine put it, and the only thing this function decides is
    WHERE each plate goes and which way round it is - which is a model
    question, not an engineering one. The one exception is the rod plate,
    which is stated as such: see :func:`fabrication.rod_plate_geometry`.

    A connection with no geometry is skipped and named.  That covers a result
    written before schema 1.2.0 and a layout that would not close, and
    neither is a case for drawing an approximate plate: an invented plate
    looks exactly as convincing as a designed one, and no limit state covers
    it.
    """
    from . import detailing as dt
    from . import fabrication as fb

    conns = list(getattr(result, "connections", ()) or ())
    if not conns:
        return

    grp = doc.addObject("App::DocumentObjectGroup", "Connections")
    grp.Label = "Connection detailing"

    def by_type(ct):
        return [c for c in conns if c.connection_type is ct]

    n = 0
    V = App.Vector

    def place(shape, conn, kind, geom, mass=None, basis=None, suffix="",
              length=None, stem=None, utilisation=None):
        """One built plate, marked so its part sheet is its own.

        ``suffix`` keeps the anchors apart from the plate they pass through:
        the part sheets group on the mark PREFIX, so ``BP1`` and ``BP1A``
        get a sheet each and a bag of anchor bolts is not drawn as a base
        plate. ``utilisation`` is 0 for a plate no check ran on - a girt
        clip carrying the purlin cleat's ratio would claim a check of the
        girt connection that was never made.
        """
        nonlocal n
        n += 1
        m = dt.mass_kg(shape) if mass is None else mass
        stem = stem or f"{conn.mark}{suffix}"
        # A plate's "length" in a cutting list is its long side, and an
        # anchor's is the rod. Leaving it at zero put a column of 0 mm in
        # the MTO, which reads like something failed rather than like a
        # plate.
        if length is None:
            length = (float(geom.get("anchor_length_mm", 0.0))
                      if kind == "anchor"
                      else max(float(geom["width_mm"]),
                               float(geom["height_mm"])))
        return _add_plate(
            doc, shape,
            name=f"{stem}_{n}", mark=f"{stem}-{n}", kind=kind,
            profile=_plate_profile(kind, geom), mass=m, length=length,
            utilisation=(float(getattr(conn, "utilisation", 0.0) or 0.0)
                         if utilisation is None else utilisation),
            group=grp, report=report, basis=basis,
        )

    # -- end plates: one per eave and per ridge, on every frame line --------
    # The plate goes where the frame plan put it: on the column's inner
    # flange at a knee, centred on the node at a ridge.
    for ct, nodes in (
        (ConnectionType.EAVE_END_PLATE, list(layout.eave_nodes)),
        (ConnectionType.APEX_END_PLATE, list(layout.ridge_nodes)),
    ):
        for conn, node in zip(by_type(ct), nodes):
            geom = dt.geometry_of(conn)
            if geom is None:
                report.skipped.append((conn.mark, dt.why_not(conn)))
                continue
            jp = plan.joints.get(node)
            if jp is None or jp.conn is not conn:
                report.warnings.append(
                    f"{conn.mark} pairs with node {node}, which already has "
                    f"{getattr(getattr(jp, 'conn', None), 'mark', 'no')} "
                    "plate - not drawn twice"
                )
                continue
            _detail_report(report, conn, geom)
            for i, y in enumerate(ys, start=1):
                datum = V(jp.x_mid, y, jp.z_datum)
                try:
                    shape = dt.end_plate_solid(
                        geom, datum,
                        V(*_ALONG_BUILDING), V(*_UP),
                    )
                except (dt.DetailError, Part.OCCError) as exc:
                    report.skipped.append((f"{conn.mark}@F{i}", str(exc)))
                    continue
                report.objects.append(place(
                    shape, conn, "end_plate", geom,
                    basis=(datum, V(*_ALONG_BUILDING), V(*_UP)),
                ))

    # -- base plates and anchors -------------------------------------------
    col_by_base = dict(zip(layout.base_nodes, plan.columns))
    for conn, node in zip(
        by_type(ConnectionType.BASE_PLATE), list(layout.base_nodes)
    ):
        geom = dt.geometry_of(conn)
        if geom is None:
            report.skipped.append((conn.mark, dt.why_not(conn)))
            continue
        _detail_report(report, conn, geom)
        col = col_by_base[node]
        # Under the middle of the column footprint, not under the grid:
        # the grid is the outer flange face.
        x_c = col.x_grid + col.sigma * col.taper.start.d / 2.0
        for i, y in enumerate(ys, start=1):
            centre = V(x_c, y, col.z_base)
            try:
                plate = dt.base_plate_solid(
                    geom, centre,
                    V(*_ACROSS_FRAME), V(*_ALONG_BUILDING),
                )
                rodset = dt.anchor_solids(
                    geom, centre,
                    V(*_ACROSS_FRAME), V(*_ALONG_BUILDING),
                )
            except (dt.DetailError, Part.OCCError) as exc:
                report.skipped.append((f"{conn.mark}@F{i}", str(exc)))
                continue
            # The layout's (u, v) origin is the plate's own centre, which
            # sits half a thickness below the column base - the same point
            # base_plate_solid drilled from.
            n_up = V(*_UP)
            bp_datum = centre + V(n_up) * (-float(geom["thickness_mm"]) / 2.0)
            report.objects.append(place(
                plate, conn, "base_plate", geom,
                basis=(bp_datum, V(*_ACROSS_FRAME), V(*_ALONG_BUILDING)),
            ))
            if rodset is not None:
                # The anchors are a SEPARATE object, not fused into the
                # plate.  Fusing them would put the anchor steel on the plate
                # line of the MTO, and an anchor is bought, not fabricated.
                report.objects.append(
                    place(rodset, conn, "anchor", geom, suffix="A")
                )

    # -- purlin cleats: one per purlin line per frame line ------------------
    # Welded on the rafter top flange, standing square to the roof, on the
    # DOWNHILL side of the purlin web that bolts to it.
    for conn in by_type(ConnectionType.PURLIN_CLEAT):
        geom = dt.geometry_of(conn)
        if geom is None:
            report.skipped.append((conn.mark, dt.why_not(conn)))
            continue
        _detail_report(report, conn, geom)
        t = float(geom["thickness_mm"])
        h = float(geom["height_mm"])
        for rp, s in _purlin_lines(plan, result.spec.geometry):
            nx, nz = rp.n
            e_v = V(nx, 0.0, nz)
            x, z = rp.point(s - t / 2.0, h / 2.0)
            for i, y in enumerate(ys, start=1):
                centre = V(x, y, z)
                try:
                    shape = dt.cleat_solid(
                        geom, centre, V(*_ALONG_BUILDING), e_v
                    )
                except (dt.DetailError, Part.OCCError) as exc:
                    report.skipped.append((f"{conn.mark}@F{i}", str(exc)))
                    continue
                report.objects.append(place(
                    shape, conn, "purlin_cleat", geom,
                    basis=(centre, V(*_ALONG_BUILDING), e_v),
                ))

        # -- girt and eave strut clips: the same plate, lying flat under
        # the member's horizontal web on the column's outer flange. Not a
        # separate design: the REF-2026-001 shop drawings use the purlin
        # cleat plate here, and so does this.
        clip_rows = []
        girt = next((m for m in result.members
                     if m.member_type is MemberType.GIRT), None)
        if girt is not None:
            d = fb.cf_dims(girt.section_id)
            if d is not None:
                for col, zg in _girt_lines(plan, result.spec.geometry):
                    z0 = zg - (d[2] / 2.0 if d[0] == "C" else 0.0)
                    clip_rows.append((col, z0))
        strut = next((m for m in result.members
                      if m.member_type is MemberType.EAVE_STRUT), None)
        if strut is not None:
            prof = _profile_outline(strut.section_id)
            if prof is not None:
                for col in plan.exterior_columns():
                    clip_rows.append((col, plan_eave_z(plan, col) - prof[3]))
        for col, z0 in clip_rows:
            e_v = V(-col.sigma, 0.0, 0.0)
            centre = V(col.x_grid - col.sigma * h / 2.0, 0.0, z0 - t / 2.0)
            for i, y in enumerate(ys, start=1):
                c = V(centre.x, y, centre.z)
                try:
                    shape = dt.cleat_solid(geom, c, V(*_ALONG_BUILDING), e_v)
                except (dt.DetailError, Part.OCCError) as exc:
                    report.skipped.append((f"{conn.mark}G@F{i}", str(exc)))
                    continue
                report.objects.append(place(
                    shape, conn, "girt_clip", geom,
                    basis=(c, V(*_ALONG_BUILDING), e_v),
                    stem=f"{conn.mark}G", utilisation=0.0,
                ))

    # -- bracing: a rod plate at every rod end -----------------------------
    gussets = [c for c in by_type(ConnectionType.BRACING_GUSSET)
               if dt.geometry_of(c) is not None]
    for conn in by_type(ConnectionType.BRACING_GUSSET):
        if dt.geometry_of(conn) is None:
            report.skipped.append((conn.mark, dt.why_not(conn)))
    if not gussets:
        return
    conn = gussets[0]
    _detail_report(report, conn, dt.geometry_of(conn))
    if not rod_ends:
        report.warnings.append(
            f"{conn.mark} is designed and carries geometry, but no brace "
            "was built for it to sit on - nothing was drawn"
        )
        return
    braces = {m.mark: m for m in result.members if m.member_type in _BRACING}
    stated = set()
    for end in rod_ends:
        md = braces.get(end.rod_mark)
        if md is None or not _is_rod(md.section_id):
            report.skipped.append((
                f"{conn.mark}@{end.rod_mark}",
                f"{end.rod_mark} is not a threaded rod, and the gusset an "
                "angle brace needs is an arrangement this adapter does not "
                "have - not drawn",
            ))
            continue
        geom = fb.rod_plate_geometry(end.rod_diameter)
        if end.rod_mark not in stated:
            stated.add(end.rod_mark)
            report.warnings.append(
                f"{conn.mark} is laid out for a bolted gusset "
                f"({_plate_profile('bracing_gusset', dt.geometry_of(conn))}) "
                f"and {end.rod_mark} is a threaded rod, which has none: each "
                f"rod end is built on a rod plate "
                f"{_plate_profile('bracing_gusset', geom)}, hole "
                f"{geom['hole_diameter_mm']:.0f} mm, sized to the rod by "
                "set-out rule - no limit state ran on it"
            )
        try:
            shape = dt.cleat_solid(geom, end.centre, end.e_u, end.e_v)
        except (dt.DetailError, Part.OCCError) as exc:
            report.skipped.append((f"{end.rod_mark}P", str(exc)))
            continue
        report.objects.append(place(
            shape, conn, "bracing_gusset", geom,
            basis=(end.centre, end.e_u, end.e_v),
            stem=f"{end.rod_mark}P", utilisation=0.0,
        ))
