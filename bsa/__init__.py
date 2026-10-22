"""
bsa - the front door.

BIMSteelAuto's engine and its FreeCAD adapter are both libraries, and both
are driven today by editing a Python file (``new_project.py``) or by
pressing a button in the GUI.  This package is the third face: a command
line, so that a job can be run by a person, a batch file, a scheduler or
an AI agent without any of them knowing Python or opening FreeCAD.

    python -m bsa doctor            is this PC able to run it?
    python -m bsa new job.csv       write a starter input sheet
    python -m bsa design job.csv    CSV -> FEA -> AISC -> calc sheet
    python -m bsa export <job dir>  design_result.json -> CSV

Two environments, one contract
------------------------------
This is the fact the whole package is shaped around, and ``doctor`` exists
to make it visible:

    ENGINE   any Python 3.11+   PyNiteFEA, pyaisc360, numpy, scipy
             imports NO FreeCAD

    ADAPTER  freecadcmd only    no pip packages at all - DXF comes from
                                TechDraw, IFC from the bundled nativeifc

    between them:  design_result.json  (schema 1.2.0)

So ``design`` runs anywhere and ``build`` runs inside FreeCAD, and the JSON
is what crosses.  That boundary is not a workaround - it is what lets the
analysis be tested without a GUI and the model be built without numpy.

What this package will NOT do
-----------------------------
It inherits the engine's rule: a number it cannot justify is refused with a
message, never filled in with a guess.  A missing cell is an error naming
the row.  An unknown key is an error rather than a silent no-op, because a
typo that is ignored is a value that is wrong.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
