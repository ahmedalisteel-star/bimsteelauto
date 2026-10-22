"""
bsa.build - the second half of the chain, inside FreeCAD, with no GUI.

    freecadcmd bsa_build.py <job folder>

This is what the workbench's "Build model" button does, minus the file
dialog.  It runs in ``freecadcmd``, where ``FreeCADGui`` is not importable
at all - every ``ViewObject`` in the adapter is already guarded for exactly
that, so the model, the IFC, the schedule and the drawings all come out.

What is different without a GUI
-------------------------------
Colour.  A contour's colours live on ``ViewObject``, which does not exist
here, so ``bsa contour`` is a GUI job for now.  Nothing else in the chain
depends on a display.

Why a separate module from cli.py
---------------------------------
``freecadcmd`` cannot be given ``-m``; it takes a script path.  Keeping the
logic here and the entry in ``bsa_build.py`` means the two interpreters
share one implementation instead of drifting apart.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve(job: str) -> tuple:
    """``(design_result.json, default out dir)`` from a folder or a file."""
    if os.path.isdir(job):
        return os.path.join(job, "design_result.json"), os.path.join(job, "out")
    return job, os.path.join(os.path.dirname(os.path.abspath(job)), "out")


def run(job: str, out_dir: str | None = None) -> int:
    if HERE not in sys.path:
        sys.path.insert(0, HERE)

    try:
        import FreeCAD as App
    except ImportError:
        print("This command must be run inside FreeCAD:")
        print(f"    freecadcmd {os.path.join(HERE, 'bsa_build.py')} <job folder>")
        print("Run  python -m bsa doctor  to find freecadcmd on this PC.")
        return 2

    path, default_out = resolve(job)
    out_dir = out_dir or default_out
    if not os.path.isfile(path):
        print(f"not found: {path}")
        print("Run  python -m bsa design job.csv  first - it writes this file.")
        return 1
    os.makedirs(out_dir, exist_ok=True)

    print(f"FreeCAD {'.'.join(App.Version()[:3])}  (headless: "
          f"{'FreeCADGui' not in sys.modules})")
    print(f"reading {path}")
    t0 = time.time()
    try:
        from bimsteelauto_fc import build_all
        result = build_all(path, out_dir)
    except Exception:                                         # noqa: BLE001
        traceback.print_exc(file=sys.stdout)
        print("\nThe build failed.  Nothing above this line was written.")
        return 1

    print(f"\nbuilt in {time.time() - t0:.1f}s")
    print(result.summary())
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # freecadcmd hands the script its own path first; drop anything that is
    # this file rather than an argument meant for us.
    argv = [a for a in argv
            if not a.lower().endswith((".py", ".fcmacro", ".exe"))]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 < len(argv):
            out = argv[i + 1]
        del argv[i:i + 2]
    if not argv:
        print("usage: freecadcmd bsa_build.py <job folder> [--out DIR]")
        return 1
    return run(argv[0], out)
