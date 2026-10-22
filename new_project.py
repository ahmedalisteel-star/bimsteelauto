"""
new_project.py - COPY THIS FILE FOR EVERY NEW JOB.

This file is DATA ONLY.  There is no engineering in it - all of that lives
in bimsteelauto/, which you never edit.  Change the numbers between the
==== EDIT ==== markers, save, and run it.

    py new_project.py

It writes into its OWN job folder, named after JOB_NUMBER:

    jobs\<JOB_NUMBER>\calc_sheet.html      open it in a browser
    jobs\<JOB_NUMBER>\design_result.json   what FreeCAD reads

so two jobs can never overwrite each other.

See USER_GUIDE.md for the whole workflow.

------------------------------------------------------------------------
A WORD ON WHAT THIS PROGRAM WILL AND WILL NOT DO FOR YOU
------------------------------------------------------------------------
It CHECKS the sections you give it.  It does not choose them.  If a member
fails you make it bigger here and run again.

It will not quietly fix anything.  A number it cannot justify is refused
with a message rather than filled in with a guess, and anything assumed is
printed on the sheet.  If the sheet says NOT FOR ISSUE, it means it.
"""

from __future__ import annotations

import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
for _p in (HERE, HERE / "vendor"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from bimsteelauto.analysis import FrameSections, MemberSectionSpec
from bimsteelauto.connections import ConnectionDetailing
from bimsteelauto.design import CFShape, ColdFormedSection
from bimsteelauto.design.bracing import ROD_ROOT_AREA, BraceMember, BraceType
from bimsteelauto.loads import AreaLoad, DeadLoadBuildUp, Surface
from bimsteelauto.pipeline import (
    BracingInputs, FrameInputs, SecondaryInputs, design_frame,
)
from bimsteelauto.report import render_html, render_text
from bimsteelauto.sections import WeldedI
from bimsteelauto.spec import (
    DesignSettings, EnclosureClassification, ExposureCategory, FrameType,
    Materials, PEBGeometry, ProjectSite, ProjectSpec, RiskCategory,
    RoofType,
)

# ==========================================================================
# ==== EDIT 1 of 6 =========================================  THE JOB  =====
# ==========================================================================

PROJECT_NAME = "PEB Warehouse - 24*36"
JOB_NUMBER = "DEMO-2026-015"
DESIGNER = "Ahmed Ali"
CHECKER = "kopesi"


# ==========================================================================
# ==== EDIT 2 of 6 ====================================  THE GEOMETRY  =====
# ==========================================================================
# Everything in METRES.

GEOMETRY = PEBGeometry.from_m(
    # Width of each span, across the frame.  ONE number for a clear span.
    # For a MULTI-SPAN frame you must change THREE things or it refuses:
    #   1. list each span here, e.g. [15.0, 15.0]
    #   2. add   frame_type=FrameType.MULTI_SPAN   below
    #   3. give  interior_column=...               in EDIT 4
    span_widths=[30.0],

    # Bay spacings ALONG the building, left to right.  They do NOT have to
    # be equal, and they must not be averaged - a 7.5 m bay carries 25%
    # more than a 6 m one and the program keeps them apart.
    # The number of bays sets the number of frames: 5 bays = 6 frame lines.
    bay_spacings=[6.0, 6.0, 6.0, 6.0, 6.0, 6.0],

    eave_height=6.0,          # floor to underside of the eave

    # Roof slope as RISE PER UNIT RUN.  0.10 is 1:10.  1:12 is 0.0833,
    # 0.5:12 is 0.0417, 2:12 is 0.1667.
    # WARNING: above 0.1228 (7 degrees) the components-and-cladding
    # coefficients change figure and the program will REFUSE to run rather
    # than guess.  See USER_GUIDE.md.
    roof_slope=0.10,

    purlin_spacing=1.25,       # purlin centres, up the roof slope
    girt_spacing=2.0,         # girt centres, up the wall

    roof_type=RoofType.GABLE, # GABLE or MONOSLOPE (monoslope: C&C refuses)
    # frame_type=FrameType.MULTI_SPAN,   # only for 2+ span_widths
)


# ==========================================================================
# ==== EDIT 3 of 6 =========================  THE 5 WIND INPUTS + SITE =====
# ==========================================================================
# These five are the ONLY wind quantities you supply.  Everything else -
# qh, Kz, the zone coefficients, the end-zone width - is derived.

SITE = ProjectSite(
    # 1. Basic wind speed V, in METRES PER SECOND, 3-second gust at 10 m in
    #    Exposure C.  Take it from the ASCE 7-16 map for the SAME risk
    #    category you set below.
    #    If your map is in mph:  V(m/s) = mph x 0.44704
    #    45 m/s ~ 101 mph.  The program refuses anything outside 10-100 m/s
    #    on the assumption you have typed mph by mistake.
    basic_wind_speed=50.0,

    # 2. Exposure category - ASCE 7-16 Sect. 26.7.
    #    B = suburban/wooded, C = open country (the usual one),
    #    D = flat unobstructed, water upwind.
    exposure_category=ExposureCategory.C,

    # 3. Enclosure - ASCE 7-16 Sect. 26.12.  Sets GCpi.
    #    ENCLOSED           +/-0.18   the usual one
    #    PARTIALLY_ENCLOSED +/-0.55   THREE TIMES the internal pressure -
    #                                 a building with a big opening on one
    #                                 wall, and it changes the uplift a lot
    #    PARTIALLY_OPEN     +/-0.18
    #    OPEN                 0.00    no internal pressure at all
    enclosure_classification=EnclosureClassification.ENCLOSED,

    # 4. Risk category - ASCE 7-16 Table 1.5-1.
    #    I   minor storage, low hazard
    #    II  ordinary buildings (the usual one)
    #    III assembly, schools, >300 occupants
    #    IV  essential - hospitals, fire stations
    #    It must match the wind map you took V from.
    risk_category=RiskCategory.II,

    # 5. Topographic factor Kzt - ASCE 7-16 Sect. 26.8.
    #    1.0 unless the building sits on a hill, ridge or escarpment that
    #    meets Sect. 26.8.1.  Leave it at 1.0 unless you have worked it out.
    Kzt=1.0,
)

# Ground elevation factor Ke, Sect. 26.9.  1.0 is sea level and is always
# permitted, so leave it unless you want the (small) benefit of altitude.
Ke = 1.0

MATERIALS = Materials(
    Fy_builtup=345.0,      # MPa - plate for the tapered frame (S355 / A572-50)
    Fu_builtup=450.0,
    Fy_cold_formed=345.0,  # MPa - purlins and girts
    Fu_cold_formed=450.0,
    bolt_grade="A325",
    anchor_grade="F1554-36",
    fc_concrete=30.0,      # MPa
)


# ==========================================================================
# ==== EDIT 4 of 6 ======================================  THE SECTIONS ====
# ==========================================================================
# The frame plate sizes.  THIS is what you change when a member fails.
#
# A tapered member may vary only in DEPTH along its length - web and flange
# plates are constant.  If you need a different flange you need a second
# member, not a taper.
#
# Column runs BASE -> EAVE, rafter runs EAVE -> RIDGE, so a haunched rafter
# has its DEEP end first.

SECTIONS = FrameSections(
    column=MemberSectionSpec(
        start=WeldedI.symmetric(d=250.0, tw=8.0, bf=200.0, tf=12.0),  # base
        end=WeldedI.symmetric(d=750.0, tw=8.0, bf=200.0, tf=12.0),    # eave
    ),
    rafter=MemberSectionSpec(
        start=WeldedI.symmetric(d=750.0, tw=8.0, bf=200.0, tf=12.0),  # eave
        end=WeldedI.symmetric(d=250.0, tw=8.0, bf=200.0, tf=12.0),    # ridge
    ),
    # For a MULTI-SPAN frame add:
    # interior_column=MemberSectionSpec.prismatic(
    #     WeldedI.symmetric(d=500.0, tw=8.0, bf=200.0, tf=12.0)),
)

# The purlin / girt section.  EVERY NUMBER HERE IS A CATALOGUE VALUE from
# your supplier's table.  Nothing is derived from the profile dimensions -
# in particular Se_top / Se_bot are EFFECTIVE section moduli, and a wrong
# one produces a sheet that is confidently wrong.
#
# The NAME matters too: FreeCAD reads the C/Z HxBxDxt pattern to size the
# solid, and a name that does not match is skipped rather than guessed.
PURLIN = ColdFormedSection(
    name="C200x60x20x1.5", shape=CFShape.C,
    t=1.5, H=200.0, B=60.0, Dlip=20.0, r=3.0,
    A=685.0, Ix=4.02e6, Sx=40_200.0,
    Se_top=34_500.0, Se_bot=34_500.0,
    Iy=0.30e6, Sy=7_000.0, J=1_430.0, Cw=2.1e9, ho=200.0,
)
GIRT = PURLIN                      # or a different catalogue section


# ==========================================================================
# ==== EDIT 5 of 6 =========================================  THE LOADS ====
# ==========================================================================
# Dead load build-up, kN/m2 of SLOPING roof.  Every line prints on the
# sheet including the zeros, so a zero means "considered, and it is zero" -
# not "forgotten".
#
# The main frame's own weight is NOT here: the analysis adds it from the
# section properties, and putting it here would count it twice.  Purlins
# and girts ARE here, because they are not in the frame model.

DEAD = DeadLoadBuildUp(
    sheeting=0.06,          # 0.7 mm profiled steel
    insulation=0.01,
    liner=0.04,
    purlins=0.08,           # secondary steel
    services=0.10,          # lighting, sprinklers, cable tray
    collateral=0.15,        # future / owner allowance
    sheeting_description="0.5 mm trapezoidal, pre-painted",
)

# Wall cladding and girts, hung off the columns.
WALL_DEAD = AreaLoad(
    value=0.09, surface=Surface.SLOPING,
    label="Wall cladding and girts", reference="Project build-up",
)

# Roof live load is NOT set here - it is derived from the tributary area to
# ASCE 7-16 Eq. 4.8-1 and printed on the sheet.


# ==========================================================================
# ==== EDIT 6 of 6 ================================  BRACING + DETAILING ===
# ==========================================================================
# Which BAYS carry longitudinal bracing, counted from 0 at the left end.
# 5 bays are numbered 0,1,2,3,4.
#
# There is no default.  A building with no longitudinal bracing has no load
# path for wind on the endwall - that is a MISSING design, not a zero one,
# and the program refuses it.
#
# Bracing the END bays (0 and 4) is common and correct; note that it leaves
# the eave strut with nothing to drag, so its force comes out zero.

BRACING = BracingInputs(
    braced_bays=(1, 3),

    # Roof and wall diagonals.  Rods are the usual PEB detail and are
    # exempt from the AISC L/r <= 300 advice; an ANGLE over a 10 m diagonal
    # is not, and will be flagged.
    # Available rod sizes: 12 16 20 22 24 27 30 36 42
    roof_brace=BraceMember(
        name="M20 rod", brace_type=BraceType.ROD,
        Ag=math.pi / 4.0 * 20.0 ** 2, An_effective=ROD_ROOT_AREA[20.0],
        r_min=20.0 / 4.0, diameter=20.0,
    ),
    wall_brace=BraceMember(
        name="M24 rod", brace_type=BraceType.ROD,
        Ag=math.pi / 4.0 * 24.0 ** 2, An_effective=ROD_ROOT_AREA[24.0],
        r_min=24.0 / 4.0, diameter=24.0,
    ),

    # The eave strut, checked for its longitudinal drag force.
    strut_section=WeldedI.symmetric(d=250.0, tw=6.0, bf=150.0, tf=8.0),

    n_braced_walls=2,        # 2 normally; 1 if one side is built against
    roof_panel_splits=2,     # X panels per roof slope
)

# Shop decisions the analysis cannot make.
DETAILING = ConnectionDetailing(
    bolt_diameter=24.0, bolt_grade="A325", plate_grade="S355JR",
    anchor_grade="F1554-36",
    base_plate_N=600.0, base_plate_B=450.0,   # mm, N along the frame
    anchor_diameter=30.0, anchor_embedment=450.0,
    anchor_spacing_x=350.0, anchor_spacing_y=350.0,
    anchor_edge=250.0,       # concrete edge distance - drives the anchor check
    pedestal_depth=1200.0,   # ASSUMED so the anchor check has a member;
                             # the foundation is a separate civil package
    fc_prime=30.0,
)

# Which frame line to design.  None = the one with the widest tributary
# bay, which is the most heavily loaded interior frame.  Set a number to
# force it (0 is the left endwall frame).
FRAME_INDEX = None


# ==========================================================================
# ============================  NOTHING BELOW HERE NEEDS EDITING  ==========
# ==========================================================================

spec = ProjectSpec(
    project_name=PROJECT_NAME, job_number=JOB_NUMBER,
    site=SITE, geometry=GEOMETRY, materials=MATERIALS,
    settings=DesignSettings(),
).validate()

inputs = FrameInputs(
    sections=SECTIONS, dead=DEAD, wall_dead=WALL_DEAD,
    secondary=SecondaryInputs(purlin_section=PURLIN, girt_section=GIRT),
    bracing=BRACING, detailing=DETAILING,
    frame_index=FRAME_INDEX, Ke=Ke,
)

run = design_frame(spec, inputs)
result = run.result

# Each job gets its OWN folder, so two jobs cannot overwrite each other's
# results - which they would if everything landed in the package root.
import re

safe = re.sub(r"[^A-Za-z0-9_.-]", "_", JOB_NUMBER) or "job"
JOB_DIR = HERE / "jobs" / safe
JOB_DIR.mkdir(parents=True, exist_ok=True)

(JOB_DIR / "calc_sheet.html").write_text(
    render_html(result, designer=DESIGNER, checker=CHECKER,
                title="PEB Frame Design Calculations"),
    encoding="utf-8",
)
(JOB_DIR / "design_result.json").write_text(result.to_json(), encoding="utf-8")

# The result picture: every station of every member, for every combination,
# so FreeCAD can COLOUR the frame instead of re-running the analysis.  The
# adapter never solves anything; this is the file it draws.
from bimsteelauto.analysis import build_contours, write_contours

contours = build_contours(run)
write_contours(contours, JOB_DIR / "contours.json")

print(render_text(result))
print()
print(f"frame designed       : {run.frame_index} of "
      f"0..{GEOMETRY.n_frames - 1}  (tributary "
      f"{GEOMETRY.tributary_widths()[run.frame_index] / 1000.0:.2f} m)")
print(f"qh                   : {run.wind.qh:.4f} kN/m2")
print(f"Sect. 28.3.4 minimum : "
      f"{'GOVERNS, wind scaled x %.4f' % run.minimum.scale if run.minimum.governs else 'does not govern'}")
print(f"purlin C&C uplift    : {run.purlin_cladding.uplift:.3f} kN/m2 "
      f"(zone {run.purlin_cladding.zone})")
print(f"unverified relations : {len(result.unverified)}")
print(f"coverage gaps        : {len(result.coverage_gaps)}")
print(f"READY FOR ISSUE      : {result.ready_for_issue()}")
print()
print(f"job folder           : {JOB_DIR}")
print(f"  calc_sheet.html    <- open in a browser")
print(f"  design_result.json <- FreeCAD reads this")
print(f"  contours.json      <- FreeCAD draws the result contours from this")
