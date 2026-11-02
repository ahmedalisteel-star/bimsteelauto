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
    "build_model_v8",
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
    # The design rafter is ONE straight taper, end plate to end plate. The
    # macro's three numbers describe two haunches and a prismatic middle, so
    # the taper goes in its profile table instead - three control points on
    # one straight line, stations along the slope from the end plate face.
    l_raf = (span / 2.0 - t_ep - (d_top + t_ep)) / c
    inp["Raf_profile_ref"] = "Along slope from eaves"
    inp["Raf_profile"] = [
        0.0, inp["Raf_h_eaves"], r0.bf_top, r0.tf_top, r0.bf_bot, r0.tf_bot,
        l_raf / 2.0, inp["Raf_h_mid"], 0.0, 0.0, 0.0, 0.0,
        l_raf, inp["Raf_h_apex"], 0.0, 0.0, 0.0, 0.0,
    ]

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
    need = reach + d["CL_leg"] / 2.0 + d["ER_gap"] + 1.0
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

    import contextlib
    import io

    old_doc = mod.DOC
    mod.DOC = document_name
    mod.PEBFrame.__init__ = _init
    log = io.StringIO()
    try:
        # The macro prints its own building report - kept, not shown: build_all
        # writes it next to the model.
        with contextlib.redirect_stdout(log):
            doc = mod.build()
    finally:
        mod.PEBFrame.__init__ = orig_init
        mod.DOC = old_doc
    frame = doc.getObject("PEB_Frame")
    doc.recompute()
    build_v8.last_log = log.getvalue()
    return doc, frame, notes, inputs


# ---------------------------------------------------------------------------
# One object per piece
# ---------------------------------------------------------------------------
#
# The macro keeps every piece of a kind in ONE object - all nine columns of
# a side in Column_L, every purlin of a slope in Purlins_R. That is right for
# a parametric model and wrong for everything downstream of it: an IFC with
# one IfcColumn holding a whole side, a schedule that cannot count, a clash
# check that cannot see two purlins in the same steel because they are the
# same object. So the delivered model is the macro's solids, one Arch object
# per piece, in Layer 2's axes, carrying the design's marks, weights and
# utilisations. The macro's own document is saved beside it as the
# PARAMETRIC model.

_MACRO_KIND = {
    "PEBColumn": "column", "PEBRafter": "rafter", "PEBEndPlateV": "end_plate",
    "PEBBasePlate": "base", "PEBRoofCleats": "purlin_cleat",
    "PEBGirtClips": "girt_clip", "PEBPurlins": "purlin", "PEBGirts": "girt",
    "PEBStruts": "strut", "PEBWallBracing": "wall_bracing",
    "PEBRoofBracing": "roof_bracing",
}


def _extents(shape) -> list[float]:
    """The piece's extents along its own principal axes, longest first.

    Not the bounding box - a raking rod or a sloping rafter has a bounding
    box as long as its run and as tall as its rise - and not the longest
    edge, because a lofted member's edges are splines."""
    try:
        pp = shape.PrincipalProperties
        axes = [pp["FirstAxisOfInertia"], pp["SecondAxisOfInertia"],
                pp["ThirdAxisOfInertia"]]
    except Exception:                                   # noqa: BLE001
        bb = shape.BoundBox
        return sorted((bb.XLength, bb.YLength, bb.ZLength), reverse=True)
    pts = [v.Point for v in shape.Vertexes]
    out = []
    for a in axes:
        d = [p.dot(a) for p in pts]
        out.append(max(d) - min(d))
    return sorted(out, reverse=True)


def _longest_edge(shape) -> float:
    """Length of a member piece, along its own long axis."""
    return _extents(shape)[0]


def _is_rod(solid) -> bool:
    """A long thin bar - an anchor or a brace rod, never a plate: one
    extent far beyond the other two. The macro draws a rod brace as a
    square bar of the rod's diameter, so roundness is not the test."""
    e = _extents(solid)
    return e[0] > 4.0 * e[1] and e[1] < 100.0


