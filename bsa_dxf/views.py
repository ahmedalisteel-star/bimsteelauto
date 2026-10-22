"""
bsa_dxf.views
=============

Sheet composers.  A composer takes a design result and lays a whole sheet
out: geometry, grid, dimensions, marks, schedule, details, border, title
block and stamp.

Why no coordinate transform
---------------------------
The sheet is model space at 1:1 in millimetres, and the sheet border is drawn
``scale`` times bigger than the paper it will plot on.  A frame 30 000 mm
wide therefore draws as 30 000 model units on a 1:50 A1 whose border is
42 050 model units wide - and plots at exactly 600 mm.  The building is drawn
at full size and only the paper is scaled, so no view ever needs a scaling
transform, and no dimension can ever disagree with the steel it measures.
The only thing a view does to its geometry is move it to where it sits on the
sheet.
"""

from __future__ import annotations

import math

from . import blocks
from .annotate import dim_chain, leader, level, member_mark, view_title
from .details import detail_view
from .geom import frame_from_result
from .sheet import TitleBlock, draw_sheet, frame_area, notes_top
from .standards import TEXT_ISO_BOLD, Standard
from .tables import member_schedule, taper_schedule
from .util import rect, text

__all__ = ["pick_scale", "frame_elevation", "SheetResult"]

#: Scales a drawing office will actually put in a title block.
SCALES = (5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 500)

#: Paper millimetres reserved on the sheet, A1 reference layout.
_TB_W = 180.0
_DETAIL_H = 96.0
_TABLE_H = 34.0


class SheetResult:
    """What one sheet came out as."""

    def __init__(self, name: str, path: str = "", scale: str = ""):
        self.name = name
        self.path = path
        self.scale = scale
        self.warnings: list[str] = []
        self.details: list = []

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SheetResult {self.name} scale={self.scale} warn={len(self.warnings)}>"


def pick_scale(model_w: float, model_h: float,
               avail_w_mm: float, avail_h_mm: float) -> float:
    """The largest office scale at which the model still fits the space.

    ``model_*`` are millimetres of building; ``avail_*`` are millimetres of
    paper.  Returns the scale denominator.
    """
    for s in SCALES:
        if model_w / s <= avail_w_mm and model_h / s <= avail_h_mm:
            return float(s)
    return float(SCALES[-1])


# ---------------------------------------------------------------------------


def frame_elevation(doc, result: dict, tb: TitleBlock, *,
                    sheet: str = "A1", scale: float | None = None,
                    details: bool = True) -> SheetResult:
    """Compose the frame elevation sheet onto ``doc``'s model space.

    The sheet carries, from the top down: the rafter taper schedule, the
    frame elevation with its grid, levels, member marks and detail callouts,
    then a strip of connection details, then the title block.
    """
    from .standards import Standard as _Std, apply as _apply

    frame = frame_from_result(result)
    geo = result.get("spec", {}).get("geometry", {}) or {}

    # --- scale -----------------------------------------------------------
    if scale is None:
        from .standards import SHEET_SIZES
        sw, sh = SHEET_SIZES[sheet]
        avail_w = sw - 20.0 - 10.0 - 40.0
        avail_h = sh - 10.0 - 10.0 - _DETAIL_H - _TABLE_H - 60.0
        scale = pick_scale(frame.width, frame.ridge_height, avail_w, avail_h)

    std = _Std(scale=float(scale), sheet=sheet)
    _apply(doc, std)
    blocks.define_all(doc, std)
    msp = doc.modelspace()

    res = SheetResult("frame elevation", scale=f"1:{scale:g}")
    tb.scale = res.scale

    # --- zones ------------------------------------------------------------
    fx0, fy0, fx1, fy1 = frame_area(std)
    tb_left = fx1 - std.paper(_TB_W)
    detail_top = fy0 + std.paper(_DETAIL_H)
    # The elevation spans the full width, so it also has to clear the notes
    # block and title block stacked in the right-hand column - not just the
    # detail strip below it.
    band_y0 = max(detail_top + std.paper(24.0),
                  notes_top(std, tb) + std.paper(12.0))
    band_y1 = fy1 - std.paper(_TABLE_H + 22.0)

    # --- place the frame --------------------------------------------------
    ox = fx0 + ((fx1 - fx0) - frame.width) / 2.0
    oy = band_y0 + ((band_y1 - band_y0) - frame.ridge_height) / 2.0

    def P(p):
        return (p[0] + ox, p[1] + oy)

    _draw_grid(msp, std, frame, ox, oy, band_y1)
    _draw_members(msp, std, frame, P)
    _draw_secondary(msp, std, frame, geo, P)
    _draw_levels(msp, std, frame, ox, oy)
    _draw_grid_dims(msp, std, frame, ox, oy)
    _draw_marks(msp, std, frame, P)

    callouts = _draw_callouts(msp, std, frame, result, P)

    view_title(
        msp, std,
        ((fx0 + tb_left) / 2, band_y0 - std.paper(14.0)),
        _frame_title(result), res.scale,
    )

    # --- member schedule, in the band the elevation does not fill ---------
    members = result.get("members") or []
    if members:
        member_schedule(
            msp, std, members, fx0,
            band_y0 - std.paper(26.0),
        )

    # --- taper schedule ---------------------------------------------------
    taper_schedule(
        msp, std, _shift(frame, ox, oy), fy1 - std.paper(8.0),
        splice_length=6000.0,
        purlin_spacing=float(geo.get("purlin_spacing") or 0) or None,
    )
    text(msp, "RAFTER FABRICATION SCHEDULE",
         (fx0, fy1 - std.paper(5.0)), std.text("head"),
         layer="S-ANNO-TEXT", align="bl", style=TEXT_ISO_BOLD)

    # --- detail strip ------------------------------------------------------
    if details:
        res.details = _draw_details(
            msp, std, result, callouts, fx0, fy0, tb_left - std.paper(4.0)
        )
        for d in res.details:
            for m in d.missing:
                res.warnings.append(f"{d.mark}: {m} not reported")

    draw_sheet(msp, std, tb)
    return res


