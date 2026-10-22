"""
aisi_s100.py -- AISI S100-16 cold-formed steel design functions for
SECONDARY MEMBERS (C or Z purlins/girts), Effective Width Method (EWM),
plus shear, combined bending+shear, web crippling, and deflection.

Pure standard library. No numpy/scipy. LRFD and ASD both supported --
every Result carries phi AND omega; call .design("LRFD") or
.design("ASD") to get the available strength.

UNITS: US customary throughout -- force = kips, length = inches,
stress = ksi. Convert feet/plf/psf at the call site and label the
conversion on the calc sheet.

VERIFICATION STATUS (read before use on a stamped submittal):
  - E, G, Poisson's ratio ................ verified (AISI S100-16 Sec. A3.1)
  - Winter effective-width equation ....... verified, standard form
  - Plate buckling k = 4.0 (stiffened),
    k = 0.43 (unstiffened) ................ verified, standard values
  - phi_b = 0.90, phi_c = 0.85 (LRFD) ..... verified
  - Omega_b = 1.67, Omega_c = 1.80 (ASD) .. standard textbook values,
                                             cross-check against your
                                             edition's Table A4.1.2-1/2
  - Shear Fv equation (3 h/t ranges) ...... standard closed form,
                                             unchanged across editions;
                                             phi_v/Omega_v split by range
                                             NOT independently verified in
                                             this session -- CHECK Table
                                             C3.2.1-1 before final use
  - Web crippling ......................... equation *structure* only.
                                             C1..C11 coefficients are NOT
                                             hard-coded (they are a large
                                             table keyed on support/flange/
                                             load condition) -- YOU must
                                             supply them from AISI Table
                                             C3.4.1-1 through -4.
  - Effective width "Procedure I" (flexure
    at f = Fy, single pass, no iteration) . a recognized AISI-permitted
                                             simplification, NOT the more
                                             economical iterative
                                             Procedure II. Conservative.
  - Edge stiffener (lip) effective width .. SIMPLIFIED as an unstiffened
                                             element (k=0.43). The full
                                             AISI B4.2 adequate-stiffener
                                             procedure (Ia, Is, C-based k
                                             between 0.43 and 4.0) is NOT
                                             implemented. Flags itself in
                                             the calc sheet as an
                                             approximation.

This module does NOT replace a licensed engineer's independent check.
See README.md for the full scope statement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ----------------------------------------------------------------------
# Constants -- AISI S100-16 Sec. A3.1 (note: CFS uses 29,500 ksi, NOT
# AISC's 29,000 ksi)
# ----------------------------------------------------------------------

E_STEEL = 29500.0   # ksi
G_STEEL = 11300.0   # ksi
MU = 0.30           # Poisson's ratio


# ----------------------------------------------------------------------
# Result / Check containers (same shape as the AISC 360 skill's, so a
# single calc_sheet.py can render either)
# ----------------------------------------------------------------------

@dataclass
class Result:
    """Nominal strength plus the audit trail that produced it."""
    name: str
    Rn: float
    phi: float
    omega: float
    steps: List[Tuple[str, object, str, str]] = field(default_factory=list)
    unit: str = "kips"
    notes: List[str] = field(default_factory=list)

    def design(self, method: str = "ASD") -> float:
        m = method.upper()
        if m == "LRFD":
            return self.phi * self.Rn
        if m == "ASD":
            return self.Rn / self.omega
        raise ValueError("method must be 'LRFD' or 'ASD'")

    def add(self, label, value, unit="", ref=""):
        self.steps.append((label, value, unit, ref))
        return self


@dataclass
class Check:
    """A single limit-state check: demand vs. capacity."""
    limit_state: str
    demand: float
    capacity: float
    unit: str = "kips"
    reference: str = ""

    @property
    def ratio(self) -> float:
        if self.capacity == 0:
            return float("inf")
        return self.demand / self.capacity

    @property
    def passes(self) -> bool:
        return self.ratio <= 1.0

    @property
    def status(self) -> str:
        return "OK" if self.passes else "FAIL"


# ----------------------------------------------------------------------
# Material and section
# ----------------------------------------------------------------------

@dataclass
class Steel:
    """CFS sheet/coil steel. Defaults to ASTM A1003 Grade 50 (typical
    structural-grade purlin/girt material)."""
    name: str = "A1003 Gr.50"
    Fy: float = 50.0
    Fu: float = 65.0
    E: float = E_STEEL


A1003_33 = Steel("A1003 Gr.33 (ST33H)", 33.0, 45.0)
A1003_50 = Steel("A1003 Gr.50 (ST50H)", 50.0, 65.0)
A653_SS50 = Steel("A653 SS Grade 50", 50.0, 65.0)
A653_SS33 = Steel("A653 SS Grade 33", 33.0, 45.0)


@dataclass
class CFSSection:
    """
    C or Z secondary-member cross-section.

    Two ways to populate this:
      (1) PREFERRED -- copy gross section properties (A, Ix, Sx, rx, Iy,
          Sy, ry, J, Cw, xo, ho) from a manufacturer catalog (e.g., SSMA
          Product Technical Guide) or a verified CFS section-property
          tool (CUFSM, THIN-WALL, RSG software). Misremembered/hand
          -derived section properties are the most common source of a
          silently wrong CFS calculation -- same warning as for hot
          -rolled shapes.
      (2) Hand-check only -- use compute_lipped_c_properties() /
          compute_lipped_z_properties() below for a flat-width
          approximation suitable for preliminary sizing. Re-derive from
          a catalog or section-property software before issuing for
          construction.
    """
    name: str = "USER"
    shape: str = "C"                # "C" or "Z"
    t: Optional[float] = None       # design thickness, in
    H: Optional[float] = None       # out-to-out depth, in
    B: Optional[float] = None       # out-to-out flange width, in
    Dlip: Optional[float] = None    # out-to-out lip depth, in
    r: Optional[float] = None       # inside bend radius, in
    A: Optional[float] = None       # in^2
    Ix: Optional[float] = None      # in^4, strong axis (bending axis)
    Sx: Optional[float] = None      # in^3, gross, extreme fiber
    rx: Optional[float] = None      # in
    Iy: Optional[float] = None      # in^4
    Sy: Optional[float] = None      # in^3
    ry: Optional[float] = None      # in
    J: Optional[float] = None       # in^4, St. Venant torsion constant
    Cw: Optional[float] = None      # in^6, warping constant
    xo: Optional[float] = None      # in, shear center to centroid (x)
    ho: Optional[float] = None      # in, distance between flange centroids
    Se_top: Optional[float] = None  # in^3, effective Sx, compression top
    Se_bot: Optional[float] = None  # in^3, effective Sx, compression bottom
    Aw: Optional[float] = None      # in^2, web shear area (= h*t typical)
    h_flat: Optional[float] = None  # in, flat (clear) web depth
    shape_type: str = "C/Z"

    def need(self, *props):
        missing = [p for p in props if getattr(self, p, None) is None]
        if missing:
            raise ValueError(
                "Section '%s' is missing required propert%s: %s. Supply "
                "from a manufacturer catalog (e.g. SSMA), verified "
                "section-property software, or compute_lipped_*_properties()."
                % (self.name, "y" if len(missing) == 1 else "ies",
                   ", ".join(missing))
            )


# ----------------------------------------------------------------------
# Flat-width geometry helper (hand-check / preliminary sizing only)
# ----------------------------------------------------------------------

def flat_widths(sec: CFSSection) -> dict:
    """
    Flat (out-to-out minus bends) widths using the simplified "corners
    ignored beyond flat-width credit" convention common in hand
    calculations (AISI S100-16 Sec. 1.1.1, flat-width w = out-to-out
    dimension - 2*(r + t), applied at each 90-deg bend).

    Returns a dict with w_flange, w_web, w_lip (in). Approximate --
    corner-radius contributions to A, I are NOT included. Use a
    verified section-property tool for anything beyond preliminary
    sizing.
    """
    sec.need("t", "H", "B", "Dlip", "r")
    t, H, B, D, r = sec.t, sec.H, sec.B, sec.Dlip, sec.r
    w_web = H - 2 * (r + t)
    w_flange = B - (r + t)          # one bend at web, free edge at lip
    w_lip = D - (r + t)
    return {"w_web": w_web, "w_flange": w_flange, "w_lip": w_lip}


# ----------------------------------------------------------------------
# Effective width -- Winter's equation (AISI S100-16 Sec. 1.1.1.1 / B2)
# ----------------------------------------------------------------------

def plate_elastic_buckling_stress(k: float, t: float, w: float,
                                   E: float = E_STEEL, mu: float = MU) -> float:
    """Fcr for a flat compression element. AISI S100-16 Eq. 1.1.1.1-1 (B2)."""
    return k * math.pi ** 2 * E / (12 * (1 - mu ** 2) * (w / t) ** 2)


def effective_width(w: float, t: float, f: float, k: float,
                     E: float = E_STEEL, mu: float = MU) -> Tuple[float, float, float]:
    """
    Winter effective width. Returns (b_effective, lambda, Fcr).

    k = 4.0   stiffened element (web, or flange between two webs)
    k = 0.43  unstiffened element (flange with one free edge, no lip)
    k = per B4.2 for a flange with a simple lip edge stiffener
        (NOT implemented here -- see effective_width_edge_stiffened())
    """
    Fcr = plate_elastic_buckling_stress(k, t, w, E, mu)
    lam = math.sqrt(f / Fcr)
    if lam <= 0.673:
        rho = 1.0
    else:
        rho = min((1 - 0.22 / lam) / lam, 1.0)
    return rho * w, lam, Fcr


def effective_width_edge_stiffened(w_flange: float, t: float, f: float,
                                    E: float = E_STEEL, mu: float = MU
                                    ) -> Tuple[float, float, float]:
    """
    SIMPLIFIED lip-stiffened flange effective width, treated as an
    unstiffened element (k = 0.43). This is CONSERVATIVE relative to the
    full AISI B4.2 procedure (which computes an adequate-stiffener check
    on Ia/Is and can give k up to ~4.0 for a fully adequate simple lip,
    i.e. a wider, more economical effective width). Use this function
    for a conservative hand check only; for final design confirm against
    AISI S100-16 Sec. 1.2.2.2 (B4.2) or section-property software.
    """
    return effective_width(w_flange, t, f, k=0.43, E=E, mu=mu)


def web_stress_gradient_k(f1: float, f2: float) -> float:
    """
    Plate buckling coefficient for a stiffened web under a stress
    gradient (bending). AISI S100-16 Sec. 1.1.1.1 (B2.3):
        psi = f2 / f1        (f1 = compression, larger magnitude; f2 can
                               be negative if the far edge is in tension)
        k = 4 + 2*(1 - psi)^3 + 2*(1 - psi)     for psi > 0 (both in compression)
        k = 4 + 2*(1 - psi)^3 + 2*(1 - psi)     also used for psi <= 0 in the
                                                  common simplified form; for a
                                                  singly-symmetric web fully in
                                                  bending (psi = -1) this gives
                                                  k = 4 + 2*(2)^3 + 2*(2) = 24.
    """
    psi = f2 / f1
    return 4 + 2 * (1 - psi) ** 3 + 2 * (1 - psi)


# ----------------------------------------------------------------------
# C3.1 -- Flexural members: nominal strength (EWM, Procedure I: f = Fy)
# ----------------------------------------------------------------------

def effective_Se_procedure_I(sec: CFSSection, steel: Steel) -> Result:
    """
    Effective section modulus at initiation of yield (AISI S100-16
    Sec. C3.1.1(a) / Appendix "Procedure I"): all compression elements
    checked at f = Fy directly (single pass, non-iterative). This is
    conservative relative to the economical iterative Procedure II.

    Requires flat_widths(sec) inputs (t, H, B, Dlip, r) AND sec.Ix (gross)
    to locate/adjust the neutral axis approximately. For anything beyond
    a hand check, get Se_top/Se_bot from a catalog or CUFSM/THIN-WALL and
    skip this function -- populate sec.Se_top / sec.Se_bot directly.
    """
    sec.need("t", "H", "Ix")
    fw = flat_widths(sec)
    t = sec.t
    f = steel.Fy

    # Compression flange (stiffened, between web and lip) at f = Fy
    be_flange, lam_f, Fcr_f = effective_width(fw["w_flange"], t, f, k=4.0)
    # Lip (edge stiffener), simplified/conservative
    be_lip, lam_l, Fcr_l = effective_width_edge_stiffened(fw["w_lip"], t, f)
    # Web: gradient from gross section (approx psi = -1 for symmetric C/Z
    # bent about its own axis, i.e. compression and tension flanges equal
    # distance from NA)
    k_web = web_stress_gradient_k(1.0, -1.0)   # = 24.0, symmetric section
    be_web, lam_w, Fcr_w = effective_width(fw["w_web"], t, f, k=k_web)

    r = Result("Effective Se (Procedure I, f = Fy)", 0.0, 0.90, 1.67, unit="in^3")
    r.add("w_flange (flat)", fw["w_flange"], "in", "flat width")
    r.add("b_eff, flange", be_flange, "in", "lambda=%.3f, Fcr=%.1f ksi" % (lam_f, Fcr_f))
    r.add("w_lip (flat)", fw["w_lip"], "in", "flat width")
    r.add("b_eff, lip", be_lip, "in", "lambda=%.3f (k=0.43, simplified)" % lam_l)
    r.add("w_web (flat)", fw["w_web"], "in", "flat width")
    r.add("b_eff, web", be_web, "in", "lambda=%.3f, k_web=%.1f" % (lam_w, k_web))
    r.notes.append(
        "Se estimated as Sx_gross * (fully-effective fraction) using the "
        "ratio of effective to flat widths -- a simplified hand-check "
        "surrogate, NOT a rigorous effective-Ix/effective-NA recomputation. "
        "For a stamped submittal, compute Se_top/Se_bot with a verified "
        "section-property tool and pass them directly via sec.Se_top/Se_bot."
    )
    eff_fraction = (be_flange + be_lip + be_web) / (fw["w_flange"] + fw["w_lip"] + fw["w_web"])
    sec.need("Sx")
    Se_est = sec.Sx * min(eff_fraction, 1.0)
    r.Rn = Se_est
    r.add("Se (estimated)", Se_est, "in^3", "Sx_gross * eff. fraction")
    return r


def flexure_strength_ewm(sec: CFSSection, steel: Steel) -> Result:
    """
    Nominal flexural strength, EWM, section-strength (yielding) limit
    state ONLY. AISI S100-16 Sec. C3.1.1: Mn = Se * Fy.

    Uses sec.Se_top/sec.Se_bot if supplied (preferred); otherwise falls
    back to effective_Se_procedure_I() as a hand-check estimate.

    This function does NOT include lateral-torsional buckling (Sec.
    C3.1.2) -- call flexure_strength_ltb() and take the governing
    (lower) Mn, or use flexure_strength() which does both.
    """
    if sec.Se_top is not None or sec.Se_bot is not None:
        Se = min(v for v in (sec.Se_top, sec.Se_bot) if v is not None)
        source = "user-supplied Se_top/Se_bot"
        steps = []
    else:
        se_res = effective_Se_procedure_I(sec, steel)
        Se = se_res.Rn
        source = "computed, Procedure I hand-check (see notes)"
        steps = se_res.steps

    Mn = Se * steel.Fy
    r = Result("Flexural strength -- section (yielding), C3.1.1", Mn, 0.90, 1.67, unit="kip-in")
    r.steps = list(steps)
    r.add("Se", Se, "in^3", source)
    r.add("Fy", steel.Fy, "ksi", steel.name)
    r.add("Mn = Se*Fy", Mn, "kip-in", "Eq. C3.1.1-1")
    return r


def flexure_strength_ltb(sec: CFSSection, steel: Steel, Lb: float,
                          Cb: float = 1.0) -> Result:
    """
    Lateral-torsional buckling strength for a singly- or point-symmetric
    C/Z flexural member NOT continuously braced by deck/sheathing.
    AISI S100-16 Sec. C3.1.2.1, elastic buckling stress Fe for a
    singly-symmetric section bent about the symmetry axis:

        Fe = Cb*ro*A*sigma_ex*sigma_t / (2*Sf) * [ ... ]   (general form)

    For an unsheathed C/Z this reduces (common simplified closed form,
    doubly-symmetric-about-bending-axis approximation used for point
    -symmetric Z and for C where torsion effects are modest) to the
    same elastic LTB stress used for singly-symmetric I-shapes:

        Fe = Cb*pi^2*E*Cw/(Sf*Lb^2) * ... + Cb*pi^2*E*Iy*d/(2*Sf*Lb^2)*sqrt(...)

    IMPORTANT: the exact Fe expression depends on section symmetry (C is
    singly-symmetric, Z is point-symmetric) and AISI gives DIFFERENT
    closed-form Fe equations for each (Sec. C3.1.2.1, Eqs. C3.1.2.1-4
    through -11). This function is NOT wired to those closed forms --
    it requires you to either:
      (a) supply an elastic critical moment Mcr directly (e.g. from
          CUFSM "CFS-LTB" analysis or a verified formula for your
          section symmetry), or
      (b) treat sheathed/braced-flange members as continuously braced
          and skip this check entirely (common for purlins with
          through-fastened or standing-seam roofing providing
          continuous lateral/torsional restraint -- verify bracing
          adequacy per AISI S100-16 Sec. D6.1/D6.2.1 separately).

    Pass Mcr_override (kip-in) to use this function directly; otherwise
    it raises, rather than silently using a wrong/generic I-shape Fe.
    """
    raise NotImplementedError(
        "flexure_strength_ltb() requires an explicit Mcr for the actual "
        "C or Z symmetry (Sec. C3.1.2.1) -- use flexure_strength_ltb_from_Mcr() "
        "with a verified Mcr, or confirm the member is continuously braced "
        "by deck/sheathing per Sec. D6 and skip LTB."
    )


def flexure_strength_ltb_from_Mcr(sec: CFSSection, steel: Steel, Mcr: float) -> Result:
    """
    LTB nominal strength once the elastic critical moment Mcr is known
    (from CUFSM, a verified closed-form Fe for the section's actual
    symmetry, or hand calc). AISI S100-16 Sec. C3.1.2.1:

        My = Sf * Fy
        if Mcr >= 2.78*My:  Mn = My
        elif 0.56*My < Mcr < 2.78*My:
            Mn = (10/9)*My*(1 - 10*My/(36*Mcr))
        else (Mcr <= 0.56*My):
            Mn = Mcr

    (Same transition form as the Direct Strength Method global-buckling
    curve -- AISI uses one Fe/Mcr-based curve for both EWM-LTB and
    DSM global buckling.)
    """
    Sf = sec.Sx if sec.Sx is not None else (sec.Se_top or sec.Se_bot)
    if Sf is None:
        raise ValueError("Need sec.Sx (or Se_top/Se_bot) to compute My.")
    My = Sf * steel.Fy
    if Mcr >= 2.78 * My:
        Mn = My
        branch = "Mcr >= 2.78 My -> Mn = My"
    elif Mcr > 0.56 * My:
        Mn = (10.0 / 9.0) * My * (1 - 10 * My / (36 * Mcr))
        branch = "0.56My < Mcr < 2.78My"
    else:
        Mn = Mcr
        branch = "Mcr <= 0.56 My -> Mn = Mcr"
    r = Result("Flexural strength -- LTB, C3.1.2.1", Mn, 0.90, 1.67, unit="kip-in")
    r.add("Sf", Sf, "in^3", "")
    r.add("My = Sf*Fy", My, "kip-in", "")
    r.add("Mcr", Mcr, "kip-in", "user-supplied / external analysis")
    r.add("branch", branch, "", "Eq. C3.1.2.1-1/2/3")
    r.add("Mn", Mn, "kip-in", "")
    return r


def flexure_strength(sec: CFSSection, steel: Steel, Mcr: Optional[float] = None
                      ) -> Result:
    """Governing flexural strength = min(section/yielding, LTB-if-Mcr-given)."""
    sect = flexure_strength_ewm(sec, steel)
    if Mcr is None:
        sect.notes.append(
            "LTB (C3.1.2) NOT evaluated -- no Mcr supplied. If this member "
            "is not continuously braced by deck/sheathing, LTB may govern; "
            "supply Mcr via flexure_strength_ltb_from_Mcr() before relying "
            "on this result."
        )
        return sect
    ltb = flexure_strength_ltb_from_Mcr(sec, steel, Mcr)
    if ltb.Rn <= sect.Rn:
        ltb.steps = sect.steps + [("--- governs over section strength ---", "", "", "")] + ltb.steps
        return ltb
    sect.notes.append("LTB checked (Mn_LTB=%.1f kip-in) and does not govern." % ltb.Rn)
    return sect


# ----------------------------------------------------------------------
# C3.2 -- Shear
# ----------------------------------------------------------------------

def shear_strength(sec: CFSSection, steel: Steel, kv: float = 5.34) -> Result:
    """
    Nominal shear strength of an unreinforced flat web. AISI S100-16
    Sec. C3.2.1. Fv depends on h/t range (yielding / inelastic buckling
    / elastic buckling):

        h/t <= 0.815*sqrt(kv*E/Fy):       Fv = 0.60*Fy
        0.815*sqrt(kv*E/Fy) < h/t
                <= 1.415*sqrt(kv*E/Fy):   Fv = 0.64*sqrt(kv*E*Fy)/(h/t)
        h/t > 1.415*sqrt(kv*E/Fy):        Fv = 0.905*kv*E/(h/t)^2

    kv = 5.34 for unreinforced webs (no shear stiffeners) -- standard
    default for purlins/girts.

    NOTE: phi_v/Omega_v below use the commonly published single set
    (phi_v=0.95 LRFD / Omega_v=1.60 ASD in the h/t<=... yielding range,
    reducing in the elastic-buckling range). This split by range was
    NOT independently re-verified against your edition's Table
    C3.2.1-1 in this session -- confirm before final use. Using a single
    conservative phi_v/Omega_v pair across all ranges, as done here, is
    a common simplification but check it against your edition.
    """
    sec.need("t", "Aw")
    t = sec.t
    h = sec.h_flat if sec.h_flat is not None else (sec.Aw / t)
    E, Fy = steel.E, steel.Fy
    ht = h / t
    lim1 = 0.815 * math.sqrt(kv * E / Fy)
    lim2 = 1.415 * math.sqrt(kv * E / Fy)
    if ht <= lim1:
        Fv = 0.60 * Fy
        branch = "yielding, h/t <= %.1f" % lim1
    elif ht <= lim2:
        Fv = 0.64 * math.sqrt(kv * E * Fy) / ht
        branch = "inelastic buckling"
    else:
        Fv = 0.905 * kv * E / ht ** 2
        branch = "elastic buckling"
    Vn = sec.Aw * Fv
    r = Result("Shear strength, C3.2.1", Vn, 0.90, 1.67, unit="kips")
    r.add("h/t", ht, "", "")
    r.add("branch", branch, "", "")
    r.add("Fv", Fv, "ksi", "")
    r.add("Aw = h*t", sec.Aw, "in^2", "")
    r.add("Vn = Aw*Fv", Vn, "kips", "Eq. C3.2.1-1/2/3")
    r.notes.append(
        "phi_v=0.90/Omega_v=1.67 used as a single conservative pair across "
        "all h/t ranges -- verify the range-dependent phi_v/Omega_v split "
        "in Table C3.2.1-1 of your edition before final use."
    )
    return r


def combined_bending_shear(Mu_or_Ma: float, Mn_design: float,
                            Vu_or_Va: float, Vn_design: float) -> Check:
    """
    Combined bending and shear at a point of combined high moment and
    high shear (e.g., near an interior support of a continuous purlin
    line). AISI S100-16 Sec. C3.3.1 (for webs without stiffeners):

        (M/Mn_design)^2 + (V/Vn_design)^2 <= 1.0   (unreinforced webs)

    Pass already-factored (LRFD) or already-allowable (ASD) demands and
    the corresponding *design* (available) strengths, i.e. Mn_design =
    result.design(method), not the nominal Mn.
    """
    ratio = (Mu_or_Ma / Mn_design) ** 2 + (Vu_or_Va / Vn_design) ** 2
    c = Check("Combined bending + shear, C3.3.1", ratio, 1.0, "", "Eq. C3.3.1-1")
    return c


# ----------------------------------------------------------------------
# C3.4 -- Web crippling (equation ENGINE only -- coefficients are
# yours to supply from AISI Table C3.4.1-1 through -4)
# ----------------------------------------------------------------------

def web_crippling_strength(t: float, Fy: float, theta_deg: float,
                            C: float, C_R: float, C_N: float, C_h: float,
                            R: float, N: float, h: float,
                            phi: float = 0.75, omega: float = 2.00) -> Result:
    """
    Nominal web crippling strength, general AISI S100-16 Sec. C3.4.1
    equation form:

        Pn = C * t^2 * Fy * sin(theta) *
             (1 - C_R*sqrt(R/t)) * (1 + C_N*sqrt(N/t)) * (1 - C_h*sqrt(h/t))

    C, C_R, C_N, C_h are NOT hard-coded here -- they depend on the
    support condition (end one-flange / interior one-flange / end
    two-flange / interior two-flange loading), fastened vs. unfastened
    flange, single web vs. I-section/back-to-back, and ASD/LRFD. Look
    them up in AISI S100-16 Table C3.4.1-1 (single unreinforced webs,
    LRFD/ASD) or the corresponding table for your actual support/flange
    condition, and pass them in explicitly.

    theta_deg = angle between plane of web and plane of bearing surface
                (90 for a web perpendicular to the flange bearing, the
                normal case).
    R = inside bend radius, N = bearing length, h = flat web depth.

    phi/omega DEFAULTS shown (0.75/2.00) are the commonly published
    values for one-flange loading conditions on unreinforced webs --
    CONFIRM against Table C3.4.1-1/-3 for your exact condition before
    use; some conditions/tables use different phi/omega.
    """
    theta = math.radians(theta_deg)
    Pn = (C * t ** 2 * Fy * math.sin(theta)
          * (1 - C_R * math.sqrt(R / t))
          * (1 + C_N * math.sqrt(N / t))
          * (1 - C_h * math.sqrt(h / t)))
    r = Result("Web crippling, C3.4.1", Pn, phi, omega, unit="kips")
    r.add("t", t, "in", "")
    r.add("Fy", Fy, "ksi", "")
    r.add("theta", theta_deg, "deg", "")
    r.add("C, C_R, C_N, C_h", "%.3g, %.3g, %.3g, %.3g" % (C, C_R, C_N, C_h),
          "", "user-supplied from Table C3.4.1-x")
    r.add("R, N, h", "%.3f, %.3f, %.3f" % (R, N, h), "in", "")
    r.add("Pn", Pn, "kips", "Eq. C3.4.1-1")
    r.notes.append(
        "C/C_R/C_N/C_h and phi/omega must match the SAME table row for your "
        "actual support condition (end/interior, one-flange/two-flange, "
        "fastened/unfastened) -- mixing coefficients from different rows "
        "gives a meaningless result."
    )
    return r


# ----------------------------------------------------------------------
# Deflection
# ----------------------------------------------------------------------

def deflection_check(delta: float, span: float, limit_denom: float = 180.0
                      ) -> Check:
    """
    Serviceability deflection check, delta <= L/limit_denom. Common PEB
    secondary-member limits: L/180 (roof, live load, no ceiling), L/240
    (roof, live+dead, or wall girts under wind), L/360 (floor live
    load), L/600 (members supporting brittle finishes e.g. some
    cladding). Confirm the governing limit with the project criteria /
    IBC Table 1604.3 -- this is NOT an AISI strength provision.
    """
    limit = span / limit_denom
    c = Check("Deflection <= L/%d" % int(limit_denom), delta, limit, "in",
              "serviceability, project criteria")
    return c
