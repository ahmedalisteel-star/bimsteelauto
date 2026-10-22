"""
combos.py -- ASCE 7-16 Chapter 2 load combinations, SI.

All loads in consistent units (kN, kN/m or kN/m^2 -- the combinations
are dimensionless factors, so whatever goes in comes out).

The seismic combinations are the ones worth care. Sec. 2.3.6 is written
in terms of E, and E resolves through Sec. 12.4 into two parts:

    Eh = rho * QE          horizontal, from the analysis
    Ev = 0.2 * SDS * D     vertical, proportional to dead load

Substituting those back gives the expanded forms this module produces:

    (1.2 + 0.2 SDS) D + rho QE + L + 0.2 S
    (0.9 - 0.2 SDS) D + rho QE

Omitting Ev -- writing 1.2D + QE and calling it done -- is the single
most common load combination error in seismic work, and it is
unconservative on the uplift case where the 0.9D combination governs
overturning and foundation design.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from asce7 import Result, fmt

__all__ = ["Combination", "lrfd_combinations", "asd_combinations",
           "governing_combination", "combination_table"]


@dataclass
class Combination:
    name: str
    ref: str
    factors: Dict[str, float]
    value: float = 0.0

    def expression(self) -> str:
        parts = []
        for k, f in self.factors.items():
            if f == 0:
                continue
            sign = "-" if f < 0 else "+"
            mag = abs(f)
            term = k if mag == 1.0 else "%s%s" % (fmt(mag), k)
            parts.append((sign, term))
        if not parts:
            return "0"
        out = ("-" if parts[0][0] == "-" else "") + parts[0][1]
        for sign, term in parts[1:]:
            out += " %s %s" % (sign, term)
        return out


def _evaluate(factors: Dict[str, float], loads: Dict[str, float]) -> float:
    return sum(f * loads.get(k, 0.0) for k, f in factors.items())


def lrfd_combinations(D: float = 0.0, L: float = 0.0, Lr: float = 0.0,
                      S: float = 0.0, R: float = 0.0, W: float = 0.0,
                      QE: float = 0.0, SDS: float = 0.0, rho: float = 1.0,
                      L_small: bool = False,
                      include_seismic: Optional[bool] = None,
                      ) -> List[Combination]:
    """Strength design combinations, Sec. 2.3.1 and 2.3.6.

    `L_small` invokes the Sec. 2.3.1 exception that lets the load factor
    on L drop from 1.0 to 0.5 in combinations 4, 5 and 6, permitted when
    Lo <= 4.79 kN/m^2 and the occupancy is not a public assembly or a
    garage. Setting it when it does not apply understates the design
    load, so it defaults off.

    QE is the horizontal seismic effect from the analysis, before rho.
    Sign conventions: pass QE positive; the combinations that reverse it
    are generated with the opposite sign so an envelope covers both.
    """
    if include_seismic is None:
        include_seismic = bool(QE) or bool(SDS)
    lf = 0.5 if L_small else 1.0
    roof = {"Lr": Lr, "S": S, "R": R}
    dominant = max(roof, key=lambda k: roof[k])

    combos: List[Combination] = [
        Combination("LRFD 1", "Sec. 2.3.1", {"D": 1.4}),
        Combination("LRFD 2", "Sec. 2.3.1",
                    {"D": 1.2, "L": 1.6, dominant: 0.5}),
        Combination("LRFD 3a", "Sec. 2.3.1",
                    {"D": 1.2, dominant: 1.6, "L": 1.0}),
        Combination("LRFD 3b", "Sec. 2.3.1",
                    {"D": 1.2, dominant: 1.6, "W": 0.5}),
        Combination("LRFD 4", "Sec. 2.3.1",
                    {"D": 1.2, "W": 1.0, "L": lf, dominant: 0.5}),
        Combination("LRFD 5", "Sec. 2.3.1", {"D": 0.9, "W": 1.0}),
        Combination("LRFD 5 (uplift)", "Sec. 2.3.1", {"D": 0.9, "W": -1.0}),
    ]

    if include_seismic:
        ev = 0.2 * SDS
        combos += [
            Combination("LRFD 6", "Sec. 2.3.6",
                        {"D": 1.2 + ev, "QE": rho, "L": lf, "S": 0.2}),
            Combination("LRFD 6 (reversed)", "Sec. 2.3.6",
                        {"D": 1.2 + ev, "QE": -rho, "L": lf, "S": 0.2}),
            Combination("LRFD 7", "Sec. 2.3.6",
                        {"D": 0.9 - ev, "QE": rho}),
            Combination("LRFD 7 (reversed)", "Sec. 2.3.6",
                        {"D": 0.9 - ev, "QE": -rho}),
        ]

    loads = {"D": D, "L": L, "Lr": Lr, "S": S, "R": R, "W": W, "QE": QE}
    for c in combos:
        c.value = _evaluate(c.factors, loads)
    return combos


def asd_combinations(D: float = 0.0, L: float = 0.0, Lr: float = 0.0,
                     S: float = 0.0, R: float = 0.0, W: float = 0.0,
                     QE: float = 0.0, SDS: float = 0.0, rho: float = 1.0,
                     include_seismic: Optional[bool] = None,
                     ) -> List[Combination]:
    """Allowable stress design combinations, Sec. 2.4.1 and 2.4.5.

    The 0.6 on W and the 0.7 on E convert strength-level wind and
    seismic effects to service level. Applying 1.0W in an ASD check --
    or 0.6W in an LRFD check -- is a factor-of-1.7 error in the wrong
    direction, and it is easy to make when combinations are copied
    between projects that used different methods.
    """
    if include_seismic is None:
        include_seismic = bool(QE) or bool(SDS)
    roof = {"Lr": Lr, "S": S, "R": R}
    dominant = max(roof, key=lambda k: roof[k])

    combos: List[Combination] = [
        Combination("ASD 1", "Sec. 2.4.1", {"D": 1.0}),
        Combination("ASD 2", "Sec. 2.4.1", {"D": 1.0, "L": 1.0}),
        Combination("ASD 3", "Sec. 2.4.1", {"D": 1.0, dominant: 1.0}),
        Combination("ASD 4", "Sec. 2.4.1",
                    {"D": 1.0, "L": 0.75, dominant: 0.75}),
        Combination("ASD 5", "Sec. 2.4.1", {"D": 1.0, "W": 0.6}),
        Combination("ASD 6", "Sec. 2.4.1",
                    {"D": 1.0, "L": 0.75, "W": 0.45, dominant: 0.75}),
        Combination("ASD 7", "Sec. 2.4.1", {"D": 0.6, "W": 0.6}),
        Combination("ASD 7 (uplift)", "Sec. 2.4.1", {"D": 0.6, "W": -0.6}),
    ]

    if include_seismic:
        ev = 0.2 * SDS
        combos += [
            Combination("ASD 8", "Sec. 2.4.5",
                        {"D": 1.0 + 0.7 * ev, "QE": 0.7 * rho}),
            Combination("ASD 8 (reversed)", "Sec. 2.4.5",
                        {"D": 1.0 + 0.7 * ev, "QE": -0.7 * rho}),
            Combination("ASD 9", "Sec. 2.4.5",
                        {"D": 1.0 + 0.525 * ev, "QE": 0.525 * rho,
                         "L": 0.75, "S": 0.75}),
            Combination("ASD 10", "Sec. 2.4.5",
                        {"D": 0.6 - 0.7 * ev, "QE": 0.7 * rho}),
            Combination("ASD 10 (reversed)", "Sec. 2.4.5",
                        {"D": 0.6 - 0.7 * ev, "QE": -0.7 * rho}),
        ]

    loads = {"D": D, "L": L, "Lr": Lr, "S": S, "R": R, "W": W, "QE": QE}
    for c in combos:
        c.value = _evaluate(c.factors, loads)
    return combos


def governing_combination(method: str = "LRFD", **loads) -> Result:
    """Run every combination and report the governing one.

    Both extremes are reported. The maximum sizes the member; the
    minimum matters for uplift, overturning and net tension at holdowns,
    and a sheet that reports only the maximum has not checked the load
    case that pulls the building off its foundation.
    """
    m = method.upper().strip()
    if m == "LRFD":
        combos = lrfd_combinations(**loads)
    elif m == "ASD":
        combos = asd_combinations(**loads)
    else:
        raise ValueError("method must be 'LRFD' or 'ASD', not %r" % method)

    hi = max(combos, key=lambda c: c.value)
    lo = min(combos, key=lambda c: c.value)

    r = Result("Governing %s combination" % m, hi.value, "",
               hi.ref)
    for c in combos:
        r.step("%s: %s" % (c.name, c.expression()), c.value, "", c.ref)
    r.step("GOVERNING MAXIMUM -- %s" % hi.name, hi.value, "", hi.ref)
    r.step("GOVERNING MINIMUM -- %s" % lo.name, lo.value, "", lo.ref)
    r.extra = {"combinations": combos, "max": hi, "min": lo}
    if lo.value < 0:
        r.note("The minimum combination is negative (%s = %s): net uplift "
               "or reversal. Check anchorage, holdowns and overturning "
               "against this case, not against the maximum."
               % (lo.name, fmt(lo.value)))
    if m == "LRFD":
        r.note("Strength-level combination. Compare against phi*Rn, "
               "never against an allowable stress.")
    else:
        r.note("Service-level combination. Compare against Rn/Omega or an "
               "allowable stress, never against phi*Rn.")
    return r


def combination_table(combos: List[Combination]) -> str:
    """Markdown table of combinations, for dropping into a sheet."""
    L = ["| Combination | Expression | Value | Reference |",
         "|---|---|---:|---|"]
    for c in combos:
        L.append("| %s | %s | %s | %s |"
                 % (c.name, c.expression(), fmt(c.value), c.ref))
    return "\n".join(L)