def _frame_title(result: dict) -> str:
    geo = result.get("spec", {}).get("geometry", {}) or {}
    kind = (geo.get("frame_type") or "").replace("_", " ").upper()
    return f"FRAME SECTION{' - ' + kind if kind else ''}"


def _shift(frame, dx, dy):
    """A shallow copy of the frame moved onto the sheet."""
    import copy
    f = copy.deepcopy(frame)
    for m in f.members:
        m.start = (m.start[0] + dx, m.start[1] + dy)
        m.end = (m.end[0] + dx, m.end[1] + dy)
    f.columns_x = [x + dx for x in f.columns_x]
    f.ridges = [(x + dx, z + dy) for x, z in f.ridges]
    return f


# ---------------------------------------------------------------------------
# pieces of the elevation
# ---------------------------------------------------------------------------


def _draw_grid(msp, std, frame, ox, oy, top) -> None:
    """Vertical grid lines with a bubble at each end."""
    below = oy - std.paper(34.0)
    tag_y = below - std.paper(6.0)
    for i, x in enumerate(frame.columns_x):
        gx = x + ox
        msp.add_line((gx, tag_y + std.paper(6.0)), (gx, top),
                     dxfattribs={"layer": "S-GRID"})
        blocks.insert(msp, blocks.GRID_BUBBLE, (gx, tag_y), std,
                      text=_grid_tag(i), layer="S-GRID-IDEN")
        blocks.insert(msp, blocks.GRID_BUBBLE, (gx, top + std.paper(6.0)), std,
                      text=_grid_tag(i), layer="S-GRID-IDEN")


def _grid_tag(i: int) -> str:
    """A .. Z, then AA, AB ..."""
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(ord("A") + r) + out
    return out


def _draw_members(msp, std, frame, P) -> None:
    for m in frame.members:
        polys = m.outline()
        if not polys:
            continue
        silhouette, near, far = polys[0], polys[1], polys[2]
        msp.add_lwpolyline([P(p) for p in silhouette], close=True,
                           dxfattribs={"layer": "S-MEMB"})
        for line in (near, far):
            msp.add_line(P(line[0]), P(line[1]),
                         dxfattribs={"layer": "S-MEMB"})
        # work line
        msp.add_line(P(m.start), P(m.end),
                     dxfattribs={"layer": "S-MEMB-CL"})


def _draw_secondary(msp, std, frame, geo, P) -> None:
    """Purlins and girts as the small squares a GA shows them as."""
    spacing = float(geo.get("purlin_spacing") or 0)
    if spacing > 0:
        size = std.paper(1.2)
        for span_i, span in enumerate(frame.spans):
            x0 = sum(frame.spans[:span_i])
            n = max(1, int(span / spacing))
            for k in range(n + 1):
                x = x0 + span * k / n
                z = frame.roof_z(x)
                px, py = P((x, z))
                rect(msp, px - size, py, px + size, py + size * 2,
                     layer="S-CLAD")

    gspace = float(geo.get("girt_spacing") or 0)
    if gspace > 0:
        size = std.paper(1.2)
        for x in (frame.columns_x[0], frame.columns_x[-1]):
            side = 1 if x == frame.columns_x[0] else -1
            z = gspace
            while z < frame.eave_height - gspace * 0.4:
                px, py = P((x, z))
                rect(msp, px - size * side, py - size,
                     px + size * side * 3, py + size, layer="S-CLAD")
                z += gspace


def _draw_levels(msp, std, frame, ox, oy) -> None:
    x = frame.columns_x[-1] + ox + std.paper(12.0)
    for z, label in (
        (0.0, "T.O. BASE PLATE"),
        (frame.eave_height, "EAVE"),
        (frame.ridge_height, "RIDGE"),
    ):
        level(msp, std, (x, z + oy), z)
        # The tag already prints the value on its own bar, so the name goes
        # to the right of it, past the bar's 30 mm run.
        text(msp, label, (x + std.paper(32.0), z + oy + std.paper(4.6)),
             std.paper(2.0), layer="S-ANNO-SYMB", align="bl")

    # Vertical chain of the same levels on the left of the frame.
    bx = frame.columns_x[0] + ox - std.paper(22.0)
    dim_chain(msp, std,
              [oy, frame.eave_height + oy, frame.ridge_height + oy],
              bx, axis="v", overall=True, overall_gap=12.0)


