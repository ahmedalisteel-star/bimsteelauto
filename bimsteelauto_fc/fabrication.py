"""
bimsteelauto_fc.fabrication
===========================

Where each piece is BUILT, as opposed to where it was ANALYSED.

The engine's frame is a stick model: a column is a line from the base node
to the eave node, a rafter is a line from the eave node to the ridge, and a
purlin is a point on that line.  Put a solid on every line and every joint
in the building becomes two members occupying the same steel - the first
adapter did exactly that, and REF-2026-001 came out with 652 hard clashes:
purlins through rafters, cleats inside purlins, columns and rafters
overlapping at the knee, end plates inside the members, girts and eave
struts through the columns, gussets through everything.

This module turns the stick model into the shop model.  It decides nothing
about strength - every size still comes off the ``DesignResult`` - only
where each part goes so that parts which are bolted together TOUCH and
nothing else meets.  The conventions are the ones the REF-2026-001 shop
drawings were made to (``03_FreeCAD_BIM/ph_bim_model.py``, 0 clashes):

* **Grid on the outer face of the column flange.**  An exterior column's
  outer flange is vertical on the grid line and its depth runs inward; the
  inner flange carries the taper.  An interior column is centred on its
  grid.
* **Eave height to the top of steel.**  The rafter's top flange outer face
  lies on the engine's analysis line - through the eave node and the ridge
  node - so the eave height is the intersection of that line with the outer
  column face, the PEB definition.
* **Constant-depth knee zone.**  Over the height of the eave end plate (and
  ``KNEE_RUNOUT`` below it) the column's inner flange is vertical at the
  column's top depth, so the plate bolts to a flat flange.
* **The rafter starts at the end plate face.**  The plate sits on the
  column's inner flange; the rafter is cut vertically and starts at the
  plate's far face.  At the ridge one plate is centred on the ridge line
  and each rafter stops at its face.
* **Purlins stand on their cleats.**  The cleat is welded to the rafter top
  flange and stands square to the roof; the purlin's bottom flange sits on
  the top flange with its web against the cleat.  Everything is set out in
  slope coordinates: ``s`` along the top of steel from the analysis node,
  ``n`` normal to it.
* **Girts and eave struts outside the column on clips, web horizontal.**
  ``GAP`` clear of the outer flange, carried on a clip plate under the web.
* **Rods stop at the web faces.**  A rod brace passes through a hole in the
  member web and bears on a rod plate sized to the rod; the two diagonals of
  an X are offset so they pass each other instead of meeting.

The numbers in this file are CLEARANCES and set-out rules - how far apart
two parts are - never a size that a limit state depends on.  Each one is a
named constant with the reason next to it.

Nothing here imports a solver; the only engine imports are the pure
geometry ones, which is the layer rule tests/test_fc_adapter.py checks.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import FreeCAD as App
import Part

from bimsteelauto.spec import ConnectionType, MemberType

__all__ = [
    "GAP",
    "KNEE_RUNOUT",
    "ROD_CLEAR",
    "FramePlan",
    "ColumnPlan",
    "RafterPlan",
    "JointPlan",
    "RodEnd",
    "plan_frame",
    "setout",
    "cf_outline",
    "i_outline",
    "rod_plate_geometry",
]

#: mm between two parts that are bolted to a common part but not to each
#: other - a girt and the column flange it is clipped to, two purlins'
#: worth of nothing at a joint. Large enough that a clash check never
#: reports touching steel, small enough to vanish in a drawing.
GAP = 5.0

#: mm the constant-depth knee zone runs below the bottom of the eave end
#: plate, so the plate's bottom bolt row is not on the kink of the flange.
KNEE_RUNOUT = 30.0

#: mm from a rafter's end plate face (or a column's base) to the first rod
#: hole, so the hole and its rod plate clear the plate, the weld and the
#: base.
ROD_CLEAR = 150.0

#: mm of clear web between a rod plate and the flange it sits beside.
_FLANGE_CLEAR = 10.0


# ---------------------------------------------------------------------------
# Set-out
# ---------------------------------------------------------------------------


def setout(length: float, spacing: float) -> list[float]:
    """Positions ``k * spacing`` for ``k = 1, 2, ...`` strictly inside a
    member of ``length``.

    The ONE place purlin and girt positions are counted, used both by
    ``builder._run_lines`` and by the plan here, so the cleats and the
    members they carry cannot disagree about how many there are.
    """
    if spacing <= 0:
        return []
    n = max(1, int(math.floor(length / spacing)))
    out = []
    for k in range(1, n + 1):
        s = k * spacing
        if s >= length - 1e-6:
            break
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Profiles, in their own 2-D frame
# ---------------------------------------------------------------------------


def i_outline(d, tw, bt, tt, bb, tb) -> list[tuple[float, float]]:
    """A welded I as twelve ``(u, v)`` points, ``v`` from 0 (the "bottom"
    flange's outer face) to ``d`` (the "top" flange's outer face) and ``u``
    across the flanges, centred on the web."""
    return [
        (-bb / 2.0, 0.0), (+bb / 2.0, 0.0), (+bb / 2.0, tb), (+tw / 2.0, tb),
        (+tw / 2.0, d - tt), (+bt / 2.0, d - tt), (+bt / 2.0, d),
        (-bt / 2.0, d), (-bt / 2.0, d - tt), (-tw / 2.0, d - tt),
        (-tw / 2.0, tb), (-bb / 2.0, tb),
    ]


def cf_outline(shape: str, H: float, B: float, D: float, t: float
               ) -> list[tuple[float, float]]:
    """A lipped C or Z as one closed ``(s, n)`` polygon.

    ``n`` runs up the web from 0 to ``H``; ``s`` runs across it, with the
    web occupying ``0 <= s <= t``.  A C has both flanges toward ``+s``; a Z
    has its ``n = 0`` flange toward ``+s`` and its ``n = H`` flange toward
    ``-s``.  So the ``s = 0`` face of the web is the face that goes against
    a cleat, and the ``n = 0`` flange is the one that sits on the support.

    The real profile rather than its bounding box: the box was a good
    enough stand-in while every member was on its centreline, and a solid
    200 x 60 block is not something a purlin cleat can stand beside.
    """
    D = min(D, H / 2.0)
    if shape.upper() == "Z":
        return [
            (0.0, 0.0), (B, 0.0), (B, D), (B - t, D), (B - t, t), (t, t),
            (t, H), (-B + t, H), (-B + t, H - D), (-B + 2 * t, H - D),
            (-B + 2 * t, H - t), (0.0, H - t),
        ]
    return [
        (0.0, 0.0), (B, 0.0), (B, D), (B - t, D), (B - t, t), (t, t),
        (t, H - t), (B - t, H - t), (B - t, H - D), (B, H - D), (B, H),
        (0.0, H),
    ]


_CF = re.compile(
    r"^(?P<shape>[CZ])(?P<H>[\d.]+)x(?P<B>[\d.]+)x(?P<D>[\d.]+)x(?P<t>[\d.]+)$",
    re.IGNORECASE,
)


def cf_dims(name: str):
    """``(shape, H, B, D, t)`` from ``C200x60x20x2.5``, or ``None``."""
    m = _CF.match(str(name).strip())
    if not m:
        return None
    return (m.group("shape").upper(), float(m.group("H")), float(m.group("B")),
            float(m.group("D")), float(m.group("t")))


def rod_plate_geometry(rod_diameter: float) -> dict:
    """The plate a threaded rod brace bears on at each end, sized to the rod.

    A rod brace is not bolted to a gusset: it passes through a hole in the
    member web and is nutted on a plate (a hillside washer, in the shop).
    The engine's ``GP1`` is laid out for a bolted gusset - 4 x M20 in a
    500 x 600 x 12 plate - which no rod brace has, and which drawn at every
    rod end both clashed with everything near it and put about 28 kg of
    plate per rod end into the take-off.

    The size here is a set-out rule, not a design: hole ``d + 2`` (a
    standard hole), edge ``1.5 d`` each side, rounded up to 10 mm; thickness
    ``d / 2`` rounded up to 2 mm and not less than 6. It is reported as
    such in the build report, because no limit state ran on it.
    """
    d = float(rod_diameter)
    hole = d + 2.0
    side = math.ceil((hole + 3.0 * d) / 10.0) * 10.0
    t = max(6.0, math.ceil(d / 4.0) * 2.0)
    mass = (side * side - math.pi * hole * hole / 4.0) * t * 7850.0 / 1e9
    return {
        "kind": "bracing_gusset", "available": True,
        "width_mm": side, "height_mm": side, "thickness_mm": t,
        "hole_diameter_mm": hole, "n_holes": 1, "holes_uv_mm": [(0.0, 0.0)],
        "mass_kg": mass, "rod_diameter_mm": d,
    }


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass
class ColumnPlan:
    """One column as built, in the frame's own ``(x, z)`` plane."""

    mark: str
    member_type: MemberType
    taper: object
    x_grid: float
    sigma: int                  # +1 / -1: the side the depth runs to; 0 = centred
    z_base: float
    z_top: float
    z_knee: float               # inner flange vertical above this (exterior only)
    node_top: str

    @property
    def exterior(self) -> bool:
        return self.sigma != 0

    def depth_at(self, z: float) -> float:
        d0, d1 = self.taper.start.d, self.taper.end.d
        if not self.exterior:
            f = (z - self.z_base) / max(self.z_top - self.z_base, 1e-9)
            return d0 + (d1 - d0) * min(max(f, 0.0), 1.0)
        if z >= self.z_knee:
            return d1
        f = (z - self.z_base) / max(self.z_knee - self.z_base, 1e-9)
        return d0 + (d1 - d0) * min(max(f, 0.0), 1.0)

    @property
    def outer(self):
        """``(bf, tf)`` of the outer flange - the section's ``top`` flange."""
        s = self.taper.start
        return s.bf_top, s.tf_top

    @property
    def inner(self):
        s = self.taper.start
        return s.bf_bot, s.tf_bot

    @property
    def tw(self) -> float:
        return self.taper.start.tw

    @property
    def length(self) -> float:
        return self.z_top - self.z_base


