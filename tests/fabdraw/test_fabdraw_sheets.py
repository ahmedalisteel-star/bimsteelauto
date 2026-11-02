"""Stage 5 step 3 - extract, sheets and G3 on the fixture whose every dimension is known by construction.

The fixture (fabdraw_models.py): beam b1, welded I 300 x 150 x 7.1 x 10.7, L 4000 along +X, a cope
120 x 60 at the far end's top flange; plate p1 180 (Y) x 340 (Z) x 20 on each end face, 4 holes d 22
at Y +/-50, Z +/-115. Nothing here is compared with another program's output.
"""

from __future__ import annotations

import math

import ezdxf
import numpy as np
import pytest

from . import fabdraw_models as fm

D = fm.DIMS


@pytest.fixture(scope="module")
def model(good_ifc):
    from bimsteelauto.fabdraw import extract
    return extract.read_model(good_ifc, policy="strict")


@pytest.fixture(scope="module")
def drawn(good_ifc, fabdir):
    from bimsteelauto.fabdraw import run
    out = fabdir / "sheets"
    rep = run.run(good_ifc, out, tb={"project": "fixture", "project_no": "T-1"}, policy="strict", gates=False,
                  log=lambda *_: None)
    return out, rep


# ------------------------------------------------------------------ extraction
def test_assembly_and_parts(model):
    assert [a.mark for a in model.assemblies] == ["B1"]
    a = model.assemblies[0]
    assert a.qty == 1
    assert sorted(p.mark for p in a.parts) == ["b1", "p1", "p1"]
    assert a.main.mark == "b1"


def test_beam_frame_and_length(model):
    b = model.assemblies[0].main
    o, e1, e2, n = b.frame
    assert np.allclose(np.abs(e1), [1, 0, 0], atol=1e-6)          # along the beam
    assert np.allclose(np.abs(e2), [0, 0, 1], atol=1e-6)          # depth is Z: the web is seen face-on
    assert b.ext[0] == pytest.approx(D.length, abs=0.01)
    assert b.ext[1] == pytest.approx(D.depth, abs=0.01)
    assert b.ext[2] == pytest.approx(D.width, abs=0.01)


def test_weights_from_geometry(model):
    a = model.assemblies[0]
    area = 2 * D.width * D.tf + (D.depth - 2 * D.tf) * D.tw
    cope = D.cope_len * (D.width * D.tf + D.tw * (D.cope_depth - D.tf))
    beam_kg = (area * D.length - cope) * 7850e-9
    plate_kg = (D.plate_w * D.plate_h - 4 * math.pi * (D.hole_d / 2) ** 2) * D.plate_t * 7850e-9
    assert a.main.weight == pytest.approx(beam_kg, rel=2e-3)
    for p in a.parts:
        if p.mark == "p1":
            assert p.weight == pytest.approx(plate_kg, rel=5e-3)     # 24-gon holes: the facets cost ~0.3 %


def test_plate_size_and_holes(model):
    plates = [p for p in model.assemblies[0].parts if p.mark == "p1"]
    for p in plates:
        assert p.is_plate
        assert p.description() == f"PL{int(D.plate_t)}*{int(D.plate_w)}"
        assert p.ext[0] == pytest.approx(D.plate_h, abs=0.01)
        assert len(p.holes) == 4
        for h in p.holes:
            assert h.d == pytest.approx(D.hole_d)
            assert abs(h.axis[0]) == pytest.approx(1.0)
            assert h.depth == pytest.approx(D.plate_t, abs=0.01)      # clipped to the plate, not the 22 mm opening
    starts = sorted((round(h.center[1], 3), round(h.center[2], 3)) for h in plates[0].holes)
    assert starts == sorted((y, z) for y, z in D.holes)


def test_allocation(model):
    assert dict(model.allocation["p1"]) == {"B1": 2}
    assert dict(model.allocation["b1"]) == {"B1": 1}


# ------------------------------------------------------------------ sheets
def _dim_texts(path, view_prefix):
    from bimsteelauto.fabdraw.gate_roundtrip import _xd
    msp = ezdxf.readfile(path).modelspace()
    out = {"x": set(), "y": set()}
    for t in msp.query('TEXT[layer=="DIM"]'):
        d = _xd(t)
        if d.get("view", "").startswith(view_prefix) and "axis" in d:
            out[d["axis"]].add(t.dxf.text)
    return out


def test_sheets_written(drawn):
    out, rep = drawn
    assert (out / "ASSEM" / "B1.dxf").is_file()
    for mk in ("b1", "p1"):
        assert (out / "SINGLE" / f"{mk}.dxf").is_file()
    assert (out / "NC_DXF" / "p1.dxf").is_file() and (out / "DSTV" / "p1.nc1").is_file()
    assert (out / "register.csv").is_file()


def test_end_plate_detail_dimensions(drawn):
    """The end plate's holes are on the beam axis: a DETAIL shows them face-on, with the plate's own set-out."""
    out, _ = drawn
    t = _dim_texts(out / "ASSEM" / "B1.dxf", "DETAIL p1")
    gauge = {str(int(D.plate_w / 2 - D.hole_y)), str(int(D.plate_w / 2 + D.hole_y))}           # 40, 140
    pitch = {str(int(D.plate_h / 2 - D.hole_z)), str(int(D.plate_h / 2 + D.hole_z))}           # 55, 285
    both = t["x"] | t["y"]
    assert gauge <= both and pitch <= both


