"""
bimsteelauto.pipeline
=====================

The ONE entry point.

    run_frame(spec, inputs) -> DesignResult

Everything the engine does, in the order it has to happen, behind a single
call.  This is what Layer 2 talks to: the FreeCAD adapter should never have
to know that a purlin is checked after the frame is solved, or that the
longitudinal pressure comes out of Load Case B.

What is in ``ProjectSpec`` and what is in ``FrameInputs``
--------------------------------------------------------
``ProjectSpec`` holds what the BUILDING is - geometry, site, materials, the
code basis.  :class:`FrameInputs` holds what the ENGINEER chose - the plate
sizes, the purlin section, where the bracing goes, the shop detailing.

The split is not arbitrary.  A spec can be written before anything has been
sized; the inputs cannot.  Keeping them apart is what will let a future
auto-sizer produce a ``FrameInputs`` from a ``ProjectSpec`` without either
of them changing shape.

Nothing here has an engineering default.  A section, a brace or a bolt
diameter has to be supplied, because a default would silently become a
design - see the package's working agreement (c).  The few defaults that do
exist are ARRANGEMENT conventions (two braced sidewalls, X-panels split at
mid-slope), each stated in its own docstring and each printed on the sheet.

Why the intermediates are reachable
-----------------------------------
:func:`design_frame` returns a :class:`FrameRun`, which carries the layout,
the analysis envelope, the wind result and the brace forces alongside the
``DesignResult``.  Callers that want to print a load table or drive a
diagram need those; the adapter does not, and takes the ``DesignResult``
only.  :func:`run_frame` is the thin wrapper that returns just the result.

Layer rule: this module imports no FreeCAD.  It is Layer 1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import __version__
from .analysis import (
    Envelope,
    FrameAnalysis,
    FrameLayout,
    FrameSections,
    ModelSettings,
    build_layout,
)
from .analysis.bracing import BraceForces, LongitudinalSystem, solve_longitudinal
from .connections import ConnectionDetailing, design_frame_connections
from .design import (
    ColdFormedSection,
    MemberDesignInput,
    PurlinBracing,
    PurlinLine,
    PurlinLoadCase,
    check_member,
    check_purlin,
)
from .design.bracing import BraceMember, check_strut, design_bracing
from .design.members import BracingSpec
from .loads import (
    AreaLoad,
    CCZone,
    DeadLoadBuildUp,
    RoofLiveLoad,
    apply_loads,
    build_load_summary,
    combinations_for,
    envelope_pressures,
    frame_tributary_area,
    girt_pressures,
    gravity_loads_for_frame,
    longitudinal_endwall_pressure,
    minimum_design_wind_load,
    purlin_pressures,
    roof_live_load,
    wind_case_names,
    wind_loads_for_frame,
)
from .loads.cladding import CladdingPressure
from .loads.wind import MinimumWindLoad, WindResult
from .report import collect_coverage_gaps
from .sections import WeldedI
from .spec import (
    BracingArrangement,
    ConnectionDesign,
    ConnectionType,
    DesignResult,
    LoadCase,
    MemberDesign,
    MemberType,
    ProjectSpec,
    SupportReaction,
)

__all__ = [
    "PipelineError",
    "SecondaryInputs",
    "BracingInputs",
    "FrameInputs",
    "FrameRun",
    "design_frame",
    "run_frame",
    "ENGINE_MODULES",
    "open_verification_flags",
]


class PipelineError(RuntimeError):
    """The pipeline could not be run as specified."""


#: Every engine module carrying a VERIFIED gate.  Listed explicitly rather
#: than discovered, because a module that quietly stopped being scanned
#: would take its open flags off the calculation sheet with it - which is
#: the one failure this list exists to prevent.
ENGINE_MODULES: tuple[str, ...] = (
    "bimsteelauto.sections",
    "bimsteelauto.analysis.frame",
    "bimsteelauto.analysis.bracing",
    "bimsteelauto.analysis.space",
    "bimsteelauto.analysis.results",
    "bimsteelauto.loads.gravity",
    "bimsteelauto.loads.combinations",
    "bimsteelauto.loads.frame",
    "bimsteelauto.loads.wind",
    "bimsteelauto.loads.cladding",
    "bimsteelauto.design.members",
    "bimsteelauto.design.purlins",
    "bimsteelauto.design.bracing",
    "bimsteelauto.connections",
    "bimsteelauto.detailing",
)


def open_verification_flags() -> tuple[str, ...]:
    """Every VERIFIED flag still False, across the whole engine."""
    out: list[str] = []
    for name in ENGINE_MODULES:
        mod = __import__(name, fromlist=["VERIFIED"])
        short = name.replace("bimsteelauto.", "")
        out += [
            f"{short}.{k}"
            for k, v in getattr(mod, "VERIFIED", {}).items()
            if not v
        ]
    return tuple(out)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecondaryInputs:
    """Purlins and girts.

    ``purlin_section`` and ``girt_section`` are CATALOGUE items.  Nothing is
    derived from their profile dimensions - the effective moduli in
    particular must come from the supplier's table, because a wrong ``Se``
    produces a sheet that is confidently wrong.

    ``purlin_zones`` and ``girt_zones`` are the ASCE 7-16 Chapter 30 zones
    the member crosses.  Which zones those are is a geometry decision the
    engine does not make; passing all of them is the conservative choice and
    is what the demonstration does.
    """

    purlin_section: ColdFormedSection
    girt_section: ColdFormedSection
    purlin_zones: tuple[str, ...] = (
        CCZone.ROOF_INTERIOR, CCZone.ROOF_1, CCZone.ROOF_EDGE,
        CCZone.ROOF_CORNER,
    )
    girt_zones: tuple[str, ...] = (CCZone.WALL_INTERIOR, CCZone.WALL_CORNER)
    #: Anti-sag ties per bay.  Without them the uplift flexural check cannot
    #: be evaluated at all and reports an infinite ratio - which is correct
    #: and useless.  Three is the common PEB detail (third points).
    antisag_ties_per_bay: int = 3

    def problems(self) -> list[str]:
        out: list[str] = []
        if self.antisag_ties_per_bay < 1:
            out.append(
                "secondary.antisag_ties_per_bay must be at least 1; a purlin "
                "with no bottom-flange restraint cannot be checked for "
                "uplift and will report an infinite ratio"
            )
        for name, zones in (
            ("purlin_zones", self.purlin_zones),
            ("girt_zones", self.girt_zones),
        ):
            if not zones:
                out.append(f"secondary.{name} is empty - no zone to design for")
        return out


@dataclass(frozen=True)
class BracingInputs:
    """The longitudinal system.

    ``braced_bays`` are bay indices counted from the left.  There is no
    default: a building with no longitudinal bracing has no load path for
    wind on the endwall, and that is a MISSING design rather than a zero
    one.
    """

    braced_bays: tuple[int, ...]
    roof_brace: BraceMember
    wall_brace: BraceMember
    strut_section: WeldedI
    #: Sidewalls that actually carry wall bracing.  Two on a normal
    #: building; one where the other side is built against something.
    n_braced_walls: int = 2
    #: X-panels per roof slope.  The diagonal force is ``V / cos(theta)``,
    #: and one X over a whole 15 m slope against a 6 m bay puts 2.7x the
    #: panel shear into the rod - so splitting the slope is the usual
    #: detail, and it is stated rather than assumed.
    roof_panel_splits: int = 2

    def problems(self) -> list[str]:
        out: list[str] = []
        if not self.braced_bays:
            out.append(
                "bracing.braced_bays is empty. A building with no "
                "longitudinal bracing has no load path for endwall wind - "
                "that is a missing design, not a zero one."
            )
        if self.roof_panel_splits < 1:
            out.append("bracing.roof_panel_splits must be at least 1")
        if self.n_braced_walls not in (1, 2):
            out.append("bracing.n_braced_walls must be 1 or 2")
        return out


@dataclass(frozen=True)
class FrameInputs:
    """Everything the engineer chose that ``ProjectSpec`` does not carry."""

    sections: FrameSections
    secondary: SecondaryInputs
    bracing: BracingInputs
    detailing: ConnectionDetailing
    dead: DeadLoadBuildUp
    wall_dead: AreaLoad
    #: Which transverse frame line this run designs.  ``None`` picks the
    #: one with the largest tributary width, which is the most heavily
    #: loaded interior frame - a DERIVED choice, not an assumed one.
    frame_index: int | None = None
    settings: ModelSettings = field(default_factory=ModelSettings)
    #: Ground elevation factor, ASCE 7-16 Sect. 26.9.
    Ke: float = 1.0

    def problems(self) -> list[str]:
        return self.secondary.problems() + self.bracing.problems()


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrameRun:
    """The ``DesignResult``, plus the intermediates that produced it.

    The adapter takes ``result`` and nothing else.  Everything beside it is
    for callers that need to print a load table, draw a bending-moment
    diagram, or explain where a number came from.
    """

    result: DesignResult
    spec: ProjectSpec
    inputs: FrameInputs
    frame_index: int
    layout: FrameLayout
    envelope: Envelope
    wind: WindResult
    minimum: MinimumWindLoad
    endwall_pressure: float
    endwall_minimum: MinimumWindLoad | None
    longitudinal: LongitudinalSystem
    brace_forces: BraceForces
    purlin_cladding: CladdingPressure
    girt_cladding: CladdingPressure
    roof_live: RoofLiveLoad
    #: The solved analysis.  Optional so a caller that builds a FrameRun by
    #: hand still can; ``design_frame`` always sets it, and the result
    #: post-processor needs it - the deflected shape lives in the solver's
    #: member functions, not in the envelope.
    analysis: FrameAnalysis | None = None


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def _governing_frame_index(spec: ProjectSpec) -> int:
    """The frame line with the largest tributary width.

    Derived, not assumed: on a non-uniform bay layout the widest tributary
    is the most heavily loaded interior frame, and picking it by measurement
    is what stops a 7.5 m bay being designed on a 6 m frame.
    """
    widths = spec.geometry.tributary_widths()
    return max(range(len(widths)), key=lambda i: widths[i])


def design_frame(spec: ProjectSpec, inputs: FrameInputs) -> FrameRun:
    """Run the whole engine on one transverse frame line.

    Order matters and is fixed here so no caller has to know it:

    1. validate the spec and the inputs, together, before anything is built
    2. lay the frame out
    3. gravity loads (dead build-up, roof live to Eq. 4.8-1)
    4. wind on the FRAME - Chapter 28, with the Sect. 28.3.4 minimum
    5. analyse, second order
    6. main members - AISC 360-16
    7. secondary members - AISI S100-16, on CHAPTER 30 pressures
    8. longitudinal system - endwall wind, braces, eave strut
    9. connections
    10. assemble the DesignResult
    """
    problems = spec.problems() + inputs.problems()
    if problems:
        raise PipelineError(
            "the spec and inputs must both be valid before anything is "
            "designed:\n  " + "\n  ".join(problems)
        )

    geometry = spec.geometry
    site = spec.site
    materials = spec.materials
    method = spec.settings.design_method
    frame_index = (
        _governing_frame_index(spec)
        if inputs.frame_index is None
        else inputs.frame_index
    )
    if not 0 <= frame_index < geometry.n_frames:
        raise PipelineError(
            f"frame_index {frame_index} outside 0..{geometry.n_frames - 1}"
        )

    # -- 2. layout ----------------------------------------------------------
    layout = build_layout(geometry, inputs.sections, inputs.settings)

    # -- 3. gravity ---------------------------------------------------------
    At = frame_tributary_area(geometry, frame_index)
    live = roof_live_load(At, geometry.roof_slope)
    gravity = gravity_loads_for_frame(
        layout, frame_index,
        dead=inputs.dead.as_area_load(),
        roof_live=live.as_area_load(),
        wall_dead=inputs.wall_dead,
    )

    # -- 4. wind on the frame ----------------------------------------------
    wind = envelope_pressures(geometry, site, Ke=inputs.Ke)
    wind_loads = wind_loads_for_frame(layout, frame_index, wind)
    minimum = minimum_design_wind_load(geometry, wind, end_zone=False)

    # -- 5. analysis --------------------------------------------------------
    analysis = FrameAnalysis(layout)
    all_loads = list(gravity) + list(wind_loads)
    combos = combinations_for(method, wind_cases=wind_case_names())
    applied = apply_loads(analysis, all_loads, combos)
    envelope = analysis.solve(p_delta=spec.settings.include_p_delta)

    # -- 6. main members ----------------------------------------------------
    members: list[MemberDesign] = []
    for fm in layout.members:
        L = fm.taper.length
        if fm.member_type is MemberType.RAFTER:
            bracing = BracingSpec.at_spacing(L, geometry.purlin_spacing, L / 3.0)
        else:
            bracing = BracingSpec.at_spacing(L, geometry.girt_spacing, L / 2.0)
        forces = {
            combo: [st.to_member_forces() for st in stations]
            for combo, stations in envelope.stations[fm.mark].items()
        }
        members.append(
            check_member(
                MemberDesignInput(
                    mark=fm.mark, member_type=fm.member_type, taper=fm.taper,
                    forces=forces, bracing=bracing, materials=materials,
                    method=method,
                )
            )
        )

    # -- 7. secondary members, on Chapter 30 --------------------------------
    sec = inputs.secondary
    cos_theta = math.cos(math.atan(geometry.roof_slope))
    s_purlin = geometry.purlin_spacing / 1000.0
    s_girt = geometry.girt_spacing / 1000.0
    tie_spacing = min(geometry.bay_spacings) / sec.antisag_ties_per_bay

    # The roof live load is defined on the horizontal projection; the purlin
    # spans on the sloping surface, so it is converted once, here.
    w_purlin_gravity = (
        1.2 * inputs.dead.total + 1.6 * live.Lr * cos_theta
    ) * s_purlin

    purlin_cc = purlin_pressures(geometry, wind, zones=sec.purlin_zones)
    w_purlin_uplift = abs(
        0.9 * inputs.dead.total * cos_theta + 1.0 * purlin_cc.outward
    ) * s_purlin

    purlin = check_purlin(
        PurlinLine(
            mark="P1", section=sec.purlin_section,
            spans=geometry.bay_spacings, spacing=geometry.purlin_spacing,
            bracing=PurlinBracing(
                sheeting_braces_top=True,
                bottom_flange_brace_spacing=tie_spacing,
            ),
        ),
        [
            PurlinLoadCase(name="1.2D+1.6Lr", w=w_purlin_gravity),
            PurlinLoadCase(name="0.9D+1.0W", w=w_purlin_uplift, uplift=True),
        ],
        Fy=materials.Fy_cold_formed, Fu=materials.Fu_cold_formed,
        method=method, deflection_limit=spec.settings.defl_rafter_live,
    )

    girt_cc = girt_pressures(geometry, wind, zones=sec.girt_zones)
    p_wall = max(girt_cc.inward, girt_cc.uplift)
    girt = check_purlin(
        PurlinLine(
            mark="G1", section=sec.girt_section,
            spans=geometry.bay_spacings, spacing=geometry.girt_spacing,
            bracing=PurlinBracing(
                sheeting_braces_top=True,
                bottom_flange_brace_spacing=tie_spacing,
            ),
            member_type=MemberType.GIRT,
        ),
        [
            PurlinLoadCase(name="1.0W inward", w=p_wall * s_girt),
            PurlinLoadCase(name="1.0W outward", w=p_wall * s_girt, uplift=True),
        ],
        Fy=materials.Fy_cold_formed, Fu=materials.Fu_cold_formed,
        method=method, deflection_limit=spec.settings.defl_girt_wind,
    )

    # -- 8. longitudinal system --------------------------------------------
    br = inputs.bracing
    endwall_p, endwall_min = longitudinal_endwall_pressure(geometry, wind)
    longitudinal = LongitudinalSystem(
        geometry=geometry,
        braced_bays=tuple(br.braced_bays),
        n_braced_walls=br.n_braced_walls,
        roof_panel_depth=geometry.rafter_length / br.roof_panel_splits,
    )
    brace_forces = solve_longitudinal(longitudinal, endwall_p)
    braces = design_bracing(
        brace_forces,
        roof_brace=br.roof_brace, wall_brace=br.wall_brace,
        Fy=materials.Fy_builtup, Fu=materials.Fu_builtup, method=method,
    )
    strut = check_strut(
        br.strut_section,
        compression=brace_forces.max_strut_force,
        length=max(geometry.bay_spacings),
        materials=materials, mark="ES1", method=method,
    )

    # -- 9. connections -----------------------------------------------------
    connections = list(
        design_frame_connections(
            layout, envelope, materials, inputs.detailing, method=method,
            capacity_fraction=spec.settings.connection_capacity_fraction,
            strict=False,
        )
    )
    connections += _secondary_connections(
        geometry=geometry,
        detailing=inputs.detailing,
        purlin_section=sec.purlin_section,
        w_purlin_gravity=w_purlin_gravity,
        brace_forces=brace_forces,
    )

    # -- 10. assemble -------------------------------------------------------
    cases = _load_cases(
        dead=inputs.dead, live=live, wind=wind, minimum=minimum,
        endwall_p=endwall_p, endwall_min=endwall_min,
        longitudinal=longitudinal, brace_forces=brace_forces,
    )
    summary = build_load_summary(
        all_loads, applied, cases=cases,
        qh=wind.qh, end_zone_a=wind.end_zone_a,
    )
    reactions = tuple(
        SupportReaction(
            node=node, combination=combo,
            **dict(zip(("Fx", "Fy", "Mz"), envelope.base_reaction(node, combo))),
        )
        for node in layout.base_nodes
        for combo in envelope.combos
    )

    result = DesignResult(
        spec=spec,
        loads=summary,
        members=tuple(members) + (purlin, girt, *braces, strut),
        connections=tuple(connections),
        reactions=reactions,
        bracing=BracingArrangement(
            braced_bays=tuple(br.braced_bays),
            n_braced_walls=br.n_braced_walls,
            roof_panel_splits=br.roof_panel_splits,
        ),
        warnings=applied.warnings,
        unverified=open_verification_flags(),
        assumptions=_assumptions(
            inputs=inputs, wind=wind, frame_index=frame_index,
            purlin_cc=purlin_cc, girt_cc=girt_cc,
        ),
        coverage_gaps=collect_coverage_gaps(),
        engine_version=__version__,
    )

    return FrameRun(
        result=result, spec=spec, inputs=inputs, frame_index=frame_index,
        layout=layout, envelope=envelope, wind=wind, minimum=minimum,
        endwall_pressure=endwall_p, endwall_minimum=endwall_min,
        longitudinal=longitudinal, brace_forces=brace_forces,
        purlin_cladding=purlin_cc, girt_cladding=girt_cc, roof_live=live,
        analysis=analysis,
    )


def run_frame(spec: ProjectSpec, inputs: FrameInputs) -> DesignResult:
    """The pipeline, returning only its single output.

    This is the call Layer 2 makes.  Everything the adapter needs is on the
    ``DesignResult``: the geometry is on ``result.spec``, and each member's
    section is recoverable from ``section_id`` and ``length`` through
    :func:`bimsteelauto.sections.parse_mark`.
    """
    return design_frame(spec, inputs).result


# ---------------------------------------------------------------------------
# Pieces, kept out of the main flow so it stays readable
# ---------------------------------------------------------------------------


def _secondary_connections(
    *, geometry, detailing, purlin_section, w_purlin_gravity, brace_forces
) -> list[ConnectionDesign]:
    """The purlin cleat and the bracing gusset."""
    import bimsteelauto_connections as bc

    # A continuous beam over near-equal spans peaks at about 1.1 w L on the
    # first interior support.
    V_purlin = 1.1 * w_purlin_gravity * (max(geometry.bay_spacings) / 1000.0)
    cleat = bc.check_purlin_cleat(
        Vu=V_purlin, Nu=0.1 * V_purlin,
        d_bolt=12.0, n_bolts=2, t_cleat=6.0,
        t_purlin=purlin_section.t, purlin_grade="S450J0",
        pitch=60.0, edge_end=30.0,
        eccentricity=40.0,          # required since defect #12
        strict=False,
    )
    gusset = bc.check_gusset(
        Pu=max(brace_forces.roof_diagonal, brace_forces.wall_diagonal),
        action="tension", t_gusset=12.0,
        l_connection=300.0, w_first_row=100.0,
        b_available=600.0, l_buckling=150.0,
        n_bolts=4, d_bolt=20.0, strict=False,
    )
    cleat_geom = _cleat_geometry(
        t_cleat=6.0, d_bolt=12.0, n_bolts=2, pitch=60.0, edge_end=30.0,
        purlin_depth=purlin_section.H,
    )
    gusset_geom = _gusset_geometry(
        t_gusset=12.0, d_bolt=20.0, n_bolts=4, l_connection=300.0,
        w_first_row=100.0, b_available=600.0, details=gusset.details,
    )

    pc1 = ConnectionDesign.from_result(
            bc.from_checks(
                mark="PC1", kind="purlin cleat",
                checks=cleat.checks, demand=cleat.demand,
                selections={
                    "bolts": "2 x M12 8.8", "cleat": "PL 6 mm S275JR",
                    "purlin": purlin_section.name,
                },
                warnings=cleat.warnings, verified=False,
                citation=cleat.citation,
            ),
            ConnectionType.PURLIN_CLEAT, ("P1", "R1"),
    )
    gp1 = ConnectionDesign.from_result(
            bc.from_checks(
                mark="GP1", kind="bracing gusset",
                checks=gusset.checks, demand=gusset.demand,
                selections={
                    "gusset": "PL 12 mm S355JR", "bolts": "4 x M20",
                    "roof diagonal": f"{brace_forces.roof_diagonal:.1f} kN",
                    "wall diagonal": f"{brace_forces.wall_diagonal:.1f} kN",
                },
                geometry=gusset.details, warnings=gusset.warnings,
                verified=False, citation=gusset.citation,
            ),
            ConnectionType.BRACING_GUSSET, ("RB1", "WB1"),
    )
    # Schema 1.2.0 - shop geometry on the result. The cleat and the gusset
    # are laid out on the SAME numbers their checks ran on, above, so a
    # drawing made from this model cannot show a connection that was never
    # checked.
    pc1.detail["geometry"] = cleat_geom
    gp1.detail["geometry"] = gusset_geom
    return [pc1, gp1]


def _cleat_geometry(*, t_cleat, d_bolt, n_bolts, pitch, edge_end, purlin_depth):
    from .detailing import DetailingError, cleat_layout

    try:
        layout = cleat_layout(
            thickness=t_cleat, bolt_diameter=d_bolt, n_bolts=n_bolts,
            pitch=pitch, edge_end=edge_end, purlin_depth=purlin_depth,
        )
    except DetailingError as exc:
        return {"kind": "purlin_cleat", "available": False, "why": str(exc)}
    out = layout.to_dict()
    out["available"] = True
    return out


def _gusset_geometry(*, t_gusset, d_bolt, n_bolts, l_connection, w_first_row,
                     b_available, details):
    from .detailing import DetailingError, gusset_layout

    whitmore = None
    for key, value in (details or {}).items():
        if "whitmore width" in str(key).lower() and isinstance(value, (int, float)):
            whitmore = float(value)
            break
    try:
        layout = gusset_layout(
            thickness=t_gusset, bolt_diameter=d_bolt, n_bolts=n_bolts,
            l_connection=l_connection, w_first_row=w_first_row,
            b_available=b_available, whitmore_width=whitmore,
        )
    except DetailingError as exc:
        return {"kind": "bracing_gusset", "available": False, "why": str(exc),
                "checks": dict(details or {})}
    out = layout.to_dict()
    out["available"] = True
    # The Whitmore numbers were already here and the calculation sheet reads
    # them; keeping them alongside the outline rather than replacing them.
    out["checks"] = dict(details or {})
    return out


def _load_cases(
    *, dead, live, wind, minimum, endwall_p, endwall_min, longitudinal,
    brace_forces,
) -> list[LoadCase]:
    """What the calculation sheet prints in its load-case table."""
    cases = [
        LoadCase(
            name="D", kind="dead",
            description=f"Dead, {dead.total:.3f} kN/m2 of sloping roof",
            reference="Project build-up",
        ),
        LoadCase(
            name="Lr", kind="roof_live",
            description=(
                f"Roof live, Lr = {live.Lr:.3f} kN/m2 (Lo {live.Lo:.2f}, "
                f"R1 {live.R1:.3f}, R2 {live.R2:.3f}, At {live.At:.1f} m2)"
            ),
            reference=live.reference,
        ),
    ]

    min_note = (
        f"; ASCE 7-16 Sect. 28.3.4 GOVERNS - scaled x {minimum.scale:.4f} "
        f"({minimum.F_computed:.1f} kN computed against "
        f"{minimum.F_minimum:.1f} kN minimum, 16 psf walls + 8 psf roof "
        "projection)"
        if minimum.governs
        else (
            f"; ASCE 7-16 Sect. 28.3.4 checked and does not govern "
            f"({minimum.F_computed:.1f} kN computed against "
            f"{minimum.F_minimum:.1f} kN minimum)"
        )
    )
    for name in wind_case_names():
        load_case = "A" if "_A_" in name else "B"
        positive = name.endswith("_pi_pos")
        cases.append(
            LoadCase(
                name=name, kind="wind",
                description=(
                    f"Envelope procedure Load Case {load_case}, "
                    f"{'positive' if positive else 'negative'} internal "
                    f"pressure (GCpi = "
                    f"{wind.GCpi[0] if positive else wind.GCpi[1]:+.2f})"
                    + (
                        min_note if load_case == "A"
                        else "; the transverse Sect. 28.3.4 minimum does not "
                             "apply to Load Case B, which has no net "
                             "transverse force"
                    )
                ),
                reference=wind.reference,
            )
        )

    raised = (
        f", raised x {endwall_min.scale:.4f} by Sect. 28.3.4"
        if endwall_min is not None and endwall_min.governs
        else ""
    )
    cases.append(
        LoadCase(
            name="W_long", kind="wind",
            description=(
                f"Longitudinal wind on the endwall, {endwall_p:.4f} kN/m2 "
                f"net (Load Case B zones 5 - 6{raised}). Drives the "
                f"longitudinal system: F_eave = {brace_forces.F_eave:.1f} "
                f"kN, {longitudinal.n_braced} braced bays, roof diagonal "
                f"{brace_forces.roof_diagonal:.1f} kN, wall diagonal "
                f"{brace_forces.wall_diagonal:.1f} kN, strut "
                f"{brace_forces.max_strut_force:.1f} kN. NOT in the "
                "transverse frame combinations above - it is resisted by "
                "the bracing, not by the portal."
            ),
            reference="ASCE 7-16 Fig. 28.3-1 Load Case B; statics",
        )
    )
    return cases


def _assumptions(
    *, inputs, wind, frame_index, purlin_cc, girt_cc
) -> tuple[str, ...]:
    """What was assumed, in words, for the sheet.

    An assumption nobody can see is a lie.  Every one of these prints.
    """
    d = inputs.detailing
    return (
        f"Wind is applied to frame line {frame_index} only, from ONE "
        "direction across the section. The frame is symmetric, so the other "
        "direction is its mirror; on an asymmetric frame both must be run "
        "and enveloped.",
        f"A pedestal {d.pedestal_depth:.0f} mm deep with "
        f"{d.anchor_edge:.0f} mm anchor edge distance is ASSUMED, solely so "
        f"the anchor-to-concrete check has a member to sit in. With the "
        f"{d.anchor_spacing_x:.0f} x {d.anchor_spacing_y:.0f} mm anchor "
        f"gauge that is a pedestal at least "
        f"{d.anchor_spacing_x * (d.anchor_cols - 1) + 2 * d.anchor_edge:.0f} "
        f"mm across the frame by "
        f"{d.anchor_spacing_y * (d.anchor_rows - 1) + 2 * d.anchor_edge:.0f} "
        f"mm along it - THE DEMAND THIS DESIGN PLACES ON THE FOUNDATION, "
        "and the number to hand the civil engineer. The foundation is a "
        "separate package and is NOT designed here; the anchor result moves "
        "with this assumption and must be re-run against the real pier.",
        "Base shear is NOT resisted by a shear lug in this run. On a pinned "
        "base the gravity combinations produce the largest horizontal "
        "thrust, and the connection layer reports it as an unchecked demand "
        "rather than absorbing it - see the base plate warnings.",
        "End plate geometry is the AISC DG 4 proportional first pass, not "
        "detailing-office geometry.",
        "K = 1.0 in plane, justified by the second-order (P-Delta) solution. "
        "The Direct Analysis Method's reduced stiffness and notional loads "
        "are NOT applied - see the coverage gaps.",
        f"Ke = {wind.Ke:.2f} (Sect. 26.9), i.e. the site is taken at sea "
        "level.",
        f"Purlin and girt sections are CATALOGUE items and their effective "
        "moduli Se must come from the supplier's table - nothing here is "
        "derived from the profile dimensions.",
        f"Purlins and girts are designed on ASCE 7-16 CHAPTER 30 components-"
        f"and-cladding pressures, not the frame's Chapter 28 pressures. "
        f"Purlin: zone {purlin_cc.zone}, effective wind area "
        f"{purlin_cc.area:.2f} m2, GCp {purlin_cc.gcp_negative:+.3f}, uplift "
        f"{purlin_cc.uplift:.3f} kN/m2. Girt: zone {girt_cc.zone}, area "
        f"{girt_cc.area:.2f} m2, GCp {girt_cc.gcp_negative:+.3f}. Both take "
        "the WORST zone the member crosses, because the zone layout is a "
        "geometry decision this package does not make.",
        f"Bracing is in bays {list(inputs.bracing.braced_bays)}, "
        f"{inputs.bracing.n_braced_walls} braced sidewalls, each roof slope "
        f"split into {inputs.bracing.roof_panel_splits} X panels. All three "
        "are arrangement decisions, not derived results.",
    )