@dataclass
class RafterPlan:
    """One rafter as built.

    The top flange outer face lies on the analysis line through ``i`` and
    ``j``; ``xa`` and ``xb`` are the vertical end cuts at the ``i`` and
    ``j`` ends.
    """

    mark: str
    taper: object
    i: tuple[float, float]
    j: tuple[float, float]
    node_i: str
    node_j: str
    xa: float = 0.0
    xb: float = 0.0

    @property
    def dirx(self) -> float:
        return 1.0 if self.j[0] >= self.i[0] else -1.0

    @property
    def axis_length(self) -> float:
        return math.hypot(self.j[0] - self.i[0], self.j[1] - self.i[1])

    @property
    def cos(self) -> float:
        return abs(self.j[0] - self.i[0]) / self.axis_length

    def z_top(self, x: float) -> float:
        (xi, zi), (xj, zj) = self.i, self.j
        return zi + (x - xi) * (zj - zi) / (xj - xi)

    def s_of_x(self, x: float) -> float:
        return (x - self.i[0]) * self.dirx / self.cos

    def x_of_s(self, s: float) -> float:
        return self.i[0] + self.dirx * s * self.cos

    @property
    def a(self) -> tuple[float, float]:
        """Unit vector along the top of steel, ``i`` to ``j``."""
        L = self.axis_length
        return ((self.j[0] - self.i[0]) / L, (self.j[1] - self.i[1]) / L)

    @property
    def n(self) -> tuple[float, float]:
        """Unit normal to the top of steel, pointing up out of the roof."""
        ax, az = self.a
        nx, nz = -az, ax
        if nz < 0:
            nx, nz = -nx, -nz
        return nx, nz

    def point(self, s: float, n: float = 0.0) -> tuple[float, float]:
        """Frame-plane ``(x, z)`` of slope coordinates ``(s, n)``."""
        x = self.x_of_s(s)
        nx, nz = self.n
        return x + nx * n, self.z_top(x) + nz * n

    def depth_at(self, x: float) -> float:
        """Section depth (normal to the flanges) at ``x``, varying linearly
        between the two cuts - the start section at ``xa``, the end one at
        ``xb``."""
        d0, d1 = self.taper.start.d, self.taper.end.d
        if abs(self.xb - self.xa) < 1e-9:
            return d0
        f = (x - self.xa) / (self.xb - self.xa)
        return d0 + (d1 - d0) * min(max(f, 0.0), 1.0)

    def z_bot(self, x: float) -> float:
        return self.z_top(x) - self.depth_at(x) / self.cos

    @property
    def length(self) -> float:
        return abs(self.xb - self.xa) / self.cos


