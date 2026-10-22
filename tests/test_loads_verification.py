"""Independent verification of bimsteelauto.loads.

Reference: the ``asce7-loads`` skill's ``combos.py`` and ``gravity.py`` - a
separately written implementation of the same chapters, by a different
author.  Plus longhand clause arithmetic for the values ASCE 7-16 states
outright, because two implementations can share a misreading and a printed
bound cannot.

Run 2026-09-01.
"""

from __future__ import annotations

import importlib.util
import math
import pathlib
import sys

import pytest

from bimsteelauto.loads import (
    AreaLoad,
    RoofCompanion,
    Surface,
    asd_combinations,
    lrfd_combinations,
    roof_live_load,
)

pytestmark = pytest.mark.verification


def _load(name: str):
    """Import one asce7-loads script, wherever the skill is installed."""
    # The VENDORED copy first: it is pinned beside the tests and cannot move
    # when a skill is re-synced. The skill folders are a fallback for a
    # machine that has not vendored it yet.
    here = pathlib.Path(__file__).resolve()
    roots = [
        here.parent.parent.parent / "vendor" / "reference",   # peb/vendor/reference
        pathlib.Path.cwd() / "vendor" / "reference",
        pathlib.Path.home() / ".claude" / "skills",
        pathlib.Path("/mnt/skills"),
        pathlib.Path.cwd(),
    ]
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob(f"asce7-loads/scripts/{name}.py"):
            pkg = p.parent
            if str(pkg) not in sys.path:
                sys.path.insert(0, str(pkg))
            spec = importlib.util.spec_from_file_location(f"ref_{name}", p)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[f"ref_{name}"] = mod
            spec.loader.exec_module(mod)
            return mod
    return None


REF_COMBOS = _load("combos")
REF_GRAVITY = _load("gravity")

needs_combos = pytest.mark.skipif(
    REF_COMBOS is None, reason="the asce7-loads reference is not present"
)
needs_gravity = pytest.mark.skipif(
    REF_GRAVITY is None, reason="the asce7-loads reference is not present"
)


def _evaluate(factors, loads):
    return sum(f * loads.get(k, 0.0) for k, f in factors.items())


def _envelope(combos, loads):
    vals = [_evaluate(c.factors, loads) for c in combos]
    return min(vals), max(vals)


#: A PEB roof under uplift: light dead load, real suction.  The case where
#: getting the combinations wrong actually loses a building.
UPLIFT_CASE = {"D": 0.30, "Lr": 0.58, "W": -1.20}
GRAVITY_CASE = {"D": 0.30, "Lr": 0.58, "W": 0.35}


# ---------------------------------------------------------------------------
# roof live load
# ---------------------------------------------------------------------------


@needs_gravity
@pytest.mark.parametrize("At", [5.0, 18.58, 25.0, 40.0, 55.74, 120.0, 500.0])
@pytest.mark.parametrize("slope", [0.0, 0.05, 0.10, 0.25, 0.50, 1.0])
def test_roof_live_matches_the_reference(At, slope):
    """Eq. 4.8-1 across the whole R1 and R2 range, two implementations.

    The reference takes F directly; this package takes the SLOPE and derives
    F = 12 x slope, so the conversion is exercised here as well.
    """
    mine = roof_live_load(At, slope)
    theirs = REF_GRAVITY.roof_live_load(Lo=0.96, At=At, F=slope * 12.0)
    assert mine.Lr == pytest.approx(theirs.value, rel=1e-9), (
        f"At = {At} m2, slope = {slope}: {mine.Lr:.4f} vs {theirs.value:.4f}"
    )


