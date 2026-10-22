"""Shared fixtures and path bootstrap for the connection-layer suite.

The package needs ``pyaisc360`` importable.  Rather than require the caller
to have set PYTHONPATH, this looks for it in the usual places and fails with
a message that says what to do, because "ModuleNotFoundError: pyaisc360"
three frames deep is a bad first impression of a test suite.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _ensure_importable() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        import pyaisc360  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    candidates: list[Path] = []
    env = os.environ.get("PYAISC360_PATH")
    if env:
        candidates.append(Path(env))
    home = Path.home() / ".claude" / "skills"
    if home.is_dir():
        candidates += [p.parent for p in home.rglob("steel-design-toolkit/libs/pyaisc360/__init__.py")]

    for c in candidates:
        if (c / "pyaisc360" / "__init__.py").is_file():
            sys.path.insert(0, str(c))
            return

    raise pytest.UsageError(
        "pyaisc360 is not importable. Either install it, or set "
        "PYAISC360_PATH to the directory that CONTAINS the pyaisc360 "
        "package (not pyaisc360 itself)."
    )


_ensure_importable()


# ----------------------------------------------------------------------
# Fixture loading
# ----------------------------------------------------------------------

def load_fixture(name: str) -> dict:
    """Read a verification report fixture by file stem."""
    path = FIXTURES / f"{name}.json"
    if not path.is_file():
        raise pytest.UsageError(f"missing verification fixture: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def profis_ca450() -> dict:
    return load_fixture("profis_anchors_ca450")


@pytest.fixture(scope="session")
def profis_ca150() -> dict:
    return load_fixture("profis_anchors_ca150")


@pytest.fixture(scope="session")
def cet_4E() -> dict:
    return load_fixture("cet_endplate_4E_B1C1")


@pytest.fixture(scope="session")
def profis_baseplate() -> dict:
    return load_fixture("profis_baseplate_cases")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def within_pct(mine: float, reference: float, tol_pct: float) -> tuple[bool, float]:
    """Relative agreement against an INDEPENDENT reference value.

    Returns ``(ok, delta_pct)``.  ``reference`` is the external tool's
    number; the deviation is expressed as a percentage of it, which is the
    convention the verification records in the modules use.
    """
    if reference == 0.0:
        return (abs(mine) <= 1e-9, 0.0 if mine == 0 else float("inf"))
    delta = abs(mine - reference) / abs(reference) * 100.0
    return (delta <= tol_pct, delta)


@pytest.fixture(scope="session")
def anchor_group_factory():
    """Build an AnchorGroup from a fixture's ``inputs`` block."""
    from bimsteelauto_connections.anchors import AnchorGroup

    FIELDS = ("d", "hef", "n_x", "n_y", "s_x", "s_y", "ca_x1", "ca_x2",
              "ca_y1", "ca_y2", "cracked", "supplementary_reinforcement",
              "edge_reinforcement", "stirrups_at_100mm", "grout_pad",
              "ha", "across_flats")

    def build(inputs: dict, **overrides):
        kw = {k: inputs[k] for k in FIELDS if k in inputs}
        kw.update(overrides)
        return AnchorGroup(**kw)

    return build


@pytest.fixture(scope="session")
def cet_4E_w24() -> dict:
    """The reproducible 4E record (2026-08-31), replacing B1_C1."""
    return load_fixture("cet_endplate_4E_W24x76")


@pytest.fixture(scope="session")
def cet_4ES_w30() -> dict:
    """Run 2 — the independent verification of the defect #10 fix."""
    return load_fixture("cet_endplate_4ES_W30x90")


@pytest.fixture(scope="session")
def cet_8ES_w40() -> dict:
    """8ES run 1 — supplied the defect #11 fix; cannot verify it."""
    return load_fixture("cet_endplate_8ES_W40x149")


@pytest.fixture(scope="session")
def cet_8ES_w36() -> dict:
    """8ES run 2 — confirms most of the expression, not the pb^2 term."""
    return load_fixture("cet_endplate_8ES_W36x150")


@pytest.fixture(scope="session")
def cet_8ES_pb5() -> dict:
    """8ES run 3 — the run that discriminated the pb^2 term."""
    return load_fixture("cet_endplate_8ES_W36x150_pb5")


@pytest.fixture(scope="session")
def aisc_IIA17B() -> dict:
    """AISC published numbers for II.A-17B — outputs only, no inputs."""
    return load_fixture("aisc_designexample_IIA17B_cleat")


@pytest.fixture(scope="session")
def cet_cleat() -> dict:
    """Single-plate shear connection — the cleat comparison."""
    return load_fixture("cet_cleat_singleplate_W18x50")


@pytest.fixture(scope="session")
def cet_cleat_b2b() -> dict:
    """Second cleat geometry — A36 plate, beam to beam web."""
    return load_fixture("cet_cleat_beam_to_beam_A36")


@pytest.fixture(scope="session")
def cet_gusset() -> dict:
    return load_fixture("cet_gusset_HSS5x5_brace")
