r"""BIMSteelAutoWB - non-GUI init.

FreeCAD imports this for every module in ``Mod\`` at start-up, GUI or not,
so it must do as little as possible. The workbench itself is registered in
``InitGui.py``, which only runs when there is a GUI.

Deliberately empty of imports: anything here runs on every FreeCADCmd
invocation too, including the headless test runs.
"""
