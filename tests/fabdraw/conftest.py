"""Shared fabdraw fixtures: each model is built once per session and each
gate run once per model, because a clash run costs seconds."""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")
pytest.importorskip("ifcclash")
pytest.importorskip("ifctester")

from . import fabdraw_models as fm  # noqa: E402


@pytest.fixture(scope="session")
def fabdir(tmp_path_factory):
    return tmp_path_factory.mktemp("fabdraw")


@pytest.fixture(scope="session")
def good_ifc(fabdir):
    p = fabdir / "good.ifc"
    fm.beam_assembly_model(p)
    return p


@pytest.fixture(scope="session")
def shifted_ifc(fabdir):
    p = fabdir / "shifted.ifc"
    fm.beam_assembly_model(p, plate_shift_mm=fm.TEST2_SHIFT_MM)
    return p


@pytest.fixture(scope="session")
def nomark_ifc(fabdir):
    p = fabdir / "nomark.ifc"
    fm.beam_assembly_model(p, drop_part_mark=True)
    return p


@pytest.fixture(scope="session")
def g1_good(good_ifc, fabdir):
    from bimsteelauto.fabdraw import gate_clash
    return gate_clash.run_clash_gate(good_ifc, fabdir / "g1_good")


@pytest.fixture(scope="session")
def g1_shifted(shifted_ifc, fabdir):
    from bimsteelauto.fabdraw import gate_clash
    return gate_clash.run_clash_gate(shifted_ifc, fabdir / "g1_shifted")
