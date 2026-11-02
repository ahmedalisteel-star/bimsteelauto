"""
Assembly and single-part fabrication sheets (DXF), and plate NC files.

Layout follows the usual Tekla shop set:

* ASSEMBLY sheet (A3, larger if it will not fit): front view, top view when
  there are flange holes, a detail of every plate whose holes neither shows;
  running dimensions from the set-out end, hole gauges, part marks; general
  notes and the material list top right, title block bottom right,
  "n Nos. ... REQUIRED AS DRAWN MARKED x" bottom left.
* SINGLE-PART sheet (A4): the part face-on (plates) or in elevation + end
  (profiles), overall sizes, running hole dimensions both ways, hole
  call-outs, allocation to assemblies, part table.
* NC: for every plate a DXF on the CUT / HOLE / TEXT layers at 1:1 and a
  DSTV ``.nc1`` (ST / AK / IK / BO / EN blocks).

Every number on a sheet is computed here from the :class:`~.extract.Part`
geometry. Nothing is typed.
"""

from __future__ import annotations

import math
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .drafting import CW, SCALES, TXT, TXT_L, TXT_S, Rect, Sheet, fmt_len, pack
from .extract import Assembly, FabModel, Part, _natural
from .views import BREAK_MAX_REMOVED, View, assembly_views, clear_breaks, part_views, set_breaks

__all__ = ["SheetResult", "assembly_sheet", "part_sheet", "plate_nc", "draw_view"]

HOLE_GROUP_MM = 0.5          # holes closer than this along a running dimension share a tick


@dataclass
class SheetResult:
    kind: str                   # "assembly" | "part"
    mark: str
    path: str
    size: str
    scale: float
    views: list[str]
    warnings: list[str] = field(default_factory=list)
    overlaps: int = 0


def _scale_txt(s: float) -> str:
    return f"1:{fmt_len(s)}"


def _uniq(vals, tol=HOLE_GROUP_MM):
    out = []
    for v in sorted(vals):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


