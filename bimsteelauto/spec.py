"""
bimsteelauto.spec
=================

The contract between the three layers of BIMSteelAuto.

    ProjectSpec  ->  ENGINE  ->  DesignResult  ->  ADAPTER  ->  FreeCAD / IFC / TechDraw

Nothing in this module imports FreeCAD, numpy, or any analysis library.
It is pure stdlib so that it can be imported from *any* environment:
FreeCAD's bundled Python, the fat engine env, or a bare pytest run.

Units
-----
SI internal, matching the connection layer already in the package:

    length      mm
    force       kN
    moment      kN.m
    stress      MPa  (= N/mm^2)
    pressure    kN/m^2
    speed       m/s
    density     kg/m^3

Geometry is entered by the user in metres in the GUI; ``PEBGeometry.from_m``
does the conversion once, at the boundary.  Everything downstream is mm.

Design rule
-----------
No field is given a default unless that default is a *code* value
(e.g. Kzt = 1.0 from ASCE 7-16 Sect. 26.8.2) or a project constant already
fixed for BIMSteelAuto (e.g. CAPACITY_FRACTION = 0.50).  Anything that must
come from the engineer has no default and validation fails loudly if it is
missing.  Blanks, never assumptions.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

__all__ = [
    "SCHEMA_VERSION",
    "BracingArrangement",
    "CAPACITY_FRACTION",
    "SpecError",
    "ExposureCategory",
    "EnclosureClassification",
    "RiskCategory",
    "FrameType",
    "RoofType",
    "EndwallType",
    "DesignMethod",
    "LoadCode",
    "MemberType",
    "ConnectionType",
    "ProjectSite",
    "PEBGeometry",
    "Materials",
    "DesignSettings",
    "ProjectSpec",
    "MemberForces",
    "MemberCheck",
    "MemberDesign",
    "ConnectionDesign",
    "LoadCase",
    "LoadCombination",
    "LoadSummary",
    "SupportReaction",
    "DesignResult",
]

#: Bumped to 1.1.0 on 2026-09-02 when ``DesignResult.bracing`` was added.
#: The change is ADDITIVE and backward compatible: ``_from_jsonable`` skips
#: keys a document does not carry, so a 1.0.0 result still loads and simply
#: reports no bracing arrangement.
#:
#: Bumped to 1.2.0 on 2026-09-03 when ``ConnectionDesign.detail["geometry"]``
#: started carrying SHOP GEOMETRY - the plate outline, the hole positions and
#: the anchor grid, in mm, from ``bimsteelauto.detailing``.  Also additive:
#: ``detail`` is a free dict, so a 1.1.0 result loads unchanged and simply
#: has no geometry to build.  The adapter must therefore CHECK rather than
#: assume - an empty geometry means nobody recorded one, and the right
#: response is to build the member model and say so, not to invent a plate.
SCHEMA_VERSION = "1.2.0"

#: Connections shall develop not less than this fraction of the connected
#: member's design capacity (client specification, confirmed 2026-08-30).
#:
#: This value is duplicated from ``bimsteelauto_connections.core`` on purpose:
#: spec.py imports nothing, so it cannot read it from there.  The two are held
#: equal by ``tests/test_spec_connections_bridge.py``, which fails loudly if
#: they ever drift.
CAPACITY_FRACTION = 0.50

#: Young's modulus of structural steel, MPa (AISC 360-16 Sect. B3).
E_STEEL = 200_000.0

_MM_PER_M = 1000.0


class SpecError(ValueError):
    """Raised when a spec or result fails validation.

    Carries *every* problem found, not just the first, so the GUI can show
    the engineer a complete list of blanks in one pass.
    """

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class _StrEnum(str, Enum):
    """str-backed enum so that dataclasses serialise straight to JSON."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class ExposureCategory(_StrEnum):
    """ASCE 7-16 Sect. 26.7.3. Exposure A was withdrawn after ASCE 7-02."""

    B = "B"
    C = "C"
    D = "D"


class EnclosureClassification(_StrEnum):
    """ASCE 7-16 Sect. 26.2 / Table 26.13-1."""

    ENCLOSED = "enclosed"
    PARTIALLY_ENCLOSED = "partially_enclosed"
    PARTIALLY_OPEN = "partially_open"
    OPEN = "open"


class RiskCategory(_StrEnum):
    """ASCE 7-16 Table 1.5-1."""

    I = "I"
    II = "II"
    III = "III"
    IV = "IV"


