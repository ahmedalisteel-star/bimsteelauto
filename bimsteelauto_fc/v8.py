"""
bimsteelauto_fc.v8
==================

Drive ``peb_building_param_v8`` - the parametric PEB building macro - from a
``DesignResult``.

The macro is where the shop detailing lives: drilled column flanges, the
extended end plates with their bolt groups laid out from the flanges, base
plates and anchors, roof cleats and girt clips with their holes, purlins and
girts drilled to match, eave struts on cleats and fins, bracing on gussets,
gable posts, splices. This module does not re-derive any of that. It does
two things:

1. **Maps the design onto the macro's inputs** (:func:`frame_inputs`). Every
   size, plate, bolt and set-out comes off the ``DesignResult``; where the
   design and the macro disagree on what a part IS, the choice is stated in
   :data:`CHOICES` and repeated in the build report.
2. **Builds the macro's model and hands it back** (:func:`build_v8`), without
   the macro's own ``build()`` running at import time - importing the file
   as it stands builds a default 24 m building first, which is a minute and
   a half spent on someone else's building.

Coordinates. The macro has X ALONG the building and Y ACROSS it, with the
origin at the first column's base centreline. Layer 2 has X across, Y along,
and the grid on the outer face of the column flange. :func:`to_adapter` is
the one transform between the two.
"""

from __future__ import annotations

import importlib.util
import math
import os
import re
import sys
import types

__all__ = [
    "CHOICES",
    "MacroNotFound",
    "find_macro",
    "load_macro",
    "frame_inputs",
    "build_v8",
]

#: The decisions where the design and the macro do not describe the same
#: part. Approved on 2026-10-21 against REF-2026-001; each is printed with
#: the build report so nobody has to remember them.
CHOICES: tuple[str, ...] = (
    "1 GRID: the macro's Span is column base centreline to centreline; it "
    "is set to the design span less the column base depth, and the model "
    "is shifted so the outer column flanges sit on the grids.",
    "2 EAVE STRUT: the macro builds struts as tubes on the column web; a "
    "built-up I strut is drawn as the RHS of the same depth, width and wall "
    "on the macro's strut detail. Its WEIGHT is the design's.",
    "3 ROOF BRACING: one X per slope from the eave to the design panel point "
    "(RB_panels = 1), which is the bracing that was analysed.",
    "4 GUSSETS: the macro's gussets are sized to the rod (plate t = d/2 >= 6, "
    "hole d + 2) and are NOT designed - the engine's GP1 is a placeholder "
    "bolted gusset that a rod brace does not have.",
    "5 ANCHORS: straight, as checked (embedment, grout, plate, projection), "
    "through the base plate holes - not the macro's L hook.",
    "6 ENDWALL: gable posts and endwall girts are not designed by the engine "
    "and are not built.",
)

#: Where the macro is looked for, first hit wins. ``BSA_V8_MACRO`` overrides.
_CANDIDATES = (
    r"<your-macro-folder>\peb_building_param_v8.py",
    os.path.expandvars(r"%APPDATA%\FreeCAD\v1-1\peb_building_param_v8.FCMacro"),
    os.path.expandvars(r"%APPDATA%\FreeCAD\Macro\peb_building_param_v8.FCMacro"),
)

#: The module name the macro is registered under. FeaturePython objects
#: store their proxy class by module and class name, so this is the name a
#: saved parametric model asks for when it is opened again.
MODULE_NAME = "peb_building_param_v8"


class MacroNotFound(RuntimeError):
    """The v8 macro is not where it was looked for."""


def find_macro() -> str:
    env = os.environ.get("BSA_V8_MACRO")
    for p in ((env,) if env else ()) + _CANDIDATES:
        if p and os.path.isfile(p):
            return p
    raise MacroNotFound(
        "peb_building_param_v8 was not found. Looked in: "
        + "; ".join(p for p in ((env,) if env else ()) + _CANDIDATES if p)
        + ". Set BSA_V8_MACRO to its path."
    )


