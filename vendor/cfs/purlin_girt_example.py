"""
purlin_girt_example.py -- worked example: a Z-section roof purlin
(secondary PEB member) checked per AISI S100-16, both by the Effective
Width Method (EWM) and by the Direct Strength Method (DSM), ASD.

*** ALL SECTION PROPERTIES AND LOADS BELOW ARE ILLUSTRATIVE PLACEHOLDER
VALUES *** -- they are round numbers chosen to demonstrate the code
path, NOT verified SSMA catalog properties or a real project's loads.
Before using this for an actual PEB:
  1. Replace CFSSection properties with real catalog values (SSMA
     Product Technical Guide, or your manufacturer's ICC-ES report).
  2. Replace Mu/Vu (or Ma/Va) with your actual factored/service demands
     from a real structural analysis (continuous-span purlin analysis,
     or your PEB software's reaction/moment output).
  3. Replace Mcrl/Mcrd/Mcre with CUFSM (or equivalent finite-strip)
     output for the actual section if using the DSM path.
  4. Confirm bracing: through-fastened or standing-seam roofing may
     provide continuous lateral-torsional bracing to the purlin's top
     flange (AISI S100-16 Sec. D6.1.1/D6.1.2) -- if so, LTB (EWM) or
     global buckling (DSM) may not govern, but the bracing-adequacy
     check itself (force transferred to the diaphragm) is a SEPARATE
     limit state not evaluated here.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from aisi_s100 import (CFSSection, Steel, A1003_50, shear_strength,
                        flexure_strength_ewm, combined_bending_shear,
                        deflection_check)
from direct_strength import beam_strength_dsm
from calc_sheet import CalcSheet

# ----------------------------------------------------------------------
# 1. Section and material (PLACEHOLDER -- replace with catalog values)
# ----------------------------------------------------------------------

steel = A1003_50   # Fy = 50 ksi, typical structural-grade CFS coil

purlin = CFSSection(
    name="Z8x2.5 (illustrative, e.g. ~ SSMA 800Z250-59 class)",
    shape="Z",
    t=0.059,          # design thickness, in (~16 ga)
    H=8.0,             # out-to-out depth, in
    B=2.5,             # out-to-out flange width, in
    Dlip=0.75,         # out-to-out lip depth, in
    r=0.1875,          # inside bend radius, in
    A=0.93,            # in^2  -- PLACEHOLDER, confirm vs. catalog
    Ix=8.35,           # in^4  -- PLACEHOLDER
    Sx=2.09,           # in^3  -- PLACEHOLDER (gross, extreme fiber)
    Se_top=1.85,       # in^3  -- PLACEHOLDER effective Se (compression
                       #          flange braced/unbraced case combined --
                       #          get the real value from your section
                       #          property tool)
    Se_bot=2.02,       # in^3  -- PLACEHOLDER
    Aw=0.44,           # in^2  -- PLACEHOLDER web shear area (~ h*t)
    h_flat=7.4,        # in    -- PLACEHOLDER flat web depth
)

span_ft = 20.0
spacing_ft = 3.0

# ----------------------------------------------------------------------
# 2. Demands (PLACEHOLDER -- replace with actual analysis output)
#    Single-span simple beam, uniform load, ASD service loads shown for
#    illustration. A real design should use the continuous-span (2- or
#    3-span) moments a purlin line actually sees, which are lower at
#    midspan and introduce a hogging moment + higher shear at supports.
# ----------------------------------------------------------------------

w_D = 3.0    # psf, dead
w_Lr = 16.0  # psf, roof live
w_asd = (w_D + w_Lr) * spacing_ft / 1000.0   # kips/ft, ASD (D+Lr)
span_in = span_ft * 12.0

Ma = w_asd * span_ft ** 2 / 8.0 * 12.0   # kip-in, simple-span moment
Va = w_asd * span_ft / 2.0               # kips, simple-span end shear
delta = 5 * (w_asd / 12.0) * span_in ** 4 / (384 * steel.E * purlin.Ix)  # in

# ----------------------------------------------------------------------
# 3. Build the calc sheet -- EWM path
# ----------------------------------------------------------------------

sheet = CalcSheet(
    "Roof Purlin Check -- Z8x2.5, EWM",
    project="Example PEB Project",
    member="Purlin line P-1, typical interior span",
    method="ASD",
)
sheet.assume("Simple-span, uniformly loaded, single span %.0f ft" % span_ft)
sheet.assume("Purlin spacing %.1f ft" % spacing_ft)
sheet.assume("Loads: D=%.1f psf, Lr=%.1f psf (ASD combination D+Lr)" % (w_D, w_Lr))
sheet.assume("Top flange assumed continuously braced by roof panel for "
             "this EWM run -- LTB (Sec. C3.1.2) not evaluated; see DSM "
             "sheet below for an unbraced-condition example instead")
sheet.warn("ALL section properties are illustrative placeholders -- "
           "replace with verified SSMA catalog or section-software output "
           "before use on a real project.")

sheet.heading("Demands (illustrative)")
sheet.given("w (D+Lr)", w_asd, "kip/ft", "ASD")
sheet.given("Ma", Ma, "kip-in", "wL^2/8")
sheet.given("Va", Va, "kips", "wL/2")
sheet.given("Deflection", delta, "in", "5wL^4/384EI")

fx = flexure_strength_ewm(purlin, steel)
sheet.heading("Flexure (Sec. C3.1.1)")
sheet.result(fx)
chk_fx = sheet.check_result(fx, Ma, "Flexure, Se*Fy (C3.1.1)")

vx = shear_strength(purlin, steel)
sheet.heading("Shear (Sec. C3.2.1)")
sheet.result(vx)
chk_vx = sheet.check_result(vx, Va, "Shear (C3.2.1)")

combo = combined_bending_shear(Ma, fx.design("ASD"), Va, vx.design("ASD"))
sheet.heading("Combined bending + shear (Sec. C3.3.1)")
sheet.check(combo)

defl = deflection_check(delta, span_in, limit_denom=180.0)
sheet.heading("Deflection")
sheet.check(defl)

sheet.exclude("Lateral-torsional buckling (Sec. C3.1.2) -- assumed "
              "continuously braced by roof panel; verify actual bracing "
              "and fastener pattern per Sec. D6.1")
sheet.exclude("Web crippling at supports/load points (Sec. C3.4) -- "
              "needs the actual bearing length N and support condition")
sheet.exclude("Continuous-span (2-/3-span) moment and shear envelope -- "
              "this example used a conservative single simple span only")
sheet.exclude("Fastener/connection design to structural frame")

print(sheet.to_markdown())
sheet.save(os.path.join(os.path.dirname(__file__), "..", "purlin_check_EWM.md"))

# ----------------------------------------------------------------------
# 4. DSM path example -- unbraced condition, illustrative Mcr values
#    In practice: run CUFSM on the actual cross-section to get
#    Mcrl/Mcrd/Mcre. The values below are ROUND-NUMBER PLACEHOLDERS.
# ----------------------------------------------------------------------

Mcre_example = 220.0   # kip-in, global (LTB) -- PLACEHOLDER, unbraced case
Mcrl_example = 95.0    # kip-in, local -- PLACEHOLDER
Mcrd_example = 60.0    # kip-in, distortional -- PLACEHOLDER

dsm_fx = beam_strength_dsm(purlin.Sx, steel.Fy, Mcre_example, Mcrl_example,
                            Mcrd_example)

sheet2 = CalcSheet(
    "Roof Purlin Check -- Z8x2.5, DSM (Appendix 1)",
    project="Example PEB Project",
    member="Purlin line P-1, unbraced condition (e.g., during erection, "
           "before deck attached)",
    method="ASD",
)
sheet2.assume("Mcre/Mcrl/Mcrd are PLACEHOLDER example values -- replace "
              "with CUFSM finite-strip output for the actual section")
sheet2.warn("Elastic buckling values (Mcre, Mcrl, Mcrd) are NOT computed "
            "by this script -- they must come from a finite-strip "
            "analysis of the exact cross-section.")
sheet2.heading("Demands (illustrative, same as EWM case)")
sheet2.given("Ma", Ma, "kip-in", "wL^2/8")

sheet2.heading("Flexure -- DSM (Appendix 1 Sec. 1.2.2)")
sheet2.result(dsm_fx)
sheet2.check_result(dsm_fx, Ma, "Flexure, DSM (App. 1)")

sheet2.exclude("Shear, combined bending+shear, web crippling, deflection "
               "-- not repeated here, see EWM sheet")

print("\n\n")
print(sheet2.to_markdown())
sheet2.save(os.path.join(os.path.dirname(__file__), "..", "purlin_check_DSM.md"))
