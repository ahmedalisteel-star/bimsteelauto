"""
bsa.doctor - can this PC run BIMSteelAuto, and with which Python?

Why this exists
---------------
The package needs TWO environments and says so nowhere.  On the machine
this was written on, three interpreters were installed and the state was:

    py 3.14      numpy, ezdxf                 - cannot run the engine
    python 3.12  pyaisc360, Pynite, scipy     - CAN run the engine
    FreeCAD 3.11 Pynite, scipy, ifcopenshell  - cannot run the engine

Nothing recorded which one was "the" one.  Moving to a second PC, or coming
back after a month, that is discovered as an ImportError six frames deep in
a library nobody has read.  This module turns that into one table.

It probes by RUNNING each interpreter, not by importing into this one - an
interpreter cannot report on its neighbours, and the whole question here is
about the neighbours.

The verdict
-----------
``doctor`` names the interpreter to use for ``design`` and the executable
to use for ``build``, or says exactly which package is missing from which
interpreter and gives the pip line that fixes it.  Exit code is 0 when a
full chain is possible, 1 when it is not, so it can gate a batch file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: What the ENGINE needs.  Import name on the left because that is what
#: fails; the pip name differs for two of them and that is exactly the
#: trap this table exists to remove.
ENGINE_REQUIRES = {
    "Pynite": "PyNiteFEA==3.0.0",
    "pyaisc360": (
        "pyaisc360 @ git+https://github.com/ahmedalisteel-star/pyaisc360.git"
        "@007a3f6f47ceaacf28b5f4da479a38bff8d7eb30"
    ),
    "numpy": "numpy==2.5.1",
    "scipy": "scipy==1.18.0",
}

#: Nice to have on the engine side; nothing fails without them.
ENGINE_OPTIONAL = {
    "pytest": "pytest==9.1.1",
    "pypdf": "pypdf==6.15.0",
}

#: What the ADAPTER needs from FreeCAD's own Python.  All of these ship
#: with FreeCAD 1.1 - none is pip-installable into it and none should be.
ADAPTER_REQUIRES = ("FreeCAD", "Part", "Arch")
ADAPTER_OPTIONAL = ("nativeifc", "ifcopenshell")

#: Places FreeCAD puts itself on Windows.  PATH is tried first.
FREECAD_GUESSES = (
    r"C:\Program Files\FreeCAD 1.1\bin",
    r"C:\Program Files\FreeCAD 1.0\bin",
    r"C:\Program Files\FreeCAD 0.21\bin",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\FreeCAD 1.1\bin"),
)

#: Printed inside a probed interpreter.  Deliberately stdlib-only and
#: deliberately tolerant: a probe that crashes tells us nothing about the
#: interpreter, only about the probe.
_PROBE = r"""
import json, sys
try:
    import importlib.util as u
    def has(m):
        try:
            return u.find_spec(m) is not None
        except Exception:
            return False
except Exception:
    def has(m):
        return False
def ver(m):
    try:
        mod = __import__(m)
        return str(getattr(mod, "__version__", "") or "")
    except Exception:
        return ""
