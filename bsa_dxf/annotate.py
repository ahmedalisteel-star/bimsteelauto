"""
bsa_dxf.annotate
================

Dimensions, leaders, member marks and levels.

Dimensions are real DIMENSION entities rendered against the sheet's own
DIMSTYLE, not lines with a number typed beside them.  That is the single
biggest difference between a drawing and a plot of a model: a real dimension
carries its measurement, so it updates when the geometry moves, it can be
restyled by the receiving office, and - the part that matters on an approval
drawing - it cannot silently disagree with the geometry it sits under.

Leaders are drawn rather than emitted as LEADER entities.  A LEADER's
arrowhead, text attachment and landing are governed by a dozen DIMSTYLE
variables that different readers honour differently; a polyline with a solid
head and a placed TEXT looks the same in every reader and never lands in the
wrong place.  The trade is deliberate.
"""

from __future__ import annotations

import math

from .blocks import LEVEL_TAG, insert
from .standards import TEXT_ISO_BOLD, Standard
from .util import text

__all__ = [
    "dim_chain",
    "dim_single",
    "level",
    "leader",
    "member_mark",
    "view_title",
]


def dim_single(msp, std: Standard, p1, p2, base, *, angle: float = 0.0,
               override: dict | None = None):
    """One linear dimension.  ``base`` is a point on the dimension line."""
    ov = {"dimclrd": 256, "dimclre": 256}
    if override:
        ov.update(override)
    dim = msp.add_linear_dim(
        base=base, p1=p1, p2=p2, angle=angle,
        dimstyle=std.dimstyle, override=ov,
        dxfattribs={"layer": "S-ANNO-DIMS"},
    )
    dim.render()
    return dim


def dim_chain(msp, std: Standard, points, base, *, axis: str = "h",
              overall: bool = True, overall_gap: float = 10.0,
              overall_text: str | None = None):
    """A running chain of dimensions between neighbouring set-out points.

    ``points`` are the set-out ordinates (x values for ``axis="h"``, z values
    for ``axis="v"``).  ``base`` is the offset of the dimension line: a z for
    a horizontal chain, an x for a vertical one.  With ``overall`` an extra
    dimension is placed one gap further out, which is what a grid line always
    carries.
    """
    pts = sorted(set(round(float(p), 3) for p in points))
    if len(pts) < 2:
        return
    angle = 0.0 if axis == "h" else 90.0

    for a, b in zip(pts, pts[1:]):
        if axis == "h":
            p1, p2, bs = (a, base), (b, base), (0.0, base)
        else:
            p1, p2, bs = (base, a), (base, b), (base, 0.0)
        dim_single(msp, std, p1, p2, bs, angle=angle)

    if overall and len(pts) > 2:
        gap = std.paper(overall_gap)
        off = base - gap if axis == "h" else base - gap
        if axis == "h":
            p1, p2, bs = (pts[0], off), (pts[-1], off), (0.0, off)
        else:
            p1, p2, bs = (off, pts[0]), (off, pts[-1]), (off, 0.0)
        ov = {}
        if overall_text:
            ov["text"] = overall_text
        dim_single(msp, std, p1, p2, bs, angle=angle,
                   override=({"dimtxt": std.text("dim") * 1.15}))


def level(msp, std: Standard, point, value_mm: float, *, label: str | None = None):
    """A level tag: triangle on the point, value in metres above it."""
    tag = label or f"+{value_mm / 1000.0:.3f}"
    return insert(msp, LEVEL_TAG, point, std, text=tag)


def leader(msp, std: Standard, anchor, landing, label, *,
           layer: str = "S-ANNO-MARK", align: str | None = None,
           style: str = TEXT_ISO_BOLD, height_role: str = "mark"):
    """An arrowed leader from ``anchor`` to ``landing``, text at the landing.

    A short horizontal shoulder is added at the landing end, the way a
    detailer draws one, and the text sits on the far side of the shoulder.
    """
    ax, ay = anchor
    lx, ly = landing
    to_right = lx >= ax
    shoulder = std.paper(6.0) * (1 if to_right else -1)
    sx = lx + shoulder

    msp.add_lwpolyline([(ax, ay), (lx, ly), (sx, ly)],
                       dxfattribs={"layer": layer})

    # Solid arrowhead at the anchor, pointing back along the first leg.
    dx, dy = lx - ax, ly - ay
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    a = std.paper(2.5)
    px, py = -uy, ux
    msp.add_solid(
        [(ax, ay),
         (ax + ux * a * 2.5 + px * a * 0.5, ay + uy * a * 2.5 + py * a * 0.5),
         (ax + ux * a * 2.5 - px * a * 0.5, ay + uy * a * 2.5 - py * a * 0.5),
         (ax + ux * a * 2.5 - px * a * 0.5, ay + uy * a * 2.5 - py * a * 0.5)],
        dxfattribs={"layer": layer},
    )

    al = align or ("bl" if to_right else "br")
    return text(msp, label, (sx + shoulder * 0.15, ly + std.paper(0.8)),
                std.text(height_role), layer=layer, align=al, style=style)


def member_mark(msp, std: Standard, member, *, offset: float = 6.0,
                rotate: bool = True, side: int = 1, label: str | None = None):
    """The section callout written along a member's axis.

    On a column this comes out reading bottom-to-top, on a rafter it follows
    the roof slope - which is what the reference sheets do and what makes a
    frame elevation readable without turning the paper.
    """
    ang = member.angle
    # Keep text the right way up: CAD text is unreadable past +/-90 degrees.
    if ang > 90.0:
        ang -= 180.0
    elif ang < -90.0:
        ang += 180.0

    mx, my = member.mid()
    rad = math.radians(member.angle)
    nx, ny = -math.sin(rad) * side, math.cos(rad) * side
    d = std.paper(offset)
    pos = (mx + nx * d, my + ny * d)

    return text(
        msp, label or member.section.describe(), pos, std.text("mark"),
        layer="S-ANNO-MARK", align="bc" if side > 0 else "tc",
        rotation=ang if rotate else 0.0, style=TEXT_ISO_BOLD,
    )


def view_title(msp, std: Standard, point, title: str, scale: str,
               *, underline: bool = True):
    """A view title with its scale, underlined, centred on ``point``."""
    h = std.text("title")
    t = text(msp, title.upper(), point, h, layer="S-ANNO-TEXT",
             align="bc", style=TEXT_ISO_BOLD)
    if underline:
        half = len(title) * h * 0.42
        y = point[1] - std.paper(2.0)
        msp.add_line((point[0] - half, y), (point[0] + half, y),
                     dxfattribs={"layer": "S-ANNO-TEXT"})
    if scale:
        text(msp, f"SCALE {scale}", (point[0], point[1] - std.paper(9.0)),
             std.text("note"), layer="S-ANNO-TEXT", align="tc")
    return t