@dataclass
class JointPlan:
    """Where one end plate goes.

    ``x_mid`` is the plate's mid-thickness and ``z_datum`` the top flange
    outer face of the rafter it belongs to, at the plate - the layout's own
    ``v = 0``.
    """

    node: str
    kind: str                     # "knee" | "centred"
    x_mid: float
    z_datum: float
    conn: object = None
    geom: dict | None = None


@dataclass
class RodEnd:
    """One end of a rod brace: where its plate goes."""

    rod_mark: str
    rod_diameter: float
    centre: App.Vector            # plate centre (mid-thickness)
    e_u: App.Vector
    e_v: App.Vector


@dataclass
class FramePlan:
    columns: list[ColumnPlan] = field(default_factory=list)
    rafters: list[RafterPlan] = field(default_factory=list)
    joints: dict[str, JointPlan] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def column_at_node(self, node: str) -> ColumnPlan | None:
        return next((c for c in self.columns if c.node_top == node), None)

    def exterior_columns(self) -> list[ColumnPlan]:
        return [c for c in self.columns if c.exterior]


def _end_plates(result, layout) -> dict[str, tuple[object, dict]]:
    """``node -> (connection, geometry)`` for every end plate with geometry,
    paired exactly the way ``builder._build_details`` pairs them."""
    from . import detailing as dt

    conns = list(getattr(result, "connections", ()) or ())
    out: dict[str, tuple[object, dict]] = {}
    for ct, nodes in (
        (ConnectionType.EAVE_END_PLATE, list(layout.eave_nodes)),
        (ConnectionType.APEX_END_PLATE, list(layout.ridge_nodes)),
    ):
        mine = [c for c in conns if c.connection_type is ct]
        for conn, node in zip(mine, nodes):
            geom = dt.geometry_of(conn)
            if geom is None or node in out:
                continue
            out[node] = (conn, geom)
    return out


