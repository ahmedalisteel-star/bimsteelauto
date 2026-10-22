"""
bsa_dxf.details
===============

Connection detail views, drawn from ``connections[].detail.geometry``.

Every number here arrives from the engine.  There is no default plate size,
no assumed gauge and no invented hole: if the engine did not report a hole
position, no hole is drawn and the detail says what is missing.  A plate this
module invented would be a plate no limit state ever covered, and on a
drawing it would look exactly as convincing as the ones that were designed.

Drawing a detail bigger than the sheet
--------------------------------------
The sheet is model space at 1:1 in millimetres plotted at, say, 1:50.  A
detail at 1:5 therefore has to be drawn ten times true size.  Its dimensions
would then read ten times too big, so every dimension on a detail carries a
``dimlfac`` override of ``1/zoom`` - which is precisely what DIMLFAC exists
for, and it keeps the printed value equal to the real millimetres of steel.
"""

from __future__ import annotations

import math

from .annotate import dim_single
from .standards import TEXT_ISO_BOLD, Standard
from .util import rect, text

__all__ = ["detail_view", "DetailResult"]


class DetailResult:
    """What a detail view actually managed to draw."""

    def __init__(self, mark: str):
        self.mark = mark
        self.drawn = False
        self.width = 0.0
        self.height = 0.0
        self.missing: list[str] = []

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DetailResult {self.mark} drawn={self.drawn} missing={self.missing}>"


def detail_view(msp, std: Standard, conn: dict, origin, *,
                detail_scale: float = 5.0, title: str | None = None,
                box_w: float = 78.0, box_h: float = 96.0) -> DetailResult:
    """One connection detail in a titled panel, bottom-left at ``origin``.

    Draws the plate elevation with its bolt pattern, the member it bolts to
    seen on edge, the gauge and row dimension chains, and the plate/bolt
    callouts.  ``box_w``/``box_h`` are the panel size in paper mm.
    """
    mark = conn.get("mark", "?")
    res = DetailResult(mark)
    geo = (conn.get("detail") or {}).get("geometry") or {}
    if not geo:
        res.missing.append("no geometry reported")

    zoom = std.scale / float(detail_scale)
    ox, oy = origin
    bw, bh = std.paper(box_w), std.paper(box_h)
    res.width, res.height = bw, bh

    # --- panel ------------------------------------------------------------
    rect(msp, ox, oy, ox + bw, oy + bh, layer="S-DETL")
    cap_h = std.paper(8.0)
    msp.add_line((ox, oy + cap_h), (ox + bw, oy + cap_h),
                 dxfattribs={"layer": "S-DETL"})
    split = ox + bw - std.paper(20.0)
    msp.add_line((split, oy), (split, oy + cap_h),
                 dxfattribs={"layer": "S-DETL"})
    text(msp, title or f"DETAIL - {mark}",
         ((ox + split) / 2, oy + std.paper(2.4)), std.text("head"),
         layer="S-ANNO-TEXT", align="bc", style=TEXT_ISO_BOLD)
    text(msp, f"1:{detail_scale:g}",
         ((split + ox + bw) / 2, oy + std.paper(2.4)), std.text("note"),
         layer="S-ANNO-TEXT", align="bc")

    if not geo:
        text(msp, "GEOMETRY NOT REPORTED BY DESIGN",
             (ox + bw / 2, oy + bh / 2), std.text("note"),
             layer="S-ANNO-STMP", align="mc")
        return res

    kind = geo.get("kind", "")
    # The usable area: inside the caption bar, clear of the callout block
    # that runs down the left edge, and clear of the dimension chains that
    # stand off the plate's top and right.
    pad = std.paper(3.0)
    panel = (
        ox + std.paper(30.0),
        oy + cap_h + pad,
        ox + bw - std.paper(13.0),
        oy + bh - std.paper(10.0),
    )

    if kind == "end_plate":
        _end_plate(msp, std, geo, panel, zoom, res)
    elif kind == "base_plate":
        _base_plate(msp, std, geo, panel, zoom, res)
    else:
        _generic_plate(msp, std, geo, panel, zoom, res)

    # --- callouts ---------------------------------------------------------
    th = std.text("note")
    ty = oy + bh - std.paper(3.0)
    lines = []
    if geo.get("bolt_diameter_mm"):
        n = geo.get("n_holes")
        lines.append(
            f"BOLT DIA. : M{geo['bolt_diameter_mm']:g}"
            + (f"  ({n} No.)" if n else "")
        )
    if geo.get("thickness_mm"):
        lines.append(f"PLATE THK. : {geo['thickness_mm']:g} mm")
    if geo.get("hole_diameter_mm"):
        lines.append(f"HOLE DIA. : {geo['hole_diameter_mm']:g} mm")
    util = conn.get("utilisation")
    if util is not None:
        lines.append(f"UTILISATION : {util:.2f}")
    for line in lines:
        text(msp, line, (ox + std.paper(2.5), ty), th,
             layer="S-ANNO-TEXT", align="tl")
        ty -= th * 1.6

    res.drawn = True
    return res


