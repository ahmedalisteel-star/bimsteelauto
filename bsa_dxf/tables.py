"""
bsa_dxf.tables
==============

Schedule tables drawn as real geometry: the taper schedule that sits over a
PEB frame elevation, and the member schedule.

The taper schedule is the PEB-specific thing a generic detailing package does
not produce and a PEB fabricator cannot work without.  It reads left to right
along the rafter and gives, per fabrication segment, the plate sizes the shop
will actually cut::

    SPACING      | BAL | 6000 | 6000 | BAL
    OUTER FLANGE | 250X16 | 250X16 | ...
    WEB          | 850-650X7 | 650X7 | ...
    INNER FLANGE | 250X16 | ...
    PURLIN       | BAL+6X1500+300

Its columns are positioned to line up with the splice positions on the
elevation above it, so a reader can drop a vertical line from any table cell
onto the piece of steel it describes.  That alignment is the whole point of
the table; a free-floating version of it would be a worse version of the
member schedule.
"""

from __future__ import annotations

from .standards import TEXT_ISO_BOLD, Standard
from .util import rect, text

__all__ = ["grid_table", "taper_schedule", "member_schedule"]


def grid_table(msp, std: Standard, x_edges, y_top, rows, *,
               label_w: float = 30.0, row_h: float = 6.0,
               layer: str = "S-ANNO-TABL", heading: str | None = None):
    """A table whose column edges are given in MODEL x, growing downward.

    ``x_edges`` are the vertical rules, left to right.  ``rows`` is a list of
    ``(label, [cell, ...])``; a row whose cell list is shorter than the
    column count leaves the remaining cells blank, and a row with exactly one
    cell spans the whole table.

    Returns the y of the table's bottom edge.
    """
    if len(x_edges) < 2:
        return y_top

    lw = std.paper(label_w)
    rh = std.paper(row_h)
    th = std.paper(2.2)
    x_left = x_edges[0] - lw
    x_right = x_edges[-1]
    y = y_top

    if heading:
        text(msp, heading, (x_left, y + std.paper(2.0)), std.text("note"),
             layer=layer, align="bl", style=TEXT_ISO_BOLD)

    for label, cells in rows:
        y_bot = y - rh
        # label cell
        rect(msp, x_left, y_bot, x_edges[0], y, layer=layer)
        text(msp, label, (x_left + std.paper(1.5), y_bot + std.paper(1.6)),
             th, layer=layer, align="bl")

        if len(cells) == 1:
            rect(msp, x_edges[0], y_bot, x_right, y, layer=layer)
            text(msp, cells[0], ((x_edges[0] + x_right) / 2,
                                 y_bot + std.paper(1.6)),
                 th, layer=layer, align="bc")
        else:
            for i in range(len(x_edges) - 1):
                x0, x1 = x_edges[i], x_edges[i + 1]
                rect(msp, x0, y_bot, x1, y, layer=layer)
                if i < len(cells) and cells[i]:
                    text(msp, cells[i], ((x0 + x1) / 2, y_bot + std.paper(1.6)),
                         th, layer=layer, align="bc")
        y = y_bot
    return y


