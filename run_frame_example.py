"""
run_frame_example.py - the whole pipeline, end to end, in one call.

    ProjectSpec + FrameInputs -> run_frame() -> DesignResult -> calc_sheet.html

The engineering used to live in this file.  It now lives in
``bimsteelauto.pipeline``, so the FreeCAD adapter can make the same call
this script makes.  What is left here is the DATA - one building, one set of
engineering choices - plus the console report.

Run it with::

    python run_frame_example.py
"""

from __future__ import annotations

import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
for p in (HERE, HERE / "vendor"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bimsteelauto.connections import ConnectionDetailing
from bimsteelauto.design import CFShape, ColdFormedSection
from bimsteelauto.design.bracing import ROD_ROOT_AREA, BraceMember, BraceType
from bimsteelauto.analysis import FrameSections, MemberSectionSpec
from bimsteelauto.loads import AreaLoad, DeadLoadBuildUp, Surface
from bimsteelauto.pipeline import (
    BracingInputs,
    FrameInputs,
    SecondaryInputs,
    design_frame,
)
from bimsteelauto.report import render_html, render_text
from bimsteelauto.sections import WeldedI
from bimsteelauto.spec import (
    DesignSettings,
    EnclosureClassification,
    ExposureCategory,
    Materials,
    PEBGeometry,
    ProjectSite,
    ProjectSpec,
    RiskCategory,
)

# ======================================================================
# 1.  The building
# ======================================================================

spec = ProjectSpec(
    project_name="PEB Warehouse - Frame F2",
    job_number="DEMO-2026-014",
    site=ProjectSite(
        basic_wind_speed=45.0,                       # m/s, 3s gust at 10 m Exp C
        exposure_category=ExposureCategory.C,
        enclosure_classification=EnclosureClassification.ENCLOSED,
        risk_category=RiskCategory.II,
        Kzt=1.0,
    ),
    geometry=PEBGeometry.from_m(
        span_widths=[30.0],
        bay_spacings=[6.0, 7.5, 7.5, 7.5, 6.0],      # deliberately non-uniform
        eave_height=7.0,
        roof_slope=0.10,                             # 1:10, theta = 5.71 deg
        # 1.3 m, not the 1.5 m first tried.  P1 failed at 1.075 on the
        # Chapter 30 corner-zone uplift, and the purlin line load is the
        # zone pressure times this width - so the spacing IS the fix.  Note
        # that it does not change the effective wind area: Sect. 30.2 takes
        # A = span x max(span/3, spacing), and span/3 = 2.5 m is larger
        # than either spacing, so GCp is unchanged and only the tributary
        # width moves.
        purlin_spacing=1.3,
        girt_spacing=2.0,
    ),
    materials=Materials(
        Fy_builtup=345.0, Fu_builtup=450.0,
        Fy_cold_formed=345.0, Fu_cold_formed=450.0,
        bolt_grade="A325", anchor_grade="F1554-36",
        fc_concrete=30.0,
    ),
    settings=DesignSettings(),
).validate()


# ======================================================================
# 2.  What the engineer chose
# ======================================================================

#: Catalogue purlin/girt.  The effective moduli Se are supplier values -
#: nothing is derived from the profile dimensions.
C200 = ColdFormedSection(
    name="C200x60x20x2.5", shape=CFShape.C,
    t=2.5, H=200.0, B=60.0, Dlip=20.0, r=3.0,
    A=685.0, Ix=4.02e6, Sx=40_200.0,
    Se_top=34_500.0, Se_bot=34_500.0,
    Iy=0.30e6, Sy=7_000.0, J=1_430.0, Cw=2.1e9, ho=200.0,
)

inputs = FrameInputs(
    # A tapered member may vary only in DEPTH - the plate sizes are fixed
    # along its length, and a change of flange means a second member.
    sections=FrameSections(
        column=MemberSectionSpec(
            start=WeldedI.symmetric(d=450.0, tw=8.0, bf=250.0, tf=16.0),
            end=WeldedI.symmetric(d=850.0, tw=8.0, bf=250.0, tf=16.0),
        ),
        rafter=MemberSectionSpec(
            start=WeldedI.symmetric(d=850.0, tw=7.0, bf=250.0, tf=16.0),
            end=WeldedI.symmetric(d=450.0, tw=7.0, bf=250.0, tf=16.0),
        ),
    ),
    dead=DeadLoadBuildUp(
        # 0.086, up from the 0.080 assumed at 1.5 m purlin centres: a
        # C200x60x20x2.5 is 5.38 kg/m, so closing the spacing to 1.3 m adds
        # 0.0054 kN/m2 of purlin.  Small, and carried anyway - a dead load
        # that does not follow the spacing it is derived from is the kind
        # of thing that stays wrong for the life of a job.
        sheeting=0.06, insulation=0.01, liner=0.04, purlins=0.086,
        services=0.10, collateral=0.15,
        sheeting_description="0.5 mm trapezoidal, pre-painted",
    ),
    wall_dead=AreaLoad(
        value=0.09, surface=Surface.SLOPING,
        label="Wall cladding and girts", reference="Project build-up",
    ),
    secondary=SecondaryInputs(purlin_section=C200, girt_section=C200),
    bracing=BracingInputs(
        # One bay in from each end.  Bracing the END bays is equally common,
        # but then the endwall force enters the braced panel directly and
        # the eave strut carries NOTHING - a correct answer that makes a
        # poor demonstration.  Keeping the end bays clear (for endwall
        # doors) is a real arrangement and exercises the whole drag path.
        braced_bays=(1, 3),
        roof_brace=BraceMember(
            name="M20 rod", brace_type=BraceType.ROD,
            Ag=math.pi / 4.0 * 20.0 ** 2, An_effective=ROD_ROOT_AREA[20.0],
            r_min=20.0 / 4.0, diameter=20.0,
        ),
        # A ROD, not an angle.  The sidewall diagonal is about 10 m long; an
        # L65x65x6 over that has L/r = 732 against AISC's advisory 300, and
        # nobody would detail it.  A pretensioned rod is the standard PEB
        # wall brace and is exempt from the L/r advice.
        wall_brace=BraceMember(
            name="M24 rod", brace_type=BraceType.ROD,
            Ag=math.pi / 4.0 * 24.0 ** 2, An_effective=ROD_ROOT_AREA[24.0],
            r_min=24.0 / 4.0, diameter=24.0,
        ),
        strut_section=WeldedI.symmetric(d=250.0, tw=6.0, bf=150.0, tf=8.0),
    ),
    detailing=ConnectionDetailing(
        bolt_diameter=24.0, bolt_grade="A325", plate_grade="S355JR",
        anchor_grade="F1554-36",
        base_plate_N=600.0, base_plate_B=450.0,
        anchor_diameter=30.0, anchor_embedment=450.0,
        anchor_spacing_x=350.0, anchor_spacing_y=350.0,
        # 500 mm, not the 250 mm first tried.  The base carries 113.8 kN of
        # horizontal thrust from 1.2D+1.6Lr on a PINNED base, and the
        # governing limit state is ACI 318-19 Sect. 17.7.2 CONCRETE
        # BREAKOUT IN SHEAR - which scales as ca1^1.5 and on nothing else
        # that is available here.  Anchor diameter, embedment and a third
        # column of anchors were all tried and moved it by nothing: it is a
        # concrete edge problem, not a steel one.
        #
        # 500 mm edge with the 350 mm gauge implies a PEDESTAL 1350 mm
        # square.  That is large, and it is stated rather than buried,
        # because the pedestal is the civil engineer's and this is a demand
        # on it - see the assumption printed on the sheet.
        #
        # The detail a fabricator would actually use is a SHEAR LUG, which
        # takes the thrust into bearing and leaves the anchors carrying
        # tension only.  The connection layer says outright that it does
        # not check one, so it cannot be relied on here.
        anchor_edge=500.0,
        pedestal_depth=1200.0, fc_prime=30.0,
    ),
    frame_index=2,
)


# ======================================================================
# 3.  Run it
# ======================================================================

run = design_frame(spec, inputs)
result = run.result

out = HERE / "calc_sheet.html"
out.write_text(
    render_html(
        result, designer="Ahmed Ali", checker="-",
        title="PEB Frame Design Calculations",
    ),
    encoding="utf-8",
)

# The layer boundary, written down.  The FreeCAD adapter reads THIS, not
# the engine - which is why it needs no solver installed.
json_out = HERE / "design_result.json"
json_out.write_text(result.to_json(), encoding="utf-8")


# ======================================================================
# 4.  Report
# ======================================================================

geometry = spec.geometry
wind = run.wind

print(render_text(result))
print()
print("=" * 70)
print("WIND - ASCE 7-16 Ch. 28 Part 1, derived")
print("=" * 70)
for label, value, unit in wind.steps:
    print(f"  {label:44s} {value:12.4f}  {unit}")
print(f"  {'governing uplift case':44s} {wind.roof_uplift_case():>12s}")
print()
print("  ASCE 7-16 Sect. 28.3.4 minimum design wind loading")
for label, value, unit in run.minimum.steps:
    print(f"    {label:42s} {value:12.4f}  {unit}")
print(f"    {'governs?':42s} {str(run.minimum.governs):>12s}")
print()
print("  ASCE 7-16 Ch. 30 components and cladding (purlins and girts)")
for label, cp in (("purlin", run.purlin_cladding), ("girt", run.girt_cladding)):
    print(f"    {label:6s} zone {cp.zone:3s}  A = {cp.area:6.2f} m2  "
          f"GCp {cp.gcp_negative:+.3f}/{cp.gcp_positive:+.3f}  "
          f"inward {cp.inward:+.3f}  outward {cp.outward:+.3f} kN/m2"
          + ("  [Sect. 30.2.2 floor applied]"
             if any(cp.minimum_applied) else ""))
worst_mwfrs = min(wind.pressures["W_A_pi_pos"].values())
print(f"    for comparison, worst MWFRS roof suction = {worst_mwfrs:+.3f} "
      f"kN/m2 -> C&C is {run.purlin_cladding.uplift / abs(worst_mwfrs):.2f}x worse")
print()
print(f"  longitudinal endwall pressure = {run.endwall_pressure:.4f} kN/m2")
print(f"  F_eave = {run.brace_forces.F_eave:.1f} kN over "
      f"{run.longitudinal.n_braced} braced bays")
print()
print(f"frame designed      : {run.frame_index} of 0..{geometry.n_frames - 1}"
      f"  (tributary "
      f"{geometry.tributary_widths()[run.frame_index] / 1000.0:.3f} m)")
print(f"combinations solved : {len(result.loads.combinations)}")
print(f"unverified relations: {len(result.unverified)}")
print(f"coverage gaps       : {len(result.coverage_gaps)}")
print(f"ready for issue     : {result.ready_for_issue()}")
print(f"\nwrote {out}")
