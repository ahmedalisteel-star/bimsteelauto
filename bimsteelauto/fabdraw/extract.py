"""
Stage 5 step 3a - read everything a fabrication sheet needs out of the IFC.

Nothing here is typed by anyone: lengths, plate sizes, hole positions,
weights and quantities are measured on the IFC geometry
(``ifcopenshell.geom``, world coordinates, millimetres). The marks and
descriptions are read from property sets; where a mark does not come
from the fabrication Pset the source it DID come from is recorded on the
part, so a register can show that a mark was taken from ``Tag`` or
``Name`` instead of being silently trusted.

Two mark policies:

* ``strict`` (BIMSteelAuto models) - only ``BSA_Fabrication``. A part
  without a mark keeps ``""`` and the sheet is not for issue (G2 has
  already said why).
* ``any`` (models from Tekla, Advance Steel, Revit, ...) - the first of
  a known list of Psets that carries a mark, then ``Tag``, then ``Name``.

Holes are the round ``IfcOpeningElement`` s that void a part (the same
definition G2 rule S5 uses): centre, axis, diameter and depth come from
the opening's own extrusion, not from the triangulated hole wall. Any
other opening (a cope, a notch, a slot) is subtracted from the part's
shape by the geometry kernel and is drawn as part outline.
"""

from __future__ import annotations

import math
import multiprocessing
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ue
import ifcopenshell.util.placement as up
import ifcopenshell.util.unit as uu

from . import ifcmodel

__all__ = ["Hole", "Part", "Assembly", "FabModel", "read_model", "STEEL_DENSITY_KG_M3"]

#: kg/m3. Used only when the model gives no weight. Stated, not hidden: every
#: register prints it.
STEEL_DENSITY_KG_M3 = 7850.0

# (pset, property) tried in order under the "any" policy, after BSA_Fabrication.
PART_MARK_KEYS = [("BSA_Fabrication", "PartMark"), ("Fabrication", "PartMark"), ("Tekla Common", "Part mark"),
                  ("Tekla Common", "Part Mark"), ("Tekla Part", "Part mark"), ("AdvanceSteel", "Single part mark"),
                  ("Identity Data", "Mark"), ("Pset_ManufacturerOccurrence", "SerialNumber")]
ASM_MARK_KEYS = [("BSA_Fabrication", "AssemblyMark"), ("Fabrication", "AssemblyMark"), ("Pset_SteelAssembly", "Mark"),
                 ("Tekla Assembly", "Assembly/Cast unit Mark"), ("Tekla Assembly", "Assembly mark"),
                 ("Tekla Common", "Assembly mark"), ("AdvanceSteel", "Assembly mark"), ("Identity Data", "Mark")]
PROFILE_KEYS = [("BSA_Fabrication", "Profile"), ("Fabrication", "Profile"), ("Tekla Common", "Profile"),
                ("Pset_SteelAssembly", "Profile"), ("Identity Data", "Type Name")]
GRADE_KEYS = [("Fabrication", "Material"), ("Pset_SteelAssembly", "Grade"), ("Tekla Common", "Material"),
              ("Pset_MaterialSteel", "StructuralGrade")]
NAME_KEYS = [("Fabrication", "Name"), ("Pset_SteelAssembly", "MemberType"), ("Tekla Common", "Name")]


@dataclass
class Hole:
    """A round hole: ``center`` is the mid-point of the hole axis inside the part (mm, world)."""

    center: np.ndarray
    axis: np.ndarray            # unit, world
    d: float
    depth: float
    gid: str                    # the IfcOpeningElement


@dataclass
class Part:
    gid: str
    mark: str
    mark_src: str
    ifc_class: str
    name: str
    profile: str
    grade: str
    is_main: bool | None
    verts: np.ndarray           # (N, 3) mm world
    tris: np.ndarray            # (M, 3) int
    holes: list[Hole] = field(default_factory=list)
    frame: tuple = ()           # (origin, e1, e2, n) - e1 along the length, n through the thinnest side
    ext: tuple = (0.0, 0.0, 0.0)   # extents along e1, e2, n (mm)
    is_plate: bool = False
    volume: float = 0.0         # mm3
    area: float = 0.0           # m2 (all faces)
    weight: float = 0.0         # kg
    weight_src: str = ""

    @property
    def length(self) -> float:
        return self.ext[0]

    @property
    def thickness(self) -> float:
        return self.ext[2]

    def description(self) -> str:
        """Tekla style: plates PLt*w, profiles their designation."""
        if self.is_plate:
            t, w = self.ext[2], self.ext[1]
            return f"PL{_fmt(t)}*{_fmt(w)}"
        return self.profile or self.name or self.ifc_class[3:]

    def fingerprint(self) -> tuple:
        return (round(self.ext[0]), round(self.ext[1]), round(self.ext[2]), len(self.holes), round(self.weight, 1))


