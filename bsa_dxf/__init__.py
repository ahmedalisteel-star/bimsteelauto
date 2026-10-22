"""
bsa_dxf - drawing-office DXF output for BIMSteelAuto.

Reads ``design_result.json`` and writes real DXF sheets: border, title block,
grid, dimensions, member marks, taper schedule and connection details.

This package imports **ezdxf only**.  It does not import FreeCAD and it does
not import the design engine, so a design result can be drawn on a machine
that has neither - which is the machine a drawing office runs.  The contract
between the engine and this package is the JSON file and nothing else.

Usage::

    from bsa_dxf import write_from_file
    out = write_from_file("design_result.json", "out/dxf")
    print(out.ok, out.paths(), out.warnings)

or from a shell::

    python -m bsa_dxf design_result.json -o out/dxf --sheet A1
"""

from .geom import (
    Frame,
    FrameError,
    Member,
    Section,
    frame_from_result,
    parse_section,
)
from .sheet import TitleBlock, stamp_for
from .standards import LAYERS, SHEET_SIZES, Standard
from .views import SheetResult, frame_elevation, pick_scale
from .writer import (
    DxfSet,
    title_block_from_result,
    verify,
    write_from_file,
    write_set,
)

__all__ = [
    "DxfSet",
    "Frame",
    "FrameError",
    "LAYERS",
    "Member",
    "SHEET_SIZES",
    "Section",
    "SheetResult",
    "Standard",
    "TitleBlock",
    "frame_elevation",
    "frame_from_result",
    "parse_section",
    "pick_scale",
    "stamp_for",
    "title_block_from_result",
    "verify",
    "write_from_file",
    "write_set",
]

__version__ = "0.1.0"
