"""
bimsteelauto.design.units
=========================

The SI / imperial boundary, in one file.

``pyaisc360`` is kip-inch-ksi throughout.  This package is kN-mm-MPa
throughout.  Every conversion between them happens here and nowhere else, so
there is exactly one place to check when a number looks wrong by a factor
that smells like 25.4, 6.895 or 4.448.

The factors are exact by definition, not rounded:

    1 in   = 25.4 mm                 (exact, international inch)
    1 kip  = 4.4482216152605 kN      (exact, from lbf = 4.4482216152605 N)
    1 ksi  = 6.894757293168361 MPa   (derived from the two above)

``ksi`` is derived rather than typed so it cannot drift from the other two.
"""

from __future__ import annotations

__all__ = [
    "MM_PER_IN",
    "KN_PER_KIP",
    "MPA_PER_KSI",
    "mm_to_in",
    "in_to_mm",
    "mm2_to_in2",
    "mm3_to_in3",
    "mm4_to_in4",
    "mm6_to_in6",
    "mpa_to_ksi",
    "ksi_to_mpa",
    "kn_to_kip",
    "kip_to_kn",
    "knm_to_kipin",
    "kipin_to_knm",
]

#: Exact by international definition.
MM_PER_IN = 25.4
#: Exact: 1 lbf = 4.4482216152605 N, so 1 kip = 4.4482216152605 kN.
KN_PER_KIP = 4.4482216152605
#: Derived from the two above, never typed independently.
#: 1 ksi = 1 kip/in^2 = (KN_PER_KIP * 1000 N) / (MM_PER_IN^2 mm^2)
MPA_PER_KSI = KN_PER_KIP * 1000.0 / (MM_PER_IN**2)


# -- length ------------------------------------------------------------------


def mm_to_in(v: float) -> float:
    return v / MM_PER_IN


def in_to_mm(v: float) -> float:
    return v * MM_PER_IN


def mm2_to_in2(v: float) -> float:
    return v / MM_PER_IN**2


def mm3_to_in3(v: float) -> float:
    return v / MM_PER_IN**3


def mm4_to_in4(v: float) -> float:
    return v / MM_PER_IN**4


def mm6_to_in6(v: float) -> float:
    return v / MM_PER_IN**6


# -- stress ------------------------------------------------------------------


def mpa_to_ksi(v: float) -> float:
    return v / MPA_PER_KSI


def ksi_to_mpa(v: float) -> float:
    return v * MPA_PER_KSI


# -- force and moment --------------------------------------------------------


def kn_to_kip(v: float) -> float:
    return v / KN_PER_KIP


def kip_to_kn(v: float) -> float:
    return v * KN_PER_KIP


def knm_to_kipin(v: float) -> float:
    """kN.m -> kip.in.  Note the metre-to-millimetre step hiding in here."""
    return kn_to_kip(v) * 1000.0 / MM_PER_IN


def kipin_to_knm(v: float) -> float:
    return kip_to_kn(v) * MM_PER_IN / 1000.0
