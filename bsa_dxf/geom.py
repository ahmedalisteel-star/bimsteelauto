"""
bsa_dxf.geom
============

Frame set-out and member outlines, derived from ``design_result.json``.

This module deliberately does NOT import the engine.  It needs four numbers
from a section mark - web depth at each end, flange width, web thickness,
flange thickness - and parsing them out of the mark is a dozen lines, while
importing ``bimsteelauto.sections`` would drag the whole design package into
a drawing writer that otherwise only needs ezdxf.  Keeping it out means a
``design_result.json`` can be drawn on a machine that has no engine
installed, which is exactly the machine a drawing office runs.

Set-out convention
------------------
Elevation, looking along the building.  ``x`` across the span, ``z`` up,
origin at the base of the leftmost column, on the OUTER face of its web.

    * a column's datum face is its OUTER flange face, plumb, and the section
      grows inward across the span
    * a rafter's datum face is its TOP flange face, following the roof line,
      and the section grows downward

That is how a PEB frame is set out on site and how the reference drawings
dimension it: the grid is the outside of the steel, not a member centreline.
The knee then falls out correctly - the column's inner face and the rafter's
underside meet at the haunch without either being trimmed by hand.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

__all__ = [
    "FrameError",
    "Section",
    "Member",
    "Frame",
    "parse_section",
    "tapered_outline",
    "frame_from_result",
]

# BU-450/850x250x8/16   tapered built-up
# BU-850x250x7/16       prismatic built-up
_BU = re.compile(
    r"^BU-(?P<d1>[\d.]+)(?:/(?P<d2>[\d.]+))?"
    r"x(?P<bf>[\d.]+)x(?P<tw>[\d.]+)/(?P<tf>[\d.]+)$",
    re.I,
)
# C200x60x20x2.5 / Z200x65x20x2.0
_CF = re.compile(
    r"^(?P<shape>[CZ])(?P<d>[\d.]+)x(?P<bf>[\d.]+)x(?P<lip>[\d.]+)x(?P<t>[\d.]+)$",
    re.I,
)
# M20 rod
_ROD = re.compile(r"^M(?P<dia>[\d.]+)\s*rod$", re.I)


@dataclass(frozen=True)
class Section:
    """Just enough of a section to draw it in elevation."""

    mark: str
    d_start: float
    d_end: float
    bf: float
    tw: float
    tf: float
    kind: str = "I"          # I | C | Z | rod

    @property
    def tapered(self) -> bool:
        return abs(self.d_end - self.d_start) > 1e-6

    def depth_at(self, t: float) -> float:
        """Depth at fraction ``t`` (0 at the start, 1 at the end)."""
        return self.d_start + (self.d_end - self.d_start) * t

    def describe(self) -> str:
        """The mark the way a PEB drawing writes it.

        ``W:450-850x8 - 2F:250x16`` - web depth range and thickness, then the
        two flanges.  This is the convention the reference set uses and it is
        not the same as the engine's internal mark.
        """
        if self.kind != "I":
            return self.mark
        web = (f"{_n(self.d_start)}-{_n(self.d_end)}"
               if self.tapered else _n(self.d_start))
        return f"W:{web}X{_n(self.tw)}-2F:{_n(self.bf)}X{_n(self.tf)}"


def _n(v: float) -> str:
    return f"{v:g}"


def parse_section(mark: str) -> Section:
    """A :class:`Section` from an engine section id.

    Unrecognised marks come back as a nominal 200 deep I so a sheet still
    draws; the mark itself is preserved and printed, so nothing is silently
    misrepresented as something it is not.
    """
    mark = (mark or "").strip()

    m = _BU.match(mark)
    if m:
        d1 = float(m["d1"])
        d2 = float(m["d2"]) if m["d2"] else d1
        return Section(mark, d1, d2, float(m["bf"]),
                       float(m["tw"]), float(m["tf"]), "I")

    m = _CF.match(mark)
    if m:
        d = float(m["d"])
        t = float(m["t"])
        return Section(mark, d, d, float(m["bf"]), t, t, m["shape"].upper())

    m = _ROD.match(mark)
    if m:
        dia = float(m["dia"])
        return Section(mark, dia, dia, dia, dia, dia, "rod")

    return Section(mark or "UNKNOWN", 200.0, 200.0, 100.0, 6.0, 10.0, "I")


@dataclass
class Member:
    """One drawable member on the elevation."""

    mark: str
    member_type: str
    section: Section
    start: tuple[float, float]
    end: tuple[float, float]
    #: Which side of the axis the section occupies.  ``+1`` is the LEFT
    #: normal of ``start -> end`` and ``-1`` the right, matching
    #: :func:`tapered_outline`.  For a column drawn bottom-to-top, ``+1`` is
    #: therefore -x and ``-1`` is +x - the source of a real bug once, so the
    #: sign is spelled out here rather than inferred at each call site.
    grow: int = 1

    @property
    def length(self) -> float:
        return math.hypot(self.end[0] - self.start[0],
                          self.end[1] - self.start[1])

    @property
    def angle(self) -> float:
        """Axis direction, degrees."""
        return math.degrees(math.atan2(self.end[1] - self.start[1],
                                       self.end[0] - self.start[0]))

    def mid(self) -> tuple[float, float]:
        return ((self.start[0] + self.end[0]) / 2,
                (self.start[1] + self.end[1]) / 2)

    def outline(self) -> list[list[tuple[float, float]]]:
        return tapered_outline(
            self.start, self.end,
            self.section.d_start, self.section.d_end,
            self.section.tf, self.grow,
        )


def tapered_outline(p0, p1, d0: float, d1: float, tf: float,
                    grow: int = 1) -> list[list[tuple[float, float]]]:
    """Elevation outline of a tapered I member.

    Returns polylines: the silhouette first, then the two flange inner lines.
    The datum face lies ON the ``p0 -> p1`` axis; the section grows to
    ``grow`` side of it.
    """
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        return []
    ux, uy = dx / length, dy / length
    nx, ny = -uy * grow, ux * grow          # unit normal on the grow side

    def off(p, dist):
        return (p[0] + nx * dist, p[1] + ny * dist)

    silhouette = [p0, p1, off(p1, d1), off(p0, d0)]
    near_flange = [off(p0, tf), off(p1, tf)]
    far_flange = [off(p0, d0 - tf), off(p1, d1 - tf)]
    return [silhouette, near_flange, far_flange]


# ---------------------------------------------------------------------------


@dataclass
class Frame:
    """The set-out of one cross-frame."""

    spans: list[float]
    eave_height: float
    slope: float
    members: list[Member] = field(default_factory=list)
    columns_x: list[float] = field(default_factory=list)
    ridges: list[tuple[float, float]] = field(default_factory=list)

    @property
    def width(self) -> float:
        return sum(self.spans)

    @property
    def ridge_height(self) -> float:
        return self.eave_height + (self.spans[0] / 2.0) * self.slope

    def roof_z(self, x: float) -> float:
        """Height of the roof line at ``x``, following each gable."""
        x0 = 0.0
        for span in self.spans:
            if x <= x0 + span or math.isclose(x, x0 + span):
                half = span / 2.0
                local = x - x0
                rise = (local if local <= half else span - local) * self.slope
                return self.eave_height + rise
            x0 += span
        return self.eave_height

    def bbox(self) -> tuple[float, float, float, float]:
        xs, zs = [0.0, self.width], [0.0, self.ridge_height]
        for m in self.members:
            for p in m.outline()[0] if m.outline() else []:
                xs.append(p[0])
                zs.append(p[1])
        return min(xs), min(zs), max(xs), max(zs)


class FrameError(ValueError):
    """The design result does not describe a frame that can be drawn."""


def frame_from_result(result: dict) -> Frame:
    """Build the frame set-out from a design result.

    Columns are taken from the member list by ``member_type``; where the
    engine reports one column mark per side, it is used on both.  Where it
    reports fewer rafters than spans, the first rafter section is repeated -
    a GA sheet showing the right silhouette with a repeated section is more
    useful than no sheet, and the mark printed on each member is always the
    mark the engine actually gave for it.
    """
    geo = result.get("spec", {}).get("geometry", {}) or {}

    # A missing span list is refused rather than defaulted.  A drawing that
    # invented a 30 m building because the result had none would be the most
    # convincing wrong drawing this package could produce.
    raw_spans = geo.get("span_widths") or []
    spans = [float(s) for s in raw_spans if float(s) > 0]
    if not spans:
        raise FrameError(
            "spec.geometry.span_widths is missing or empty - "
            "there is no frame to draw"
        )
    eave = float(geo.get("eave_height") or 0.0)
    if eave <= 0:
        raise FrameError("spec.geometry.eave_height is missing or not positive")
    slope = float(geo.get("roof_slope") or 0.0)

    members = result.get("members", []) or []
    cols = [m for m in members if m.get("member_type") == "column"]
    rafs = [m for m in members if m.get("member_type") == "rafter"]

    frame = Frame(spans=spans, eave_height=eave, slope=slope)

    # ---- columns --------------------------------------------------------
    n_cols = len(spans) + 1
    xs = [0.0]
    for s in spans:
        xs.append(xs[-1] + s)
    frame.columns_x = xs

    for i, x in enumerate(xs):
        src = cols[min(i, len(cols) - 1)] if cols else None
        sec = parse_section(src["section_id"] if src else "BU-450/850x250x8/16")
        mark = src["mark"] if src else f"C{i + 1}"
        # Every column's OUTER face sits on its grid and the section grows
        # INTO the building; interior columns straddle their own grid.
        # Columns run bottom-to-top, so the inward side is the right normal
        # (grow = -1) on the left-hand column and the left normal on the
        # right-hand one.
        if i == 0:
            grow, datum = -1, x
        elif i == n_cols - 1:
            grow, datum = 1, x
        else:
            grow, datum = 1, x + sec.d_end / 2.0
        top = frame.roof_z(x)
        members_out = Member(
            mark=mark, member_type="column", section=sec,
            start=(datum, 0.0), end=(datum, top), grow=grow,
        )
        frame.members.append(members_out)

    # ---- rafters --------------------------------------------------------
    x0 = 0.0
    for si, span in enumerate(spans):
        half = span / 2.0
        ridge_x = x0 + half
        ridge_z = eave + half * slope
        frame.ridges.append((ridge_x, ridge_z))

        left = rafs[min(si * 2, len(rafs) - 1)] if rafs else None
        right = rafs[min(si * 2 + 1, len(rafs) - 1)] if rafs else None
        for src, (pa, pb) in (
            (left, ((x0, eave), (ridge_x, ridge_z))),
            (right, ((ridge_x, ridge_z), (x0 + span, eave))),
        ):
            sec = parse_section(
                src["section_id"] if src else "BU-850/450x250x7/16"
            )
            # A rafter's mark reads eave-end first; the right-hand rafter of
            # a gable runs ridge-to-eave, so its section is reversed to keep
            # the deep end at the knee where the moment is.
            if pa[1] > pb[1]:
                sec = Section(sec.mark, sec.d_end, sec.d_start, sec.bf,
                              sec.tw, sec.tf, sec.kind)
            frame.members.append(Member(
                mark=src["mark"] if src else f"R{si + 1}",
                member_type="rafter", section=sec,
                start=pa, end=pb, grow=-1,     # grows downward
            ))
        x0 += span

    return frame
