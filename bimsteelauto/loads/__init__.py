"""
bimsteelauto.loads
==================

Where a load stops being a number somebody typed.

    gravity       dead load build-up, roof live load (ASCE 7-16 Sect. 4.8)
    combinations  ASCE 7-16 Sect. 2.3.1 LRFD / 2.4.1 ASD factor sets
    frame         area loads -> frame line loads -> the analysis model
    wind          ASCE 7-16 Ch. 28 Part 1, the envelope procedure (the FRAME)
    cladding      ASCE 7-16 Ch. 30 Part 1, components and cladding
                  (purlins, girts and sheeting - a different, larger load)

Wind plugs in at the same boundary as gravity: it produces zone pressures,
which become the same :class:`~bimsteelauto.loads.frame.FrameLoad` objects
the gravity loads do, and go into the analysis through the same
:func:`~bimsteelauto.loads.frame.apply_loads`.
"""

from .combinations import (  # noqa: F401
    RoofCompanion,
    asd_combinations,
    combinations_for,
    lrfd_combinations,
)
from .frame import (  # noqa: F401
    AppliedLoads,
    FrameLoad,
    apply_loads,
    build_load_summary,
    gravity_loads_for_frame,
    line_load,
    tributary_width,
)
from .cladding import (  # noqa: F401
    GCP_ROOF_LOW_SLOPE,
    GCP_WALL,
    MIN_CC_PRESSURE,
    CCZone,
    CladdingPressure,
    cladding_pressure,
    effective_wind_area,
    gcp,
    girt_pressures,
    governing_pressure,
    purlin_pressures,
)
from .gravity import (  # noqa: F401
    CLADDING_CATALOGUE,
    AreaLoad,
    DeadLoadBuildUp,
    LoadError,
    RoofLiveLoad,
    Surface,
    frame_tributary_area,
    roof_live_load,
)
from .wind import (  # noqa: F401
    GCPF_LOAD_CASE_A,
    GCPF_LOAD_CASE_B,
    MIN_ROOF_PRESSURE,
    MIN_WALL_PRESSURE,
    MinimumWindLoad,
    WindResult,
    ZoneRole,
    end_zone_width,
    envelope_pressures,
    frame_is_in_end_zone,
    gcpf_load_case_a,
    gcpf_load_case_b,
    longitudinal_endwall_pressure,
    minimum_design_wind_load,
    net_horizontal_pressures,
    velocity_pressure,
    velocity_pressure_coefficient,
    wind_case_names,
    wind_loads_for_frame,
    zone_map_for_frame,
)

__all__ = [
    "AreaLoad", "Surface", "DeadLoadBuildUp", "RoofLiveLoad",
    "roof_live_load", "frame_tributary_area", "CLADDING_CATALOGUE",
    "LoadError",
    "RoofCompanion", "lrfd_combinations", "asd_combinations",
    "combinations_for",
    "FrameLoad", "AppliedLoads", "tributary_width", "line_load", "gravity_loads_for_frame",
    "apply_loads", "build_load_summary",
    # wind, ASCE 7-16 Ch. 28 Part 1
    "WindResult", "ZoneRole", "envelope_pressures", "wind_case_names",
    "velocity_pressure", "velocity_pressure_coefficient", "end_zone_width",
    "gcpf_load_case_a", "gcpf_load_case_b", "GCPF_LOAD_CASE_A",
    "GCPF_LOAD_CASE_B", "frame_is_in_end_zone", "zone_map_for_frame",
    "wind_loads_for_frame",
    "MinimumWindLoad", "MIN_WALL_PRESSURE", "MIN_ROOF_PRESSURE",
    "net_horizontal_pressures", "minimum_design_wind_load",
    "longitudinal_endwall_pressure",
    # components and cladding, ASCE 7-16 Ch. 30 Part 1
    "CCZone", "CladdingPressure", "GCP_WALL", "GCP_ROOF_LOW_SLOPE",
    "MIN_CC_PRESSURE", "effective_wind_area", "gcp", "cladding_pressure",
    "governing_pressure", "purlin_pressures", "girt_pressures",
]
