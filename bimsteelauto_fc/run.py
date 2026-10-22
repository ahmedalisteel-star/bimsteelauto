"""
bimsteelauto_fc.run
===================

One call that turns a ``design_result.json`` into everything Layer 2
produces::

    build_all("design_result.json", "out/")

    out/
      <job>.FCStd                 the BIM model
      <job>.ifc                   IFC4
      schedule.csv                the MTO
      GA_frame_elevation.svg      general arrangement
      GA_side_elevation.svg
      GA_roof_plan.svg
      GA_*.dxf                    the same, for a fabricator
      PART_*.svg                  single-part drawings

Nothing here decides anything.  Every number came out of the engine and
every geometry decision is in :mod:`~bimsteelauto_fc.builder`; this module
only sequences them and reports what happened.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

import FreeCAD as App

from .builder import BuildReport, build_model, load_result
from .drawings import DrawingSet, export_dxf, general_arrangement, single_part_drawings
from .ifc import IfcExport, export_ifc
from .schedule import format_schedule, member_schedule, write_schedule_csv, write_schedule_sheet

__all__ = ["AdapterRun", "build_all", "adapter_coverage_gaps"]


def adapter_coverage_gaps() -> tuple[str, ...]:
    """Every Layer 2 module's declared gaps, in one tuple.

    Layer 1's gaps reach the reader through the calculation sheet.  Layer
    2's had nowhere to go, so they sat in module docstrings where nobody
    opening a drawing would see them.  They print with the build report
    now, which is where a person looking at the MODEL will look.
    """
    from . import builder, detailing, drawings

    gaps: list[str] = []
    for module, label in (
        (builder, "model"),
        (detailing, "connection geometry"),
        (drawings, "drawings"),
    ):
        for gap in getattr(module, "COVERAGE_GAPS", ()):
            gaps.append(f"[{label}] {gap}")
    return tuple(gaps)


@dataclass
class AdapterRun:
    """Everything Layer 2 produced, and how long it took."""

    result: object = None
    build: BuildReport | None = None
    schedule: tuple = ()
    ifc: IfcExport | None = None
    ga: DrawingSet | None = None
    parts: DrawingSet | None = None
    dxf: DrawingSet | None = None
    document_path: str = ""
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        out = ["=" * 74, "BIMSteelAuto - FreeCAD adapter", "=" * 74]
        if self.result is not None:
            spec = self.result.spec
            out.append(f"{spec.project_name}  [{spec.job_number}]")
            out.append(
                "DESIGN STATUS: "
                + ("READY FOR ISSUE" if self.result.ready_for_issue()
                   else "NOT FOR ISSUE")
            )
            for b in self.result.issue_blockers()[:6]:
                out.append(f"   - {b}")
        out.append("")
        if self.build:
            out.append(self.build.summary())
        if self.document_path:
            out.append(f"model    {self.document_path}")
        if self.ifc:
            out.append(f"ifc      {self.ifc.path}  ({self.ifc.n_elements} elements)")
        if self.ga:
            out.append(f"GA       {len(self.ga.sheets)} sheets")
        if self.parts:
            out.append(f"parts    {len(self.parts.sheets)} sheets")
        if self.dxf:
            out.append(f"dxf      {len(self.dxf.sheets)} files")
        for w in self.warnings:
            out.append(f"  ! {w}")

        gaps = adapter_coverage_gaps()
        if gaps:
            out.append("")
            out.append(f"WHAT THIS MODEL DOES NOT CONTAIN ({len(gaps)}):")
            for gap in gaps:
                out.append(f"  - {gap}")
        out.append(f"\n{self.seconds:.1f} s")
        out.append("=" * 74)
        return "\n".join(out)


def build_all(
    json_path: str,
    out_dir: str,
    *,
    save_document: bool = True,
    do_ifc: bool = True,
    do_drawings: bool = True,
    do_parts: bool = True,
    do_dxf: bool = True,
) -> AdapterRun:
    """Read the engine's JSON and produce the model, IFC, MTO and drawings.

    Every step is independently switchable, and a step that fails records
    its reason and lets the rest run - a broken IFC exporter should not cost
    you the drawings.  The one exception is the model itself: without it
    there is nothing for the others to work on, so that failure propagates.
    """
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    run = AdapterRun()

    run.result = load_result(json_path)
    run.build = build_model(run.result)
    doc = run.build.document
    job = re.sub(r"[^A-Za-z0-9_.-]", "_", run.result.spec.job_number) or "model"

    # -- schedule -----------------------------------------------------------
    run.schedule = member_schedule(run.build)
    write_schedule_csv(run.schedule, os.path.join(out_dir, "schedule.csv"))
    try:
        write_schedule_sheet(doc, run.schedule)
    except Exception as exc:                            # noqa: BLE001
        run.warnings.append(f"schedule spreadsheet not written: {exc}")

    # -- drawings BEFORE the IFC spatial tree -------------------------------
    # Arch.makeFloor REPARENTS the elements it is given, so building the
    # spatial tree changes what a plain document scan finds. Drawing first
    # keeps both steps working off the same set.
    if do_drawings:
        try:
            run.ga = general_arrangement(doc, out_dir, result=run.result)
        except Exception as exc:                        # noqa: BLE001
            run.warnings.append(f"general arrangement failed: {exc}")
    if do_parts:
        try:
            run.parts = single_part_drawings(doc, out_dir, result=run.result)
        except Exception as exc:                        # noqa: BLE001
            run.warnings.append(f"single-part drawings failed: {exc}")
    if do_dxf:
        try:
            run.dxf = export_dxf(doc, out_dir)
        except Exception as exc:                        # noqa: BLE001
            run.warnings.append(f"DXF export failed: {exc}")

    if do_ifc:
        try:
            run.ifc = export_ifc(
                doc, os.path.join(out_dir, job + ".ifc"),
                project_name=run.result.spec.project_name,
            )
        except Exception as exc:                        # noqa: BLE001
            run.warnings.append(f"IFC export failed: {exc}")

    if save_document:
        path = os.path.join(out_dir, job + ".FCStd")
        doc.saveAs(path)
        run.document_path = path

    run.seconds = time.time() - t0
    return run


def print_report(run: AdapterRun) -> None:
    print(run.summary())
    print()
    print(format_schedule(run.schedule))