# ------------------------------------------------------------------ drawing one view
def draw_view(sh: Sheet, v: View, *, dims: str = "assembly", label: str | None = None,
              balloons: bool = True, mark_of=None) -> Rect:
    """Draw ``v`` (already placed: scale, ox, oy) with its dimensions. Returns the paper box used."""
    P = v.P
    s = v.scale
    # view definition for G3: origin of view coords and the frame
    pt = sh.msp.add_point(P(v.bx0, v.by0), dxfattribs={"layer": "BSA_VIEW"})
    vf = v.vf
    sh_x = ";".join(f"{c:.6f}" for c in (*vf.origin, *vf.ex, *vf.ey, *vf.ez))
    brk = ",".join(f"{a:.4f}:{b:.4f}" for a, b in v.breaks)
    pt.set_xdata("BSA_FABDRAW", [(1000, f"view={v.name}"), (1000, f"scale={s}"), (1000, f"bx0={v.bx0:.4f}"),
                                 (1000, f"by0={v.by0:.4f}"), (1000, f"frame={sh_x}"), (1000, f"breaks={brk}"),
                                 (1000, f"gap={v.gap:.4f}")])
    for L in v.lines.values():
        for seg in L.visible:
            for a, b in v.clip(seg):
                sh.line(P(*a), P(*b), "PART")
        for seg in L.hidden:
            for a, b in v.clip(seg):
                sh.line(P(*a), P(*b), "HIDDEN")
    for a, b in v.breaks:
        # a Z break line across the steel at the middle of the gap
        xm = P(a, 0)[0] + v.gap / s / 2
        ys = []
        for L in v.lines.values():
            for (x0, y0), (x1, y1) in L.visible:
                if min(x0, x1) <= a and max(x0, x1) >= b:
                    t = 0.5 if x1 == x0 else ((a + b) / 2 - x0) / (x1 - x0)
                    ys.append(y0 + (y1 - y0) * t)
        y0p = P(0, min(ys))[1] - 1.5 if ys else P(0, v.by0)[1]
        y1p = P(0, max(ys))[1] + 1.5 if ys else P(0, v.by1)[1]
        ym = (y0p + y1p) / 2
        z = min(1.4, max(0.6, (y1p - y0p) / 8))
        sh.msp.add_lwpolyline([(xm, y0p), (xm, ym - z), (xm + z, ym - z / 3), (xm - z, ym + z / 3), (xm, ym + z),
                               (xm, y1p)], dxfattribs={"layer": "PART"})
    for h, p, c in v.face_holes:
        x, y = P(*c)
        r = max(h.d / 2 / s, 0.5)
        sh.circle((x, y), r, "HOLE")
        e = r + 1.0
        sh.line((x - e, y), (x + e, y), "HOLE")
        sh.line((x, y - e), (x, y + e), "HOLE")
    for h, p, a, b in v.edge_holes:
        A, B = np.array(P(*a)), np.array(P(*b))
        d = B - A
        L = float(np.hypot(*d))
        if L < 1e-6:
            continue
        u = d / L
        nn = np.array([-u[1], u[0]])
        r = max(h.d / 2 / s, 0.35)
        sh.line(tuple(A - u * 1.5), tuple(B + u * 1.5), "CENTER")
        sh.line(tuple(A + nn * r), tuple(B + nn * r), "HOLE")
        sh.line(tuple(A - nn * r), tuple(B - nn * r), "HOLE")

    gx0, gy0 = P(v.bx0, v.by0)
    gx1, gy1 = P(v.bx1, v.by1)
    box = Rect(gx0, gy0, gx1 - gx0, gy1 - gy0)
    rx0, rx1, ry0, ry1 = v.ref_range()
    zero_x, zero_y = rx0, ry0

    # ---- running dimension of the holes seen face-on, along x (above) and y (right)
    hx = _uniq([c[0] for _, _, c in v.face_holes])
    hy = _uniq([c[1] for _, _, c in v.face_holes])
    top = gy1
    if dims in ("assembly", "part"):
        # x: holes + the far end of the reference part
        ticks = [x for x in hx if abs(x - zero_x) > HOLE_GROUP_MM] + [rx1]
        ticks = _uniq(ticks)
        if hx or dims == "part":
            at = gy1 + 9.0
            sh.running_dim(P(zero_x, 0)[0], [P(x, 0)[0] for x in ticks], [x - zero_x for x in ticks], at, gy1 + 1.0,
                           "x", +1, v.name)
            top = at + 1.5 + 0.6 + CW * TXT * 5
        # y: holes, right of the view
        right = gx1
        if hy:
            yt = _uniq([y for y in hy if abs(y - zero_y) > HOLE_GROUP_MM] + [ry1])
            at = gx1 + 8.0
            sh.running_dim(P(0, zero_y)[1], [P(0, y)[1] for y in yt], [y - zero_y for y in yt], at, gx1 + 1.0,
                           "y", +1, v.name)
            right = at + 1.5 + 0.6 + CW * TXT * 5
        # overall length below, end depths left
        bottom = gy0
        if dims == "assembly" and balloons:
            bottom = gy0 - 7.0          # balloon lane
        if dims == "assembly":
            sec = _secondary_positions(v)
            if sec:
                at = bottom - 6.0
                sh.running_dim(P(zero_x, 0)[0], [P(x, 0)[0] for x in sec], [x - zero_x for x in sec], at, gy0 - 1.0,
                               "x", -1, v.name, xdata_extra={"kind": "parts"})
                bottom = at - 1.5 - 0.6 - CW * TXT * 5
        base_y = bottom - 6.0
        broken = any(rx0 < a and b < rx1 for a, b in v.breaks)
        sh.linear_dim(P(rx0, ry0), P(rx1, ry0), (P(rx0, 0)[0], base_y), s, 0.0,
                      xdata={"view": v.name, "what": "overall length", "value": fmt_len(rx1 - rx0)},
                      text=fmt_len(rx1 - rx0) if broken else None)
        bottom = base_y - 1.0
        left = gx0
        xa, y0a, y1a = v.end_depth(0)
        if y1a - y0a > 1.0:
            bx = gx0 - 6.0
            sh.linear_dim(P(xa, y0a), P(xa, y1a), (bx, P(0, y0a)[1]), s, 90.0,
                          xdata={"view": v.name, "what": "end depth", "value": fmt_len(y1a - y0a)})
            left = bx - TXT - 2.0
        xb, y0b, y1b = v.end_depth(1)
        if abs((y1b - y0b) - (y1a - y0a)) > 1.0 and not hy:
            bx = gx1 + 6.0
            sh.linear_dim(P(xb, y0b), P(xb, y1b), (bx, P(0, y0b)[1]), s, 90.0,
                          xdata={"view": v.name, "what": "end depth", "value": fmt_len(y1b - y0b)})
            right = max(right, bx + 2.0)
        if dims == "part" and v.h > 1.0 and not hy:
            bx = max(gx1 + 6.0, right + TXT + 4.0) if right > gx1 + 1.0 else gx1 + 6.0
            sh.linear_dim(P(rx1, ry0), P(rx1, ry1), (bx, P(0, ry0)[1]), s, 90.0,
                          xdata={"view": v.name, "what": "overall width", "value": fmt_len(ry1 - ry0)})
            right = max(right, bx + 2.0)
        used = Rect(left, bottom, right - left, top - bottom)
    else:
        used = box
    # ---- balloons (part marks), one per mark per view, in lanes above / below
    if balloons and dims == "assembly":
        lanes = {+1: [], -1: []}
        seen = set()
        midy = (v.by0 + v.by1) / 2
        for p in sorted(v.parts, key=lambda q: (q is not v.ref, _natural(q.mark))):
            if p.mark in seen:
                continue
            bb = v.part_bbox(p)
            if bb is None:
                continue
            seen.add(p.mark)
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            if p is v.ref:
                cx = bb[0] + (bb[2] - bb[0]) * 0.18
                tgt = (cx, bb[3] if (bb[3] - bb[1]) < 1e9 else cy)
                side = +1
                tgt = (cx, cy)
            else:
                side = +1 if cy >= midy else -1
                tgt = (cx, bb[3] if side > 0 else bb[1])
            lanes[side].append((P(*tgt), p.mark))
        for side, items in lanes.items():
            y = gy1 + 4.0 if side > 0 else gy0 - 4.0
            last = -1e9
            for (tx, ty), mk in sorted(items):
                w = CW * TXT * len(mk) + 2.0
                x = max(tx, last + w / 2 + 1.5)
                sh.balloon(mk, (x, y), (tx, ty))
                last = x + w / 2
        used = Rect(min(used.x, gx0), used.y, max(used.x1, last if lanes[+1] or lanes[-1] else 0) - min(used.x, gx0),
                    used.h) if False else used
    if label:
        sh.text(label, (gx0 + gx1) / 2, used.y - TXT_L - 1.5, TXT_L, align="CENTER")
        used = Rect(used.x, used.y - TXT_L - 2.5, used.w, used.h + TXT_L + 2.5)
    return used