def _draw_grid_dims(msp, std, frame, ox, oy) -> None:
    base = oy - std.paper(20.0)
    dim_chain(msp, std, [x + ox for x in frame.columns_x], base,
              axis="h", overall=True, overall_gap=10.0)
    # Ridge ordinates, on their own line closer to the frame.
    if frame.ridges:
        pts = [frame.columns_x[0] + ox]
        pts += [rx + ox for rx, _ in frame.ridges]
        pts.append(frame.columns_x[-1] + ox)
        dim_chain(msp, std, pts, oy - std.paper(8.0), axis="h", overall=False)


def _draw_marks(msp, std, frame, P) -> None:
    for m in frame.members:
        if m.member_type == "column":
            side = 1 if m.grow > 0 else -1
            member_mark(msp, std, _moved(m, P), offset=10.0, side=-side)
        else:
            member_mark(msp, std, _moved(m, P), offset=8.0, side=1)


def _moved(m, P):
    """A copy of a member positioned on the sheet, for annotation only."""
    import copy
    c = copy.copy(m)
    c.start = P(m.start)
    c.end = P(m.end)
    return c


def _draw_callouts(msp, std, frame, result, P) -> list[tuple[str, dict]]:
    """Put a detail bubble on the frame for every connection the engine has.

    Returns the connections in the order their bubbles were placed, so the
    detail strip below can be drawn in the same order.
    """
    conns = result.get("connections", []) or []
    by_kind: dict[str, list] = {}
    for c in conns:
        by_kind.setdefault(c.get("connection_type", ""), []).append(c)

    cols = [m for m in frame.members if m.member_type == "column"]
    rafs = [m for m in frame.members if m.member_type == "rafter"]

    anchors: list[tuple[str, dict, tuple[float, float]]] = []

    def first(kind):
        got = by_kind.get(kind) or []
        return got[0] if got else None

    def along(member, t):
        return (member.start[0] + (member.end[0] - member.start[0]) * t,
                member.start[1] + (member.end[1] - member.start[1]) * t)

    # (kind, anchor, bubble direction) - the direction keeps each bubble in
    # open paper rather than over the steel or over its neighbour.
    plan = []
    if rafs and cols:
        plan.append(("eave_end_plate", rafs[0].start, (-1.4, 0.9)))
    if frame.ridges:
        plan.append(("apex_end_plate", frame.ridges[0], (0.0, 1.6)))
    if cols:
        plan.append(("base_plate", cols[0].start, (-1.4, 0.6)))
    if rafs:
        plan.append(("purlin_cleat", along(rafs[0], 0.55), (-0.2, 1.5)))
        plan.append(("bracing_gusset", along(rafs[-1], 0.45), (0.6, 1.5)))

    out = []
    for kind, anchor, (fx, fy) in plan:
        conn = first(kind)
        if not conn:
            continue
        mark = conn.get("mark", kind)
        ax, ay = P(anchor)
        bx = ax + std.paper(26.0) * fx
        by = ay + std.paper(26.0) * fy
        msp.add_line((ax, ay), (bx, by), dxfattribs={"layer": "S-ANNO-SYMB"})
        blocks.insert(msp, blocks.DETAIL_BUBBLE, (bx, by), std,
                      text=mark, layer="S-ANNO-SYMB")
        out.append((mark, conn))
    return out


def _draw_details(msp, std, result, callouts, x0, y0, x_max) -> list:
    """The strip of connection details along the bottom of the sheet."""
    conns = [c for _, c in callouts]
    if not conns:
        conns = (result.get("connections") or [])[:4]

    box_w, gap = 78.0, 4.0
    n_fit = max(1, int((x_max - x0) / std.paper(box_w + gap)))
    out = []
    for i, conn in enumerate(conns[:n_fit]):
        ox = x0 + i * std.paper(box_w + gap)
        out.append(detail_view(
            msp, std, conn, (ox, y0),
            detail_scale=_detail_scale(conn, std, box_w),
            title=f"DETAIL - {conn.get('mark', '?')}",
            box_w=box_w, box_h=_DETAIL_H,
        ))
    return out


def _detail_scale(conn: dict, std: Standard, box_w: float) -> float:
    """Largest office scale at which the plate fits its panel."""
    geo = (conn.get("detail") or {}).get("geometry") or {}
    w = float(geo.get("width_mm") or 300.0)
    h = float(geo.get("height_mm") or 600.0)
    avail_w = box_w * 0.55
    avail_h = (_DETAIL_H - 20.0) * 0.80
    for s in (2, 5, 10, 20, 25, 50):
        if w / s <= avail_w and h / s <= avail_h:
            return float(s)
    return 50.0