class FrameType(_StrEnum):
    CLEAR_SPAN = "clear_span"
    MULTI_SPAN = "multi_span"


class RoofType(_StrEnum):
    GABLE = "gable"
    MONOSLOPE = "monoslope"


class EndwallType(_StrEnum):
    POST_AND_BEAM = "post_and_beam"
    RIGID_FRAME = "rigid_frame"


class DesignMethod(_StrEnum):
    LRFD = "LRFD"
    ASD = "ASD"


class LoadCode(_StrEnum):
    ASCE_7_16 = "ASCE 7-16"
    SBC_301_2018 = "SBC 301-2018"


class MemberType(_StrEnum):
    COLUMN = "column"
    RAFTER = "rafter"
    INTERIOR_COLUMN = "interior_column"
    ENDWALL_POST = "endwall_post"
    ENDWALL_RAFTER = "endwall_rafter"
    PURLIN = "purlin"
    GIRT = "girt"
    EAVE_STRUT = "eave_strut"
    ROOF_BRACING = "roof_bracing"
    WALL_BRACING = "wall_bracing"


class ConnectionType(_StrEnum):
    EAVE_END_PLATE = "eave_end_plate"
    APEX_END_PLATE = "apex_end_plate"
    BASE_PLATE = "base_plate"
    PURLIN_CLEAT = "purlin_cleat"
    BRACING_GUSSET = "bracing_gusset"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectSite:
    """The five variable wind inputs.

    Everything else the wind module needs (h, B, L, theta, end-zone width a)
    is derived from :class:`PEBGeometry`.  These five are the only wind
    quantities the engineer supplies.

    Attributes
    ----------
    basic_wind_speed:
        V, 3-second gust at 10 m in Exposure C, m/s.  ASCE 7-16 Figs. 26.5-1
        and 26.5-2 give V already mapped to the risk category, so V and
        ``risk_category`` must be taken from the *same* map.
    exposure_category:
        ASCE 7-16 Sect. 26.7.3.
    enclosure_classification:
        ASCE 7-16 Sect. 26.12; sets GCpi via Table 26.13-1.
    Kzt:
        Topographic factor, ASCE 7-16 Sect. 26.8.  1.0 unless the building
        sits on an escarpment or hill meeting Sect. 26.8.1.
    risk_category:
        ASCE 7-16 Table 1.5-1.  Selects the wind map and Ie.
    """

    basic_wind_speed: float
    exposure_category: ExposureCategory
    enclosure_classification: EnclosureClassification
    risk_category: RiskCategory
    Kzt: float = 1.0

    # -- derived, code-fixed -------------------------------------------------

    @property
    def Kd(self) -> float:
        """Directionality factor, ASCE 7-16 Table 26.6-1, MWFRS of a building."""
        return 0.85

    @property
    def GCpi(self) -> tuple[float, float]:
        """Internal pressure coefficients (positive, negative), Table 26.13-1."""
        table = {
            EnclosureClassification.ENCLOSED: (0.18, -0.18),
            EnclosureClassification.PARTIALLY_ENCLOSED: (0.55, -0.55),
            EnclosureClassification.PARTIALLY_OPEN: (0.18, -0.18),
            EnclosureClassification.OPEN: (0.00, 0.00),
        }
        return table[self.enclosure_classification]

    def problems(self) -> list[str]:
        out: list[str] = []
        if not (10.0 <= self.basic_wind_speed <= 100.0):
            out.append(
                f"site.basic_wind_speed = {self.basic_wind_speed} m/s is outside "
                "the credible range 10-100 m/s (is it in mph?)"
            )
        if not (1.0 <= self.Kzt <= 3.0):
            out.append(f"site.Kzt = {self.Kzt} is outside 1.0-3.0")
        return out