def _segments(section, splice_length: float, total: float):
    """Split a member into fabrication segments at ``splice_length``.

    The end segments take the balance, which is how a PEB shop nests plate:
    full-length middle pieces and two odd ends.  Returns
    ``[(t0, t1, label), ...]`` with ``t`` as a fraction of the member.
    """
    if total <= splice_length:
        return [(0.0, 1.0, "BAL")]
    n_full = int((total - 1e-6) // splice_length)
    balance = total - n_full * splice_length
    if balance < splice_length * 0.25 and n_full > 1:
        n_full -= 1
        balance += splice_length
    head = balance / 2.0
    out, run = [], 0.0
    for i in range(n_full + 2):
        if i == 0:
            seg, lab = head, "BAL"
        elif i == n_full + 1:
            seg, lab = total - run, "BAL"
        else:
            seg, lab = splice_length, f"{splice_length:g}"
        if seg <= 1e-6:
            continue
        out.append((run / total, (run + seg) / total, lab))
        run += seg
    return out


def taper_schedule(msp, std: Standard, frame, y_top: float, *,
                   splice_length: float = 6000.0,
                   purlin_spacing: float | None = None):
    """The rafter taper schedule, columns aligned to the elevation above.

    Draws one table per span, spanning that span's two rafters, with the
    column edges sitting at the true splice x-ordinates so they line up with
    the frame drawn above.
    """
    rafters = [m for m in frame.members if m.member_type == "rafter"]
    if not rafters:
        return y_top

    edges: list[float] = []
    spacing: list[str] = []
    outer: list[str] = []
    web: list[str] = []
    inner: list[str] = []

    for raf in rafters:
        sec = raf.section
        total = raf.length
        x_a, x_b = raf.start[0], raf.end[0]
        for t0, t1, lab in _segments(sec, splice_length, total):
            if not edges:
                edges.append(x_a + (x_b - x_a) * t0)
            edges.append(x_a + (x_b - x_a) * t1)
            d0, d1 = sec.depth_at(t0), sec.depth_at(t1)
            spacing.append(lab)
            outer.append(f"{sec.bf:g}X{sec.tf:g}")
            inner.append(f"{sec.bf:g}X{sec.tf:g}")
            web.append(
                f"{d0:.0f}-{d1:.0f}X{sec.tw:g}"
                if abs(d1 - d0) > 1.0 else f"{d0:.0f}X{sec.tw:g}"
            )

    rows = [
        ("SPACING", spacing),
        ("OUTER FLANGE", outer),
        ("WEB", web),
        ("INNER FLANGE", inner),
    ]
    if purlin_spacing:
        n = max(1, int(frame.spans[0] / 2 / purlin_spacing) - 1)
        rows.append(("PURLIN", [f"BAL+{n}X{purlin_spacing:g}+BAL"]))

    return grid_table(msp, std, edges, y_top, rows, label_w=34.0)


def member_schedule(msp, std: Standard, members, x_left: float, y_top: float,
                    *, widths=(20.0, 56.0, 26.0, 20.0, 20.0)):
    """A mark / section / length / no. / weight table.

    Not a take-off - a drawing's own index, so a reader can tie a mark on the
    elevation to the section it stands for without hunting across the sheet.
    """
    # No quantity column: the engine reports one entry per MARK and does not
    # count erected pieces, and a blank column on a drawing reads as an
    # omission rather than as "not known".
    heads = ("MARK", "SECTION", "TYPE", "LENGTH", "MASS kg")
    edges = [x_left]
    for w in widths:
        edges.append(edges[-1] + std.paper(w))

    rh = std.paper(6.0)
    th = std.paper(2.2)
    y = y_top

    text(msp, "MEMBER SCHEDULE", (x_left, y + std.paper(2.0)),
         std.text("note"), layer="S-ANNO-TABL", align="bl",
         style=TEXT_ISO_BOLD)

    for i, head in enumerate(heads):
        rect(msp, edges[i], y - rh, edges[i + 1], y, layer="S-ANNO-TABL")
        text(msp, head, ((edges[i] + edges[i + 1]) / 2, y - rh + std.paper(1.6)),
             th, layer="S-ANNO-TABL", align="bc", style=TEXT_ISO_BOLD)
    y -= rh

    for m in members:
        cells = (
            m.get("mark", ""),
            m.get("section_id", ""),
            (m.get("member_type", "") or "").replace("_", " ").upper(),
            f"{m.get('length', 0):.0f}",
            f"{m.get('weight', 0):.0f}",
        )
        for i, cell in enumerate(cells):
            rect(msp, edges[i], y - rh, edges[i + 1], y, layer="S-ANNO-TABL")
            al = "bc" if i in (3, 4) else "bl"
            px = (edges[i] + edges[i + 1]) / 2 if al == "bc" \
                else edges[i] + std.paper(1.5)
            text(msp, cell, (px, y - rh + std.paper(1.6)), th,
                 layer="S-ANNO-TABL", align=al)
        y -= rh
    return y
