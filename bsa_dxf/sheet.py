"""
bsa_dxf.sheet
=============

The sheet itself: trimmed edge, drawing frame, zone references, title block,
revision table, approval box, notes block and the issue stamp.

The title block is modelled on the approval sheets a PEB fabricator actually
sends out, because that is the document this package is trying to be able to
produce.  Its rows are therefore not generic CAD furniture - BUILDING DESC.,
BUILDING QUANTITY, SITE LOCATION and STEEL DESIGNER are there because a PEB
approval drawing carries them and a consultant's border does not.

The one rule this module enforces on its own
--------------------------------------------
``TitleBlock.stamp`` is not a caller's free choice.  It is derived from the
design result - errors, unverified items, coverage gaps - by
:func:`stamp_for`.  A drawing that came out of a failing design and does not
say so is the most dangerous thing this package can emit, so the stamp is
computed, not passed in.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field

from .standards import TEXT_ISO, TEXT_ISO_BOLD, Standard
from .util import rect, text

__all__ = ["TitleBlock", "stamp_for", "draw_sheet", "frame_area",
           "drawing_area", "notes_top"]

#: Title block width on paper, mm.
_TB_W = 180.0

#: Row heights on paper, mm, bottom-up within the title block.
_ROW_TITLE = 16.0
_ROW = 10.0
_ROW_REV = 6.5
_N_REV_ROWS = 4


@dataclass
class TitleBlock:
    """Everything that prints in the title block of one sheet."""

    drawing_title: str = ""
    drawing_no: str = ""
    job_no: str = ""
    project: str = ""
    customer: str = ""
    site: str = ""
    building_desc: str = ""
    building_qty: str = ""
    steel_designer: str = ""
    consultant: str = ""
    cad_file: str = ""
    scale: str = ""
    date: str = ""
    revisions: list[tuple[str, str, str, str, str, str]] = field(
        default_factory=list
    )
    notes: list[str] = field(default_factory=list)
    stamp: str = ""

    def __post_init__(self) -> None:
        if not self.date:
            self.date = datetime.date.today().strftime("%d-%m-%y")
        if not self.revisions:
            self.revisions = [("0", self.date, "ISSUED FOR APPROVAL", "", "", "")]


def stamp_for(result: dict) -> str:
    """The issue stamp, taken from the design rather than from a caller.

    Anything that makes the design unfit to build gets a stamp across the
    sheet.  A clean design gets none, which is the only way an absent stamp
    can mean anything.
    """
    if result.get("errors"):
        return "NOT FOR ISSUE - DESIGN HAS ERRORS"
    if result.get("unverified"):
        return "NOT FOR ISSUE - UNVERIFIED ITEMS"
    over = [
        m for m in result.get("members", [])
        for c in m.get("checks", [])
        if (c.get("utilisation") or 0) > 1.0
    ]
    if over:
        return "NOT FOR ISSUE - MEMBER OVERSTRESSED"
    return "PRELIMINARY - NOT FOR CONSTRUCTION"


def frame_area(std: Standard) -> tuple[float, float, float, float]:
    """``(x0, y0, x1, y1)`` of the drawing frame, model units.

    The frame is inside the trimmed sheet edge by the binding margin on the
    left and the plain margin on the other three sides.
    """
    w, h = std.sheet_size
    return std.binding, std.margin, w - std.margin, h - std.margin


def drawing_area(std: Standard) -> tuple[float, float, float, float]:
    """The frame less the title block - where a view may actually go."""
    x0, y0, x1, y1 = frame_area(std)
    return x0, y0 + _tb_height(std), x1 - std.paper(_TB_W), y1


#: Height of the approval box and the consultant strip, paper mm.
#: The approval box has to hold four lines of preamble, three tick
#: options and two signature rows; at 36 mm they spilled through the
#: revision table below it.
_APPROVAL_H = 46.0
_CONSULT_H = _ROW


def _tb_height(std: Standard) -> float:
    """Full height of the title block, consultant strip included.

    Anything placed above the block measures from this, so it has to count
    every band ``_title_block`` actually draws - the consultant strip used to
    be left out, and the notes landed inside the approval box.
    """
    rows = _ROW_TITLE + _ROW * 6
    revs = _ROW_REV * (_N_REV_ROWS + 1)
    return std.paper(rows + revs + _APPROVAL_H + _CONSULT_H)


# ---------------------------------------------------------------------------


def draw_sheet(msp, std: Standard, tb: TitleBlock) -> None:
    """Draw the border, zone marks, title block and stamp."""
    _border(msp, std)
    _zones(msp, std)
    _title_block(msp, std, tb)
    if tb.notes:
        _notes(msp, std, tb)
    if tb.stamp:
        _stamp(msp, std, tb.stamp)


def _border(msp, std: Standard) -> None:
    w, h = std.sheet_size
    # Trimmed sheet edge, thin.
    rect(msp, 0, 0, w, h, layer="S-SHET-TTLB")
    # Drawing frame, heavy.
    x0, y0, x1, y1 = frame_area(std)
    rect(msp, x0, y0, x1, y1, layer="S-SHET")


def _zones(msp, std: Standard) -> None:
    """ISO 7200 zone references around the frame: 1..n across, A..n up."""
    x0, y0, x1, y1 = frame_area(std)
    step = std.paper(105.0)
    th = std.text("note")
    n_x = max(1, int(round((x1 - x0) / step)))
    n_y = max(1, int(round((y1 - y0) / step)))
    dx = (x1 - x0) / n_x
    dy = (y1 - y0) / n_y
    off = std.paper(5.0)

    for i in range(n_x):
        cx = x0 + dx * (i + 0.5)
        for yy, al in ((y0 - off, "tc"), (y1 + off, "bc")):
            text(msp, i + 1, (cx, yy), th, layer="S-SHET-TTLB", align=al)
        if i:
            xx = x0 + dx * i
            msp.add_line((xx, y0), (xx, y0 - off * 1.6),
                         dxfattribs={"layer": "S-SHET-TTLB"})
            msp.add_line((xx, y1), (xx, y1 + off * 1.6),
                         dxfattribs={"layer": "S-SHET-TTLB"})

    for j in range(n_y):
        cy = y0 + dy * (j + 0.5)
        tag = chr(ord("A") + j)
        for xx, al in ((x0 - off, "mr"), (x1 + off, "ml")):
            text(msp, tag, (xx, cy), th, layer="S-SHET-TTLB", align=al)
        if j:
            yy = y0 + dy * j
            msp.add_line((x0, yy), (x0 - off * 1.6, yy),
                         dxfattribs={"layer": "S-SHET-TTLB"})
            msp.add_line((x1, yy), (x1 + off * 1.6, yy),
                         dxfattribs={"layer": "S-SHET-TTLB"})


def _cell(msp, std, x0, y0, x1, y1, label, value, *,
          vrole="note", lrole=None, align="left"):
    """One labelled box: small caps label top-left, value below it."""
    rect(msp, x0, y0, x1, y1, layer="S-SHET-TTLB")
    pad = std.paper(1.6)
    lh = std.paper(1.8)
    if label:
        text(msp, label, (x0 + pad, y1 - pad - lh), lh,
             layer="S-SHET-TTLB", align="bl")
    if value:
        vh = std.text(vrole)
        if align == "center":
            text(msp, value, ((x0 + x1) / 2, y0 + pad), vh,
                 layer="S-ANNO-TEXT", align="bc", style=TEXT_ISO_BOLD)
        else:
            text(msp, value, (x0 + pad, y0 + pad), vh,
                 layer="S-ANNO-TEXT", align="bl", style=TEXT_ISO_BOLD)


def _title_block(msp, std: Standard, tb: TitleBlock) -> None:
    x0, y0, x1, y1 = frame_area(std)
    left = x1 - std.paper(_TB_W)
    y = y0

    # --- row 1: drawing title | drawing number ---------------------------
    h = std.paper(_ROW_TITLE)
    split = left + std.paper(_TB_W - 52.0)
    _cell(msp, std, left, y, split, y + h, "DRAWING TITLE",
          tb.drawing_title, vrole="head")
    _cell(msp, std, split, y, x1, y + h, "DRAWING NO.",
          tb.drawing_no, vrole="title", align="center")
    y += h

    # --- rows 2..7: identity ---------------------------------------------
    h = std.paper(_ROW)
    s2 = left + std.paper(_TB_W - 66.0)
    for label, value, second in (
        ("JOB NO.", tb.job_no, ("CAD FILE", tb.cad_file)),
        ("SITE LOCATION", tb.site, ("SCALE", tb.scale)),
        ("CUSTOMER", tb.customer, None),
        ("PROJECT", tb.project, None),
        ("BUILDING DESC.", tb.building_desc, ("QUANTITY", tb.building_qty)),
        ("STEEL DESIGNER", tb.steel_designer, ("DATE", tb.date)),
    ):
        if second:
            _cell(msp, std, left, y, s2, y + h, label, value)
            _cell(msp, std, s2, y, x1, y + h, second[0], second[1])
        else:
            _cell(msp, std, left, y, x1, y + h, label, value)
        y += h

    # --- revision table ---------------------------------------------------
    h = std.paper(_ROW_REV)
    cols = [0.0, 10.0, 32.0, 130.0, 146.0, 163.0, _TB_W]
    xs = [left + std.paper(c) for c in cols]
    heads = ("REV", "DATE", "DESCRIPTION", "DRAWN", "CHK'D", "APP'D")
    rows = list(tb.revisions)[:_N_REV_ROWS]
    while len(rows) < _N_REV_ROWS:
        rows.insert(0, ("", "", "", "", "", ""))

    for row in rows:                       # oldest at the bottom
        for i, val in enumerate(row):
            rect(msp, xs[i], y, xs[i + 1], y + h, layer="S-SHET-TTLB")
            if val:
                text(msp, val, (xs[i] + std.paper(1.2), y + std.paper(1.4)),
                     std.paper(2.2), layer="S-ANNO-TEXT", align="bl")
        y += h

    for i, head in enumerate(heads):
        rect(msp, xs[i], y, xs[i + 1], y + h, layer="S-SHET-TTLB")
        text(msp, head, ((xs[i] + xs[i + 1]) / 2, y + std.paper(1.4)),
             std.paper(2.2), layer="S-SHET-TTLB", align="bc")
    y += h

    # --- approval box -----------------------------------------------------
    h = std.paper(_APPROVAL_H)
    rect(msp, left, y, x1, y + h, layer="S-SHET-TTLB")
    pad = std.paper(2.5)
    th = std.paper(2.2)
    ty = y + h - pad - th
    for line in (
        'ISSUED FOR "APPROVAL" BY CUSTOMER.',
        "NOT FOR CONSTRUCTION AND NOT TO SCALE.",
        "",
        "CUSTOMER IS REQUIRED TO TICK ONE BOX ONLY:",
    ):
        if line:
            text(msp, line, (left + pad, ty), th,
                 layer="S-ANNO-TEXT", align="bl")
        ty -= th * 1.8

    box = std.paper(3.0)
    for opt in ("APPROVED AS IS", "APPROVED AS NOTED", "RESUBMIT AS NOTED"):
        rect(msp, left + pad, ty, left + pad + box, ty + box,
             layer="S-SHET-TTLB")
        text(msp, opt, (left + pad + box * 1.8, ty), th,
             layer="S-ANNO-TEXT", align="bl")
        ty -= th * 2.0

    ty -= th * 0.4
    text(msp, "CUSTOMER SIGNATURE:", (left + pad, ty), th,
         layer="S-ANNO-TEXT", align="bl")
    msp.add_line((left + std.paper(52.0), ty),
                 (x1 - pad, ty), dxfattribs={"layer": "S-SHET-TTLB"})
    ty -= th * 2.0
    text(msp, "DATE:", (left + pad, ty), th, layer="S-ANNO-TEXT", align="bl")
    msp.add_line((left + std.paper(52.0), ty),
                 (x1 - pad, ty), dxfattribs={"layer": "S-SHET-TTLB"})

    # --- consultant strip above the block ---------------------------------
    if tb.consultant:
        hh = std.paper(_ROW)
        _cell(msp, std, left, y + h, x1, y + h + hh, "CONSULTANT",
              tb.consultant)


def _notes(msp, std: Standard, tb: TitleBlock) -> None:
    """The general-notes block, sitting on top of the title block.

    Drawn from its BOTTOM edge upward.  Drawing it downward from a fixed top
    means the block grows into whatever is beneath it as soon as a job adds a
    note, which is how the notes ended up printed across the approval box.
    """
    x0, y0, x1, y1 = frame_area(std)
    left = x1 - std.paper(_TB_W)
    th = std.text("note")
    line = th * 1.7

    bottom = y0 + _tb_height(std) + std.paper(6.0)
    y = bottom
    for i, note in reversed(list(enumerate(tb.notes, 1))):
        text(msp, f"{i}.", (left, y), th, layer="S-ANNO-TEXT", align="bl")
        text(msp, note, (left + std.paper(5.0), y), th,
             layer="S-ANNO-TEXT", align="bl")
        y += line
    rule_y = y + std.paper(1.5)
    msp.add_line((left, rule_y), (x1, rule_y),
                 dxfattribs={"layer": "S-SHET-TTLB"})
    text(msp, "GENERAL NOTES", (left, rule_y + std.paper(2.0)),
         std.text("head"), layer="S-ANNO-TEXT", align="bl",
         style=TEXT_ISO_BOLD)


def notes_top(std: Standard, tb: TitleBlock) -> float:
    """Model y of the top of the notes block - where a view must stop."""
    th = std.text("note")
    n = len(tb.notes)
    return (frame_area(std)[1] + _tb_height(std) + std.paper(6.0)
            + th * 1.7 * n + std.text("head") + std.paper(6.0))


def _stamp(msp, std: Standard, message: str) -> None:
    """A diagonal stamp across the drawing area."""
    x0, y0, x1, y1 = drawing_area(std)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    angle = math.degrees(math.atan2(y1 - y0, x1 - x0))
    text(msp, message, (cx, cy), std.paper(9.0),
         layer="S-ANNO-STMP", align="mc", rotation=angle,
         style=TEXT_ISO_BOLD, width=0.9)
