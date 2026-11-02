"""
Orthographic views of IFC parts with hidden-line removal - no OpenCASCADE needed.

A view is a frame ``(origin, ex, ey, ez)``: sheet x along ``ex``, sheet y along
``ey``, ``ez`` toward the viewer. Lines are the mesh's FEATURE edges (a fold of
more than ``FEATURE_DEG`` between the two faces, or an open edge) plus the
silhouette of curved faces for this view direction. Each line is cut into
short pieces and each piece is tested against every triangle in front of it
(shapely STRtree); visible pieces go to ``visible``, the rest to ``hidden``.

Round holes are NOT drawn from the mesh: their faceted walls are removed and
the caller draws a hole symbol from the opening itself (centre, axis,
diameter), which is what a shop drawing shows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely import STRtree, points as shp_points
from shapely.geometry import LineString

__all__ = ["ViewFrame", "Lines", "feature_edges", "project_parts", "FEATURE_DEG"]

FEATURE_DEG = 25.0          # a fold sharper than this is an edge; a 24-gon cylinder (15 deg) is smooth
DEPTH_TOL = 0.5             # mm - a triangle must be this much nearer to hide a line


@dataclass
class ViewFrame:
    origin: np.ndarray
    ex: np.ndarray
    ey: np.ndarray
    ez: np.ndarray          # toward the viewer

    def to2d(self, p: np.ndarray) -> np.ndarray:
        d = np.atleast_2d(p) - self.origin
        return np.stack([d @ self.ex, d @ self.ey], axis=1)

    def depth(self, p: np.ndarray) -> np.ndarray:
        return (np.atleast_2d(p) - self.origin) @ self.ez

    def to3d(self, xy) -> np.ndarray:
        return self.origin + xy[0] * self.ex + xy[1] * self.ey


@dataclass
class Lines:
    visible: list = field(default_factory=list)     # [((x0,y0),(x1,y1))] mm, view coords
    hidden: list = field(default_factory=list)

    def bbox(self):
        xs = [c for s in self.visible + self.hidden for c in (s[0][0], s[1][0])]
        ys = [c for s in self.visible + self.hidden for c in (s[0][1], s[1][1])]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))


def _face_normals(v, f):
    cr = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    n = np.linalg.norm(cr, axis=1)
    n[n == 0] = 1.0
    return cr / n[:, None]


def feature_edges(v: np.ndarray, f: np.ndarray, holes=(), deg: float = FEATURE_DEG):
    """``(edges (E,2) feature, smooth (S,2) with their two face ids (S,2), face normals)``.

    Vertices are welded on a 0.001 mm grid first. Edges whose both ends lie on
    a round hole's wall (``holes``: objects with center, axis, d, depth) are
    dropped.
    """
    key = np.round(v, 3)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    F = inv[f]
    V = uniq
    good = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])
    F = F[good]
    N = _face_normals(V, F)
    e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    tri = np.concatenate([np.arange(len(F))] * 3)
    e.sort(axis=1)
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, tri = e[order], tri[order]
    ue_, start, counts = np.unique(e, axis=0, return_index=True, return_counts=True)
    feat, smooth, smooth_tris = [], [], []
    cosd = math.cos(math.radians(deg))
    for k in range(len(ue_)):
        c = counts[k]
        if c == 2:
            t1, t2 = tri[start[k]], tri[start[k] + 1]
            if N[t1] @ N[t2] < cosd:
                feat.append(ue_[k])
            else:
                smooth.append(ue_[k])
                smooth_tris.append((t1, t2))
        else:
            feat.append(ue_[k])
    feat = np.array(feat, dtype=np.int64).reshape(-1, 2)
    smooth = np.array(smooth, dtype=np.int64).reshape(-1, 2)
    smooth_tris = np.array(smooth_tris, dtype=np.int64).reshape(-1, 2)
    if len(holes) and len(feat):
        drop = np.zeros(len(feat), bool)
        for h in holes:
            P0, P1 = V[feat[:, 0]] - h.center, V[feat[:, 1]] - h.center
            on = np.ones(len(feat), bool)
            for P in (P0, P1):
                t = P @ h.axis
                r = np.linalg.norm(P - np.outer(t, h.axis), axis=1)
                on &= (np.abs(r - h.d / 2) < 0.8) & (np.abs(t) <= h.depth / 2 + 0.8)
            drop |= on
        feat = feat[~drop]
        if len(smooth):
            keep = np.ones(len(smooth), bool)
            for h in holes:
                on = np.ones(len(smooth), bool)
                for P in (V[smooth[:, 0]] - h.center, V[smooth[:, 1]] - h.center):
                    t = P @ h.axis
                    r = np.linalg.norm(P - np.outer(t, h.axis), axis=1)
                    on &= (np.abs(r - h.d / 2) < 0.8) & (np.abs(t) <= h.depth / 2 + 0.8)
                keep &= ~on
            smooth, smooth_tris = smooth[keep], smooth_tris[keep]
    return V, F, N, feat, smooth, smooth_tris


class _Prepared:
    """One part's mesh, welded and edge-classified once, re-used by every view."""

    def __init__(self, v, f, holes=()):
        self.V, self.F, self.N, self.feat, self.smooth, self.smooth_tris = feature_edges(v, f, holes)


_cache: dict[int, _Prepared] = {}


def prepared(part) -> _Prepared:
    k = id(part)
    if k not in _cache:
        _cache[k] = _Prepared(part.verts, part.tris, part.holes)
    return _cache[k]