def _secondary_positions(v: View) -> list[float]:
    """x of the parts that carry no face-on hole here (stiffeners, cleats seen edge-on), not at the ends."""
    rx0, rx1, _, _ = v.ref_range()
    holed = {id(p) for _, p, _ in v.face_holes}
    xs = []
    for p in v.parts:
        if p is v.ref or id(p) in holed:
            continue
        bb = v.part_bbox(p)
        if bb is None:
            continue
        cx = (bb[0] + bb[2]) / 2
        if cx - rx0 < 30 or rx1 - cx < 30:
            continue
        xs.append(cx)
    xs = _uniq(xs, 1.0)
    return xs if len(xs) <= 30 else []


def _margins(dims: str, has_hx: bool, has_hy: bool, balloons: bool):
    """Paper margins (left, bottom, right, top) a view needs around its geometry."""
    top = (9.0 + 2.1 + CW * TXT * 5) if (has_hx or dims == "part") else 2.0
    if balloons:
        top = max(top, 7.0)
    right = (8.0 + 2.1 + CW * TXT * 5) if has_hy else 9.0
    bottom = 7.0 if balloons else 0.0
    bottom += 6.0 + 2.1 + CW * TXT * 5 if dims == "assembly" else 0.0
    bottom += 7.0
    left = 6.0 + TXT + 2.0
    return left, bottom, right, top


# ------------------------------------------------------------------ assembly sheet
def _material_rows(a: Assembly):
    groups: OrderedDict[str, list[Part]] = OrderedDict()
    for p in sorted(a.parts, key=lambda q: (q is not a.main, _natural(q.mark))):
        groups.setdefault(p.mark, []).append(p)
    rows = []
    for mk, ps in groups.items():
        p = ps[0]
        rows.append([mk or "?", p.description(), p.grade, str(len(ps)), fmt_len(p.length),
                     f"{sum(q.area for q in ps):.1f}", f"{sum(q.weight for q in ps):.1f}"])
    return rows


