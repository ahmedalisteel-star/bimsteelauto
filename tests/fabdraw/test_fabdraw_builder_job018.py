"""The steel-project skill's build_model_peb.py, fixed 2026-10-26, on JOB-018's real input.

Regressions for what the fabdraw gates found in the skill's model:

* 70 purlin cleats were built twice, coincident (each rafter took both slopes' cleats and was then
  mirrored) - and G1 cannot see an exact duplicate, so this is checked on the model itself;
* the purlin ends were never drilled at the cleats, so no CL1 bolt could be placed;
* the cleat carried 1 bolt per purlin end where the CL1 design says 2 x M12 at 60 mm pitch.

Skipped where the skill or the JOB-018 folder is not on this machine.
"""

from __future__ import annotations

import importlib.util
import os
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

SKILL = Path.home() / ".claude" / "skills" / "steel-project" / "scripts"
JOB = Path(os.environ.get("BSA_JOB018_DIR", "JOB-018-v2-ModelChain"))
pytestmark = pytest.mark.skipif(not (SKILL / "build_model_peb.py").is_file() or not (JOB / "04_design").is_dir(),
                                reason="steel-project skill or JOB-018 not on this machine")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SKILL))
    spec.loader.exec_module(mod)
    return mod


def _model_input():
    """JOB-018's own model_input.json plus the CL1 bolt layout make_model_input.py now carries."""
    mmi = _load("make_model_input")
    I = json.loads((JOB / "05_model" / "model_input.json").read_text(encoding="utf-8"))
    con = json.loads((JOB / "04_design" / "connection_result.json").read_text(encoding="utf-8"))["connections"]
    I["purlins"]["cleat"].update(mmi.cleat_bolts(con["CL1"]))
    return I


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("job018")
    mi = d / "model_input.json"
    mi.write_text(json.dumps(_model_input()), encoding="utf-8")
    run = subprocess.run([sys.executable, str(SKILL / "build_model_peb.py"), str(mi), str(d / "model.json")],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    run = subprocess.run([sys.executable, str(SKILL / "model_to_ifc.py"), str(d / "model.json"), str(d / "JOB-018.ifc")],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return d, json.loads((d / "model.json").read_text(encoding="utf-8")), run.stdout


def test_cleat_layout_comes_from_the_design():
    c = _model_input()["purlins"]["cleat"]
    assert (c["n_bolts"], c["pitch"], c["hole_d"], c["bolt_d"]) == (2, 60.0, 14.0, 12.0)


def test_no_part_is_built_twice(built):
    _, M, _ = built
    n = Counter(json.dumps(p["solid"], sort_keys=True) for a in M["assemblies"] for p in a["parts"])
    dup = {k: v for k, v in n.items() if v > 1}
    assert not dup, f"{len(dup)} solids appear more than once"


def test_every_cl1_bolt_is_placed(built):
    d, _, out = built
    unplaced = json.loads((d / "JOB-018.unplaced_bolts.json").read_text(encoding="utf-8"))
    assert "CL1" not in {r["group"] for r in unplaced}
    # GC1 and FB1 have no connection design yet - they must still be listed, not guessed
    assert {r["group"] for r in unplaced} == {"GC1", "FB1"}


def test_shared_cleat_has_two_bolts_per_purlin_end(built):
    _, M, _ = built
    rafter_cleats = [p for a in M["assemblies"] if a["mark"] in ("R1", "R2") for p in a["parts"] if p.get("mark") == "CL1"]
    end_cleats = [p for a in M["assemblies"] if a["mark"] == "ER1" for p in a["parts"] if p.get("mark") == "CL1"]
    assert rafter_cleats and end_cleats
    assert {len(p["holes"]) for p in rafter_cleats} == {4}      # two purlin ends x 2 bolts
    assert {len(p["holes"]) for p in end_cleats} == {2}         # one purlin end x 2 bolts


def test_builder_refuses_a_cleat_with_no_designed_bolt_layout(tmp_path):
    I = _model_input()
    for k in ("n_bolts", "pitch", "hole_d", "bolt_d"):                                    # strip the layout
        I["purlins"]["cleat"].pop(k, None)
    mi = tmp_path / "mi.json"
    mi.write_text(json.dumps(I), encoding="utf-8")
    run = subprocess.run([sys.executable, str(SKILL / "build_model_peb.py"), str(mi), str(tmp_path / "m.json")],
                         capture_output=True, text=True)
    assert run.returncode != 0
    assert "cleat bolt layout comes from the CL1 design" in run.stderr
