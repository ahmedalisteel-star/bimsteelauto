"""
bimsteelauto.analysis.space
===========================

THE WHOLE BUILDING, IN THREE DIMENSIONS.

:mod:`bimsteelauto.analysis.frame` solves ONE transverse frame line, held
in its own plane; :mod:`bimsteelauto.analysis.bracing` works out the
longitudinal load path by closed-form statics.  Between them they design a
PEB correctly and they cannot show you a torsion diagram, an out-of-plane
moment, or what the third frame does when the wind is on the endwall -
because in that pair of models those quantities do not exist.

This module builds the building: every frame line, the purlins, the girts,
the eave struts and the bracing, in one PyNite ``FEModel3D`` with nothing
artificially restrained.  Every station comes back with all six components
- ``P, Vy, Vz, T, My, Mz`` - and every node with three translations and
three rotations.

It does NOT replace ``frame.py``.  That module is verified, it is what the
member design is done on, and a plane frame is the right model for a
transverse frame in a braced building.  This one is additive: a second,
richer view of the same building, and the first thing it is asked to do is
AGREE with the plane frame where the two overlap.

----------------------------------------------------------------------
COORDINATES - read this before anything else
----------------------------------------------------------------------
::

    X   across the frame (the span)
    Y   UP
    Z   along the building (the length)

**Y is up, and that is not a preference.**  PyNite's local-axis rules are
hard-wired around it: ``Member3D.T`` treats "vertical" as constant X and Z,
and its comment "the top of the beam is always on top" means the local y
axis is given an upward component in +Y.  Build this model Z-up and every
member's local axes come out rotated, ``Iy`` and ``Iz`` swap on the sloping
members, and the answer is wrong in a way that still looks like a frame.

This is the SAME convention ``frame.py`` already uses - it puts its plane
frame at ``(x, y, 0)``, X across and Y up - so the 3D model is that model
with frame lines spread along Z, not a different one.

Layer 2's model is ``X across, Y along, Z up``.  The mapping between them
is one swap, ``(x, y, z) -> (x, z, y)``, and it belongs in the adapter next
to :func:`bimsteelauto_fc.builder._node_xyz`, which already does exactly
this for the plane frame.

----------------------------------------------------------------------
Local axes, and why each member is rotated the way it is
----------------------------------------------------------------------
``add_section(name, A, Iy, Iz, J)`` - PyNite bends about local z using
``Iz``.  With the default roll:

===============  =========================  ==========================
member           PyNite's default local z   what that means
===============  =========================  ==========================
column           global +Z                  bends in the frame plane
rafter           global +/-Z                bends in the frame plane
along-building   global -X                  bends in a vertical plane
===============  =========================  ==========================

So for the frame members ``Iz`` is the STRONG axis - which is why both this
module and ``frame.py`` pass ``add_section(..., Iy=s.Iy, Iz=s.Ix, ...)``.

The secondary members are then rolled off that default:

* **purlin** - ``rotation = roof slope angle``, so the web is normal to the
  roof and the strong axis takes the load off the sheeting.
* **girt** - ``rotation = 90 degrees``, so the web is horizontal and the
  strong axis takes the WIND, which is what a girt is for.  Left at zero it
  would resist wind on its weak axis and be roughly forty times too
  flexible.
* **eave strut, bracing** - ``rotation = 0``.

----------------------------------------------------------------------
What this module does NOT do - the Phase 1 line
----------------------------------------------------------------------
It redistributes THE LOADS IT IS GIVEN.  Loads still arrive as per-frame
line loads from ``loads/frame.py``, applied to the frame members of the
frame line they belong to.  This module does not derive load from a roof
SURFACE and it does not put load on the purlins, so the purlins and girts
act here as struts and restraints, not as loaded beams.  That is a real
boundary and it is stated on the result, not implied.

Consequences worth naming:

* An END frame carries the tributary width its caller gives it.  If the
  caller hands every frame the interior tributary, the end frames are
  overloaded - by design of the input, not of this module.
* Torsion and out-of-plane moment in the PRIMARY members will come out
  small in a properly braced building, because the purlins and girts
  restrain them.  That is the right answer, not a failure to compute one.

Units: kN, kN.m, mm, MPa.  PyNite is driven in N and mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..sections import TaperedMember, WeldedI
from ..spec import E_STEEL, MemberType, PEBGeometry
from .frame import (
    AnalysisError,
    BaseFixity,
    FrameLayout,
    FrameSections,
    LoadDirection,
    ModelSettings,
    build_layout,
)

__all__ = [
    "VERIFIED",
    "COVERAGE_GAPS",
    "SpaceError",
    "SectionProps",
    "SpaceMember",
    "SpaceLayout",
    "SpaceStation",
    "SpaceEnvelope",
    "SpaceAnalysis",
    "build_space_layout",
    "build_space_from_run",
    "open_section_J",
]

_N_PER_KN = 1000.0
_MM_PER_M = 1000.0
_G_STEEL = 77_000.0
_NU = 0.3
_RHO_STEEL = 7.85e-9


class SpaceError(AnalysisError):
    """The 3D model could not be built or solved."""


# ---------------------------------------------------------------------------
# Verification gate
# ---------------------------------------------------------------------------

#: What a 3D frame model can get wrong is not arithmetic - PyNite does the
#: arithmetic, and it is the same solver the plane frame already trusts.
#: What it can get wrong is MODELLING: an axis rolled the wrong way, a
#: taper smeared into a prism, a purlin floating beside the rafter instead
#: of bolted to it, both diagonals of an X working at once.  Every one of
#: those assembles, solves cleanly and looks like a building.
#:
#: CLOSED 2026-09-06.  Each flag is closed by a control whose answer is
#: known WITHOUT running this module, in ``tests/test_space.py``.
#:
#:     agrees_with_the_plane_frame
#:         The 3D model, loaded exactly as the plane frame is, reproduces
#:         the VERIFIED plane frame at an interior frame line to 0.08% on
#:         P, V, M, both base reactions and the eave drift.  Two
#:         independent models of one building agreeing is the only check
#:         that catches a rolled axis, and it found a real defect: the
#:         first version modelled each tapered member as ONE prismatic
#:         member on its start section and came out 36% away - a tapered
#:         column on its shallow end is too flexible and a tapered rafter
#:         on its deep end is too stiff, and the two errors add rather than
#:         cancel.  Fixed by slicing the taper here the same way frame.py
#:         does.
#:
#:     symmetric_load_gives_no_torsion
#:         THE NEGATIVE CONTROL.  A symmetric building under symmetric
#:         gravity has Vz = T = My = 0 everywhere and no longitudinal
#:         drift - known by symmetry, not by this model.  Every rolled
#:         axis, swapped section and out-of-plane load fails it.  A test
#:         that merely asserted "a torsion diagram was produced" would pass
#:         on all of them.  Measured: worst |T| < 1e-3 kN.m.
#:
#:     longitudinal_path_matches_the_hand_calculation
#:         Wind along the building arrives at the base through the braced
#:         bays.  The demo building's wall diagonal is 31.8 kN by the
#:         closed-form statics in ``analysis/bracing.py``; this model gives
#:         30.52 kN, which is 96.0% of it - and the model also shows that
#:         96.2% of the applied force reaches the diagonals, the rest going
#:         through the frames in weak-axis bending (2.6 kN.m in a column).
#:         The hand method assumes the diagonals take everything, so it is
#:         conservative by exactly the amount the frames pick up.  The two
#:         agree, and the 4% is explained rather than tolerated.
#:
#:     compression_diagonal_drops_out
#:         Of eight wall diagonals under longitudinal load, exactly four
#:         carry - all in tension - and four are at zero.  The solver
#:         arrives at the assumption ``bracing.py`` makes by hand.  A model
#:         where both diagonals worked would be twice as stiff along its
#:         length as the building.
VERIFIED: dict[str, bool] = {
    "agrees_with_the_plane_frame": True,
    "symmetric_load_gives_no_torsion": True,
    "longitudinal_path_matches_the_hand_calculation": True,
    "compression_diagonal_drops_out": True,
}

COVERAGE_GAPS: tuple[str, ...] = (
    "Loads are applied to the FRAME MEMBERS of each frame line, exactly as "
    "the plane frame receives them. Load is not derived from a roof surface "
    "and the purlins carry none, so in this model the purlins and girts are "
    "struts and restraints rather than loaded beams. A purlin's own bending "
    "is designed in design/purlins.py and is not read from here.",
    "Endwall framing is not built - no endwall posts, no endwall rafters, "
    "no gable columns. The end frame lines carry an ordinary transverse "
    "frame, which is what the adapter also builds. Wind on the endwall "
    "therefore has to be introduced as node loads at the eave rather than "
    "picked up by posts.",
    "Member design is not run off this model - the CALCULATION SHEET is "
    "still written from the plane frame, which is the verified "
    "second-order model. The six-component stations ARE graded when the "
    "contour is built with run=: F6 minor-axis flexure, G6 weak-axis "
    "shear, H3.3 torsional shear and H1-1 with both bending terms. Where "
    "the two models disagree that is information, not an error, and "
    "nothing here overrides the sheet.",
    "Torsion is graded on UNIFORM (St Venant) shear only - the warping "
    "stresses need the AISC Design Guide 9 solution and this model does "
    "not produce the end conditions to solve it with. On an open section "
    "the warping term is usually the larger one, so a torsion ratio from "
    "here is a LOWER BOUND. See design.members.COVERAGE_GAPS.",
    "Connections are modelled as continuous. Every joint between frame "
    "members is rigid and every purlin, girt and strut is rigidly attached "
    "to the frame, so this model cannot show the flexibility of a real "
    "bolted cleat or the release at a pinned strut end.",
    "Second-order analysis and tension-only bracing are both iterative, and "
    "PyNite runs them by different routes. Which one was used is recorded "
    "on the SpaceEnvelope as p_delta and tension_only; a run with "
    "tension-only bracing that could not also do P-Delta says so rather "
    "than silently giving a first-order answer.",
)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def open_section_J(*plates: tuple[float, float]) -> float:
    """St Venant torsion constant of a thin open section, mm^4.

    ``J = sum(b * t^3) / 3`` over the rectangular plate elements - the
    standard thin-walled result.  Used ONLY for the cold-formed members,
    whose catalogue record carries A, Ix and Iy but no J.

    It is an approximation and it is stated as one.  It is also the right
    approximation to make here: an open section's J is tiny whatever
    formula is used, the member's torsional resistance in the real building
    comes from the sheeting rather than from J, and the alternative -
    leaving J out - would make the member torsionally rigid, which is the
    error that actually changes an answer.
    """
    return sum(b * t ** 3 for b, t in plates) / 3.0


@dataclass(frozen=True)
class SectionProps:
    """The four numbers PyNite needs, plus the name to report them under.

    ``Iz`` is bending about the member's local z, which with PyNite's
    default roll is the STRONG axis for every member in this model - see
    the module docstring.  Keeping the field named after the solver's axis
    rather than after "strong" is deliberate: the mapping from a section's
    Ix to a solver's Iz is exactly where this goes wrong, so it is written
    out at every call site instead of being hidden behind a nice name.
    """

    name: str
    A: float
    Iy: float
    Iz: float
    J: float

    @classmethod
    def from_welded_i(cls, name: str, s: WeldedI) -> "SectionProps":
        return cls(name=name, A=s.A, Iy=s.Iy, Iz=s.Ix, J=s.J)

    @classmethod
    def from_cold_formed(cls, name: str, sec) -> "SectionProps":
        """A catalogue C or Z.  A, Ix and Iy are catalogue values; J is not
        in the catalogue and is computed by :func:`open_section_J`."""
        H = float(getattr(sec, "H", 0.0))
        B = float(getattr(sec, "B", 0.0))
        D = float(getattr(sec, "Dlip", 0.0))
        t = float(getattr(sec, "t", 0.0))
        Iy = float(getattr(sec, "Iy", 0.0))
        Ix = float(getattr(sec, "Ix", 0.0))
        if not (Ix > 0.0 and t > 0.0):
            raise SpaceError(
                f"cold-formed section {name!r} has no usable Ix/t - it "
                f"cannot be put in the model, and a guessed stiffness would "
                f"change every force around it"
            )
        if Iy <= 0.0:
            raise SpaceError(
                f"cold-formed section {name!r} carries no Iy. A member with "
                f"no weak-axis stiffness is a mechanism out of plane, and "
                f"this model is the one place that matters"
            )
        # The CATALOGUE J if the record carries one - a supplier value beats
        # a formula every time, and ColdFormedSection does carry J. The thin
        # open-section estimate is the fallback, and only that.
        J = float(getattr(sec, "J", 0.0) or 0.0)
        if J <= 0.0:
            J = open_section_J((H, t), (B, t), (B, t), (D, t), (D, t))
        return cls(name=name, A=float(sec.A), Iy=Iy, Iz=Ix, J=J)

    @classmethod
    def round_bar(cls, name: str, diameter: float) -> "SectionProps":
        """A bracing rod, on its nominal shank."""
        r = diameter / 2.0
        I = math.pi * r ** 4 / 4.0
        return cls(name=name, A=math.pi * r ** 2, Iy=I, Iz=I, J=2.0 * I)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpaceMember:
    """One member of the 3D model.

    ``i_point`` / ``j_point`` are ``(X, Y, Z)`` in mm, Y up.  ``rotation``
    is the roll of the local axes about the member, radians, applied on top
    of PyNite's default - see the module docstring for why each member type
    gets the value it does.
    """

    mark: str
    member_type: MemberType
    i_point: tuple[float, float, float]
    j_point: tuple[float, float, float]
    section: SectionProps
    taper: TaperedMember | None = None
    rotation: float = 0.0
    tension_only: bool = False
    frame_line: int | None = None      # which frame line, for the frame members
    plane_mark: str = ""               # the plane frame's mark, where there is one
    #: Where this SEGMENT sits along its design member, mm from the i end.
    #: A tapered column is several segments and the design member is the
    #: thing an engineer names, so the two have to be kept apart.
    x_start: float = 0.0
    x_end: float = 0.0

    @property
    def length(self) -> float:
        return math.dist(self.i_point, self.j_point)

    @property
    def is_primary(self) -> bool:
        return self.member_type in (
            MemberType.COLUMN, MemberType.RAFTER, MemberType.INTERIOR_COLUMN
        )


@dataclass(frozen=True)
class SpaceLayout:
    """Every node and member in the building."""

    members: tuple[SpaceMember, ...]
    nodes: dict[str, tuple[float, float, float]]
    base_nodes: tuple[str, ...]
    frame_lines: tuple[float, ...]          # Z of each frame line, mm
    plane: FrameLayout                      # the frame line this was grown from
    geometry: PEBGeometry
    settings: ModelSettings
    braced_bays: tuple[int, ...] = ()
    notes: tuple[str, ...] = ()
    #: ``{design member: [(solver member, x_start, x_end), ...]}`` - the map
    #: from the member an engineer names to the prismatic slices the solver
    #: was given. Exactly the same idea as ``FrameAnalysis.segments``, and
    #: for exactly the same reason: a tapered member is not prismatic and
    #: modelling it as though it were is not a small error. It was measured:
    #: one prismatic member on the START section put the interior frame 36%
    #: away from the plane frame, because it makes the column too flexible
    #: and the rafter too stiff at the same time.
    segments: dict[str, tuple[tuple[str, float, float], ...]] = field(
        default_factory=dict
    )

    @property
    def design_members(self) -> tuple[str, ...]:
        """Every member results are reported against - not the slices."""
        return tuple(self.segments)

    def member(self, mark: str) -> SpaceMember:
        for m in self.members:
            if m.mark == mark:
                return m
        raise KeyError(mark)

    @property
    def marks(self) -> tuple[str, ...]:
        return tuple(m.mark for m in self.members)

    def frame_members(self, line: int) -> tuple[SpaceMember, ...]:
        """The primary members on one frame line, in plane-frame order."""
        return tuple(m for m in self.members if m.frame_line == line
                     and m.is_primary)

    @property
    def steel_weight(self) -> float:
        """Primary steel in the whole model, kg - a cross-check against the
        schedule, which counts the same members from the other end."""
        return sum(m.taper.weight for m in self.members if m.taper is not None)


def _node_name(prefix: str, line: int) -> str:
    return f"{prefix}#{line}"


def _merge_stations(values, length, tol=1.0):
    """Sorted break positions along a member, ends included, near-duplicates
    collapsed.

    ``tol`` is 1 mm: two break points a fraction of a millimetre apart are
    the same point on a fabrication drawing, and putting a member of that
    length into the model makes the stiffness matrix ill-conditioned for no
    gain at all.
    """
    out: list[float] = []
    for v in sorted([0.0, length] + [float(v) for v in values]):
        if v < -tol or v > length + tol:
            continue
        v = min(max(v, 0.0), length)
        if not out or v - out[-1] > tol:
            out.append(v)
        else:
            out[-1] = max(out[-1], v)
    if len(out) < 2 or out[-1] < length - tol:      # pragma: no cover
        out.append(length)
    return out


def _attachment_stations(fm, geometry, *, purlins: bool, girts: bool):
    """Where the secondary members land on this design member, mm from i.

    A purlin does not float beside a rafter - it is bolted to it, and in a
    3D model that means they SHARE A NODE. Without that the purlin line is
    a free-floating chain of members restrained by nothing, and the
    stiffness matrix is singular in a way a solver may or may not report.
    So the frame member is split wherever a secondary member arrives, and
    those splits are merged with the taper's own.
    """
    if fm.member_type is MemberType.RAFTER and purlins:
        step = geometry.purlin_spacing
    elif fm.member_type in (
        MemberType.COLUMN, MemberType.INTERIOR_COLUMN
    ) and girts:
        step = geometry.girt_spacing
    else:
        return ()
    if step <= 0.0:
        return ()
    L = fm.length
    out, k = [], 1
    while k * step < L - 1e-6:
        out.append(k * step)
        k += 1
    return tuple(out)


def build_space_layout(
    geometry: PEBGeometry,
    sections: FrameSections,
    *,
    settings: ModelSettings | None = None,
    purlin_section=None,
    girt_section=None,
    eave_strut_section: WeldedI | None = None,
    brace_diameter: float | None = None,
    braced_bays: Sequence[int] = (),
    roof_panel_splits: int = 2,
) -> SpaceLayout:
    """Grow the whole building from the one designed frame line.

    The transverse frame comes from :func:`bimsteelauto.analysis.build_layout`
    - the SAME function the plane frame and the FreeCAD adapter both use, so
    the 3D model cannot be laid out on a frame that differs from the one that
    was designed.  It is lifted to 3D at each frame line's Z, split wherever
    a purlin or girt lands, and the longitudinal members are hung between
    the lines on those shared nodes.

    Every secondary member is OPTIONAL and its absence is recorded rather
    than filled in.  A model with no bracing has no longitudinal load path;
    it should say so and fail to solve, not be quietly propped up by
    something invented here.
    """
    settings = settings or ModelSettings()
    plane = build_layout(geometry, sections, settings)

    zs = [0.0]
    for bay in geometry.bay_spacings:
        zs.append(zs[-1] + bay)
    n_bays = len(zs) - 1

    want_purlins = purlin_section is not None and geometry.purlin_spacing > 0
    want_girts = girt_section is not None and geometry.girt_spacing > 0

    nodes: dict[str, tuple[float, float, float]] = {}
    members: list[SpaceMember] = []
    base_nodes: list[str] = []
    notes: list[str] = []
    segments: dict[str, list[tuple[str, float, float]]] = {}
    #: ``(plane mark, line, station) -> node name`` - where a secondary
    #: member is allowed to attach.  Built while slicing, so a purlin can
    #: only ever be hung on a node that really exists on the rafter.
    attach: dict[tuple[str, int, float], str] = {}

    # -- the frames, sliced -----------------------------------------------
    #
    # Sliced by the same TaperedMember rule the plane frame uses, at the
    # same max_taper_step, PLUS a split at every secondary attachment.
    #
    # The taper slicing is not tidiness. One prismatic member on the START
    # section was tried first: it solved cleanly, looked entirely
    # reasonable, and put the interior frame 36% away from the plane frame -
    # because a tapered column modelled on its shallow end is too flexible
    # and a tapered rafter modelled on its deep end is too stiff, and the
    # two errors do not cancel. They both push thrust out of the frame.
    n_seg_max = 0
    for line, z in enumerate(zs):
        for name, (x, y) in plane.nodes.items():
            nodes[_node_name(name, line)] = (x, y, z)
        for b in plane.base_nodes:
            base_nodes.append(_node_name(b, line))

        for fm in plane.members:
            key = f"{fm.mark}#{line}"
            L = fm.length
            n = fm.taper.suggested_segments(
                max_depth_change=settings.max_taper_step
            )
            taper_breaks = [L * i / n for i in range(n + 1)]
            fixings = _attachment_stations(
                fm, geometry, purlins=want_purlins, girts=want_girts
            )
            breaks = _merge_stations(list(taper_breaks) + list(fixings), L)
            n_seg_max = max(n_seg_max, len(breaks) - 1)

            ux = (fm.j_point[0] - fm.i_point[0]) / L
            uy = (fm.j_point[1] - fm.i_point[1]) / L

            def point(sx: float, z=z, fm=fm, ux=ux, uy=uy):
                return (fm.i_point[0] + ux * sx, fm.i_point[1] + uy * sx, z)

            # End nodes are the PLANE frame's own nodes, so two members
            # meeting at an eave meet at one node and not at two that
            # happen to coincide.
            i_name = _node_name(_plane_node_at(plane, fm.i_point), line)
            j_name = _node_name(_plane_node_at(plane, fm.j_point), line)

            names = [i_name]
            for sx in breaks[1:-1]:
                nm = f"{key}@{sx:.0f}"
                nodes[nm] = point(sx)
                names.append(nm)
            names.append(j_name)

            entries: list[tuple[str, float, float]] = []
            for k in range(len(breaks) - 1):
                a, b = breaks[k], breaks[k + 1]
                mark = f"{key}_s{k}"
                members.append(
                    SpaceMember(
                        mark=mark,
                        member_type=fm.member_type,
                        i_point=nodes[names[k]],
                        j_point=nodes[names[k + 1]],
                        # Mid-station section, the same rule
                        # TaperedMember.discretise uses.
                        section=SectionProps.from_welded_i(
                            mark, fm.taper.at(0.5 * (a + b))
                        ),
                        taper=fm.taper if k == 0 else None,
                        rotation=0.0,
                        frame_line=line,
                        plane_mark=fm.mark,
                        x_start=a,
                        x_end=b,
                    )
                )
                entries.append((mark, a, b))
            segments[key] = entries

            for sx in fixings:
                idx = min(
                    range(len(breaks)), key=lambda i: abs(breaks[i] - sx)
                )
                attach[(fm.mark, line, round(sx, 3))] = names[idx]

    notes.append(
        f"Primary members sliced into up to {n_seg_max} prismatic segments: "
        f"the taper's own breaks at max_taper_step "
        f"{settings.max_taper_step:g}, merged with a split at every purlin "
        f"and girt so the secondary members share a node with the frame."
    )

    # -- longitudinal members ---------------------------------------------
    def run(prefix, member_type, section, rotation, name_at, tag,
            tension_only=False):
        made = 0
        for bay in range(n_bays):
            i_name, j_name = name_at(bay), name_at(bay + 1)
            if i_name is None or j_name is None:
                continue
            mark = f"{prefix}_{tag}_b{bay}"
            members.append(
                SpaceMember(
                    mark=mark, member_type=member_type,
                    i_point=nodes[i_name], j_point=nodes[j_name],
                    section=section, rotation=rotation,
                    tension_only=tension_only,
                )
            )
            segments[mark] = [(mark, 0.0, math.dist(nodes[i_name],
                                                    nodes[j_name]))]
            made += 1
        return made

    slope_angle = math.atan(geometry.roof_slope)

    if eave_strut_section is not None:
        sec = SectionProps.from_welded_i("eave_strut", eave_strut_section)
        for k, e in enumerate(plane.eave_nodes):
            run("ES", MemberType.EAVE_STRUT, sec, 0.0,
                lambda bay, e=e: _node_name(e, bay), f"e{k}")
    else:
        notes.append(
            "No eave strut section was supplied, so none was built. The "
            "longitudinal path runs eave strut -> roof bracing -> wall "
            "bracing; without the strut the roof bracing has nothing to "
            "collect into."
        )

    n_girt = n_purlin = 0
    if want_girts:
        sec = SectionProps.from_cold_formed("girt", girt_section)
        for fm in plane.members:
            if fm.member_type not in (
                MemberType.COLUMN, MemberType.INTERIOR_COLUMN
            ):
                continue
            for sx in _attachment_stations(
                fm, geometry, purlins=False, girts=True
            ):
                run("GT", MemberType.GIRT, sec, math.pi / 2.0,
                    lambda bay, m=fm.mark, s=sx:
                        attach.get((m, bay, round(s, 3))),
                    f"{fm.mark}_{sx:.0f}")
                n_girt += 1
        notes.append(
            f"{n_girt} girt lines, web horizontal (rotation 90 deg) so the "
            f"strong axis takes the wind."
        )
    else:
        notes.append("No girt section supplied - none built.")

    if want_purlins:
        sec = SectionProps.from_cold_formed("purlin", purlin_section)
        for fm in plane.members:
            if fm.member_type is not MemberType.RAFTER:
                continue
            for sx in _attachment_stations(
                fm, geometry, purlins=True, girts=False
            ):
                run("PL", MemberType.PURLIN, sec, slope_angle,
                    lambda bay, m=fm.mark, s=sx:
                        attach.get((m, bay, round(s, 3))),
                    f"{fm.mark}_{sx:.0f}")
                n_purlin += 1
        notes.append(
            f"{n_purlin} purlin lines, web normal to the roof "
            f"(rotation {math.degrees(slope_angle):.2f} deg)."
        )
    else:
        notes.append("No purlin section supplied - none built.")

    # -- bracing ----------------------------------------------------------
    bays = tuple(sorted({b for b in braced_bays if 0 <= b < n_bays}))
    if brace_diameter and bays:
        sec = SectionProps.round_bar("brace", brace_diameter)
        n_brace = 0
        sidewalls = (0, len(plane.base_nodes) - 1)
        for b in bays:
            for k, (bn, en) in enumerate(
                zip(plane.base_nodes, plane.eave_nodes)
            ):
                if k not in sidewalls:
                    continue          # sidewalls only - interior columns
                                      # are not a wall
                a1, a2 = _node_name(bn, b), _node_name(en, b)
                b1, b2 = _node_name(bn, b + 1), _node_name(en, b + 1)
                for d, (lo, hi) in enumerate(((a1, b2), (b1, a2))):
                    mark = f"WB_b{b}_w{k}_{d}"
                    members.append(
                        SpaceMember(
                            mark=mark,
                            member_type=MemberType.WALL_BRACING,
                            i_point=nodes[lo], j_point=nodes[hi],
                            section=sec, tension_only=True,
                        )
                    )
                    segments[mark] = [
                        (mark, 0.0, math.dist(nodes[lo], nodes[hi]))
                    ]
                    n_brace += 1

            for fm in plane.members:
                if fm.member_type is not MemberType.RAFTER:
                    continue
                # The roof panel runs from the eave to the panel point the
                # design used - the same 1/roof_panel_splits along the
                # rafter that the adapter draws and that bracing.py sized.
                s_panel = fm.length / max(1, int(roof_panel_splits))
                inner = min(
                    (s for (m, ln, s) in attach if m == fm.mark and ln == b),
                    key=lambda s: abs(s - s_panel), default=None,
                )
                if inner is None:
                    notes.append(
                        f"no purlin node on {fm.mark} to land a roof brace "
                        f"on - none drawn in bay {b}"
                    )
                    continue
                e0 = _node_name(_plane_node_at(plane, fm.i_point), b)
                e1 = _node_name(_plane_node_at(plane, fm.i_point), b + 1)
                p0 = attach[(fm.mark, b, inner)]
                p1 = attach[(fm.mark, b + 1, inner)]
                for d, (lo, hi) in enumerate(((e0, p1), (e1, p0))):
                    mark = f"RB_b{b}_{fm.mark}_{d}"
                    members.append(
                        SpaceMember(
                            mark=mark,
                            member_type=MemberType.ROOF_BRACING,
                            i_point=nodes[lo], j_point=nodes[hi],
                            section=sec, tension_only=True,
                        )
                    )
                    segments[mark] = [
                        (mark, 0.0, math.dist(nodes[lo], nodes[hi]))
                    ]
                    n_brace += 1
        notes.append(
            f"{n_brace} tension-only diagonals in bays {list(bays)} - the "
            f"compression diagonal drops out in the solver rather than by "
            f"assumption."
        )
    else:
        notes.append(
            "NO BRACING was built. This building has no longitudinal load "
            "path and the model is a mechanism along its length: the solve "
            "will fail, and that failure is the correct report."
        )

    return SpaceLayout(
        members=tuple(members),
        nodes=nodes,
        base_nodes=tuple(base_nodes),
        frame_lines=tuple(zs),
        plane=plane,
        geometry=geometry,
        settings=settings,
        braced_bays=bays,
        notes=tuple(notes),
        segments={k: tuple(v) for k, v in segments.items()},
    )


def _plane_node_at(plane: FrameLayout, point: tuple[float, float]) -> str:
    for name, p in plane.nodes.items():
        if math.isclose(p[0], point[0], abs_tol=1e-6) and math.isclose(
            p[1], point[1], abs_tol=1e-6
        ):
            return name
    raise SpaceError(f"no plane-frame node at {point}")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpaceStation:
    """All six components at one station.  kN and kN.m, member local axes.

    ``P`` positive in COMPRESSION, matching ``frame.StationForces`` and the
    AISC chapter the design layer calls most - the two modules must not
    disagree about a sign.
    """

    x: float
    P: float = 0.0
    Vy: float = 0.0
    Vz: float = 0.0
    T: float = 0.0
    My: float = 0.0
    Mz: float = 0.0

    def to_member_forces(self):
        from ..spec import MemberForces

        return MemberForces(
            P=self.P, Vy=self.Vy, Vz=self.Vz, T=self.T,
            My=self.My, Mz=self.Mz, station=self.x,
        )


@dataclass(frozen=True)
class SpaceEnvelope:
    """Six-component station results for the whole building.

    ``displacements[node][combo] = (DX, DY, DZ, RX, RY, RZ)`` - mm and
    radians.  ``reactions[node][combo]`` is the full six-component reaction,
    which is what a 3D model can give and a plane frame cannot.
    """

    stations: dict[str, dict[str, tuple[SpaceStation, ...]]]
    reactions: dict[str, dict[str, tuple[float, ...]]]
    displacements: dict[str, dict[str, tuple[float, ...]]]
    combos: tuple[str, ...]
    p_delta: bool
    tension_only: bool
    notes: tuple[str, ...] = ()

    def max_abs(self, mark: str, attr: str) -> float:
        return max(
            abs(getattr(st, attr))
            for sts in self.stations[mark].values()
            for st in sts
        )

    def worst(self, attr: str) -> tuple[str, str, float]:
        """``(mark, combo, value)`` of the largest ``attr`` in the building."""
        best = ("", "", 0.0)
        for mark, per_combo in self.stations.items():
            for combo, sts in per_combo.items():
                for st in sts:
                    v = abs(getattr(st, attr))
                    if v > abs(best[2]):
                        best = (mark, combo, getattr(st, attr))
        return best

    def drift(self, node: str, combo: str) -> tuple[float, float]:
        """``(across, along)`` translation of one node, mm.

        The pair a plane frame cannot report: sway across the building and
        sway ALONG it, which is what the bracing controls.
        """
        d = self.displacements[node][combo]
        return d[0], d[2]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


class SpaceAnalysis:
    """Builds and solves the 3D building.

    Usage mirrors :class:`~bimsteelauto.analysis.frame.FrameAnalysis`, and
    that is deliberate - the two are meant to be run on the same building
    and compared::

        sa = SpaceAnalysis(space_layout)
        sa.add_frame_line_load("D", "R1", 2.4)          # every frame line
        sa.add_node_load("W_long", "E1#0", fz=45.0)     # wind on the endwall
        sa.add_combination("1.2D+1.6Lr", {"D": 1.2, "Lr": 1.6})
        env = sa.solve()
    """

    def __init__(self, layout: SpaceLayout) -> None:
        self.layout = layout
        self.settings = layout.settings
        self._line_loads: list[tuple] = []
        self._node_loads: list[tuple] = []
        self._combos: dict[str, dict[str, float]] = {}
        self._model = None
        self._notes: list[str] = list(layout.notes)

    @property
    def model(self):
        return self._model

    # -- loads ------------------------------------------------------------

    def add_frame_line_load(
        self,
        case: str,
        plane_mark: str,
        w: float,
        direction: LoadDirection = LoadDirection.GLOBAL_Y,
        w_end: float | None = None,
        lines: Sequence[int] | None = None,
    ) -> None:
        """The plane frame's line load, applied on every frame line.

        ``plane_mark`` is the mark on the DESIGNED frame - ``R1``, ``C2`` -
        and the load goes onto that member of each frame line named in
        ``lines`` (all of them by default).

        This is the Phase 1 load path and its limit is stated on the
        result: ``w`` already contains the tributary width its caller
        chose. Handing every line the interior tributary overloads the end
        frames, and that is a property of the input, not of the model.
        """
        if plane_mark not in self.layout.plane.marks:
            raise SpaceError(f"unknown plane-frame member {plane_mark!r}")
        want = (tuple(lines) if lines is not None
                else tuple(range(len(self.layout.frame_lines))))
        self._line_loads.append(
            (case, plane_mark, w, w if w_end is None else w_end, direction, want)
        )

    def add_node_load(
        self, case: str, node: str, *, fx: float = 0.0, fy: float = 0.0,
        fz: float = 0.0,
    ) -> None:
        """A point load at one node, kN.  Positive along the global axis.

        This is how longitudinal wind gets in until endwall framing exists:
        the endwall posts are not modelled, so the force they would deliver
        to the eave is applied at the eave.  It is an input, and the
        COVERAGE_GAPS say so.
        """
        if node not in self.layout.nodes:
            raise SpaceError(f"unknown node {node!r}")
        self._node_loads.append((case, node, fx, fy, fz))

    def add_combination(self, name: str, factors: Mapping[str, float]) -> None:
        if not factors:
            raise SpaceError(f"combination {name!r} has no factors")
        self._combos[name] = dict(factors)

    @property
    def cases(self) -> tuple[str, ...]:
        seen: list[str] = []
        for case, *_ in self._line_loads:
            if case not in seen:
                seen.append(case)
        for case, *_ in self._node_loads:
            if case not in seen:
                seen.append(case)
        return tuple(seen)

    # -- model ------------------------------------------------------------

    def build(self):
        """Assemble the PyNite model.  Idempotent within one instance."""
        if self._model is not None:
            return self._model
        try:
            from Pynite import FEModel3D
        except ImportError as exc:  # pragma: no cover - environment issue
            raise SpaceError(
                "PyNiteFEA is required for 3D analysis. "
                "Install it with: pip install PyNiteFEA"
            ) from exc

        model = FEModel3D()
        model.add_material("steel", E_STEEL, _G_STEEL, _NU, _RHO_STEEL)

        for name, (x, y, z) in self.layout.nodes.items():
            model.add_node(name, x, y, z)

        self._name_of = {v: k for k, v in self.layout.nodes.items()}
        seen_sections: set[str] = set()
        for m in self.layout.members:
            s = m.section
            if s.name not in seen_sections:
                # add_section(name, A, Iy, Iz, J). Iz is bending about the
                # local z axis, which is the STRONG axis here - see the
                # module docstring's table. Swapping these two is the one
                # mistake that produces a model that solves cleanly and is
                # wrong everywhere.
                model.add_section(s.name, s.A, s.Iy, s.Iz, s.J)
                seen_sections.add(s.name)
            model.add_member(
                m.mark,
                self._node_at(m.i_point),
                self._node_at(m.j_point),
                "steel",
                s.name,
                rotation=math.degrees(m.rotation),
                tension_only=m.tension_only,
            )

        self._apply_supports(model)
        self._apply_loads(model)
        for name, factors in self._combos.items():
            model.add_load_combo(name, dict(factors))

        self._model = model
        return model

    def _node_at(self, point: tuple[float, float, float]) -> str:
        name = self._name_of.get(point)
        if name is None:
            for n, p in self.layout.nodes.items():
                if all(math.isclose(a, b, abs_tol=1e-6)
                       for a, b in zip(p, point)):
                    return n
            raise SpaceError(f"no node at {point}")
        return name

    def _apply_supports(self, model) -> None:
        """Bases only.

        This is the whole point of the module and it is worth being explicit
        about what is NOT here: no ``support_DZ``, no ``support_RX``, no
        ``support_RY`` on the free nodes. The plane frame restrains those at
        every node to hold itself flat; here the building holds itself up,
        and if it cannot the solve fails - which is the correct answer for a
        building with no bracing.
        """
        fixed = self.settings.base_fixity is BaseFixity.FIXED
        for b in self.layout.base_nodes:
            model.def_support(
                b,
                support_DX=True, support_DY=True, support_DZ=True,
                support_RX=fixed, support_RY=True, support_RZ=fixed,
            )

    def _apply_loads(self, model) -> None:
        scale = _N_PER_KN / _MM_PER_M
        for case, plane_mark, w0, w1, direction, lines in self._line_loads:
            fm = self.layout.plane.member(plane_mark)
            for line in lines:
                key = f"{plane_mark}#{line}"
                entries = self.layout.segments.get(key)
                if not entries:                          # pragma: no cover
                    raise SpaceError(f"no members for {key}")
                # A design member is several slices, and a linearly varying
                # load has to be cut up with them - the same interpolation
                # FrameAnalysis._apply_loads does, for the same reason.
                for mem_name, x0, x1 in entries:
                    f0, f1 = x0 / fm.length, x1 / fm.length
                    self._add_line(
                        model, mem_name, fm,
                        w0 + (w1 - w0) * f0, w0 + (w1 - w0) * f1,
                        direction, case, scale,
                    )

        for case, node, fx, fy, fz in self._node_loads:
            model.add_node_load(node, "FX", fx * _N_PER_KN, case=case)
            model.add_node_load(node, "FY", fy * _N_PER_KN, case=case)
            model.add_node_load(node, "FZ", fz * _N_PER_KN, case=case)

    @staticmethod
    def _add_line(model, mark, fm, w0, w1, direction, case, scale) -> None:
        """kN/m -> N/mm and PyNite's direction code.

        Deliberately the same arithmetic as ``FrameAnalysis._add_segment_load``
        including the projected-load cosine, because the two models are
        required to agree and a difference in how a load is applied would
        show up as a difference in the answer and be blamed on the geometry.
        """
        if direction is LoadDirection.GLOBAL_Y:
            model.add_member_dist_load(
                mark, "FY", -w0 * scale, -w1 * scale, case=case
            )
        elif direction is LoadDirection.GLOBAL_Y_PROJECTED:
            cos_theta = fm.horizontal_projection / fm.length
            model.add_member_dist_load(
                mark, "FY", -w0 * scale * cos_theta, -w1 * scale * cos_theta,
                case=case,
            )
        elif direction is LoadDirection.GLOBAL_X:
            model.add_member_dist_load(
                mark, "FX", w0 * scale, w1 * scale, case=case
            )
        elif direction is LoadDirection.NORMAL:
            model.add_member_dist_load(
                mark, "Fy", -w0 * scale, -w1 * scale, case=case
            )
        elif direction is LoadDirection.AXIAL:
            model.add_member_dist_load(
                mark, "Fx", w0 * scale, w1 * scale, case=case
            )
        else:  # pragma: no cover
            raise SpaceError(f"unhandled direction {direction}")

    # -- solve ------------------------------------------------------------

    def solve(self, *, p_delta: bool = False, stations: int | None = None) -> SpaceEnvelope:
        """Run every combination and return the six-component envelope.

        ``p_delta`` defaults to FALSE here, which is the opposite of the
        plane frame, and the reason is on the record rather than in a
        preference: this model carries TENSION-ONLY diagonals, which PyNite
        resolves by its own iteration, and combining that with the P-Delta
        iteration is a second nonlinearity on top of a first. The plane
        frame is where second-order effects are accounted for, it is
        verified, and it is what the member design reads. Ask for
        ``p_delta=True`` here deliberately, and the envelope records which
        was used either way.
        """
        if not self._combos:
            raise SpaceError("no load combinations were added")
        model = self.build()
        tension_only = any(m.tension_only for m in self.layout.members)

        try:
            if p_delta:
                model.analyze_PDelta(
                    log=False, max_iter=self.settings.p_delta_max_iter
                )
            else:
                model.analyze(log=False, check_statics=False)
        except Exception as exc:
            raise SpaceError(
                f"the 3D solve failed: {exc}. A singular stiffness matrix "
                f"here almost always means a MECHANISM - most often no "
                f"bracing, so the building has no longitudinal load path"
            ) from exc

        return self._extract(model, p_delta, tension_only, stations)

    def _extract(self, model, p_delta, tension_only, stations) -> SpaceEnvelope:
        """PyNite results -> kN, kN.m.  The one unit boundary on the way out."""
        n = stations or self.settings.stations_per_member
        n = max(3, int(n))
        kn = _N_PER_KN
        knm = _N_PER_KN * _MM_PER_M

        # Results are reported against the DESIGN member - the thing an
        # engineer names - not against the slices the solver was given, and
        # ``x`` is measured from the design member's own i end. Same
        # contract as FrameAnalysis._extract, so the two models can be laid
        # side by side without anyone converting a station.
        out_stations: dict[str, dict[str, tuple[SpaceStation, ...]]] = {}
        for key, entries in self.layout.segments.items():
            total = entries[-1][2] if entries else 0.0
            per_combo: dict[str, tuple[SpaceStation, ...]] = {}
            for combo in self._combos:
                sts: list[SpaceStation] = []
                for mem_name, x0, x1 in entries:
                    mem = model.members[mem_name]
                    L = mem.L() if callable(mem.L) else mem.L
                    k = max(3, math.ceil(n * (x1 - x0) / total)) if total else n
                    for i in range(k):
                        xl = L * i / (k - 1)
                        sts.append(
                            SpaceStation(
                                x=x0 + xl,
                                P=mem.axial(xl, combo) / kn,
                                Vy=mem.shear("Fy", xl, combo) / kn,
                                Vz=mem.shear("Fz", xl, combo) / kn,
                                T=mem.torque(xl, combo) / knm,
                                My=mem.moment("My", xl, combo) / knm,
                                Mz=mem.moment("Mz", xl, combo) / knm,
                            )
                        )
                per_combo[combo] = tuple(sorted(sts, key=lambda t: t.x))
            out_stations[key] = per_combo

        reactions: dict[str, dict[str, tuple[float, ...]]] = {}
        for b in self.layout.base_nodes:
            node = model.nodes[b]
            reactions[b] = {
                combo: (
                    node.RxnFX[combo] / kn, node.RxnFY[combo] / kn,
                    node.RxnFZ[combo] / kn, node.RxnMX[combo] / knm,
                    node.RxnMY[combo] / knm, node.RxnMZ[combo] / knm,
                )
                for combo in self._combos
            }

        displacements: dict[str, dict[str, tuple[float, ...]]] = {}
        for name in self.layout.nodes:
            node = model.nodes[name]
            displacements[name] = {
                combo: (
                    node.DX[combo], node.DY[combo], node.DZ[combo],
                    node.RX[combo], node.RY[combo], node.RZ[combo],
                )
                for combo in self._combos
            }

        notes = list(self._notes)
        notes.append(
            f"Solved {'with' if p_delta else 'WITHOUT'} P-Delta; "
            f"tension-only diagonals {'present' if tension_only else 'absent'}."
        )
        return SpaceEnvelope(
            stations=out_stations,
            reactions=reactions,
            displacements=displacements,
            combos=tuple(self._combos),
            p_delta=p_delta,
            tension_only=tension_only,
            notes=tuple(notes),
        )


# ---------------------------------------------------------------------------
# From a solved FrameRun
# ---------------------------------------------------------------------------


def build_space_from_run(run, *, include_longitudinal: bool = True):
    """``FrameRun`` -> a built 3D model, loaded and ready to solve.

    The glue between the pipeline and this module, and it is glue rather
    than a second derivation on purpose.  Everything comes off the run that
    was already solved:

    * the geometry and the sections, through ``build_layout`` - the same
      frame;
    * the purlin, girt and strut sections and the brace diameters, from the
      inputs the design used;
    * the braced bays the design actually braced;
    * and the LOADS AND COMBINATIONS exactly as the plane frame received
      them, replayed onto every frame line through
      :attr:`FrameAnalysis.loads`.

    Nothing here recomputes a load.  If it did, the 3D model and the plane
    frame could disagree for a reason that has nothing to do with the
    model, and the agreement test - which is what makes this module
    trustworthy - would be testing the wrong thing.

    ``include_longitudinal`` adds the endwall force the plane frame cannot
    carry, as node loads at the eaves of the two end frames, in a case
    named ``W_long``.  It is the one thing the 3D model is asked to do that
    the plane frame never could, and it is OPT-OUT rather than automatic so
    a run that only wants gravity gets exactly what it asked for.

    Returns ``(SpaceLayout, SpaceAnalysis)``.  The caller solves, so it
    chooses P-Delta and stations.
    """
    analysis = getattr(run, "analysis", None)
    if analysis is None:
        raise SpaceError(
            "this run carries no solved FrameAnalysis, so its loads cannot "
            "be replayed. Re-run design_frame with an engine of 0.9 or later."
        )
    inputs = run.inputs
    secondary = getattr(inputs, "secondary", None)
    bracing = getattr(inputs, "bracing", None)

    brace_d = None
    if bracing is not None:
        # The WALL brace sizes the sidewall X, which is what carries the
        # longitudinal shear to the base. Where the two differ the wall rod
        # is the one the panel is named for.
        brace_d = getattr(bracing.wall_brace, "diameter", None) or getattr(
            bracing.roof_brace, "diameter", None
        )

    layout = build_space_layout(
        run.layout.geometry,
        inputs.sections,
        settings=run.layout.settings,
        purlin_section=getattr(secondary, "purlin_section", None),
        girt_section=getattr(secondary, "girt_section", None),
        eave_strut_section=getattr(bracing, "strut_section", None),
        brace_diameter=brace_d,
        braced_bays=tuple(getattr(bracing, "braced_bays", ()) or ()),
    )

    sa = SpaceAnalysis(layout)
    for case, mark, w_i, w_j, direction in analysis.loads:
        sa.add_frame_line_load(case, mark, w_i, direction, w_end=w_j)
    for name, factors in analysis.combinations.items():
        sa.add_combination(name, factors)

    if include_longitudinal:
        # F_eave lives on brace_forces, not on the longitudinal SYSTEM -
        # the system is where the bracing IS, the forces are what it
        # carries, and the pipeline keeps them apart.
        F = float(getattr(
            getattr(run, "brace_forces", None), "F_eave", 0.0
        ) or 0.0)
        eaves = layout.plane.eave_nodes
        ends = (0, len(layout.frame_lines) - 1)
        if F > 0.0 and eaves:
            # Split between the two END frames and across the eaves of
            # each. The endwall POSTS that would really deliver this force
            # are not modelled - COVERAGE_GAPS says so - so it is applied
            # where they would hand it over.
            each = F / (len(ends) * len(eaves))
            for line in ends:
                for e in eaves:
                    sa.add_node_load("W_long", f"{e}#{line}", fz=each)
            sa.add_combination("1.0W_long", {"W_long": 1.0})
        elif F <= 0.0:
            sa._notes.append(
                "No longitudinal case: this run reports no endwall eave "
                "force, so there was nothing to apply."
            )

    return layout, sa
