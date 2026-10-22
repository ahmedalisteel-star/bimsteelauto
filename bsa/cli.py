"""
bsa.cli - the command line.

    python -m bsa doctor
    python -m bsa new job.csv
    python -m bsa design job.csv [--out DIR]
    python -m bsa export jobs/DEMO-2026-014 job.csv

``design`` deliberately does NOT touch FreeCAD.  It ends at
``design_result.json``, which is the contract the adapter reads, so the
whole analysis-and-check half of the chain runs on any Python with the
engine's four packages - no FreeCAD installed, no GUI, no display.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _bootstrap() -> None:
    """Put the package root (and ``vendor``) on the path.

    ``python -m bsa`` from anywhere should work, and the engine imports
    ``vendor`` for the cold-formed library, so both go on before any
    ``bimsteelauto`` import is attempted.
    """
    for p in (HERE, os.path.join(HERE, "vendor")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


def _engine_missing(exc: ImportError) -> int:
    print()
    print("The engine could not be imported in THIS interpreter:")
    print(f"    {sys.executable}")
    print(f"    {type(exc).__name__}: {exc}")
    print()
    print("Run  python -m bsa doctor  to see which interpreter has the")
    print("engine's packages, or install them here with:")
    print(f'    "{sys.executable}" -m pip install -r '
          f'"{os.path.join(HERE, "requirements.txt")}"')
    print()
    return 2


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "job"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_doctor(args) -> int:
    from bsa import doctor
    return doctor.run()


def cmd_new(args) -> int:
    from bsa import csvio

    if os.path.exists(args.path) and not args.force:
        print(f"{args.path} already exists.  Use --force to overwrite it.")
        return 1
    path = csvio.template(args.path)
    n = len(csvio.SCHEMA)
    print(f"Wrote {path}")
    print(f"  {n} rows, {len(csvio.BLOCKS)} blocks, every one with a note.")
    print()
    print("Open it in Excel, change the numbers, then:")
    print(f"    python -m bsa design {args.path}")
    return 0


def cmd_design(args) -> int:
    from bsa import csvio

    try:
        job = csvio.load(args.path)
    except csvio.CsvError as exc:
        print(exc.report())
        return 1

    try:
        from bimsteelauto.analysis import build_contours, write_contours
        from bimsteelauto.pipeline import design_frame
        from bimsteelauto.report import render_html, render_text
    except ImportError as exc:
        return _engine_missing(exc)

    out_dir = args.out or os.path.join(HERE, "jobs", _safe(job.spec.job_number))
    os.makedirs(out_dir, exist_ok=True)

    print(f"designing {job.spec.job_number} - {job.spec.project_name}")
    try:
        run = design_frame(job.spec, job.inputs)
    except Exception as exc:                                  # noqa: BLE001
        print()
        print("The engine refused this design:")
        print(f"    {type(exc).__name__}: {exc}")
        if args.traceback:
            traceback.print_exc()
        print()
        print("Nothing was written.  Fix the sheet and run again.")
        return 1
    result = run.result

    sheet = os.path.join(out_dir, "calc_sheet.html")
    with open(sheet, "w", encoding="utf-8") as fh:
        fh.write(render_html(result, designer=job.designer,
                             checker=job.checker,
                             title="PEB Frame Design Calculations"))
    with open(os.path.join(out_dir, "design_result.json"), "w",
              encoding="utf-8") as fh:
        fh.write(result.to_json())
    write_contours(build_contours(run), os.path.join(out_dir, "contours.json"))

    # The sheet that produced this goes in with it.  A calc sheet whose
    # inputs are on someone's desktop is not a record of anything.
    kept = os.path.join(out_dir, "job.csv")
    if os.path.abspath(job.source) != os.path.abspath(kept):
        shutil.copyfile(job.source, kept)

    print()
    print(render_text(result))
    print()
    geom = job.spec.geometry
    print(f"frame designed        : {run.frame_index} of 0..{geom.n_frames - 1}"
          f"  (tributary "
          f"{geom.tributary_widths()[run.frame_index] / 1000.0:.2f} m)")
    print(f"qh                    : {run.wind.qh:.4f} kN/m2")
    print(f"unverified relations  : {len(result.unverified)}")
    print(f"coverage gaps         : {len(result.coverage_gaps)}")
    print(f"READY FOR ISSUE       : {result.ready_for_issue()}")
    print()
    print(f"job folder            : {out_dir}")
    print("  calc_sheet.html     <- open in a browser")
    print("  design_result.json  <- FreeCAD reads this")
    print("  contours.json       <- the result picture")
    print("  job.csv             <- the inputs that produced all of it")
    print()
    print("Then, headless, no GUI:")
    print(f"    python -m bsa build {out_dir}")
    return 0 if result.ready_for_issue() else 3


def cmd_build(args) -> int:
    """Hand the job to ``freecadcmd``.

    The build must run in FreeCAD's own interpreter, but making the user
    remember that - and remember where freecadcmd lives - is what the
    ``doctor`` table is for, not what a daily command should demand.  So
    this finds it and shells out, and the two-environment split stays an
    implementation detail unless something goes wrong.
    """
    from bsa import build as build_mod
    from bsa import doctor

    try:
        import FreeCAD  # noqa: F401
    except ImportError:
        pass
    else:                       # already inside FreeCAD - just do it
        return build_mod.run(args.job, args.out)

    fc = doctor.find_freecad()
    if not fc["freecadcmd"]:
        print("freecadcmd was not found on this PC.")
        print("Run  python -m bsa doctor  for where it is looked for.")
        return 2

    path, _ = build_mod.resolve(args.job)
    if not os.path.isfile(path):
        print(f"not found: {path}")
        print("Run  python -m bsa design job.csv  first.")
        return 1

    cmd = [fc["freecadcmd"], os.path.join(HERE, "bsa_build.py"), args.job]
    if args.out:
        cmd += ["--out", args.out]
    print(f"handing off to {fc['freecadcmd']}")
    print("(this takes about a minute - meshing and drawing the whole model)")
    print()
    import subprocess
    proc = subprocess.run(cmd)
    return proc.returncode


def cmd_run(args) -> int:
    """design, then build - the whole chain from one CSV."""
    code = cmd_design(args)
    if code not in (0, 3):
        return code
    if code == 3 and not args.build_anyway:
        print("Design is NOT FOR ISSUE, so the model was not built.")
        print("Every drawing it produces would carry that stamp.  Use")
        print("--build-anyway if you want the model regardless.")
        return 3
    from bsa import csvio
    job = csvio.load(args.path)
    out_dir = args.out or os.path.join(HERE, "jobs", _safe(job.spec.job_number))
    print()
    print("=" * 74)
    args.job = out_dir
    args.out = None
    return cmd_build(args)


def cmd_export(args) -> int:
    from bsa import csvio

    src = args.job
    if os.path.isdir(src):
        src = os.path.join(src, "design_result.json")
    if not os.path.isfile(src):
        print(f"not found: {src}")
        return 1
    if os.path.exists(args.path) and not args.force:
        print(f"{args.path} already exists.  Use --force to overwrite it.")
        return 1

    path, n_got, n_todo = csvio.export(src, args.path)
    print(f"Wrote {path}")
    print(f"  {n_got} value(s) recovered from the design result")
    print(f"  {n_todo} marked {csvio.TODO}")
    print()
    if n_todo:
        print("A design result records what was DESIGNED, not what was typed:")
        print("the purlin's catalogue properties, the dead load build-up and")
        print("the detailing dimensions were inputs and are not in it.  Those")
        print(f"cells are marked {csvio.TODO} and the loader REFUSES a sheet")
        print("containing one, so this cannot be run on invented numbers.")
        print()
        print("Fill them in from the job that produced it, then:")
        print(f"    python -m bsa design {args.path}")
    return 0


def cmd_draw(args) -> int:
    """``bsa draw`` - design result to DXF drawing sheets.

    Unlike ``build``, this never touches FreeCAD.  ``bsa_dxf`` composes the
    sheets straight from ``design_result.json`` with ezdxf, so ``draw`` runs
    on the same interpreter as ``design`` and needs nothing else installed.
    """
    from bsa_dxf import write_set

    src = args.job
    if os.path.isdir(src):
        src = os.path.join(src, "design_result.json")
    if not os.path.isfile(src):
        print(f"not found: {src}")
        return 1

    with open(src, encoding="utf-8") as fh:
        result = json.load(fh)

    out_dir = args.out or os.path.join(os.path.dirname(src) or ".", "dxf")
    dxfset = write_set(result, out_dir, sheet=args.sheet, scale=args.scale)

    for sheet in dxfset.sheets:
        print(f"  {sheet.name:<22} {sheet.scale:<8} {sheet.path}")
    for w in dxfset.warnings:
        print(f"  WARNING  {w}")
    for e in dxfset.errors:
        print(f"  ERROR    {e}")

    if dxfset.errors:
        print()
        print("Every sheet is read back and audited before this command")
        print("returns, so an error here means the FILE is wrong, not just")
        print("that something looked odd on it.")
        return 3

    print()
    print(f"{len(dxfset.sheets)} sheet(s) written to {out_dir}")
    stamp = result.get("errors") or result.get("unverified")
    if stamp:
        print("The sheets carry a NOT FOR ISSUE stamp, taken from the design.")
    return 0


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bsa",
        description="BIMSteelAuto from the command line - no GUI, no Python.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "the chain:\n"
            "  job.csv --design--> design_result.json --build--> "
            "FCStd / IFC / MTO\n"
            "  design_result.json --draw--> DXF drawing sheets\n"
            "  design and draw run on any Python with the engine's packages;\n"
            "  build runs inside freecadcmd, headless.\n"
        ),
    )
    p.add_argument("--traceback", action="store_true",
                   help="show the full traceback when something fails")
    sub = p.add_subparsers(dest="cmd")

    d = sub.add_parser("doctor", help="can this PC run it, and with which Python")
    d.set_defaults(func=cmd_doctor)

    n = sub.add_parser("new", help="write a starter job sheet")
    n.add_argument("path", nargs="?", default="job.csv")
    n.add_argument("--force", action="store_true")
    n.set_defaults(func=cmd_new)

    g = sub.add_parser("design", help="CSV -> FEA -> AISC -> calc sheet")
    g.add_argument("path", help="the job sheet")
    g.add_argument("--out", help="output folder (default jobs/<job number>)")
    g.set_defaults(func=cmd_design)

    b = sub.add_parser("build", help="job folder -> FCStd, IFC, DXF, MTO "
                                     "(runs in freecadcmd, headless)")
    b.add_argument("job", help="a job folder or a design_result.json")
    b.add_argument("--out", help="output folder (default <job folder>/out)")
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("run", help="design then build - the whole chain")
    r.add_argument("path", help="the job sheet")
    r.add_argument("--out", help="output folder (default jobs/<job number>)")
    r.add_argument("--build-anyway", action="store_true",
                   help="build the model even when the design is NOT FOR ISSUE")
    r.set_defaults(func=cmd_run)

    w = sub.add_parser("draw", help="design_result.json -> DXF drawing sheets "
                                    "(no FreeCAD needed)")
    w.add_argument("job", help="a job folder or a design_result.json")
    w.add_argument("--out", help="output folder (default <job folder>/dxf)")
    w.add_argument("--sheet", default="A1",
                   choices=["A0", "A1", "A2", "A3", "A4"])
    w.add_argument("--scale", type=float, default=None,
                   help="plot scale denominator; omitted means fit the sheet")
    w.set_defaults(func=cmd_draw)

    e = sub.add_parser("export", help="design_result.json -> job sheet (lossy)")
    e.add_argument("job", help="a job folder or a design_result.json")
    e.add_argument("path", nargs="?", default="job.csv")
    e.add_argument("--force", action="store_true")
    e.set_defaults(func=cmd_export)
    return p


def main(argv=None) -> int:
    _bootstrap()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