def _notes(a_parts, tb) -> list[str]:
    ds = Counter(round(h.d, 1) for p in a_parts for h in p.holes)
    out = []
    if ds:
        d = ds.most_common(1)[0][0]
        out.append(f"All Holes Are: {fmt_len(d)} mm U.N.O")
    if tb.get("weld"):
        out.append(f"All Welds Are: {tb['weld']} mm U.N.O")
    return out


def assembly_sheet(a: Assembly, out: Path, tb: dict, *, status: str = "", prefix: str = "",
                   sizes=("A3", "A2", "A1")) -> SheetResult:
    views = assembly_views(a)
    main_views = [v for v in views if v.name in ("FRONT", "TOP")]
    details = [v for v in views if v.name.startswith("DETAIL")]
    L = max(a.main.ext[0], 1.0)
    warnings: list[str] = []
    rows = _material_rows(a)
    for size in sizes:
        sh = Sheet(size)
        sh.border()
        f = sh.frame
        tbr = sh.title_block(tb, (a.name or "ASSEMBLY").upper(), f"{prefix}{a.mark}", "", status)
        # material list, top right
        cols = [("Mark", 16), ("Profile", 44), ("Material", 20), ("No.", 9), ("Length", 15), ("Area", 12),
                ("Weight", 15)]
        W = sum(w for _, w in cols)
        notes = _notes(a.parts, tb)
        title_rows = (["GENERAL NOTES:   " + "    ".join(notes)] if notes else []) + \
                     [f"MATERIAL LIST FOR ASSEMBLY {a.mark}   {a.qty} Nos. RQD"]
        tot_w = sum(float(r[6]) for r in rows)
        tot_a = sum(float(r[5]) for r in rows)
        mrows = rows + [["", "", "", "", "Total", f"{tot_a:.1f}", f"{tot_w:.1f}"]]
        ml = sh.table(f.x1 - W, f.y1, cols, mrows, title_rows=title_rows,
                      aligns=["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"], tag="material")
        sh.taken.append(ml)
        # required-as-drawn line, bottom left
        req = f"{a.qty}  Nos.  {(a.name or 'ASSEMBLY').upper()}        REQUIRED AS DRAWN MARKED  {a.mark}"
        sh.text(req, f.x + 4, f.y + 3, TXT_L)
        sh.taken.append(Rect(f.x, f.y, CW * TXT_L * len(req) + 8, TXT_L + 5))
        area = Rect(f.x + 2, f.y + 2, f.w - 4, f.h - 4)
        # ---- main views: the smallest scale denominator at which the views - whole, or shortened with
        # break lines - and every plate detail fit; failing that, the first at which the views fit
        for v in main_views + details:
            v.prepare()
        placed = fallback = None
        for s in SCALES:
            if s < 2.5 or L / s * (1.0 - BREAK_MAX_REMOVED) > area.w:
                continue
            for shorten in (False, True):
                if shorten:
                    set_breaks(main_views, s)
                    if not main_views[0].breaks:
                        continue
                else:
                    clear_breaks(main_views)
                blocks = []
                for v in main_views:
                    ml_, mb, mr, mt = _margins("assembly", bool(v.face_holes), bool(v.face_holes), v.name == "FRONT")
                    blocks.append((v, ml_, mb, mr, mt, v.wc / s, v.h / s))
                Wc = max(b[1] + b[5] + b[3] for b in blocks)
                Hc = sum(b[2] + b[6] + b[4] for b in blocks)
                r = pack((Wc, Hc), area, sh.taken, gap=3.0, step=2.0)
                if r is None:
                    continue
                cand = (s, r, blocks, shorten, [(v.breaks, v.gap) for v in main_views])
                plan = _plan_details(details, area, sh.taken + [r], s)
                if plan is not None:
                    placed = cand + (plan,)
                    break
                if fallback is None:
                    fallback = cand + (None,)
            if placed is not None:
                break
        placed = placed or fallback
        if placed is None:
            continue
        s, r, blocks, shorten, brk, plan = placed
        for v, (b, g) in zip(main_views, brk):
            v.breaks, v.gap = b, g
        step = max(0.8 * s, 1.0)
        for v in main_views:
            v.compute(step)
        # FRONT at the bottom of the block, TOP above it, x aligned on the view origin
        lmax = max(b[1] for b in blocks)
        y = r.y
        for v, ml_, mb, mr, mt, w, h in blocks:
            v.scale = s
            v.ox = r.x + lmax
            v.oy = y + mb
            draw_view(sh, v, dims="assembly", balloons=(v.name == "FRONT"))
            y = v.oy + h + mt
        sh.taken.append(r)
        # ---- details
        if plan is None:
            plan = _plan_details(details, area, sh.taken, s, all_or_none=False)
        placed_marks = set()
        for dv, ds, rr, ml_, mb in plan:
            dv.compute(max(0.8 * ds, 0.5))
            dv.scale = ds
            dv.ox = rr.x + ml_
            dv.oy = rr.y + mb + TXT_L + 3
            draw_view(sh, dv, dims="part", balloons=False,
                      label=f"{dv.name[7:]}  -  {dv.parts[0].description()}   ({_scale_txt(ds)})")
            sh.taken.append(rr)
            placed_marks.add(dv.name)
        for dv in details:
            if dv.name not in placed_marks:
                warnings.append(f"no room for the detail of {dv.name[7:]} - see its single-part sheet")
        # scale into the title block
        _fill_scale(sh, _scale_txt(s))
        path = Path(out) / f"{prefix}{a.mark}.dxf"
        sh.save(path)
        ov = len(sh.overlaps())
        return SheetResult("assembly", a.mark, str(path), size, s, [v.name for v in views], warnings, ov)
    raise RuntimeError(f"assembly {a.mark} does not fit on {sizes[-1]} at 1:{SCALES[-1]}")