def plan_frame(result, layout) -> FramePlan:
    """The built position of every frame member and end plate on one frame
    line.  Pure geometry in the frame plane; ``y`` is added by the caller.
    """
    plan = FramePlan()
    plates = _end_plates(result, layout)
    nodes = layout.nodes

    # -- columns: which node is at the top of which --------------------------
    xs = sorted({round(nodes[n][0], 6) for n in layout.base_nodes})
    x_min, x_max = xs[0], xs[-1]
    col_members = [m for m in layout.members
                   if m.member_type in (MemberType.COLUMN,
                                        MemberType.INTERIOR_COLUMN)]
    for fm, base, top in zip(col_members, layout.base_nodes, layout.eave_nodes):
        x = nodes[base][0]
        if abs(x - x_min) < 1e-6:
            sigma = +1
        elif abs(x - x_max) < 1e-6:
            sigma = -1
        else:
            sigma = 0
        plan.columns.append(ColumnPlan(
            mark=fm.mark, member_type=fm.member_type, taper=fm.taper,
            x_grid=x, sigma=sigma, z_base=nodes[base][1],
            z_top=nodes[top][1], z_knee=nodes[top][1], node_top=top,
        ))

    # The node names a rafter runs between. FrameMember carries points, not
    # names, so they are matched back by position.
    def node_named(pt):
        for name, xy in nodes.items():
            if abs(xy[0] - pt[0]) < 1e-6 and abs(xy[1] - pt[1]) < 1e-6:
                return name
        return ""

    for fm in layout.members:
        if fm.member_type is not MemberType.RAFTER:
            continue
        plan.rafters.append(RafterPlan(
            mark=fm.mark, taper=fm.taper, i=tuple(fm.i_point),
            j=tuple(fm.j_point), node_i=node_named(fm.i_point),
            node_j=node_named(fm.j_point),
        ))

    # -- the rafter ends, and the plate at each -----------------------------
    def rafter_end(rp: RafterPlan, at_i: bool) -> float:
        node = rp.node_i if at_i else rp.node_j
        nx = (rp.i if at_i else rp.j)[0]
        # +1 when the rafter runs away from this node toward +x.
        away = rp.dirx if at_i else -rp.dirx
        conn, geom = plates.get(node, (None, None))
        t = float(geom["thickness_mm"]) if geom else 0.0
        col = plan.column_at_node(node)
        if col is not None and col.exterior:
            face = col.x_grid + col.sigma * col.taper.end.d
            x_end = face + col.sigma * (t if geom else GAP)
            if node not in plan.joints:
                plan.joints[node] = JointPlan(
                    node=node, kind="knee", x_mid=face + col.sigma * t / 2.0,
                    z_datum=rp.z_top(x_end), conn=conn, geom=geom,
                )
            return x_end
        # A ridge, or a valley over an interior column: one plate centred on
        # the node, each rafter stopping at its face.
        half = t / 2.0 if geom else GAP / 2.0
        x_end = nx + away * half
        if node not in plan.joints:
            plan.joints[node] = JointPlan(
                node=node, kind="centred", x_mid=nx,
                z_datum=rp.z_top(x_end), conn=conn, geom=geom,
            )
        return x_end

    for rp in plan.rafters:
        rp.xa = rafter_end(rp, True)
        rp.xb = rafter_end(rp, False)

    # -- column tops and knee zones ----------------------------------------
    for col in plan.columns:
        jp = plan.joints.get(col.node_top)
        here = [rp for rp in plan.rafters
                if col.node_top in (rp.node_i, rp.node_j)]
        if col.exterior:
            rp = here[0] if here else None
            if jp is not None and jp.geom is not None:
                g = jp.geom
                top = jp.z_datum + float(g["ext_top_mm"])
                bot = jp.z_datum - (float(g["depth_on_cut_mm"])
                                    + float(g["ext_bot_mm"]))
            elif rp is not None:
                x_end = rp.xa if rp.node_i == col.node_top else rp.xb
                top = rp.z_top(x_end)
                bot = rp.z_bot(x_end)
            else:                                       # pragma: no cover
                continue
            col.z_top = top
            col.z_knee = max(col.z_base + 0.25 * (top - col.z_base),
                             bot - KNEE_RUNOUT)
        else:
            # Interior: stops under the lowest rafter bottom (or plate) it
            # carries, across its own flange width.
            lows = []
            half = col.taper.end.d / 2.0
            for rp in here:
                x_end = rp.xa if rp.node_i == col.node_top else rp.xb
                lows.append(rp.z_bot(x_end))
                for x in (col.x_grid - half, col.x_grid + half):
                    lo, hi = sorted((rp.xa, rp.xb))
                    if lo <= x <= hi:
                        lows.append(rp.z_bot(x))
            if jp is not None and jp.geom is not None:
                lows.append(jp.z_datum - (float(jp.geom["depth_on_cut_mm"])
                                          + float(jp.geom["ext_bot_mm"])))
            if lows:
                col.z_top = min(lows) - GAP
                col.z_knee = col.z_top
            plan.notes.append(
                f"{col.mark} is an interior column: built centred on its "
                "grid and stopped under the rafters it carries; the cap and "
                "valley connection are not detailed."
            )
    return plan


