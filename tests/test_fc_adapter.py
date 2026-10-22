"""Layer 2 - the FreeCAD adapter - inside the main test suite.

The adapter's own checks cannot run under this pytest: they need FreeCAD,
and FreeCAD's bundled interpreter is a different Python that has no pytest
in it.  So the checks live in ``bimsteelauto_fc/selftest.py`` and this file
SHELLS OUT to ``freecadcmd`` and asserts they passed.

That indirection is worth being explicit about, because a test that
silently skips is worse than no test.  If FreeCAD is not installed this
skips and says so; if it is installed the checks really run, and a failure
inside FreeCAD fails the build here with its output attached.

The tests that do NOT need FreeCAD - the layer rule, and whether the JSON
contract carries enough to rebuild geometry from - run here directly.
"""

from __future__ import annotations

import ast
import os
import pathlib
import subprocess

import pytest

PEB = pathlib.Path(__file__).resolve().parent.parent
FC_PKG = PEB / "bimsteelauto_fc"
WB = PEB.parent / "BIMSteelAutoWB"

#: Where FreeCAD lives on a Windows install.  Extend rather than replace -
#: a machine that puts it elsewhere should still find it.
_CANDIDATES = (
    r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe",
    r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe",
    r"C:\Program Files\FreeCAD\bin\freecadcmd.exe",
    "/usr/bin/freecadcmd",
    "/usr/local/bin/freecadcmd",
)


def _freecadcmd() -> str | None:
    env = os.environ.get("FREECADCMD")
    if env and os.path.exists(env):
        return env
    for p in _CANDIDATES:
        if os.path.exists(p):
            return p
    import shutil

    return shutil.which("freecadcmd") or shutil.which("FreeCADCmd")


# ---------------------------------------------------------------------
# The layer rule - these need no FreeCAD
# ---------------------------------------------------------------------

def test_the_adapter_package_exists() -> None:
    assert FC_PKG.is_dir()
    for f in ("builder.py", "ifc.py", "drawings.py", "schedule.py",
              "run.py", "selftest.py", "__init__.py"):
        assert (FC_PKG / f).is_file(), f


def test_layer_1_never_imports_freecad() -> None:
    """The rule the whole architecture rests on, checked by parsing.

    Every module under ``bimsteelauto/`` is read as an AST and its imports
    inspected. This is stricter than a grep: it will not be fooled by the
    word FreeCAD in a docstring, and it will catch an import hidden inside a
    function, which is exactly how the rule would get broken by accident.
    """
    banned = {"FreeCAD", "FreeCADGui", "Part", "Arch", "Draft", "TechDraw",
              "PySide", "PySide2", "PySide6", "PyQt5", "PyQt6"}
    offences = []
    for path in (PEB / "bimsteelauto").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for n in names:
                if n in banned:
                    offences.append(f"{path.relative_to(PEB)}:{node.lineno} {n}")
    assert not offences, "Layer 1 imported a Layer 2 dependency:\n" + "\n".join(offences)


def test_layer_2_only_imports_pure_engine_modules() -> None:
    """The adapter must not need a solver.

    FreeCAD's Python has no PyNite and no pyaisc360, and installing them
    into it is not something to require of a user. So the adapter may import
    only the engine modules that are pure Python - spec, sections, analysis
    (whose PyNite import is lazy, inside solve()).

    An import of ``bimsteelauto.design`` or ``bimsteelauto.pipeline`` here
    would break the adapter on every machine, and would do it at import time
    with a confusing error about a missing library the user never asked for.
    """
    allowed_prefixes = ("bimsteelauto.spec", "bimsteelauto.sections",
                        "bimsteelauto.analysis")
    offences = []
    for path in FC_PKG.glob("*.py"):
        if path.name == "selftest.py":
            continue                # the selftest may reach further
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            mod = ""
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
            elif isinstance(node, ast.Import):
                mod = node.names[0].name
            if mod.startswith("bimsteelauto.") and not mod.startswith(
                allowed_prefixes
            ):
                offences.append(f"{path.name}:{node.lineno} {mod}")
    assert not offences, (
        "the adapter imported an engine module that needs a solver:\n"
        + "\n".join(offences)
    )


def test_the_engine_writes_the_json_the_adapter_reads() -> None:
    """The handover between the layers, as a file on disk.

    ``run_frame_example.py`` writes it. If it is missing the adapter has
    nothing to do, so this fails loudly rather than skipping - a missing
    boundary file is a broken pipeline, not an absent optional dependency.
    """
    path = PEB / "design_result.json"
    assert path.exists(), (
        "design_result.json is missing - run run_frame_example.py, which is "
        "what writes the engine's output for the adapter to read"
    )
    import json

    from bimsteelauto.spec import DesignResult

    data = json.loads(path.read_text(encoding="utf-8"))
    result = DesignResult.from_dict(data)
    assert result.members
    assert result.spec.geometry.n_frames >= 2


# ---------------------------------------------------------------------
# The real checks, inside FreeCAD
# ---------------------------------------------------------------------