@dataclass(frozen=True)
class PEBGeometry:
    """Pre-engineered building geometry, all lengths in mm.

    Use :meth:`from_m` to build one from the metre values the GUI collects.

    Attributes
    ----------
    span_widths:
        Out-to-out width of each span, mm.  One entry for a clear-span frame,
        two or more for a multi-span frame.  Their sum is the building width B.
    bay_spacings:
        Centre-to-centre spacing of each bay along the length, mm.  Their sum
        is the building length L.  Non-uniform spacing is allowed and carried
        through - it must not be averaged.
    eave_height:
        Finished floor to the underside of the eave, mm.
    roof_slope:
        Rise per unit run, e.g. 0.10 for 1:10.  Always positive.
    """

    span_widths: tuple[float, ...]
    bay_spacings: tuple[float, ...]
    eave_height: float
    roof_slope: float
    purlin_spacing: float
    girt_spacing: float
    frame_type: FrameType = FrameType.CLEAR_SPAN
    roof_type: RoofType = RoofType.GABLE
    endwall_type: EndwallType = EndwallType.POST_AND_BEAM

    # -- constructors --------------------------------------------------------

    @classmethod
    def from_m(
        cls,
        *,
        span_widths: Sequence[float],
        bay_spacings: Sequence[float],
        eave_height: float,
        roof_slope: float,
        purlin_spacing: float,
        girt_spacing: float,
        frame_type: FrameType | str = FrameType.CLEAR_SPAN,
        roof_type: RoofType | str = RoofType.GABLE,
        endwall_type: EndwallType | str = EndwallType.POST_AND_BEAM,
    ) -> "PEBGeometry":
        """Build from metres.  The only place m -> mm conversion happens."""
        return cls(
            span_widths=tuple(float(v) * _MM_PER_M for v in span_widths),
            bay_spacings=tuple(float(v) * _MM_PER_M for v in bay_spacings),
            eave_height=float(eave_height) * _MM_PER_M,
            roof_slope=float(roof_slope),
            purlin_spacing=float(purlin_spacing) * _MM_PER_M,
            girt_spacing=float(girt_spacing) * _MM_PER_M,
            frame_type=FrameType(frame_type),
            roof_type=RoofType(roof_type),
            endwall_type=EndwallType(endwall_type),
        )

    # -- derived -------------------------------------------------------------

    @property
    def width(self) -> float:
        """Building width B, mm (across the frame)."""
        return sum(self.span_widths)

    @property
    def length(self) -> float:
        """Building length L, mm (along the ridge)."""
        return sum(self.bay_spacings)

    @property
    def n_bays(self) -> int:
        return len(self.bay_spacings)

    @property
    def n_frames(self) -> int:
        """Number of transverse frame lines, including the two endwalls."""
        return self.n_bays + 1

    @property
    def slope_deg(self) -> float:
        return math.degrees(math.atan(self.roof_slope))

    @property
    def ridge_rise(self) -> float:
        """Rise from eave to the highest point of the roof, mm."""
        if self.roof_type is RoofType.MONOSLOPE:
            run = self.width
        else:
            # Gable: ridge sits over the centre of each span; the governing
            # rise is that of the widest span's half-width.
            run = max(self.span_widths) / 2.0
        return run * self.roof_slope

    @property
    def ridge_height(self) -> float:
        """Finished floor to ridge, mm."""
        return self.eave_height + self.ridge_rise

    @property
    def mean_roof_height(self) -> float:
        """h per ASCE 7-16 Sect. 26.3.

        For theta <= 10 deg, h is the eave height; otherwise the average of
        eave and ridge.
        """
        if self.slope_deg <= 10.0:
            return self.eave_height
        return 0.5 * (self.eave_height + self.ridge_height)

    @property
    def rafter_length(self) -> float:
        """Sloped length of one rafter over the widest span, mm."""
        if self.roof_type is RoofType.MONOSLOPE:
            run = self.width
        else:
            run = max(self.span_widths) / 2.0
        return math.hypot(run, run * self.roof_slope)

    @property
    def plan_area(self) -> float:
        """Footprint, mm^2."""
        return self.width * self.length

    def tributary_widths(self) -> tuple[float, ...]:
        """Plan tributary width of each frame line, mm.

        Endwall frames take half of their single adjacent bay; interior frames
        take half of each adjacent bay.  Non-uniform bay spacing is preserved.
        """
        b = self.bay_spacings
        out = [b[0] / 2.0]
        for i in range(1, len(b)):
            out.append((b[i - 1] + b[i]) / 2.0)
        out.append(b[-1] / 2.0)
        return tuple(out)

    def problems(self) -> list[str]:
        out: list[str] = []
        if not self.span_widths:
            out.append("geometry.span_widths is empty")
        if any(v <= 0 for v in self.span_widths):
            out.append("geometry.span_widths contains a non-positive value")
        if not self.bay_spacings:
            out.append("geometry.bay_spacings is empty")
        if any(v <= 0 for v in self.bay_spacings):
            out.append("geometry.bay_spacings contains a non-positive value")
        if self.eave_height <= 0:
            out.append("geometry.eave_height must be positive")
        if self.roof_slope <= 0:
            out.append("geometry.roof_slope must be positive")
        if self.purlin_spacing <= 0:
            out.append("geometry.purlin_spacing must be positive")
        if self.girt_spacing <= 0:
            out.append("geometry.girt_spacing must be positive")
        if self.frame_type is FrameType.CLEAR_SPAN and len(self.span_widths) != 1:
            out.append(
                "geometry.frame_type is clear_span but "
                f"{len(self.span_widths)} span_widths were given"
            )
        if self.frame_type is FrameType.MULTI_SPAN and len(self.span_widths) < 2:
            out.append("geometry.frame_type is multi_span but only one span was given")
        if self.roof_type is RoofType.MONOSLOPE and len(self.span_widths) != 1:
            out.append("geometry.roof_type monoslope supports a single span only")
        if self.slope_deg > 45.0:
            out.append(f"geometry.roof_slope gives {self.slope_deg:.1f} deg, above 45 deg")
        return out