names = json.loads(sys.argv[1])
print("BSAPROBE" + json.dumps({
    "version": "%d.%d.%d" % sys.version_info[:3],
    "executable": sys.executable,
    "found": {n: (has(n), ver(n) if has(n) else "") for n in names},
}))
"""


@dataclass
class Probe:
    """One interpreter, and what it turned out to have."""

    label: str
    exe: str
    ok: bool = False
    version: str = ""
    error: str = ""
    found: dict = field(default_factory=dict)

    def missing(self, required) -> list:
        return [m for m in required if not self.found.get(m, (False, ""))[0]]

    def can_run_engine(self) -> bool:
        return self.ok and not self.missing(ENGINE_REQUIRES)


def _run_probe(label: str, exe: str, names) -> Probe:
    p = Probe(label=label, exe=exe)
    try:
        cp = subprocess.run(
            [exe, "-c", _PROBE, json.dumps(list(names))],
            capture_output=True, text=True, timeout=90,
        )
    except Exception as exc:                                  # noqa: BLE001
        p.error = f"{type(exc).__name__}: {exc}"
        return p
    line = ""
    for ln in (cp.stdout or "").splitlines():
        if ln.startswith("BSAPROBE"):
            line = ln[len("BSAPROBE"):]
    if not line:
        blurb = (cp.stderr or cp.stdout or "").strip()
        # The Windows Store "app execution alias" is a stub that prints an
        # advert instead of being a Python.  It is not a broken install and
        # listing it as one sends people off to fix nothing.
        if "Microsoft Store" in blurb or "was not found" in blurb:
            p.error = "__stub__"
            return p
        tail = blurb.splitlines()[-1:] if blurb else []
        p.error = tail[0][:60] if tail else "no output"
        return p
    data = json.loads(line)
    p.ok = True
    p.version = data["version"]
    p.exe = data["executable"] or exe
    p.found = {k: tuple(v) for k, v in data["found"].items()}
    return p


def find_pythons() -> list:
    """Every interpreter worth asking, de-duplicated by real path.

    ``py -0p`` is the reliable enumerator on Windows; ``python`` and
    ``python3`` on PATH catch everything else, including a venv that is
    active right now.  This interpreter is always included - it is the one
    that will run ``design`` if the user just types ``python -m bsa``.
    """
    cands = []

    def add(label, exe):
        if exe and (label, exe) not in cands:
            cands.append((label, exe))

    add("this interpreter", sys.executable)
    for name in ("python", "python3", "py"):
        w = shutil.which(name)
        if w:
            add(f"PATH: {name}", w)

    py = shutil.which("py")
    if py:
        try:
            cp = subprocess.run([py, "-0p"], capture_output=True,
                                text=True, timeout=30)
            for ln in (cp.stdout or "").splitlines():
                ln = ln.strip()
                if not ln or ":" not in ln:
                    continue
                # " -V:3.12 *        C:\...\python.exe"
                tag, _, path = ln.partition(" ")
                path = path.strip().strip("*").strip()
                if path.lower().endswith(".exe") and os.path.exists(path):
                    add(f"py {tag.replace('-V:', '')}", path)
        except Exception:                                     # noqa: BLE001
            pass

    seen, out = set(), []
    for label, exe in cands:
        try:
            key = os.path.normcase(os.path.realpath(exe))
        except Exception:                                     # noqa: BLE001
            key = os.path.normcase(exe)
        if key in seen:
            continue
        seen.add(key)
        out.append((label, exe))
    return out


def find_freecad() -> dict:
    """``freecadcmd`` (headless), ``freecad`` (GUI) and FreeCAD's python."""
    out = {"bin": "", "freecadcmd": "", "freecad": "", "python": ""}
    for name in ("freecadcmd", "FreeCADCmd"):
        w = shutil.which(name)
        if w:
            out["bin"] = os.path.dirname(w)
            break
    if not out["bin"]:
        for d in FREECAD_GUESSES:
            if os.path.isdir(d) and (
                os.path.exists(os.path.join(d, "freecadcmd.exe"))
                or os.path.exists(os.path.join(d, "FreeCADCmd.exe"))
            ):
                out["bin"] = d
                break
    if not out["bin"]:
        return out
    for key, names in (
        ("freecadcmd", ("freecadcmd.exe", "FreeCADCmd.exe", "freecadcmd")),
        ("freecad", ("freecad.exe", "FreeCAD.exe", "freecad")),
        ("python", ("python.exe", "python")),
    ):
        for n in names:
            p = os.path.join(out["bin"], n)
            if os.path.exists(p):
                out[key] = p
                break
    return out


def find_workbench() -> dict:
    """Is the workbench installed where FreeCAD will actually look?

    FreeCAD 1.1 reads ``%APPDATA%\\FreeCAD\\v1-1\\Mod`` - note the version
    segment.  A link placed in ``%APPDATA%\\FreeCAD\\Mod`` looks right and
    is never read, which is a long afternoon.
    """
    out = {"mod_dir": "", "installed": False, "target": "", "target_ok": False,
           "source": os.path.join(os.path.dirname(HERE), "BIMSteelAutoWB")}
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return out
    for sub in ("FreeCAD/v1-1/Mod", "FreeCAD/v1-0/Mod", "FreeCAD/Mod"):
        d = os.path.join(appdata, *sub.split("/"))
        if os.path.isdir(d):
            out["mod_dir"] = d
            wb = os.path.join(d, "BIMSteelAutoWB")
            if os.path.exists(wb):
                out["installed"] = True
                out["target"] = os.path.realpath(wb)
                out["target_ok"] = os.path.exists(
                    os.path.join(out["target"], "InitGui.py")
                )
                return out
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_TICK, _CROSS, _DASH = "ok", "--", "  "


def _mark(present: bool) -> str:
    return _TICK if present else _CROSS


