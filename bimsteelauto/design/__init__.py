"""
bimsteelauto.design
===================

AISC 360-16 member checks, over ``pyaisc360``.

Everything crossing into the imperial library goes through
:mod:`bimsteelauto.design.units`; nothing else in the package converts.
"""

from .purlins import (  # noqa: F401
    CFShape,
    ColdFormedSection,
    ElasticBuckling,
    PurlinBracing,
    PurlinError,
    PurlinLine,
    PurlinLoadCase,
    check_purlin,
    continuous_beam_forces,
)
from .members import (  # noqa: F401
    COVERAGE_GAPS,
    VERIFIED,
    BracingSpec,
    DesignError,
    MemberDesignInput,
    cb_from_moments,
    check_member,
    station_utilisations,
)

__all__ = [
    "VERIFIED",
    "COVERAGE_GAPS",
    "DesignError",
    "BracingSpec",
    "MemberDesignInput",
    "cb_from_moments",
    "check_member",
    "station_utilisations",
    # cold-formed, AISI S100-16
    "CFShape",
    "ColdFormedSection",
    "ElasticBuckling",
    "PurlinBracing",
    "PurlinLine",
    "PurlinLoadCase",
    "PurlinError",
    "check_purlin",
    "continuous_beam_forces",
]