@dataclass
class Assembly:
    mark: str
    mark_src: str
    name: str
    gids: list[str]             # every instance with this mark
    parts: list[Part]           # of the representative (first) instance
    main: Part | None
    frame: tuple = ()

    @property
    def qty(self) -> int:
        return len(self.gids)

    @property
    def weight(self) -> float:
        return sum(p.weight for p in self.parts)

    @property
    def area(self) -> float:
        return sum(p.area for p in self.parts)


@dataclass
class FabModel:
    ifc_path: str
    schema: str
    project: str
    assemblies: list[Assembly]
    parts_by_mark: dict[str, list[Part]]          # every part instance, by part mark
    allocation: dict[str, Counter]                # part mark -> {assembly mark: total qty}
    warnings: list[str]
    policy: str


def _fmt(x: float) -> str:
    return str(int(round(x))) if abs(x - round(x)) < 0.05 else f"{x:.1f}"


def _pset(el, keys):
    ps = ue.get_psets(el)
    for p, k in keys:
        v = (ps.get(p) or {}).get(k)
        if v not in (None, ""):
            return str(v), f"{p}.{k}"
    return "", ""


def resolve_mark(el, kind: str, policy: str) -> tuple[str, str]:
    keys = ASM_MARK_KEYS if kind == "assembly" else PART_MARK_KEYS
    if policy == "strict":
        keys = keys[:1]
    v, src = _pset(el, keys)
    if v or policy == "strict":
        return v, src
    for attr in ("Tag", "Name"):
        v = getattr(el, attr, None)
        if v:
            return str(v), attr
    return "", ""


# ---------------------------------------------------------------- geometry
def _settings():
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    s.set("weld-vertices", True)
    return s


def _meshes(model, elements) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """``{element id: (verts mm, tris)}`` for every element that has a body."""
    out = {}
    if not elements:
        return out
    it = ifcopenshell.geom.iterator(_settings(), model, max(1, multiprocessing.cpu_count() - 1), include=elements)
    if not it.initialize():
        return out
    while True:
        sh = it.get()
        g = sh.geometry
        v = np.asarray(g.verts, dtype=float).reshape(-1, 3) * 1000.0      # geom is always metres
        f = np.asarray(g.faces, dtype=np.int64).reshape(-1, 3)
        if len(f):
            out[sh.id] = (v, f)
        if not it.next():
            break
    return out


def mesh_volume_area(v: np.ndarray, f: np.ndarray) -> tuple[float, float]:
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    vol = abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0
    area = np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2.0 / 1e6
    return float(vol), float(area)


def _unit(x):
    x = np.asarray(x, dtype=float)
    n = np.linalg.norm(x)
    return x / n if n > 1e-12 else x


def _min_rect_axis(pts2: np.ndarray) -> np.ndarray:
    """Direction (2D unit) of the long side of the minimum-area rectangle around ``pts2``."""
    from shapely.geometry import MultiPoint

    hull = MultiPoint([tuple(p) for p in np.unique(np.round(pts2, 3), axis=0)]).convex_hull
    if hull.geom_type != "Polygon":
        d = pts2.max(0) - pts2.min(0)
        return np.array([1.0, 0.0]) if d[0] >= d[1] else np.array([0.0, 1.0])
    r = np.asarray(hull.minimum_rotated_rectangle.exterior.coords)[:4]
    s1, s2 = r[1] - r[0], r[2] - r[1]
    s = s1 if np.linalg.norm(s1) >= np.linalg.norm(s2) else s2
    return _unit(s)


