# BIMSteelAuto — user guide

How to take a PEB from dimensions to a model, a calculation sheet, an IFC
file and drawings.

You need two things open: **PowerShell** (to run the engine, one command)
and **FreeCAD** (to see and build the model). Nothing else.

---

## Contents

1. [What it does, in one picture](#1-what-it-does-in-one-picture)
2. [One-time setup](#2-one-time-setup)
3. [Step 1 — start a new job](#3-step-1--start-a-new-job)
4. [Step 2 — put in the geometry](#4-step-2--put-in-the-geometry)
5. [Step 3 — put in the 5 wind inputs](#5-step-3--put-in-the-5-wind-inputs)
6. [Step 4 — sections, loads, bracing](#6-step-4--sections-loads-bracing)
7. [Step 5 — run it](#7-step-5--run-it)
8. [Step 6 — read the calculation sheet](#8-step-6--read-the-calculation-sheet)
9. [Step 7 — build the model in FreeCAD](#9-step-7--build-the-model-in-freecad)
10. [Step 8 — what lands in the output folder](#10-step-8--what-lands-in-the-output-folder)
11. [Fixing a failing member](#11-fixing-a-failing-member)
12. [When it refuses](#12-when-it-refuses)
13. [What it does NOT do](#13-what-it-does-not-do)

---

## 1. What it does, in one picture

```
 you type                 the engine                    FreeCAD
 ─────────                ──────────                    ───────
 geometry      ──►   loads (ASCE 7-16)       ──►   BIM model
 5 wind inputs       analysis (P-Delta)            IFC4
 sections            members (AISC 360-16)         member schedule / MTO
 loads               purlins  (AISI S100-16)       GA drawings
 bracing             connections                   single-part drawings
                     ↓                             DXF
              calc_sheet.html
              design_result.json  ──────────────►  (FreeCAD reads this)
```

The engine and FreeCAD are **deliberately separate**. The engine needs
libraries FreeCAD does not have, so it runs on its own, writes
`design_result.json`, and FreeCAD reads that. You never install anything
into FreeCAD.

---

## 2. One-time setup

Already done on this PC. You only need this again on a new machine.

**The workbench** is linked into FreeCAD from
`%APPDATA%\FreeCAD\Mod\BIMSteelAutoWB`. To check it is there:

```powershell
Test-Path "$env:APPDATA\FreeCAD\Mod\BIMSteelAutoWB"
```

`True` means FreeCAD will show **BIMSteelAuto** in the workbench selector
after a restart. If it says `False`, run this once (as Administrator):

```powershell
cmd /c mklink /J "$env:APPDATA\FreeCAD\Mod\BIMSteelAutoWB" "<your-folder>\bimsteelauto\BIMSteelAutoWB"
```

**Check the engine works** — this should end with a number and no errors:

```powershell
cd "<your-folder>\bimsteelauto"; python -m pytest -q
```

Expect **858 passed, 1 skipped, 3 xfailed**. The skip and the three xfails
are deliberate and documented — they are not faults.

---

## 3. Step 1 — start a new job

Copy the template. Give it the job number as its name so you can find it
later:

```powershell
cd "<your-folder>\bimsteelauto"; Copy-Item new_project.py "job_DEMO-2026-021.py"
```

Open your copy in any text editor (Notepad is fine; VS Code is nicer).

**You only edit the six blocks marked `==== EDIT n of 6 ====`.** Everything
below `NOTHING BELOW HERE NEEDS EDITING` is plumbing. There is no
engineering in this file at all — it is a form.

Start with the job name:

```python
PROJECT_NAME = "Warehouse - Jeddah"
JOB_NUMBER   = "DEMO-2026-021"
DESIGNER     = "Ahmed Ali"
CHECKER      = "-"
```

`JOB_NUMBER` becomes the FreeCAD document name and the IFC filename, so
keep it to letters, digits and dashes.

---

## 4. Step 2 — put in the geometry

**EDIT 2 of 6.** Everything in **metres**.

```python
GEOMETRY = PEBGeometry.from_m(
    span_widths=[30.0],
    bay_spacings=[6.0, 7.5, 7.5, 7.5, 6.0],
    eave_height=7.0,
    roof_slope=0.10,
    purlin_spacing=1.5,
    girt_spacing=2.0,
    roof_type=RoofType.GABLE,
)
```

| What | Means |
|---|---|
| `span_widths` | Width of each span, across the frame. **One** number = clear span. For two or more you must ALSO set `frame_type=FrameType.MULTI_SPAN` here and give an `interior_column` in EDIT 4 — both, or it refuses. |
| `bay_spacings` | Bay spacings **along** the building, left to right. |
| `eave_height` | Floor to underside of the eave. |
| `roof_slope` | **Rise per unit run**, not degrees. See the table below. |
| `purlin_spacing` | Purlin centres, measured up the slope. |
| `girt_spacing` | Girt centres, up the wall. |
| `roof_type` | `RoofType.GABLE` or `RoofType.MONOSLOPE`. |

**Bays do not have to be equal, and they are not averaged.** A 7.5 m bay
carries 25% more load than a 6 m one, and the program keeps every bay
separate all the way through. The number of bays sets the number of frames:
**5 bays = 6 frame lines**.

### Roof slope conversion

| Pitch | `roof_slope` | Degrees | C&C OK? |
|---|---|---|---|
| 0.5 : 12 | `0.0417` | 2.4° | yes |
| 1 : 12 | `0.0833` | 4.8° | yes |
| 1 : 10 | `0.10` | 5.7° | yes |
| 1.5 : 12 | `0.125` | 7.1° | **no** |
| 2 : 12 | `0.1667` | 9.5° | **no** |

> **The 7° limit.** Above 7° the ASCE 7-16 components-and-cladding figure
> changes, and the new one has a **different zone layout**, not just
> different numbers. There is nothing safe to extrapolate to, so the
> program **stops with a message** rather than guessing. If you need a
> 2:12 roof, say so — Fig. 30.3-2B has to be added first.

---

## 5. Step 3 — put in the 5 wind inputs

**EDIT 3 of 6.** These five are the *only* wind quantities you supply.
Everything else — the velocity pressure `qh`, the exposure coefficient
`Kz`, every zone coefficient, the end-zone width — is derived and printed
on the sheet.

```python
SITE = ProjectSite(
    basic_wind_speed=45.0,
    exposure_category=ExposureCategory.C,
    enclosure_classification=EnclosureClassification.ENCLOSED,
    risk_category=RiskCategory.II,
    Kzt=1.0,
)
```

### 1. `basic_wind_speed` — V, in **metres per second**

3-second gust at 10 m in Exposure C, from the ASCE 7-16 map.

> **If your map is in mph:** `V(m/s) = mph × 0.44704`.
> 120 mph = 53.6, 115 mph = 51.4, 110 mph = 49.2, 100 mph = 44.7.

The program refuses anything outside 10–100 m/s, on the assumption you have
pasted mph by mistake. That guard has caught it before.

### 2. `exposure_category`

| Value | Use for |
|---|---|
| `ExposureCategory.B` | Suburban, wooded, closely spaced obstructions |
| `ExposureCategory.C` | **Open country — the usual one** |
| `ExposureCategory.D` | Flat unobstructed, water upwind |

### 3. `enclosure_classification` — sets the internal pressure GCpi

| Value | GCpi | Use for |
|---|---|---|
| `ENCLOSED` | ±0.18 | **The usual one** |
| `PARTIALLY_ENCLOSED` | ±0.55 | A big opening on one wall |
| `PARTIALLY_OPEN` | ±0.18 | |
| `OPEN` | 0.00 | No walls |

> **This one matters more than it looks.** Partially enclosed is **three
> times** the internal pressure of enclosed, and because a pressurised
> interior pushes up on its own roof, it makes the uplift a lot worse — it
> is usually what sizes the holding-down bolts. If a wall has a large
> door that could be open in a storm, this is the box to think about.

### 4. `risk_category`

| Value | Use for |
|---|---|
| `RiskCategory.I` | Minor storage, low hazard to life |
| `RiskCategory.II` | **Ordinary buildings — the usual one** |
| `RiskCategory.III` | Assembly, schools, >300 occupants |
| `RiskCategory.IV` | Essential — hospital, fire station |

It must be the **same** risk category as the map you took V from. ASCE 7-16
publishes a separate wind map per risk category; mixing them is a common
and invisible error.

### 5. `Kzt` — topographic factor

`1.0` unless the building sits on a hill, ridge or escarpment meeting
ASCE 7-16 Sect. 26.8.1. Leave it at 1.0 unless you have actually worked it
out — it can only make things worse, never better.

### Also in this block

`Ke` (ground elevation factor) — leave at `1.0`, which is sea level and is
always permitted.

`MATERIALS` — plate and cold-formed grades in MPa, bolt and anchor grades,
concrete strength.

---

## 6. Step 4 — sections, loads, bracing

### EDIT 4 — the sections

**This is what you change when a member fails.**

```python
SECTIONS = FrameSections(
    column=MemberSectionSpec(
        start=WeldedI.symmetric(d=450.0, tw=8.0, bf=250.0, tf=16.0),  # base
        end=WeldedI.symmetric(d=850.0, tw=8.0, bf=250.0, tf=16.0),    # eave
    ),
    rafter=MemberSectionSpec(
        start=WeldedI.symmetric(d=850.0, tw=7.0, bf=250.0, tf=16.0),  # eave
        end=WeldedI.symmetric(d=450.0, tw=7.0, bf=250.0, tf=16.0),    # ridge
    ),
)
```

All in **mm**: `d` overall depth, `tw` web, `bf` flange width, `tf` flange
thickness.

Two rules:

- **Column runs base → eave. Rafter runs eave → ridge.** So a haunched
  rafter has its **deep end first**.
- **A taper may vary only in DEPTH.** Web and flange plates are constant
  along a member. If you change `tw`, `bf` or `tf` between the two ends it
  will refuse — that is two members, not a taper.

The purlin/girt section is a **catalogue item**. Every number comes from
your supplier's table, especially `Se_top` / `Se_bot`, which are *effective*
section moduli, not gross. Nothing is derived from the profile dimensions.

> The **name** matters too. FreeCAD reads the `C200x60x20x2.5` pattern to
> size the solid. A name that does not match `C` or `Z` + `HxBxDxt` is
> **skipped and reported**, never guessed.

### EDIT 5 — the loads

Dead load build-up in kN/m² of **sloping** roof. Every line prints on the
sheet including the zeros — a zero means "considered, and it is zero", not
"forgotten".

```python
DEAD = DeadLoadBuildUp(
    sheeting=0.06, insulation=0.01, liner=0.04,
    purlins=0.08, services=0.10, collateral=0.15,
)
```

Typical sheeting weights, kN/m²:

| Cladding | kN/m² |
|---|---|
| 0.5 mm single skin profiled steel | 0.050 |
| 0.7 mm single skin profiled steel | 0.070 |
| 50 mm PUF sandwich panel | 0.120 |
| 75 mm PUF sandwich panel | 0.140 |
| 100 mm PUF sandwich panel | 0.160 |
| Single skin + 50 mm glasswool + liner | 0.110 |

> **Do not put the main frame's self weight here.** The analysis adds it
> from the section properties. Putting it here counts it twice. Purlins and
> girts *do* belong here, because they are not in the frame model.

**Roof live load is not an input** — it is derived from the tributary area
to ASCE 7-16 Eq. 4.8-1 and printed on the sheet with its R1 and R2 factors.

### EDIT 6 — bracing and detailing

```python
BRACING = BracingInputs(
    braced_bays=(1, 3),
    roof_brace=...,   # M20 rod
    wall_brace=...,   # M24 rod
    strut_section=...,
    n_braced_walls=2,
    roof_panel_splits=2,
)
```

`braced_bays` are bay numbers counted **from 0** at the left end. With 5
bays they are 0, 1, 2, 3, 4.

> **There is no default and there cannot be one.** A building with no
> longitudinal bracing has no load path for wind on the endwall. That is a
> *missing* design, not a zero one, and the program refuses to run.

Bracing the **end bays** (0 and 4) is common and correct. Note that it
leaves the eave strut with nothing to drag along, so its force comes out
zero — that is right, not a bug.

Rod sizes available: 12, 16, 20, 22, 24, 27, 30, 36, 42.

> Use **rods**, not angles, for a long diagonal. A 10 m angle diagonal has
> L/r around 730 against the AISC advisory limit of 300 and will be
> flagged. Rods are exempt from that advice, which is exactly why PEBs use
> them.

`DETAILING` is the shop stuff: bolt and anchor sizes, base plate size,
anchor edge distance, assumed pedestal. **`anchor_edge` and
`pedestal_depth` drive the anchor result** — they are assumptions, they are
printed on the sheet, and the anchor check must be re-run against the real
pier when the civil design exists.

`FRAME_INDEX = None` designs the frame with the **widest tributary bay**,
which is the most heavily loaded interior frame. Set a number to force a
particular one.

---

## 7. Step 5 — run it

```powershell
cd "<your-folder>\bimsteelauto"; python job_DEMO-2026-021.py
```

It prints a summary and writes **into its own job folder**, named after
`JOB_NUMBER`:

```
peb\jobs\DEMO-2026-021\
    calc_sheet.html       the calculation sheet
    design_result.json    what FreeCAD reads
```

Each job gets its own folder, so two jobs can never overwrite each other's
results. The last line of the run tells you the exact path.

The last lines tell you what you need:

```
frame designed       : 2 of 0..5  (tributary 7.50 m)
qh                   : 0.9797 kN/m2
Sect. 28.3.4 minimum : GOVERNS, wind scaled x 1.3626
purlin C&C uplift    : 1.957 kN/m2 (zone 3)
unverified relations : 0
coverage gaps        : 29
READY FOR ISSUE      : False
```

**`Sect. 28.3.4 minimum: GOVERNS`** means the code's minimum wind load was
bigger than the calculated one, so the wind case was scaled up to it. That
happens on moderate-wind sites and is normal.

**`unverified relations : 0`** means every design formula in the engine has
been checked against an independent source. If this is ever not zero, the
sheet says so and the design cannot be issued.

---

## 8. Step 6 — read the calculation sheet

Open `calc_sheet.html` in any browser.

```powershell
start jobs\DEMO-2026-021\calc_sheet.html
```

Read it in this order:

1. **The banner at the top.** Green `READY FOR ISSUE`, or red
   `NOT FOR ISSUE` with every reason listed. There is no way to turn this
   off, and every drawing produced from a failing design carries the same
   stamp.
2. **Assumptions.** Things assumed because they are outside the scope of
   work — the pedestal size, the K = 1.0, the bracing arrangement. An
   assumption you cannot see is a lie, so they all print.
3. **Checks NOT performed.** The coverage gaps. These are checks that are
   *absent*, which is a different thing from a formula that is unchecked.
   Read them: they tell you what you still have to do by hand.
4. **Loads.** Every case with its clause reference, and the combinations
   that were actually solved.
5. **Member pages.** One per member, every limit state with its AISC or
   AISI equation number, and the governing one marked.
6. **Connection pages.** Same, with the selections made.
7. **Reactions.** For the foundation designer.

---

## 9. Step 7 — build the model in FreeCAD

1. Open **FreeCAD**.
2. Pick **BIMSteelAuto** from the workbench selector (the dropdown in the
   toolbar). If it is not there, see [setup](#2-one-time-setup) and restart
   FreeCAD.
3. Click **Build model**.
4. Choose your `design_result.json`.
5. Wait — about 20 seconds for a 6-frame building.

You get the model on screen, and a summary box telling you what was built,
what it weighs, and whether the design behind it is issuable.

### Result contours — the STAAD-style picture

**BIMSteelAuto -> Result contours** draws the frame in its deflected shape,
coloured station by station. It reads `contours.json`, which the job script
writes next to `design_result.json`; it does not re-run the analysis, so it
is instant and it cannot disagree with the calculation sheet.

The dialog asks three things:

| Colour by | What it shows | Read it for |
|---|---|---|
| **Resultant displacement [mm]** | sqrt(dx^2 + dy^2) at every station | where the frame moves, eave drift, ridge sag |
| **Extreme fibre stress [MPa]** | \|P\|/A + \|M\|/S on the section AT that station | where the material is working, and where a taper is wasting it |
| **AISC 360-16 utilisation [-]** | the worst limit-state ratio at that station | where the design is actually tight — green under 0.75, amber to 1.00, red over |

plus the **combination** (every one that was solved is in the list, the
governing one is marked) and the **deflection magnification** (automatic
unless you type one).

The magnification changes the picture only. Every number in the legend and
the title block is the real one, unscaled.

A displacement contour is drawn on FACTORED combinations, because those are
what the frame was designed on. Do not read it as a serviceability check
without dividing out the load factors.

The drawing lands in a group called **BSA_Contour** on the frame line that
was designed, so it lines up with the model in a front view. Drawing again
replaces it.

### The other three buttons

| Button | Does |
|---|---|
| **Design status** | Reads a `design_result.json` and reports blockers, unverified relations, coverage gaps and assumptions — **without building anything**. Use it to check a job you ran last week. |
| **Open output folder** | Opens the folder the last build wrote to. |
| **Settings** | Where the `peb` package folder is. Only needed if you move it. |

### What is in the model tree

```
Main frames
  Frame line 1 ... 6      C1, C2, R1, R2 on every line
Secondary
  purlins, girts, eave struts
Bracing
  the rod X panels, in the bays you braced
MTO                       the schedule, as a spreadsheet
```

Click any member: its **Mark**, **MemberType**, **Profile**, **Length_mm**,
**Weight_kg** and **Utilisation** are on the property panel under
*BIMSteelAuto*.

---

## 10. Step 8 — what lands in the output folder

Everything goes in an `out\` folder beside your JSON — so for job
`DEMO-2026-021` that is `peb\jobs\DEMO-2026-021\out\`.

| File | What it is |
|---|---|
| `<JOB>.FCStd` | The FreeCAD model. Open it any time. |
| `<JOB>.ifc` | **IFC4** — columns as `IfcColumn`, rafters/purlins/girts/struts as `IfcBeam`, bracing as `IfcMember`, in a proper Site → Building → Storey tree. Opens in Revit, Tekla, Navisworks, BIMcollab. |
| `schedule.csv` | The **MTO** — type, profile, count, length each, mass, worst utilisation, then main / secondary / total. Opens in Excel. |
| `GA_frame_elevation.svg` | Transverse frame elevation, A3, auto-scaled, title block. |
| `GA_side_elevation.svg` | Side elevation. |
| `GA_roof_plan.svg` | Roof plan. |
| `GA_*.dxf` | The same three views as **DXF**, for the fabricator. |
| `PART_*.svg` | **One single-part drawing per mark** — C1, C2, R1, R2, P1, G1, ES1, RB1, WB1. Each says the mark, how many, the profile, the length and the mass each. |

SVG opens in any browser, Inkscape, or Illustrator. To print, open in a
browser and print to PDF at A3.

> Every sheet carries the **NOT FOR ISSUE** stamp if the design behind it
> is failing. That is driven by the design, not by a setting.

---

## 11. Fixing a failing member

The sheet names the member, the check and the ratio:

```
** NOT FOR ISSUE **
   - member P1 fails Combined bending and shear at ratio 1.075
   - connection BP1 fails at ratio 1.990
```

| What failed | What to change |
|---|---|
| **Column or rafter** | EDIT 4 — make it deeper (`d`), or thicker web (`tw`) for shear, or wider/thicker flange (`bf`, `tf`) for bending. Deeper usually helps most per kilo. |
| **Purlin or girt** | EDIT 4 — a heavier catalogue section; or EDIT 2 — closer `purlin_spacing`; or a shorter end bay in `bay_spacings`. |
| **Brace (RB1/WB1)** | EDIT 6 — the next rod size up. |
| **Eave strut ES1** | EDIT 6 — a bigger `strut_section`, or brace more bays so each carries less. |
| **Base plate (BP1/BP2)** | EDIT 6 — bigger `base_plate_N`/`B`, larger `anchor_diameter`, deeper `anchor_embedment`, or **more `anchor_edge`** — concrete edge distance usually governs. If it fails on **anchor shear**, that normally needs a **shear lug**, which this program does not design. |
| **End plate (EC1/EC2/AP1)** | EDIT 6 — bigger `bolt_diameter`, or a thicker `plate_grade` plate. |

Change one thing, run again, look at the number. The whole run takes a few
seconds.

> **Aim for about 0.85–0.95.** Below 0.7 you are wasting steel; above 1.0
> it fails. `steel per plan area` in the FreeCAD summary is the sanity
> check — a normal PEB lands around **25–40 kg/m²**.

---

## 12. When it refuses

The program stops rather than guessing. The message says what to do.

| Message | Means |
|---|---|
| `basic_wind_speed = 101.0 m/s is outside the credible range` | You pasted mph. Multiply by 0.44704. |
| `roof slope 9.46 deg is above the 7 deg limit of Fig. 30.3-2A` | The C&C figure changes above 7°. Flatten the roof, or ask for Fig. 30.3-2B. |
| `braced_bays is empty ... a missing design, not a zero one` | EDIT 6 — say which bays are braced. |
| `a tapered member may vary only in depth` | EDIT 4 — you changed a flange or web between the two ends. That is two members. |
| `geometry.frame_type is clear_span but 2 span_widths were given` | EDIT 2 — a multi-span frame needs **two** changes: add `frame_type=FrameType.MULTI_SPAN` in EDIT 2 **and** an `interior_column` in EDIT 4. |
| `2 spans need an interior_column section` | EDIT 4 — you set `frame_type` but not the interior column. |
| `the transverse zone mapping is defined for a gable roof` | Monoslope C&C is not implemented. |
| `antisag_ties_per_bay must be at least 1` | Without a bottom-flange tie the uplift check cannot be evaluated at all. |
| `no design_result.json` in FreeCAD | Run your job script first. |

Several of these arrive behind a header line:

```
the spec and inputs must both be valid before anything is designed:
  bracing.braced_bays is empty. A building with no longitudinal bracing has
  no load path for endwall wind - that is a missing design, not a zero one.
```

**The line that matters is the indented one underneath.** Everything wrong
with the inputs is listed at once, so you can fix them all in one pass
rather than one run per mistake.

Everything it refuses, it refuses **for a stated reason**. If a message is
not clear, send it to me and I will make it clearer.

---

## 13. What it does NOT do

Be clear about this before you rely on it.

- **It does not choose sections.** It checks the ones you give it.
- **The model has no shop detailing.** No bolt holes, no haunch cuts, no
  cleats, no stiffeners, no real end-plate outlines. The solids are the
  *design* sections. Quantities, IFC classes and general arrangement are
  right; fabrication geometry is not there yet.
- **No endwall framing.** No endwall posts, endwall girts or gable columns
  — the engine designs a transverse frame and the longitudinal system.
- **No foundation.** A pedestal is assumed only so the anchor check has
  something to sit in, and it is stated on the sheet.
- **No shear lug**, so a pinned base with large horizontal thrust will
  report the demand as unchecked rather than absorbing it.
- **No seismic.** Wind only.
- **No torsional wind load cases, parapets or roof overhangs.**
- **The Direct Analysis Method's stiffness reduction and notional loads are
  not applied.** K = 1.0 is justified by the second-order run; the rest is
  a declared gap.

All of these print on the calculation sheet under **Checks NOT performed**.
Nothing above is hidden.

---

## Quick reference card

```powershell
# new job
cd "<your-folder>\bimsteelauto"
Copy-Item new_project.py "job_MYJOB.py"

# ... edit the six EDIT blocks ...

# run it
python job_MYJOB.py

# read it  (the run prints the exact path)
start jobs\MYJOB\calc_sheet.html
```

Then: **FreeCAD → BIMSteelAuto → Build model → pick
`jobs\MYJOB\design_result.json`**.

Outputs land in `jobs\MYJOB\out\`.
