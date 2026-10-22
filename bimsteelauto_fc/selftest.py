"""
bimsteelauto_fc.selftest
========================

Layer 2's test suite.

It does not use pytest, because FreeCAD's bundled interpreter does not ship
it and adding a 3.12 site-packages to a 3.11 runtime is a worse problem than
writing twenty lines of harness.  ``tests/test_fc_adapter.py`` in the main
suite shells out to this and asserts it passed, so Layer 2 is covered by a
plain ``pytest`` run even though the checks execute inside FreeCAD.

Run it directly::

    freecadcmd bimsteelauto_fc/selftest.py

The checks that matter are the ones that would otherwise pass silently:
a model that builds the right NUMBER of objects in the wrong PLACES, a
solid with no volume, an elevation drawn on its side.  Each of those was a
real defect during development, and each has a check here named after it.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

_FAILURES: list[str] = []
_PASSED = 0


def check(name: str, condition, detail: str = "") -> bool:
    global _PASSED
    if condition:
        _PASSED += 1
        print(f"  PASS  {name}", flush=True)
        return True
    _FAILURES.append(f"{name}: {detail}")
    print(f"  FAIL  {name}  {detail}", flush=True)
    return False


def close(a: float, b: float, rel: float = 1e-3) -> bool:
    if b == 0:
        return abs(a) < 1e-9
    return abs(a - b) / abs(b) <= rel


def _pitches(values):
    """Gaps between neighbouring set-out values, deduplicated."""
    vs = sorted({round(float(v), 3) for v in values})
    return sorted({round(b - a, 3) for a, b in zip(vs, vs[1:])})


def run(json_path: str, out_dir: str) -> int:
    import FreeCAD as App

    from bimsteelauto_fc.builder import (
        _axes, _cold_formed_dims, _run_lines, _sections_from_result,
        build_model, load_result, member_solid,
    )
    from bimsteelauto_fc.drawings import (
        _orient, general_arrangement, single_part_drawings,
    )
    from bimsteelauto_fc.ifc import export_ifc, summarise_ifc
    from bimsteelauto_fc.schedule import member_schedule, schedule_totals
    from bimsteelauto.analysis import build_layout
    from bimsteelauto.spec import MemberType

    print("bimsteelauto_fc selftest")
    print("-" * 60)

    result = load_result(json_path)
    check("json round-trips into a DesignResult", bool(result.members),
          f"{len(result.members)} members")

    # ---- geometry primitives -----------------------------------------
    print("geometry")
    sections = _sections_from_result(result)
    layout = build_layout(result.spec.geometry, sections)

    col = next(m for m in layout.members
               if m.member_type is MemberType.COLUMN)
    sh = member_solid(
        col.taper, App.Vector(0, 0, 0), App.Vector(0, 0, col.taper.length)
    )
    check("a lofted member is a closed solid", len(sh.Solids) == 1,
          f"solids={len(sh.Solids)}")
    mass = sh.Volume * 1e-9 * 7850.0
    check("the solid's mass matches the engine's own weight",
          close(mass, col.taper.weight, 0.005),
          f"solid {mass:.1f} kg vs engine {col.taper.weight:.1f} kg")

    # The mutation bug: positions must stay inside the building.
    lines = _run_lines(layout, result.spec.geometry)
    span = result.spec.geometry.width
    height = result.spec.geometry.ridge_height
    inside = all(
        -1.0 <= b.x <= span + 1.0 and -1.0 <= b.z <= height + 1.0
        for _k, b, _a in lines
    )
    check("purlin and girt positions stay on the building", inside,
          "a Vector.multiply() that mutates in place sends these "
          "exponentially off the building")
    check("there are purlins AND girts",
          {k for k, _, _ in lines} == {"purlin", "girt"})

    # Orientation: a member running along the building must not be
    # oriented with u parallel to its own axis.
    a, u, v = _axes(App.Vector(0, 0, 7000), App.Vector(0, 34500, 7000))
    check("a member along the building gets a valid section basis",
          abs(a.dot(u)) < 1e-6 and abs(a.dot(v)) < 1e-6,
          f"axis={a} u={u} v={v}")
    check("the section basis keeps 'up' up", v.z > 0.9, f"v={v}")

    check("a catalogue cold-formed name parses",
          _cold_formed_dims("C200x60x20x2.5") == (200.0, 60.0))
    check("a name that is not a catalogue pattern is refused",
          _cold_formed_dims("some purlin") is None)

    # ---- the build ---------------------------------------------------
    print("build")
    rep = build_model(result)
    doc = rep.document
    check("every member was built - nothing skipped", not rep.skipped,
          "; ".join(f"{m}: {w}" for m, w in rep.skipped[:3]))
    check("the model has objects", rep.n_built > 0, f"{rep.n_built}")

    n_frames = result.spec.geometry.n_frames
    n_main = len([o for o in rep.objects
                  if str(o.MemberType) in ("column", "rafter")])
    expected = n_frames * len(
        [m for m in layout.members
         if m.member_type in (MemberType.COLUMN, MemberType.RAFTER)]
    )
    check("one frame is built per frame line", n_main == expected,
          f"{n_main} main members, expected {expected}")

    vols = [o.Shape.Volume for o in rep.objects]
    check("no object is an empty shell", all(v > 1.0 for v in vols),
          f"{sum(1 for v in vols if v <= 1.0)} objects have no volume")

    bb = None
    for o in rep.objects:
        bb = o.Shape.BoundBox if bb is None else bb.united(o.Shape.BoundBox)
    g = result.spec.geometry
    check("the model fits the building envelope",
          bb.XLength < g.width * 1.2 and bb.YLength < g.length * 1.2
          and bb.ZLength < g.ridge_height * 1.5,
          f"bbox {bb.XLength:.0f} x {bb.YLength:.0f} x {bb.ZLength:.0f} vs "
          f"building {g.width:.0f} x {g.length:.0f} x {g.ridge_height:.0f}")

    # ---- placement ---------------------------------------------------
    #
    # The centreline model put every solid on its analysis line and had
    # 652 hard clashes on REF-2026-001 while passing every check above. So
    # the conventions are checked directly, and then the whole model is
    # clash-checked: two parts sharing more than 50 mm3 of steel is a
    # model that cannot be built.
    print("placement")
    by_label = {str(o.Mark): o for o in rep.objects}
    c1, c2 = by_label.get("C1-F1"), by_label.get("C2-F1")
    if c1 is not None and c2 is not None:
        check("the grid is on the outer face of the column flanges",
              abs(c1.Shape.BoundBox.XMin) < 1e-3
              and abs(c2.Shape.BoundBox.XMax - g.width) < 1e-3,
              f"C1 outer face at {c1.Shape.BoundBox.XMin:.1f}, C2 at "
              f"{c2.Shape.BoundBox.XMax:.1f} (span {g.width:.0f})")
    r1 = by_label.get("R1-F1")
    if r1 is not None:
        check("the rafter starts clear of the column",
              r1.Shape.BoundBox.XMin > col.taper.end.d,
              f"R1 starts at x = {r1.Shape.BoundBox.XMin:.1f}, column inner "
              f"face at {col.taper.end.d:.1f}")
    purlins = [o for o in rep.objects if str(o.MemberType) == "purlin"]
    rafters = [o for o in rep.objects if str(o.MemberType) == "rafter"]
    if purlins and rafters:
        v = purlins[0].Shape.common(rafters[0].Shape).Volume
        check("a purlin stands on the rafter, not through it", v < 50.0,
              f"{v:.0f} mm3 shared")

    import time as _time

    from bimsteelauto_fc.clash import hard_clashes
    t_clash = _time.time()
    clashes = hard_clashes(rep.objects)
    check("the built model has no hard clashes (> 50 mm3)", not clashes,
          f"{len(clashes)}: " + "; ".join(str(c) for c in clashes[:4]))
    print(f"  ....  clash check {len(rep.objects)} solids in "
          f"{_time.time() - t_clash:.0f} s", flush=True)

    # ---- the schedule ------------------------------------------------
    print("schedule")
    rows = member_schedule(rep)
    totals = schedule_totals(rows)
    check("the schedule covers every built object",
          int(totals["pieces"]) == rep.n_built,
          f"{int(totals['pieces'])} vs {rep.n_built}")
    check("main and secondary steel both appear",
          totals["main_kg"] > 0 and totals["secondary_kg"] > 0)
    check("the schedule total matches the model",
          close(totals["total_kg"], rep.steel_kg, 1e-6))

    plan = g.width * g.length * 1e-6
    kg_m2 = totals["total_kg"] / plan
    check("steel per plan area is credible for a PEB (15-60 kg/m2)",
          15.0 <= kg_m2 <= 60.0, f"{kg_m2:.1f} kg/m2")

    frame_rows = [r for r in rows if r.member_type == "column"]
    if frame_rows:
        r = frame_rows[0]
        design = next(m for m in result.members
                      if m.member_type is MemberType.COLUMN)
        check("a column's scheduled mass matches the design",
              close(r.mass_each, design.weight, 0.01),
              f"{r.mass_each:.1f} kg vs design {design.weight:.1f} kg")

    # ---- shop detailing ----------------------------------------------
    #
    # COUNT IS NOT CORRECTNESS. Every check here is against a number the
    # ENGINE already knows - the layout's own mass, the hole count, the
    # flange the plate is supposed to sit on - because a plate built at the
    # wrong size, in the wrong place, or with no holes in it, still counts
    # as one plate.
    print("detailing")
    from bimsteelauto_fc.detailing import geometry_of, mass_kg
    from bimsteelauto.spec import ConnectionType

    details = [o for o in rep.objects
               if str(o.MemberType) in ("end_plate", "base_plate", "anchor",
                                        "purlin_cleat", "bracing_gusset")]
    conns = list(result.connections)
    with_geom = [c for c in conns if geometry_of(c) is not None]
    check("the result carries shop geometry",
          bool(with_geom),
          f"{len(with_geom)} of {len(conns)} connections, schema "
          f"{result.schema_version}")
    check("connection plates were built", bool(details),
          f"{len(details)} plates and anchor sets")

    # THE check: a built plate's own volume against the mass the engine's
    # layout computed from the same outline, net of the same holes. Two
    # numbers from two different sides of the JSON agreeing is the only
    # thing that catches a plate at the wrong size.
    worst = ("", 0.0, 0.0)
    for c in with_geom:
        geom = geometry_of(c)
        if geom["kind"] == "base_plate":
            built = [o for o in details
                     if str(o.MemberType) == "base_plate"]
        else:
            built = [o for o in details
                     if str(o.MemberType) == geom["kind"]
                     and str(o.Mark).startswith(c.mark)]
        if not built:
            continue
        got = mass_kg(built[0].Shape)
        want = float(geom["mass_kg"])
        if abs(got - want) > abs(worst[1] - worst[2]):
            worst = (c.mark, got, want)
    check("a built plate weighs what its layout says it weighs",
          worst[0] == "" or close(worst[1], worst[2], 0.01),
          f"worst {worst[0]}: solid {worst[1]:.3f} kg vs layout "
          f"{worst[2]:.3f} kg")

    # A plate with no holes in it is a plate nobody can bolt. The volume
    # removed must be the holes the layout asked for - not merely nonzero.
    ep_conn = next((c for c in with_geom
                    if geometry_of(c)["kind"] == "end_plate"), None)
    if ep_conn is not None:
        geom = geometry_of(ep_conn)
        built = next(o for o in details
                     if str(o.Mark).startswith(ep_conn.mark))
        gross = (float(geom["width_mm"]) * float(geom["height_mm"])
                 * float(geom["thickness_mm"]))
        holes = (geom["n_holes"] * 3.14159265358979
                 * (float(geom["hole_diameter_mm"]) / 2.0) ** 2
                 * float(geom["thickness_mm"]))
        check("the bolt holes are actually cut",
              close(built.Shape.Volume, gross - holes, 0.01),
              f"{built.Shape.Volume:.0f} mm3 vs gross {gross:.0f} less "
              f"{geom['n_holes']} holes {holes:.0f}")

        # POSITION, not just size. The plate's top edge must land where the
        # layout said - ext_top above the rafter's top flange outer face.
        # That face is on the analysis line through the eave node, and the
        # plate is on the column's inner flange, so the face is measured at
        # the plate's far side: the grid (the outer column face) plus the
        # column's top depth plus the plate. Stated here from the geometry,
        # not read back from the builder. A plate half a section out looks
        # fine in a viewport and is unbuildable.
        node = layout.nodes[layout.eave_nodes[0]]
        dv = float(geom["depth_on_cut_mm"])
        t_ep = float(geom["thickness_mm"])
        x_face = node[0] + col.taper.end.d
        z_face = node[1] + g.roof_slope * (col.taper.end.d + t_ep)
        z_top = z_face + float(geom["ext_top_mm"])
        z_bot = z_face - dv - float(geom["ext_bot_mm"])
        bbz = built.Shape.BoundBox
        check("the end plate sits on the flange it was laid out from",
              close(bbz.ZMax, z_top, 1e-4) and close(bbz.ZMin, z_bot, 1e-4),
              f"plate z {bbz.ZMin:.1f}..{bbz.ZMax:.1f} vs expected "
              f"{z_bot:.1f}..{z_top:.1f}")
        check("the end plate is on the column's inner flange",
              abs(bbz.XMin - x_face) < 1e-3
              and abs(bbz.XMax - (x_face + t_ep)) < 1e-3,
              f"plate x {bbz.XMin:.1f}..{bbz.XMax:.1f} vs column face "
              f"{x_face:.1f} + {t_ep:.0f}")

        # The plate is cut VERTICALLY, so its face normal is across the
        # span and it has no thickness in Y beyond its width.
        check("the end plate is a vertical plate across the span",
              close(bbz.XLength, float(geom["thickness_mm"]), 1e-3)
              and close(bbz.YLength, float(geom["width_mm"]), 1e-3),
              f"x {bbz.XLength:.1f} (t {geom['thickness_mm']:.1f}), "
              f"y {bbz.YLength:.1f} (w {geom['width_mm']:.1f})")

    # A base plate must sit UNDER the column, not through it.
    bp = next((o for o in details if str(o.MemberType) == "base_plate"), None)
    if bp is not None:
        base_z = layout.nodes[layout.base_nodes[0]][1]
        check("the base plate hangs below the column base",
              bp.Shape.BoundBox.ZMax <= base_z + 1e-6,
              f"plate top at {bp.Shape.BoundBox.ZMax:.2f}, base at {base_z:.2f}")

    anchors = [o for o in details if str(o.MemberType) == "anchor"]
    if anchors:
        check("the anchors are a separate object from the plate",
              all(o.Shape.Volume > 0 for o in anchors),
              f"{len(anchors)} anchor sets")

    check("every detail plate is a real solid",
          all(o.Shape.Solids for o in details),
          f"{sum(1 for o in details if not o.Shape.Solids)} with no solid")

    # A threaded rod has no gusset. The engine's GP1 is a 500 x 600 bolted
    # gusset; drawn at every rod end it clashed with everything near it and
    # put ~28 kg of plate per rod end into the take-off. Each rod end gets a
    # plate sized to its own rod instead.
    from bimsteelauto_fc.fabrication import rod_plate_geometry
    rods = [m for m in result.members
            if m.member_type in (MemberType.ROOF_BRACING,
                                 MemberType.WALL_BRACING)
            and "rod" in m.section_id.lower()]
    for md in rods:
        dia = float(md.section_id.lower().split("m")[1].split()[0])
        want = rod_plate_geometry(dia)
        plates = [o for o in details if str(o.Mark).startswith(md.mark + "P")]
        n_rods = len([o for o in rep.objects
                      if str(o.Mark).startswith(md.mark + "-")])
        check(f"every {md.mark} rod end has a plate sized to the M{dia:g} rod",
              len(plates) == 2 * n_rods and all(
                  close(mass_kg(o.Shape), want["mass_kg"], 0.01)
                  for o in plates),
              f"{len(plates)} plates for {n_rods} rods, want "
              f"PL {want['thickness_mm']:.0f} x {want['width_mm']:.0f}")
    if rods:
        check("no 500 x 600 gusset is drawn for a rod brace",
              not [o for o in details if str(o.Mark).startswith("GP1")],
              "GP1 plates found")

    # ---- drawings ----------------------------------------------------
    print("drawings")
    flat = _orient([[(0.0, 100.0), (0.0, 0.0)]])
    check("the sheet rotation turns the projection plane upright",
          flat[0][0] == (100.0, 0.0), f"{flat[0]}")

    ga = general_arrangement(doc, out_dir, result=result)
    check("three general arrangement sheets", len(ga.sheets) == 3,
          f"{len(ga.sheets)}: {[s.name for s in ga.sheets]}")
    check("no drawing warnings", not ga.warnings, "; ".join(ga.warnings))

    import re
    for name, expect_w, expect_h in (
        ("GA_frame_elevation.svg", g.width, g.ridge_height),
        ("GA_side_elevation.svg", g.length, g.ridge_height),
    ):
        path = os.path.join(out_dir, name)
        txt = open(path, encoding="utf-8").read()
        nums = [float(n) for p in re.findall(r'<path d="M ([^"]+)"', txt)
                for n in re.findall(r"-?\d+\.?\d*", p)]
        xs, ys = nums[0::2], nums[1::2]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        check(f"{name} is the right way up",
              w > h and close(h, expect_h, 0.15),
              f"drawn {w:.0f} wide x {h:.0f} high, building is "
              f"{expect_w:.0f} x {expect_h:.0f}")
        # The stamp must MATCH the design, both ways round. Until the demo
        # building passed, only the "design fails, stamp present" half had
        # ever run - so the name says what is being asserted rather than
        # asserting one half and being named after the other.
        stamped = "NOT FOR ISSUE" in txt
        ready = result.ready_for_issue()
        check(f"{name} stamp matches the design "
              f"({'ready, unstamped' if ready else 'failing, stamped'})",
              stamped == (not ready),
              f"stamped={stamped}, ready_for_issue={ready}")

    parts = single_part_drawings(doc, out_dir, result=result)
    check("a single-part sheet per distinct mark", len(parts.sheets) >= 5,
          f"{len(parts.sheets)}")

    # ---- dimensions ---------------------------------------------------
    #
    # A dimension is a NUMBER on a drawing that somebody will cut steel to,
    # so every check here is that the number agrees with something the
    # design already knows - never that a dimension is merely present.
    print("dimensions")
    from bimsteelauto_fc.drawings import (
        DIM_TEXT, VIEWS, VIEW_TO_SHEET, projection_map,
    )

    # The three hard-coded view maps were found by projecting a box and
    # reading the corners back. projection_map derives the same thing at run
    # time from three one-line projections. Two independent routes to the
    # same matrix - if they disagree, one of them is wrong, and every
    # dimension placed with it is in the wrong place while still reading a
    # plausible number.
    probe = [(1000.0, 2000.0, 3000.0), (-500.0, 700.0, 250.0), (0.0, 0.0, 0.0)]
    worst_map = ("", 0.0)
    for label, direction in VIEWS.items():
        m_run = projection_map(direction)
        m_fix = VIEW_TO_SHEET[label]
        for pt in probe:
            p_run, p_fix = m_run(*pt), m_fix(*pt)
            d = max(abs(p_run[0] - p_fix[0]), abs(p_run[1] - p_fix[1]))
            if d > worst_map[1]:
                worst_map = (label, d)
    check("the measured projection basis matches the derived one",
          worst_map[1] < 1e-6,
          f"worst {worst_map[0]} differs by {worst_map[1]:.6f} mm")

    _DIM_RE = r'font-size="' + str(DIM_TEXT) + r'"[^>]*>([-\d.]+)<'

    def sheet_dims(path):
        txt = open(path, encoding="utf-8").read()
        return txt, [float(v) for v in re.findall(_DIM_RE, txt)]

    txt, vals = sheet_dims(os.path.join(out_dir, "GA_frame_elevation.svg"))
    check("the frame elevation is dimensioned", len(vals) >= 3,
          f"{len(vals)} dimensions")
    check("every dimension has two arrowheads",
          txt.count("<polygon") == 2 * len(vals),
          f"{txt.count('<polygon')} arrowheads for {len(vals)} dimensions")
    for want, what in ((g.width, "span"), (g.eave_height, "eave height"),
                       (g.ridge_height, "ridge height")):
        check(f"the frame elevation dimensions the {what}",
              any(abs(v - want) <= 1.0 for v in vals),
              f"{want:.0f} not among {vals}")

    txt, vals = sheet_dims(os.path.join(out_dir, "GA_side_elevation.svg"))
    bays = list(g.bay_spacings)
    check("the side elevation dimensions every bay",
          all(any(abs(v - b) <= 1.0 for v in vals) for b in bays),
          f"bays {bays} vs {vals}")
    check("the bay chain adds up to the overall length on the sheet",
          any(abs(v - g.length) <= 1.0 for v in vals),
          f"{g.length:.0f} not among {vals}")

    # Nothing may run off the sheet or into the title block. This is the
    # check that failed first: the sheet reserved height for the title block
    # but measured the drawing's origin from the sheet edge anyway, so the
    # steel sat 28 mm inside the block - invisible while nothing was drawn
    # below the steel, and immediately visible once dimensions were.
    tb_x = 420.0 - 6.0 - 190.0
    tb_y = 297.0 - 6.0 - 34.0
    line_re = (r'<line x1="([-\d.]+)" y1="([-\d.]+)" '
               r'x2="([-\d.]+)" y2="([-\d.]+)"')
    off, clash = [], []
    for sh in list(ga.sheets) + list(parts.sheets):
        txt = open(sh.path, encoding="utf-8").read()
        for m in re.finditer(line_re, txt):
            x1, y1, x2, y2 = (float(v) for v in m.groups())
            if min(x1, x2) < 6.0 or max(y1, y2) > 291.0:
                off.append(os.path.basename(sh.path))
            if max(x1, x2) > tb_x and max(y1, y2) > tb_y:
                clash.append(os.path.basename(sh.path))
    check("no dimension runs off the sheet", not off,
          ", ".join(sorted(set(off))[:3]))
    check("no dimension runs into the title block", not clash,
          ", ".join(sorted(set(clash))[:3]))

    # A plate's part sheet must be TRUE SHAPE and must carry its bolt
    # set-out. A gusset lies in the plane of the bracing, and drawn along a
    # cardinal axis it came out foreshortened - a 500 x 600 plate
    # dimensioned 778 x 778, which is the size of nothing.
    for kind in ("end_plate", "bracing_gusset"):
        conn = next((c for c in with_geom
                     if geometry_of(c)["kind"] == kind), None)
        if conn is None:
            continue
        geom = geometry_of(conn)
        path = os.path.join(out_dir, f"PART_{conn.mark}.svg")
        # Only a sheet THIS run drew. The out dir is reused between runs,
        # and a sheet left there by an older build passes for a current one.
        if os.path.normcase(path) not in {os.path.normcase(s.path)
                                          for s in parts.sheets}:
            continue
        _t, vals = sheet_dims(path)
        check(f"the {kind.replace('_', ' ')} sheet is drawn true shape",
              any(abs(v - geom["width_mm"]) <= 1.0 for v in vals)
              and any(abs(v - geom["height_mm"]) <= 1.0 for v in vals),
              f"{geom['width_mm']:.0f} x {geom['height_mm']:.0f} not among "
              f"{vals}")
        us = [u for u, _ in geom["holes_uv_mm"]]
        span = max(us) - min(us)
        if span > 1.0:
            check(f"the {kind.replace('_', ' ')} sheet dimensions its bolts",
                  any(abs(v - span) <= 1.0 for v in vals)
                  or all(any(abs(v - p) <= 1.0 for v in vals)
                         for p in _pitches(us)),
                  f"bolt span {span:.0f} not among {vals}")

    # ---- result contours ----------------------------------------------
    #
    # The only automated coverage the contour DRAWING gets: pytest cannot
    # run it, because it needs Part and a document.
    print("contours")
    c3d = os.path.join(os.path.dirname(json_path), "out", "contours3d.json")
    if not os.path.exists(c3d):
        print("  ....  no out/contours3d.json - run run_space_example.py to "
              "make one; the 3D contour checks did not run", flush=True)
    else:
        from bimsteelauto_fc.contours import draw_contours
        from bimsteelauto.analysis.results import Quantity, load_contours

        cs3 = load_contours(c3d)
        check("the 3D contour file is a whole-building set",
              cs3.is_3d and len(cs3.members) > 20,
              f"is_3d={cs3.is_3d}, {len(cs3.members)} members")

        # A 3D set must occupy the building, not one plane. The failure this
        # catches is an empty Z read as zero, which piles every frame line
        # onto grid 1 - and the result still looks like a frame.
        zs = {round(z, 1) for m in cs3.members for z in m.Z_at(cs3.frame_y)}
        check("the 3D contour spans the building's frame lines",
              len(zs) > 2, f"{len(zs)} distinct Z values")

        wanted_q = (Quantity.DISPLACEMENT, Quantity.AXIAL, Quantity.MOMENT,
                    Quantity.TORSION, Quantity.MOMENT_Y,
                    Quantity.UTILISATION)
        drawn = {}
        for q in wanted_q:
            try:
                drawn[q] = draw_contours(
                    cs3, quantity=q, legend=False, show_undeformed=True
                )
            except Exception as exc:                    # noqa: BLE001
                check(f"the {q} contour draws", False, str(exc))
        check("every quantity draws", len(drawn) == len(wanted_q),
              f"{len(drawn)} of {len(wanted_q)}")

        # The utilisation is the one that turns My and T into a number, and
        # it is graded on all six components. A member with no grade must
        # be LEFT OUT and named - never drawn in the zero colour, which on
        # a utilisation contour is the calmest thing on the picture.
        u = drawn.get(Quantity.UTILISATION)
        if u is not None:
            check("the 3D utilisation contour has a real range",
                  0.0 < u.vmax <= 5.0, f"0 .. {u.vmax:.3f}")
            skipped = [n for n in u.notes if n.startswith("not drawn")]
            graded = sum(1 for m in cs3.members if m.ratio)
            check("the primary members are graded and the rest are named",
                  graded > 0 and len(skipped) == len(cs3.members) - graded,
                  f"{graded} graded, {len(skipped)} named as not drawn, "
                  f"{len(cs3.members)} members")
            check("the utilisation was graded on all six components",
                  any("ALL SIX" in n for n in cs3.check_notes),
                  "; ".join(cs3.check_notes[-1:]))

        if Quantity.MOMENT in drawn:
            d = drawn[Quantity.MOMENT]
            check("the moment contour found a real range",
                  d.vmax > d.vmin and abs(d.vmax) > 1.0,
                  f"{d.vmin:.1f} .. {d.vmax:.1f} {d.unit}")

        # The drawn compound must occupy the BUILDING, not a plane. This is
        # the count-is-not-correctness check for the contour: a set of edges
        # in one plane is the right number of edges and the wrong picture.
        obj = doc.getObject("BSA_ContourShape")
        if obj is not None:
            bb = obj.Shape.BoundBox
            check("the drawn contour occupies all three dimensions",
                  bb.XLength > 1000.0 and bb.YLength > 1000.0
                  and bb.ZLength > 1000.0,
                  f"bbox {bb.XLength:.0f} x {bb.YLength:.0f} x "
                  f"{bb.ZLength:.0f} mm")
            check("the contour is drawn the length of the building",
                  bb.YLength > 0.8 * g.length,
                  f"{bb.YLength:.0f} mm of a {g.length:.0f} mm building")

    # ---- IFC ---------------------------------------------------------
    print("ifc")
    ifc_path = os.path.join(out_dir, "selftest.ifc")
    ex = export_ifc(doc, ifc_path, project_name="selftest")
    check("the IFC file was written and is not empty",
          os.path.exists(ifc_path) and os.path.getsize(ifc_path) > 1000,
          f"{os.path.getsize(ifc_path) if os.path.exists(ifc_path) else 0} bytes")
    counts = summarise_ifc(ifc_path)
    if counts:
        n_elem = sum(counts.get(k, 0)
                     for k in ("IfcColumn", "IfcBeam", "IfcMember", "IfcPlate",
                               "IfcMechanicalFastener"))
        check("every built member reached the IFC", n_elem == rep.n_built,
              f"{n_elem} IFC elements vs {rep.n_built} built")
        check("the IFC has a spatial tree",
              counts.get("IfcSite", 0) >= 1
              and counts.get("IfcBuilding", 0) >= 1
              and counts.get("IfcBuildingStorey", 0) >= 1,
              str({k: v for k, v in counts.items() if "Ifc" in k
                   and "Site" in k or "Building" in k}))
        check("columns are classified as columns",
              counts.get("IfcColumn", 0) == n_frames * 2,
              f"{counts.get('IfcColumn', 0)} vs {n_frames * 2}")

    print("-" * 60, flush=True)
    if _FAILURES:
        print(f"{_PASSED} passed, {len(_FAILURES)} FAILED", flush=True)
        for f in _FAILURES:
            print(f"  FAILED {f}", flush=True)
        sys.stdout.flush()
        return 1
    print(f"ALL PASSED ({_PASSED} checks)", flush=True)
    sys.stdout.flush()
    return 0


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if here not in sys.path:
        sys.path.insert(0, here)
    json_path = os.environ.get(
        "BIMSTEELAUTO_JSON", os.path.join(here, "design_result.json")
    )
    if not os.path.exists(json_path):
        print(f"no design_result.json at {json_path} - run "
              "run_frame_example.py first")
        return 2
    out_dir = os.environ.get(
        "BIMSTEELAUTO_OUT", os.path.join(tempfile.gettempdir(), "bsa_selftest")
    )
    os.makedirs(out_dir, exist_ok=True)
    try:
        return run(json_path, out_dir)
    except Exception:                                    # noqa: BLE001
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