def test_lr_bounds_are_the_printed_values():
    """0.58 and 0.96 kN/m2 are 12 and 20 psf, Sect. 4.8.2."""
    assert roof_live_load(1.0, 0.0).Lr == pytest.approx(0.96)
    assert roof_live_load(1e6, 2.0).Lr == pytest.approx(0.58)
    # 20 psf = 0.9576 kPa and 12 psf = 0.5746 kPa; ASCE prints the rounded
    # metric values 0.96 and 0.58, which is what the code must use.
    assert 0.96 == pytest.approx(20.0 * 0.04788026, rel=5e-3)
    assert 0.58 == pytest.approx(12.0 * 0.04788026, rel=1e-2)


def test_r1_breakpoints_are_200_and_600_square_feet():
    """18.58 m2 and 55.74 m2 are the metric forms of 200 and 600 ft2."""
    assert 18.58 == pytest.approx(200.0 * 0.09290304, rel=1e-3)
    assert 55.74 == pytest.approx(600.0 * 0.09290304, rel=1e-3)


# ---------------------------------------------------------------------------
# combinations
# ---------------------------------------------------------------------------


#: The reference fabricates one extra combination that this package does not
#: emit: it negates the wind FACTOR to cover "wind the other way".  That is
#: correct for seismic - QE genuinely reverses with equal magnitude - and the
#: reference applies the same treatment to W.  For wind it is not correct:
#: pressure and suction patterns are not mirror images, and Chapter 2 carries
#: the sign in W itself.  On a real suction of -1.20 kN/m2 the fabricated
#: case returns +1.47, a downward pressure that does not exist.
REF_FABRICATED = {"LRFD 5 (uplift)", "ASD 7 (uplift)", "ASD 8 (uplift)"}


@needs_combos
@pytest.mark.parametrize("loads", [GRAVITY_CASE, UPLIFT_CASE])
def test_every_reference_combination_is_reproduced_exactly(loads):
    """Term by term, not just the envelope.

    Every combination the reference emits - except its fabricated wind
    reversal - must be produced by this package with the identical value.
    An envelope comparison alone could hide two offsetting errors.
    """
    # The reference always emits the floor-live combinations, so this
    # package is asked for them too - otherwise the comparison would report
    # a difference that is only a difference of scope.
    mine = {
        round(_evaluate(c.factors, loads), 10)
        for c in lrfd_combinations(include_floor_live=True)
    }
    ref = REF_COMBOS.lrfd_combinations(**loads)
    for c in ref:
        if c.name in REF_FABRICATED:
            continue
        assert round(c.value, 10) in mine, (
            f"{c.name} = {c.value:.4f} ({c.expression()}) has no counterpart "
            f"in this package's set {sorted(mine)}"
        )


@needs_combos
def test_envelopes_agree_once_the_reversal_is_a_real_load_case():
    """The architectural claim, tested.

    Give the reference a symmetric wind and let it fabricate the reversal;
    give this package the SAME two wind directions as two explicit cases.
    Both envelopes must then be identical - which is the whole argument for
    handling direction as a case rather than as a sign on the factor.
    """
    ref = REF_COMBOS.lrfd_combinations(D=0.30, Lr=0.58, W=1.20)
    ref_env = (min(c.value for c in ref), max(c.value for c in ref))

    loads = {"D": 0.30, "Lr": 0.58, "W_pos": 1.20, "W_neg": -1.20}
    mine = lrfd_combinations(wind_cases=("W_pos", "W_neg"))
    my_env = _envelope(mine, loads)

    assert my_env[0] == pytest.approx(ref_env[0], rel=1e-9)
    assert my_env[1] == pytest.approx(ref_env[1], rel=1e-9)


@needs_combos
def test_a_real_suction_is_not_turned_into_a_pressure():
    """The defect this file caught, pinned so it cannot return.

    With W = -1.20 kN/m2 (suction) and nothing else, the largest effect any
    combination may produce is 1.2D + 1.6Lr = 1.288.  A value near 1.47
    means a reversed-factor case has reappeared and is inventing a downward
    load of 1.2 kN/m2 that was never applied.
    """
    worst = max(_evaluate(c.factors, UPLIFT_CASE) for c in lrfd_combinations())
    assert worst == pytest.approx(1.288, rel=1e-6)

    ref = REF_COMBOS.lrfd_combinations(**UPLIFT_CASE)
    fabricated = max(c.value for c in ref)
    assert fabricated == pytest.approx(1.47, rel=1e-6)
    assert worst < fabricated


