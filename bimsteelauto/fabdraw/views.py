"""
Views of an assembly or a part, ready to be placed on a sheet.

A :class:`View` holds its hidden-line drawing in VIEW millimetres (model
size). It knows which holes it sees face-on (drawn as a circle and
dimensioned) and edge-on (drawn as a centre line), and it is placed on a
sheet by :meth:`View.place` at a scale. Which views an assembly needs is
decided from its holes, not from a template:

* FRONT  - along the main part's thin axis (an I section seen on its web);
* TOP    - from above, when any hole's axis is the main part's depth axis
           (flange holes, knee-plate holes);
* one DETAIL per part mark whose holes are seen in neither (end plates,
  base plates, cap plates): the part on its own, face-on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import hlr
from .extract import Assembly, Hole, Part

__all__ = ["View", "assembly_views", "part_views", "set_breaks", "clear_breaks", "FACE_COS", "EDGE_COS"]

FACE_COS = math.cos(math.radians(3.0))      # a hole within 3 deg of the view axis is seen as a circle
EDGE_COS = math.sin(math.radians(3.0))      # within 3 deg of the view plane, as a line


@dataclass
class View:
    name: str
    vf: hlr.ViewFrame
    parts: list[Part]
    occluders: list[Part] | None = None
    lines: dict = field(default_factory=dict)
    face_holes: list = field(default_factory=list)      # [(Hole, Part, (x, y))]
    edge_holes: list = field(default_factory=list)      # [(Hole, Part, (x0, y0), (x1, y1))]
    ref: Part | None = None                              # the part dimensions are taken from
    # placement
    scale: float = 1.0
    ox: float = 0.0             # paper x of view x = bx0
    oy: float = 0.0
    bx0: float = 0.0
    by0: float = 0.0
    bx1: float = 0.0
    by1: float = 0.0
    # shortening: view-x intervals (a, b) drawn as a gap of ``gap`` view mm (BREAK_GAP paper mm)
    breaks: list = field(default_factory=list)
    gap: float = 0.0

    def prepare(self):
        """Holes and the bounding box, from the vertices - cheap; enough to choose a scale."""
        self._holes()
        xy = np.concatenate([self.vf.to2d(p.verts) for p in self.parts])
        xs, ys = list(xy[:, 0]), list(xy[:, 1])
        for h, _, c in self.face_holes:
            xs += [c[0] - h.d / 2, c[0] + h.d / 2]
            ys += [c[1] - h.d / 2, c[1] + h.d / 2]
        self.bx0, self.by0, self.bx1, self.by1 = float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))
        return self

    def _holes(self):
        self.face_holes, self.edge_holes = [], []
        for p in self.parts:
            for h in p.holes:
                c = self.vf.to2d(h.center)[0]
                cz = abs(float(h.axis @ self.vf.ez))
                if cz >= FACE_COS:
                    self.face_holes.append((h, p, (float(c[0]), float(c[1]))))
                elif cz <= EDGE_COS:
                    a = self.vf.to2d(h.center - h.axis * h.depth / 2)[0]
                    b = self.vf.to2d(h.center + h.axis * h.depth / 2)[0]
                    self.edge_holes.append((h, p, (float(a[0]), float(a[1])), (float(b[0]), float(b[1]))))

    def compute(self, step: float):
        """Hidden-line drawing at ``step`` (model mm per tested piece), holes and the bounding box."""
        self.lines = hlr.project_parts(self.parts, self.vf, step=step, occluders=self.occluders)
        self._holes()
        xs, ys = [], []
        for L in self.lines.values():
            for s in L.visible + L.hidden:
                xs += [s[0][0], s[1][0]]
                ys += [s[0][1], s[1][1]]
        for h, _, c in self.face_holes:
            xs += [c[0] - h.d / 2, c[0] + h.d / 2]
            ys += [c[1] - h.d / 2, c[1] + h.d / 2]
        if not xs:
            xs, ys = [0.0], [0.0]
        self.bx0, self.by0, self.bx1, self.by1 = min(xs), min(ys), max(xs), max(ys)
        return self

    # --- geometry in the view
    @property
    def w(self):
        return self.bx1 - self.bx0

    @property
    def h(self):
        return self.by1 - self.by0

    @property
    def wc(self):
        """Width after shortening (view mm)."""
        return self.w - sum((b - a) - self.gap for a, b in self.breaks)

    def mx(self, x: float) -> float:
        """View x -> shortened view x. Continuous: inside a break the interval is squeezed into the gap."""
        shift = 0.0
        for a, b in self.breaks:
            if x <= a:
                break
            if x >= b:
                shift += (b - a) - self.gap
            else:
                shift += (x - a) * (1.0 - self.gap / (b - a))
                break
        return x - shift

    def clip(self, seg):
        """A view segment cut at the breaks; the pieces inside a break are dropped."""
        (x0, y0), (x1, y1) = seg
        if not self.breaks or x0 == x1:
            if any(a < x0 < b for a, b in self.breaks):
                return []
            return [seg]
        cuts = sorted({t for a, b in self.breaks for t in ((a - x0) / (x1 - x0), (b - x0) / (x1 - x0)) if 0 < t < 1})
        ts = [0.0] + cuts + [1.0]
        out = []
        for t0, t1 in zip(ts, ts[1:]):
            xm = x0 + (x1 - x0) * (t0 + t1) / 2
            if any(a < xm < b for a, b in self.breaks):
                continue
            out.append(((x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0), (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)))
        return out

    def keep_intervals(self) -> list[tuple[float, float]]:
        """View-x intervals that must stay visible: holes, every part but the reference, and every
        place the reference part's outline turns (its fold-edge ends: cut ends, copes, haunches)."""
        out = []
        for h, _, c in self.face_holes:
            out.append((c[0] - h.d / 2, c[0] + h.d / 2))
        for h, _, a, b in self.edge_holes:
            out.append((min(a[0], b[0]) - h.d / 2, max(a[0], b[0]) + h.d / 2))
        ref = self.ref or self.parts[0]
        for p in self.parts:
            if p is ref:
                continue
            xy = self.vf.to2d(p.verts)
            out.append((float(xy[:, 0].min()), float(xy[:, 0].max())))
        pr = hlr.prepared(ref)
        if len(pr.feat):
            xs = self.vf.to2d(pr.V[np.unique(pr.feat)])[:, 0]
            out += [(float(x), float(x)) for x in np.unique(np.round(xs, 1))]
        out += [(self.bx0, self.bx0), (self.bx1, self.bx1)]
        return out

    def P(self, x, y):
        """View mm -> paper mm (through the shortening)."""
        return (self.ox + (self.mx(x) - self.bx0) / self.scale, self.oy + (y - self.by0) / self.scale)

    def part_bbox(self, part) -> tuple[float, float, float, float] | None:
        L = self.lines.get(part.gid)
        if L is None or not (L.visible or L.hidden):
            xy = self.vf.to2d(part.verts)
            return float(xy[:, 0].min()), float(xy[:, 1].min()), float(xy[:, 0].max()), float(xy[:, 1].max())
        return L.bbox()

    def ref_range(self):
        """(x0, x1, y0, y1) of the reference part in the view - where dimensions are measured from."""
        p = self.ref or self.parts[0]
        xy = self.vf.to2d(p.verts)
        return float(xy[:, 0].min()), float(xy[:, 0].max()), float(xy[:, 1].min()), float(xy[:, 1].max())

    def section_y(self, x: float) -> tuple[float, float] | None:
        """(y min, y max) where the reference part's solid crosses the line view-x = ``x``."""
        p = self.ref or self.parts[0]
        xy = self.vf.to2d(p.verts)
        E = np.concatenate([p.tris[:, [0, 1]], p.tris[:, [1, 2]], p.tris[:, [2, 0]]])
        xa, xb = xy[E[:, 0], 0], xy[E[:, 1], 0]
        m = (np.minimum(xa, xb) <= x) & (np.maximum(xa, xb) >= x) & (np.abs(xb - xa) > 1e-9)
        if not m.any():
            return None
        t = (x - xa[m]) / (xb[m] - xa[m])
        y = xy[E[m, 0], 1] + t * (xy[E[m, 1], 1] - xy[E[m, 0], 1])
        return float(y.min()), float(y.max())

    def end_depth(self, end: int) -> tuple[float, float, float]:
        """``(x, y0, y1)``: the full depth of the reference part at one end (0 = start, 1 = far end).

        Sections are cut through the solid, stepping in from the end; the first one whose depth
        then holds steady (changes no faster than a taper, 0.12 mm/mm) over ``win`` is taken.
        A square end is measured at the end; a sloped end cut (a rafter at its end plate) just
        past the toe of the cut; an end plate fused into the same solid (a brep export) is
        stepped over. The step is at most ``reach / 239`` (7 mm on an 8.5 m rafter), so on a
        tapered member the value is within step x taper of the toe (0.2 mm there).
        """
        p = self.ref or self.parts[0]
        xy = self.vf.to2d(p.verts)
        x0, x1 = float(xy[:, 0].min()), float(xy[:, 0].max())
        L = x1 - x0
        D = float(np.ptp(xy[:, 1]))
        reach = min(0.3 * L, 3.0 * D + 50.0)          # how far in from the end a section may be taken
        win = max(30.0, 0.15 * D)                      # the depth must hold steady over this run
        sgn = 1.0 if end == 0 else -1.0
        xe = x0 if end == 0 else x1
        n = 240
        xs = xe + sgn * np.linspace(0.05, reach, n)
        ds = []
        for x in xs:
            r = self.section_y(float(x))
            ds.append(r)
        step = reach / (n - 1)
        k = max(2, int(math.ceil(win / step)))
        for i in range(n - k):
            if ds[i] is None or any(ds[j] is None for j in range(i, i + k + 1)):
                continue
            d0 = ds[i][1] - ds[i][0]
            # inside an end cut or a fused end plate the depth jumps; past it only the taper moves it
            if all(abs((ds[j][1] - ds[j][0]) - d0) <= 0.12 * abs(xs[j] - xs[i]) + 0.3 for j in range(i + 1, i + k + 1)):
                # refine: walk back toward the end while the section still gives the same depth line
                return (float(xs[i]), ds[i][0], ds[i][1])
        r = self.section_y(float(xe + sgn * min(reach, 0.5 * L)))
        if r is None:
            return (xe, float(xy[:, 1].min()), float(xy[:, 1].max()))
        return (float(xe + sgn * min(reach, 0.5 * L)), r[0], r[1])


