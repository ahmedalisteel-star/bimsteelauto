"""
gravity.py -- ASCE 7-16 Chapters 4, 7 and 8: live, snow and rain, SI.

Area loads in kN/m^2, areas in m^2, lengths in m, snow densities kN/m^3.
"""

from __future__ import annotations

import math
from typing import Optional

from asce7 import Result, importance_factor

__all__ = [
    "KLL_TABLE", "live_load_reduction", "roof_live_load",
    "flat_roof_snow", "sloped_roof_snow", "snow_drift",
    "rain_load", "ponding_check",
]

# Table 4.7-1
KLL_TABLE = {
    "interior column": 4,
    "exterior column without cantilever slab": 4,
    "edge column with cantilever slab": 3,
    "corner column with cantilever slab": 2,
    "edge beam without cantilever slab": 2,
    "interior beam": 2,
    "all other": 1,
}

# Sec. 4.7.2 threshold and the Lo ceiling, converted from 400 ft^2 and
# 100 psf.
_KLL_AT_MIN = 37.16     # m^2
_LO_LIMIT = 4.79        # kN/m^2


def live_load_reduction(Lo: float, AT: float, member: str = "interior beam",
                        floors_supported: int = 1,
                        occupancy: str = "general") -> Result:
    """Reduced live load L, Eq. 4.7-1 (SI form).

        L = Lo (0.25 + 4.57 / sqrt(KLL * AT))

    The reduction exists because the probability that every square metre
    of a large tributary area is loaded to Lo simultaneously falls as the
    area grows. That reasoning fails for assembly occupancies (crowds do
    fill the whole floor) and for garages, which is why Sec. 4.7.4-4.7.6
    exclude them.
    """
    key = member.lower().strip()
    if key not in KLL_TABLE:
        raise ValueError("member must be one of: %s"
                         % ", ".join(sorted(KLL_TABLE)))
    KLL = KLL_TABLE[key]
    influence = KLL * AT

    r = Result("Reduced live load L", Lo, "kN/m^2", "Sec. 4.7")
    r.step("Lo (unreduced, Table 4.3-1)", Lo, "kN/m^2", "Table 4.3-1")
    r.step("Member type", key, "", "Table 4.7-1")
    r.step("KLL", KLL, "", "Table 4.7-1")
    r.step("AT (tributary area)", AT, "m^2", "")
    r.step("KLL x AT (influence area)", influence, "m^2", "Sec. 4.7.2")

    occ = occupancy.lower().strip()
    if occ in ("assembly", "public assembly"):
        r.value = Lo
        r.step("No reduction permitted (assembly)", Lo, "kN/m^2",
               "Sec. 4.7.5")
        r.note("Live load reduction is not permitted for assembly "
               "occupancies -- the statistical basis for the reduction "
               "does not hold when the design event is a full crowd.")
        return r
    if occ in ("garage", "passenger vehicle garage") and floors_supported < 2:
        r.value = Lo
        r.step("No reduction permitted (garage, one floor)", Lo, "kN/m^2",
               "Sec. 4.7.4")
        r.note("Passenger vehicle garages may be reduced only for members "
               "supporting two or more floors, and then by no more than "
               "20% (Sec. 4.7.4).")
        return r
    if Lo > _LO_LIMIT and floors_supported < 2:
        r.value = Lo
        r.step("No reduction permitted (Lo > 4.79 kN/m^2)", Lo, "kN/m^2",
               "Sec. 4.7.3")
        r.note("For Lo above 4.79 kN/m^2 the reduction is permitted only "
               "for members supporting two or more floors, limited to "
               "20% (Sec. 4.7.3).")
        return r
    if influence < _KLL_AT_MIN:
        r.value = Lo
        r.step("KLL x AT < 37.16 m^2, no reduction", Lo, "kN/m^2",
               "Sec. 4.7.2")
        return r

    factor = 0.25 + 4.57 / math.sqrt(influence)
    floor_limit = 0.50 if floors_supported <= 1 else 0.40
    factor_applied = max(factor, floor_limit)
    if (Lo > _LO_LIMIT or occ in ("garage", "passenger vehicle garage")):
        factor_applied = max(factor_applied, 0.80)
        r.step("Reduction capped at 20% for this occupancy", 0.80, "",
               "Sec. 4.7.3 / 4.7.4")
    L = Lo * factor_applied

    r.step("0.25 + 4.57/sqrt(KLL AT)", factor, "", "Eq. 4.7-1")
    r.step("Floor on the reduction (%d floor%s supported)"
           % (floors_supported, "" if floors_supported == 1 else "s"),
           floor_limit, "", "Sec. 4.7.2")
    r.step("Reduction factor used", factor_applied, "", "Sec. 4.7")
    r.step("L = Lo x factor", L, "kN/m^2", "Eq. 4.7-1")
    r.value = L
    r.extra = {"KLL": KLL, "influence": influence, "factor": factor_applied}
    return r


