"""Holds spec.py and bimsteelauto_connections in step.

spec.py deliberately imports nothing, so a few values are duplicated from the
connection package.  Duplication is only safe if drift is caught, which is
what this file is for.  It skips cleanly in an environment where the
connection package is not installed (e.g. a bare CI runner), and runs for
real inside FreeCAD and on the workstation.
"""

import pytest

from bimsteelauto.spec import (
    CAPACITY_FRACTION,
    ConnectionDesign,
    ConnectionType,
)

core = pytest.importorskip(
    "bimsteelauto_connections.core",
    reason="bimsteelauto_connections not on sys.path in this environment",
)


def test_capacity_fraction_matches_connection_package():
    """0.50 is stated in two places; they must never disagree."""
    assert CAPACITY_FRACTION == core.CAPACITY_FRACTION


def test_real_connection_result_is_accepted_by_from_result():
    """The duck-typed adapter must fit the real dataclass, not just the fake."""
    result = core.ConnectionResult(
        mark="BP1",
        kind="base plate",
        limit_states=(
            core.LimitState(
                name="Concrete bearing",
                capacity=850.0,
                demand=612.0,
                clause="AISC DG 1 Eq. 3.1.1",
                unit="kN",
            ),
            core.LimitState(
                name="Plate bending",
                capacity=120.0,
                demand=44.0,
                clause="AISC DG 1 Eq. 3.3.15",
                unit="kNm",
            ),
        ),
        forces=(
            core.design_force(
                analysis=612.0,
                member_capacity=1400.0,
                fraction=core.CAPACITY_FRACTION,
                label="Pu",
            ),
        ),
        selections={"plate": "PL30x450x450"},
        verified=True,
        citation="AISC Design Guide 1, 2nd ed.",
    )

    cd = ConnectionDesign.from_result(result, ConnectionType.BASE_PLATE, ["C1"])

    assert cd.mark == "BP1"
    assert cd.connection_type is ConnectionType.BASE_PLATE
    assert cd.utilisation == pytest.approx(result.utilization)
    assert cd.verified is True
    assert cd.passed
    assert len(cd.detail["limit_states"]) == 2
    assert cd.detail["citation"] == "AISC Design Guide 1, 2nd ed."
    assert cd.detail["selections"]["plate"] == "PL30x450x450"


def test_capacity_basis_is_read_from_the_real_design_force():
    """612 kN analysis vs 0.50 x 1400 = 700 kN capacity -> capacity governs."""
    result = core.ConnectionResult(
        mark="BP2",
        kind="base plate",
        limit_states=(core.LimitState("Concrete bearing", 850.0, 700.0, unit="kN"),),
        forces=(
            core.design_force(
                analysis=612.0,
                member_capacity=1400.0,
                fraction=core.CAPACITY_FRACTION,
                label="Pu",
            ),
        ),
        verified=True,
    )
    cd = ConnectionDesign.from_result(result, ConnectionType.BASE_PLATE, ["C1"])
    assert cd.design_force_basis == "capacity_fraction"
    assert cd.detail["forces"][0]["value"] == pytest.approx(700.0)


def test_analysis_basis_when_analysis_governs():
    result = core.ConnectionResult(
        mark="BP3",
        kind="base plate",
        limit_states=(core.LimitState("Concrete bearing", 1200.0, 900.0, unit="kN"),),
        forces=(
            core.design_force(
                analysis=900.0,
                member_capacity=1400.0,
                fraction=core.CAPACITY_FRACTION,
                label="Pu",
            ),
        ),
        verified=True,
    )
    cd = ConnectionDesign.from_result(result, ConnectionType.BASE_PLATE, ["C1"])
    assert cd.design_force_basis == "analysis"
