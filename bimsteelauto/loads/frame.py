"""
bimsteelauto.loads.frame
========================

Area loads (kN/m^2)  ->  frame line loads (kN/m)  ->  the analysis model.

One frame line carries the load from half a bay either side of it.  That
tributary width is the only thing that turns a pressure into a line load,
and on a non-uniform bay layout it differs frame by frame - which is exactly
the v6 defect that must not come back, so it is read from
``PEBGeometry.tributary_widths()`` rather than recomputed here.

The surface discipline, enforced
--------------------------------
:class:`~bimsteelauto.loads.gravity.AreaLoad` knows whether it is defined per
square metre of sloping roof or of horizontal plan.  This module maps that
onto the analysis module's ``LoadDirection`` so the two can never drift:

    Surface.SLOPING    ->  LoadDirection.GLOBAL_Y
    Surface.PROJECTED  ->  LoadDirection.GLOBAL_Y_PROJECTED

Nothing else is allowed to choose that pairing, and a test pins it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from ..analysis import FrameAnalysis, FrameLayout, LoadDirection
from ..spec import LoadCase, LoadCombination, LoadSummary, MemberType, PEBGeometry
from .gravity import AreaLoad, LoadError, Surface

__all__ = [
    "VERIFIED",
    "FrameLoad",
    "AppliedLoads",
    "tributary_width",
    "line_load",
    "gravity_loads_for_frame",
    "apply_loads",
    "build_load_summary",
]

#: CLOSED 2026-09-01.  This module routes rather than calculates, so it is
#: pinned by end-to-end statics rather than against a second tool:
#:
#:     tributary_width        non-uniform bays stay non-uniform
#:                            (3.0 / 6.75 / 7.5 / 6.75 / 3.0 m by hand)
#:     surface_to_direction   SLOPING -> GLOBAL_Y and PROJECTED ->
#:                            GLOBAL_Y_PROJECTED, pinned so the pairing
#:                            cannot drift from the analysis module
#:     line_load_conversion   the whole chain solved: a sloping load totals
#:                            w x trib x SLOPED length, a projected one
#:                            totals w x trib x PLAN width, both to 1e-9,
#:                            confirming cos(theta) is applied exactly once
VERIFIED: dict[str, bool] = {
    "tributary_width": True,
    "surface_to_direction": True,
    "line_load_conversion": True,
}

#: The ONE mapping from a load's defining surface to the analysis direction.
_DIRECTION_FOR: dict[Surface, LoadDirection] = {
    Surface.SLOPING: LoadDirection.GLOBAL_Y,
    Surface.PROJECTED: LoadDirection.GLOBAL_Y_PROJECTED,
}


@dataclass(frozen=True)
class FrameLoad:
    """One line load, ready to hand to :class:`FrameAnalysis`."""

    case: str
    member_mark: str
    w: float                      # kN/m
    direction: LoadDirection
    origin: str = ""              # what it came from, for the sheet


def tributary_width(geometry: PEBGeometry, frame_index: int) -> float:
    """Plan tributary width of one frame line, in METRES.

    ``PEBGeometry`` works in mm; a line load is kN per metre.  The single
    conversion lives here so it cannot be applied twice.
    """
    widths = geometry.tributary_widths()
    if not 0 <= frame_index < len(widths):
        raise LoadError(
            f"frame_index {frame_index} outside 0..{len(widths) - 1}"
        )
    return widths[frame_index] / 1000.0


def line_load(load: AreaLoad, geometry: PEBGeometry, frame_index: int) -> float:
    """kN/m on one frame line, measured on the load's own surface.

    The value is NOT converted between surfaces here.  It stays on the
    surface it was defined on, and the direction handed to the analysis says
    which that is - the analysis then applies ``cos(theta)`` once, in the one
    place that already knows how.  Converting here as well would apply it
    twice.
    """
    return load.value * tributary_width(geometry, frame_index)


def gravity_loads_for_frame(
    layout: FrameLayout,
    frame_index: int,
    *,
    dead: AreaLoad,
    roof_live: AreaLoad | None = None,
    wall_dead: AreaLoad | None = None,
) -> tuple[FrameLoad, ...]:
    """Every gravity line load on one frame line.

    Roof loads go on the rafters; ``wall_dead`` (cladding and girts hanging
    off the columns) goes on the columns as a vertical load along their
    length.
    """
    geometry = layout.geometry
    out: list[FrameLoad] = []
    w_trib = tributary_width(geometry, frame_index)

    rafters = [m.mark for m in layout.members if m.member_type is MemberType.RAFTER]
    columns = [
        m.mark
        for m in layout.members
        if m.member_type in (MemberType.COLUMN, MemberType.INTERIOR_COLUMN)
    ]

    for mark in rafters:
        out.append(
            FrameLoad(
                case="D",
                member_mark=mark,
                w=line_load(dead, geometry, frame_index),
                direction=_DIRECTION_FOR[dead.surface],
                origin=f"{dead.label}: {dead.value:.3f} kN/m2 "
                f"({dead.surface.value}) x {w_trib:.3f} m tributary",
            )
        )
        if roof_live is not None:
            out.append(
                FrameLoad(
                    case="Lr",
                    member_mark=mark,
                    w=line_load(roof_live, geometry, frame_index),
                    direction=_DIRECTION_FOR[roof_live.surface],
                    origin=f"{roof_live.label}: {roof_live.value:.3f} kN/m2 "
                    f"({roof_live.surface.value}) x {w_trib:.3f} m tributary",
                )
            )

    if wall_dead is not None:
        for mark in columns:
            out.append(
                FrameLoad(
                    case="D",
                    member_mark=mark,
                    w=line_load(wall_dead, geometry, frame_index),
                    direction=LoadDirection.GLOBAL_Y,
                    origin=f"{wall_dead.label}: {wall_dead.value:.3f} kN/m2 "
                    f"x {w_trib:.3f} m tributary, hung off the column",
                )
            )

    return tuple(out)


@dataclass(frozen=True)
class AppliedLoads:
    """What actually went into the analysis, and what did not.

    ``skipped`` is not a detail: a combination named ``1.2D+1.0W`` that was
    solved without any wind case is a lie on the calculation sheet, and the
    reader has no way to see it.  So it is reported, and the caller is
    expected to put it on the sheet.
    """

    cases: tuple[str, ...]
    combinations: tuple[LoadCombination, ...]
    skipped: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(
            f"Load combination {name} was NOT analysed: no load was applied "
            f"for case(s) {', '.join(missing)}."
            for name, missing in self.skipped
        )


def apply_loads(
    analysis: FrameAnalysis,
    loads: Iterable[FrameLoad],
    combinations: Sequence[LoadCombination],
) -> AppliedLoads:
    """Push line loads and combinations into the analysis, in one place.

    A combination runs only if EVERY case it names carries a load.  The
    first version instead dropped the unmatched factors and ran what was
    left, so with no wind modelled the analysis solved ``1.2D`` but the
    sheet printed ``1.2D+1.0W+0.5Lr`` beside the answer.  Partial
    combinations are now skipped outright and reported by name.
    """
    present: set[str] = set()
    for fl in loads:
        analysis.add_line_load(fl.case, fl.member_mark, fl.w, fl.direction)
        present.add(fl.case)

    applied: list[LoadCombination] = []
    skipped: list[tuple[str, tuple[str, ...]]] = []
    for c in combinations:
        missing = tuple(sorted(set(c.factors) - present))
        if missing:
            skipped.append((c.name, missing))
            continue
        analysis.add_combination(c.name, dict(c.factors))
        applied.append(c)

    if not applied:
        raise LoadError(
            "no combination could be analysed: every one names a case with "
            f"no applied load. Applied cases were {sorted(present) or 'none'}."
        )
    return AppliedLoads(
        cases=tuple(sorted(present)),
        combinations=tuple(applied),
        skipped=tuple(skipped),
    )


def build_load_summary(
    loads: Sequence[FrameLoad],
    applied: "AppliedLoads",
    *,
    cases: Sequence[LoadCase] = (),
    qh: float = 0.0,
    end_zone_a: float = 0.0,
) -> LoadSummary:
    """A :class:`LoadSummary` for the calculation sheet.

    Takes the :class:`AppliedLoads` from :func:`apply_loads`, not the full
    combination list, so the sheet can only ever print combinations that
    were actually solved.

    ``frame_line_loads`` is keyed ``"<member>/<case>"`` so a reviewer can
    trace any line load on the sheet back to the member it sits on.
    """
    return LoadSummary(
        cases=tuple(cases),
        combinations=tuple(applied.combinations),
        frame_line_loads={f"{fl.member_mark}/{fl.case}": fl.w for fl in loads},
        qh=qh,
        end_zone_a=end_zone_a,
    )