def roof_live_load(Lo: float = 0.96, At: float = 0.0,
                   F: float = 0.0) -> Result:
    """Lr = Lo * R1 * R2, Eq. 4.8-1. Bounded to 0.58 <= Lr <= 0.96 kN/m^2.

    F is the roof slope expressed as rise per 12 units of run for a
    pitched roof, or the rise-to-span ratio multiplied by 32 for an arch
    or dome. Roof live load is a construction and maintenance load, not
    an occupancy load, and it does not combine with snow -- Chapter 2
    takes whichever of Lr, S or R is larger.
    """
    if At <= 18.58:
        R1 = 1.0
    elif At < 55.74:
        R1 = 1.2 - 0.011 * At
    else:
        R1 = 0.6
    if F <= 4:
        R2 = 1.0
    elif F < 12:
        R2 = 1.2 - 0.05 * F
    else:
        R2 = 0.6

    Lr = max(0.58, min(0.96, Lo * R1 * R2))
    r = Result("Roof live load Lr", Lr, "kN/m^2", "Sec. 4.8.2")
    r.step("Lo", Lo, "kN/m^2", "Table 4.3-1")
    r.step("At (tributary roof area)", At, "m^2", "")
    r.step("R1", R1, "", "Sec. 4.8.2")
    r.step("F (slope factor)", F, "", "Sec. 4.8.2")
    r.step("R2", R2, "", "Sec. 4.8.2")
    r.step("Lr = Lo R1 R2 (0.58 to 0.96)", Lr, "kN/m^2", "Eq. 4.8-1")
    r.note("Lr, S and R are alternatives in the Chapter 2 combinations, "
           "not additive. On a snow-governed roof Lr rarely controls.")
    return r


def flat_roof_snow(pg: float, Ce: float = 1.0, Ct: float = 1.0,
                   risk_category: str = "II") -> Result:
    """pf = 0.7 Ce Ct Is pg, Eq. 7.3-1, plus the Sec. 7.3.4 minimum.

    The 0.7 accounts for wind scouring a flat roof. Sec. 7.3.4 then
    reimposes a minimum, because a sheltered roof in a low-ground-snow
    region can accumulate more than the scoured value suggests -- and
    the minimum is a separate uniform load case, not a floor on pf for
    drift purposes.
    """
    Is = importance_factor(risk_category, "snow").value
    pf = 0.7 * Ce * Ct * Is * pg
    pm = Is * pg if pg <= 0.96 else 0.96 * Is

    r = Result("Flat roof snow load pf", pf, "kN/m^2", "Eq. 7.3-1")
    r.step("pg (ground snow load)", pg, "kN/m^2", "Fig. 7.2-1")
    r.step("Ce (exposure)", Ce, "", "Table 7.3-1")
    r.step("Ct (thermal)", Ct, "", "Table 7.3-2")
    r.step("Is (importance)", Is, "", "Table 1.5-2")
    r.step("pf = 0.7 Ce Ct Is pg", pf, "kN/m^2", "Eq. 7.3-1")
    r.step("pm (minimum, Sec. 7.3.4)", pm, "kN/m^2", "Sec. 7.3.4")
    r.extra = {"pf": pf, "pm": pm, "Is": Is}
    r.note("Sec. 7.3.4's minimum applies only to monoslope, hip and "
           "gable roofs with slope below 15 deg, and to low-slope roofs. "
           "It is a separate uniform load case -- it does not replace pf "
           "when computing drift or unbalanced loads.")
    if Ce < 1.0:
        r.note("Ce below 1.0 claims a fully exposed roof in open "
               "terrain. Sec. 7.3.1 requires the roof to be exposed on "
               "all sides with no shelter from terrain, higher "
               "structures or trees within 10 times their height.")
    return r


def sloped_roof_snow(pf: float, slope_deg: float, Ct: float = 1.0,
                     slippery: bool = False,
                     obstructed: bool = False) -> Result:
    """ps = Cs * pf, Eq. 7.4-1, with Cs from Fig. 7.4-1.

    Cs credits snow sliding off. A roof only sheds if the surface is
    slippery AND unobstructed -- a single snow guard, a vent stack, or a
    rough surface removes the credit entirely, which is why `slippery`
    and `obstructed` are separate arguments.
    """
    theta = slope_deg
    unobstructed_slippery = slippery and not obstructed

    if Ct <= 1.0:
        lo, hi = (5.0, 70.0) if unobstructed_slippery else (30.0, 70.0)
    elif Ct <= 1.1:
        lo, hi = (10.0, 70.0) if unobstructed_slippery else (37.5, 70.0)
    else:
        lo, hi = (15.0, 70.0) if unobstructed_slippery else (45.0, 70.0)

    if theta <= lo:
        Cs = 1.0
    elif theta >= hi:
        Cs = 0.0
    else:
        Cs = 1.0 - (theta - lo) / (hi - lo)

    ps = Cs * pf
    r = Result("Sloped roof snow load ps", ps, "kN/m^2", "Eq. 7.4-1")
    r.step("pf", pf, "kN/m^2", "Eq. 7.3-1")
    r.step("Roof slope", theta, "deg", "")
    r.step("Ct", Ct, "", "Table 7.3-2")
    r.step("Surface", "slippery" if slippery else "not slippery", "",
           "Sec. 7.4")
    r.step("Obstructed", obstructed, "", "Sec. 7.4")
    r.step("Cs", Cs, "", "Fig. 7.4-1")
    r.step("ps = Cs pf", ps, "kN/m^2", "Eq. 7.4-1")
    if theta > 0:
        r.note("Unbalanced snow (Sec. 7.6) is a separate required load "
               "case for gable, hip, curved and sawtooth roofs and is "
               "not computed here. On many roofs it governs the leeward "
               "rafters.")
    return r