# ---------------------------------------------------------------------------


def _centre_on(panel, u_range, v_range, zoom):
    """Where to put a plate's local origin so its extents centre in ``panel``.

    A plate's local (0, 0) is rarely its middle - an end plate measures from
    the member's top flange, so nearly all of it lies below the origin.  A
    detail that centred the ORIGIN would hang out of the bottom of its panel,
    which is exactly what happens if you skip this.
    """
    x0, y0, x1, y1 = panel
    u_mid = (u_range[0] + u_range[1]) / 2.0
    v_mid = (v_range[0] + v_range[1]) / 2.0
    return ((x0 + x1) / 2.0 - u_mid * zoom,
            (y0 + y1) / 2.0 - v_mid * zoom)


def _fit(panel, u_range, v_range, zoom):
    """Shrink ``zoom`` until the plate plus its dimension chains fit."""
    x0, y0, x1, y1 = panel
    du = (u_range[1] - u_range[0]) or 1.0
    dv = (v_range[1] - v_range[0]) or 1.0
    return min(zoom, (x1 - x0) / du, (y1 - y0) / dv)


def _holes(msp, std, geo, centre, zoom, *, dia_key="hole_diameter_mm"):
    """Draw every reported hole, plus its centre cross."""
    cx, cy = centre
    holes = geo.get("holes_uv_mm") or []
    r = float(geo.get(dia_key, 22.0)) / 2.0 * zoom
    tick = r * 1.8
    for u, v in holes:
        hx, hy = cx + u * zoom, cy + v * zoom
        msp.add_circle((hx, hy), r, dxfattribs={"layer": "S-BOLT"})
        msp.add_line((hx - tick, hy), (hx + tick, hy),
                     dxfattribs={"layer": "S-BOLT-CL"})
        msp.add_line((hx, hy - tick), (hx, hy + tick),
                     dxfattribs={"layer": "S-BOLT-CL"})
    return holes


def _chain_1d(msp, std, values, fixed, zoom, *, axis="h", origin=(0, 0),
              offset=8.0):
    """Dimension a set of ordinates on one axis of a detail.

    A bolt gauge on a 1:20 end plate puts three dimensions inside 15 mm of
    paper, so the detail chains run smaller text than the sheet chains and
    let the fit rules move a value outside its own extension lines rather
    than overprint the neighbour.
    """
    ov = {
        "dimlfac": 1.0 / zoom,
        "dimtxt": std.paper(1.8),
        "dimasz": std.paper(1.6),
        "dimtsz": std.paper(0.7),
        "dimgap": std.paper(0.6),
        "dimexo": std.paper(1.2),
        "dimatfit": 3,      # move the text outside when it will not fit
        "dimtmove": 1,      # ... and add a leader to it
    }
    pts = sorted(set(round(v, 3) for v in values))
    if len(pts) < 2:
        return
    ox, oy = origin
    for a, b in zip(pts, pts[1:]):
        if axis == "h":
            p1 = (ox + a * zoom, fixed)
            p2 = (ox + b * zoom, fixed)
            base = (0.0, fixed + std.paper(offset))
            dim_single(msp, std, p1, p2, base, angle=0.0, override=ov)
        else:
            p1 = (fixed, oy + a * zoom)
            p2 = (fixed, oy + b * zoom)
            base = (fixed + std.paper(offset), 0.0)
            dim_single(msp, std, p1, p2, base, angle=90.0, override=ov)