def _orient(e1, e2, column_up=True):
    """Stable signs: e1 runs left->right (+X, then +Y) or bottom->top; e2 points up where it can."""
    e1, e2 = _unit(e1), _unit(e2)
    if abs(e1[2]) > 0.7:
        if column_up and e1[2] < 0:
            e1 = -e1
    else:
        k = 0 if abs(e1[0]) >= abs(e1[1]) else 1
        if e1[k] < 0:
            e1 = -e1
    if abs(e2[2]) > 1e-6:
        if e2[2] < 0:
            e2 = -e2
    else:
        k = 0 if abs(e2[0]) >= abs(e2[1]) else 1
        if e2[k] < 0:
            e2 = -e2
    return e1, e2, _unit(np.cross(e1, e2))


def _dominant_normal(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    """The normal of the largest flat face (by area): a plate's face, exactly."""
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    cr = np.cross(b - a, c - a)
    ar = np.linalg.norm(cr, axis=1)
    ok = ar > 1e-9
    nrm = cr[ok] / ar[ok, None]
    # fold +/- together
    k = np.argmax(np.abs(nrm), axis=1)
    sgn = np.sign(nrm[np.arange(len(nrm)), k])
    nrm = nrm * sgn[:, None]
    keys = np.round(nrm, 3)
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    tot = np.bincount(inv.ravel(), weights=ar[ok])
    best = int(np.argmax(tot))
    return _unit((nrm[inv.ravel() == best] * ar[ok][inv.ravel() == best, None]).sum(0))


def _reference_edge_dir(v: np.ndarray, f: np.ndarray, e1: np.ndarray, max_deg: float = 8.0) -> np.ndarray:
    """The set-out direction of a member: its straight flange, not the PCA mean.

    A tapered rafter's PCA axis sits between its flanges; the shop sets out
    from a straight flange. Only FOLD edges count (a long face's triangulation
    diagonal is not an edge). The fold edges within ``max_deg`` of ``e1`` are
    grouped by direction and the group with the most length wins; among groups
    within 15 % of the best, a near-vertical member takes the most vertical
    (a column's outer flange) and any other the highest (a rafter's top flange).
    """
    from .hlr import feature_edges

    V, _F, _N, feat, _s, _st = feature_edges(v, f)
    if not len(feat):
        return e1
    d = V[feat[:, 1]] - V[feat[:, 0]]
    L = np.linalg.norm(d, axis=1)
    ok = L > 1e-6
    feat, d, L = feat[ok], d[ok], L[ok]
    u = d / L[:, None]
    u = u * np.sign(u @ e1)[:, None]
    cand = (u @ e1) >= math.cos(math.radians(max_deg))
    if not cand.any():
        return e1
    u, L, mid = u[cand], L[cand], (V[feat[cand, 0]] + V[feat[cand, 1]]) / 2
    groups: list[list[int]] = []
    for i in np.argsort(-L):
        for g in groups:
            if u[g[0]] @ u[i] >= math.cos(math.radians(0.3)):
                g.append(i)
                break
        else:
            groups.append([i])
    tot = [L[g].sum() for g in groups]
    best = max(tot)
    near = [g for g, t in zip(groups, tot) if t >= 0.85 * best]
    if abs(e1[2]) > 0.7:
        g = max(near, key=lambda g: abs(u[g[0]][2]))
    else:
        g = max(near, key=lambda g: (mid[g, 2] * L[g]).sum() / L[g].sum())
    return _unit((u[g] * L[g, None]).sum(0))


def _depth_dir(v: np.ndarray, f: np.ndarray, e1: np.ndarray) -> np.ndarray | None:
    """The section's depth direction (unit, perpendicular to ``e1``), or None for a round section.

    The flat faces along the member are grouped by normal (12 deg); the group whose faces lie
    furthest apart along its normal is the flanges (an I, H, C, Z: flanges a depth apart, the
    web's two faces a web thickness apart; an RHS: the deeper pair; a tapered member: its two
    flanges, a few degrees apart, still group). That normal is the depth.
    """
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    cr = np.cross(b - a, c - a)
    ar = np.linalg.norm(cr, axis=1)
    ok = ar > 1e-6
    nrm, ar, cen = cr[ok] / ar[ok, None], ar[ok] / 2, ((a + b + c) / 3)[ok]
    side = np.abs(nrm @ e1) < 0.2
    nrm, ar, cen = nrm[side], ar[side], cen[side]
    if not len(nrm):
        return None
    nrm = nrm - np.outer(nrm @ e1, e1)
    nrm /= np.linalg.norm(nrm, axis=1)[:, None]
    tot = ar.sum()
    groups: list[list[int]] = []
    reps: list[np.ndarray] = []
    cos12 = math.cos(math.radians(12))
    for i in np.argsort(-ar):
        for g, r in zip(groups, reps):
            if abs(nrm[i] @ r) >= cos12:
                g.append(i)
                break
        else:
            groups.append([i])
            reps.append(nrm[i])
    best, best_spread = None, -1.0
    for g, r in zip(groups, reps):
        if ar[g].sum() < 0.03 * tot:
            continue
        # the group's planes (own normal + offset), each with its area and area-weighted centroid
        planes: dict[tuple, list] = {}
        for i in g:
            key = tuple(np.round(nrm[i], 2)) + (round(float(cen[i] @ nrm[i]) * 2) / 2,)
            pl = planes.setdefault(key, [0.0, np.zeros(3)])
            pl[0] += ar[i]
            pl[1] += ar[i] * cen[i]
        big = max(pl[0] for pl in planes.values())
        offs = [float((pl[1] / pl[0]) @ r) for pl in planes.values() if pl[0] >= 0.5 * big]
        spread = max(offs) - min(offs)
        if spread > best_spread + 1e-6:
            best, best_spread = r, spread
    if best is None or len(groups) > 40:          # a faceted round section
        return None
    return _unit(best - (best @ e1) * e1)


def _snap(x: np.ndarray, tol: float = 2e-4) -> np.ndarray:
    y = np.where(np.abs(x) < tol, 0.0, x)
    return _unit(y)


def principal_frame(v: np.ndarray, plate_hint: bool | None = None, f: np.ndarray | None = None):
    """``(origin, e1, e2, n), (L, W, T), is_plate`` from the mesh alone.

    e1 = the longest direction, n = the thinnest; for a profile the cross-section's
    long side is e2 (an I section is seen on its web), for a plate the in-plane
    short side is e2. With faces given, a plate's normal is its largest face's
    and a member's axis its longest straight edge.
    """
    c = v.mean(0)
    w, vec = np.linalg.eigh(np.cov((v - c).T) + np.eye(3) * 1e-9)
    axes = [vec[:, i] for i in np.argsort(w)[::-1]]
    spans = [float(np.ptp((v - c) @ a)) for a in axes]
    # plate: one span much smaller than the others
    thin = int(np.argmin(spans))
    s_sorted = sorted(spans)
    is_plate = plate_hint if plate_hint is not None else (s_sorted[0] <= 60 and s_sorted[0] < 0.25 * s_sorted[1])
    if is_plate:
        n = _snap(_dominant_normal(v, f)) if f is not None else axes[thin]
        b1 = _unit(np.cross(n, [0, 0, 1] if abs(n[2]) < 0.9 else [1, 0, 0]))
        b2 = np.cross(n, b1)
        p2 = np.stack([(v - c) @ b1, (v - c) @ b2], 1)
        d = _min_rect_axis(p2)
        e1 = _unit(d[0] * b1 + d[1] * b2)
        e2 = np.cross(n, e1)
    else:
        e1 = axes[int(np.argmax(spans))]
        if f is not None:
            e1 = _snap(_reference_edge_dir(v, f, e1))
        dd = _depth_dir(v, f, e1) if f is not None else None
        if dd is not None:
            e2 = _snap(dd)
        else:
            b1 = _unit(np.cross(e1, [0, 0, 1] if abs(e1[2]) < 0.9 else [1, 0, 0]))
            b2 = np.cross(e1, b1)
            p2 = np.stack([(v - c) @ b1, (v - c) @ b2], 1)
            d = _min_rect_axis(p2)
            e2 = _unit(d[0] * b1 + d[1] * b2)
    e1, e2, n = _orient(e1, e2)
    if is_plate:
        # keep n the plate normal after the sign games
        pass
    P = (v - c)
    lo = np.array([P @ e1, P @ e2, P @ n]).min(1)
    hi = np.array([P @ e1, P @ e2, P @ n]).max(1)
    origin = c + lo[0] * e1 + lo[1] * e2 + lo[2] * n
    ext = tuple(float(x) for x in (hi - lo))
    if is_plate and ext[1] > ext[0]:
        e1, e2 = e2, -e1
        e1, e2, n = _orient(e1, e2)
        lo = np.array([P @ e1, P @ e2, P @ n]).min(1)
        hi = np.array([P @ e1, P @ e2, P @ n]).max(1)
        origin = c + lo[0] * e1 + lo[1] * e2 + lo[2] * n
        ext = tuple(float(x) for x in (hi - lo))
    return (origin, e1, e2, n), ext, bool(is_plate)


# ---------------------------------------------------------------- holes
def _axis2placement_matrix(p) -> np.ndarray:
    if p is None:
        return np.eye(4)
    return np.asarray(up.get_axis2placement(p), dtype=float)


def round_holes(model, part, unit_mm: float) -> list[Hole]:
    out = []
    for rel in getattr(part, "HasOpenings", None) or ():
        o = rel.RelatedOpeningElement
        rep = o.Representation
        items = [i for r in (rep.Representations if rep else ()) if r.RepresentationIdentifier == "Body" for i in r.Items]
        if not items or not all(i.is_a("IfcExtrudedAreaSolid") and i.SweptArea.is_a("IfcCircleProfileDef")
                                for i in items):
            continue
        M = np.asarray(up.get_local_placement(o.ObjectPlacement), dtype=float)
        M[:3, 3] *= unit_mm
        for it in items:
            S = _axis2placement_matrix(it.Position)
            S[:3, 3] *= unit_mm
            P = np.eye(4)
            if it.SweptArea.Position is not None:
                p2 = it.SweptArea.Position
                loc = p2.Location.Coordinates
                P[0, 3], P[1, 3] = loc[0] * unit_mm, loc[1] * unit_mm
            W = M @ S @ P
            dirn = np.asarray(it.ExtrudedDirection.DirectionRatios, dtype=float)
            depth = float(it.Depth) * unit_mm
            start = W[:3, 3]
            axis = _unit(W[:3, :3] @ _unit(dirn))
            out.append(Hole(center=start + axis * depth / 2.0, axis=axis, d=2.0 * float(it.SweptArea.Radius) * unit_mm,
                            depth=depth, gid=o.GlobalId))
    return out


def clip_hole_to_part(h: Hole, verts: np.ndarray) -> Hole:
    """Shrink a hole to the part's own extent along its axis (openings are often modelled long)."""
    t = (verts - h.center) @ h.axis
    r = np.linalg.norm((verts - h.center) - np.outer(t, h.axis), axis=1)
    near = t[r <= h.d / 2.0 + 0.6]
    if near.size == 0:
        return h
    lo, hi = max(near.min(), -h.depth / 2), min(near.max(), h.depth / 2)
    if hi <= lo:
        return h
    return Hole(center=h.center + h.axis * (lo + hi) / 2.0, axis=h.axis, d=h.d, depth=float(hi - lo), gid=h.gid)


# ---------------------------------------------------------------- the model
def read_model(ifc_path, *, policy: str = "any", marks: list[str] | None = None,
               density: float = STEEL_DENSITY_KG_M3) -> FabModel:
    """Read ``ifc_path`` into a :class:`FabModel` (assemblies and parts, mm, kg)."""
    if policy not in ("any", "strict"):
        raise ValueError("policy is 'any' or 'strict'")
    model = ifcopenshell.open(str(ifc_path))
    unit_mm = uu.calculate_unit_scale(model) * 1000.0
    warnings: list[str] = []
    proj = model.by_type("IfcProject")
    project = (proj[0].LongName or proj[0].Name or "") if proj else ""

    all_parts = ifcmodel.parts(model)
    part_asm: dict[int, object] = {}
    instances: dict[str, list] = defaultdict(list)
    asm_src: dict[str, str] = {}
    asm_name: dict[str, str] = {}
    for a in model.by_type("IfcElementAssembly"):
        ps = ifcmodel.assembly_parts(a)
        if not ps:
            continue
        mk, src = resolve_mark(a, "assembly", policy)
        if not mk:
            mk, src = f"?{a.GlobalId[:8]}", "none"
            warnings.append(f"assembly {a.GlobalId} has no mark")
        instances[mk].append(a)
        asm_src.setdefault(mk, src)
        asm_name.setdefault(mk, _pset(a, NAME_KEYS)[0] or (a.ObjectType or "") or (a.Name or ""))
        for p in ps:
            part_asm[p.id()] = a
    # a loose part (not in any assembly) is its own assembly
    for p in all_parts:
        if p.id() in part_asm:
            continue
        mk, src = resolve_mark(p, "assembly", policy)
        if not mk or policy == "strict":
            mk, src = resolve_mark(p, "part", policy)
            mk = mk.upper() if mk else f"?{p.GlobalId[:8]}"
        instances[mk].append(p)
        asm_src.setdefault(mk, src or "none")
        asm_name.setdefault(mk, _pset(p, NAME_KEYS)[0] or p.ObjectType or p.Name or "")

    wanted = set(marks) if marks else None
    reps = {mk: insts[0] for mk, insts in instances.items() if wanted is None or mk in wanted}

    def parts_of(inst):
        return ifcmodel.assembly_parts(inst) if inst.is_a("IfcElementAssembly") else [inst]

    # geometry for every part of every instance (allocation needs all instances' marks, the
    # drawings only the representative's geometry)
    rep_parts = [p for inst in reps.values() for p in parts_of(inst)]
    meshes = _meshes(model, rep_parts)

    def build_part(p) -> Part | None:
        mk, src = resolve_mark(p, "part", policy)
        mesh = meshes.get(p.id())
        if mesh is None:
            warnings.append(f"part {mk or p.GlobalId} has no body geometry - skipped")
            return None
        v, f = mesh
        mat = ue.get_material(p, should_inherit=True)
        grade = getattr(mat, "Name", None) or ""
        if not grade and mat is not None and mat.is_a("IfcMaterialProfileSetUsage"):
            grade = getattr(mat.ForProfileSet.MaterialProfiles[0].Material, "Name", "") or ""
        grade = grade or _pset(p, GRADE_KEYS)[0]
        frame, ext, is_plate = principal_frame(v, plate_hint=True if p.is_a("IfcPlate") else None, f=f)
        vol, area = mesh_volume_area(v, f)
        w_pset = (ue.get_psets(p).get("BSA_Fabrication") or {}).get("Weight_kg")
        weight, wsrc = (float(w_pset), "BSA_Fabrication.Weight_kg") if w_pset else (vol * 1e-9 * density, f"volume x {density:g}")
        holes = [clip_hole_to_part(h, v) for h in round_holes(model, p, unit_mm)]
        return Part(gid=p.GlobalId, mark=mk, mark_src=src, ifc_class=p.is_a(), name=p.Name or "",
                    profile=_pset(p, PROFILE_KEYS)[0] or (p.ObjectType or ""), grade=grade,
                    is_main=ifcmodel.pset_value(p, "IsMainPart"), verts=v, tris=f, holes=holes, frame=frame,
                    ext=ext, is_plate=is_plate, volume=vol, area=area, weight=weight, weight_src=wsrc)

    assemblies = []
    for mk in sorted(reps, key=_natural):
        inst = reps[mk]
        ps = [x for x in (build_part(p) for p in parts_of(inst)) if x is not None]
        if not ps:
            continue
        mains = [p for p in ps if p.is_main is True]
        main = mains[0] if len(mains) == 1 else max(ps, key=lambda p: p.volume)
        if len(mains) != 1 and len(ps) > 1:
            warnings.append(f"assembly {mk}: {len(mains)} parts flagged main - the largest is used")
        frame = main.frame
        if not main.is_plate:
            o, e1, e2, n = main.frame
            frame = (o, e1, e2, n)
        assemblies.append(Assembly(mark=mk, mark_src=asm_src.get(mk, ""), name=asm_name.get(mk, ""),
                                   gids=[i.GlobalId for i in instances[mk]], parts=ps, main=main, frame=frame))
        # same mark, different steel?
        if len(instances[mk]) > 1:
            n_parts = Counter(len(parts_of(i)) for i in instances[mk])
            if len(n_parts) > 1:
                warnings.append(f"assembly {mk}: instances have different part counts {dict(n_parts)}")

    parts_by_mark: dict[str, list[Part]] = defaultdict(list)
    allocation: dict[str, Counter] = defaultdict(Counter)
    for a in assemblies:
        for p in a.parts:
            parts_by_mark[p.mark].append(p)
            allocation[p.mark][a.mark] += a.qty
    for pm, ps in parts_by_mark.items():
        fp = Counter(p.fingerprint()[:4] for p in ps)
        if len(fp) > 1:
            warnings.append(f"part mark {pm or '(blank)'}: {len(fp)} different geometries share the mark {list(fp)}")
    return FabModel(ifc_path=str(ifc_path), schema=model.schema, project=project, assemblies=assemblies,
                    parts_by_mark=dict(parts_by_mark), allocation=dict(allocation), warnings=warnings, policy=policy)


def _natural(s: str):
    import re

    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
