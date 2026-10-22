"""
bimsteelauto — the engine layer of the BIMSteelAuto PEB pipeline.
=================================================================

Pure Python.  Nothing in this package imports FreeCAD, PySide6, vtk or
torch, so it runs identically in FreeCAD's bundled interpreter, in a bare
pytest run, and under FreeCADCmd.

    ProjectSpec  ->  ENGINE  ->  DesignResult  ->  ADAPTER  ->  FreeCAD
       spec.py       loads/           spec.py      bimsteelauto_fc/
                     analysis/
                     design/
                     connections  (bimsteelauto_connections, v0.7.0)
                     report/

Layer rule
----------
Nothing here knows a FreeCAD document exists.  The adapter reads a
``DesignResult`` and builds geometry from it; the engine never reaches the
other way.  ``tests/test_spec.py`` enforces this by parsing spec.py's own
imports.
"""

from .sections import (  # noqa: F401
    SectionError,
    TaperedMember,
    WeldedI,
    parse_mark,
)
from .spec import (  # noqa: F401
    CAPACITY_FRACTION,
    SCHEMA_VERSION,
    DesignResult,
    ProjectSpec,
    SpecError,
)

__version__ = "0.9.0.dev0"

__all__ = [
    # contract
    "ProjectSpec",
    "DesignResult",
    "SpecError",
    "CAPACITY_FRACTION",
    "SCHEMA_VERSION",
    # sections
    "WeldedI",
    "TaperedMember",
    "SectionError",
    "parse_mark",
    "__version__",
]