# ---------------------------------------------------------------------------
# Solids
# ---------------------------------------------------------------------------

V = App.Vector


def _wire(points) -> Part.Wire:
    pts = [V(p) for p in points]
    pts.append(V(pts[0]))
    return Part.makePolygon(pts)


def _section(origin: V, e_u: V, e_v: V, uv) -> Part.Wire:
    return _wire([origin + V(e_u) * u + V(e_v) * v for u, v in uv])


def _loft(wires) -> Part.Shape:
    solid = Part.makeLoft(wires, True, True)
    if not solid.isValid():
        solid = solid.removeSplitter()
    return solid


def column_solid(col: ColumnPlan, y: float) -> Part.Shape:
    """A column on frame line ``y``: outer flange on the grid, inner flange
    tapered up to the knee zone and vertical through it."""
    e_u = V(0, 1, 0)
    s0, s1 = col.taper.start, col.taper.end
    if col.exterior:
        e_v = V(col.sigma, 0, 0)
        (bo, to), (bi, ti) = col.outer, col.inner

        def sec(z, d):
            return _section(V(col.x_grid, y, z), e_u, e_v,
                            i_outline(d, col.tw, bi, ti, bo, to))

        levels = [(col.z_base, s0.d)]
        if col.z_knee < col.z_top - 1.0:
            levels.append((col.z_knee, s1.d))
        levels.append((col.z_top, s1.d))
        return _loft([sec(z, d) for z, d in levels])

    e_v = V(1, 0, 0)

    def csec(z, d):
        return _section(V(col.x_grid - d / 2.0, y, z), e_u, e_v,
                        i_outline(d, s0.tw, s0.bf_bot, s0.tf_bot,
                                  s0.bf_top, s0.tf_top))

    return _loft([csec(col.z_base, s0.d), csec(col.z_top, s1.d)])