@dataclass(frozen=True)
class Materials:
    """Material strengths, MPa.

    ``Fy_builtup`` is the plate grade for the tapered main frame; the
    connection layer looks up the thickness-dependent value itself, so this is
    the nominal grade only.
    """

    Fy_builtup: float
    Fu_builtup: float
    Fy_cold_formed: float
    Fu_cold_formed: float
    bolt_grade: str
    anchor_grade: str
    fc_concrete: float
    E: float = E_STEEL

    def problems(self) -> list[str]:
        out: list[str] = []
        pairs = (
            ("builtup", self.Fy_builtup, self.Fu_builtup),
            ("cold_formed", self.Fy_cold_formed, self.Fu_cold_formed),
        )
        for name, fy, fu in pairs:
            if fy <= 0:
                out.append(f"materials.Fy_{name} must be positive")
            if fu <= 0:
                out.append(f"materials.Fu_{name} must be positive")
            if fy > 0 and fu > 0 and fu <= fy:
                out.append(f"materials.Fu_{name} ({fu}) must exceed Fy_{name} ({fy})")
        if self.fc_concrete <= 0:
            out.append("materials.fc_concrete must be positive")
        if not self.bolt_grade.strip():
            out.append("materials.bolt_grade is blank")
        if not self.anchor_grade.strip():
            out.append("materials.anchor_grade is blank")
        return out


@dataclass(frozen=True)
class DesignSettings:
    """Code basis and the project's fixed design rules."""

    design_method: DesignMethod = DesignMethod.LRFD
    load_code: LoadCode = LoadCode.ASCE_7_16
    member_code: str = "AISC 360-16"
    cold_formed_code: str = "AISI S100-16"
    connection_capacity_fraction: float = CAPACITY_FRACTION
    #: Deflection limits as span/denominator.  0 disables the check.
    defl_rafter_live: int = 180
    defl_rafter_total: int = 120
    defl_girt_wind: int = 120
    #: Lateral drift limit as height/denominator, serviceability wind.
    drift_frame_wind: int = 100
    #: Serviceability wind load factor applied to the strength-level wind.
    serviceability_wind_factor: float = 0.70
    include_p_delta: bool = True

    def problems(self) -> list[str]:
        out: list[str] = []
        if not (0.0 < self.connection_capacity_fraction <= 1.0):
            out.append(
                "settings.connection_capacity_fraction must lie in (0, 1], got "
                f"{self.connection_capacity_fraction}"
            )
        if not (0.0 < self.serviceability_wind_factor <= 1.0):
            out.append("settings.serviceability_wind_factor must lie in (0, 1]")
        for name in (
            "defl_rafter_live",
            "defl_rafter_total",
            "defl_girt_wind",
            "drift_frame_wind",
        ):
            if getattr(self, name) < 0:
                out.append(f"settings.{name} must not be negative")
        return out


