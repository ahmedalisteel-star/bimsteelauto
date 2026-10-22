"""
bimsteelauto.analysis
=====================

Frame analysis for the PEB pipeline, on top of PyNiteFEA.

    PEBGeometry + FrameSections  ->  FrameLayout  ->  FrameAnalysis  ->  Envelope
                                 ->  SpaceLayout  ->  SpaceAnalysis  ->  SpaceEnvelope

Two models of one building.  ``FrameAnalysis`` is the plane frame the
member design is done on - verified, second-order, and the right model for
a transverse frame in a braced building.  ``SpaceAnalysis`` is the whole
building in 3D: every frame line, the purlins, girts, struts and
tension-only bracing, with all six force components at every station and
nothing artificially restrained.  It is ADDITIVE.  The first thing it is
asked to do is agree with the plane frame, and it does, to 0.08%.

The layout step is pure geometry and has no solver in it, so the node and
member arrangement can be tested without running an analysis.  The analysis
step owns every call into PyNite and is the only place unit conversion
happens.

Units at this module's boundary are the package convention - kN, kN.m, mm.
PyNite is unit-agnostic and is driven internally in N and mm.
"""

from .frame import (  # noqa: F401
    VERIFIED,
    AnalysisError,
    BaseFixity,
    Envelope,
    FrameAnalysis,
    FrameLayout,
    FrameMember,
    FrameSections,
    LoadDirection,
    MemberSectionSpec,
    ModelSettings,
    StationForces,
    build_layout,
)
from .space import (  # noqa: F401
    SectionProps,
    SpaceAnalysis,
    SpaceEnvelope,
    SpaceError,
    SpaceLayout,
    SpaceMember,
    SpaceStation,
    build_space_from_run,
    build_space_layout,
)
from .results import (  # noqa: F401
    ContourError,
    ContourSet,
    MemberContour,
    Quantity,
    build_contours,
    build_space_contours,
    load_contours,
    write_contours,
)

__all__ = [
    "VERIFIED",
    "AnalysisError",
    "BaseFixity",
    "Envelope",
    "FrameAnalysis",
    "FrameLayout",
    "FrameMember",
    "FrameSections",
    "LoadDirection",
    "MemberSectionSpec",
    "ModelSettings",
    "StationForces",
    "build_layout",
    # the 3D building - additive, and NOT a replacement for the plane frame
    "SectionProps",
    "SpaceAnalysis",
    "SpaceEnvelope",
    "SpaceError",
    "SpaceLayout",
    "SpaceMember",
    "SpaceStation",
    "build_space_from_run",
    "build_space_layout",
    # post-processing - the result picture
    "ContourError",
    "ContourSet",
    "MemberContour",
    "Quantity",
    "build_contours",
    "build_space_contours",
    "load_contours",
    "write_contours",
]