def snow_drift(lu: float, pg: float, hb: float, hc: float,
               leeward: bool = True) -> Result:
    """Leeward or windward drift at a roof step, Sec. 7.7 (SI form).

        gamma = 0.426 pg + 2.2  <= 4.7 kN/m^3      Eq. 7.7-1
        hd    = 0.416 lu^(1/3) (pg + 0.479)^(1/4) - 0.457

    Drift is the most common cause of snow-related roof collapse,
    because it concentrates several times the balanced load on a narrow
    strip that was designed for the uniform case.
    """
    if lu <= 0:
        raise ValueError("Upwind fetch lu must be positive (m).")
    gamma = min(0.426 * pg + 2.2, 4.7)
    hd = 0.416 * lu ** (1.0 / 3.0) * (pg + 0.479) ** 0.25 - 0.457
    hd = max(hd, 0.0)
    if not leeward:
        hd = 0.75 * hd

    r = Result("Snow drift", 0.0, "kN/m^2", "Sec. 7.7")
    r.step("lu (upwind fetch)", lu, "m", "Sec. 7.7.1")
    r.step("pg", pg, "kN/m^2", "Fig. 7.2-1")
    r.step("gamma = 0.426 pg + 2.2 <= 4.7", gamma, "kN/m^3", "Eq. 7.7-1")
    r.step("hb (balanced snow height = ps/gamma)", hb, "m", "Sec. 7.7.1")
    r.step("hc (clear height of the step)", hc, "m", "Sec. 7.7.1")
    r.step("Drift type", "leeward" if leeward else "windward", "",
           "Sec. 7.7.1")
    r.step("hd", hd, "m", "Fig. 7.6-1 / Sec. 7.7.1")

    if hc / hb < 0.2 if hb > 0 else False:
        r.value = 0.0
        r.step("hc/hb < 0.2 -- no drift load required", 0.0, "kN/m^2",
               "Sec. 7.7.1")
        return r

    if hd <= hc:
        w = 4.0 * hd
        h_used = hd
    else:
        h_used = hc
        w = 4.0 * hd ** 2 / hc if hc > 0 else 0.0
        w = min(w, 8.0 * hc)
    pd = h_used * gamma

    r.step("Drift height used", h_used, "m", "Sec. 7.7.1")
    r.step("Drift width w", w, "m", "Sec. 7.7.1")
    r.step("pd = h x gamma (peak surcharge)", pd, "kN/m^2", "Sec. 7.7.1")
    r.value = pd
    r.extra = {"gamma": gamma, "hd": hd, "w": w, "pd": pd,
               "h_used": h_used}
    r.note("The surcharge is triangular, peaking at pd against the wall "
           "and falling to zero at width w. Total roof load in the drift "
           "is pd + the balanced load, and the step wall must also carry "
           "the resulting lateral pressure.")
    return r


def rain_load(ds: float, dh: float) -> Result:
    """R = 0.0098 (ds + dh), Eq. 8.3-1 (SI, depths in mm).

    ds is the static head to the top of the primary drainage inlet with
    the secondary blocked; dh is the additional hydraulic head to drive
    the design flow through the secondary. Sec. 8.4 then requires a
    stability check against ponding, because a roof that deflects under
    R collects more water and deflects further.
    """
    R = 0.0098 * (ds + dh)
    r = Result("Rain load R", R, "kN/m^2", "Eq. 8.3-1")
    r.step("ds (static head, secondary blocked)", ds, "mm", "Sec. 8.2")
    r.step("dh (hydraulic head at secondary)", dh, "mm", "Sec. 8.2")
    r.step("R = 0.0098 (ds + dh)", R, "kN/m^2", "Eq. 8.3-1")
    r.note("Sec. 8.4 requires a ponding instability check for roofs "
           "flatter than 1/4 in per ft (about 1.2 deg), evaluated with "
           "the primary drainage assumed blocked.")
    return r


def ponding_check(slope_deg: float) -> Result:
    """Whether Sec. 8.4 requires a ponding instability investigation."""
    required = slope_deg < 1.19   # 1/4 in per ft
    r = Result("Ponding investigation required", required, "", "Sec. 8.4")
    r.step("Roof slope", slope_deg, "deg", "")
    r.step("Threshold (1/4 in per ft)", 1.19, "deg", "Sec. 8.4")
    r.step("Investigation required", required, "", "Sec. 8.4")
    if required:
        r.note("Ponding instability must be checked with the primary "
               "drainage blocked and the roof deflected. Camber, "
               "deflection under R, and the stiffness of the supporting "
               "members all enter -- this is a stability problem, not a "
               "strength check.")
    return r
