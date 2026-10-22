"""
bsa_dxf.blocks
==============

The reusable symbols: grid bubble, detail callout, level tag, section mark,
north point, downspout.

Each one is a real DXF BLOCK with an ATTDEF for its text, inserted as a
BLOCKREF.  That matters more than it looks: a grid bubble drawn as a circle
plus a loose TEXT is two entities a detailer has to move together and cannot
renumber, while a block with an attribute renumbers from the properties
palette and survives a copy.  It is the difference between a drawing that
was exported and a drawing that was drafted.

Blocks are defined in PAPER millimetres scaled once by the standard, so a
bubble is the same size on an A1 at 1:50 as on an A3 at 1:100.
"""

from __future__ import annotations

import math

from .standards import TEXT_ISO, Standard
from .util import ALIGN, text as _text

__all__ = [
    "GRID_BUBBLE",
    "DETAIL_BUBBLE",
    "LEVEL_TAG",
    "NORTH_POINT",
    "DOWNSPOUT",
    "define_all",
    "insert",
]

GRID_BUBBLE = "BSA_GRID_BUBBLE"
DETAIL_BUBBLE = "BSA_DETAIL_BUBBLE"
LEVEL_TAG = "BSA_LEVEL_TAG"
NORTH_POINT = "BSA_NORTH"
DOWNSPOUT = "BSA_DOWNSPOUT"

#: Bubble radius on paper, mm.  4.0 is the usual structural grid bubble.
_R_GRID = 4.0
_R_DETAIL = 5.0


def define_all(doc, std: Standard) -> None:
    """Define every block on ``doc``.  Idempotent."""
    _grid_bubble(doc, std)
    _detail_bubble(doc, std)
    _level_tag(doc, std)
    _north_point(doc, std)
    _downspout(doc, std)


def insert(msp, name: str, point, std: Standard, *, text: str = "",
           layer: str = "S-ANNO-SYMB", rotation: float = 0.0):
    """Insert a block and fill its attribute in one call.

    Returns the INSERT entity.  ``text`` is written into whichever ATTDEF the
    block carries; blocks without one ignore it.
    """
    ref = msp.add_blockref(
        name, point,
        dxfattribs={"layer": layer, "rotation": rotation},
    )
    if text:
        # add_auto_attribs places the value at the ATTDEF's own position and
        # copies its height and justification, so the text stays centred in
        # the bubble no matter how long the grid label is.
        ref.add_auto_attribs({"TAG": text})
    return ref


# ---------------------------------------------------------------------------
# individual definitions
# ---------------------------------------------------------------------------


def _grid_bubble(doc, std: Standard) -> None:
    if GRID_BUBBLE in doc.blocks:
        return
    r = std.paper(_R_GRID)
    blk = doc.blocks.new(name=GRID_BUBBLE)
    blk.add_circle((0, 0), r, dxfattribs={"layer": "S-GRID-IDEN"})
    att = blk.add_attdef(
        "TAG",
        dxfattribs={
            "layer": "S-GRID-IDEN",
            "style": TEXT_ISO,
            "height": std.text("grid"),
            "invisible": 0,
        },
    )
    att.set_placement((0, 0), align=ALIGN["mc"])


def _detail_bubble(doc, std: Standard) -> None:
    """A detail callout: circle with a leader tail pointing down-left."""
    if DETAIL_BUBBLE in doc.blocks:
        return
    r = std.paper(_R_DETAIL)
    blk = doc.blocks.new(name=DETAIL_BUBBLE)
    blk.add_circle((0, 0), r, dxfattribs={"layer": "S-ANNO-SYMB"})
    att = blk.add_attdef(
        "TAG",
        dxfattribs={
            "layer": "S-ANNO-SYMB",
            "style": TEXT_ISO,
            "height": std.text("note"),
        },
    )
    att.set_placement((0, 0), align=ALIGN["mc"])