def load_macro(path: str | None = None) -> types.ModuleType:
    """The macro as a module, WITHOUT its trailing ``build()`` call.

    The file ends in a bare ``build()`` so that running it as a macro builds
    the demo building. Imported as it stands, that is what an import does.
    The call is removed from the source before it is executed - only that
    line, and only if it is the last statement - and anything else that is
    different about the file is left exactly as its author wrote it.
    """
    mod = sys.modules.get(MODULE_NAME)
    if mod is not None and getattr(mod, "_BSA_LOADED_FROM", None):
        return mod
    path = path or find_macro()
    src = open(path, encoding="utf-8").read()
    lines = src.rstrip().splitlines()
    if lines and lines[-1].strip() == "build()":
        lines = lines[:-1]
    else:
        raise MacroNotFound(
            f"{path} does not end in a bare build() call - this is not the "
            "v8 macro this adapter was written against"
        )
    mod = types.ModuleType(MODULE_NAME)
    mod.__file__ = path
    mod._BSA_LOADED_FROM = path
    sys.modules[MODULE_NAME] = mod
    exec(compile("\n".join(lines) + "\n", path, "exec"), mod.__dict__)
    return mod


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------


def _geom(result, mark):
    for c in getattr(result, "connections", ()) or ():
        if c.mark == mark:
            g = ((getattr(c, "detail", None) or {}).get("geometry") or {})
            return g if g.get("available") else None
    return None


def _member(result, mtype):
    from bimsteelauto.spec import MemberType

    mt = MemberType(mtype)
    return next((m for m in result.members if m.member_type is mt), None)


def _taper(md):
    from bimsteelauto.sections import TaperedMember, WeldedI, parse_mark

    sec = parse_mark(md.section_id, md.length)
    if isinstance(sec, WeldedI):
        sec = TaperedMember(start=sec, end=sec, length=md.length)
    return sec


def _setout(length, spacing):
    from .fabrication import setout

    return setout(length, spacing)


def _rod_d(section_id) -> float | None:
    m = re.search(r"M(\d+(?:\.\d+)?)\s*rod", str(section_id), re.I)
    return float(m.group(1)) if m else None


