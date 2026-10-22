"""
BIMSteelAutoWB - the FreeCAD workbench.

FreeCAD imports this file at start-up for every workbench it finds in
``Mod\\``, so it must stay CHEAP: no engine imports, no geometry, nothing
that touches the disk.  Everything real happens inside a command's
``Activated``, which only runs when the button is pressed.

That is not a style preference.  An import error in here does not fail
loudly - it makes the workbench vanish from the selector with the reason
buried in the report view, which is a bad half-hour for anyone who has not
seen it before.

Install
-------
Put (or link) the ``BIMSteelAutoWB`` folder in::

    %APPDATA%\\FreeCAD\\Mod\\

then restart FreeCAD and pick "BIMSteelAuto" from the workbench selector.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui

def _wb_dir() -> str:
    """Folder of this workbench.

    FreeCAD runs InitGui.py with exec(), where ``__file__`` is not defined,
    so fall back to the Mod folder (resolving the symlink) or sys.path.
    """
    try:
        return os.path.dirname(os.path.realpath(__file__))
    except NameError:
        pass
    for base in (App.getUserAppDataDir(), App.getResourceDir()):
        cand = os.path.join(base, "Mod", "BIMSteelAutoWB")
        if os.path.isdir(cand):
            return os.path.realpath(cand)
    import sys
    for p in sys.path:
        if os.path.basename(os.path.normpath(p)) == "BIMSteelAutoWB":
            return os.path.realpath(p)
    return ""


WB_DIR = _wb_dir()
ICON_DIR = os.path.join(WB_DIR, "resources", "icons")

#: Preference group, shared with the standalone macro so the two remember
#: the same last-used file.
PARAM = "User parameter:BaseApp/Preferences/Mod/BIMSteelAuto"


def _icon(name: str) -> str:
    path = os.path.join(ICON_DIR, name)
    return path if os.path.exists(path) else ""


class BIMSteelAutoWorkbench(Gui.Workbench):
    """PEB design to BIM, IFC, MTO and drawings."""

    MenuText = "BIMSteelAuto"
    ToolTip = (
        "Build a PEB model, IFC, member schedule and 2D drawings from an "
        "engine design result"
    )
    Icon = _icon("bimsteelauto.svg")

    def Initialize(self):
        """Register the commands.  Called once, when the WB is first shown."""
        # Imported HERE, not at module scope: a failure now shows up with
        # the workbench selected rather than silently removing it.
        from bsa_commands import COMMANDS, register_commands

        register_commands()
        self.appendToolbar("BIMSteelAuto", list(COMMANDS))
        self.appendMenu("BIMSteelAuto", list(COMMANDS))

    def Activated(self):
        import FreeCAD as App   # module globals of InitGui are not reliable here
        App.Console.PrintMessage(
            "BIMSteelAuto: pick 'Build model' and choose the engine's "
            "design_result.json\n"
        )

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


# The workbench folder itself has to be importable for ``bsa_commands``.
import sys                                                   # noqa: E402

if WB_DIR not in sys.path:
    sys.path.insert(0, WB_DIR)

Gui.addWorkbench(BIMSteelAutoWorkbench())