DETAIL_SCALES = (2.5, 5, 7.5, 10, 15, 20, 25)


def _plan_details(details, area: Rect, taken: list, s: float, all_or_none: bool = True):
    """Where each plate detail goes, at the largest scale that fits: ``[(view, scale, rect, ml, mb)]``.

    With ``all_or_none`` a detail that fits nowhere makes the whole plan ``None`` (the caller then tries
    a smaller main scale); otherwise it is left out.
    """
    taken = list(taken)
    plan = []
    for dv in details:
        got = None
        for ds in DETAIL_SCALES:
            if ds > max(s, 10):
                break
            ml_, mb, mr, mt = _margins("part", True, True, False)
            size_ = (ml_ + dv.w / ds + mr, mb + dv.h / ds + mt + TXT_L + 3)
            rr = pack(size_, area, taken, gap=3.0, step=2.0, prefer="top-left")
            if rr is not None:
                got = (dv, ds, rr, ml_, mb)
                break
        if got is None:
            if all_or_none:
                return None
            continue
        plan.append(got)
        taken.append(got[2])
    return plan


def _fill_scale(sh: Sheet, txt: str):
    for e in sh.msp.query("TEXT"):
        if e.dxf.text == "SCALE: ":
            e.dxf.text = f"SCALE: {txt}"