def _level_tag(doc, std: Standard) -> None:
    """The surveyor's level triangle, apex at the insertion point.

    Apex down, horizontal bar across the top, text sitting on the bar - the
    convention every elevation uses for a finished level.
    """
    if LEVEL_TAG in doc.blocks:
        return
    h = std.paper(3.5)
    w = std.paper(4.0)
    blk = doc.blocks.new(name=LEVEL_TAG)
    blk.add_lwpolyline(
        [(0, 0), (-w / 2, h), (w / 2, h), (0, 0)],
        close=True,
        dxfattribs={"layer": "S-ANNO-SYMB"},
    )
    blk.add_line((-w, h), (std.paper(30.0), h),
                 dxfattribs={"layer": "S-ANNO-SYMB"})
    att = blk.add_attdef(
        "TAG",
        dxfattribs={
            "layer": "S-ANNO-SYMB",
            "style": TEXT_ISO,
            "height": std.text("note"),
        },
    )
    att.set_placement((std.paper(1.0), h + std.paper(1.0)), align=ALIGN["left"])


def _north_point(doc, std: Standard) -> None:
    if NORTH_POINT in doc.blocks:
        return
    r = std.paper(9.0)
    blk = doc.blocks.new(name=NORTH_POINT)
    blk.add_circle((0, 0), r, dxfattribs={"layer": "S-ANNO-SYMB"})
    # A filled half-arrow: solid left of the axis, open right.  Reads as a
    # north point at any size without needing a hatch.
    blk.add_solid(
        [(0, r * 0.92), (-r * 0.30, -r * 0.55), (0, -r * 0.22), (0, -r * 0.22)],
        dxfattribs={"layer": "S-ANNO-SYMB"},
    )
    blk.add_lwpolyline(
        [(0, r * 0.92), (r * 0.30, -r * 0.55), (0, -r * 0.22)],
        close=True,
        dxfattribs={"layer": "S-ANNO-SYMB"},
    )
    _text(blk, "N", (0, r + std.paper(2.0)), std.text("head"),
          layer="S-ANNO-SYMB", align="bc")


def _downspout(doc, std: Standard) -> None:
    """The crossed square that marks a rainwater outlet on a roof plan."""
    if DOWNSPOUT in doc.blocks:
        return
    s = std.paper(2.5)
    blk = doc.blocks.new(name=DOWNSPOUT)
    blk.add_lwpolyline(
        [(-s, -s), (s, -s), (s, s), (-s, s)],
        close=True,
        dxfattribs={"layer": "S-ANNO-SYMB"},
    )
    blk.add_line((-s, -s), (s, s), dxfattribs={"layer": "S-ANNO-SYMB"})
    blk.add_line((-s, s), (s, -s), dxfattribs={"layer": "S-ANNO-SYMB"})


# ---------------------------------------------------------------------------
# free-standing symbols that are cheaper drawn than blocked
# ---------------------------------------------------------------------------


def slope_arrow(msp, start, end, std: Standard, *, layer: str = "S-ANNO-SYMB",
                text: str = "SLOPE") -> None:
    """The hollow drainage arrow used on a roof sheeting plan."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        return
    ux, uy = dx / length, dy / length
    px, py = -uy, ux                      # unit normal
    w = std.paper(1.6)                    # half shaft width
    hw = std.paper(4.0)                   # half head width
    hl = std.paper(10.0)                  # head length
    hl = min(hl, length * 0.45)
    bx, by = end[0] - ux * hl, end[1] - uy * hl

    pts = [
        (start[0] + px * w, start[1] + py * w),
        (bx + px * w, by + py * w),
        (bx + px * hw, by + py * hw),
        (end[0], end[1]),
        (bx - px * hw, by - py * hw),
        (bx - px * w, by - py * w),
        (start[0] - px * w, start[1] - py * w),
    ]
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
    if text:
        mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
        _text(msp, text, (mx, my + std.paper(2.5)), std.text("note"),
              layer=layer, align="bc",
              rotation=math.degrees(math.atan2(dy, dx)))
