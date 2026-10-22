"""
aisc360.py -- AISC 360-16 member strength functions (LRFD and ASD).

Pure standard library. No numpy, no scipy. Runs inside FreeCAD's bundled
Python interpreter unmodified.

UNITS: US customary throughout.
    force = kips, length = inches, stress = ksi
    Member lengths are passed in INCHES. Convert feet at the call site.

Every public function returns a Result object carrying:
    .Rn      nominal strength
    .phi     LRFD resistance factor
    .omega   ASD safety factor
    .design(method) -> available strength (phi*Rn or Rn/omega)
    .steps   list of (label, value, unit, reference) tuples for the calc sheet

The .steps list is what makes the calculation sheet auditable. Never
discard it.

Reference tags point to AISC 360-16 equation numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

E_STEEL = 29000.0  # ksi, modulus of elasticity, AISC 360-16 Sec. B4
G_STEEL = 11200.0  # ksi, shear modulus


# ----------------------------------------------------------------------
# Result container
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

    def design(self, method: str = "LRFD") -> float:
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
    """A single limit-state check: demand vs capacity."""
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
# Section and material
# ----------------------------------------------------------------------

@dataclass
class Steel:
    """Material. Defaults to A992, the standard grade for W-shapes."""
    name: str = "A992"
    Fy: float = 50.0
    Fu: float = 65.0
    E: float = E_STEEL


A36 = Steel("A36", 36.0, 58.0)
A992 = Steel("A992", 50.0, 65.0)
A572_50 = Steel("A572 Gr.50", 50.0, 65.0)
A500_B_ROUND = Steel("A500 Gr.B round HSS", 42.0, 58.0)
A500_B_RECT = Steel("A500 Gr.B rect HSS", 46.0, 58.0)


@dataclass
class Section:
    """
    Cross-section properties. Populate from the AISC Manual Part 1, or
    from FreeCAD geometry, or from shapes.py.

    Only the properties a given check needs must be supplied; the rest
    may stay None. Functions raise a clear error if a needed property
    is missing.
    """
    name: str = "USER"
    A: Optional[float] = None       # gross area, in^2
    d: Optional[float] = None       # overall depth, in
    tw: Optional[float] = None      # web thickness, in
    bf: Optional[float] = None      # flange width, in
    tf: Optional[float] = None      # flange thickness, in
    Ix: Optional[float] = None
    Zx: Optional[float] = None
    Sx: Optional[float] = None
    rx: Optional[float] = None
    Iy: Optional[float] = None
    Zy: Optional[float] = None
    Sy: Optional[float] = None
    ry: Optional[float] = None
    J: Optional[float] = None
    Cw: Optional[float] = None
    rts: Optional[float] = None
    ho: Optional[float] = None      # distance between flange centroids
    bf_2tf: Optional[float] = None  # flange slenderness lambda_f
    h_tw: Optional[float] = None    # web slenderness lambda_w
    x_bar: Optional[float] = None   # for shear lag U on angles/tees
    shape_type: str = "W"           # W, HSS, L, WT, PL, ...

    def need(self, *props):
        missing = [p for p in props if getattr(self, p, None) is None]
        if missing:
            raise ValueError(
                "Section '%s' is missing required propert%s: %s. "
                "Supply them from the AISC Manual Part 1 or from FreeCAD geometry."
                % (self.name, "y" if len(missing) == 1 else "ies", ", ".join(missing))
            )


# ----------------------------------------------------------------------
# ASCE 7 load combinations  (Lecture 17)
# ----------------------------------------------------------------------

def lrfd_combinations(D=0.0, L=0.0, Lr=0.0, S=0.0, R=0.0, W=0.0, Ev=0.0):
    """
    ASCE 7 strength design (LRFD) combinations 1-7 for the given
    load effects. Returns list of (label, value), sorted worst first.

    Sign convention: pass W and E with the sign that produces the
    effect you are checking. Run twice (+/-) for reversible loads.
    """
    roof = max(Lr, S, R)
    combos = [
        ("1.4D", 1.4 * D),
        ("1.2D + 1.6L + 0.5(Lr/S/R)", 1.2 * D + 1.6 * L + 0.5 * roof),
        ("1.2D + 1.6(Lr/S/R) + 0.5L", 1.2 * D + 1.6 * roof + 0.5 * L),
        ("1.2D + 1.6(Lr/S/R) + 0.5W", 1.2 * D + 1.6 * roof + 0.5 * W),
        ("1.2D + 1.0W + L + 0.5(Lr/S/R)", 1.2 * D + 1.0 * W + L + 0.5 * roof),
        ("1.2D + Ev + L + 0.2S", 1.2 * D + Ev + L + 0.2 * S),
        ("0.9D + 1.0W", 0.9 * D + 1.0 * W),
        ("0.9D + Ev", 0.9 * D + Ev),
    ]
    return sorted(combos, key=lambda c: -abs(c[1]))


def asd_combinations(D=0.0, L=0.0, Lr=0.0, S=0.0, R=0.0, W=0.0, Ev=0.0):
    """ASCE 7 allowable stress design combinations."""
    roof = max(Lr, S, R)
    combos = [
        ("D", D),
        ("D + L", D + L),
        ("D + (Lr/S/R)", D + roof),
        ("D + 0.75L + 0.75(Lr/S/R)", D + 0.75 * L + 0.75 * roof),
        ("D + 0.6W", D + 0.6 * W),
        ("D + 0.75L + 0.75(0.6W) + 0.75(Lr/S/R)",
         D + 0.75 * L + 0.45 * W + 0.75 * roof),
        ("0.6D + 0.6W", 0.6 * D + 0.6 * W),
        ("0.6D + 0.7Ev", 0.6 * D + 0.7 * Ev),
    ]
    return sorted(combos, key=lambda c: -abs(c[1]))


def governing_combination(method="LRFD", **loads):
    """Return (label, value) of the controlling combination."""
    fn = lrfd_combinations if method.upper() == "LRFD" else asd_combinations
    return fn(**loads)[0]


# ----------------------------------------------------------------------
# CHAPTER D -- TENSION MEMBERS  (Lectures 18-22)
# ----------------------------------------------------------------------

def tension_yielding(Ag: float, steel: Steel) -> Result:
    """Tensile yielding on the gross section. AISC D2(a), Eq. D2-1."""
    Rn = steel.Fy * Ag
    r = Result("Tensile yielding (gross section)", Rn, 0.90, 1.67)
    r.add("Fy", steel.Fy, "ksi", steel.name)
    r.add("Ag", Ag, "in^2", "")
    r.add("Pn = Fy*Ag", Rn, "kips", "Eq. D2-1")
    r.add("phi_t = 0.90 / Omega_t = 1.67", "", "", "Sec. D2")
    return r


def hole_diameter(d_bolt: float, punched: bool = True) -> float:
    """
    Effective hole diameter for NET AREA.

    Standard hole = d_bolt + 1/16 in.  For net-area computation AISC
    adds a further 1/16 in for damage during punching, so the value
    used in An is d_bolt + 1/8 in. This is the value Dr. Muhammad uses
    throughout the tension member lectures.
    """
    return d_bolt + (0.125 if punched else 0.0625)


def net_area(Ag: float, t: float, n_holes: int, d_bolt: float,
             stagger: Optional[List[Tuple[float, float]]] = None,
             punched: bool = True) -> Result:
    """
    Net area including staggered fastener correction (Lecture 21).

        An = Ag - sum(dh * t) + sum(s^2/(4g) * t)

    stagger: list of (s, g) pairs, s = longitudinal pitch, g = transverse
    gage, one pair per diagonal segment on the failure path.
    """
    dh = hole_diameter(d_bolt, punched)
    deduction = n_holes * dh * t
    addition = 0.0
    stagger = stagger or []
    for s, g in stagger:
        addition += (s * s) / (4.0 * g) * t
    An = Ag - deduction + addition

    r = Result("Net area", An, 1.0, 1.0, unit="in^2")
    r.add("d_bolt", d_bolt, "in", "")
    r.add("d_hole (net area)", dh, "in", "Sec. B4.3b")
    r.add("holes on path", n_holes, "", "")
    r.add("deduction = n*dh*t", deduction, "in^2", "")
    for i, (s, g) in enumerate(stagger, 1):
        r.add("stagger %d: s^2/4g*t (s=%.3f, g=%.3f)" % (i, s, g),
              (s * s) / (4.0 * g) * t, "in^2", "Sec. B4.3b")
    r.add("An", An, "in^2", "")
    if An > Ag:
        r.notes.append("An exceeds Ag -- stagger term too large; recheck the "
                       "failure path. An is capped at Ag.")
        r.Rn = Ag
    return r


def shear_lag_factor(case: str = "manual", U: Optional[float] = None,
                     x_bar: Optional[float] = None, L: Optional[float] = None,
                     **kw) -> Result:
    """
    Shear lag factor U, AISC Table D3.1 (Lecture 20).

    case:
      'all_elements'  -- tension on all cross-section elements, U = 1.0
      'general'       -- U = 1 - x_bar/L  (Case 2, the general case)
      'manual'        -- caller supplies U directly

    For bolted connections L is centre-to-centre of the extreme
    fasteners in the line of force. For welded, L is the length of the
    LONGITUDINAL weld only (transverse weld does not count toward L).
    """
    r = Result("Shear lag factor U", 0.0, 1.0, 1.0, unit="")
    if case == "all_elements":
        Uv = 1.0
        r.add("U", 1.0, "", "Table D3.1 Case 1")
    elif case == "general":
        if x_bar is None or L is None:
            raise ValueError("case='general' needs x_bar and L")
        Uv = 1.0 - x_bar / L
        r.add("x_bar (connection eccentricity)", x_bar, "in", "")
        r.add("L (connection length)", L, "in", "")
        r.add("U = 1 - x_bar/L", Uv, "", "Table D3.1 Case 2")
    else:
        if U is None:
            raise ValueError("case='manual' needs U")
        Uv = U
        r.add("U (user supplied)", U, "", "Table D3.1")
    r.Rn = Uv
    return r


def tension_rupture(An: float, U: float, steel: Steel) -> Result:
    """Tensile rupture on the net section. AISC D2(b), Eq. D2-2."""
    Ae = An * U
    Rn = steel.Fu * Ae
    r = Result("Tensile rupture (net section)", Rn, 0.75, 2.00)
    r.add("Fu", steel.Fu, "ksi", steel.name)
    r.add("An", An, "in^2", "")
    r.add("U", U, "", "Table D3.1")
    r.add("Ae = An*U", Ae, "in^2", "Eq. D3-1")
    r.add("Pn = Fu*Ae", Rn, "kips", "Eq. D2-2")
    r.add("phi_t = 0.75 / Omega_t = 2.00", "", "", "Sec. D2")
    return r


def block_shear(Agv: float, Anv: float, Ant: float, steel: Steel,
                Ubs: float = 1.0) -> Result:
    """
    Block shear rupture. AISC J4.3, Eq. J4-5 (Lecture 21).

        Rn = 0.60*Fu*Anv + Ubs*Fu*Ant  <=  0.60*Fy*Agv + Ubs*Fu*Ant

    Ubs = 1.0 uniform tension stress (most cases)
    Ubs = 0.5 non-uniform tension stress (e.g. coped beam, two rows)
    """
    rupture_term = 0.60 * steel.Fu * Anv + Ubs * steel.Fu * Ant
    yield_term = 0.60 * steel.Fy * Agv + Ubs * steel.Fu * Ant
    Rn = min(rupture_term, yield_term)

    r = Result("Block shear rupture", Rn, 0.75, 2.00)
    r.add("Agv (gross area in shear)", Agv, "in^2", "")
    r.add("Anv (net area in shear)", Anv, "in^2", "")
    r.add("Ant (net area in tension)", Ant, "in^2", "")
    r.add("Ubs", Ubs, "", "Sec. J4.3")
    r.add("0.60*Fu*Anv + Ubs*Fu*Ant", rupture_term, "kips", "Eq. J4-5 (shear rupture)")
    r.add("0.60*Fy*Agv + Ubs*Fu*Ant", yield_term, "kips", "Eq. J4-5 (shear yielding)")
    r.add("Rn = lesser", Rn, "kips", "Eq. J4-5")
    r.notes.append("Governed by " + ("shear rupture" if rupture_term <= yield_term
                                     else "shear yielding") + " on the block.")
    return r


def tension_slenderness(L: float, r_min: float) -> Check:
    """
    L/r preference for tension members, AISC D1 User Note: L/r <= 300.
    This is a serviceability preference, NOT a strength requirement.
    Rods and hangers in tension are exempt.
    """
    return Check("Tension slenderness L/r <= 300 (preferred)",
                 L / r_min, 300.0, unit="", reference="Sec. D1 User Note")


# ----------------------------------------------------------------------
# CHAPTER E -- COMPRESSION MEMBERS  (Lectures 23, 24, 2, 3, 4)
# ----------------------------------------------------------------------

def euler_stress(KL_over_r: float, E: float = E_STEEL) -> float:
    """Elastic (Euler) buckling stress Fe. AISC Eq. E3-4."""
    if KL_over_r <= 0:
        raise ValueError("KL/r must be positive")
    return (math.pi ** 2) * E / (KL_over_r ** 2)


def critical_stress(KL_over_r: float, steel: Steel) -> Result:
    """
    Flexural buckling critical stress Fcr, AISC Sec. E3.

    Transition at KL/r = 4.71*sqrt(E/Fy), equivalently Fy/Fe = 2.25.
      inelastic:  Fcr = 0.658^(Fy/Fe) * Fy      (Eq. E3-2)
      elastic:    Fcr = 0.877 * Fe              (Eq. E3-3)
    """
    Fy, E = steel.Fy, steel.E
    limit = 4.71 * math.sqrt(E / Fy)
    Fe = euler_stress(KL_over_r, E)
    inelastic = KL_over_r <= limit

    if inelastic:
        Fcr = (0.658 ** (Fy / Fe)) * Fy
        eq = "Eq. E3-2 (inelastic)"
    else:
        Fcr = 0.877 * Fe
        eq = "Eq. E3-3 (elastic)"

    r = Result("Critical stress Fcr", Fcr, 1.0, 1.0, unit="ksi")
    r.add("KL/r (governing)", KL_over_r, "", "")
    r.add("4.71*sqrt(E/Fy)", limit, "", "Sec. E3")
    r.add("Fe = pi^2*E/(KL/r)^2", Fe, "ksi", "Eq. E3-4")
    r.add("Fy/Fe", Fy / Fe, "", "")
    r.add("Fcr", Fcr, "ksi", eq)
    r.notes.append(("INELASTIC buckling controls (KL/r <= limit)."
                    if inelastic else
                    "ELASTIC buckling controls (KL/r > limit)."))
    return r


def compression_strength(sec: Section, steel: Steel,
                         KxLx: float, KyLy: float,
                         Q: float = 1.0) -> Result:
    """
    Nominal compressive strength for flexural buckling, AISC Sec. E3.

    KxLx, KyLy in INCHES. The larger slenderness ratio governs.

    Q is the net reduction factor for slender elements (Sec. E7).
    Q = 1.0 for all non-slender shapes. Rolled W-shapes of A992 in the
    Manual are flagged with a footnote when slender -- if there is no
    footnote, local buckling need not be investigated (Lecture 24).

    Torsional / flexural-torsional buckling (Sec. E4) is NOT covered by
    this function. It must be checked separately for singly-symmetric
    shapes, tees, double angles, and cruciform sections (Lecture 4).
    """
    sec.need("A", "rx", "ry")
    slx = KxLx / sec.rx
    sly = KyLy / sec.ry
    KL_r = max(slx, sly)
    axis = "x" if slx >= sly else "y"

    fcr = critical_stress(KL_r, steel)
    Fcr = fcr.Rn * Q
    Pn = Fcr * sec.A

    r = Result("Compressive strength (flexural buckling)", Pn, 0.90, 1.67)
    r.add("KxLx/rx", slx, "", "")
    r.add("KyLy/ry", sly, "", "")
    r.add("governing axis", axis, "", "")
    r.steps.extend(fcr.steps)
    if Q != 1.0:
        r.add("Q (slender element reduction)", Q, "", "Sec. E7")
        r.add("Q*Fcr", Fcr, "ksi", "Sec. E7")
    r.add("Ag", sec.A, "in^2", "")
    r.add("Pn = Fcr*Ag", Pn, "kips", "Eq. E3-1")
    r.add("phi_c = 0.90 / Omega_c = 1.67", "", "", "Sec. E1")
    r.notes.extend(fcr.notes)
    if KL_r > 200:
        r.notes.append("KL/r = %.0f exceeds 200. AISC does not forbid this but "
                       "strongly advises against it (Sec. E2 User Note)." % KL_r)
    r.notes.append("Torsional/flexural-torsional buckling (Sec. E4) NOT checked here.")
    return r


def alignment_chart_G(Ic_over_Lc_sum: float, Ig_over_Lg_sum: float) -> float:
    """
    Stiffness ratio G for the alignment charts (Lectures 3, 4).

        G = sum(Ic/Lc) / sum(Ig/Lg)

    Conventional idealised values: G = 10 for a pinned base,
    G = 1.0 for a fixed base (theoretical values 
    infinity and 0 are not used in practice).
    """
    if Ig_over_Lg_sum == 0:
        return 10.0
    return Ic_over_Lc_sum / Ig_over_Lg_sum


def K_braced(GA: float, GB: float) -> float:
    """
    Effective length factor for a BRACED frame (sidesway prevented),
    0.5 <= K <= 1.0. Dumont-Hall / Duan approximation to the
    alignment chart, accurate to ~1%.
    """
    return (3.0 * GA * GB + 1.4 * (GA + GB) + 0.64) / \
           (3.0 * GA * GB + 2.0 * (GA + GB) + 1.28)


def K_unbraced(GA: float, GB: float) -> float:
    """
    Effective length factor for an UNBRACED frame (sidesway permitted),
    K >= 1.0. French approximation to the alignment chart.
    """
    num = 1.6 * GA * GB + 4.0 * (GA + GB) + 7.5
    den = GA + GB + 7.5
    return math.sqrt(num / den)


def inelastic_K_factor(Pu_over_Ag: float, steel: Steel) -> float:
    """
    Stiffness reduction factor tau_b for inelastic column K (Lecture 4).

    Multiply the elastic G values by tau_b before entering the chart.
    Returns 1.0 in the elastic range.
    """
    a = Pu_over_Ag / steel.Fy
    if a <= 0.5:
        return 1.0
    return 4.0 * a * (1.0 - a)


# ----------------------------------------------------------------------
# CHAPTER F -- FLEXURE  (Lectures 5-9)
# ----------------------------------------------------------------------

def classify_flange(lam_f: float, steel: Steel, rolled: bool = True) -> str:
    """Flange classification for flexure, AISC Table B4.1b Case 10."""
    E, Fy = steel.E, steel.Fy
    lp = 0.38 * math.sqrt(E / Fy)
    lr = 1.0 * math.sqrt(E / Fy)
    if lam_f <= lp:
        return "compact"
    if lam_f <= lr:
        return "noncompact"
    return "slender"


def classify_web(lam_w: float, steel: Steel) -> str:
    """Web classification for flexure, AISC Table B4.1b Case 15."""
    E, Fy = steel.E, steel.Fy
    lp = 3.76 * math.sqrt(E / Fy)
    lr = 5.70 * math.sqrt(E / Fy)
    if lam_w <= lp:
        return "compact"
    if lam_w <= lr:
        return "noncompact"
    return "slender"


def Cb_factor(Mmax: float, MA: float, MB: float, MC: float) -> Result:
    """
    Lateral-torsional buckling modification factor, AISC Eq. F1-1
    (Lecture 7).

        Cb = 12.5*Mmax / (2.5*Mmax + 3*MA + 4*MB + 3*MC)

    MA, MB, MC are absolute moments at 1/4, 1/2, 3/4 of the UNBRACED
    segment. Cb = 1.0 is always conservative. For a cantilever with a
    free end, Cb = 1.0.
    """
    Mmax, MA, MB, MC = abs(Mmax), abs(MA), abs(MB), abs(MC)
    den = 2.5 * Mmax + 3 * MA + 4 * MB + 3 * MC
    Cb = 12.5 * Mmax / den if den else 1.0
    r = Result("Cb", Cb, 1.0, 1.0, unit="")
    r.add("Mmax", Mmax, "kip-in", "")
    r.add("MA (quarter)", MA, "kip-in", "")
    r.add("MB (midspan)", MB, "kip-in", "")
    r.add("MC (three-quarter)", MC, "kip-in", "")
    r.add("Cb", Cb, "", "Eq. F1-1")
    return r


def Lp(sec: Section, steel: Steel) -> float:
    """Limiting unbraced length for yielding. AISC Eq. F2-5."""
    sec.need("ry")
    return 1.76 * sec.ry * math.sqrt(steel.E / steel.Fy)


def Lr(sec: Section, steel: Steel, c: float = 1.0) -> float:
    """
    Limiting unbraced length for inelastic LTB. AISC Eq. F2-6.
    c = 1.0 for doubly symmetric I-shapes.
    """
    sec.need("rts", "J", "Sx", "ho")
    Fl = 0.7 * steel.Fy
    Jc_Sxho = sec.J * c / (sec.Sx * sec.ho)
    inner = Jc_Sxho + math.sqrt(Jc_Sxho ** 2 + 6.76 * (Fl / steel.E) ** 2)
    return 1.95 * sec.rts * (steel.E / Fl) * math.sqrt(inner)


def flexure_strength(sec: Section, steel: Steel, Lb: float, Cb: float = 1.0,
                     c: float = 1.0) -> Result:
    """
    Nominal flexural strength about the major axis for a doubly
    symmetric compact I-shape, AISC Sec. F2 (Lectures 6, 7).

    Lb in INCHES. Returns Mn in KIP-INCHES.

    Three zones:
      Lb <= Lp        : Mn = Mp                       (yielding, Eq. F2-1)
      Lp < Lb <= Lr   : inelastic LTB                 (Eq. F2-2)
      Lb > Lr         : elastic LTB                   (Eq. F2-3, F2-4)

    If the flange is noncompact this function warns and the caller must
    additionally check Sec. F3 (compression flange local buckling).
    """
    sec.need("Zx", "Sx", "ry", "rts", "J", "ho")
    Mp = steel.Fy * sec.Zx
    lp_, lr_ = Lp(sec, steel), Lr(sec, steel, c)

    r = Result("Flexural strength (major axis)", 0.0, 0.90, 1.67, unit="kip-in")
    r.add("Zx", sec.Zx, "in^3", "")
    r.add("Sx", sec.Sx, "in^3", "")
    r.add("Mp = Fy*Zx", Mp, "kip-in", "Eq. F2-1")
    r.add("Lb", Lb, "in", "")
    r.add("Lp = 1.76*ry*sqrt(E/Fy)", lp_, "in", "Eq. F2-5")
    r.add("Lr", lr_, "in", "Eq. F2-6")
    r.add("Cb", Cb, "", "Eq. F1-1")

    if Lb <= lp_:
        Mn = Mp
        r.add("Lb <= Lp -> Mn = Mp", Mn, "kip-in", "Eq. F2-1")
        r.notes.append("Zone 1: full plastic moment, no LTB reduction.")
    elif Lb <= lr_:
        Mn = Cb * (Mp - (Mp - 0.7 * steel.Fy * sec.Sx) *
                   ((Lb - lp_) / (lr_ - lp_)))
        r.add("0.7*Fy*Sx", 0.7 * steel.Fy * sec.Sx, "kip-in", "")
        r.add("Mn (before cap)", Mn, "kip-in", "Eq. F2-2")
        if Mn > Mp:
            r.notes.append("Cb raised Mn above Mp; capped at Mp.")
            Mn = Mp
        r.notes.append("Zone 2: inelastic lateral-torsional buckling.")
    else:
        Fcr = (Cb * math.pi ** 2 * steel.E / (Lb / sec.rts) ** 2) * \
              math.sqrt(1.0 + 0.078 * (sec.J * c / (sec.Sx * sec.ho)) *
                        (Lb / sec.rts) ** 2)
        Mn = Fcr * sec.Sx
        r.add("Lb/rts", Lb / sec.rts, "", "")
        r.add("Fcr", Fcr, "ksi", "Eq. F2-4")
        r.add("Mn = Fcr*Sx (before cap)", Mn, "kip-in", "Eq. F2-3")
        if Mn > Mp:
            Mn = Mp
            r.notes.append("Elastic LTB exceeded Mp; capped at Mp.")
        r.notes.append("Zone 3: elastic lateral-torsional buckling.")

    r.Rn = Mn
    r.add("Mn", Mn, "kip-in", "Sec. F2")
    r.add("phi_b = 0.90 / Omega_b = 1.67", "", "", "Sec. F1")

    if sec.bf_2tf is not None:
        cls = classify_flange(sec.bf_2tf, steel)
        r.add("flange class", cls, "", "Table B4.1b")
        if cls != "compact":
            r.notes.append("Flange is %s -- Sec. F3 flange local buckling MUST "
                           "also be checked; Mn above is LTB only." % cls)
    if sec.h_tw is not None:
        clw = classify_web(sec.h_tw, steel)
        r.add("web class", clw, "", "Table B4.1b")
        if clw != "compact":
            r.notes.append("Web is %s -- Sec. F4/F5 applies, not F2." % clw)
    return r


def flexure_flange_local_buckling(sec: Section, steel: Steel) -> Result:
    """
    Compression flange local buckling for a NONCOMPACT flange,
    AISC Eq. F3-1 (Lecture 7).
    """
    sec.need("Zx", "Sx", "bf_2tf")
    E, Fy = steel.E, steel.Fy
    lam = sec.bf_2tf
    lpf = 0.38 * math.sqrt(E / Fy)
    lrf = 1.0 * math.sqrt(E / Fy)
    Mp = Fy * sec.Zx
    r = Result("Flange local buckling", Mp, 0.90, 1.67, unit="kip-in")
    r.add("lambda_f = bf/2tf", lam, "", "")
    r.add("lambda_pf", lpf, "", "Table B4.1b")
    r.add("lambda_rf", lrf, "", "Table B4.1b")
    if lam <= lpf:
        r.notes.append("Compact flange -- FLB does not apply.")
        return r
    if lam <= lrf:
        Mn = Mp - (Mp - 0.7 * Fy * sec.Sx) * ((lam - lpf) / (lrf - lpf))
        r.add("Mn", Mn, "kip-in", "Eq. F3-1")
        r.notes.append("Noncompact flange.")
    else:
        raise NotImplementedError(
            "Slender flange (Eq. F3-2) requires kc and is outside the scope "
            "of this skill. Check the Manual directly.")
    r.Rn = Mn
    return r


def shear_strength(sec: Section, steel: Steel, kv: float = 5.34) -> Result:
    """
    Shear strength of a rolled I-shape web, AISC Sec. G2 (Lecture 8).

        Vn = 0.6*Fy*Aw*Cv1,   Aw = d*tw

    For most rolled W-shapes h/tw <= 2.24*sqrt(E/Fy), which gives
    Cv1 = 1.0 AND phi_v = 1.00 (not 0.90).
    """
    sec.need("d", "tw", "h_tw")
    E, Fy = steel.E, steel.Fy
    Aw = sec.d * sec.tw
    h_tw = sec.h_tw
    limit_224 = 2.24 * math.sqrt(E / Fy)

    if h_tw <= limit_224:
        Cv1, phi, omega = 1.0, 1.00, 1.50
        basis = "Sec. G2.1(a): h/tw <= 2.24*sqrt(E/Fy), phi_v = 1.00"
    else:
        phi, omega = 0.90, 1.67
        lim1 = 1.10 * math.sqrt(kv * E / Fy)
        if h_tw <= lim1:
            Cv1 = 1.0
            basis = "Eq. G2-3"
        else:
            Cv1 = lim1 / h_tw
            basis = "Eq. G2-4"

    Vn = 0.6 * Fy * Aw * Cv1
    r = Result("Shear strength", Vn, phi, omega)
    r.add("Aw = d*tw", Aw, "in^2", "Sec. G2.1")
    r.add("h/tw", h_tw, "", "")
    r.add("2.24*sqrt(E/Fy)", limit_224, "", "Sec. G2.1(a)")
    r.add("Cv1", Cv1, "", basis)
    r.add("Vn = 0.6*Fy*Aw*Cv1", Vn, "kips", "Eq. G2-1")
    r.add("phi_v / Omega_v", "%.2f / %.2f" % (phi, omega), "", "Sec. G2.1")
    return r


def deflection_check(delta: float, L: float, limit_denom: float = 360.0,
                     label: str = "Live load deflection") -> Check:
    """
    Serviceability deflection check (Lecture 8). L and delta same units.
    Common limits: L/360 live load on floors, L/240 total load,
    L/180 roof live. These are IBC/serviceability values, NOT AISC 360
    strength requirements.
    """
    return Check("%s <= L/%.0f" % (label, limit_denom),
                 delta, L / limit_denom, unit="in",
                 reference="IBC Table 1604.3 / AISC Design Guide 3")


# ----------------------------------------------------------------------
# CHAPTER H + APPENDIX 8 -- BEAM-COLUMNS  (Lectures 10-14)
# ----------------------------------------------------------------------

def B1_factor(Pr: float, Pe1: float, Cm: float = 1.0,
              method: str = "LRFD") -> Result:
    """
    P-delta (member) moment amplifier, AISC Eq. A-8-3 (Lecture 12).

        B1 = Cm / (1 - alpha*Pr/Pe1) >= 1.0
        alpha = 1.00 LRFD, 1.60 ASD

    Applies to the NO-TRANSLATION moment Mnt in a braced member.
    """
    alpha = 1.00 if method.upper() == "LRFD" else 1.60
    denom = 1.0 - alpha * Pr / Pe1
    r = Result("B1 amplification", 1.0, 1.0, 1.0, unit="")
    r.add("Cm", Cm, "", "Eq. A-8-4")
    r.add("Pr", Pr, "kips", "")
    r.add("Pe1", Pe1, "kips", "Eq. A-8-5")
    r.add("alpha", alpha, "", method.upper())
    if denom <= 0:
        r.notes.append("B1 denominator <= 0: the member is unstable under this "
                       "axial load. Increase the section.")
        r.Rn = float("inf")
        return r
    B1 = max(1.0, Cm / denom)
    r.add("B1 = Cm/(1 - alpha*Pr/Pe1)", B1, "", "Eq. A-8-3")
    r.Rn = B1
    return r


def Cm_factor(M1: float, M2: float, transverse_load: bool = False) -> float:
    """
    Equivalent uniform moment factor, AISC Eq. A-8-4 (Lecture 12).

        Cm = 0.6 - 0.4*(M1/M2)

    M1/M2 is POSITIVE for reverse curvature, NEGATIVE for single
    curvature. For members with transverse load between supports,
    Cm = 1.0 is permitted.
    """
    if transverse_load:
        return 1.0
    if M2 == 0:
        return 1.0
    return 0.6 - 0.4 * (M1 / M2)


def Pe1_value(sec: Section, steel: Steel, K1L: float,
              axis: str = "x", tau_b: float = 1.0) -> float:
    """Elastic buckling load in the plane of bending, AISC Eq. A-8-5."""
    I = sec.Ix if axis == "x" else sec.Iy
    if I is None:
        raise ValueError("Section needs I%s for Pe1" % axis)
    return (math.pi ** 2) * (steel.E * tau_b) * I / (K1L ** 2)


def B2_factor(Pstory: float, Pe_story: float, method: str = "LRFD") -> Result:
    """
    Sidesway (story) moment amplifier, AISC Eq. A-8-6 (Lecture 13).

        B2 = 1 / (1 - alpha*Pstory/Pe_story) >= 1.0

    Pstory   = total vertical load on the story (all columns)
    Pe_story = elastic critical buckling strength of the STORY,
               = Rm * H * L / delta_H   (Eq. A-8-7)
    """
    alpha = 1.00 if method.upper() == "LRFD" else 1.60
    denom = 1.0 - alpha * Pstory / Pe_story
    r = Result("B2 amplification", 1.0, 1.0, 1.0, unit="")
    r.add("Pstory (total story gravity load)", Pstory, "kips", "")
    r.add("Pe_story", Pe_story, "kips", "Eq. A-8-7")
    r.add("alpha", alpha, "", method.upper())
    if denom <= 0:
        r.notes.append("B2 denominator <= 0: the STORY is unstable. The frame "
                       "must be stiffened -- no member size fixes this.")
        r.Rn = float("inf")
        return r
    B2 = max(1.0, 1.0 / denom)
    r.add("B2 = 1/(1 - alpha*Pstory/Pe_story)", B2, "", "Eq. A-8-6")
    r.Rn = B2
    if B2 > 1.7:
        r.notes.append("B2 = %.2f > 1.7: AISC requires the direct analysis "
                       "method; the effective length method is not permitted "
                       "(Sec. C1 / App. 7)." % B2)
    return r


def Pe_story_from_drift(Rm: float, H: float, L: float, drift: float) -> float:
    """Story elastic buckling strength from a drift analysis, Eq. A-8-7.
    Rm = 0.85 for moment frames, 1.0 for braced frames."""
    return Rm * H * L / drift


def required_second_order(Mnt: float, Mlt: float, B1: float, B2: float,
                          Pnt: float = 0.0, Plt: float = 0.0):
    """
    Second-order required strengths, AISC Eq. A-8-1 and A-8-2.
        Mr = B1*Mnt + B2*Mlt
        Pr = Pnt + B2*Plt
    """
    return B1 * Mnt + B2 * Mlt, Pnt + B2 * Plt


def beam_column_interaction(Pr: float, Pc: float,
                            Mrx: float, Mcx: float,
                            Mry: float = 0.0, Mcy: float = 1.0) -> Result:
    """
    Combined axial + flexure interaction, AISC Sec. H1.1 (Lecture 10).

        Pr/Pc >= 0.2 :  Pr/Pc + (8/9)*(Mrx/Mcx + Mry/Mcy) <= 1.0   (H1-1a)
        Pr/Pc <  0.2 :  Pr/(2Pc) + (Mrx/Mcx + Mry/Mcy) <= 1.0      (H1-1b)

    Pc and Mc are AVAILABLE strengths -- already factored for the chosen
    method. Mix them consistently (all LRFD or all ASD).
    """
    ratio_P = Pr / Pc if Pc else float("inf")
    mom = (Mrx / Mcx if Mcx else 0.0) + (Mry / Mcy if Mcy else 0.0)

    if ratio_P >= 0.2:
        value = ratio_P + (8.0 / 9.0) * mom
        eq = "Eq. H1-1a"
    else:
        value = ratio_P / 2.0 + mom
        eq = "Eq. H1-1b"

    r = Result("Beam-column interaction", value, 1.0, 1.0, unit="")
    r.add("Pr", Pr, "kips", "")
    r.add("Pc (available axial)", Pc, "kips", "Ch. E")
    r.add("Pr/Pc", ratio_P, "", "")
    r.add("Mrx", Mrx, "kip-in", "")
    r.add("Mcx (available flexural)", Mcx, "kip-in", "Ch. F")
    if Mry:
        r.add("Mry", Mry, "kip-in", "")
        r.add("Mcy", Mcy, "kip-in", "Ch. F")
    r.add("interaction value", value, "", eq)
    r.notes.append("%s governs (Pr/Pc = %.3f %s 0.2)."
                   % (eq, ratio_P, ">=" if ratio_P >= 0.2 else "<"))
    return r


# ----------------------------------------------------------------------
# APPENDIX 6 -- STABILITY BRACING  (Lectures 15, 16)
# ----------------------------------------------------------------------

def panel_bracing_column(Pr: float, Lbr: float, method: str = "LRFD"):
    """
    Panel (formerly 'relative') bracing of a column, AISC App. 6.2.1.
        required strength   Pbr = 0.005*Pr                (Eq. A-6-1)
        required stiffness  beta_br = (1/phi)*(2*Pr/Lbr)  (Eq. A-6-2)
        phi = 0.75 (LRFD), Omega = 2.00 (ASD)
    Returns (Pbr, beta_br).
    """
    Pbr = 0.005 * Pr
    if method.upper() == "LRFD":
        beta = (1.0 / 0.75) * (2.0 * Pr / Lbr)
    else:
        beta = 2.00 * (2.0 * Pr / Lbr)
    return Pbr, beta


def point_bracing_column(Pr: float, Lbr: float, method: str = "LRFD"):
    """
    Point (formerly 'nodal') bracing of a column, AISC App. 6.2.2.
        Pbr = 0.01*Pr                                     (Eq. A-6-3)
        beta_br = (1/phi)*(8*Pr/Lbr)                      (Eq. A-6-4)
    Returns (Pbr, beta_br).
    """
    Pbr = 0.01 * Pr
    if method.upper() == "LRFD":
        beta = (1.0 / 0.75) * (8.0 * Pr / Lbr)
    else:
        beta = 2.00 * (8.0 * Pr / Lbr)
    return Pbr, beta


def point_bracing_beam(Mr: float, Lbr: float, ho: float, Cd: float = 1.0,
                       method: str = "LRFD"):
    """
    Point bracing of a beam, AISC App. 6.3.2.
        Pbr = 0.02*Mr*Cd/ho                               (Eq. A-6-7)
        beta_br = (1/phi)*(10*Mr*Cd)/(Lbr*ho)             (Eq. A-6-8)
    Cd = 1.0 generally; 2.0 for the brace closest to the inflection
    point in a member subject to double curvature.
    """
    Pbr = 0.02 * Mr * Cd / ho
    if method.upper() == "LRFD":
        beta = (1.0 / 0.75) * (10.0 * Mr * Cd) / (Lbr * ho)
    else:
        beta = 2.00 * (10.0 * Mr * Cd) / (Lbr * ho)
    return Pbr, beta