BREAK_KEEP = 10.0         # paper mm of steel kept each side of a feature before a break
BREAK_GAP = 6.0           # paper mm the cut-out length closes up to
BREAK_MIN = 30.0          # paper mm: a run shorter than this is not worth a break
BREAK_MAX_REMOVED = 0.75  # never take out more than this share of the length


def set_breaks(views: list[View], scale: float) -> float:
    """Shorten ``views`` (which share their x axis: FRONT and TOP) at ``scale``. Returns the shortened width.

    The runs between features longer than ``BREAK_MIN`` paper mm (with ``BREAK_KEEP`` kept either side)
    are closed up to ``BREAK_GAP``. If that takes out more than ``BREAK_MAX_REMOVED`` of the length the
    smallest breaks are given back first - a member must still read as long.
    """
    keep = []
    for v in views:
        keep += v.keep_intervals()
    keep.sort()
    k, g, mn = BREAK_KEEP * scale, BREAK_GAP * scale, BREAK_MIN * scale
    merged = []
    for a, b in keep:
        a, b = a - k, b + k
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    brk = [(merged[i][1], merged[i + 1][0]) for i in range(len(merged) - 1)
           if merged[i + 1][0] - merged[i][1] > max(mn, g + 1e-6)]
    total = views[0].w
    while brk and sum((b - a) - g for a, b in brk) > BREAK_MAX_REMOVED * total:
        brk.remove(min(brk, key=lambda ab: ab[1] - ab[0]))
    for v in views:
        v.breaks, v.gap = list(brk), g
    return views[0].wc