def _plate_dims(solid):
    """``(t, w, h)`` of a flat plate, ``w <= h``: thickness from its volume
    over its biggest face, outline from that face's own extents."""
    import FreeCAD as App

    face = max(solid.Faces, key=lambda f: f.Area)
    t = solid.Volume / face.Area if face.Area > 0 else 0.0
    try:
        n = face.normalAt(0, 0)
    except Exception:                                   # noqa: BLE001
        e = sorted((solid.BoundBox.XLength, solid.BoundBox.YLength,
                    solid.BoundBox.ZLength))
        return e[0], e[1], e[2]
    ref = App.Vector(0, 0, 1) if abs(n.z) < 0.9 else App.Vector(1, 0, 0)
    u = n.cross(ref)
    u.normalize()
    v = n.cross(u)
    v.normalize()
    pts = [p.Point for p in face.OuterWire.Vertexes]
    us = [p.dot(u) for p in pts]
    vs = [p.dot(v) for p in pts]
    w, h = sorted((max(us) - min(us), max(vs) - min(vs)))
    return t, w, h


def _row_profile(obj) -> str | None:
    """The macro's own description of a part, off its ScheduleRows -
    ``Purlin cleat 60x6, legs 100/114`` - which says what a bent cleat or
    clip is better than any flat-plate size could."""
    rows = list(getattr(obj, "ScheduleRows", []) or [])
    if len(rows) != 1:
        return None
    desc = rows[0].split("|")[1] if "|" in rows[0] else ""
    return desc.split(" capped")[0].strip() or None


def _pl_profile(solid) -> str:
    t, w, h = _plate_dims(solid)
    return f"PL {round(t * 2.0) / 2.0:g} x {w:.0f} x {h:.0f}"