def frame_inputs(result) -> tuple[dict, list[str]]:
    """``(inputs, notes)``: every ``PEB_Frame`` property this design sets,
    and the notes the report should carry.

    Pure arithmetic on the ``DesignResult`` - no FreeCAD - so it can be read
    and checked on its own. Anything the design does not carry is left at
    the macro's default and is NOT in the dict.
    """
    from .fabrication import cf_dims, rod_plate_geometry

    g = result.spec.geometry
    notes: list[str] = []
    if len(g.span_widths) != 1 or str(getattr(g.roof_type, "value", g.roof_type)) != "gable":
        raise NotImplementedError(
            "the v8 drive covers a single-span gable frame; this one is "
            f"{len(g.span_widths)} span(s), {g.roof_type}"
        )
    span = float(g.span_widths[0])
    H = float(g.eave_height)
    m = float(g.roof_slope)
    c = math.cos(math.atan(m))

    col = _taper(_member(result, "column"))
    raf = _taper(_member(result, "rafter"))
    c0, c1 = col.start, col.end
    r0, r1 = raf.start, raf.end
    ec = _geom(result, "EC1")
    ap = _geom(result, "AP1")
    bp = _geom(result, "BP1")
    pc = _geom(result, "PC1")

    t_ep = float(ec["thickness_mm"]) if ec else 25.0
    d_base, d_top = c0.d, c1.d

    inp: dict = {
        # 01 building
        "Span": span - d_base,
        "EavesHeight": H + m * (d_top + t_ep),
        "EavesHeight_ref": "Top of steel at eaves",
        "Slope": m,
        "NumBays": len(g.bay_spacings),
        "BaySpacing": float(g.bay_spacings[0]),
        "BaySpacings": [float(b) for b in g.bay_spacings],
        # a straight taper is the legacy three points with the haunch at
        # (just under) half the rafter and the mid depth halfway - set below
        "PurlinSpacing": float(g.purlin_spacing),
        "DetailLevel": "Full",
        # 02 column - outer flange = the section's top flange, as fabrication.py
        "Col_h_base": c0.d - c0.tf_top - c0.tf_bot,
        "Col_h_top": c1.d - c1.tf_top - c1.tf_bot,
        "Col_tw": c0.tw,
        "Col_bf_out": c0.bf_top, "Col_tf_out": c0.tf_top,
        "Col_bf_in": c0.bf_bot, "Col_tf_in": c0.tf_bot,
        "Col_taper_ref": "bottom",
        # 03 rafter - a straight taper is the legacy three points with the
        # mid depth halfway and the haunch at half the rafter
        "Raf_h_eaves": r0.d - r0.tf_top - r0.tf_bot,
        "Raf_h_apex": r1.d - r1.tf_top - r1.tf_bot,
        "Raf_tw": r0.tw,
        "Raf_bf_top": r0.bf_top, "Raf_tf_top": r0.tf_top,
        "Raf_bf_bot": r0.bf_bot, "Raf_tf_bot": r0.tf_bot,
        "Raf_taper_ref": "top",
        "Raf_flange_symmetric": (r0.bf_top == r0.bf_bot
                                 and r0.tf_top == r0.tf_bot),
        # splice, portal, gable, endwall, interior - not in the design
        "RS_enable": False, "PF_enable": False, "EG_enable": False,
        "EW_enable": False, "IC_enable": False,
        "ER_gap": 5.0,
    }
    inp["Raf_h_mid"] = (inp["Raf_h_eaves"] + inp["Raf_h_apex"]) / 2.0
    l_raf = (span / 2.0 - t_ep - (d_top + t_ep)) / c
    inp["HaunchLen"] = l_raf / 2.0 - 2.0

    # 04 end plates
    if ec:
        rows = [float(r) for r in ec.get("row_offsets_mm", ())]
        tft = float(ec.get("tf_top_on_cut_mm", r0.tf_top / c))
        dv = float(ec["depth_on_cut_mm"])
        inner_top = sorted(r for r in rows if 0.0 < r < dv / 2.0)
        gauge = [float(x) for x in ec.get("gauge_mm", ())]
        inp.update({
            "EP_eaves_type": "Flush end plate",
            "EP_t": t_ep,
            "EP_AutoWidth": False,
            "EP_W": float(ec["width_mm"]),
            "EP_ext_top_eaves": float(ec["ext_top_mm"]),
            "EP_ext_bot_eaves": float(ec["ext_bot_mm"]),
            "EP_e_out": (-min(rows) if rows and min(rows) < 0 else 60.0),
            "EP_e_in": (inner_top[0] - tft) if inner_top else 50.0,
            "EP_rows_in": max(1, len(inner_top)),
            "EP_d": float(ec["hole_diameter_mm"]),
            "EP_bolts_row": max(1, len(gauge)),
            "EP_gauge": (max(gauge) - min(gauge)) if len(gauge) > 1 else 0.0,
            "EP_drill_column": True,
            # the knee zone: the plate below the eave plus 30 mm
            "Col_conn_straight": dv + float(ec["ext_bot_mm"]) + 30.0,
        })
        if len(inner_top) > 1:
            inp["EP_pitch"] = inner_top[1] - inner_top[0]
    if ap:
        inp["EP_ext_top_apex"] = float(ap["ext_top_mm"])
        inp["EP_ext_bot_apex"] = float(ap["ext_bot_mm"])
        if ec and (abs(float(ap["thickness_mm"]) - t_ep) > 1e-6
                   or abs(float(ap["width_mm"]) - float(ec["width_mm"])) > 1e-6):
            notes.append(
                "AP1 and EC1 differ in thickness or width; the macro takes "
                "one EP_t and one EP_W for both, so the apex is built to EC1"
            )

    # 05 base plate - the macro's X is ALONG the building
    if bp:
        holes = [(float(u), float(v)) for u, v in bp.get("holes_uv_mm", ())]
        us = sorted({round(u, 3) for u, _ in holes})
        vs = sorted({round(v, 3) for _, v in holes})
        inp.update({
            "BP_auto": False,
            "BP_B": float(bp["N_mm"]),            # along the building
            "BP_N": float(bp["B_mm"]),            # across the frame
            "BP_t": float(bp["thickness_mm"]),
            "BP_grout": float(bp.get("grout_mm", 30.0)),
            "AB_n": int(bp.get("n_anchors", len(holes))),
            "AB_d": float(bp["anchor_diameter_mm"]),
            "AB_gx": (vs[-1] - vs[0]) if len(vs) > 1 else 0.0,
            "AB_gy": (us[-1] - us[0]) if len(us) > 1 else 0.0,
            "AB_embed": float(bp["anchor_embedment_mm"]),
            "AB_proj": float(bp["anchor_projection_mm"]),
            "AB_shape": "straight",
        })

    # 06 roof cleats - the welded flat plate the cleat check ran on
    if pc:
        inp.update({
            "CL_w": float(pc["width_mm"]),
            "CL_h": float(pc["height_mm"]),
            "CL_t": float(pc["thickness_mm"]),
            "CL_up_d": float(pc["hole_diameter_mm"]),
            "CL_up_e": float(pc.get("edge_end_mm", 30.0)),
            "CL_up_pitch": float(pc.get("pitch_mm", 60.0)),
            "CL_drill_rafter": False,
        })

    # 08 purlins - the design's set-out, measured from the grid along the
    # slope; the macro measures from the rafter start (the end plate face)
    pu = _member(result, "purlin")
    if pu is not None:
        s_raf = (d_top + t_ep) / c
        purl = _setout(raf.length, float(g.purlin_spacing))
        inp.update({
            "PU_enable": True,
            "PU_section_custom": pu.section_id,
            "PU_setout": "Eave",
            "PU_first": (purl[0] - s_raf) if purl else float(g.purlin_spacing),
            "PU_count": len(purl),
            "PU_end": 0.0,
            # the purlin was designed CONTINUOUS (the cleat demand is the
            # 1.1 wL of a continuous beam), which on site is a lapped Z
            "PU_stagger": True,
        })
    gi = _member(result, "girt")
    if gi is not None:
        girts = _setout(col.length, float(g.girt_spacing))
        inp.update({
            "GI_enable": bool(girts), "GT_enable": bool(girts),
            "GI_section_custom": gi.section_id,
            "GT_setout": "Base",
            "GT_start": girts[0] if girts else float(g.girt_spacing),
            "GT_spacing": float(g.girt_spacing),
            "GT_count": len(girts),
            "GT_end": 0.0,
            # the clip's two bolts on the girt web, on the cleat's own
            # pattern, so they land inside a 150 web
            "GC_up_e": float(pc.get("edge_end_mm", 30.0)) if pc else 30.0,
            "GC_up_pitch": float(pc.get("pitch_mm", 60.0)) if pc else 60.0,
            "GC_up_d": float(pc["hole_diameter_mm"]) if pc else 14.0,
        })

    # 10 eave strut - choice 2
    es = _member(result, "eave_strut")
    if es is not None:
        cf = cf_dims(es.section_id)
        if cf is not None:
            name = es.section_id
        else:
            s = _taper(es).start
            name = f"RHS{s.d:g}x{max(s.bf_top, s.bf_bot):g}x{s.tw:g}"
            notes.append(
                f"eave strut {es.section_id} drawn as {name} on the macro's "
                "tube strut detail (choice 2); weight from the design"
            )
        dims = [float(x) for x in re.findall(r"[\d.]+", name)[:2]]
        inp.update({"ST_enable": True, "ST_section_custom": name,
                    "ST_count": 1,
                    # the cap closes the tube end, so it covers the tube
                    "ST_cap_w": max(dims + [140.0]) + 10.0})

    # 11 bracing - choices 3 and 4
    br = getattr(result, "bracing", None)
    rb, wb = _member(result, "roof_bracing"), _member(result, "wall_bracing")
    if br is not None and br.is_stated:
        inp["BracedBays"] = [int(b) + 1 for b in br.braced_bays]
        inp["BR_count"] = len(br.braced_bays)
        if br.n_braced_walls not in (0, 2):
            notes.append(
                f"the design braces {br.n_braced_walls} sidewall(s); the "
                "macro braces both or neither - both are built"
            )
        inp["BR_wall"] = br.n_braced_walls > 0 and wb is not None
    inp["BR_roof"] = rb is not None
    inp["BR_cross"] = "X"
    rods = []
    for md, key in ((wb, "WB"), (rb, "RB")):
        if md is None:
            continue
        d = _rod_d(md.section_id)
        if d is None:
            notes.append(f"{md.mark} {md.section_id} is not a rod - left on "
                         "the macro's brace defaults")
            continue
        inp[f"{key}_section_custom"] = f"ROD{d:g}"
        rods.append(d)
    if rods:
        rp = rod_plate_geometry(max(rods))
        inp.update({"GS_t": rp["thickness_mm"], "GS_d": rp["hole_diameter_mm"]})
    if rb is not None:
        splits = max(1, int(getattr(br, "roof_panel_splits", 1) or 1))
        s_raf = (d_top + t_ep) / c
        l_raf = (span / 2.0 - t_ep - (d_top + t_ep)) / c
        s_panel = raf.length / splits - s_raf
        s_panel = _clear_of_cleats(s_panel, inp, l_raf, notes)
        inp.update({"RB_panels": 1, "RB_s1": 150.0,
                    "RB_end_clear": max(l_raf - s_panel, 150.0)})
    inp.update({"WB_z_bot": 150.0, "WB_z_top": H - 150.0})
    return inp, notes


