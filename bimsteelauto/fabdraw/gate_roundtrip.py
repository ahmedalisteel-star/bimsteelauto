"""
G3 - the round trip. Every sheet is read back from its DXF and checked
against the model it was drawn from, before it may be issued.

The sheet is not trusted: what G3 reads is the DXF as a CAD user would get
it - text on the DIM layer, circles on the HOLE layer, cells of the tables -
plus the view definitions the generator left on the unplotted ``BSA_VIEW``
layer (a point per view: where the view's origin landed on the paper, its
scale and its 3D frame). With those it maps paper back to model millimetres
and compares with the IFC geometry (:class:`~.extract.FabModel`).

Rules::

    R1  hard     every running-dimension value equals its own tick's signed distance
                 from the zero tick, read on the paper and scaled (the number
                 says what the drawing shows)
    R2  hard     every tick sits on a feature of the steel: a hole centre, a
                 part's edge or centre (the drawing shows something real)
    R3  hard     every round hole of the sheet's parts is drawn face-on in some
                 view, as a circle at its own centre - for a repeated part mark,
                 one instance drawn face-on covers the mark
    R4  hard     the material list / part table / allocation say the IFC's
                 quantities and weights
    R5  hard     nothing is drawn outside the sheet's border
    R6  warning  text boxes that overlap (layout quality; never blocks)

Known limit: G3 checks the sheet against the same extraction it was drawn
from (``extract.read_model``). An extraction error that is consistent - a
wrong frame, a hole read at the wrong place - passes G3; G2 and the
fixtures in ``tests/fabdraw`` are what guard the extraction.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .core import Finding, GateResult, write_report
from .extract import FabModel, Part

__all__ = ["run_roundtrip_gate", "check_sheet"]

APP = "BSA_FABDRAW"
TOL_VALUE = 0.1          # mm: R1, printed value against its own geometry
TOL_FEATURE = 0.6        # mm: R2 / R3, a tick or a circle against the IFC feature


def _xd(e) -> dict:
    try:
        tags = e.get_xdata(APP)
    except Exception:          # noqa: BLE001 - no xdata
        return {}
    out = {}
    for code, val in tags:
        if code == 1000 and "=" in str(val):
            k, v = str(val).split("=", 1)
            out[k] = v
    return out


def _views(msp) -> dict:
    out = {}
    for p in msp.query('POINT[layer=="BSA_VIEW"]'):
        d = _xd(p)
        if "view" not in d:
            continue
        fr = [float(x) for x in d["frame"].split(";")]
        brk = [tuple(float(t) for t in ab.split(":")) for ab in d.get("breaks", "").split(",") if ab]
        out[d["view"]] = {"px": p.dxf.location.x, "py": p.dxf.location.y, "scale": float(d["scale"]),
                          "bx0": float(d["bx0"]), "by0": float(d["by0"]), "o": np.array(fr[0:3]),
                          "ex": np.array(fr[3:6]), "ey": np.array(fr[6:9]), "ez": np.array(fr[9:12]),
                          "breaks": brk, "gap": float(d.get("gap", 0) or 0)}
    return out


def _unbreak(V, xs: float) -> float:
    """Shortened view x -> true view x (the inverse of ``View.mx``)."""
    shift = 0.0
    for a, b in V["breaks"]:
        a2 = a - shift
        if xs <= a2:
            break
        b2 = a2 + V["gap"]
        if xs >= b2:
            shift += (b - a) - V["gap"]
        else:
            return a + (xs - a2) * (b - a) / V["gap"]
    return xs + shift


def _break(V, x: float) -> float:
    """True view x -> shortened view x (``View.mx``)."""
    shift = 0.0
    for a, b in V["breaks"]:
        if x <= a:
            break
        if x >= b:
            shift += (b - a) - V["gap"]
        else:
            shift += (x - a) * (1.0 - V["gap"] / (b - a))
            break
    return x - shift


def _to_view(V, axis, paper):
    if axis == "x":
        return _unbreak(V, V["bx0"] + (paper - V["px"]) * V["scale"])
    return V["by0"] + (paper - V["py"]) * V["scale"]


def _features(parts: list[Part], V) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    for p in parts:
        d = p.verts - V["o"]
        x, y = d @ V["ex"], d @ V["ey"]
        xs += [x.min(), x.max(), (x.min() + x.max()) / 2]
        ys += [y.min(), y.max(), (y.min() + y.max()) / 2]
        for h in p.holes:
            c = h.center - V["o"]
            xs.append(c @ V["ex"])
            ys.append(c @ V["ey"])
        # the toe of an end cut: every vertex x is a candidate feature (end depths, cut ends)
        xs += list(np.unique(np.round(x, 2)))
    return np.array(xs), np.array(ys)


def check_sheet(path, parts: list[Part], *, kind: str, qty_expect: dict) -> tuple[list[Finding], dict]:
    import ezdxf
    from ezdxf import bbox

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    name = Path(path).stem
    F: list[Finding] = []
    views = _views(msp)
    stats = Counter()
    feats = {k: _features(parts, V) for k, V in views.items()}
    # R1 / R2 - running dimensions
    for t in msp.query('TEXT[layer=="DIM"]'):
        d = _xd(t)
        if "tick" not in d or d.get("view") not in views:
            continue
        V = views[d["view"]]
        ax = d["axis"]
        tick, zero = _to_view(V, ax, float(d["tick"])), _to_view(V, ax, float(d["zero"]))
        try:
            val = float(t.dxf.text)            # what is PRINTED, not what the generator meant
        except ValueError:
            F.append(Finding("R1", "hard", f"{name} {d['view']}: running dimension text {t.dxf.text!r} is not a "
                                           "number", marks=(name,)))
            continue
        stats["dims"] += 1
        # signed: a feature before the set-out end (a plate standing proud of the member) reads negative
        if abs(val - (tick - zero)) > TOL_VALUE + 1e-4 * V["scale"]:
            F.append(Finding("R1", "hard", f"{name} {d['view']}: running dimension says {t.dxf.text}, its tick is "
                                           f"{tick - zero:.2f} from zero", marks=(name,)))
        fx, fy = feats[d["view"]]
        arr = fx if ax == "x" else fy
        for v_ in (tick, zero):
            if not len(arr) or np.abs(arr - v_).min() > TOL_FEATURE:
                F.append(Finding("R2", "hard", f"{name} {d['view']}: a {ax}-tick at {v_:.1f} is on no hole or "
                                               "part edge", marks=(name,)))
                break
    # R1 for real DIMENSION entities: CAD measurement x DIMLFAC is the value meant, at the view's scale
    for dim in msp.query("DIMENSION"):
        d = _xd(dim)
        if "value" not in d or d.get("view") not in views:
            continue
        V = views[d["view"]]
        try:
            lfac = float(dim.override().get("dimlfac"))
            meas = float(dim.get_measurement()) * lfac
            txt = (dim.dxf.get("text", "") or "").strip()
            if txt not in ("", "<>"):
                # a shortened view: the text is what is read; it must equal the break-corrected length
                p1, p2 = dim.dxf.defpoint2, dim.dxf.defpoint3
                if abs(float(dim.dxf.get("angle", 0.0))) < 1e-6:
                    meas = abs(_to_view(V, "x", p2.x) - _to_view(V, "x", p1.x))
                if abs(float(txt) - meas) > TOL_VALUE + 1e-4 * V["scale"]:
                    F.append(Finding("R1", "hard", f"{name} {d['view']}: dimension '{d.get('what')}' reads {txt}, "
                                                   f"the drawing (breaks restored) gives {meas:.2f}", marks=(name,)))
        except Exception as e:          # noqa: BLE001 - reported
            F.append(Finding("R1", "hard", f"{name} {d['view']}: dimension '{d.get('what')}' cannot be measured: {e}",
                             marks=(name,)))
            continue
        stats["dims"] += 1
        if abs(lfac - V["scale"]) > 1e-6:
            F.append(Finding("R1", "hard", f"{name} {d['view']}: dimension '{d.get('what')}' DIMLFAC {lfac:g} is not "
                                           f"the view scale {V['scale']:g}", marks=(name,)))
        elif abs(meas - float(d["value"])) > TOL_VALUE + 1e-4 * V["scale"]:
            F.append(Finding("R1", "hard", f"{name} {d['view']}: dimension '{d.get('what')}' measures {meas:.2f}, "
                                           f"was drawn for {d['value']}", marks=(name,)))
    # R3 - holes drawn face-on at their centres
    circles = defaultdict(list)
    for c in msp.query('CIRCLE[layer=="HOLE"]'):
        circles["all"].append((c.dxf.center.x, c.dxf.center.y))
    by_mark_ok: dict[str, bool] = {}
    for p in parts:
        if not p.holes:
            continue
        ok_all = True
        for h in p.holes:
            seen = False
            for V in views.values():
                if abs(float(h.axis @ V["ez"])) < 0.9986:
                    continue
                c = h.center - V["o"]
                px = V["px"] + (_break(V, c @ V["ex"]) - V["bx0"]) / V["scale"]
                py = V["py"] + ((c @ V["ey"]) - V["by0"]) / V["scale"]
                tol = TOL_FEATURE / V["scale"] + 0.01
                if any(abs(px - x) <= tol and abs(py - y) <= tol for x, y in circles["all"]):
                    seen = True
                    break
            ok_all &= seen
            stats["holes"] += 1
        by_mark_ok[p.mark] = by_mark_ok.get(p.mark, False) or ok_all
    for mk, ok in by_mark_ok.items():
        if not ok:
            n = sum(len(p.holes) for p in parts if p.mark == mk)
            F.append(Finding("R3", "hard", f"{name}: the holes of part {mk} ({n}) are not all drawn face-on in any "
                                           "view, so their positions are not dimensioned", marks=(mk,)))
    # R4 - tables
    cells = defaultdict(dict)
    for t in msp.query("TEXT"):
        d = _xd(t)
        if "tbl" in d:
            cells[(d["tbl"], int(d["row"]))][d["col"]] = t.dxf.text
    if kind == "assembly":
        exp = Counter(p.mark for p in parts)
        wexp = defaultdict(float)
        for p in parts:
            wexp[p.mark] += p.weight
        got = {}
        for (tbl, _r), row in cells.items():
            if tbl == "material" and row.get("Mark"):
                got[row["Mark"]] = row
        for mk, n in exp.items():
            row = got.get(mk or "?")
            if row is None:
                F.append(Finding("R4", "hard", f"{name}: part {mk} is not in the material list", marks=(mk,)))
                continue
            if int(row.get("No.", "-1")) != n:
                F.append(Finding("R4", "hard", f"{name}: material list says {row.get('No.')} x {mk}, the IFC has {n}",
                                 marks=(mk,)))
            if abs(float(row.get("Weight", "nan")) - wexp[mk]) > 0.051 + 0.001 * wexp[mk]:
                F.append(Finding("R4", "hard", f"{name}: material list weight of {mk} {row.get('Weight')} kg, IFC "
                                               f"{wexp[mk]:.2f} kg", marks=(mk,)))
        extra = set(got) - {m or "?" for m in exp}
        for mk in extra:
            F.append(Finding("R4", "hard", f"{name}: material list has {mk}, which is not in the assembly", marks=(mk,)))
    else:
        rows = [r for (tbl, _), r in cells.items() if tbl == "part"]
        q = sum(qty_expect.values())
        if not rows or int(rows[0].get("QTY", "-1")) != q:
            F.append(Finding("R4", "hard", f"{name}: part table QTY {rows[0].get('QTY') if rows else 'missing'}, "
                                           f"the IFC needs {q}", marks=(name,)))
        alloc = {r.get("Assembly Mark"): r.get("Quantity") for (tbl, _), r in cells.items() if tbl == "allocation"}
        for am, n in qty_expect.items():
            if am in alloc and str(n) != str(alloc[am]):
                F.append(Finding("R4", "hard", f"{name}: allocation to {am} says {alloc[am]}, the IFC has {n}",
                                 marks=(name,)))
    # R5 - inside the border
    trim = list(msp.query('LWPOLYLINE[layer=="BSA_VIEW"]'))
    if trim:
        pts = list(trim[0].get_points("xy"))
        W, H = max(p[0] for p in pts), max(p[1] for p in pts)
        ents = [e for e in msp if e.dxf.layer != "BSA_VIEW"]
        ext = bbox.extents(ents, fast=True)
        if ext.has_data and (ext.extmin.x < -0.5 or ext.extmin.y < -0.5 or ext.extmax.x > W + 0.5
                             or ext.extmax.y > H + 0.5):
            F.append(Finding("R5", "hard", f"{name}: drawing runs outside the sheet "
                                           f"({ext.extmin.x:.0f},{ext.extmin.y:.0f})-({ext.extmax.x:.0f},"
                                           f"{ext.extmax.y:.0f}) on {W:.0f}x{H:.0f}", marks=(name,)))
    return F, dict(stats)


def run_roundtrip_gate(M: FabModel, out_dir, *, prefix: str = "", sheets: list[dict] | None = None) -> GateResult:
    """G3 over every sheet in ``out_dir/ASSEM`` and ``out_dir/SINGLE``. Writes ``gates/roundtrip_report``."""
    out_dir = Path(out_dir)
    F: list[Finding] = []
    tot = Counter()
    n_sheets = 0
    for a in M.assemblies:
        p = out_dir / "ASSEM" / f"{prefix}{a.mark}.dxf"
        if not p.is_file():
            continue
        f, st = check_sheet(p, a.parts, kind="assembly", qty_expect={})
        F += f
        tot.update(st)
        n_sheets += 1
    for mk, ps in M.parts_by_mark.items():
        p = out_dir / "SINGLE" / f"{prefix}{mk}.dxf"
        if not p.is_file():
            continue
        f, st = check_sheet(p, [ps[0]], kind="part", qty_expect=dict(M.allocation[mk]))
        F += f
        tot.update(st)
        n_sheets += 1
    for s in sheets or ():
        if s.get("overlaps"):
            F.append(Finding("R6", "warning", f"{Path(s['path']).stem}: {s['overlaps']} overlapping text boxes",
                             marks=(s["mark"],)))
        for w in s.get("warnings") or ():
            F.append(Finding("R6", "warning", w, marks=(s["mark"],)))
    summary = {"sheets": n_sheets, "running-dimension values": tot["dims"], "holes checked": tot["holes"]}
    for r in ("R1", "R2", "R3", "R4", "R5", "R6"):
        summary[r] = sum(f.rule == r for f in F)
    res = GateResult(gate="G3 round trip", ifc_path=M.ifc_path, findings=tuple(F), summary=summary,
                     citation="gate_roundtrip.py R1-R6")
    (out_dir / "gates").mkdir(parents=True, exist_ok=True)
    write_report(res, out_dir / "gates", "roundtrip_report")
    return res
