"""
G1 - the clash gate. Zero hard clashes, or nothing downstream runs.

IfcClash is used in LIBRARY mode (``ifcclash.ifcclash.Clasher``), not the
CLI. Its geometry backend in the installed 0.8.5 is
``ifcopenshell.geom.tree()`` (OpenCASCADE triangle BVH); ``hppfcl`` is not
installed and not used.

How a clash is judged
---------------------
Every intersection set is run at tolerance 0, so the tree reports EVERY
penetration with its depth. Measured on the fixture: two faces that only
touch (a plate on a beam end, a bolt head on a plate) produce no result at
all, and a plate pushed 5 mm into the beam is reported at 5.000 mm. The
gate then classifies:

    depth > CLASH_TOLERANCE_MM   ->  hard finding
    otherwise                    ->  counted, not a finding

and while ``CLASH_TOLERANCE_MM`` is ``None`` there is nothing to compare
against, so EVERY measured penetration is hard and the missing constant is
a blocker. A penetration is never waved through because nobody chose a
number.

UNITS TRAP: ``ifcopenshell.geom`` works in METRES whatever the file's
units are. Depths and points come back in metres and the clearance goes
in in metres; :data:`M_PER_MM` is the one conversion.

Fastener exclusions (set H2)
----------------------------
A bolt in its own hole is not a clash. IfcClash has no per-element
exclusion, so H2 runs fasteners against all parts and the gate then drops
each pair whose part the fastener CONNECTS - from
``IfcRelConnectsWithRealizingElements``, else from its assembly, else (an
anchor bolt) the part whose hole it passes through (see
:func:`ifcmodel.connected_parts_map`). Every dropped pair is listed in
the report with its depth and the reason: an exclusion is visible, never
silent. Known breadth: the assembly fallback excludes every part of that
assembly, so a bolt driven into its own beam's web is hidden here. The
hole >= bolt diameter check (approved for step 3, extract.py) narrows that.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.selector as selector

from . import ifcmodel
from .core import Finding, GateResult, write_report

__all__ = ["CLASH_TOLERANCE_MM", "ERECTION_TOLERANCE_MM", "BOLT_WELD_CLEARANCE_MM", "BOLT_EDGE_MIN_D",
           "CLASH_SETS_PATH", "load_clash_sets", "measure", "run_clash_gate"]

#: Penetration depth (mm) above which an intersection is a HARD clash, so
#: that touching faces do not count. 5.0 mm - user, 2026-10-26, after being
#: shown that a 4.9 mm overlap of solids then passes G1. Set back to None
#: and every measured penetration is hard and G1 cannot pass.
CLASH_TOLERANCE_MM: float | None = 5.0

#: Maximum ERECTION tolerance, mm (user, 2026-10-26). A separate constant
#: from CLASH_TOLERANCE_MM although both are 5 today: an overlap of solids
#: is not an erection deviation, and either may change alone. No check uses it yet - it is recorded for
#: the gap/clearance checks to come, so nobody has to ask for it again.
ERECTION_TOLERANCE_MM: float = 5.0

#: Minimum bolt-to-weld clearance (mm) for warning set W1. NOT CHOSEN HERE.
#: While None, W1 reports NOT EVALUATED (a warning set never blocks).
BOLT_WELD_CLEARANCE_MM: float | None = None

#: Minimum hole-centre-to-edge distance as a multiple of the bolt's nominal
#: diameter, warning set W2: AISC 360-16 J3.4, 1.25d - the same rule
#: ``bimsteelauto_connections/secondary.py`` applies (user, 2026-10-26).
BOLT_EDGE_MIN_D: float = 1.25

CLASH_SETS_PATH = Path(__file__).with_name("clash_sets.json")

#: ifcopenshell.geom works in metres.
M_PER_MM = 1.0e-3

_log = logging.getLogger("bimsteelauto.fabdraw.ifcclash")


def load_clash_sets(path=CLASH_SETS_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _selected(model, sel: str) -> set:
    return set(selector.filter_elements(model, sel))


def measure(ifc_path, set_def: dict, *, clearance_mm: float | None = None) -> list[dict]:
    """Run one IfcClash set and return its raw results, depths in mm.

    Intersection sets run at tolerance 0 (every penetration, with its
    depth); a clearance set runs at ``clearance_mm``. The caller decides
    what is hard. Returns ``[]`` when either side selects nothing.
    """
    from ifcclash.ifcclash import Clasher, ClashSettings

    model = ifcopenshell.open(str(ifc_path))
    a_sel = set_def["a"]["selector"]
    b_sel = set_def["b"]["selector"] if set_def.get("b") else None
    if not _selected(model, a_sel) or (b_sel and not _selected(model, b_sel)):
        return []

    settings = ClashSettings()
    settings.logger = _log
    clasher = Clasher(settings)
    cs = {"name": set_def["id"], "a": [{"file": str(ifc_path), "mode": "i", "selector": a_sel}],
          "mode": set_def["mode"], "check_all": bool(set_def.get("check_all", True))}
    if b_sel:
        cs["b"] = [{"file": str(ifc_path), "mode": "i", "selector": b_sel}]
    if set_def["mode"] == "intersection":
        cs["tolerance"] = 0.0
    elif set_def["mode"] == "clearance":
        if clearance_mm is None:
            raise ValueError(f"set {set_def['id']} is a clearance set and needs clearance_mm")
        cs["clearance"] = clearance_mm * M_PER_MM
    else:
        raise ValueError(f"set {set_def['id']}: mode {set_def['mode']!r} is not used by this gate")
    clasher.clash_sets = [cs]
    clasher.clash()

    out = []
    for r in cs["clashes"].values():
        out.append({
            "a": r["a_global_id"], "b": r["b_global_id"],
            "a_class": r["a_ifc_class"], "b_class": r["b_ifc_class"],
            "a_name": r["a_name"], "b_name": r["b_name"],
            "type": str(r["type"]),
            "depth_mm": r["distance"] / M_PER_MM,
            "p1_mm": [v / M_PER_MM for v in r["p1"]],
            "p2_mm": [v / M_PER_MM for v in r["p2"]],
            "p1_m": list(r["p1"]),
        })
    return out


def _write_bcf(path: Path, hard: list[Finding], title: str) -> None:
    """One BCF 2.1 topic per hard clash, viewpoint at the clash point."""
    import numpy as np
    from bcf.v2.bcfxml import BcfXml

    bcf = BcfXml.create_new(title)
    for f in hard:
        topic = bcf.add_topic(f"{f.rule}: {' / '.join(m or g for g, m in zip(f.global_ids, f.marks))}",
                              f.message, "bimsteelauto.fabdraw")
        topic.add_viewpoint_from_point_and_guids(np.array(f.data["p1_m"]), *f.global_ids)
    bcf.save(str(path))


def run_clash_gate(ifc_path, out_dir=None, *, clash_sets_path=CLASH_SETS_PATH) -> GateResult:
    """G1. Writes ``clash_report.json/.html`` and ``clash_report.bcf`` to ``out_dir``."""
    ifc_path = str(ifc_path)
    sets = load_clash_sets(clash_sets_path)
    tol = CLASH_TOLERANCE_MM
    model = ifcopenshell.open(ifc_path)

    def who(gid):
        el = model.by_guid(gid)
        return ifcmodel.mark_of(el)

    findings: list[Finding] = []
    blockers: list[str] = []
    notes: list[str] = []
    connected_map = None
    summary: dict = {"CLASH_TOLERANCE_MM": tol if tol is not None else "NOT SET",
                     "parts": len(ifcmodel.parts(model)),
                     "fasteners": len(model.by_type("IfcMechanicalFastener"))}
    if tol is None:
        blockers.append("CLASH_TOLERANCE_MM is not set (gate_clash.py) - without it a touching face cannot be "
                        "told from a clash, so every measured penetration is treated as hard")

    for s in sets["sets"]:
        sid = s["id"]
        if s.get("engine") != "ifcclash":
            if sid == "W2":
                notes.append(f"W2 bolt to edge (>= {BOLT_EDGE_MIN_D}d, AISC J3.4): NOT EVALUATED - needs hole "
                             "centres and part contours from extract.py (step 3)")
            else:
                notes.append(f"{sid}: NOT EVALUATED - engine {s.get('engine')!r} is not implemented")
            continue

        if s["mode"] == "clearance":
            if BOLT_WELD_CLEARANCE_MM is None:
                notes.append(f"{sid} {s['name']}: NOT EVALUATED - BOLT_WELD_CLEARANCE_MM is not set")
                continue
            if not _selected(model, s["b"]["selector"]):
                notes.append(f"{sid} {s['name']}: NOT EVALUATED - the model has no element matching "
                             f"{s['b']['selector']!r}")
                continue
            res = measure(ifc_path, s, clearance_mm=BOLT_WELD_CLEARANCE_MM)
            for r in res:
                findings.append(Finding(
                    sid, s["severity"],
                    f"{r['a_class']} within {BOLT_WELD_CLEARANCE_MM} mm of {r['b_class']} "
                    f"(distance {r['depth_mm']:.2f} mm)",
                    (r["a"], r["b"]), (who(r["a"]), who(r["b"])), r))
            summary[f"{sid} {s['name']}"] = f"{len(res)} within clearance"
            continue

        res = measure(ifc_path, s)
        n_within = n_excl = 0
        for r in res:
            if "fastener_connected_parts" in s.get("exclude", []):
                if connected_map is None:
                    connected_map = ifcmodel.connected_parts_map(model)
                connected, source = connected_map.get(r["a"], (set(), ""))
                if r["b"] in connected:
                    n_excl += 1
                    notes.append(f"{sid} excluded {r['a_class']} {who(r['a']) or r['a']} x {r['b_class']} "
                                 f"{who(r['b']) or r['b']}: depth {r['depth_mm']:.3f} mm, connected part "
                                 f"(from {source})")
                    continue
            if tol is not None and r["depth_mm"] <= tol:
                n_within += 1
                continue
            findings.append(Finding(
                sid, s["severity"],
                f"{r['a_class']} {r['a_name'] or ''} penetrates {r['b_class']} {r['b_name'] or ''} by "
                f"{r['depth_mm']:.3f} mm ({r['type']})",
                (r["a"], r["b"]), (who(r["a"]), who(r["b"])), r))
        summary[f"{sid} {s['name']}"] = (f"{len(res)} penetrations measured, {n_excl} excluded, "
                                         f"{n_within} within tolerance")

    result = GateResult(
        gate="G1 clash", ifc_path=ifc_path, findings=tuple(findings), blockers=tuple(blockers),
        notes=tuple(notes), summary=summary,
        citation=f"IfcClash library mode, ifcopenshell.geom.tree; sets from {Path(clash_sets_path).name}")
    if out_dir is not None:
        write_report(result, out_dir, "clash_report")
        _write_bcf(Path(out_dir) / "clash_report.bcf", list(result.hard), f"G1 clash - {Path(ifc_path).name}")
    return result