# ------------------------------------------------------------------ single-part sheet
def part_sheet(mark: str, parts: list[Part], alloc: Counter, out: Path, tb: dict, *, status: str = "",
               prefix: str = "") -> SheetResult:
    p = parts[0]
    qty = sum(alloc.values())
    views = part_views(p)
    warnings: list[str] = []
    for size in ("A4", "A3", "A2"):
        sh = Sheet(size)
        sh.border()
        f = sh.frame
        kind = "PLATE" if p.is_plate else (p.ifc_class[3:].upper() if p.ifc_class else "PART")
        sh.title_block(tb, "SINGLE", f"{prefix}{mark}", "", status)
        # allocation to assemblies, top right
        acols = [("Assembly Mark", 30), ("Quantity", 22)]
        arows = [[k, str(v)] for k, v in sorted(alloc.items(), key=lambda kv: _natural(kv[0]))] + [["Total", str(qty)]]
        if len(arows) > 14:
            arows = arows[:13] + [["...", ""], ["Total", str(qty)]]
        ar = sh.table(f.x1 - 52, f.y1, acols, arows, title_rows=["ALLOCATION TO ASSEMBLIES"],
                      aligns=["LEFT", "RIGHT"], tag="allocation")
        sh.taken.append(ar)
        # part table bottom left
        ds = Counter(round(h.d, 1) for h in p.holes)
        note = (f"All holes are {fmt_len(ds.most_common(1)[0][0])}mm U.N.O   " if ds else "") + \
            "Length & Area & Weight per piece"
        room = f.w - (150.0 if size == "A4" else 180.0) - 3.0
        base = [("Mark", 16), ("Profile", 32), ("Grade", 18), ("QTY", 10), ("Length", 15), ("Area", 12), ("Weight", 14)]
        k = min(1.4, room / sum(w for _, w in base))
        pcols = [(c, w * k) for c, w in base]
        prow = [[mark, p.description(), p.grade, str(qty), f"{p.length:.1f}", f"{p.area:.2f}", f"{p.weight:.1f}"]]
        hh = TXT_S if size == "A4" else TXT
        pt = sh.table(f.x, f.y + 3 * hh * 1.9, pcols, prow, h=hh, title_rows=[note],
                      aligns=["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"], tag="part")
        sh.taken.append(pt)
        req = f"{qty} Nos. {kind}    REQUIRED AS DRAWN MARKED  {mark}"
        rh = min(TXT_L, (room - 6) / (CW * len(req)))
        sh.text(req, f.x + 3, pt.y1 + 3, rh)
        sh.taken.append(Rect(f.x, pt.y1, room, rh + 6))
        area = Rect(f.x + 2, f.y + 2, f.w - 4, f.h - 4)
        L = max(p.ext[0], 1.0)
        for v in views:
            v.prepare()
        long_views = [v for v in views if v.name in ("FRONT", "TOP")]
        placed = None
        for s in SCALES:
            if L / s * (1.0 - BREAK_MAX_REMOVED) > area.w:
                continue
            for shorten in ((False, True) if long_views else (False,)):
                if shorten:
                    set_breaks(long_views, s)
                    if not long_views[0].breaks:
                        continue
                elif long_views:
                    clear_breaks(long_views)
                front = views[0]
                others = views[1:]
                blocks = []
                for v in [front] + [o for o in others if o.name == "TOP"]:
                    ml_, mb, mr, mt = _margins("part", True, bool(v.face_holes), False)
                    blocks.append((v, ml_, mb, mr, mt, v.wc / s, v.h / s))
                Wc = max(b[1] + b[5] + b[3] for b in blocks)
                Hc = sum(b[2] + b[6] + b[4] for b in blocks)
                end = [o for o in others if o.name == "END"]
                We = sum(9 + o.w / s + 22 for o in end)
                r = pack((Wc + We, Hc), area, sh.taken, gap=3.0, step=2.0, prefer="top-left")
                if r is not None:
                    placed = (s, r, blocks, end)
                    break
            if placed is not None:
                break
        if placed is None:
            continue
        s, r, blocks, end = placed
        step = max(0.8 * s, 0.5)
        for v in views:
            v.compute(step)
        lmax = max(b[1] for b in blocks)
        y = r.y
        for v, ml_, mb, mr, mt, w, h in blocks:
            v.scale, v.ox, v.oy = s, r.x + lmax, y + mb
            draw_view(sh, v, dims="part", balloons=False)
            y = v.oy + h + mt
        x = r.x + max(b[1] + b[5] + b[3] for b in blocks)
        for o in end:
            o.scale, o.ox, o.oy = s, x + 9, r.y + blocks[0][2]
            draw_view(sh, o, dims="none", balloons=False)
            x += 9 + o.w / s + 22
        # mark balloon and hole call-outs on the first view
        v = blocks[0][0]
        gx0, gy0 = v.P(v.bx0, v.by0)
        gx1, gy1 = v.P(v.bx1, v.by1)
        sh.balloon(mark, (gx0 + 0.25 * (gx1 - gx0), gy1 + 4.0) if not v.face_holes else (gx0 + 12, gy0 + (gy1 - gy0) * 0.5 + 0.0),
                   None)
        _hole_callouts(sh, v)
        sh.taken.append(r)
        _fill_scale(sh, _scale_txt(s))
        path = Path(out) / f"{prefix}{mark}.dxf"
        sh.save(path)
        return SheetResult("part", mark, str(path), size, s, [v.name for v in views], warnings, len(sh.overlaps()))
    raise RuntimeError(f"part {mark} does not fit")


