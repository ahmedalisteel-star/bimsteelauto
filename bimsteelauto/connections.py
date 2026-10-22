"""
bimsteelauto.connections
========================

The wire between the analysis and the connection layer.

    Envelope + FrameLayout + detailing  ->  ConnectionDesign per joint

``bimsteelauto_connections`` (v0.7.0, all sixteen VERIFIED flags closed) does
the engineering.  This module does none of it.  Its job is the three things
that sit between an analysis result and that package's entry points, and each
one is a place a real design goes wrong quietly:

1. **Finding the forces.**  The eave moment is not a single number - the
   column top and the rafter start are the same joint, and the envelope holds
   both, under every combination, with different signs.  Taking the wrong one
   under-designs the connection.

2. **Applying the capacity rule.**  Per the client specification a
   connection develops not less than ``CAPACITY_FRACTION`` = 0.50 of the
   connected member's design capacity.  That needs the real capacity of the
   real section at the joint, not a nominal one - so it comes from
   ``design.available_strengths``, the same code path that checks the member.

3. **Detailing that analysis cannot supply.**  Bolt gauge, pitch, plate
   width, edge distance and anchor layout are shop decisions.  They are NOT
   derived silently here.  :class:`ConnectionDetailing` must be supplied, and
   :func:`suggest_endplate_geometry` exists only as an explicitly-labelled
   first pass whose output is recorded as a suggestion on the result.

Units are the package convention throughout: kN, kN.m, mm, MPa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .analysis import Envelope, FrameLayout, FrameMember
from .design.members import AvailableStrengths, available_strengths
from .detailing import (
    DetailingError,
    base_plate_layout,
    end_plate_layout,
)
from .sections import WeldedI
from .spec import (
    ConnectionDesign,
    ConnectionType,
    DesignMethod,
    Materials,
    MemberType,
)

__all__ = [
    "VERIFIED",
    "COVERAGE_GAPS",
    "ConnectionBridgeError",
    "JointForces",
    "ConnectionDetailing",
    "suggest_endplate_geometry",
    "joint_forces",
    "design_frame_connections",
    "cut_cosine",
]


class ConnectionBridgeError(RuntimeError):
    """A joint could not be assembled or designed."""


# ---------------------------------------------------------------------------
# Verification gate
# ---------------------------------------------------------------------------

#: This module performs no engineering, so there are no formulas to verify
#: against a second tool.  What it CAN get wrong is force SELECTION and
#: ROUTING, and those are pinned against envelopes built by hand in
#: tests/test_connections_bridge.py, where the right answer is known by
#: inspection rather than by running this code.
#:
#: CLOSED 2026-09-01.
#:
#:     joint_force_extraction     the larger of the two member ends wins, the
#:                                scan covers every combination, and the
#:                                contributing combination is recorded per
#:                                action - checked against a two-member,
#:                                two-combination envelope written by hand
#:     capacity_fraction_routing  the design moment equals
#:                                max(analysis, 0.50 x Mc) at every joint, and
#:                                the capacity branch is shown to actually
#:                                fire on a lightly loaded frame - a rule that
#:                                never triggers is not implemented
#:     sign_convention_at_joints  moment is taken on magnitude so a hogging
#:                                eave is not lost; axial KEEPS its sign so a
#:                                base in uplift is not silently turned into a
#:                                base in compression
#:
#: DEFECT FOUND AND FIXED during this run: the first version used one
#: reflective converter for all three design shapes.  It matched none of
#: them, produced ConnectionResults with an empty limit-state tuple, and
#: reported every connection in the frame at U = 0.000 - a sheet that says
#: PASS while checking nothing.  Replaced with an explicit converter per
#: type, and pinned by ``test_every_connection_has_limit_states``.
VERIFIED: dict[str, bool] = {
    "joint_force_extraction": True,
    "capacity_fraction_routing": True,
    "sign_convention_at_joints": True,
}

COVERAGE_GAPS: tuple[str, ...] = (
    "Detailing geometry (bolt gauge, pitch, plate width, edge distances, "
    "anchor layout) is an input. suggest_endplate_geometry() gives a first "
    "pass from AISC DG 4 proportions; it is a starting point for the "
    "detailer, not a design, and every result it feeds is marked as such.",
    "Purlin cleats and bracing gussets are not generated from the frame; "
    "the connection layer supports them but the secondary framing forces "
    "are not yet produced by the analysis module.",
    "Column-side checks (AISC J10 stiffeners and doublers) are available in "
    "the connection layer but are not invoked automatically here.",
)


# ---------------------------------------------------------------------------
# Joint forces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JointForces:
    """The design actions at one joint, kN and kN.m.

    Every value is the worst over all combinations, and ``combos`` records
    which combination produced each - because they are usually different
    ones, and a sheet that reports a single combination for a joint is
    hiding that.
    """

    node: str
    M: float
    V: float
    P: float
    combos: dict[str, str] = field(default_factory=dict)
    #: Contributing (member mark, station) pairs, for the audit trail.
    sources: tuple[tuple[str, float], ...] = ()

    @property
    def uplift(self) -> bool:
        """True when the axial action is net tension."""
        return self.P < 0.0


def joint_forces(
    envelope: Envelope,
    node: str,
    members: Sequence[tuple[FrameMember, float]],
) -> JointForces:
    """Worst M, V and P at a joint, over every member end that meets there.

    ``members`` is the list of ``(member, station)`` pairs framing into the
    node - for an eave that is the column at its top AND the rafter at its
    start.  Both are scanned: the envelope is not symmetric about a joint
    once a taper and a haunch are involved, and the larger of the two is what
    the connection must carry.
    """
    best = {"M": 0.0, "V": 0.0, "P": 0.0}
    combos: dict[str, str] = {}
    sources: list[tuple[str, float]] = []
    signed_P = 0.0

    for member, station in members:
        per_combo = envelope.stations.get(member.mark)
        if not per_combo:
            continue
        sources.append((member.mark, station))
        for combo, stations in per_combo.items():
            st = min(stations, key=lambda s: abs(s.x - station))
            for key, value in (("M", st.M), ("V", st.V), ("P", st.P)):
                if abs(value) > abs(best[key]):
                    best[key] = abs(value)
                    combos[key] = combo
                    if key == "P":
                        signed_P = value

    return JointForces(
        node=node,
        M=best["M"],
        V=best["V"],
        # P keeps its sign: a base plate under net uplift is a different
        # design from one under compression, and abs() would erase that.
        P=signed_P,
        combos=combos,
        sources=tuple(sources),
    )


# ---------------------------------------------------------------------------
# Detailing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectionDetailing:
    """Shop decisions the analysis cannot make.

    Nothing here has a default that could silently become a design.  The two
    that do carry values - ``plate_palette`` is the stock list, and
    ``bolt_grade`` - are catalogue facts, not engineering choices.
    """

    bolt_diameter: float               # mm
    bolt_grade: str                    # e.g. "A325" or "8.8"
    plate_grade: str                   # e.g. "S355JR"
    anchor_grade: str                  # e.g. "F1554-36"
    #: Base plate, mm.  N along the frame, B across it.
    base_plate_N: float
    base_plate_B: float
    anchor_diameter: float
    anchor_embedment: float
    anchor_rows: int = 2
    anchor_cols: int = 2
    anchor_spacing_x: float = 300.0
    anchor_spacing_y: float = 300.0
    anchor_edge: float = 150.0
    pedestal_depth: float | None = None
    A2_over_A1: float = 1.0
    fc_prime: float = 25.0


def suggest_endplate_geometry(
    section: WeldedI,
    detailing: ConnectionDetailing,
    *,
    config: str = "4E",
) -> tuple[Any, str]:
    """A FIRST PASS at end plate geometry from AISC DG 4 proportions.

    Returns ``(EndPlateGeometry, note)``.  The note says, in words, that the
    geometry was suggested rather than detailed - it is attached to the
    result so it reaches the calculation sheet and the detailer sees it.

    The proportions used, all standard DG 4 practice:

        bp   = flange width + 2 x 25 mm, rounded up to 10 mm
        g    = 100 mm gauge for bolts up to M24, else 140 mm
        pf   = bolt diameter + 12 mm, but not less than 40 mm
        de   = 1.5 x bolt diameter
        pb   = 3 x bolt diameter (8ES only)

    These are proportions, not a design.  A detailer will move them to suit
    the wrench clearance, the flange plate and the shop's jig.
    """
    from bimsteelauto_connections import EndPlateConfig, EndPlateGeometry

    db = detailing.bolt_diameter
    bp = math.ceil((section.bf_top + 50.0) / 10.0) * 10.0
    g = 100.0 if db <= 24.0 else 140.0
    pf = max(40.0, db + 12.0)
    de = 1.5 * db
    pb = 3.0 * db

    d = section.d
    tf = section.tf_top
    cfg = EndPlateConfig(config)

    if cfg is EndPlateConfig.EIGHT_ES:
        # Two rows outside and two inside the tension flange.
        h_rows = (d + pf, d - tf - pf, d - tf - pf - pb, pf)
    elif cfg is EndPlateConfig.FOUR_ES:
        h_rows = (d + pf, d - tf - pf)
    else:  # 4E
        h_rows = (d + pf, d - tf - pf)

    geom = EndPlateGeometry(
        bp=bp,
        g=g,
        pfo=pf,
        pfi=pf,
        h_rows=tuple(sorted(h_rows, reverse=True)),
        de=de,
        pb=pb if cfg is EndPlateConfig.EIGHT_ES else None,
    )
    note = (
        f"End plate geometry SUGGESTED from AISC DG 4 proportions for a "
        f"{config} configuration with M{db:.0f} bolts (bp = {bp:.0f}, "
        f"g = {g:.0f}, pf = {pf:.0f}, de = {de:.0f} mm). Not a detailed "
        f"layout - confirm against wrench clearance and the shop standard "
        f"before issue."
    )
    return geom, note


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------


def design_frame_connections(
    layout: FrameLayout,
    envelope: Envelope,
    materials: Materials,
    detailing: ConnectionDetailing,
    *,
    method: DesignMethod = DesignMethod.LRFD,
    capacity_fraction: float | None = None,
    endplate_config: str = "4E",
    strict: bool = True,
) -> tuple[ConnectionDesign, ...]:
    """Design every joint in one frame line.

    One eave end plate per eave, one apex end plate per ridge, one base plate
    with anchors per base.
    """
    try:
        import bimsteelauto_connections as conn
    except ImportError as exc:  # pragma: no cover - environment issue
        raise ConnectionBridgeError(
            "bimsteelauto_connections is required. Put its parent folder on "
            "sys.path."
        ) from exc

    fraction = (
        conn.CAPACITY_FRACTION if capacity_fraction is None else capacity_fraction
    )
    out: list[ConnectionDesign] = []

    columns = {m.mark: m for m in layout.members if m.member_type is MemberType.COLUMN}
    rafters = {m.mark: m for m in layout.members if m.member_type is MemberType.RAFTER}

    # -- eaves ---------------------------------------------------------------
    for i, node in enumerate(layout.eave_nodes, start=1):
        framing = _members_at(layout, node)
        if not framing:
            continue
        jf = joint_forces(envelope, node, framing)
        # The connected member is the shallower of the two at the joint - it
        # is the one whose capacity the connection must develop.
        section = _governing_section(framing)
        caps = available_strengths(
            section, materials, Lb=_brace_length(framing), method=method
        )
        out.append(
            _design_endplate_joint(
                conn=conn,
                mark=f"EC{i}",
                node=node,
                ctype=ConnectionType.EAVE_END_PLATE,
                jf=jf,
                section=section,
                caps=caps,
                detailing=detailing,
                fraction=fraction,
                config=endplate_config,
                member_marks=tuple(m.mark for m, _ in framing),
                strict=strict,
                cos_cut=cut_cosine(framing),
            )
        )

    # -- apexes --------------------------------------------------------------
    for i, node in enumerate(layout.ridge_nodes, start=1):
        framing = _members_at(layout, node)
        if not framing:
            continue
        jf = joint_forces(envelope, node, framing)
        section = _governing_section(framing)
        caps = available_strengths(
            section, materials, Lb=_brace_length(framing), method=method
        )
        out.append(
            _design_endplate_joint(
                conn=conn,
                mark=f"AP{i}",
                node=node,
                ctype=ConnectionType.APEX_END_PLATE,
                jf=jf,
                section=section,
                caps=caps,
                detailing=detailing,
                fraction=fraction,
                config=endplate_config,
                member_marks=tuple(m.mark for m, _ in framing),
                strict=strict,
                cos_cut=cut_cosine(framing),
            )
        )

    # -- bases ---------------------------------------------------------------
    for i, node in enumerate(layout.base_nodes, start=1):
        out.append(
            _design_base_joint(
                conn=conn,
                mark=f"BP{i}",
                node=node,
                layout=layout,
                envelope=envelope,
                materials=materials,
                detailing=detailing,
                strict=strict,
            )
        )

    return tuple(out)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _members_at(
    layout: FrameLayout, node: str
) -> list[tuple[FrameMember, float]]:
    """Members framing into a node, with the station of that end."""
    target = layout.nodes[node]
    out: list[tuple[FrameMember, float]] = []
    for m in layout.members:
        if _same_point(m.i_point, target):
            out.append((m, 0.0))
        elif _same_point(m.j_point, target):
            out.append((m, m.length))
    return out


def _same_point(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return math.isclose(a[0], b[0], abs_tol=1e-6) and math.isclose(
        a[1], b[1], abs_tol=1e-6
    )


def _governing_section(framing: Sequence[tuple[FrameMember, float]]) -> WeldedI:
    """The shallower connected section at the joint.

    Both members are welded to the same end plate, so the plate has to
    develop the weaker of the two.  Taking the deeper one would overstate the
    capacity the connection is required to reach - which sounds conservative
    but is not: the capacity rule sets a MINIMUM design force, and inflating
    it designs the wrong connection while masking that the real member is
    weaker.
    """
    sections = [m.taper.at(x) for m, x in framing]
    return min(sections, key=lambda s: s.Zx)


def _brace_length(framing: Sequence[tuple[FrameMember, float]]) -> float:
    """A conservative unbraced length at a joint.

    A joint is a brace point in practice - the column flange, the haunch and
    the end plate all restrain it - so the shortest member framing in bounds
    it.  Used only for the CAPACITY side of the design-force rule.
    """
    return min(m.length for m, _ in framing)


def cut_cosine(framing: Sequence[tuple[FrameMember, float]]) -> float:
    """Cosine of the angle between a VERTICAL plate and the member it cuts.

    An eave and an apex plate are both cut vertically across a sloping
    rafter, so the plate sees ``d / cos`` of section depth and ``tf / cos``
    of each flange.  That cosine is not an input anywhere - it is the
    rafter's own geometry, and taking it from the layout means the plate is
    laid out on the slope that was actually analysed rather than on a slope
    someone typed in twice.

    Returned as the RAFTER's horizontal run over its length.  A joint with
    no rafter framing into it (which should not happen at an eave or a
    ridge) falls back to 1.0 - a normal cut - and that is the safe fallback
    because it is the one that does not stretch anything.
    """
    for member, _ in framing:
        if member.member_type is not MemberType.RAFTER:
            continue
        dx = member.j_point[0] - member.i_point[0]
        dy = member.j_point[1] - member.i_point[1]
        L = math.hypot(dx, dy)
        if L > 1e-9:
            return min(1.0, max(abs(dx) / L, 1e-6))
    return 1.0


def _endplate_geometry(
    *, section, geom, design, detailing, config, cos_cut
) -> dict[str, Any]:
    """Shop geometry for one end plate, or an explanation of its absence.

    The layout is arithmetic on the SAME DG 4 proportions the design used -
    ``pfo``, ``pfi``, ``de``, ``pb`` - and on the plate thickness the design
    selected, so the plate that is drawn is the plate that was checked. It
    can still fail to close (a section too deep for its extension, a bolt
    row that will not stay on the plate), and when it does the reason is
    recorded rather than a fallback plate being drawn.
    """
    rows_in = 2 if str(config).upper().startswith("8") else 1
    try:
        layout = end_plate_layout(
            section,
            thickness=float(design.t_plate),
            bolt_diameter=float(detailing.bolt_diameter),
            width=float(geom.bp),
            gauge_g=float(geom.g),
            e_out=float(geom.pfo),
            e_in=float(geom.pfi),
            de=float(geom.de),
            rows_in=rows_in,
            pitch=float(geom.pb) if geom.pb else None,
            cos_cut=cos_cut,
        )
    except DetailingError as exc:
        return {
            "kind": "end_plate",
            "available": False,
            "why": str(exc),
        }
    out = layout.to_dict()
    out["available"] = True
    out["section_at_joint"] = {
        "mark": section.mark,
        "d_mm": round(section.d, 3),
        "tw_mm": round(section.tw, 3),
        "bf_top_mm": round(section.bf_top, 3),
        "tf_top_mm": round(section.tf_top, 3),
        "bf_bot_mm": round(section.bf_bot, 3),
        "tf_bot_mm": round(section.tf_bot, 3),
    }
    return out


def _design_endplate_joint(
    *, conn, mark, node, ctype, jf, section, caps, detailing, fraction, config,
    member_marks, strict, cos_cut=1.0,
) -> ConnectionDesign:
    Mu = conn.design_force(
        analysis=abs(jf.M),
        member_capacity=caps.Mc,
        fraction=fraction,
        label="Mu",
        unit="kNm",
    )
    geom, geom_note = suggest_endplate_geometry(section, detailing, config=config)

    try:
        design = conn.design_endplate(
            Mu=Mu.value,
            geom=geom,
            plate_grade=detailing.plate_grade,
            config=conn.EndPlateConfig(config),
            strict=strict,
        )
    except Exception as exc:
        raise ConnectionBridgeError(
            f"{mark} at {node}: end plate design failed for Mu = "
            f"{Mu.value:.1f} kNm - {exc}"
        ) from exc

    result = _endplate_result(conn, design, mark, ctype, Mu, geom_note)
    cd = ConnectionDesign.from_result(result, ctype, member_marks)
    # Schema 1.2.0: the shop geometry rides on the result, so the FreeCAD
    # adapter can cut the real plate without re-deriving a single dimension.
    cd.detail["geometry"] = _endplate_geometry(
        section=section, geom=geom, design=design, detailing=detailing,
        config=config, cos_cut=cos_cut,
    )
    cd.detail["joint"] = {
        "node": node,
        "M_analysis": jf.M,
        "V_analysis": jf.V,
        "P_analysis": jf.P,
        "combinations": dict(jf.combos),
        "member_capacity_Mc": caps.Mc,
        "capacity_fraction": fraction,
        "design_moment": Mu.value,
        "criterion": Mu.criterion.value,
        "section_at_joint": section.mark,
        "geometry_note": geom_note,
    }
    return cd


def _design_base_joint(
    *, conn, mark, node, layout, envelope, materials, detailing, strict
) -> ConnectionDesign:
    """Base plate and anchors, from the support reactions.

    The governing combination is chosen on UPLIFT first and compression
    second.  A PEB base is almost always sized by the wind uplift case, and
    scanning for the largest compression would miss it entirely.
    """
    per_combo = envelope.reactions[node]
    worst_uplift = min(per_combo.items(), key=lambda kv: kv[1][1])
    worst_comp = max(per_combo.items(), key=lambda kv: kv[1][1])
    worst_moment = max(per_combo.items(), key=lambda kv: abs(kv[1][2]))
    worst_shear = max(per_combo.items(), key=lambda kv: abs(kv[1][0]))

    combo_u, (fx_u, fy_u, mz_u) = worst_uplift
    combo_c, (fx_c, fy_c, mz_c) = worst_comp

    # Pu positive in compression, matching the connection layer.
    Pu = max(fy_c, 0.0)
    Nua = max(-fy_u, 0.0)
    Mu = abs(worst_moment[1][2])
    Vu = abs(worst_shear[1][0])

    column = next(
        (m for m in layout.members if _same_point(m.i_point, layout.nodes[node])),
        None,
    )
    if column is None:  # pragma: no cover - every base carries a column
        raise ConnectionBridgeError(f"no column found at base node {node}")
    section = column.taper.at(0.0)

    geom = conn.BasePlateGeometry(
        N=detailing.base_plate_N,
        B=detailing.base_plate_B,
        d=section.d,
        bf=section.bf_top,
        f=None,
        A2_over_A1=detailing.A2_over_A1,
        tf=section.tf_top,
    )
    try:
        design = conn.design_base_plate(
            Pu=Pu,
            Mu=Mu,
            Vu=Vu,
            geom=geom,
            plate_grade=detailing.plate_grade,
            fc_prime=detailing.fc_prime,
            strict=strict,
        )
    except Exception as exc:
        raise ConnectionBridgeError(
            f"{mark} at {node}: base plate design failed - {exc}"
        ) from exc

    group = conn.AnchorGroup(
        d=detailing.anchor_diameter,
        hef=detailing.anchor_embedment,
        n_x=detailing.anchor_cols,
        n_y=detailing.anchor_rows,
        s_x=detailing.anchor_spacing_x,
        s_y=detailing.anchor_spacing_y,
        ca_x1=detailing.anchor_edge,
        ca_x2=detailing.anchor_edge,
        ca_y1=detailing.anchor_edge,
        ca_y2=detailing.anchor_edge,
        cracked=True,
        supplementary_reinforcement=False,
        edge_reinforcement=False,
        stirrups_at_100mm=False,
        grout_pad=True,
        ha=detailing.pedestal_depth,
        across_flats=None,
    )
    try:
        anchors = conn.check_anchors(
            Nua=Nua,
            Vua=Vu,
            group=group,
            grade=detailing.anchor_grade,
            fc_prime=detailing.fc_prime,
            strict=strict,
        )
    except Exception as exc:
        raise ConnectionBridgeError(
            f"{mark} at {node}: anchor check failed - {exc}"
        ) from exc

    Pu_force = conn.design_force(
        analysis=max(Pu, Nua), label="Pu" if Pu >= Nua else "Nua", unit="kN"
    )
    result = _baseplate_result(conn, design, anchors, mark, Pu_force, "")
    cd = ConnectionDesign.from_result(
        result, ConnectionType.BASE_PLATE, (column.mark,)
    )
    # Schema 1.2.0 - the drawn plate is the plate the bearing check used, and
    # the drawn anchors are at the positions the ACI 318 check used. Nothing
    # here re-derives a dimension; if a layout will not close it says so.
    try:
        bp_layout = base_plate_layout(
            N=detailing.base_plate_N,
            B=detailing.base_plate_B,
            thickness=float(design.t_selected),
            anchor_diameter=detailing.anchor_diameter,
            n_x=detailing.anchor_cols,
            n_y=detailing.anchor_rows,
            s_x=detailing.anchor_spacing_x,
            s_y=detailing.anchor_spacing_y,
            anchor_embedment=float(anchors.hef),
        )
    except DetailingError as exc:
        cd.detail["geometry"] = {
            "kind": "base_plate", "available": False, "why": str(exc)
        }
    else:
        geo = bp_layout.to_dict()
        geo["available"] = True
        geo["column_at_base"] = {
            "mark": section.mark,
            "d_mm": round(section.d, 3),
            "tw_mm": round(section.tw, 3),
            "bf_top_mm": round(section.bf_top, 3),
            "tf_top_mm": round(section.tf_top, 3),
            "bf_bot_mm": round(section.bf_bot, 3),
            "tf_bot_mm": round(section.tf_bot, 3),
        }
        cd.detail["geometry"] = geo
    cd.detail["joint"] = {
        "node": node,
        "Pu_compression": Pu,
        "Nua_uplift": Nua,
        "Mu": Mu,
        "Vu": Vu,
        "combination_uplift": combo_u,
        "combination_compression": combo_c,
        "combination_moment": worst_moment[0],
        "cracked_concrete_assumed": True,
    }
    return cd


def _endplate_result(conn, design, mark, ctype, force, note):
    """EndPlateDesign -> ConnectionResult.

    Written out per type rather than guessed generically.  The first version
    of this module used one reflective converter for all three design shapes;
    it found no limit states on any of them and reported every connection at
    U = 0.000 - a connection sheet that says PASS while checking nothing.
    An explicit converter per type cannot fail that way silently: if the
    library adds a field, this raises, it does not quietly zero.
    """
    states = [
        conn.LimitState(
            name="End plate flexural strength",
            capacity=design.Mn_design,
            demand=design.Mu,
            clause="AISC Design Guide 4, yield-line mechanism",
            unit="kNm",
        ),
        conn.LimitState(
            name="End plate thickness",
            capacity=design.t_plate,
            demand=design.t_required,
            clause="AISC DG 4 thick-plate rule",
            unit="mm",
        ),
    ]
    yl = getattr(design, "yield_line", None)
    selections = {
        "configuration": str(getattr(design.config, "value", design.config)),
        "plate thickness": f"{design.t_plate:.0f} mm (required {design.t_required:.1f})",
        "plate Fy": f"{design.Fy:.0f} MPa",
    }
    if yl is not None:
        for key in ("Yp", "s", "db_required"):
            v = getattr(yl, key, None)
            if isinstance(v, (int, float)):
                selections[key] = round(v, 3)

    return conn.ConnectionResult(
        mark=mark,
        kind=ctype.value,
        limit_states=tuple(states),
        forces=(force,) if force is not None else (),
        selections=selections,
        warnings=(note,) if note else (),
        verified=True,
        citation="AISC Design Guide 4 / AISC 358-16",
    )


def _baseplate_result(conn, design, anchors, mark, force, note):
    """BasePlateDesign + AnchorCheck -> one ConnectionResult.

    Plate and anchors are one connection on the drawing and one page on the
    sheet, so they are one result.  Keeping them apart would let a base pass
    with a failing anchor.
    """
    states = [
        conn.LimitState(
            name="Concrete bearing under the plate",
            capacity=design.phiPp,
            demand=design.Pu,
            clause="AISC Design Guide 1, Eq. 3.1.1",
            unit="kN",
        ),
        conn.LimitState(
            name="Base plate thickness",
            capacity=design.t_selected,
            demand=design.t_required,
            clause="AISC Design Guide 1, Sect. 3.3 / 3.4",
            unit="mm",
        ),
        conn.LimitState(
            name="Anchor tension (governing mode)",
            capacity=anchors.phiNn,
            demand=anchors.Nua,
            clause=f"ACI 318-19 Ch. 17 - {anchors.governing_tension}",
            unit="kN",
        ),
        conn.LimitState(
            name="Anchor shear (governing mode)",
            capacity=anchors.phiVn,
            demand=anchors.Vua,
            clause=f"ACI 318-19 Ch. 17 - {anchors.governing_shear}",
            unit="kN",
        ),
        conn.LimitState(
            name="Anchor tension-shear interaction",
            capacity=1.0,
            demand=anchors.interaction,
            clause=f"ACI 318-19 Sect. 17.8 ({anchors.interaction_rule})",
            unit="-",
        ),
    ]
    selections = {
        "bearing case": str(getattr(design.case, "value", design.case)),
        "plate thickness": f"{design.t_selected:.0f} mm (required {design.t_required:.1f})",
        "eccentricity": f"{design.eccentricity:.0f} mm (critical {design.e_critical:.0f})",
        "anchor tension per group": f"{design.T_anchor:.1f} kN",
        "embedment hef used": f"{anchors.hef:.0f} mm",
    }
    warnings = tuple(getattr(design, "warnings", ()) or ()) + tuple(
        getattr(anchors, "warnings", ()) or ()
    )
    if note:
        warnings = warnings + (note,)

    return conn.ConnectionResult(
        mark=mark,
        kind=ConnectionType.BASE_PLATE.value,
        limit_states=tuple(states),
        forces=(force,) if force is not None else (),
        selections=selections,
        warnings=warnings,
        verified=True,
        citation=str(getattr(design, "citation", "")) or "AISC DG 1 / ACI 318-19",
    )


def _flatten(obj: Any) -> dict[str, Any]:
    """Public scalar attributes of a result object, for the audit trail."""
    out: dict[str, Any] = {}
    for k in dir(obj):
        if k.startswith("_"):
            continue
        try:
            v = getattr(obj, k)
        except Exception:  # pragma: no cover - defensive
            continue
        if isinstance(v, (int, float, str, bool)) and not callable(v):
            out[k] = v
    return out