def rafter_solid(rp: RafterPlan, y: float) -> Part.Shape:
    """A rafter on frame line ``y``, cut vertically at both ends, its top
    flange on the top-of-steel line and its depth hanging below it."""
    s = rp.taper.start
    c = rp.cos
    e_u, e_v = V(0, 1, 0), V(0, 0, 1)

    def sec(x):
        dv = rp.depth_at(x) / c
        return _section(V(x, y, rp.z_top(x) - dv), e_u, e_v,
                        i_outline(dv, s.tw, s.bf_top, s.tf_top / c,
                                  s.bf_bot, s.tf_bot / c))

    return _loft([sec(rp.xa), sec(rp.xb)])


def _xz_prism(points_xz, y0: float, y1: float) -> Part.Shape:
    face = Part.Face(_wire([V(x, y0, z) for x, z in points_xz]))
    solid = face.extrude(V(0, y1 - y0, 0))
    if not solid.Solids:                               # pragma: no cover
        raise ValueError("the extruded profile is not a solid")
    return solid


def purlin_solid(rp: RafterPlan, s_web: float, outline, y0, y1) -> Part.Shape:
    """A cold-formed purlin standing on the rafter: the ``s = 0`` web face
    at slope position ``s_web``, the ``n = 0`` flange on the top flange."""
    ax, az = rp.a
    nx, nz = rp.n
    x0, z0 = rp.point(s_web, 0.0)
    pts = [(x0 + ax * s + nx * n, z0 + az * s + nz * n) for s, n in outline]
    return _xz_prism(pts, y0, y1)


def wall_solid(col: ColumnPlan, z_web: float, outline, y0, y1,
               gap: float = GAP) -> Part.Shape:
    """A member outside an exterior column with its web HORIZONTAL - a girt
    or an eave strut.  ``outline`` is the profile in ``(s, n)``: ``n`` (the
    depth) runs outward from ``gap`` off the column face and ``s`` (across
    the web) runs up from ``z_web``."""
    pts = [(col.x_grid - col.sigma * (gap + n), z_web + s) for s, n in outline]
    return _xz_prism(pts, y0, y1)


def rod_shape(p: V, q: V, r: float, shorten_by=(0.0, 0.0)) -> Part.Shape:
    d = q.sub(p)
    L = d.Length
    u = V(d.x / L, d.y / L, d.z / L)
    a = p + V(u) * shorten_by[0]
    length = L - shorten_by[0] - shorten_by[1]
    return Part.makeCylinder(r, length, a, u), length


def disc_clearance(direction: V, plane_normal: V, r: float) -> float:
    """How far back along ``direction`` a rod of radius ``r`` must stop so
    that its square-cut end clears a plane it meets at an angle.

    The end disc of a cylinder is perpendicular to the rod, so on a raking
    rod one edge of it reaches past the centre by ``r tan(theta)``.
    """
    u = V(direction)
    u.normalize()
    nn = V(plane_normal)
    nn.normalize()
    c = abs(u.dot(nn))
    if c < 1e-6:                                       # pragma: no cover
        return r
    return r * math.sqrt(max(0.0, 1.0 - c * c)) / c + 0.5