def run(argv=None) -> int:
    """Print the report.  0 = a full chain is possible, 1 = it is not."""
    print("=" * 74)
    print("BIMSteelAuto - environment report")
    print("=" * 74)
    print(f"package  {HERE}")
    print(f"host     {sys.platform}  |  cwd {os.getcwd()}")
    print()

    names = list(ENGINE_REQUIRES) + list(ENGINE_OPTIONAL)
    pythons = [_run_probe(lb, ex, names) for lb, ex in find_pythons()]
    stubs = [p for p in pythons if p.error == "__stub__"]
    pythons = [p for p in pythons if p.error != "__stub__"]

    print("-" * 74)
    print("PYTHON INTERPRETERS - the ENGINE (design) runs in one of these")
    print("-" * 74)
    cols = list(ENGINE_REQUIRES)
    head = f"{'interpreter':<22}{'ver':<8}" + "".join(f"{c[:9]:<11}" for c in cols)
    print(head)
    print("-" * len(head))
    for p in pythons:
        if not p.ok:
            print(f"{p.label:<22}{'?':<8}{p.error[:40]}")
            continue
        row = f"{p.label:<22}{p.version:<8}"
        row += "".join(f"{_mark(p.found.get(c, (False,''))[0]):<11}" for c in cols)
        print(row)
    print()

    engine = [p for p in pythons if p.can_run_engine()]
    best = engine[0] if engine else None

    for p in pythons:
        if p.ok and not p.can_run_engine() and p.found:
            miss = p.missing(ENGINE_REQUIRES)
            if len(miss) < len(ENGINE_REQUIRES):     # partial: worth naming
                print(f"  {p.label} ({p.version}) is missing: {', '.join(miss)}")
    if best is None:
        print("  NO interpreter can run the engine.")
    for p in stubs:
        print(f"  {p.label} is the Windows Store placeholder, not a Python - "
              f"ignored.")
    print()

    print("-" * 74)
    print("FREECAD - the ADAPTER (build, contours) runs here, headless")
    print("-" * 74)
    fc = find_freecad()
    if not fc["bin"]:
        print("  freecadcmd NOT FOUND on PATH or in the usual install folders.")
        print("  Install FreeCAD 1.1, or add its bin folder to PATH.")
        fc_probe = None
    else:
        print(f"  bin         {fc['bin']}")
        print(f"  freecadcmd  {_mark(bool(fc['freecadcmd']))}  {fc['freecadcmd']}")
        print(f"  freecad     {_mark(bool(fc['freecad']))}  {fc['freecad']}")
        fc_probe = None
        if fc["python"]:
            fc_probe = _run_probe(
                "FreeCAD python", fc["python"],
                list(ADAPTER_OPTIONAL) + list(ENGINE_REQUIRES),
            )
            if fc_probe.ok:
                have = [n for n in ADAPTER_OPTIONAL
                        if fc_probe.found.get(n, (False, ""))[0]]
                print(f"  python      {fc_probe.version}  "
                      f"(IFC export: {', '.join(have) if have else 'NOT available'})")
    print()

    print("-" * 74)
    print("WORKBENCH - only needed for the GUI buttons, not for the CLI")
    print("-" * 74)
    wb = find_workbench()
    if not wb["mod_dir"]:
        print("  no FreeCAD Mod folder found (FreeCAD may never have been run)")
    elif not wb["installed"]:
        print(f"  Mod folder   {wb['mod_dir']}")
        print("  NOT installed.  To install, run as Administrator:")
        print(f'    mklink /J "{os.path.join(wb["mod_dir"], "BIMSteelAutoWB")}" '
              f'"{wb["source"]}"')
    else:
        print(f"  Mod folder   {wb['mod_dir']}")
        print(f"  link target  {wb['target']}")
        print(f"  target valid {_mark(wb['target_ok'])}"
              + ("" if wb["target_ok"] else "   <- InitGui.py not found there"))
    print()

    print("=" * 74)
    print("VERDICT")
    print("=" * 74)
    code = 0
    if best is not None:
        print(f"  design   {best.exe}")
        print(f"           python -m bsa design job.csv     (from {HERE})")
    else:
        code = 1
        print("  design   BLOCKED - no interpreter has the engine's packages.")
        print("           Pick a Python 3.11+ above and run:")
        print("             <python> -m pip install -r requirements.txt")
        print("           requirements.txt pins every version this was verified on.")
    if fc["freecadcmd"]:
        print(f'  build    "{fc["freecadcmd"]}" '
              f'"{os.path.join(HERE, "bsa_build.py")}" <job folder>')
        print("           (freecadcmd takes a script path, not -m)")
    else:
        code = 1
        print("  build    BLOCKED - freecadcmd not found.")
    print()
    if code == 0:
        print("  Full chain available: CSV -> FEA -> AISC -> sheet -> BIM/IFC/DXF.")
    else:
        print("  Chain incomplete - see above.  Nothing here is guessed at.")
    print()
    return code