@dataclass(frozen=True)
class ProjectSpec:
    """Everything the engine needs.  The single input to the pipeline."""

    project_name: str
    job_number: str
    site: ProjectSite
    geometry: PEBGeometry
    materials: Materials
    settings: DesignSettings = field(default_factory=DesignSettings)
    schema_version: str = SCHEMA_VERSION

    # -- validation ----------------------------------------------------------

    def problems(self) -> list[str]:
        out: list[str] = []
        if not self.project_name.strip():
            out.append("project_name is blank")
        if not self.job_number.strip():
            out.append("job_number is blank")
        out += self.site.problems()
        out += self.geometry.problems()
        out += self.materials.problems()
        out += self.settings.problems()
        if (
            self.settings.load_code is LoadCode.SBC_301_2018
            and self.site.exposure_category is ExposureCategory.B
        ):
            out.append(
                "SBC 301-2018 projects rarely qualify for Exposure B; confirm "
                "the surface roughness before accepting it"
            )
        return out

    def validate(self) -> "ProjectSpec":
        """Return self, or raise :class:`SpecError` listing every problem."""
        p = self.problems()
        if p:
            raise SpecError(p)
        return self

    @property
    def is_valid(self) -> bool:
        return not self.problems()

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProjectSpec":
        return _from_jsonable(cls, data)

    @classmethod
    def from_json(cls, text: str) -> "ProjectSpec":
        return cls.from_dict(json.loads(text))


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberForces:
    """Design forces at one station.  kN and kN.m, local axes.

    Sign convention: axial positive in compression, to match the AISC
    compression chapter the design layer calls most often.
    """

    P: float = 0.0
    Vy: float = 0.0
    Vz: float = 0.0
    T: float = 0.0
    My: float = 0.0
    Mz: float = 0.0
    station: float = 0.0  # mm from member start


@dataclass(frozen=True)
class MemberCheck:
    """One limit state on one member.

    Named ``MemberCheck`` rather than ``LimitState`` so it cannot collide with
    ``bimsteelauto.connections.core.LimitState``.
    """

    name: str
    reference: str  # e.g. "AISC 360-16 Eq. H1-1a"
    demand: float
    capacity: float
    ratio: float
    unit: str = ""
    note: str = ""

    @property
    def passed(self) -> bool:
        return self.ratio <= 1.0


@dataclass(frozen=True)
class MemberDesign:
    mark: str
    member_type: MemberType
    section_id: str
    length: float  # mm
    governing_combination: str
    forces: MemberForces
    checks: tuple[MemberCheck, ...] = ()
    weight: float = 0.0  # kg
    note: str = ""

    @property
    def utilisation(self) -> float:
        return max((c.ratio for c in self.checks), default=0.0)

    @property
    def governing_check(self) -> MemberCheck | None:
        if not self.checks:
            return None
        return max(self.checks, key=lambda c: c.ratio)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


@dataclass(frozen=True)
class ConnectionDesign:
    """Summary of one connection.

    ``detail`` carries the full ``ConnectionResult`` from the connection layer
    as a plain dict, so this module keeps its zero-dependency promise while the
    calc sheet renderer still gets every limit state.
    """

    mark: str
    connection_type: ConnectionType
    member_marks: tuple[str, ...]
    design_force_basis: str  # "analysis" or "capacity_fraction"
    utilisation: float
    verified: bool
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.utilisation <= 1.0

    @classmethod
    def from_result(
        cls,
        result: Any,
        connection_type: ConnectionType | str,
        member_marks: Sequence[str],
    ) -> "ConnectionDesign":
        """Build from a ``bimsteelauto_connections.core.ConnectionResult``.

        Duck-typed on purpose.  spec.py must not import the connection
        package - Layer 1 stays dependency-free - so this reads the
        attributes it needs and flattens the limit states into ``detail``
        for the calculation sheet.
        """
        basis = "analysis"
        for f_ in getattr(result, "forces", ()):
            crit = getattr(getattr(f_, "criterion", None), "value", "")
            if "capacity" in str(crit):
                basis = "capacity_fraction"
                break

        detail = {
            "kind": getattr(result, "kind", ""),
            "citation": getattr(result, "citation", ""),
            "geometry": dict(getattr(result, "geometry", {}) or {}),
            "selections": dict(getattr(result, "selections", {}) or {}),
            "warnings": list(getattr(result, "warnings", ()) or ()),
            "limit_states": [
                {
                    "name": ls.name,
                    "capacity": ls.capacity,
                    "demand": ls.demand,
                    "utilisation": ls.utilization,
                    "clause": getattr(ls, "clause", ""),
                    "unit": getattr(ls, "unit", "kN"),
                }
                for ls in getattr(result, "limit_states", ())
            ],
            "forces": [
                {
                    "label": getattr(f_, "label", ""),
                    "value": getattr(f_, "value", 0.0),
                    "analysis": getattr(f_, "analysis", 0.0),
                    "capacity_based": getattr(f_, "capacity_based", None),
                    "fraction": getattr(f_, "fraction", None),
                    "unit": getattr(f_, "unit", "kN"),
                }
                for f_ in getattr(result, "forces", ())
            ],
        }

        return cls(
            mark=getattr(result, "mark", ""),
            connection_type=ConnectionType(connection_type),
            member_marks=tuple(member_marks),
            design_force_basis=basis,
            utilisation=float(getattr(result, "utilization", 0.0)),
            verified=bool(getattr(result, "verified", False)),
            detail=detail,
        )