def test_overall_length_dimension(drawn):
    out, _ = drawn
    from bimsteelauto.fabdraw.gate_roundtrip import _xd
    msp = ezdxf.readfile(out / "ASSEM" / "B1.dxf").modelspace()
    lens = [d for d in msp.query("DIMENSION") if _xd(d).get("what") == "overall length"]
    assert lens
    d = lens[0]
    txt = (d.dxf.get("text", "") or "").strip()
    if txt not in ("", "<>"):                     # a shortened view writes the true length
        assert float(txt) == pytest.approx(D.length, abs=0.05)
    else:
        assert float(d.get_measurement()) * float(d.override().get("dimlfac")) == pytest.approx(D.length, abs=0.05)


def test_nc_plate(drawn):
    out, _ = drawn
    msp = ezdxf.readfile(out / "NC_DXF" / "p1.dxf").modelspace()
    circles = list(msp.query('CIRCLE[layer=="HOLE"]'))
    assert len(circles) == 4 and all(c.dxf.radius == pytest.approx(D.hole_d / 2) for c in circles)
    cut = list(msp.query('LWPOLYLINE[layer=="CUT"]'))
    xs = [p[0] for p in cut[0].get_points("xy")]
    ys = [p[1] for p in cut[0].get_points("xy")]
    assert max(xs) - min(xs) == pytest.approx(D.plate_h, abs=0.1)
    assert max(ys) - min(ys) == pytest.approx(D.plate_w, abs=0.1)
    nc1 = (out / "DSTV" / "p1.nc1").read_text()
    assert nc1.startswith("ST") and "\nAK\n" in nc1 and "\nBO\n" in nc1 and nc1.rstrip().endswith("EN")


def test_not_issued_without_gates(drawn):
    out, rep = drawn
    assert rep["issue"]["issued"] is False
    msp = ezdxf.readfile(out / "ASSEM" / "B1.dxf").modelspace()
    assert [t.dxf.text for t in msp.query('TEXT[layer=="STAMP"]')] == ["NOT FOR ISSUE"]


# ------------------------------------------------------------------ G3
def test_g3_passes_on_the_set(drawn):
    _, rep = drawn
    g3 = rep["gates"]["G3"]
    assert g3["passed"], g3
    assert g3["summary"]["holes checked"] >= 8


def _mutated(out, model, fn, tmp_path):
    from bimsteelauto.fabdraw import gate_roundtrip as G
    doc = ezdxf.readfile(out / "ASSEM" / "B1.dxf")
    fn(doc.modelspace(), G)
    p = tmp_path / "B1_mut.dxf"
    doc.saveas(p)
    F, _ = G.check_sheet(p, model.assemblies[0].parts, kind="assembly", qty_expect={})
    return {f.rule for f in F if f.severity == "hard"}


def test_g3_catches_a_changed_number(drawn, model, tmp_path):
    def fn(msp, G):
        t = next(t for t in msp.query('TEXT[layer=="DIM"]') if G._xd(t).get("tick") and t.dxf.text != "0")
        t.dxf.text = str(float(t.dxf.text) + 5)
    assert "R1" in _mutated(drawn[0], model, fn, tmp_path)


def test_g3_catches_a_missing_hole(drawn, model, tmp_path):
    def fn(msp, G):
        for c in list(msp.query('CIRCLE[layer=="HOLE"]')):
            msp.delete_entity(c)
    assert "R3" in _mutated(drawn[0], model, fn, tmp_path)


def test_g3_catches_a_wrong_quantity(drawn, model, tmp_path):
    def fn(msp, G):
        t = next(t for t in msp.query("TEXT") if G._xd(t).get("tbl") == "material" and G._xd(t).get("col") == "No."
                 and t.dxf.text == "2")
        t.dxf.text = "3"
    assert "R4" in _mutated(drawn[0], model, fn, tmp_path)


def test_g3_catches_drawing_off_the_sheet(drawn, model, tmp_path):
    def fn(msp, G):
        msp.add_line((10, 10), (900, 10), dxfattribs={"layer": "PART"})
    assert "R5" in _mutated(drawn[0], model, fn, tmp_path)


# ------------------------------------------------------------------ shortening
def test_breaks_keep_true_values(model):
    """A view shortened at known places: mx is continuous, clip drops the cut-out, G3's inverse undoes it."""
    from bimsteelauto.fabdraw import gate_roundtrip as G
    from bimsteelauto.fabdraw.views import assembly_views, set_breaks
    front = assembly_views(model.assemblies[0])[0].prepare()
    # features only at the ends: set_breaks would take out > BREAK_MAX_REMOVED of the length, so it refuses
    set_breaks([front], 10.0)
    assert front.breaks == []
    front.breaks, front.gap = [(500.0, 1500.0), (2000.0, 3500.0)], 60.0
    for a, b in front.breaks:
        assert front.mx(b) - front.mx(a) == pytest.approx(front.gap)
    assert front.mx(500.0) == pytest.approx(500.0)
    assert front.mx(2000.0) == pytest.approx(2000.0 - (1000.0 - 60.0))
    assert front.clip(((400.0, 0.0), (1600.0, 0.0))) == [((400.0, 0.0), (500.0, 0.0)), ((1500.0, 0.0), (1600.0, 0.0))]
    V = {"breaks": front.breaks, "gap": front.gap}
    for x in np.linspace(front.bx0, front.bx1, 41):
        assert G._unbreak(V, front.mx(x)) == pytest.approx(x, abs=1e-6)
        assert G._break(V, x) == pytest.approx(front.mx(x), abs=1e-9)
    assert front.wc < front.w


def test_g3_catches_a_wrong_overall_length_on_a_broken_view(drawn, model, tmp_path):
    def fn(msp, G):
        d = next(d for d in msp.query("DIMENSION") if G._xd(d).get("what") == "overall length")
        d.dxf.text = "3999"
    assert "R1" in _mutated(drawn[0], model, fn, tmp_path)