@pytest.mark.verification
def test_the_adapter_selftest_passes_inside_freecad() -> None:
    """Run Layer 2's checks in FreeCAD and require them all to pass.

    31 checks covering the geometry, the build, the schedule, the drawings
    and the IFC. The ones worth knowing about are the regression guards:
    that a lofted member is a closed SOLID and not an open shell, that
    purlin positions stay on the building (a Vector.multiply() that mutates
    in place sent them off exponentially), and that an elevation is drawn
    the right way up.
    """
    exe = _freecadcmd()
    if exe is None:
        pytest.skip(
            "freecadcmd not found - set FREECADCMD to run Layer 2's checks"
        )
    if not (PEB / "design_result.json").exists():
        pytest.skip("design_result.json missing - run run_frame_example.py")

    proc = subprocess.run(
        [exe, str(PEB / "run_selftest_fc.py")],
        cwd=str(PEB), capture_output=True, text=True, timeout=900,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "ALL PASSED" in out, (
        "the FreeCAD adapter selftest did not pass.\n"
        f"exit={proc.returncode}\n{out[-4000:]}"
    )
    assert "SELFTEST EXIT 0" in out, out[-2000:]


# ---------------------------------------------------------------------
# Layer 3 - the workbench
# ---------------------------------------------------------------------
#
# The GUI itself cannot be driven headlessly - FreeCADCmd has no
# FreeCADGui - so these check STRUCTURE, which is where a workbench
# actually breaks. A workbench with a bad import does not raise: it
# silently disappears from the selector with the reason buried in the
# report view.

def test_the_workbench_has_the_files_freecad_looks_for() -> None:
    assert WB.is_dir(), f"{WB} is missing"
    for f in ("Init.py", "InitGui.py", "bsa_commands.py"):
        assert (WB / f).is_file(), f
    assert (WB / "resources" / "icons" / "bimsteelauto.svg").is_file()


def test_every_workbench_file_parses() -> None:
    for f in WB.glob("*.py"):
        ast.parse(f.read_text(encoding="utf-8"), str(f))


def test_initgui_imports_nothing_expensive_at_module_scope() -> None:
    """The failure mode this guards is silent.

    FreeCAD imports InitGui.py for every workbench at start-up. An import
    error there does not stop FreeCAD - it removes the workbench from the
    selector and writes the reason into the report view, which is a bad
    half-hour for anyone who has not seen it before. So the engine and the
    command module are imported inside Initialize(), never at module level.
    """
    tree = ast.parse((WB / "InitGui.py").read_text(encoding="utf-8"))
    offenders = []
    for node in tree.body:                       # TOP LEVEL only
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for n in names:
            if n.startswith(("bimsteelauto", "bsa_commands", "Part", "Arch")):
                offenders.append(f"line {node.lineno}: {n}")
    assert not offenders, (
        "InitGui.py imports these at module scope, which can make the "
        "workbench vanish from the selector:\n" + "\n".join(offenders)
    )


def test_init_py_is_inert() -> None:
    """It runs on every FreeCADCmd start too, including the test runs."""
    tree = ast.parse((WB / "Init.py").read_text(encoding="utf-8"))
    real = [n for n in tree.body if not isinstance(n, ast.Expr)]
    assert not real, "Init.py should contain nothing but its docstring"


def test_every_registered_command_maps_to_a_real_class() -> None:
    """A name in the toolbar with no class behind it is a dead button."""
    tree = ast.parse((WB / "bsa_commands.py").read_text(encoding="utf-8"))
    classes = {n.name for n in tree.body if isinstance(n, ast.ClassDef)}
    commands, mapping = None, None
    for n in tree.body:
        if not isinstance(n, ast.Assign):
            continue
        target = getattr(n.targets[0], "id", "")
        if target == "COMMANDS":
            commands = [e.value for e in n.value.elts]
        elif target == "_CLASSES":
            mapping = {k.value: v.id for k, v in zip(n.value.keys, n.value.values)}
    assert commands, "COMMANDS not found"
    assert mapping, "_CLASSES not found"
    assert set(commands) == set(mapping), (
        f"toolbar and registry disagree: {set(commands) ^ set(mapping)}"
    )
    missing = [v for v in mapping.values() if v not in classes]
    assert not missing, f"registered but not defined: {missing}"


def test_every_command_class_has_the_methods_freecad_calls() -> None:
    tree = ast.parse((WB / "bsa_commands.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
        assert "GetResources" in methods, node.name
        assert "Activated" in methods, node.name
        assert "IsActive" in methods, node.name


def test_the_workbench_is_installed_where_freecad_will_find_it() -> None:
    """Skips rather than fails - a checkout is not an installation."""
    mod = pathlib.Path(
        os.path.expandvars(r"%APPDATA%\FreeCAD\Mod\BIMSteelAutoWB")
    )
    if not mod.exists():
        pytest.skip(
            "workbench not installed - link or copy BIMSteelAutoWB into "
            r"%APPDATA%\FreeCAD\Mod\ to use it in the GUI"
        )
    assert (mod / "InitGui.py").is_file()
