"""
bimsteelauto.loads.combinations
===============================

ASCE 7-16 Chapter 2 load combinations, as factor sets.

This module produces the FACTORS, not evaluated numbers.  The analysis
solves each combination itself, so what it needs is a name and a dict -
``{"D": 1.2, "W": 1.0}`` - not a total.  Evaluating here as well would mean
two places could disagree about what "1.2D + 1.0W" means.

How wind direction is handled - and how it is NOT
--------------------------------------------------
The first version of this module emitted every wind combination twice, once
with ``+1.0W`` and once with ``-1.0W``, reasoning that wind blows both ways.
That is wrong, and the independent check caught it.

``W`` in Chapter 2 already carries its own sign: on a PEB roof it is
negative (suction), which is why ``0.9D + 1.0W`` is the uplift case without
any help.  Negating the FACTOR does not model wind from the other direction
- it turns that suction into an equal pressure, which is not a load that
exists.  On the uplift case it produced a spurious maximum 28% above the
real one.

Wind direction and internal pressure are handled the way the Specification
handles them: as separate load CASES, each with its own pressures.  ASCE
7-16 Ch. 28 gives Load Case A and Load Case B, each with both signs of
GCpi - four wind cases, which is exactly what ``peb_wind.py`` produces.
Pass their names as ``wind_cases`` and one combination is emitted per case,
with the code factor, positive.

The roof companion
------------------
Sect. 2.3.1 uses ``(Lr or S or R)`` - whichever of roof live, snow and rain
is largest, never their sum.  Which one that is depends on the project, so
it is an input here rather than a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from ..spec import DesignMethod, LoadCombination

__all__ = [
    "VERIFIED",
    "RoofCompanion",
    "DEFAULT_WIND_CASES",
    "lrfd_combinations",
    "asd_combinations",
    "combinations_for",
]

#: CLOSED 2026-09-01 against the ``asce7-loads`` skill's ``combos.py``, a
#: separately written implementation of the same chapter.
#:
#:     lrfd_2_3_1   every combination the reference emits is reproduced with
#:                  an identical value, TERM BY TERM - not merely the same
#:                  envelope, which two offsetting errors could also produce
#:     asd_2_4_1    same, including the 0.6D of comb. 7
#:     wind_case_expansion  with a symmetric wind supplied to this package as
#:                  two explicit cases, and to the reference as one it
#:                  reverses internally, the two envelopes are identical
#:     roof_companion_selection  no combination ever carries more than one of
#:                  Lr, S, R
#:
#: KNOWN, EXPLAINED DIFFERENCE - the reference emits one extra combination
#: per wind case, negating the wind FACTOR to cover "wind the other way".
#: That treatment is correct for seismic, where QE genuinely reverses with
#: equal magnitude, and the reference applies it to W as well.  For wind it
#: is not: pressure and suction patterns are not mirror images, and Chapter 2
#: already carries the sign inside W.  On a real suction of -1.20 kN/m2 the
#: fabricated case returns +1.47 - a downward pressure that was never
#: applied, 14% above the true governing gravity effect of 1.288.
#:
#: THIS PACKAGE'S FIRST VERSION HAD THE SAME DEFECT.  It was found by the
#: comparison above and is now pinned by
#: ``test_a_real_suction_is_not_turned_into_a_pressure``.
VERIFIED: dict[str, bool] = {
    "lrfd_2_3_1": True,
    "asd_2_4_1": True,
    "wind_case_expansion": True,
    "roof_companion_selection": True,
}

#: The default wind case name when a project has only one.
DEFAULT_WIND_CASES: tuple[str, ...] = ("W",)


class RoofCompanion(str, Enum):
    """Which of ``(Lr or S or R)`` governs on this roof.

    Not derived.  A Gulf project has no snow and usually no ponding case, so
    ``Lr`` is right - but saying so is the engineer's job, not a default this
    module reaches for.
    """

    ROOF_LIVE = "Lr"
    SNOW = "S"
    RAIN = "R"


def lrfd_combinations(
    *,
    roof_companion: RoofCompanion = RoofCompanion.ROOF_LIVE,
    wind_cases: Sequence[str] = DEFAULT_WIND_CASES,
    include_floor_live: bool = False,
    L_factor_reduced: bool = False,
) -> tuple[LoadCombination, ...]:
    """Strength design combinations, ASCE 7-16 Sect. 2.3.1.

    Parameters
    ----------
    roof_companion:
        The governing member of ``(Lr or S or R)``.
    wind_cases:
        The names of the wind load cases in the model.  One combination is
        emitted per case wherever Chapter 2 calls for ``W``.  Give the four
        Ch. 28 envelope cases here (Load Case A and B, each with both GCpi
        signs) and every one is enveloped.  Factors stay POSITIVE - the
        sign lives in the case, not in the factor.
    include_floor_live:
        A single-storey PEB usually has no floor live load ``L``; leaving it
        out keeps the sheet readable.  Set True for a mezzanine.
    L_factor_reduced:
        The Sect. 2.3.1 exception that drops the ``L`` factor from 1.0 to
        0.5 in combination 4, permitted when ``Lo <= 4.79 kN/m^2`` and the
        occupancy is neither public assembly nor a garage.  Defaults OFF:
        setting it where it does not apply understates the design load.
    """
    r = roof_companion.value
    lf = 0.5 if L_factor_reduced else 1.0
    ref = "ASCE 7-16 Sect. 2.3.1"
    winds = tuple(wind_cases) or DEFAULT_WIND_CASES

    combos: list[LoadCombination] = [
        LoadCombination("1.4D", {"D": 1.4}, f"{ref} comb. 1"),
    ]

    if include_floor_live:
        combos.append(
            LoadCombination(
                f"1.2D+1.6L+0.5{r}", {"D": 1.2, "L": 1.6, r: 0.5}, f"{ref} comb. 2"
            )
        )
        combos.append(
            LoadCombination(
                f"1.2D+1.6{r}+1.0L", {"D": 1.2, r: 1.6, "L": 1.0}, f"{ref} comb. 3"
            )
        )
    else:
        combos.append(
            LoadCombination(f"1.2D+1.6{r}", {"D": 1.2, r: 1.6}, f"{ref} comb. 3")
        )

    for w in winds:
        combos.append(
            LoadCombination(
                f"1.2D+1.6{r}+0.5{w}",
                {"D": 1.2, r: 1.6, w: 0.5},
                f"{ref} comb. 3",
            )
        )

    for w in winds:
        f4 = {"D": 1.2, w: 1.0, r: 0.5}
        if include_floor_live:
            f4["L"] = lf
        combos.append(
            LoadCombination(f"1.2D+1.0{w}+0.5{r}", f4, f"{ref} comb. 4")
        )

    # Combination 5 is the uplift case that sizes the anchors: the 0.9 on
    # dead load is there precisely so a suction W is not resisted by the
    # full weight of the building.
    for w in winds:
        combos.append(
            LoadCombination(
                f"0.9D+1.0{w}", {"D": 0.9, w: 1.0}, f"{ref} comb. 5"
            )
        )
    return tuple(combos)


def asd_combinations(
    *,
    roof_companion: RoofCompanion = RoofCompanion.ROOF_LIVE,
    wind_cases: Sequence[str] = DEFAULT_WIND_CASES,
    include_floor_live: bool = False,
) -> tuple[LoadCombination, ...]:
    """Allowable stress design combinations, ASCE 7-16 Sect. 2.4.1.

    Note the 0.6 on dead load in combination 7 - not 0.9 as in LRFD.  Using
    the LRFD value here would overstate the restoring weight by half and
    under-design the holding-down bolts.
    """
    r = roof_companion.value
    ref = "ASCE 7-16 Sect. 2.4.1"
    winds = tuple(wind_cases) or DEFAULT_WIND_CASES

    combos: list[LoadCombination] = [
        LoadCombination("1.0D", {"D": 1.0}, f"{ref} comb. 1"),
    ]
    if include_floor_live:
        combos.append(
            LoadCombination("1.0D+1.0L", {"D": 1.0, "L": 1.0}, f"{ref} comb. 2")
        )
        combos.append(
            LoadCombination(
                f"1.0D+0.75L+0.75{r}",
                {"D": 1.0, "L": 0.75, r: 0.75},
                f"{ref} comb. 4",
            )
        )
    combos.append(
        LoadCombination(f"1.0D+1.0{r}", {"D": 1.0, r: 1.0}, f"{ref} comb. 3")
    )

    for w in winds:
        combos.append(
            LoadCombination(f"1.0D+0.6{w}", {"D": 1.0, w: 0.6}, f"{ref} comb. 5")
        )
    for w in winds:
        f6 = {"D": 1.0, w: 0.45, r: 0.75}
        if include_floor_live:
            f6["L"] = 0.75
        combos.append(
            LoadCombination(f"1.0D+0.45{w}+0.75{r}", f6, f"{ref} comb. 6")
        )
    for w in winds:
        combos.append(
            LoadCombination(
                f"0.6D+0.6{w}", {"D": 0.6, w: 0.6}, f"{ref} comb. 7 (uplift)"
            )
        )
    return tuple(combos)


def combinations_for(
    method: DesignMethod,
    *,
    roof_companion: RoofCompanion = RoofCompanion.ROOF_LIVE,
    wind_cases: Sequence[str] = DEFAULT_WIND_CASES,
    include_floor_live: bool = False,
    L_factor_reduced: bool = False,
) -> tuple[LoadCombination, ...]:
    """The right set for the project's design method."""
    if method is DesignMethod.LRFD:
        return lrfd_combinations(
            roof_companion=roof_companion,
            wind_cases=wind_cases,
            include_floor_live=include_floor_live,
            L_factor_reduced=L_factor_reduced,
        )
    return asd_combinations(
        roof_companion=roof_companion,
        wind_cases=wind_cases,
        include_floor_live=include_floor_live,
    )
