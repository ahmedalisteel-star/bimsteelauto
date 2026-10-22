"""
asce7.py -- shared primitives for ASCE 7-16 load calculations in SI.

Everything in this package returns a `Result`, not a bare float. A load
calculation that reports only its answer cannot be reviewed, and load
calculations get reviewed -- by a checker, by a plan reviewer, by the
engineer of record six months later. The `steps` list is the audit
trail: every intermediate value with the clause it came from.

Unit convention, enforced by discipline rather than by a unit library:

    length          m
    area            m^2
    force           kN
    line load       kN/m
    area load       kN/m^2  (= kPa)
    weight density  kN/m^3
    velocity        m/s
    velocity press. N/m^2   -- see note below
    period          s
    stress          MPa

The one deliberate inconsistency: velocity pressure q comes out of
Eq. 26.10-1 in N/m^2 because that is how the SI form of the equation is
written in the standard (0.613 is an SI constant). `velocity_pressure()`
returns N/m^2 and every wind pressure function converts to kN/m^2 at the
point of use. Do not "fix" this by changing the constant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

__all__ = [
    "Result", "Check", "interp", "interp_table", "fmt",
    "RISK_CATEGORIES", "importance_factor",
    "G", "KN_PER_KIP", "M_PER_FT", "KPA_PER_PSF", "MS_PER_MPH",
]

# -- unit bridges, for reading US-written references ------------------

G = 9.80665           # m/s^2
KN_PER_KIP = 4.448222
M_PER_FT = 0.3048
KPA_PER_PSF = 0.0478803
MS_PER_MPH = 0.44704


# -- result container -------------------------------------------------

@dataclass
class Result:
    """One computed quantity plus the trail that produced it."""
    name: str
    value: float
    unit: str = ""
    ref: str = ""
    steps: List[Tuple[str, Any, str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def step(self, label: str, value: Any, unit: str = "", ref: str = ""):
        self.steps.append((label, value, unit, ref))
        return self

    def note(self, text: str):
        """Attach a caveat. Notes render as call-outs on the sheet, so use
        them for anything a reviewer must not skim past."""
        self.notes.append(text)
        return self

    def __float__(self):
        return float(self.value)

    def __repr__(self):
        return "<%s = %s %s (%s)>" % (self.name, fmt(self.value),
                                      self.unit, self.ref)


@dataclass
class Check:
    """A demand-vs-limit comparison. `limit` is whatever the code caps,
    which for loads is usually a drift or a ratio rather than a strength."""
    name: str
    demand: float
    limit: float
    unit: str = ""
    ref: str = ""

    @property
    def ratio(self) -> float:
        return abs(self.demand) / self.limit if self.limit else float("inf")

    @property
    def passes(self) -> bool:
        return self.ratio <= 1.0

    @property
    def status(self) -> str:
        return "OK" if self.passes else "EXCEEDED"


# -- interpolation ----------------------------------------------------

def interp(x: float, xs, ys, clamp: bool = True) -> float:
    """Straight-line interpolation on a tabulated function.

    ASCE 7 says "straight-line interpolation for intermediate values" in
    a dozen places and means it literally -- not log, not spline. Outside
    the tabulated range the standard intends the end value, so `clamp`
    defaults to True. Extrapolating a code table is never correct.
    """
    pts = sorted(zip(xs, ys))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if x <= xs[0]:
        if not clamp and x < xs[0]:
            raise ValueError("%g below tabulated range %g" % (x, xs[0]))
        return ys[0]
    if x >= xs[-1]:
        if not clamp and x > xs[-1]:
            raise ValueError("%g above tabulated range %g" % (x, xs[-1]))
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            t = (x - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + t * (ys[i + 1] - ys[i])
    raise ValueError("interpolation failed for %g" % x)


def interp_table(x: float, table: dict, key: str) -> float:
    """Interpolate one row of a {row_key: {x: y}} table.

    Rows whose values are non-numeric (the "see Section 11.4.8" cells in
    the ASCE 7-16 site coefficient tables) raise rather than guess. A
    site that lands in one of those cells needs a site-specific ground
    motion analysis, and returning a number there would hide that.
    """
    row = table[key]
    xs = sorted(row)
    ys = [row[k] for k in xs]
    bad = [k for k, v in zip(xs, ys) if not isinstance(v, (int, float))]
    if bad:
        lo = min(bad)
        if x >= lo:
            raise ValueError(
                "Site Class %s at %g requires site-specific analysis "
                "(the table cell reads 'see Section 11.4.8'). No "
                "coefficient exists to return." % (key, x))
        xs = [k for k in xs if k < lo]
        ys = [row[k] for k in xs]
    return interp(x, xs, ys)


# -- risk category ----------------------------------------------------

RISK_CATEGORIES = {
    "I": "Low hazard to human life: agricultural, minor storage, "
         "temporary facilities.",
    "II": "All buildings not in I, III or IV. The default.",
    "III": "Substantial hazard: assembly >300, schools, jails, "
           "power/water plants, buildings with hazardous materials in "
           "quantities below the IV threshold.",
    "IV": "Essential facilities: hospitals with surgery or emergency "
          "treatment, fire and police stations, emergency shelters, "
          "aviation control towers, critical national defence.",
}

# Table 1.5-2. Snow and ice share Is; wind has its own column; the
# seismic column is Ie and is used in Chapters 12 and 13.
_IMPORTANCE = {
    #        Is     Iw     Ie
    "I":   (0.80,  1.00,  1.00),
    "II":  (1.00,  1.00,  1.00),
    "III": (1.10,  1.00,  1.25),
    "IV":  (1.20,  1.00,  1.50),
}


def importance_factor(risk_category: str, kind: str = "seismic") -> Result:
    """Importance factor from Table 1.5-2.

    In ASCE 7-16 the wind importance factor is 1.0 for every risk
    category -- risk is carried by the mapped wind speed instead, which
    is why Figs. 26.5-1 through 26.5-2 come as a set of maps rather than
    one map with a multiplier. Iw is kept here so the sheet can state it
    explicitly; a blank on the sheet reads as an omission.
    """
    rc = str(risk_category).upper().strip()
    if rc not in _IMPORTANCE:
        raise ValueError("Risk category must be I, II, III or IV, not %r. "
                         "See Table 1.5-1." % risk_category)
    Is, Iw, Ie = _IMPORTANCE[rc]
    val = {"snow": Is, "ice": Is, "wind": Iw, "seismic": Ie}[kind]
    sym = {"snow": "Is", "ice": "Is", "wind": "Iw", "seismic": "Ie"}[kind]
    r = Result("Importance factor %s" % sym, val, "", "Table 1.5-2")
    r.step("Risk category", rc, "", "Table 1.5-1")
    r.step(RISK_CATEGORIES[rc], "", "", "")
    r.step(sym, val, "", "Table 1.5-2")
    if kind == "wind":
        r.note("Iw = 1.0 for all risk categories in ASCE 7-16. Risk is "
               "carried by the mapped wind speed V, so confirm V was "
               "taken from the map for Risk Category %s." % rc)
    return r


# -- formatting -------------------------------------------------------

def fmt(v) -> str:
    """Render a number for a calculation sheet.

    Significant figures are chosen so that a checker can re-key the value
    and reproduce the next line. Three decimals on small numbers matters:
    Cs values live around 0.05 and rounding to two decimals destroys them.
    """
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if v != v:
            return "-"
        a = abs(v)
        if a >= 1e5 or (a < 1e-4 and a > 0):
            return "%.4g" % v
        if v == int(v) and a < 1e6:
            return "%d" % int(v)
        if a >= 100:
            return "%.1f" % v
        if a >= 1:
            return "%.3f" % v
        return "%.4f" % v
    return str(v)
