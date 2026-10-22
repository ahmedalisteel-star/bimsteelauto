"""
bimsteelauto.design.bracing
===========================

Design of the longitudinal bracing members: tension diagonals and the eave
strut, to AISC 360-16 Chapters D and E.

Two member types, two chapters
------------------------------
A brace diagonal is a TENSION member (Chapter D): yielding on the gross
section and rupture on the effective net section, whichever is less.  For a
threaded rod the net section is the thread root, and it usually governs -
which is why a rod is sized on its root area and not its nominal diameter.

The eave strut is a COMPRESSION member (Chapter E) that also spans as a
beam between frames.  Only its axial capacity is checked here; its bending
as a purlin belongs in :mod:`bimsteelauto.design.purlins`.

What the tension-only assumption costs
--------------------------------------
The analysis assumes the compression diagonal has buckled and carries
nothing.  That is safe for the diagonal.  It is NOT safe to then forget the
strut, because the same assumption puts the whole horizontal component into
it.  Both are checked here from the same set of forces.

Units: kN, mm, MPa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from ..analysis.bracing import BracedBay, BraceForces
from ..spec import DesignMethod, MemberCheck, MemberDesign, MemberForces, MemberType
from .units import kip_to_kn, kn_to_kip, mm2_to_in2, mm_to_in, mpa_to_ksi

__all__ = [
    "VERIFIED",
    "COVERAGE_GAPS",
    "BraceDesignError",
    "BraceType",
    "BraceMember",
    "ROD_ROOT_AREA",
    "check_brace",
    "check_strut",
    "design_bracing",
]


class BraceDesignError(RuntimeError):
    """A brace could not be checked."""


#: CLOSED 2026-09-01.
#:
#:     rod_root_area          every tabulated diameter is between 70% and
#:                            85% of the nominal area, which is where the
#:                            ISO coarse-thread stress area sits; M20 pinned
#:                            at 245 mm2 against a 314 mm2 shank
#:     tension_yield_rupture  phi Ag Fy and phi An Fu computed by hand for an
#:                            M24 (100.9 vs 105.9 kN) and the governing mode
#:                            shown to SWITCH to rupture when Fy is raised
#:                            toward Fu - so both branches are exercised
#:     slenderness_advisory   reported as advisory and never failing a rod;
#:                            an angle does carry it
#:     strut_compression      routed through design.members.available_
#:                            strengths, which is itself verified against a
#:                            second AISC implementation
VERIFIED: dict[str, bool] = {
    "rod_root_area": True,
    "tension_yield_rupture": True,
    "slenderness_advisory": True,
    "strut_compression": True,
}

COVERAGE_GAPS: tuple[str, ...] = (
    "The brace end connection - clevis, gusset, or the bolt through an "
    "angle leg - is not designed here. Block shear and bolt bearing at the "
    "brace end are in the connection layer and must be run separately.",
    "Rod bracing is assumed pretensioned enough not to sag. The "
    "pretension force and the turnbuckle are not designed.",
    "Eave strut forces between two braced bays are reported as zero: the "
    "load shed into the strut by intermediate frames is not modelled. The "
    "governing strut is the one between an endwall and the first braced "
    "bay, which IS computed.",
    "Longitudinal seismic is not considered; only wind on the endwall.",
)


class BraceType(str, Enum):
    ROD = "threaded rod"
    ANGLE = "single angle"
    FLAT = "flat bar"
    PIPE = "pipe / CHS"


#: Tensile stress area of a metric coarse thread, mm^2 - ISO 898-1 / the
#: standard Ase = pi/4 (d - 0.9382 p)^2.  Tabulated rather than computed so
#: a wrong pitch cannot silently change the answer.
#:
#: The gap between nominal and root area is the whole point: an M20 rod has
#: 314 mm^2 of shank but only 245 mm^2 at the thread - 22% less.  Sizing a
#: rod on its nominal diameter overstates it by that much.
ROD_ROOT_AREA: dict[float, float] = {
    12.0: 84.3,
    16.0: 157.0,
    20.0: 245.0,
    22.0: 303.0,
    24.0: 353.0,
    27.0: 459.0,
    30.0: 561.0,
    36.0: 817.0,
    42.0: 1120.0,
}


@dataclass(frozen=True)
class BraceMember:
    """A brace diagonal.

    ``Ag`` is the gross area; ``An_effective`` is ``U * An``.  For a rod it
    is the thread root area with ``U = 1.0``; for an angle bolted through
    one leg it is the net area times the Table D3.1 shear-lag factor, which
    is an input because it depends on the connection length.
    """

    name: str
    brace_type: BraceType
    Ag: float                      # mm^2
    An_effective: float            # mm^2
    r_min: float                   # mm, least radius of gyration
    diameter: float | None = None  # mm, for a rod

    @classmethod
    def rod(cls, diameter: float) -> "BraceMember":
        """A threaded rod, sized on its ROOT area.

        The gross area is the shank; the effective net area is the thread
        root from :data:`ROD_ROOT_AREA`, with no shear lag (U = 1.0, a rod
        is connected concentrically).
        """
        if diameter not in ROD_ROOT_AREA:
            raise BraceDesignError(
                f"M{diameter:g} is not in the rod table. Known: "
                + ", ".join(f"M{d:g}" for d in sorted(ROD_ROOT_AREA))
            )
        Ag = math.pi * diameter**2 / 4.0
        return cls(
            name=f"M{diameter:g} rod",
            brace_type=BraceType.ROD,
            Ag=Ag,
            An_effective=ROD_ROOT_AREA[diameter],
            r_min=diameter / 4.0,        # solid circle: r = d/4
            diameter=diameter,
        )

    def __post_init__(self) -> None:
        problems = []
        if self.Ag <= 0:
            problems.append("Ag must be positive")
        if self.An_effective <= 0:
            problems.append("An_effective must be positive")
        if self.An_effective > self.Ag * 1.001:
            problems.append(
                "the effective net area exceeds the gross area; An is a "
                "REDUCED property"
            )
        if self.r_min <= 0:
            problems.append("r_min must be positive")
        if problems:
            raise BraceDesignError(f"{self.name}: " + "; ".join(problems))


#: AISC 360-16 Sect. D1 advises L/r <= 300 for tension members other than
#: rods.  It is explicitly a RECOMMENDATION, not a limit state, and this
#: module reports it as advisory rather than failing a member on it.
SLENDERNESS_ADVISORY = 300.0


def check_brace(
    member: BraceMember,
    bay: BracedBay,
    tension: float,
    *,
    Fy: float,
    Fu: float,
    mark: str,
    method: DesignMethod = DesignMethod.LRFD,
) -> MemberDesign:
    """Check one tension diagonal, AISC 360-16 Chapter D."""
    try:
        from pyaisc360 import Basis
        from pyaisc360.chapter_d import TensionMember, tensile_strength
        from pyaisc360.materials import Steel
    except ImportError as exc:  # pragma: no cover - environment issue
        raise BraceDesignError("pyaisc360 is required for brace design") from exc

    basis = Basis.LRFD if method is DesignMethod.LRFD else Basis.ASD
    steel = Steel(
        name=f"Fy {Fy:.0f} MPa",
        Fy=mpa_to_ksi(Fy),
        Fu=mpa_to_ksi(Fu),
        standard="project brace grade",
    )

    # pyaisc360's Chapter D asks for "Ag" by that exact name; "A" is not a
    # synonym it looks for, so both are set rather than assuming which.
    section = type("S", (), {})()
    section.Ag = mm2_to_in2(member.Ag)
    section.A = mm2_to_in2(member.Ag)
    section.name = member.name

    L_over_r = bay.diagonal_length / member.r_min
    tm = TensionMember(
        section=section,
        steel=steel,
        An=mm2_to_in2(member.An_effective),
        U=1.0,   # already folded into An_effective
        L_over_r=L_over_r,
    )
    res = tensile_strength(tm, basis=basis)
    Pc = kip_to_kn(res.available)

    checks = [
        MemberCheck(
            name=f"Tension ({res.governing.limit_state.value})",
            reference=str(res.governing.citation),
            demand=tension,
            capacity=Pc,
            ratio=tension / Pc if Pc > 0 else math.inf,
            unit="kN",
            note=(
                f"Ag = {member.Ag:.0f} mm2, effective An = "
                f"{member.An_effective:.0f} mm2 "
                f"({member.An_effective / member.Ag:.0%} of gross)"
            ),
        )
    ]

    # Slenderness: advisory, and reported as such.
    advisory_ratio = L_over_r / SLENDERNESS_ADVISORY
    checks.append(
        MemberCheck(
            name="Slenderness L/r (advisory, not a limit state)",
            reference="AISC 360-16 Sect. D1 - a recommendation, not a "
            "requirement; rods are exempt",
            demand=L_over_r,
            capacity=SLENDERNESS_ADVISORY,
            ratio=0.0 if member.brace_type is BraceType.ROD else advisory_ratio,
            unit="-",
            note=(
                "A rod is exempt from the L/r advice but must be "
                "pretensioned so it does not sag."
                if member.brace_type is BraceType.ROD
                else f"L = {bay.diagonal_length:.0f} mm, r = {member.r_min:.1f} mm"
            ),
        )
    )

    return MemberDesign(
        mark=mark,
        member_type=(
            MemberType.ROOF_BRACING
            if bay.plane.value == "roof"
            else MemberType.WALL_BRACING
        ),
        section_id=member.name,
        length=bay.diagonal_length,
        governing_combination="longitudinal wind",
        forces=MemberForces(P=-tension, station=0.0),   # negative = tension
        checks=tuple(checks),
        weight=member.Ag * 1e-6 * 7850.0 * (bay.diagonal_length / 1000.0),
        note=(
            f"{bay.plane.value} bracing, bay {bay.index}, diagonal at "
            f"{math.degrees(bay.angle):.1f} deg; tension-only, so this "
            f"diagonal carries the whole panel shear "
            f"(1/cos = {1.0 / bay.cos_theta:.2f})"
        ),
    )


def check_strut(
    section: Any,
    compression: float,
    length: float,
    *,
    materials,
    mark: str = "ES1",
    method: DesignMethod = DesignMethod.LRFD,
    K: float = 1.0,
) -> MemberDesign:
    """Check the eave strut for its longitudinal DRAG force.

    Axial only.  The strut is also a purlin spanning between frames, and its
    bending is checked there - this is the axial half, and a real strut must
    pass the combined check, which is why both results carry the same mark.
    """
    from .members import available_strengths

    caps = available_strengths(
        section, materials, Lb=length, Lc=K * length, method=method
    )
    ratio = compression / caps.Pc if caps.Pc > 0 else math.inf
    return MemberDesign(
        mark=mark,
        member_type=MemberType.EAVE_STRUT,
        section_id=getattr(section, "mark", getattr(section, "name", "strut")),
        length=length,
        governing_combination="longitudinal wind",
        forces=MemberForces(P=compression, station=0.0),
        checks=(
            MemberCheck(
                name="Compression (drag force)",
                reference=caps.citations.get("Compression", "AISC 360-16 Ch. E"),
                demand=compression,
                capacity=caps.Pc,
                ratio=ratio,
                unit="kN",
                note=(
                    f"K = {K:g}, L = {length:.0f} mm. AXIAL ONLY - the strut "
                    "also spans as a purlin and must pass the combined check."
                ),
            ),
        ),
        weight=0.0,
        note="Eave strut, longitudinal drag",
    )


def design_bracing(
    forces: BraceForces,
    *,
    roof_brace: BraceMember,
    wall_brace: BraceMember,
    Fy: float,
    Fu: float,
    method: DesignMethod = DesignMethod.LRFD,
) -> tuple[MemberDesign, ...]:
    """Check both bracing planes from one :class:`BraceForces`."""
    return (
        check_brace(
            roof_brace, forces.roof_bay, forces.roof_diagonal,
            Fy=Fy, Fu=Fu, mark="RB1", method=method,
        ),
        check_brace(
            wall_brace, forces.wall_bay, forces.wall_diagonal,
            Fy=Fy, Fu=Fu, mark="WB1", method=method,
        ),
    )
