"""
bimsteelauto.analysis.frame
===========================

A 2D portal frame, built from :class:`~bimsteelauto.spec.PEBGeometry`,
solved with PyNiteFEA, returned as a per-member force envelope.

Why 2D
------
A PEB main frame is analysed one frame line at a time.  The roof and wall
bracing carry the longitudinal actions and are designed separately, so the
transverse frame is a plane frame problem.  Out-of-plane freedoms are
restrained at every node rather than left floating, which keeps the
stiffness matrix non-singular without inventing a brace that is not there.

Geometry convention
-------------------
::

              ridge                      ridge
                /\\                        /\\
               /  \\                      /  \\
        eave  /    \\  eave        eave  /    \\  eave
             |      |                  |      |
             |      |                  |      |
    ---------+------+--------  or  ----+------+------+------+----
            base   base                                (multi-span)

    X -> horizontal, along the building width
    Y -> vertical, up
    Z  = 0 for every node; the frame is planar

Units
-----
This module's public API speaks the package convention: **kN, kN.m, mm**.
PyNite is driven internally in **N and mm**, so E in MPa is consistent with
no scaling.  The conversion happens in exactly two places, marked below.

Sign convention on results
--------------------------
``P`` is positive in COMPRESSION, matching :class:`MemberForces` in
``spec.py`` and the AISC chapter it is most often handed to.

PyNite uses the same convention - verified empirically, not assumed: a
3 m member with a 10 kN downward load at its top returns ``axial() =
+10000``.  The first version of this module flipped the sign on the way out
because the docstring-level guess was that PyNite reported tension
positive.  Every column then came back in tension under gravity.  No
conversion is applied; the check that caught it is
``test_axial_sign_is_compression_positive``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Sequence

from ..sections import SectionError, TaperedMember, WeldedI
from ..spec import (
    E_STEEL,
    FrameType,
    MemberForces,
    MemberType,
    PEBGeometry,
    RoofType,
)

__all__ = [
    "VERIFIED",
    "AnalysisError",
    "BaseFixity",
    "LoadDirection",
    "MemberSectionSpec",
    "FrameSections",
    "ModelSettings",
    "FrameMember",
    "FrameLayout",
    "StationForces",
    "Envelope",
    "FrameAnalysis",
    "build_layout",
]


class AnalysisError(RuntimeError):
    """The model could not be built or solved."""


# ---------------------------------------------------------------------------
# Verification gate
# ---------------------------------------------------------------------------

#: False until matched by an INDEPENDENT source.
#:
#: CLOSED 2026-09-01.  Three independent references, none of them this
#: module: hand statics, the ``anastruct`` 1.7.0 solver (different author,
#: different formulation), and the AISC 360-16 Eq. A-8-6 story amplifier.
#:
#:     prismatic_statics      w*B and moment equilibrium, exact to 1e-9
#:     portal_frame_forces    vs anastruct, pinned AND fixed base, on peak
#:                            column moment, peak rafter moment, base
#:                            reaction and eave thrust: worst 0.00001%
#:     taper_convergence      prismatic result independent of segment count
#:                            to 1e-9; tapered result converged to within
#:                            1% at the default 10% taper step
#:     p_delta_amplification  second-order / first-order column moment vs
#:                            B2 = 1/(1 - Pstory/Pe_story), within 5%
#:     layout_geometry        node coordinates against the geometry closed
#:                            forms in spec.py, which are themselves checked
#:                            by hand in test_spec.py
#:
#: The anastruct comparison earned its keep: the first run was low by
#: exactly 0.4963% on all eight quantities, which is 1 - cos(atan(0.10)) and
#: nothing else.  That is what identified the projected-versus-along-member
#: load ambiguity now split into two LoadDirection members.
VERIFIED: dict[str, bool] = {
    "layout_geometry": True,
    "prismatic_statics": True,
    "portal_frame_forces": True,
    "taper_convergence": True,
    "p_delta_amplification": True,
}

#: N per kN.  Applied on the way into PyNite and out of it - nowhere else.
_N_PER_KN = 1000.0
#: mm per m.  Line loads arrive in kN/m and must become N/mm.
_MM_PER_M = 1000.0

#: Shear modulus of steel, MPa.  G = E / (2(1+nu)) with nu = 0.30.
_NU = 0.30
_G_STEEL = E_STEEL / (2.0 * (1.0 + _NU))
#: Mass density in t/mm^3 so that PyNite's self-weight comes out in N.
_RHO_STEEL = 7.85e-9


class BaseFixity(str, Enum):
    """Column base restraint.

    A PEB column base is normally detailed as pinned - two anchor rods on the
    column centreline resist no meaningful moment.  FIXED is offered for the
    cases where four rods and a thick plate are actually provided, but it is
    never assumed: the caller states it.
    """

    PINNED = "pinned"
    FIXED = "fixed"


class LoadDirection(str, Enum):
    """How a line load is oriented, and what its metre is measured along.

    The second half of that sentence is the part that goes wrong.  On a
    sloping rafter, "3 kN/m" is ambiguous until you say per metre of WHAT,
    and the two readings differ by ``1/cos(theta)`` - about 0.5% at 1:10,
    but 10% at 1:2.5.  So the reading is in the name.

    GLOBAL_Y
        Vertical, per metre measured ALONG THE MEMBER.  Self weight, and
        roofing or lining dead load, which is a mass per square metre of
        sloping surface.
    GLOBAL_Y_PROJECTED
        Vertical, per metre of HORIZONTAL PROJECTION.  Roof live load and
        snow, which ASCE 7 defines on the horizontal projected area, and
        anything derived from a plan tributary width.
    GLOBAL_X
        Horizontal, per metre along the member.  Notional loads.
    NORMAL
        Perpendicular to the member axis, per metre along the member.  Wind
        pressure and suction, which act on the actual sloping surface.
    AXIAL
        Along the member axis, per metre along the member.  Drag.
    """

    GLOBAL_Y = "global_Y"
    GLOBAL_Y_PROJECTED = "global_Y_projected"
    GLOBAL_X = "global_X"
    NORMAL = "normal"
    AXIAL = "axial"


# ---------------------------------------------------------------------------
# Section assignment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberSectionSpec:
    """Plate sizes at the two ends of a member, before its length is known.

    The builder knows the geometry and therefore the length; the engineer
    knows the plates.  Keeping them apart means a section can be reused on
    members of different length without carrying a stale length with it.
    """

    start: WeldedI
    end: WeldedI

    @classmethod
    def prismatic(cls, section: WeldedI) -> "MemberSectionSpec":
        return cls(start=section, end=section)

    def to_member(self, length: float) -> TaperedMember:
        return TaperedMember(start=self.start, end=self.end, length=length)


@dataclass(frozen=True)
class FrameSections:
    """The plate build-ups for one frame line.

    ``column`` runs base -> eave, ``rafter`` runs eave -> ridge.  Both are
    given in that order, so a haunched rafter has its DEEP end first.
    """

    column: MemberSectionSpec
    rafter: MemberSectionSpec
    interior_column: MemberSectionSpec | None = None


@dataclass(frozen=True)
class ModelSettings:
    """Modelling choices that change the answer, stated rather than buried."""

    base_fixity: BaseFixity = BaseFixity.PINNED
    #: An interior column in a multi-span frame is normally pin-topped, so it
    #: carries gravity without attracting rafter moment.  Set False only when
    #: the connection is genuinely moment-resisting.
    interior_column_pinned_top: bool = True
    #: Maximum depth change within one prismatic slice, as a fraction.
    #: 0.10 puts several segments in a haunch and one at a flat apex.
    max_taper_step: float = 0.10
    #: Result stations sampled per design member for the envelope.
    stations_per_member: int = 21
    #: PyNite P-Delta iteration cap.
    p_delta_max_iter: int = 30

    def problems(self) -> list[str]:
        out = []
        if not (0.0 < self.max_taper_step <= 1.0):
            out.append("max_taper_step must lie in (0, 1]")
        if self.stations_per_member < 3:
            out.append("stations_per_member must be at least 3")
        return out


# ---------------------------------------------------------------------------
# Layout - pure geometry, no solver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameMember:
    """One DESIGN member: a column leg or one rafter half.

    This is the unit the engineer thinks in and the unit the design layer
    checks.  It is subdivided into prismatic segments for the solver, but
    those segments never leave this module.
    """

    mark: str
    member_type: MemberType
    taper: TaperedMember
    i_point: tuple[float, float]
    j_point: tuple[float, float]

    @property
    def length(self) -> float:
        return self.taper.length

    @property
    def angle(self) -> float:
        """Inclination from horizontal, radians."""
        dx = self.j_point[0] - self.i_point[0]
        dy = self.j_point[1] - self.i_point[1]
        return math.atan2(dy, dx)

    @property
    def horizontal_projection(self) -> float:
        return abs(self.j_point[0] - self.i_point[0])


@dataclass(frozen=True)
class FrameLayout:
    """Node and member arrangement for one frame line."""

    members: tuple[FrameMember, ...]
    nodes: dict[str, tuple[float, float]]
    base_nodes: tuple[str, ...]
    eave_nodes: tuple[str, ...]
    ridge_nodes: tuple[str, ...]
    geometry: PEBGeometry
    settings: ModelSettings

    def member(self, mark: str) -> FrameMember:
        for m in self.members:
            if m.mark == mark:
                return m
        raise KeyError(mark)

    @property
    def marks(self) -> tuple[str, ...]:
        return tuple(m.mark for m in self.members)

    @property
    def steel_weight(self) -> float:
        """Main-frame steel in this frame line, kg."""
        return sum(m.taper.weight for m in self.members)


def build_layout(
    geometry: PEBGeometry,
    sections: FrameSections,
    settings: ModelSettings | None = None,
) -> FrameLayout:
    """Lay out one transverse frame line.

    Marks follow the shop convention: ``C1``/``C2`` for the outer columns,
    ``IC1..`` for interior columns, ``R1``/``R2`` per span counted left to
    right.
    """
    settings = settings or ModelSettings()
    problems = settings.problems()
    if problems:
        raise AnalysisError("; ".join(problems))
    if geometry.roof_type is RoofType.MONOSLOPE and len(geometry.span_widths) != 1:
        raise AnalysisError("a monoslope frame must have exactly one span")

    n_spans = len(geometry.span_widths)
    if n_spans > 1 and sections.interior_column is None:
        raise AnalysisError(
            f"{n_spans} spans need an interior_column section; none was given"
        )

    H = geometry.eave_height
    nodes: dict[str, tuple[float, float]] = {}
    members: list[FrameMember] = []
    base_nodes: list[str] = []
    eave_nodes: list[str] = []
    ridge_nodes: list[str] = []

    # Column lines at the cumulative span boundaries.
    x_lines = [0.0]
    for w in geometry.span_widths:
        x_lines.append(x_lines[-1] + w)

    for i, x in enumerate(x_lines):
        b, e = f"B{i + 1}", f"E{i + 1}"
        nodes[b] = (x, 0.0)
        nodes[e] = (x, H)
        base_nodes.append(b)
        eave_nodes.append(e)

        is_interior = 0 < i < len(x_lines) - 1
        spec = sections.interior_column if is_interior else sections.column
        assert spec is not None  # guarded above
        mark = f"IC{i}" if is_interior else f"C{1 if i == 0 else 2}"
        members.append(
            FrameMember(
                mark=mark,
                member_type=(
                    MemberType.INTERIOR_COLUMN if is_interior else MemberType.COLUMN
                ),
                taper=spec.to_member(H),
                i_point=nodes[b],
                j_point=nodes[e],
            )
        )

    # Rafters, span by span.
    for s, width in enumerate(geometry.span_widths):
        x_left = x_lines[s]
        x_right = x_lines[s + 1]

        if geometry.roof_type is RoofType.MONOSLOPE:
            # One rafter, eave to the high side.
            high = f"E{s + 2}"
            nodes[high] = (x_right, H + width * geometry.roof_slope)
            ridge_nodes.append(high)
            members.append(
                FrameMember(
                    mark=f"R{s + 1}",
                    member_type=MemberType.RAFTER,
                    taper=sections.rafter.to_member(
                        math.hypot(width, width * geometry.roof_slope)
                    ),
                    i_point=nodes[f"E{s + 1}"],
                    j_point=nodes[high],
                )
            )
            continue

        # Gable: a ridge over the centre of the span, two rafters to it.
        x_ridge = 0.5 * (x_left + x_right)
        rise = 0.5 * width * geometry.roof_slope
        ridge = f"RG{s + 1}"
        nodes[ridge] = (x_ridge, H + rise)
        ridge_nodes.append(ridge)

        sloped = math.hypot(0.5 * width, rise)
        members.append(
            FrameMember(
                mark=f"R{2 * s + 1}",
                member_type=MemberType.RAFTER,
                taper=sections.rafter.to_member(sloped),
                i_point=nodes[f"E{s + 1}"],
                j_point=nodes[ridge],
            )
        )
        members.append(
            FrameMember(
                mark=f"R{2 * s + 2}",
                member_type=MemberType.RAFTER,
                taper=sections.rafter.to_member(sloped),
                i_point=nodes[f"E{s + 2}"],
                j_point=nodes[ridge],
            )
        )

    return FrameLayout(
        members=tuple(members),
        nodes=nodes,
        base_nodes=tuple(base_nodes),
        eave_nodes=tuple(eave_nodes),
        ridge_nodes=tuple(ridge_nodes),
        geometry=geometry,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StationForces:
    """Forces at one station of one design member, kN and kN.m."""

    x: float          # mm from the member's i end
    P: float          # positive COMPRESSION
    V: float          # in-plane shear
    M: float          # in-plane moment

    def to_member_forces(self) -> MemberForces:
        return MemberForces(P=self.P, Vy=self.V, Mz=self.M, station=self.x)


@dataclass(frozen=True)
class Envelope:
    """Per-member, per-combination station results.

    ``stations[mark][combo]`` is the ordered tuple of stations along that
    member.  The convenience properties collapse it to the governing values
    the design layer asks for.
    """

    stations: dict[str, dict[str, tuple[StationForces, ...]]]
    reactions: dict[str, dict[str, tuple[float, float, float]]]
    #: ``displacements[node][combo] = (dx, dy)`` in mm.  Needed for the
    #: serviceability drift check and for the AISC B2 story amplifier.
    displacements: dict[str, dict[str, tuple[float, float]]]
    combos: tuple[str, ...]
    p_delta: bool

    def governing(self, mark: str) -> tuple[str, StationForces]:
        """Combination and station with the largest in-plane moment."""
        best: tuple[str, StationForces] | None = None
        for combo, sts in self.stations[mark].items():
            for st in sts:
                if best is None or abs(st.M) > abs(best[1].M):
                    best = (combo, st)
        if best is None:
            raise AnalysisError(f"no results for member {mark}")
        return best

    def max_abs(self, mark: str, attr: str) -> float:
        return max(
            abs(getattr(st, attr))
            for sts in self.stations[mark].values()
            for st in sts
        )

    def base_reaction(self, node: str, combo: str) -> tuple[float, float, float]:
        """(Fx, Fy, Mz) at a base node, kN and kN.m."""
        return self.reactions[node][combo]

    def eave_drift(self, combo: str) -> float:
        """Largest absolute horizontal movement of any eave node, mm."""
        eaves = [n for n in self.displacements if n.startswith("E")]
        if not eaves:
            return 0.0
        return max(abs(self.displacements[n][combo][0]) for n in eaves)

    @property
    def max_uplift(self) -> float:
        """Largest net upward base reaction across all bases and combos, kN.

        Positive means the base is being pulled up - the number the holding
        down bolts are sized for.
        """
        worst = 0.0
        for per_combo in self.reactions.values():
            for _, (_, fy, _) in per_combo.items():
                worst = min(worst, fy)
        return -worst


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


class FrameAnalysis:
    """Builds and solves one frame line.

    Usage::

        fa = FrameAnalysis(layout)
        fa.add_line_load("D", "R1", 2.4, LoadDirection.GLOBAL_Y)
        fa.add_combination("1.2D+1.6Lr", {"D": 1.2, "Lr": 1.6})
        env = fa.solve(p_delta=True)
    """

    def __init__(self, layout: FrameLayout) -> None:
        self.layout = layout
        self.settings = layout.settings
        self._loads: list[tuple[str, str, float, float, LoadDirection]] = []
        self._combos: dict[str, dict[str, float]] = {}
        self._segments: dict[str, list[tuple[str, float, float]]] = {}
        self._model = None

    @property
    def model(self):
        """The PyNite model, or None before :meth:`build`.

        Read-only on purpose.  Post-processing needs the solved model to
        ask for a deflected shape - the ``Envelope`` keeps forces, not the
        member deflection functions - but nothing outside this class may
        add a load or a node to it after the fact.
        """
        return self._model

    @property
    def loads(self):
        """``[(case, mark, w_i, w_j, direction), ...]`` as applied.

        Read-only, and a copy - the same contract ``segments`` has. It
        exists so the 3D model can replay EXACTLY the loads this frame was
        solved for rather than re-deriving them: two derivations of one
        load set is two chances to get it wrong, and the whole point of the
        3D model is that it must agree with this one.
        """
        return [tuple(t) for t in self._loads]

    @property
    def combinations(self):
        """``{name: {case: factor}}`` as added.  A copy, for the same
        reason as :attr:`loads`."""
        return {k: dict(v) for k, v in self._combos.items()}

    @property
    def segments(self):
        """``{design mark: [(solver member, x_start, x_end), ...]}``.

        The map from the member an engineer names to the prismatic slices
        the solver was actually given.
        """
        return {k: list(v) for k, v in self._segments.items()}

    # -- loads ---------------------------------------------------------------

    def add_line_load(
        self,
        case: str,
        mark: str,
        w: float,
        direction: LoadDirection = LoadDirection.GLOBAL_Y,
        w_end: float | None = None,
    ) -> None:
        """Apply a uniform or linearly varying line load, kN/m.

        Sign: for GLOBAL_Y a POSITIVE ``w`` acts downward, because that is
        how a gravity load is written on a drawing.  The sign is flipped once
        when it reaches the solver.  For NORMAL a positive ``w`` presses onto
        the member's top face.
        """
        if mark not in self.layout.marks:
            raise AnalysisError(f"unknown member {mark!r}")
        self._loads.append((case, mark, w, w if w_end is None else w_end, direction))

    def add_combination(self, name: str, factors: Mapping[str, float]) -> None:
        if not factors:
            raise AnalysisError(f"combination {name!r} has no factors")
        self._combos[name] = dict(factors)

    @property
    def cases(self) -> tuple[str, ...]:
        seen: list[str] = []
        for case, *_ in self._loads:
            if case not in seen:
                seen.append(case)
        return tuple(seen)

    # -- model ---------------------------------------------------------------

    def build(self):
        """Assemble the PyNite model.  Idempotent within one instance."""
        try:
            from Pynite import FEModel3D
        except ImportError as exc:  # pragma: no cover - environment issue
            raise AnalysisError(
                "PyNiteFEA is required for frame analysis. "
                "Install it with: pip install PyNiteFEA"
            ) from exc

        model = FEModel3D()
        model.add_material("steel", E_STEEL, _G_STEEL, _NU, _RHO_STEEL)

        # Nodes: every layout node, plus one interior node per segment break.
        for name, (x, y) in self.layout.nodes.items():
            model.add_node(name, x, y, 0.0)

        self._segments = {}
        for fm in self.layout.members:
            n = fm.taper.suggested_segments(
                max_depth_change=self.settings.max_taper_step
            )
            segs = fm.taper.discretise(n)
            xi, yi = fm.i_point
            xj, yj = fm.j_point
            dx, dy = (xj - xi) / fm.length, (yj - yi) / fm.length

            i_name = self._node_name_at(fm.i_point)
            entries: list[tuple[str, float, float]] = []
            prev = i_name
            for k, seg in enumerate(segs):
                last = k == len(segs) - 1
                if last:
                    nxt = self._node_name_at(fm.j_point)
                else:
                    nxt = f"{fm.mark}_n{k + 1}"
                    model.add_node(
                        nxt, xi + dx * seg.x_end, yi + dy * seg.x_end, 0.0
                    )

                sec_name = f"{fm.mark}_s{k}"
                s = seg.section
                model.add_section(sec_name, s.A, s.Iy, s.Ix, s.J)
                mem_name = f"{fm.mark}_{k}"
                model.add_member(mem_name, prev, nxt, "steel", sec_name)
                entries.append((mem_name, seg.x_start, seg.x_end))
                prev = nxt
            self._segments[fm.mark] = entries

        self._apply_supports(model)
        self._apply_releases(model)
        self._apply_loads(model)

        for name, factors in self._combos.items():
            model.add_load_combo(name, dict(factors))

        self._model = model
        return model

    def _node_name_at(self, point: tuple[float, float]) -> str:
        for name, p in self.layout.nodes.items():
            if math.isclose(p[0], point[0], abs_tol=1e-6) and math.isclose(
                p[1], point[1], abs_tol=1e-6
            ):
                return name
        raise AnalysisError(f"no layout node at {point}")

    def _apply_supports(self, model) -> None:
        fixed = self.settings.base_fixity is BaseFixity.FIXED
        for b in self.layout.base_nodes:
            model.def_support(
                b,
                support_DX=True,
                support_DY=True,
                support_DZ=True,
                support_RX=True,
                support_RY=True,
                support_RZ=fixed,
            )
        # Hold the frame in its own plane: restrain out-of-plane translation
        # and the two out-of-plane rotations at every free node.  This is a
        # statement that the frame is planar, not a brace - it adds no
        # in-plane stiffness.
        for name in model.nodes:
            if name in self.layout.base_nodes:
                continue
            model.def_support(
                name, support_DZ=True, support_RX=True, support_RY=True
            )

    def _apply_releases(self, model) -> None:
        if not self.settings.interior_column_pinned_top:
            return
        for fm in self.layout.members:
            if fm.member_type is not MemberType.INTERIOR_COLUMN:
                continue
            last_mem = self._segments[fm.mark][-1][0]
            model.def_releases(last_mem, Rzj=True)

    def _apply_loads(self, model) -> None:
        for case, mark, w_i, w_j, direction in self._loads:
            fm = self.layout.member(mark)
            for mem_name, x0, x1 in self._segments[mark]:
                f0 = x0 / fm.length
                f1 = x1 / fm.length
                w0 = w_i + (w_j - w_i) * f0
                w1 = w_i + (w_j - w_i) * f1
                self._add_segment_load(model, mem_name, fm, w0, w1, direction, case)

    @staticmethod
    def _add_segment_load(
        model, mem_name: str, fm: FrameMember, w0: float, w1: float,
        direction: LoadDirection, case: str,
    ) -> None:
        """kN/m -> N/mm, and pick PyNite's direction code.

        This and :meth:`_extract` are the only two places units change.
        """
        # kN/m -> N/mm is a factor of exactly 1.0 (1000 N / 1000 mm), but it
        # is written out so the cancellation is visible and cannot be
        # "simplified" into a bug later.
        scale = _N_PER_KN / _MM_PER_M

        if direction is LoadDirection.GLOBAL_Y:
            # Positive w means downward on a drawing; global Y is up.
            model.add_member_dist_load(
                mem_name, "FY", -w0 * scale, -w1 * scale, case=case
            )
        elif direction is LoadDirection.GLOBAL_Y_PROJECTED:
            # PyNite applies a global-direction distributed load per unit
            # length ALONG the member.  A load defined per metre of
            # horizontal projection therefore has to be scaled by
            # cos(theta) = horizontal / sloped, so that
            #     w_proj * horizontal_projection  ==  w_pynite * sloped_length
            # Caught by a statics check: without this factor a 1:10 rafter
            # picked up 0.5% too much load, and a 1:2.5 rafter would pick up
            # 8%.
            cos_theta = fm.horizontal_projection / fm.length
            model.add_member_dist_load(
                mem_name,
                "FY",
                -w0 * scale * cos_theta,
                -w1 * scale * cos_theta,
                case=case,
            )
        elif direction is LoadDirection.GLOBAL_X:
            model.add_member_dist_load(
                mem_name, "FX", w0 * scale, w1 * scale, case=case
            )
        elif direction is LoadDirection.NORMAL:
            # Local y is the in-plane transverse axis; positive pressure
            # pushes against the member's top face, hence the sign flip.
            model.add_member_dist_load(
                mem_name, "Fy", -w0 * scale, -w1 * scale, case=case
            )
        elif direction is LoadDirection.AXIAL:
            model.add_member_dist_load(
                mem_name, "Fx", w0 * scale, w1 * scale, case=case
            )
        else:  # pragma: no cover - enum is exhaustive
            raise AnalysisError(f"unhandled direction {direction}")

    # -- solve ---------------------------------------------------------------

    def solve(self, *, p_delta: bool = True, check_statics: bool = False) -> Envelope:
        """Run every combination and return the envelope.

        ``p_delta=True`` runs PyNite's second-order solution.  It is the
        default because a PEB portal frame is slender and sways: the
        first-order answer understates the column moment, and AISC 360-16
        Chapter C requires second-order effects to be accounted for one way
        or another.
        """
        if not self._combos:
            raise AnalysisError("no load combinations were added")
        model = self.build()

        try:
            if p_delta:
                model.analyze_PDelta(
                    log=False, max_iter=self.settings.p_delta_max_iter
                )
            else:
                model.analyze(log=False, check_statics=check_statics)
        except Exception as exc:
            raise AnalysisError(f"the solver failed: {exc}") from exc

        return self._extract(model, p_delta)

    def _extract(self, model, p_delta: bool) -> Envelope:
        """PyNite results -> kN, kN.m.  The second and last unit boundary."""
        n = self.settings.stations_per_member
        stations: dict[str, dict[str, tuple[StationForces, ...]]] = {}

        for fm in self.layout.members:
            per_combo: dict[str, tuple[StationForces, ...]] = {}
            for combo in self._combos:
                out: list[StationForces] = []
                for mem_name, x0, x1 in self._segments[fm.mark]:
                    mem = model.members[mem_name]
                    # PyNite exposes member length as a method, not a value.
                    seg_L = mem.L() if callable(mem.L) else mem.L
                    k = max(3, math.ceil(n * (x1 - x0) / fm.length))
                    for i in range(k):
                        xl = seg_L * i / (k - 1)
                        out.append(
                            StationForces(
                                x=x0 + xl,
                                # PyNite already reports compression
                                # positive - see the module docstring.
                                P=mem.axial(xl, combo) / _N_PER_KN,
                                V=mem.shear("Fy", xl, combo) / _N_PER_KN,
                                M=mem.moment("Mz", xl, combo)
                                / (_N_PER_KN * _MM_PER_M),
                            )
                        )
                per_combo[combo] = tuple(sorted(out, key=lambda s: s.x))
            stations[fm.mark] = per_combo

        reactions: dict[str, dict[str, tuple[float, float, float]]] = {}
        for b in self.layout.base_nodes:
            node = model.nodes[b]
            reactions[b] = {
                combo: (
                    node.RxnFX[combo] / _N_PER_KN,
                    node.RxnFY[combo] / _N_PER_KN,
                    node.RxnMZ[combo] / (_N_PER_KN * _MM_PER_M),
                )
                for combo in self._combos
            }

        displacements: dict[str, dict[str, tuple[float, float]]] = {}
        for name in self.layout.nodes:
            node = model.nodes[name]
            displacements[name] = {
                combo: (node.DX[combo], node.DY[combo]) for combo in self._combos
            }

        return Envelope(
            stations=stations,
            reactions=reactions,
            displacements=displacements,
            combos=tuple(self._combos),
            p_delta=p_delta,
        )
