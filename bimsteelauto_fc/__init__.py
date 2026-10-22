"""
bimsteelauto_fc - Layer 2, the FreeCAD adapter.

    DesignResult  ->  builder.py   ->  FreeCAD document (the BIM model)
                  ->  schedule.py  ->  MTO / member schedule
                  ->  ifc.py       ->  IFC4
                  ->  drawings.py  ->  2D fabrication drawings

This is the ONLY layer that imports FreeCAD.  The engine below it does not
know a FreeCAD document exists, and nothing here reaches back into the
engine for anything the ``DesignResult`` does not already carry - the one
exception being ``bimsteelauto.analysis.build_layout``, which is pure
geometry and is called to place nodes rather than to re-derive a design.

Import it inside FreeCAD, or under ``freecadcmd``:

    freecadcmd -c "import bimsteelauto_fc"
"""

from .builder import (  # noqa: F401
    BuildError,
    BuildReport,
    build_model,
    frame_line_positions,
)
from .contours import ContourDraw, draw_contours  # noqa: F401
from .run import AdapterRun, build_all, print_report  # noqa: F401
from .schedule import (  # noqa: F401
    ScheduleRow,
    member_schedule,
    write_schedule_csv,
    write_schedule_sheet,
)

__all__ = [
    "build_all",
    "draw_contours",
    "ContourDraw",
    "AdapterRun",
    "print_report",
    "build_model",
    "BuildReport",
    "BuildError",
    "frame_line_positions",
    "member_schedule",
    "ScheduleRow",
    "write_schedule_csv",
    "write_schedule_sheet",
]

__version__ = "0.1.0.dev0"