def _hole_callouts(sh: Sheet, v: View):
    """'n Ø d' once per hole diameter, leader to the first hole of that diameter."""
    by_d: dict[float, list] = {}
    for h, p, c in v.face_holes:
        by_d.setdefault(round(h.d, 1), []).append(c)
    k = 0
    for d, cs in sorted(by_d.items()):
        x, y = v.P(*cs[0])
        tx, ty = x + 6 + 4 * k, y - 7 - 4 * k
        txt = f"{len(cs)} %%c{fmt_len(d)}"
        sh.text(txt, tx + 0.8, ty + 0.6, TXT_S, "HOLE", track=True)
        sh.line((x, y), (tx, ty), "HOLE")
        sh.line((tx, ty), (tx + CW * TXT_S * (len(txt) - 2) + 1.6, ty), "HOLE")
        k += 1


# ------------------------------------------------------------------ NC
def _plate_outline(p: Part):
    """(exterior [(x,y)], interiors [[(x,y)]]) in the plate frame (mm), round holes removed."""
    import shapely
    from shapely.ops import unary_union

    o, e1, e2, n = p.frame
    xy = np.stack([(p.verts - o) @ e1, (p.verts - o) @ e2], 1)
    T = xy[p.tris]
    a2 = ((T[:, 1, 0] - T[:, 0, 0]) * (T[:, 2, 1] - T[:, 0, 1]) - (T[:, 2, 0] - T[:, 0, 0]) * (T[:, 1, 1] - T[:, 0, 1]))
    T = T[np.abs(a2) > 1e-6]
    poly = unary_union(shapely.polygons(T)).buffer(0.01, join_style="mitre").buffer(-0.01, join_style="mitre")
    if poly.geom_type == "MultiPolygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    poly = poly.simplify(0.05)
    holes2 = [((h.center - o) @ e1, (h.center - o) @ e2, h.d) for h in p.holes]
    inter = []
    for ring in poly.interiors:
        c = ring.centroid
        if any(math.hypot(c.x - hx, c.y - hy) < d / 2 + 0.5 for hx, hy, d in holes2):
            continue          # a round hole: written from the opening, not the facets
        inter.append(list(ring.coords))
    return list(poly.exterior.coords), inter, holes2


def plate_nc(mark: str, p: Part, qty: int, out_dxf: Path, out_nc1: Path, project_no: str = ""):
    """NC DXF (CUT / HOLE / TEXT, 1:1) and DSTV nc1 for one plate."""
    import ezdxf

    ext, inter, holes = _plate_outline(p)
    doc = ezdxf.new("R2010")
    doc.units = ezdxf.units.MM
    for nm, col in (("CUT", 7), ("HOLE", 1), ("TEXT", 3), ("SCRIBE", 5)):
        doc.layers.add(nm, color=col)
    msp = doc.modelspace()
    msp.add_lwpolyline(ext, close=True, dxfattribs={"layer": "CUT"})
    for ring in inter:
        msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": "CUT"})
    for x, y, d in holes:
        msp.add_circle((x, y), d / 2, dxfattribs={"layer": "HOLE"})
    msp.add_text(f"{mark}  PL{fmt_len(p.thickness)}  {p.grade}  x{qty}", height=5,
                 dxfattribs={"layer": "TEXT"}).set_placement((0, -10))
    doc.saveas(str(out_dxf))

    L, W, t = p.ext
    kg_m = p.weight / (L / 1000.0) if L else 0.0
    m2_m = p.area / (L / 1000.0) if L else 0.0

    def num(x):
        return f"{x:12.2f}"

    lines = ["ST", f"** {mark}.nc1", f"  {project_no}", f"  {mark}", "  1", f"  {mark}", f"  {p.grade}", f"  {qty}",
             f"  PL{fmt_len(t)}", "  B", num(L), num(W), num(t), num(t), num(t), num(0.0), f"{kg_m:12.3f}",
             f"{m2_m:12.3f}", num(0.0), num(0.0), num(0.0), num(0.0), "", "", "", "", "AK"]
    for x, y in ext:
        lines.append(f"  v{num(x)}{num(y)}{num(0.0)}")
    for ring in inter:
        lines.append("IK")
        for x, y in ring:
            lines.append(f"  v{num(x)}{num(y)}{num(0.0)}")
    if holes:
        lines.append("BO")
        for x, y, d in holes:
            lines.append(f"  v{num(x)}{num(y)}{num(d)}")
    lines.append("EN")
    Path(out_nc1).write_text("\n".join(lines) + "\n", encoding="ascii", errors="replace")
