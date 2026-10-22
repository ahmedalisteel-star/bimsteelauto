"""
bimsteelauto.loads.gravity
==========================

Dead and roof live load for a PEB, in kN/m^2 of the surface each one is
actually defined on.

The point of this module
------------------------
Until it existed, every load in the pipeline was a number somebody typed.
A frame checked against a typed number is not a design; it is arithmetic
with a plausible input.  This module is where a roof load stops being an
assumption and becomes a build-up with a line per component that a reviewer
can add up by hand.

The surface a load is defined on
--------------------------------
This is the trap the analysis module already had to split ``GLOBAL_Y`` from
``GLOBAL_Y_PROJECTED`` for, and it starts here:

    Sheeting, insulation, purlins, services  -> per m^2 of SLOPING roof
    Roof live load Lr (ASCE 7-16 Sect. 4.8)  -> per m^2 of HORIZONTAL plan
    Snow (Chapter 7)                         -> per m^2 of HORIZONTAL plan

So a dead load and a live load of the same numerical value are NOT the same
load on a sloping roof.  Every quantity in this module carries which surface
it belongs to in its type, and :mod:`bimsteelauto.loads.frame` refuses to
apply one as the other.

Units: kN/m^2 for area loads, kN/m for line loads, mm for geometry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..spec import PEBGeometry

__all__ = [
    "VERIFIED",
    "LoadError",
    "Surface",
    "AreaLoad",
    "DeadLoadBuildUp",
    "RoofLiveLoad",
    "roof_live_load",
    "CLADDING_CATALOGUE",
]


class LoadError(ValueError):
    """An impossible or missing load input."""


#: CLOSED 2026-09-01 against the ``asce7-loads`` skill's ``gravity.py`` - a
#: separately written ASCE 7-16 implementation - plus longhand arithmetic for
#: the values the Specification prints outright.
#:
#:     roof_live_R1      42 combinations of area and slope, exact to 1e-9
#:     roof_live_R2      same sweep; F = 12 x slope is exercised, since the
#:                       reference takes F and this package takes the slope
#:     roof_live_bounds  0.96 and 0.58 kN/m2 confirmed as the metric forms of
#:                       20 and 12 psf; 18.58 and 55.74 m2 as 200 and 600 ft2
#:     dead_build_up     arithmetic only - it is a sum, and the test pins
#:                       that every component including the zeros is listed
#:     surface_discipline  the sloping/projected ratio is exactly 1/cos(theta)
#:                       to 1e-12, and the 1:10 and 1:2.5 values (1.004988 and
#:                       1.077033) are pinned by hand
VERIFIED: dict[str, bool] = {
    "roof_live_R1": True,
    "roof_live_R2": True,
    "roof_live_bounds": True,
    "dead_build_up": True,
    "surface_discipline": True,
}


class Surface(str, Enum):
    """Which area a load is defined per square metre of.

    Getting this wrong scales every downstream number by ``cos(theta)`` -
    0.5% on a 1:10 roof, 8% on 1:2.5 - silently and in the unconservative
    direction for a dead load applied as if it were projected.
    """

    SLOPING = "sloping"      # per m^2 of the actual roof surface
    PROJECTED = "projected"  # per m^2 of horizontal plan projection


@dataclass(frozen=True)
class AreaLoad:
    """A pressure, and the surface it is defined on.  kN/m^2."""

    value: float
    surface: Surface
    label: str = ""
    reference: str = ""

    def on_projected(self, slope: float) -> float:
        """Restated per m^2 of horizontal projection.

        A sloping-surface load covers ``1/cos(theta)`` square metres of roof
        for every square metre of plan, so it gets LARGER when projected.
        """
        if self.surface is Surface.PROJECTED:
            return self.value
        return self.value / math.cos(math.atan(slope))

    def on_sloping(self, slope: float) -> float:
        """Restated per m^2 of the actual sloping roof surface."""
        if self.surface is Surface.SLOPING:
            return self.value
        return self.value * math.cos(math.atan(slope))


#: Typical self-weights, kN/m^2 of sloping surface.  A CATALOGUE, not a
#: default: the caller picks a line and the sheet prints which one.  Nothing
#: in this module reaches into it on its own.
CLADDING_CATALOGUE: dict[str, tuple[float, str]] = {
    "single skin 0.5mm profiled steel": (0.05, "0.5 mm steel sheet, ~4.5 kg/m2"),
    "single skin 0.7mm profiled steel": (0.07, "0.7 mm steel sheet, ~6.3 kg/m2"),
    "sandwich panel 50mm PUF": (0.12, "50 mm PUF core, both skins 0.5 mm"),
    "sandwich panel 75mm PUF": (0.14, "75 mm PUF core, both skins 0.5 mm"),
    "sandwich panel 100mm PUF": (0.16, "100 mm PUF core, both skins 0.5 mm"),
    "single skin + 50mm glasswool + liner": (0.11, "sheet, insulation, liner"),
}


@dataclass(frozen=True)
class DeadLoadBuildUp:
    """Roof dead load, component by component, kN/m^2 of SLOPING surface.

    Nothing here has a default that could become a design.  A zero means the
    engineer said zero; it never means "not considered", because the sheet
    prints every line including the zeros.

    ``frame_self_weight`` is deliberately NOT included: the analysis applies
    the main frame's own weight from the section properties, and adding it
    here as well would double-count it.  Purlins and girts ARE included,
    because they are not in the frame model.
    """

    sheeting: float
    insulation: float = 0.0
    liner: float = 0.0
    purlins: float = 0.0
    services: float = 0.0
    collateral: float = 0.0
    sheeting_description: str = ""

    @classmethod
    def from_catalogue(
        cls,
        cladding: str,
        *,
        purlins: float,
        insulation: float = 0.0,
        liner: float = 0.0,
        services: float = 0.0,
        collateral: float = 0.0,
    ) -> "DeadLoadBuildUp":
        """Pick a sheeting line from :data:`CLADDING_CATALOGUE` by name."""
        if cladding not in CLADDING_CATALOGUE:
            raise LoadError(
                f"{cladding!r} is not in the cladding catalogue. Known: "
                + ", ".join(sorted(CLADDING_CATALOGUE))
            )
        w, desc = CLADDING_CATALOGUE[cladding]
        return cls(
            sheeting=w,
            insulation=insulation,
            liner=liner,
            purlins=purlins,
            services=services,
            collateral=collateral,
            sheeting_description=f"{cladding} ({desc})",
        )

    def __post_init__(self) -> None:
        for name in ("sheeting", "insulation", "liner", "purlins", "services",
                     "collateral"):
            v = getattr(self, name)
            if v < 0:
                raise LoadError(f"dead load component {name} is negative: {v}")
        if self.sheeting <= 0:
            raise LoadError("sheeting weight must be positive")

    @property
    def components(self) -> tuple[tuple[str, float, str], ...]:
        """``(label, kN/m^2, note)`` for every line, zeros included."""
        return (
            ("Sheeting", self.sheeting, self.sheeting_description),
            ("Insulation", self.insulation, ""),
            ("Liner", self.liner, ""),
            ("Purlins / girts", self.purlins, "secondary steel, not in the frame model"),
            ("Services", self.services, "lighting, sprinklers, cable tray"),
            ("Collateral", self.collateral, "future / owner-specified allowance"),
        )

    @property
    def total(self) -> float:
        """kN/m^2 of sloping surface."""
        return sum(v for _, v, _ in self.components)

    def as_area_load(self) -> AreaLoad:
        return AreaLoad(
            value=self.total,
            surface=Surface.SLOPING,
            label="Roof dead load (excluding main frame self weight)",
            reference="Project build-up",
        )


# ---------------------------------------------------------------------------
# Roof live load - ASCE 7-16 Sect. 4.8
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoofLiveLoad:
    """Result of Eq. 4.8-1, with every intermediate value kept."""

    Lr: float
    Lo: float
    R1: float
    R2: float
    At: float
    F: float
    reference: str = "ASCE 7-16 Eq. 4.8-1 (Sect. 4.8.2)"

    def as_area_load(self) -> AreaLoad:
        return AreaLoad(
            value=self.Lr,
            surface=Surface.PROJECTED,
            label="Roof live load Lr",
            reference=self.reference,
        )

    @property
    def steps(self) -> tuple[tuple[str, float, str], ...]:
        return (
            ("Lo (Table 4.3-1)", self.Lo, "kN/m2"),
            ("At tributary roof area", self.At, "m2"),
            ("R1 (area reduction)", self.R1, "-"),
            ("F (slope, rise per 12 of run)", self.F, "-"),
            ("R2 (slope reduction)", self.R2, "-"),
            ("Lr = Lo x R1 x R2", self.Lr, "kN/m2"),
        )


#: ASCE 7-16 Table 4.3-1, ordinary flat/pitched/curved roof.
LO_ORDINARY_ROOF = 0.96      # kN/m^2  (20 psf)
LR_MIN = 0.58                # kN/m^2  (12 psf)
LR_MAX = 0.96


def roof_live_load(
    tributary_area: float,
    slope: float,
    *,
    Lo: float = LO_ORDINARY_ROOF,
) -> RoofLiveLoad:
    """``Lr = Lo R1 R2``, ASCE 7-16 Eq. 4.8-1.

    Parameters
    ----------
    tributary_area:
        ``At``, the tributary roof area in m^2 supported by the member,
        measured on the HORIZONTAL PROJECTION.
    slope:
        Roof slope as rise per unit run, e.g. 0.10 for 1:10.  Converted here
        to ``F``, which the Specification defines as rise per 12 units of
        run - a units trap of its own: a 1:10 roof is F = 1.2, not F = 0.10.
    Lo:
        Unreduced roof live load.  0.96 kN/m^2 for an ordinary roof.

    The result is bounded to 0.58 <= Lr <= 0.96 kN/m^2 per Sect. 4.8.2.
    """
    if tributary_area < 0:
        raise LoadError(f"tributary area must not be negative: {tributary_area}")
    if slope < 0:
        raise LoadError(f"slope must not be negative: {slope}")
    if Lo <= 0:
        raise LoadError(f"Lo must be positive: {Lo}")

    At = tributary_area
    if At <= 18.58:                       # 200 ft^2
        R1 = 1.0
    elif At < 55.74:                      # 600 ft^2
        R1 = 1.2 - 0.011 * At
    else:
        R1 = 0.6

    # F is the rise per 12 units of run, NOT the slope itself.
    F = slope * 12.0
    if F <= 4.0:
        R2 = 1.0
    elif F < 12.0:
        R2 = 1.2 - 0.05 * F
    else:
        R2 = 0.6

    Lr = max(LR_MIN, min(LR_MAX, Lo * R1 * R2))
    return RoofLiveLoad(Lr=Lr, Lo=Lo, R1=R1, R2=R2, At=At, F=F)


def frame_tributary_area(geometry: PEBGeometry, frame_index: int) -> float:
    """Plan tributary roof area of one frame line, m^2.

    Width times the frame's own tributary bay width, both on the horizontal
    projection - which is what ``At`` in Sect. 4.8.2 means.
    """
    widths = geometry.tributary_widths()
    if not 0 <= frame_index < len(widths):
        raise LoadError(
            f"frame_index {frame_index} outside 0..{len(widths) - 1}"
        )
    return geometry.width * widths[frame_index] * 1e-6