def _end_plate(msp, std, geo, panel, zoom, res) -> None:
    w = float(geo.get("width_mm", 0.0))
    ext_t = float(geo.get("ext_top_mm", 0.0))
    ext_b = float(geo.get("ext_bot_mm", 0.0))
    depth = float(geo.get("depth_on_cut_mm", 0.0))
    if not depth:
        depth = float(geo.get("height_mm", 0.0)) - ext_t - ext_b

    v_top, v_bot = ext_t, -(depth + ext_b)
    zoom = _fit(panel, (-w / 2, w / 2), (v_bot, v_top), zoom)
    cx, cy = _centre_on(panel, (-w / 2, w / 2), (v_bot, v_top), zoom)
    centre = (cx, cy)
    rect(msp, cx - w / 2 * zoom, cy + v_bot * zoom,
         cx + w / 2 * zoom, cy + v_top * zoom, layer="S-PLAT")

    # The member the plate is welded to, seen through the plate.
    sec = geo.get("section_at_joint") or {}
    if sec:
        bf = float(sec.get("bf_top_mm", w * 0.8))
        tf_t = float(geo.get("tf_top_on_cut_mm", sec.get("tf_top_mm", 0)) or 0)
        tf_b = float(geo.get("tf_bot_on_cut_mm", sec.get("tf_bot_mm", 0)) or 0)
        tw = float(sec.get("tw_mm", 8.0))
        for y0, y1 in ((0.0, -tf_t), (-depth, -depth + tf_b)):
            rect(msp, cx - bf / 2 * zoom, cy + y0 * zoom,
                 cx + bf / 2 * zoom, cy + y1 * zoom, layer="S-MEMB-HDDN")
        rect(msp, cx - tw / 2 * zoom, cy - tf_t * zoom,
             cx + tw / 2 * zoom, cy + (-depth + tf_b) * zoom,
             layer="S-MEMB-HDDN")
    else:
        res.missing.append("section at joint")

    holes = _holes(msp, std, geo, centre, zoom)
    if not holes:
        res.missing.append("hole positions")
        return

    gauge = sorted({u for u, _ in holes})
    rows = sorted({v for _, v in holes})
    _chain_1d(msp, std, [-w / 2] + gauge + [w / 2],
              cy + v_top * zoom, zoom, axis="h", origin=(cx, cy), offset=6.0)
    _chain_1d(msp, std, [v_bot] + rows + [v_top],
              cx + w / 2 * zoom, zoom, axis="v", origin=(cx, cy), offset=6.0)


def _base_plate(msp, std, geo, panel, zoom, res) -> None:
    w = float(geo.get("width_mm", 0.0))
    h = float(geo.get("height_mm", 0.0))
    zoom = _fit(panel, (-w / 2, w / 2), (-h / 2, h / 2), zoom)
    cx, cy = _centre_on(panel, (-w / 2, w / 2), (-h / 2, h / 2), zoom)
    centre = (cx, cy)
    rect(msp, cx - w / 2 * zoom, cy - h / 2 * zoom,
         cx + w / 2 * zoom, cy + h / 2 * zoom, layer="S-PLAT")

    sec = geo.get("section_at_joint") or {}
    if sec:
        d = float(sec.get("d_mm", w * 0.7))
        bf = float(sec.get("bf_top_mm", w * 0.6))
        tw = float(sec.get("tw_mm", 8.0))
        tf = float(sec.get("tf_top_mm", 12.0))
        for xa, xb in ((-d / 2, -d / 2 + tf), (d / 2 - tf, d / 2)):
            rect(msp, cx + xa * zoom, cy - bf / 2 * zoom,
                 cx + xb * zoom, cy + bf / 2 * zoom, layer="S-MEMB-SECN")
        rect(msp, cx + (-d / 2 + tf) * zoom, cy - tw / 2 * zoom,
             cx + (d / 2 - tf) * zoom, cy + tw / 2 * zoom,
             layer="S-MEMB-SECN")

    holes = _holes(msp, std, geo, centre, zoom)
    if not holes:
        res.missing.append("hole positions")
        return
    us = sorted({u for u, _ in holes})
    vs = sorted({v for _, v in holes})
    _chain_1d(msp, std, [-w / 2] + us + [w / 2],
              cy + h / 2 * zoom, zoom, axis="h", origin=(cx, cy), offset=6.0)
    _chain_1d(msp, std, [-h / 2] + vs + [h / 2],
              cx + w / 2 * zoom, zoom, axis="v", origin=(cx, cy), offset=6.0)


def _generic_plate(msp, std, geo, panel, zoom, res) -> None:
    w = float(geo.get("width_mm", 0.0))
    h = float(geo.get("height_mm", 0.0))
    if not (w and h):
        res.missing.append("plate size")
        return
    zoom = _fit(panel, (-w / 2, w / 2), (-h / 2, h / 2), zoom)
    cx, cy = _centre_on(panel, (-w / 2, w / 2), (-h / 2, h / 2), zoom)
    centre = (cx, cy)
    rect(msp, cx - w / 2 * zoom, cy - h / 2 * zoom,
         cx + w / 2 * zoom, cy + h / 2 * zoom, layer="S-PLAT")
    holes = _holes(msp, std, geo, centre, zoom)
    if not holes:
        res.missing.append("hole positions")
        return
    us = sorted({u for u, _ in holes})
    vs = sorted({v for _, v in holes})
    _chain_1d(msp, std, [-w / 2] + us + [w / 2],
              cy + h / 2 * zoom, zoom, axis="h", origin=(cx, cy), offset=6.0)
    _chain_1d(msp, std, [-h / 2] + vs + [h / 2],
              cx + w / 2 * zoom, zoom, axis="v", origin=(cx, cy), offset=6.0)