@dataclass(frozen=True)
class LoadCase:
    name: str  # "D", "Lr", "W_A_pos", ...
    kind: str  # "dead", "live", "roof_live", "wind", "seismic", "collateral"
    description: str = ""
    reference: str = ""


@dataclass(frozen=True)
class LoadCombination:
    name: str
    factors: dict[str, float]
    reference: str = ""


@dataclass(frozen=True)
class LoadSummary:
    cases: tuple[LoadCase, ...] = ()
    combinations: tuple[LoadCombination, ...] = ()
    #: Frame-line line loads, kN/m, keyed by "<frame_mark>/<case_name>/<zone>".
    frame_line_loads: dict[str, float] = field(default_factory=dict)
    #: qh at mean roof height, kN/m^2.
    qh: float = 0.0
    #: End-zone width a, mm (ASCE 7-16 Fig. 28.3-1 note 9).
    end_zone_a: float = 0.0


@dataclass(frozen=True)
class BracingArrangement:
    """Where the longitudinal bracing IS, as opposed to what it carries.

    The forces were always on the result; the LAYOUT was not, and the
    FreeCAD adapter cannot draw a brace without it. Before this existed the
    adapter drew the panels in the end bays and warned that it was guessing
    - which is honest, and still wrong on any building braced elsewhere.

    This is an ARRANGEMENT, not a derived quantity: the engineer chose it,
    the engine used it, and the model has to show the same one. Keeping it
    beside the design is what stops the drawing and the calculation
    disagreeing about where the bracing goes.
    """

    #: Bay indices, 0-based from the left, that carry bracing.
    braced_bays: tuple[int, ...] = ()
    #: Sidewalls that actually carry wall bracing - 1 or 2.
    n_braced_walls: int = 2
    #: X panels per roof slope. 1 is a single X over the whole slope.
    roof_panel_splits: int = 1

    @property
    def n_braced(self) -> int:
        return len(self.braced_bays)

    @property
    def is_stated(self) -> bool:
        """False on a result that predates this field, or never set it.

        Callers must check it rather than treating an empty tuple as "no
        bracing" - a building with no longitudinal bracing has no load path
        for endwall wind, which is a different thing from a result that did
        not record where the bracing went.
        """
        return bool(self.braced_bays)


@dataclass(frozen=True)
class SupportReaction:
    """Factored reaction at one base, kN and kN.m."""

    node: str
    combination: str
    Fx: float = 0.0
    Fy: float = 0.0
    Fz: float = 0.0
    Mx: float = 0.0
    My: float = 0.0
    Mz: float = 0.0