def build_model_v8(result, doc=None, *, document_name=None, macro=None):
    """The macro's building for ``result``, one Arch object per piece.

    Returns a :class:`~bimsteelauto_fc.builder.BuildReport` whose
    ``document`` is the per-piece model and whose ``param_document`` is the
    macro's own parametric document.

    Weights are the DESIGN's, as in the built-in builder: a column or rafter
    piece carries its member's weight, a purlin, girt or strut piece its
    share (by volume) of the design runs, a rod its design weight. Plates
    and anchors are weighed off their own solids, as before.
    """
    import FreeCAD as App
    import Part

    from bimsteelauto.analysis import build_layout
    from bimsteelauto.spec import MemberType

    from . import builder as bl
    from . import detailing as dt

    job = (re.sub(r"[^A-Za-z0-9_]", "_", result.spec.job_number)
           or "BIMSteelAuto")
    report = bl.BuildReport()
    pdoc, frame, notes, inputs = build_v8(
        result, document_name=(document_name or job) + "_param", macro=macro)
    report.param_document = pdoc
    report.macro_log = getattr(build_v8, "last_log", "")
    report.warnings.extend(f"choice {c}" for c in CHOICES)
    report.warnings.extend(notes)
    report.warnings.extend(f"v8: {c}" for c in (frame.Checks or ()))

    if doc is None:
        doc = App.newDocument(document_name or job)
    report.document = doc

    g = result.spec.geometry
    span = float(g.span_widths[0])
    d0 = _taper(_member(result, "column")).start.d
    ys = bl.frame_line_positions(g)
    # Macro (X along, Y across, origin on the first column base centreline)
    # to Layer 2 (X across, Y along, grid on the outer column face): a
    # quarter turn about Z and a shift. A ROTATION, never a mirror - a
    # mirrored Z purlin is a section nobody rolls. The macro's left side
    # therefore lands on the right-hand grid.
    to_l2 = App.Placement(App.Vector(span - d0 / 2.0, 0.0, 0.0),
                          App.Rotation(App.Vector(0, 0, 1), 90.0))
    side_map = {"Left": "2", "Right": "1"}

    by_mark = {m.mark: m for m in result.members}
    conns = {c.mark: c for c in (getattr(result, "connections", ()) or ())}
    layout = build_layout(g, bl._sections_from_result(result))
    fm_by_mark = {fm.mark: fm for fm in layout.members}

    def place(solid):
        sh = solid.copy()
        sh.Placement = to_l2.multiply(sh.Placement)
        return sh

    def frame_no(sh):
        y = sh.BoundBox.Center.y
        return min(range(len(ys)), key=lambda i: abs(ys[i] - y)) + 1

    groups: dict = {}

    def grp(name, label):
        if name not in groups:
            o = doc.addObject("App::DocumentObjectGroup", name)
            o.Label = label
            groups[name] = o
        return groups[name]

    counters: dict[str, int] = {}

    def label_for(mark):
        counters[mark] = counters.get(mark, 0) + 1
        return f"{mark}-{counters[mark]}"

    cos = math.cos(math.atan(float(g.roof_slope)))

    def length_of(sh, mtype):
        # a tapered member's principal axis leans with its taper, so the
        # frame members are measured the way the shop measures them
        # and with the TIGHT box: the quick one is loose round the lofted
        # (spline) faces the macro builds them from
        try:
            bb = sh.optimalBoundingBox()
        except Exception:                               # noqa: BLE001
            bb = sh.BoundBox
        if mtype in (MemberType.COLUMN, MemberType.INTERIOR_COLUMN):
            return bb.ZLength
        if mtype is MemberType.RAFTER:
            return bb.XLength / cos
        return _longest_edge(sh)

    def member(sh, mark, mtype, md, weight, group, label=None):
        label = label or label_for(mark)
        report.objects.append(bl._add_member(
            doc, sh, name=label.replace("-", "_"), mark=label,
            member_type=mtype, profile=md.section_id,
            length=length_of(sh, mtype), weight=weight,
            utilisation=md.utilisation, group=group))

    def plate(sh, mark, kind, conn_mark=None, util=None, basis=None,
              length=None, profile=None):
        label = label_for(mark)
        if util is None:
            c = conns.get(conn_mark or mark)
            util = float(getattr(c, "utilisation", 0.0) or 0.0) if c else 0.0
        report.objects.append(bl._add_plate(
            doc, sh, name=label.replace("-", "_"), mark=label, kind=kind,
            profile=profile or _pl_profile(sh), mass=dt.mass_kg(sh),
            length=(length if length is not None
                    else _plate_dims(sh)[2]),
            utilisation=util, group=grp("Connections",
                                        "Connection detailing"),
            report=report, basis=basis))

    for o in pdoc.Objects:
        cls = type(getattr(o, "Proxy", None)).__name__
        kind = _MACRO_KIND.get(cls)
        sh0 = getattr(o, "Shape", None)
        if sh0 is None or sh0.isNull() or not sh0.Solids \
                or o.TypeId.startswith("App::"):
            continue
        if kind is None:
            report.warnings.append(
                f"v8 object {o.Name} ({cls}) is not mapped to a piece type "
                "- not carried into the model")
            continue
        side = side_map.get(str(getattr(o, "Side", "")), "")
        solids = [place(s) for s in sh0.Solids]

        if kind in ("column", "rafter"):
            mark = ("C" if kind == "column" else "R") + side
            md, fm = by_mark.get(mark), fm_by_mark.get(mark)
            if md is None or fm is None:
                report.skipped.append((mark, "no MemberDesign for it"))
                continue
            for sh in solids:
                member(sh, mark, md.member_type, md, fm.taper.weight,
                       grp("Frames", "Main frames"),
                       label=f"{mark}-F{frame_no(sh)}")
        elif kind == "end_plate":
            cm = ("EC" + side) if str(getattr(o, "Role", "")) == "Eaves" \
                else "AP1"
            geom = dt.geometry_of(conns[cm]) if cm in conns else None
            for sh in solids:
                basis = None
                if geom is not None:
                    bb = sh.BoundBox
                    # the layout's v = 0 is the top flange outer face
                    basis = (App.Vector(bb.Center.x, bb.Center.y,
                                        bb.ZMax - float(geom["ext_top_mm"])),
                             App.Vector(0, 1, 0), App.Vector(0, 0, 1))
                plate(sh, cm, "end_plate", basis=basis)
        elif kind == "base":
            mark = "BP" + side
            geom = (dt.geometry_of(conns[mark]) if mark in conns else None) or {}
            rods = [s for s in solids if _is_rod(s)]
            for pl_ in (s for s in solids if not _is_rod(s)):
                c = pl_.BoundBox.Center
                plate(pl_, mark, "base_plate",
                      basis=(App.Vector(c.x, c.y, c.z),
                             App.Vector(1, 0, 0), App.Vector(0, 1, 0)))
                mine = [r for r in rods
                        if abs(r.BoundBox.Center.x - c.x) < 400.0
                        and abs(r.BoundBox.Center.y - c.y) < 400.0]
                if mine:
                    # the anchors of one base are ONE object, as before: a
                    # bag of bolts is bought, not fabricated
                    d_a = float(geom.get("anchor_diameter_mm", 0.0)) or \
                        min(mine[0].BoundBox.XLength, mine[0].BoundBox.YLength)
                    L_a = _longest_edge(mine[0])
                    plate(Part.makeCompound(mine), mark + "A", "anchor",
                          conn_mark=mark, length=L_a,
                          profile=f"M{d_a:.0f} x {L_a:.0f} anchor")
        elif kind == "purlin_cleat":
            prof = _row_profile(o)
            for sh in solids:
                plate(sh, "PC1", "purlin_cleat", profile=prof)
        elif kind == "girt_clip":
            prof = _row_profile(o)
            for sh in solids:
                plate(sh, "GC1", "girt_clip", util=0.0, profile=prof)
        elif kind in ("purlin", "girt"):
            mt = MemberType.PURLIN if kind == "purlin" else MemberType.GIRT
            md = next((m for m in result.members if m.member_type is mt),
                      None)
            if md is None:
                continue
            # md.weight is ONE full-length run; the macro cuts each run into
            # bay pieces, and this object is ONE side - one slope or one wall
            # - so its share of the design is its own runs, by volume
            runs = int(inputs.get("PU_count" if kind == "purlin"
                                  else "GT_count", 0))
            vol = sum(s.Volume for s in solids) or 1.0
            for sh in solids:
                member(sh, md.mark, mt, md, md.weight * runs * sh.Volume / vol,
                       grp("Secondary", "Purlins, girts and struts"))
        elif kind == "strut":
            md = _member(result, "eave_strut")
            tubes = [s for s in solids if _longest_edge(s) > 1000.0]
            if md is not None and tubes:
                run = ys[-1] - ys[0]
                try:
                    from bimsteelauto.sections import (TaperedMember,
                                                       WeldedI, parse_mark)

                    sec = parse_mark(md.section_id, run)
                    if isinstance(sec, WeldedI):
                        sec = TaperedMember(start=sec, end=sec, length=run)
                    # one eave run, as the built-in builder weighs it - the
                    # engine reports 0.0 for a strut it did not size
                    w_run = sec.weight
                except Exception:                       # noqa: BLE001
                    w_run = md.weight
                vol = sum(s.Volume for s in tubes) or 1.0
                for sh in tubes:
                    member(sh, md.mark, MemberType.EAVE_STRUT, md,
                           2.0 * w_run * sh.Volume / vol,
                           grp("Secondary", "Purlins, girts and struts"))
            for sh in (s for s in solids if _longest_edge(s) <= 1000.0):
                t, w, h = _plate_dims(sh)
                # pl155 is the square cap; of the two lapping plates the
                # cleat (pl153) is the longer
                tag = "P" if abs(w - h) < 1.0 else (
                    "C" if h >= float(inputs.get("ST_cl_L", 210.0)) - 1.0
                    else "F")
                plate(sh, f"ES1{tag}", "strut_plate", util=0.0)
        elif kind in ("wall_bracing", "roof_bracing"):
            mt = (MemberType.WALL_BRACING if kind == "wall_bracing"
                  else MemberType.ROOF_BRACING)
            md = next((m for m in result.members if m.member_type is mt),
                      None)
            if md is None:
                continue
            for sh in solids:
                if _is_rod(sh):
                    member(sh, md.mark, mt, md, md.weight,
                           grp("Bracing", "Longitudinal bracing"))
                else:
                    plate(sh, md.mark + "G", "bracing_gusset", util=0.0)

    doc.recompute()
    return report