def project_parts(parts, vf: ViewFrame, *, step: float = 5.0, hidden: bool = True,
                  occluders=None) -> dict[str, Lines]:
    """Hidden-line projection of ``parts`` (each with verts/tris/holes) onto ``vf``.

    ``step`` (model mm) is the length of the pieces each line is tested in -
    about 1 mm on the paper. ``occluders`` (default: the same parts) are what
    can hide a line. Returns ``{part gid: Lines}``.
    """
    occluders = parts if occluders is None else occluders
    # ---- occluding triangles, vectorised: 2D polygons + the depth plane z = A x + B y + C of each
    tri_xy, tri_z = [], []
    for p in occluders:
        pr = prepared(p)
        xy = vf.to2d(pr.V)
        z = vf.depth(pr.V)
        tri_xy.append(xy[pr.F])
        tri_z.append(z[pr.F])
    polys, planes, tree = [], np.zeros((0, 3)), None
    if tri_xy:
        T = np.concatenate(tri_xy)                 # (n, 3, 2)
        Z = np.concatenate(tri_z)                  # (n, 3)
        area2 = ((T[:, 1, 0] - T[:, 0, 0]) * (T[:, 2, 1] - T[:, 0, 1])
                 - (T[:, 2, 0] - T[:, 0, 0]) * (T[:, 1, 1] - T[:, 0, 1]))
        ok = np.abs(area2) > 1e-6
        T, Z = T[ok], Z[ok]
        if len(T):
            M = np.concatenate([T, np.ones((len(T), 3, 1))], axis=2)
            planes = np.linalg.solve(M, Z[:, :, None])[:, :, 0]
            polys = shapely.polygons(T)
            tree = STRtree(polys)

    out: dict[str, Lines] = {}
    for p in parts:
        pr = prepared(p)
        xy = vf.to2d(pr.V)
        z = vf.depth(pr.V)
        segs = [tuple(e) for e in pr.feat]
        # silhouette of smooth (curved) faces: one face toward the viewer, the other away
        if len(pr.smooth):
            d1 = pr.N[pr.smooth_tris[:, 0]] @ vf.ez
            d2 = pr.N[pr.smooth_tris[:, 1]] @ vf.ez
            sil = pr.smooth[(d1 * d2) <= 0]
            segs += [tuple(e) for e in sil]
        lines = Lines()
        if not segs:
            out[p.gid] = lines
            continue
        # drop zero-length (edge seen end-on); of edges that project onto the same 2D line
        # (a flange's front and back edge) keep the NEAREST, which is the one that can be seen
        best: dict[tuple, tuple] = {}
        for a, b in segs:
            A, B = xy[a], xy[b]
            if float(np.hypot(*(B - A))) < 1e-3:
                continue
            k = (round(A[0], 2), round(A[1], 2), round(B[0], 2), round(B[1], 2))
            if (k[2], k[3], k[0], k[1]) < k:
                k, a, b = (k[2], k[3], k[0], k[1]), b, a
            zm = (z[a] + z[b]) / 2
            if k not in best or zm > best[k][2]:
                best[k] = (a, b, zm)
        pieces_xy, pieces_z, owner = [], [], []
        for a, b, _ in best.values():
            A, B = xy[a], xy[b]
            L = float(np.hypot(*(B - A)))
            n = max(1, int(math.ceil(L / step)))
            ts = np.linspace(0.0, 1.0, n + 1)
            for i in range(n):
                t0, t1 = ts[i], ts[i + 1]
                pieces_xy.append((A + (B - A) * t0, A + (B - A) * t1))
                pieces_z.append(z[a] + (z[b] - z[a]) * (t0 + t1) / 2)
                owner.append(len(owner))
        if not pieces_xy:
            out[p.gid] = lines
            continue
        mids = np.array([(s[0] + s[1]) / 2 for s in pieces_xy])
        zs = np.array(pieces_z)
        hid = np.zeros(len(mids), bool)
        if tree is not None:
            pi, ti = tree.query(shp_points(mids), predicate="intersects")
            if len(pi):
                zt = planes[ti, 0] * mids[pi, 0] + planes[ti, 1] * mids[pi, 1] + planes[ti, 2]
                occl = zt > zs[pi] + DEPTH_TOL
                hid[np.unique(pi[occl])] = True
        # merge consecutive pieces of one edge with the same state
        cur = None
        for (A, B), h in zip(pieces_xy, hid):
            if cur and cur[2] == h and np.allclose(cur[1], A, atol=1e-6):
                cur = (cur[0], B, h)
                continue
            if cur:
                (lines.hidden if cur[2] else lines.visible).append((tuple(cur[0]), tuple(cur[1])))
            cur = (A, B, h)
        if cur:
            (lines.hidden if cur[2] else lines.visible).append((tuple(cur[0]), tuple(cur[1])))
        out[p.gid] = lines
    if hidden:
        _drop_hidden_under_visible(out, tol=step * 0.1)
    else:
        for L in out.values():
            L.hidden = []
    return out


def _drop_hidden_under_visible(out: dict[str, Lines], tol: float):
    """A hidden line drawn exactly under a visible one is noise (the back edge of a plate)."""
    vis = [LineString(s) for L in out.values() for s in L.visible]
    if not vis:
        return
    tree = STRtree(vis)
    for L in out.values():
        keep = []
        for s in L.hidden:
            ls = LineString(s)
            near = tree.query(ls, predicate="dwithin", distance=tol)
            covered = False
            if len(near):
                u = None
                for i in near:
                    g = vis[i].buffer(tol, cap_style="flat")
                    u = g if u is None else u.union(g)
                covered = u.contains(ls) if u is not None else False
            if not covered:
                keep.append(s)
        L.hidden = keep