@dataclass(frozen=True)
class DesignResult:
    """Everything the adapter and the calc sheet need.  The single output."""

    spec: ProjectSpec
    loads: LoadSummary = field(default_factory=LoadSummary)
    members: tuple[MemberDesign, ...] = ()
    connections: tuple[ConnectionDesign, ...] = ()
    reactions: tuple[SupportReaction, ...] = ()
    #: Where the longitudinal bracing is. ``None`` on a result written
    #: before schema 1.1.0, which is why the adapter must check
    #: ``is_stated`` rather than assume.
    bracing: BracingArrangement | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    #: Names of design relations whose VERIFIED flag is still False.
    unverified: tuple[str, ...] = ()
    #: Things the engineer assumed because they are outside this scope of
    #: work - e.g. a pedestal size assumed only so the anchor check has a
    #: member to sit in, where the foundation is a separate civil package.
    #: These PRINT ON THE SHEET.  An assumption nobody can see is a lie.
    assumptions: tuple[str, ...] = ()
    #: Checks this design did NOT perform, collected from every module.
    #: Distinct from ``unverified``: a gap is a check that is absent, not a
    #: formula that is unchecked.  Also prints on the sheet.
    coverage_gaps: tuple[str, ...] = ()
    engine_version: str = ""
    generated_at: str = ""
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.generated_at:
            object.__setattr__(
                self, "generated_at", datetime.now(timezone.utc).isoformat(timespec="seconds")
            )

    # -- roll-ups ------------------------------------------------------------

    @property
    def max_member_utilisation(self) -> float:
        return max((m.utilisation for m in self.members), default=0.0)

    @property
    def max_connection_utilisation(self) -> float:
        return max((c.utilisation for c in self.connections), default=0.0)

    @property
    def max_utilisation(self) -> float:
        return max(self.max_member_utilisation, self.max_connection_utilisation)

    @property
    def failed_members(self) -> tuple[MemberDesign, ...]:
        return tuple(m for m in self.members if not m.passed)

    @property
    def failed_connections(self) -> tuple[ConnectionDesign, ...]:
        return tuple(c for c in self.connections if not c.passed)

    @property
    def total_weight(self) -> float:
        """Sum of member weights, kg."""
        return sum(m.weight for m in self.members)

    @property
    def passed(self) -> bool:
        """Every check satisfied and no errors.  Says nothing about issue."""
        return (
            not self.errors
            and not self.failed_members
            and not self.failed_connections
        )

    def ready_for_issue(self) -> bool:
        """False while anything is unverified.

        Mirrors the rule already enforced in the connection layer: a sheet is
        NOT FOR ISSUE while any VERIFIED flag is unset, however good the
        numbers look.
        """
        return (
            self.passed
            and not self.unverified
            and bool(self.members)
            and self.spec.is_valid
        )

    def issue_blockers(self) -> list[str]:
        """Human-readable reasons ``ready_for_issue`` is False."""
        out: list[str] = []
        out += [f"spec: {p}" for p in self.spec.problems()]
        out += [f"error: {e}" for e in self.errors]
        if not self.members:
            out.append("no members were designed")
        for m in self.failed_members:
            gc = m.governing_check
            out.append(
                f"member {m.mark} fails {gc.name if gc else 'a check'} "
                f"at ratio {m.utilisation:.3f}"
            )
        for c in self.failed_connections:
            out.append(f"connection {c.mark} fails at ratio {c.utilisation:.3f}")
        out += [f"unverified: {u}" for u in self.unverified]
        return out

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return _to_jsonable(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DesignResult":
        return _from_jsonable(cls, data)

    @classmethod
    def from_json(cls, text: str) -> "DesignResult":
        return cls.from_dict(json.loads(text))


# ---------------------------------------------------------------------------
# JSON round-trip helpers
# ---------------------------------------------------------------------------


def _to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Mapping):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _unwrap_optional(tp: Any) -> Any:
    """Return T for ``T | None``; otherwise return tp unchanged."""
    import typing

    origin = typing.get_origin(tp)
    if origin is None:
        return tp
    args = [a for a in typing.get_args(tp) if a is not type(None)]  # noqa: E721
    import types as _types

    if origin in (typing.Union, getattr(_types, "UnionType", typing.Union)) and len(args) == 1:
        return args[0]
    return tp


def _from_jsonable(cls: Any, data: Any) -> Any:
    """Rebuild a dataclass tree from plain JSON types.

    Resolves string annotations itself so that ``from __future__ import
    annotations`` at the top of this module does not defeat it.
    """
    import typing

    if data is None:
        return None

    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        raw = data[f.name]
        tp = _unwrap_optional(hints.get(f.name, Any))
        origin = typing.get_origin(tp)
        args = typing.get_args(tp)

        if isinstance(tp, type) and issubclass(tp, Enum):
            kwargs[f.name] = tp(raw)
        elif isinstance(tp, type) and is_dataclass(tp):
            kwargs[f.name] = _from_jsonable(tp, raw)
        elif origin in (tuple, list):
            inner = args[0] if args else Any
            if isinstance(inner, type) and is_dataclass(inner):
                seq = [_from_jsonable(inner, v) for v in raw]
            elif isinstance(inner, type) and issubclass(inner, Enum):
                seq = [inner(v) for v in raw]
            else:
                seq = list(raw)
            kwargs[f.name] = tuple(seq) if origin is tuple else seq
        else:
            kwargs[f.name] = raw
    return cls(**kwargs)
