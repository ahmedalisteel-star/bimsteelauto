"""
Sheet drafting primitives for the fabrication drawings - paper millimetres.

Everything is drawn in model space at PAPER size (1 unit = 1 mm on the
printed sheet); views are scaled down onto it. A number written on a sheet
is the MODEL value (``DIMLFAC`` = view scale on real DIMENSION entities, the
model value in the text of running dimensions), and every running-dimension
text carries XDATA naming the view and the tick it belongs to, so the G3
round-trip can read the sheet back without trusting it.

Layers::

    FRAME   border, title block, tables          PART    visible steel
    HIDDEN  hidden steel (dashed)                HOLE    hole symbols
    CENTER  centre lines                         DIM     dimensions
    MARK    part-mark balloons and leaders       TEXT    notes, titles
    STAMP   status stamp                         BSA_VIEW  view definitions (not plotted)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import ezdxf
from ezdxf.enums import TextEntityAlignment

__all__ = ["PAPER", "SCALES", "Sheet", "Rect", "fmt_len", "pack"]

PAPER = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0)}
#: Usual shop-drawing scales (denominators), smallest first.
SCALES = (1, 2, 2.5, 5, 7.5, 10, 12.5, 15, 20, 25, 30, 40, 50, 60, 75, 100, 125, 150, 200, 250)
APP = "BSA_FABDRAW"          # XDATA application id

TXT = 2.5                    # mm - dimension and table text
TXT_S = 2.0
TXT_L = 3.5
CW = 0.80                    # character width / height allowed for the sheet font (OpenSans caps run ~0.78)


def fmt_len(x: float) -> str:
    """A shop length: whole mm, or one decimal when it is not whole (e.g. 6.5 mm)."""
    r = round(x)
    return str(int(r)) if abs(x - r) < 0.05 else f"{x:.1f}"


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def x1(self):
        return self.x + self.w

    @property
    def y1(self):
        return self.y + self.h

    def hits(self, o: "Rect", gap: float = 0.0) -> bool:
        return not (self.x1 + gap <= o.x or o.x1 + gap <= self.x or self.y1 + gap <= o.y or o.y1 + gap <= self.y)

    def inside(self, o: "Rect") -> bool:
        return self.x >= o.x - 1e-6 and self.y >= o.y - 1e-6 and self.x1 <= o.x1 + 1e-6 and self.y1 <= o.y1 + 1e-6


def pack(size: tuple[float, float], area: Rect, taken: list[Rect], gap: float = 4.0, step: float = 2.0,
         prefer: str = "top-left") -> Rect | None:
    """The first free position for a ``size`` box inside ``area`` clear of ``taken``."""
    w, h = size
    if w > area.w or h > area.h:
        return None
    ys = [area.y1 - h - i * step for i in range(int((area.h - h) / step) + 1)]
    if prefer == "bottom-left":
        ys = ys[::-1]
    xs = [area.x + i * step for i in range(int((area.w - w) / step) + 1)]
    for y in ys:
        for x in xs:
            r = Rect(x, y, w, h)
            if not any(r.hits(t, gap) for t in taken):
                return r
    return None


class Sheet:
    """One drawing sheet. ``frame`` is the inside of the border."""

    def __init__(self, size: str = "A3"):
        self.size = size
        self.W, self.H = PAPER[size]
        self.doc = ezdxf.new("R2010", setup=True)
        self.doc.units = ezdxf.units.MM
        self.doc.header["$LTSCALE"] = 0.4
        self.doc.header["$MEASUREMENT"] = 1
        self.doc.appids.add(APP)
        for name, color, lt, lw in (("FRAME", 7, "CONTINUOUS", 35), ("PART", 7, "CONTINUOUS", 35),
                                    ("HIDDEN", 8, "DASHED", 18), ("HOLE", 1, "CONTINUOUS", 25),
                                    ("CENTER", 4, "CENTER", 13), ("DIM", 3, "CONTINUOUS", 13),
                                    ("MARK", 5, "CONTINUOUS", 18), ("TEXT", 7, "CONTINUOUS", 18),
                                    ("STAMP", 1, "CONTINUOUS", 35), ("BSA_VIEW", 8, "CONTINUOUS", 0)):
            la = self.doc.layers.add(name, color=color, linetype=lt)
            la.dxf.lineweight = lw
        self.doc.layers.get("BSA_VIEW").dxf.plot = 0
        ds = self.doc.dimstyles.new("BSA")
        ds.dxf.dimtxt = TXT
        ds.dxf.dimasz = 2.0
        ds.dxf.dimexo = 1.0
        ds.dxf.dimexe = 1.5
        ds.dxf.dimgap = 0.8
        ds.dxf.dimdec = 0
        ds.dxf.dimtad = 1
        ds.dxf.dimtih = 0
        ds.dxf.dimtoh = 0
        ds.dxf.dimclrd = 3
        ds.dxf.dimclre = 3
        ds.dxf.dimclrt = 7
        ds.dxf.dimtxsty = "OpenSans"
        self.msp = self.doc.modelspace()
        self.frame = Rect(20.0, 10.0, self.W - 30.0, self.H - 20.0)
        self.taken: list[Rect] = []
        self.texts: list[Rect] = []          # every text box written, for the overlap check

    # ------------------------------------------------------------ primitives
    def line(self, a, b, layer="PART"):
        self.msp.add_line(a, b, dxfattribs={"layer": layer})

    def rect(self, r: Rect, layer="FRAME"):
        self.msp.add_lwpolyline([(r.x, r.y), (r.x1, r.y), (r.x1, r.y1), (r.x, r.y1)], close=True,
                                dxfattribs={"layer": layer})

    def circle(self, c, r, layer="HOLE"):
        self.msp.add_circle(c, r, dxfattribs={"layer": layer})

    def text(self, s, x, y, h=TXT, layer="TEXT", align="LEFT", rot=0.0, xdata=None, track=True):
        s = str(s)
        al = {"LEFT": TextEntityAlignment.BOTTOM_LEFT, "CENTER": TextEntityAlignment.BOTTOM_CENTER,
              "RIGHT": TextEntityAlignment.BOTTOM_RIGHT, "MIDDLE": TextEntityAlignment.MIDDLE_CENTER,
              "MLEFT": TextEntityAlignment.MIDDLE_LEFT, "MRIGHT": TextEntityAlignment.MIDDLE_RIGHT}[align]
        t = self.msp.add_text(s, height=h, rotation=rot, dxfattribs={"layer": layer, "style": "OpenSans"})
        t.set_placement((x, y), align=al)
        if xdata:
            t.set_xdata(APP, [(1000, f"{k}={v}") for k, v in xdata.items()])
        if track:
            w = CW * h * len(s)
            if abs(rot) < 1:
                dx = {"LEFT": 0, "MLEFT": 0, "CENTER": -w / 2, "MIDDLE": -w / 2, "RIGHT": -w, "MRIGHT": -w}[align]
                dy = -h / 2 if align in ("MIDDLE", "MLEFT", "MRIGHT") else 0
                self.texts.append(Rect(x + dx, y + dy, w, h))
            else:      # 90 deg: reads bottom-up, box sits left of x
                dy = {"LEFT": 0, "MLEFT": 0, "CENTER": -w / 2, "MIDDLE": -w / 2, "RIGHT": -w, "MRIGHT": -w}[align]
                dx = h / 2 if align in ("MIDDLE", "MLEFT", "MRIGHT") else 0
                self.texts.append(Rect(x - h + dx, y + dy, h, w))
        return t

    # ------------------------------------------------------------ dimensions
    def linear_dim(self, p1, p2, base, scale: float, angle: float = 0.0, xdata=None, text: str | None = None):
        """A real DIMENSION; its measurement is the model length (DIMLFAC = scale).

        ``text`` overrides the measurement where the view is shortened (the paper length is no longer
        the model length); G3 then checks the text against the break-corrected length.
        """
        d = self.msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle, dimstyle="BSA", text=text or "<>",
                                    override={"dimlfac": scale, "dimtxsty": "OpenSans"},
                                    dxfattribs={"layer": "DIM"})
        d.render()
        if xdata:
            d.dimension.set_xdata(APP, [(1000, f"{k}={v}") for k, v in xdata.items()])
        # text box for the overlap check: centred over the dimension line
        val = text or fmt_len(abs((p2[0] - p1[0]) if angle == 0 else (p2[1] - p1[1])) * scale)
        w = CW * TXT * len(val)
        if angle == 0:
            cx = (p1[0] + p2[0]) / 2
            self.texts.append(Rect(cx - w / 2, base[1] + 0.8, w, TXT))
        else:
            cy = (p1[1] + p2[1]) / 2
            self.texts.append(Rect(base[0] - 0.8 - TXT, cy - w / 2, TXT, w))
        return d

    def running_dim(self, zero: float, ticks: list[float], values: list[float], at: float, ref: float,
                    axis: str = "x", side: int = 1, view: str = "", xdata_extra=None, h: float = TXT):
        """Tekla running dimension: one line, a tick per feature, the cumulative value at each tick.

        ``axis='x'``: line horizontal at y=``at``, ticks at x=``ticks``; extension lines run from
        ``ref`` (the feature side) to the line. ``side`` +1 puts text above / right of the line.
        ``values`` are the MODEL values written; XDATA ties each to its tick for G3.
        """
        if not ticks:
            return
        allt = [zero] + list(ticks)
        lo, hi = min(allt), max(allt)
        ext = 1.5
        if axis == "x":
            self.line((lo, at), (hi, at), "DIM")
            for x in allt:
                self.line((x, ref), (x, at + side * ext), "DIM")
                self.line((x - 0.9, at - 0.9 * side), (x + 0.9, at + 0.9 * side), "DIM")
        else:
            self.line((at, lo), (at, hi), "DIM")
            for y in allt:
                self.line((ref, y), (at + side * ext, y), "DIM")
                self.line((at - 0.9 * side, y - 0.9), (at + 0.9 * side, y + 0.9), "DIM")
        # text positions: at each tick, pushed apart where they would touch
        order = sorted(range(len(allt)), key=lambda i: allt[i])
        vals = [0.0] + list(values)
        pos, last = {}, -1e9
        for i in order:
            p = allt[i]
            p = max(p, last + h * 1.15)
            pos[i] = p
            last = p
        for i in order:
            s = fmt_len(vals[i])
            xd = {"view": view, "axis": axis, "zero": f"{zero:.4f}", "tick": f"{allt[i]:.4f}", "value": s}
            if xdata_extra:
                xd.update(xdata_extra)
            if axis == "x":
                tx = pos[i] + h * 0.45
                if abs(pos[i] - allt[i]) > 0.05:          # moved: a short leader back to the tick
                    self.line((allt[i], at + side * ext), (pos[i], at + side * (ext + 1.2)), "DIM")
                self.text(s, tx, at + side * (ext + 0.6), h, "DIM", align="LEFT" if side > 0 else "RIGHT",
                          rot=90.0, xdata=xd)
            else:
                ty = pos[i] - h * 0.45 if side > 0 else pos[i] - h * 0.45
                if abs(pos[i] - allt[i]) > 0.05:
                    self.line((at + side * ext, allt[i]), (at + side * (ext + 1.2), pos[i]), "DIM")
                self.text(s, at + side * (ext + 0.6), ty, h, "DIM", align="LEFT" if side > 0 else "RIGHT",
                          xdata=xd)

    # ------------------------------------------------------------ marks, tables, blocks
    def balloon(self, s, at, target, h=TXT):
        """Part mark in a rounded box at ``at`` with a leader to ``target``."""
        w = CW * h * len(s) + 2.0
        x, y = at
        r = Rect(x - w / 2, y - h * 0.8, w, h * 1.6)
        pts = [(r.x + 0.8, r.y), (r.x1 - 0.8, r.y), (r.x1, y), (r.x1 - 0.8, r.y1), (r.x + 0.8, r.y1), (r.x, y)]
        self.msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "MARK"})
        self.text(s, x, y, h, "MARK", align="MIDDLE", track=False)
        self.texts.append(r)
        if target is not None:
            tx, ty = target
            ax = min(max(tx, r.x), r.x1)
            ay = r.y if ty < r.y else (r.y1 if ty > r.y1 else ty)
            if math.hypot(tx - ax, ty - ay) > 1.0:
                self.line((ax, ay), (tx, ty), "MARK")
                self.msp.add_circle((tx, ty), 0.35, dxfattribs={"layer": "MARK"})
        return r

    def table(self, x, y_top, cols, rows, h=TXT, title_rows=(), aligns=None, tag: str = "") -> Rect:
        """Ruled table, top-left at (x, y_top). ``cols`` = [(header, width)]. Returns its rect.

        With ``tag`` every body cell carries XDATA ``tbl, row, col`` so G3 can read the table back.
        """
        rh = h * 1.9
        W = sum(w for _, w in cols)
        y = y_top
        for t in title_rows:
            self.text(t, x + 1.0, y - rh + h * 0.45, h)
            y -= rh
            self.line((x, y), (x + W, y), "FRAME")
        lines = [[c for c, _ in cols]] + rows
        for r_i, r in enumerate(lines):
            yy = y - (r_i + 1) * rh
            cx = x
            for c_i, ((_, w), v) in enumerate(zip(cols, r)):
                al = (aligns[c_i] if aligns else "LEFT") if r_i else "LEFT"
                v = str(v)
                hh = min(h, (w - 1.6) / (CW * max(len(v), 1)))       # shrink to fit the cell
                xd = {"tbl": tag, "row": r_i - 1, "col": cols[c_i][0]} if tag and r_i else None
                if al == "RIGHT":
                    self.text(v, cx + w - 1.0, yy + h * 0.45, hh, align="RIGHT", xdata=xd)
                else:
                    self.text(v, cx + 1.0, yy + h * 0.45, hh, xdata=xd)
                cx += w
            self.line((x, yy), (x + W, yy), "FRAME")
        bottom = y - len(lines) * rh
        cx = x
        for _, w in cols + [("", 0)]:
            self.line((cx, y), (cx, bottom), "FRAME")
            cx += w
        R = Rect(x, bottom, W, y_top - bottom)
        self.rect(R)
        return R

    def border(self):
        self.rect(Rect(0, 0, self.W, self.H), "BSA_VIEW")       # trim line, not plotted
        self.rect(self.frame, "FRAME")

    def title_block(self, tb: dict, title: str, dwg_no: str, scale_txt: str, status: str) -> Rect:
        """Title + revision block at the bottom right, Tekla layout. Returns its rect."""
        f = self.frame
        w = 180.0 if self.size != "A4" else 150.0
        x0, y0 = f.x1 - w, f.y
        row = 6.0
        revs = (tb.get("revisions") or [{"rev": tb.get("rev", "0"), "desc": tb.get("rev_desc", ""),
                                          "by": tb.get("drawn_by_initials", ""), "chk": tb.get("checked_by", ""),
                                          "date": tb.get("date", "")}])[-3:]
        h_total = row * (len(revs) + 1) + 7 + 18 + 14 + row
        R = Rect(x0, y0, w, h_total)
        y = y0
        # bottom row: DWG NO | SCALE | REV | size
        c = [0, w * 0.45, w * 0.75, w - 9]
        self.line((x0, y + row), (x0 + w, y + row), "FRAME")
        for k in c[1:]:
            self.line((x0 + k, y), (x0 + k, y + row), "FRAME")
        self.text(f"DWG NO: {dwg_no}", x0 + 1.5, y + 1.6, TXT)
        self.text(f"SCALE: {scale_txt}", x0 + c[1] + 1.5, y + 1.6, TXT)
        self.text(f"REV: {revs[-1].get('rev', '')}", x0 + c[2] + 1.5, y + 1.6, TXT)
        self.text(self.size, x0 + c[3] + 1.0, y + 1.6, TXT)
        y += row
        # title
        self.text("TITLE:", x0 + 1.5, y + 14 - TXT - 1.5, TXT)
        self.text(title, x0 + w / 2, y + 2.0, TXT_L, align="CENTER")
        y += 14
        self.line((x0, y), (x0 + w, y), "FRAME")
        # drawn by | company
        self.line((x0 + w * 0.55, y), (x0 + w * 0.55, y + 18), "FRAME")
        self.text("DRAWING BY :", x0 + 1.5, y + 18 - TXT - 1.5, TXT)
        self.text(tb.get("drawn_by", ""), x0 + 1.5, y + 3.0, TXT)
        self.text("COMPANY", x0 + w * 0.55 + 1.5, y + 18 - TXT - 1.5, TXT)
        self.text(tb.get("company", ""), x0 + w * 0.775, y + 3.0, TXT_L, align="CENTER")
        y += 18
        self.line((x0, y), (x0 + w, y), "FRAME")
        # project | project no
        proj = str(tb.get("project", ""))
        hp = TXT if len(proj) * CW * TXT < w * 0.68 - 14 else max(1.6, (w * 0.68 - 14) / (CW * max(len(proj), 1)))
        self.text(f"PROJECT: {proj}", x0 + 1.5, y + 2.2, hp)
        self.line((x0 + w * 0.7, y), (x0 + w * 0.7, y + 7), "FRAME")
        self.text(f"NO: {tb.get('project_no', '')}", x0 + w * 0.7 + 1.5, y + 2.2, TXT)
        y += 7
        self.line((x0, y), (x0 + w, y), "FRAME")
        # revision rows (header at the bottom, like Tekla)
        cw = [0, 12, w - 46, w - 34, w - 22, w]
        self.text("REV", x0 + 1.0, y + 1.6, TXT_S)
        self.text("DESCRIPTION", x0 + (cw[1] + cw[2]) / 2, y + 1.6, TXT_S, align="CENTER")
        self.text("DWN", x0 + cw[2] + 1, y + 1.6, TXT_S)
        self.text("CHK", x0 + cw[3] + 1, y + 1.6, TXT_S)
        self.text("DATE", x0 + cw[4] + 1, y + 1.6, TXT_S)
        for i, rv in enumerate(revs[::-1]):
            yy = y + row * (i + 1)
            self.line((x0, yy), (x0 + w, yy), "FRAME")
            self.text(rv.get("rev", ""), x0 + 1.0, yy + 1.6, TXT_S)
            self.text(rv.get("desc", ""), x0 + cw[1] + 1.0, yy + 1.6, TXT_S)
            self.text(rv.get("by", ""), x0 + cw[2] + 1, yy + 1.6, TXT_S)
            self.text(rv.get("chk", ""), x0 + cw[3] + 1, yy + 1.6, TXT_S)
            self.text(rv.get("date", ""), x0 + cw[4] + 1, yy + 1.6, TXT_S)
        top = y + row * (len(revs) + 1)
        self.line((x0, top), (x0 + w, top), "FRAME")
        for k in cw[1:-1]:
            self.line((x0 + k, y), (x0 + k, top), "FRAME")
        R = Rect(x0, y0, w, top - y0)
        self.rect(R)
        if status:
            self.text(status, x0 + w / 2, top + 2.0, TXT_L, "STAMP", align="CENTER")
            R = Rect(x0, y0, w, top - y0 + 2.0 + TXT_L + 1)
        self.taken.append(R)
        return R

    def save(self, path):
        self.doc.saveas(str(path))

    # ------------------------------------------------------------ checks
    def overlaps(self) -> list[tuple[Rect, Rect]]:
        out = []
        T = self.texts
        for i in range(len(T)):
            for j in range(i + 1, len(T)):
                a, b = T[i], T[j]
                if a.hits(b, gap=-0.25):
                    out.append((a, b))
        return out