#: The macro's gusset and cleat set-out defaults, used to keep a roof brace
#: node clear of the purlin cleats when they are not overridden.
_V8_DEFAULTS = {"GS_e1": 60.0, "GS_pitch": 70.0, "GS_bolts": 2,
                "GS_edge": 40.0, "GS_setback": 20.0, "CL_leg": 100.0,
                "ER_gap": 5.0}


def _clear_of_cleats(s_node, inp, l_raf, notes):
    """The roof brace node nearest the design panel point whose gusset
    clears every purlin cleat. The macro checks this and reports a clash
    when it fails; the design set the panel point without knowing where the
    cleats are, so it is moved the least distance that works and the move is
    reported."""
    d = dict(_V8_DEFAULTS)
    d.update({k: inp[k] for k in d if k in inp})
    reach = (d["GS_e1"] + (int(d["GS_bolts"]) - 1) * d["GS_pitch"]
             + d["GS_edge"] + d["GS_setback"])
    need = reach + d["CL_leg"] / 2.0 + d["ER_gap"]
    first = inp.get("PU_first")
    if first is None:
        return s_node
    cleats = [first + k * inp["PurlinSpacing"]
              for k in range(int(inp.get("PU_count", 0)))]

    def ok(s):
        return all(abs(s - p) >= need for p in cleats) and 300.0 < s < l_raf - 150.0

    if ok(s_node):
        return s_node
    best = None
    for p in cleats:
        for cand in (p - need, p + need):
            if ok(cand) and (best is None
                             or abs(cand - s_node) < abs(best - s_node)):
                best = cand
    if best is None:
        notes.append("no roof brace node clears the purlin cleats - built at "
                     "the design panel point; the macro reports the clash")
        return s_node
    notes.append(
        f"roof brace panel point moved {best - s_node:+.0f} mm along the "
        f"rafter (to s = {best:.0f} from the end plate face) so its gusset "
        "clears the purlin cleat"
    )
    return best


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


def build_v8(result, *, document_name: str = "BSA_v8", macro=None):
    """Build the macro's model for ``result`` and return ``(doc, frame,
    notes)``.

    The macro's ``build()`` is used as it stands - it is the one place that
    knows which objects a building has and how they link - with two things
    set first: the document name, and the frame's inputs, which are applied
    the moment the frame object is created so the first recompute is
    already this building and not the demo.
    """
    import FreeCAD as App

    mod = macro or load_macro()
    inputs, notes = frame_inputs(result)
    orig_init = mod.PEBFrame.__init__

    def _init(self, obj):
        orig_init(self, obj)
        for k, v in inputs.items():
            try:
                setattr(obj, k, v)
            except Exception as exc:                    # noqa: BLE001
                notes.append(f"could not set {k} = {v!r}: {exc}")

    old_doc = mod.DOC
    mod.DOC = document_name
    mod.PEBFrame.__init__ = _init
    try:
        doc = mod.build()
    finally:
        mod.PEBFrame.__init__ = orig_init
        mod.DOC = old_doc
    frame = doc.getObject("PEB_Frame")
    doc.recompute()
    return doc, frame, notes, inputs