@needs_combos
def test_the_governing_uplift_combination_is_the_same_one():
    """0.9D + 1.0W, and it must be the minimum in both implementations."""
    worst = min(
        lrfd_combinations(), key=lambda c: _evaluate(c.factors, UPLIFT_CASE)
    )
    assert worst.name == "0.9D+1.0W"

    ref = REF_COMBOS.lrfd_combinations(**UPLIFT_CASE)
    ref_worst = min(ref, key=lambda c: c.value)
    assert ref_worst.factors["D"] == pytest.approx(0.9)
    assert _evaluate(worst.factors, UPLIFT_CASE) == pytest.approx(
        ref_worst.value, rel=1e-9
    )


@needs_combos
@pytest.mark.parametrize("loads", [GRAVITY_CASE, UPLIFT_CASE])
def test_asd_combinations_are_reproduced_exactly(loads):
    mine = {
        round(_evaluate(c.factors, loads), 10)
        for c in asd_combinations(include_floor_live=True)
    }
    ref = REF_COMBOS.asd_combinations(**loads)
    for c in ref:
        if c.name in REF_FABRICATED or "uplift" in c.name:
            continue
        assert round(c.value, 10) in mine, (
            f"{c.name} = {c.value:.4f} has no counterpart in {sorted(mine)}"
        )


def test_asd_uplift_dead_factor_is_0_6_by_hand():
    """Sect. 2.4.1 comb. 7: 0.6D + 0.6W. Not 0.9D.

    The LRFD value would overstate the restoring weight by 50% and
    under-design every holding-down bolt.
    """
    c = next(c for c in asd_combinations() if c.name == "0.6D+0.6W")
    assert c.factors == {"D": 0.6, "W": 0.6}


def test_lrfd_dead_only_factor_is_1_4_by_hand():
    c = next(c for c in lrfd_combinations() if c.name == "1.4D")
    assert c.factors == {"D": 1.4}


def test_the_roof_companion_is_never_summed_with_the_others():
    """Sect. 2.3.1 says (Lr or S or R), not Lr + S + R."""
    for companion in RoofCompanion:
        combos = lrfd_combinations(roof_companion=companion)
        for c in combos:
            present = {k for k in c.factors if k in {"Lr", "S", "R"}}
            assert len(present) <= 1, c.name


# ---------------------------------------------------------------------------
# surface discipline - a closed form, no reference needed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slope", [0.05, 0.10, 0.25, 0.40, 1.0])
def test_surface_conversion_is_exactly_one_over_cos(slope):
    a = AreaLoad(1.0, Surface.SLOPING)
    assert a.on_projected(slope) == pytest.approx(
        1.0 / math.cos(math.atan(slope)), rel=1e-12
    )


def test_a_1_in_10_roof_differs_by_half_a_percent():
    """The number that caught the analysis-module load defect."""
    a = AreaLoad(1.0, Surface.SLOPING)
    assert a.on_projected(0.10) == pytest.approx(1.004988, rel=1e-5)


def test_a_1_in_2_5_roof_differs_by_eight_percent():
    """Why the two surfaces cannot share one enum member."""
    a = AreaLoad(1.0, Surface.SLOPING)
    assert a.on_projected(0.40) == pytest.approx(1.077033, rel=1e-5)


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------


def test_all_load_relations_are_verified():
    from bimsteelauto.loads import combinations as C
    from bimsteelauto.loads import frame as F
    from bimsteelauto.loads import gravity as G

    unset = [
        f"{mod.__name__}.{k}"
        for mod in (G, C, F)
        for k, v in mod.VERIFIED.items()
        if not v
    ]
    assert unset == [], f"unverified load relations: {unset}"
