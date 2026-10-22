"""
direct_strength.py -- AISI S100-16 Appendix 1, Direct Strength Method
(DSM) for C/Z cold-formed steel secondary members (columns and beams).

Pure standard library.

DSM works from ELASTIC BUCKLING loads/moments (local, distortional,
global), not from element-by-element effective widths. AISI S100-16
Appendix 1 does not prescribe how to get those elastic buckling values
for an arbitrary section -- the standard practice is a finite-strip
analysis (CUFSM is the free, widely used tool; THIN-WALL is another).

This module therefore takes Pcrl/Pcrd/Pcre (compression) or
Mcrl/Mcrd/Mcre (bending) AS INPUTS and applies the DSM strength curves.
It does NOT compute elastic buckling loads from raw geometry -- doing
that reliably for an arbitrary lipped C/Z section requires a numerical
finite-strip solver, and a hand-rolled closed-form approximation here
would be a false economy on a safety-critical calculation. Run CUFSM
(free: https://www.ce.jhu.edu/cufsm/) on your section, or use published
Pcr/Mcr values for a catalog shape, and feed the results in here.

STRENGTH-CURVE VERIFICATION STATUS:
  Beam curves (Mne/Mnl/Mnd) -- confirmed against an independent published
  source in this session (thresholds 0.56/2.78 My for global, 0.776 for
  local, 0.673 for distortional; coefficients 0.15/0.4 local, 0.22/0.5
  distortional). High confidence.

  Column curves (Pne/Pnl/Pnd) -- confirmed against a CFSEI technical
  note in this session (lambda_c<=1.5 standard column curve for global;
  0.776/0.15/0.4 for local, same form as beams; 0.561/0.25/0.6 for
  distortional). High confidence, but the extraction had one garbled
  digit on the global-buckling threshold -- 1.5 is the value used here,
  consistent with the AISC/AISI-family column curve and cross-checked
  against textbook DSM references. VERIFY against your edition's
  Appendix 1, Eq. 1.2.1-2/3/6/7/10/11 before a stamped submittal.

phi/Omega: 0.85/1.80 for compression (DSM columns), 0.90/1.67 for
flexure (DSM beams) -- same factors as the main-body EWM provisions for
these limit states (AISI calibrates DSM to the same target reliability).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from aisi_s100 import Result


# ----------------------------------------------------------------------
# Beam (flexural member) -- Appendix 1, Sec. 1.2.2
# ----------------------------------------------------------------------

def Mne_global(My: float, Mcre: float) -> float:
    """Global (lateral-torsional) buckling nominal moment. Eq. 1.2.2-2/3/4."""
    if Mcre >= 2.78 * My:
        return My
    if Mcre > 0.56 * My:
        return (10.0 / 9.0) * My * (1 - 10 * My / (36 * Mcre))
    return Mcre


def Mnl_local(Mne: float, Mcrl: float) -> float:
    """Local buckling nominal moment. Eq. 1.2.2-5/6."""
    lam_l = math.sqrt(Mne / Mcrl)
    if lam_l <= 0.776:
        return Mne
    return (1 - 0.15 * (Mcrl / Mne) ** 0.4) * (Mcrl / Mne) ** 0.4 * Mne


def Mnd_distortional(My: float, Mcrd: float) -> float:
    """Distortional buckling nominal moment. Eq. 1.2.2-7/8."""
    lam_d = math.sqrt(My / Mcrd)
    if lam_d <= 0.673:
        return My
    return (1 - 0.22 * (Mcrd / My) ** 0.5) * (Mcrd / My) ** 0.5 * My


def beam_strength_dsm(Sf: float, Fy: float, Mcre: float, Mcrl: float,
                       Mcrd: float) -> Result:
    """
    Governing DSM nominal flexural strength: Mn = min(Mnl(using Mne),
    Mnd). AISI S100-16 Appendix 1, Sec. 1.2.2.

    Sf = gross section modulus at extreme fiber (in^3)
    Fy = yield stress (ksi)
    Mcre, Mcrl, Mcrd = elastic critical moments for global,
        local, and distortional buckling (kip-in), from a finite-strip
        analysis (e.g., CUFSM) of the actual C/Z section.
    """
    My = Sf * Fy
    Mne = Mne_global(My, Mcre)
    Mnl = Mnl_local(Mne, Mcrl)
    Mnd = Mnd_distortional(My, Mcrd)
    Mn = min(Mnl, Mnd)

    r = Result("Flexural strength -- DSM, Appendix 1 Sec. 1.2.2", Mn, 0.90, 1.67,
               unit="kip-in")
    r.add("My = Sf*Fy", My, "kip-in", "")
    r.add("Mcre", Mcre, "kip-in", "input, from finite-strip analysis")
    r.add("Mne (global)", Mne, "kip-in", "Eq. 1.2.2-2/3/4")
    r.add("Mcrl", Mcrl, "kip-in", "input, from finite-strip analysis")
    r.add("Mnl (local, using Mne)", Mnl, "kip-in", "Eq. 1.2.2-5/6")
    r.add("Mcrd", Mcrd, "kip-in", "input, from finite-strip analysis")
    r.add("Mnd (distortional)", Mnd, "kip-in", "Eq. 1.2.2-7/8")
    r.add("Mn = min(Mnl, Mnd)", Mn, "kip-in", "governing")
    governing = "local/global interaction" if Mnl <= Mnd else "distortional"
    r.notes.append("Governing mode: %s." % governing)
    return r


# ----------------------------------------------------------------------
# Column (concentrically loaded compression member) -- Appendix 1,
# Sec. 1.2.1
# ----------------------------------------------------------------------

def Pne_global(Py: float, Pcre: float) -> float:
    """Global (flexural/torsional-flexural) buckling nominal load."""
    lam_c = math.sqrt(Py / Pcre)
    if lam_c <= 1.5:
        return (0.658 ** (lam_c ** 2)) * Py
    return (0.877 / lam_c ** 2) * Py


def Pnl_local(Pne: float, Pcrl: float) -> float:
    """Local buckling nominal load."""
    lam_l = math.sqrt(Pne / Pcrl)
    if lam_l <= 0.776:
        return Pne
    return (1 - 0.15 * (Pcrl / Pne) ** 0.4) * (Pcrl / Pne) ** 0.4 * Pne


def Pnd_distortional(Py: float, Pcrd: float) -> float:
    """Distortional buckling nominal load."""
    lam_d = math.sqrt(Py / Pcrd)
    if lam_d <= 0.561:
        return Py
    return (1 - 0.25 * (Pcrd / Py) ** 0.6) * (Pcrd / Py) ** 0.6 * Py


def column_strength_dsm(Ag: float, Fy: float, Pcre: float, Pcrl: float,
                         Pcrd: float) -> Result:
    """
    Governing DSM nominal axial strength: Pn = min(Pnl(using Pne), Pnd).
    AISI S100-16 Appendix 1, Sec. 1.2.1.

    Ag = gross area (in^2); Fy = yield stress (ksi); Pcre/Pcrl/Pcrd =
    elastic critical loads for global, local, distortional buckling
    (kips), from a finite-strip analysis of the actual section (CUFSM).
    """
    Py = Ag * Fy
    Pne = Pne_global(Py, Pcre)
    Pnl = Pnl_local(Pne, Pcrl)
    Pnd = Pnd_distortional(Py, Pcrd)
    Pn = min(Pnl, Pnd)

    r = Result("Axial strength -- DSM, Appendix 1 Sec. 1.2.1", Pn, 0.85, 1.80,
               unit="kips")
    r.add("Py = Ag*Fy", Py, "kips", "")
    r.add("Pcre", Pcre, "kips", "input, from finite-strip analysis")
    r.add("Pne (global)", Pne, "kips", "std. column curve, lambda_c<=1.5")
    r.add("Pcrl", Pcrl, "kips", "input, from finite-strip analysis")
    r.add("Pnl (local, using Pne)", Pnl, "kips", "")
    r.add("Pcrd", Pcrd, "kips", "input, from finite-strip analysis")
    r.add("Pnd (distortional)", Pnd, "kips", "")
    r.add("Pn = min(Pnl, Pnd)", Pn, "kips", "governing")
    governing = "local/global interaction" if Pnl <= Pnd else "distortional"
    r.notes.append("Governing mode: %s." % governing)
    return r