def clear_breaks(views: list[View]):
    for v in views:
        v.breaks, v.gap = [], 0.0


def _covered(holes_axes, axis) -> bool:
    return any(abs(float(a @ axis)) >= FACE_COS for a in holes_axes)


def assembly_views(a: Assembly) -> list[View]:
    o, e1, e2, n = a.frame
    main = a.main
    front = View("FRONT", hlr.ViewFrame(o, e1, e2, n), a.parts, ref=main)
    views = [front]
    all_holes = [(h, p) for p in a.parts for h in p.holes]
    if any(abs(float(h.axis @ e2)) >= FACE_COS for h, _ in all_holes):
        views.append(View("TOP", hlr.ViewFrame(o, e1, -n, e2), a.parts, ref=main))
    seen_axes = [n, e2] if len(views) > 1 else [n]
    # details: a part whose holes are seen in neither main view, once per part mark
    done = set()
    for p in a.parts:
        if not p.holes or p.mark in done or p is main:
            continue
        if all(_covered(seen_axes, h.axis) for h in p.holes):
            continue
        done.add(p.mark)
        po, pe1, pe2, pn = p.frame
        # look at the plate from the side its holes' axis points to the main part's origin
        views.append(View(f"DETAIL {p.mark}", hlr.ViewFrame(po, pe1, pe2, pn), [p], occluders=[p], ref=p))
    return views


def part_views(p: Part) -> list[View]:
    """A single part: plate face-on; a profile in elevation, from above when it has holes there, and its end."""
    o, e1, e2, n = p.frame
    if p.is_plate:
        return [View("PLATE", hlr.ViewFrame(o, e1, e2, n), [p], ref=p)]
    vs = [View("FRONT", hlr.ViewFrame(o, e1, e2, n), [p], ref=p)]
    if any(abs(float(h.axis @ e2)) >= FACE_COS for h in p.holes):
        vs.append(View("TOP", hlr.ViewFrame(o, e1, -n, e2), [p], ref=p))
    vs.append(View("END", hlr.ViewFrame(o, n, e2, -e1), [p], ref=p))
    return vs
