"""
IFC -> fabrication drawing set, behind the gates::

    python -m bimsteelauto.fabdraw.run MODEL.ifc OUT_DIR [--titleblock tb.json] [--marks C1,R1]
           [--policy strict|any] [--no-gates] [--no-parts] [--no-nc] [--pdf] [--prefix JOB-]

Writes to OUT_DIR::

    ASSEM/<prefix><mark>.dxf     one per assembly mark
    SINGLE/<prefix><mark>.dxf    one per part mark
    NC_DXF/<mark>.dxf            plates, 1:1 cutting outline + holes
    DSTV/<mark>.nc1              plates, DSTV
    PDF/ASSEMBLY.pdf, PDF/SINGLE.pdf   (with --pdf)
    register.csv / register.html       every sheet, mark, qty, weight, scale, status, warnings
    fabdraw_report.json                the gates (G1, G2, G3) and every warning

Status: every sheet is drawn "NOT FOR ISSUE" and re-stamped with ``rev_desc``
("FOR APPROVAL") only when G1 and G3 passed - and G2 too under the strict
policy (G2 is the BIMSteelAuto IDS; a Tekla model cannot carry its Pset, so
under ``any`` it is reported, not blocking). A sheet that is not issued is
still drawn, so it can be reviewed, but it says on its face that it did not pass.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import sys
import time
from pathlib import Path

from . import extract
from .core import NOT_FOR_ISSUE
from .sheets import assembly_sheet, part_sheet, plate_nc

__all__ = ["run", "main", "DEFAULT_TB"]

DEFAULT_TB = {"company": "", "project": "", "project_no": "", "drawn_by": "", "checked_by": "", "date": "",
              "rev": "0", "rev_desc": "FOR APPROVAL", "weld": "", "prefix": ""}


def _gates(ifc, out, log):
    res = {}
    try:
        from .gate_clash import run_clash_gate
        g1 = run_clash_gate(ifc, out)
        res["G1"] = {"passed": g1.passed, "hard": len(g1.hard), "blockers": list(g1.blockers)}
    except Exception as e:                  # noqa: BLE001 - reported, never hidden
        res["G1"] = {"passed": False, "error": f"{type(e).__name__}: {e}"}
    try:
        from .gate_ids import run_ids_gate
        g2 = run_ids_gate(ifc, out)
        res["G2"] = {"passed": g2.passed, "hard": len(g2.hard), "blockers": list(g2.blockers),
                     "summary": g2.summary}
    except Exception as e:                  # noqa: BLE001
        res["G2"] = {"passed": False, "error": f"{type(e).__name__}: {e}"}
    for k, v in res.items():
        log(f"{k}: {'PASS' if v.get('passed') else 'FAIL'}  {v.get('error') or ''}")
    return res


def run(ifc, out, *, tb=None, marks=None, policy="strict", gates=True, parts=True, nc=True, pdf=False,
        prefix=None, log=print) -> dict:
    t0 = time.time()
    out = Path(out)
    for d in ("ASSEM", "SINGLE", "NC_DXF", "DSTV", "PDF", "gates"):
        (out / d).mkdir(parents=True, exist_ok=True)
    tb = {**DEFAULT_TB, **(tb or {})}
    prefix = tb.get("prefix", "") if prefix is None else prefix
    report = {"ifc": str(ifc), "policy": policy, "gates": {}, "sheets": [], "warnings": []}
    issue = tb.get("rev_desc") or "FOR APPROVAL"
    status = NOT_FOR_ISSUE          # every sheet is drawn NOT FOR ISSUE and re-stamped only after the gates
    if gates:
        report["gates"] = _gates(ifc, out / "gates", log)
    log("reading the model ...")
    M = extract.read_model(ifc, policy=policy, marks=marks)
    report["warnings"] += M.warnings
    tb.setdefault("project", M.project)
    if not tb.get("project"):
        tb["project"] = M.project
    log(f"{len(M.assemblies)} assembly marks, {len(M.parts_by_mark)} part marks  ({time.time() - t0:.1f}s)")
    reg = []
    for a in M.assemblies:
        try:
            r = assembly_sheet(a, out / "ASSEM", tb, status=status, prefix=prefix)
            report["sheets"].append(r.__dict__)
            reg.append(("ASSEMBLY", a.mark, a.qty, a.name, round(a.weight, 1), r.size, f"1:{r.scale:g}",
                        Path(r.path).name, a.mark_src, "; ".join(r.warnings) + (f"; {r.overlaps} text overlaps" if r.overlaps else "")))
            log(f"  ASSEM  {a.mark:10s} x{a.qty:<3d} {r.size} 1:{r.scale:g}  views {','.join(r.views)}"
                + (f"  overlaps {r.overlaps}" if r.overlaps else ""))
        except Exception as e:             # noqa: BLE001 - one bad assembly must not stop the set
            report["warnings"].append(f"assembly {a.mark}: {type(e).__name__}: {e}")
            log(f"  ASSEM  {a.mark}: FAILED {e}")
    if parts:
        for mk in sorted(M.parts_by_mark, key=extract._natural):
            ps = M.parts_by_mark[mk]
            alloc = M.allocation[mk]
            try:
                r = part_sheet(mk, ps, alloc, out / "SINGLE", tb, status=status, prefix=prefix)
                report["sheets"].append(r.__dict__)
                p = ps[0]
                reg.append(("SINGLE", mk, sum(alloc.values()), p.description(), round(p.weight, 2), r.size,
                            f"1:{r.scale:g}", Path(r.path).name, p.mark_src,
                            "; ".join(r.warnings) + (f"; {r.overlaps} text overlaps" if r.overlaps else "")))
                if nc and p.is_plate:
                    plate_nc(mk, p, sum(alloc.values()), out / "NC_DXF" / f"{mk}.dxf", out / "DSTV" / f"{mk}.nc1",
                             tb.get("project_no", ""))
            except Exception as e:         # noqa: BLE001
                report["warnings"].append(f"part {mk}: {type(e).__name__}: {e}")
                log(f"  SINGLE {mk}: FAILED {e}")
        log(f"{len(M.parts_by_mark)} single-part sheets")
    # G3 - read the sheets back against the IFC
    try:
        from .gate_roundtrip import run_roundtrip_gate
        g3 = run_roundtrip_gate(M, out, prefix=prefix, sheets=report["sheets"])
        report["gates"]["G3"] = {"passed": g3.passed, "hard": len(g3.hard), "warnings": len(g3.warnings),
                                 "summary": g3.summary}
        log(f"G3: {'PASS' if g3.passed else 'FAIL'}  {g3.summary}")
    except ImportError:
        pass
    # the issue decision: G1 and G3 always; G2 (the BIMSteelAuto IDS) under the strict policy only - a model
    # from another tool cannot carry BSA_Fabrication, so there G2 is reported, never blocking
    blocking = ["G1", "G3"] + (["G2"] if policy == "strict" else [])
    ok = gates and all(report["gates"].get(g, {}).get("passed") for g in blocking)
    report["issue"] = {"blocking_gates": blocking, "issued": bool(ok), "status": issue if ok else NOT_FOR_ISSUE}
    if ok:
        status = issue
        _restamp(out, NOT_FOR_ISSUE, status)
    log(f"status: {status}" + ("" if gates else "  (gates not run)"))
    if pdf:
        _pdf_book(out / "ASSEM", out / "PDF" / "ASSEMBLY.pdf", log)
        if parts:
            _pdf_book(out / "SINGLE", out / "PDF" / "SINGLE.pdf", log)
    _register(reg, out, status, M, report)
    report["seconds"] = round(time.time() - t0, 1)
    (out / "fabdraw_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    log(f"done in {report['seconds']} s -> {out}")
    return report


def _restamp(out: Path, old: str, new: str):
    """Replace the status stamp on every sheet (layer STAMP) once the gates have decided."""
    import ezdxf

    for d in ("ASSEM", "SINGLE"):
        for f in (out / d).glob("*.dxf"):
            doc = ezdxf.readfile(f)
            n = 0
            for t in doc.modelspace().query('TEXT[layer=="STAMP"]'):
                if t.dxf.text == old:
                    t.dxf.text = new
                    n += 1
            if n:
                doc.saveas(f)


def _register(rows, out: Path, status, M, report):
    head = ["Type", "Mark", "Qty", "Description", "Unit kg", "Sheet", "Scale", "File", "Mark from", "Warnings"]
    with open(out / "register.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)
    tr = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    gates = "".join(f"<li><b>{k}</b> {'PASS' if v.get('passed') else 'FAIL'} {html.escape(str(v.get('error') or v.get('summary') or ''))}</li>"
                    for k, v in report["gates"].items())
    warn = "".join(f"<li>{html.escape(w)}</li>" for w in report["warnings"])
    (out / "register.html").write_text(
        f"<!doctype html><meta charset=utf-8><title>Fabrication register</title>"
        f"<style>body{{font:13px system-ui;margin:16px}}td,th{{border:1px solid #ccc;padding:2px 6px}}"
        f"table{{border-collapse:collapse}}</style><h2>Fabrication drawing register</h2>"
        f"<p>{html.escape(M.ifc_path)} ({M.schema}), mark policy <b>{M.policy}</b>, status <b>{html.escape(status)}</b>. "
        f"Weights: Pset weight where given, otherwise solid volume x {extract.STEEL_DENSITY_KG_M3:g} kg/m3.</p>"
        f"<ul>{gates}</ul><table><tr>{''.join(f'<th>{h}</th>' for h in head)}</tr>{tr}</table>"
        f"<h3>Warnings</h3><ul>{warn or '<li>none</li>'}</ul>", encoding="utf-8")


def _pdf_book(src: Path, dst: Path, log):
    """Every DXF in ``src`` -> one PDF, one page per sheet at true paper size."""
    import ezdxf
    import pymupdf
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, pymupdf as pm_backend
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration, LineweightPolicy

    book = pymupdf.open()
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK,
                        lineweight_policy=LineweightPolicy.ABSOLUTE, min_lineweight=0.1)
    for f in sorted(src.glob("*.dxf"), key=lambda p: extract._natural(p.stem)):
        doc = ezdxf.readfile(f)
        msp = doc.modelspace()
        trim = [e for e in msp.query("LWPOLYLINE[layer=='BSA_VIEW']")]
        if trim:
            pts = list(trim[0].get_points("xy"))
            W, H = max(p[0] for p in pts), max(p[1] for p in pts)
        else:
            W, H = 420.0, 297.0
        be = pm_backend.PyMuPdfBackend()
        ctx = RenderContext(doc)
        def _hide(layers):
            for lp in layers:
                if lp.layer.upper() == "BSA_VIEW":
                    lp.is_visible = False
        ctx.set_layer_properties_override(_hide)
        Frontend(ctx, be, config=cfg).draw_layout(msp)
        page = layout.Page(W, H, layout.Units.mm, margins=layout.Margins.all(0))
        data = be.get_pdf_bytes(page, settings=layout.Settings(fit_page=False, scale=1.0))
        book.insert_pdf(pymupdf.open("pdf", data))
    if book.page_count:
        book.save(dst)
        log(f"PDF {dst.name}: {book.page_count} pages")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ifc")
    ap.add_argument("out")
    ap.add_argument("--titleblock", help="JSON: company, project, project_no, drawn_by, checked_by, date, rev, "
                                         "rev_desc, revisions[], weld, prefix")
    ap.add_argument("--marks", help="comma-separated assembly marks (default all)")
    ap.add_argument("--policy", default="strict", choices=["strict", "any"])
    ap.add_argument("--no-gates", action="store_true")
    ap.add_argument("--no-parts", action="store_true")
    ap.add_argument("--no-nc", action="store_true")
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--prefix")
    a = ap.parse_args(argv)
    tb = json.loads(Path(a.titleblock).read_text(encoding="utf-8")) if a.titleblock else {}
    rep = run(a.ifc, a.out, tb=tb, marks=a.marks.split(",") if a.marks else None, policy=a.policy,
              gates=not a.no_gates, parts=not a.no_parts, nc=not a.no_nc, pdf=a.pdf, prefix=a.prefix)
    return 0 if all(g.get("passed") for g in rep["gates"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
