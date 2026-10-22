"""
bsa_dxf.util
============

Small shared helpers.  Two things live here because every other module wants
them and neither is worth a module of its own:

``text()``
    One call to create and place a TEXT entity.  ezdxf splits that into
    ``add_text`` plus ``set_placement`` with an enum, which reads badly when
    a sheet places two hundred labels.

``rect()``
    A closed rectangle from two corners, which is most of a title block.
"""

from __future__ import annotations

from ezdxf.enums import TextEntityAlignment as TA

from .standards import TEXT_ISO

__all__ = ["TA", "ALIGN", "text", "rect", "fmt_mm"]

#: String spelling -> ezdxf enum, so call sites stay readable.
ALIGN: dict[str, TA] = {
    "left": TA.LEFT,
    "center": TA.CENTER,
    "right": TA.RIGHT,
    "bl": TA.BOTTOM_LEFT,
    "bc": TA.BOTTOM_CENTER,
    "br": TA.BOTTOM_RIGHT,
    "ml": TA.MIDDLE_LEFT,
    "mc": TA.MIDDLE_CENTER,
    "mr": TA.MIDDLE_RIGHT,
    "tl": TA.TOP_LEFT,
    "tc": TA.TOP_CENTER,
    "tr": TA.TOP_RIGHT,
}


def text(layout, s, point, height, *, layer="S-ANNO-TEXT", align="bl",
         rotation=0.0, style=TEXT_ISO, width=1.0):
    """Create a placed TEXT entity.  ``align`` is a key of :data:`ALIGN`."""
    ent = layout.add_text(
        str(s),
        dxfattribs={
            "layer": layer,
            "style": style,
            "height": height,
            "rotation": rotation,
            "width": width,
        },
    )
    ent.set_placement(point, align=ALIGN[align])
    return ent


def rect(layout, x0, y0, x1, y1, *, layer="S-SHET", close=True):
    """A closed rectangle from two opposite corners."""
    return layout.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=close,
        dxfattribs={"layer": layer},
    )


def fmt_mm(v: float) -> str:
    """A dimension value the way a drawing writes it: whole mm, no units."""
    return f"{round(v):d}"
