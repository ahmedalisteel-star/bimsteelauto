"""
bsa_dxf.writer
==============

The orchestrator: ``design_result.json`` in, a set of DXF sheets out.

Every file this module writes is read back and audited before the call
returns.  That is not belt and braces - it is the whole reason this package
exists.  The export it replaces wrote a raw entity stream with no DXF
wrapper, containing splines with two control points at order four, and no
text or dimension entities at all; nothing checked, so nothing complained,
and the failure only showed up in a fabricator's CAD package.  A writer that
cannot open its own output has not written a drawing.

:func:`verify` is therefore public and is the thing to call in a test.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field

import ezdxf
from ezdxf import zoom
from ezdxf.audit import Auditor

from . import views
from .sheet import TitleBlock, stamp_for

__all__ = ["DxfSet", "write_set", "verify", "title_block_from_result"]

#: DXF flavour.  R2010 (AC1024) is the oldest version that carries everything
#: used here and the newest that every mainstream reader still imports.
DXF_VERSION = "R2010"


@dataclass
class DxfSet:
    """The files produced by one run."""

    out_dir: str = ""
    sheets: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def paths(self) -> list[str]:
        return [s.path for s in self.sheets if s.path]


def title_block_from_result(result: dict, *, drawing_title: str = "",
                            drawing_no: str = "") -> TitleBlock:
    """Fill a title block from the design result.

    Only the fields the result actually carries are filled.  A blank row is
    left blank rather than guessed, because a title block that invents a
    consultant name is worse than one that shows the gap.
    """
    spec = result.get("spec", {}) or {}
    settings = spec.get("settings", {}) or {}
    geo = spec.get("geometry", {}) or {}
    mats = spec.get("materials", {}) or {}

    generated = result.get("generated_at") or ""
    try:
        date = datetime.datetime.fromisoformat(generated).strftime("%d-%m-%y")
    except ValueError:
        date = datetime.date.today().strftime("%d-%m-%y")

    notes = [
        f"DESIGN CODE : {settings.get('member_code', '-')}"
        f"   ({settings.get('design_method', '-')})",
        f"LOAD CODE : {settings.get('load_code', '-')}",
        f"BUILT-UP STEEL : Fy = {mats.get('Fy_builtup', '-')} MPa,"
        f" Fu = {mats.get('Fu_builtup', '-')} MPa",
        f"BOLTS : {mats.get('bolt_grade', '-')}"
        f"   ANCHORS : {mats.get('anchor_grade', '-')}",
        f"CONCRETE : f'c = {mats.get('fc_concrete', '-')} MPa",
        "ALL DIMENSIONS IN MILLIMETRES UNLESS NOTED OTHERWISE.",
        "DO NOT SCALE FROM THIS DRAWING.",
    ]

    return TitleBlock(
        drawing_title=drawing_title,
        drawing_no=drawing_no,
        job_no=spec.get("job_number", ""),
        project=spec.get("project_name", ""),
        building_desc=(geo.get("frame_type", "") or "").replace("_", " ").upper(),
        building_qty="1",
        steel_designer=f"BIMSteelAuto {result.get('engine_version', '')}".strip(),
        cad_file="",
        date=date,
        notes=notes,
        stamp=stamp_for(result),
    )


def write_set(result: dict, out_dir: str, *, sheet: str = "A1",
              scale: float | None = None, prefix: str = "GA") -> DxfSet:
    """Write every sheet this package knows how to compose.

    Returns a :class:`DxfSet`.  A sheet that fails to compose is recorded as
    an error and the remaining sheets are still written - a partial set is
    useful and a silent one is not.
    """
    os.makedirs(out_dir, exist_ok=True)
    out = DxfSet(out_dir=out_dir)

    plan = [
        ("frame_elevation", "FRAME SECTION", "E01", views.frame_elevation),
    ]

    for key, title, number, compose in plan:
        path = os.path.join(out_dir, f"{prefix}_{key}.dxf")
        try:
            doc = ezdxf.new(DXF_VERSION, setup=True)
            doc.header["$INSUNITS"] = 4          # millimetres
            doc.header["$MEASUREMENT"] = 1       # metric
            tb = title_block_from_result(
                result, drawing_title=title, drawing_no=number
            )
            res = compose(doc, result, tb, sheet=sheet, scale=scale)
            zoom.extents(doc.modelspace(), factor=1.05)
            doc.saveas(path)
            res.path = path
            out.sheets.append(res)
            out.warnings.extend(f"{key}: {w}" for w in res.warnings)
        except Exception as exc:                        # noqa: BLE001
            out.errors.append(f"{key}: {type(exc).__name__}: {exc}")
            continue

        problems = verify(path)
        if problems:
            out.errors.extend(f"{key}: {p}" for p in problems)

    return out


def verify(path: str) -> list[str]:
    """Read a written DXF back and report anything wrong with it.

    Checks, in the order that the old exporter failed them:

    1. the file opens as a DXF at all;
    2. ezdxf's own auditor finds no structural errors;
    3. it carries annotation - a drawing with no TEXT and no DIMENSION is a
       plot of a model, not a drawing;
    4. no degenerate spline (fewer control points than its own order needs).
    """
    problems: list[str] = []
    try:
        doc = ezdxf.readfile(path)
    except Exception as exc:                            # noqa: BLE001
        return [f"cannot be read back as DXF - {type(exc).__name__}: {exc}"]

    auditor = Auditor(doc)
    auditor.run()
    if auditor.errors:
        problems.append(
            f"{len(auditor.errors)} audit error(s), first: {auditor.errors[0]}"
        )

    msp = doc.modelspace()
    kinds: dict[str, int] = {}
    for e in msp:
        kinds[e.dxftype()] = kinds.get(e.dxftype(), 0) + 1

    n_text = kinds.get("TEXT", 0) + kinds.get("MTEXT", 0)
    n_dim = kinds.get("DIMENSION", 0)
    if n_text == 0:
        problems.append("carries no TEXT - nothing on the sheet is labelled")
    if n_dim == 0:
        problems.append("carries no DIMENSION entities")

    for e in msp.query("SPLINE"):
        order = e.dxf.degree + 1
        n_ctrl = len(e.control_points)
        if n_ctrl < order:
            problems.append(
                f"degenerate SPLINE: {n_ctrl} control points at order {order}"
            )
            break

    return problems


def write_from_file(json_path: str, out_dir: str, **kwargs) -> DxfSet:
    """Convenience: load a design result from disk and write its sheets."""
    with open(json_path, encoding="utf-8") as fh:
        result = json.load(fh)
    return write_set(result, out_dir, **kwargs)
