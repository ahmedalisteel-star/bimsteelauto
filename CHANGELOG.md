# BIMSteelAuto - changelog

## 0.14.2 - 2026-11-08 - ModelSettings: stiffness_factor and min_segments

Two modelling settings, both defaulting to the old behaviour (nothing changes
unless they are set):

- `stiffness_factor` (default 1.0, must lie in (0, 1]): E and G of the steel
  - so EI, EA and GJ - times this. 0.8 is the AISC 360-16 C2.3(a) reduction
  for the Direct Analysis Method (permitted on all members); serviceability
  stays on 1.0. Requested by Manafe3, which applied it through a subclass.
- `min_segments` (default 1): at least this many solver elements per design
  member, whatever the taper rule gives. PyNite captures P-delta (the
  member's own curvature) only through the nodes between elements: a constant
  member left as one element misses most of it. AISC 360-16 C2.1 asks for
  P-Delta AND P-delta.

**Tests** (`tests/test_model_settings.py`): a braced pin-ended column at
P/Pe = 0.30 under a small lateral load - 4 elements give the midheight moment
within 2 % of the AISC B1 value and 1 % of the exact beam-column value
(measured: 1 element 1.225, 2 -> 1.355, 4 -> 1.433, 8 -> 1.441, exact 1.441);
one element falls 15 % short (the negative control); a frame with k = 0.8
moves exactly 1/0.8 as far under a lateral load with identical forces; the
settings are validated.

## 0.14.1 - 2026-11-08 - FIX: the compression flange was chosen backwards

**Re-check every member result from earlier runs - in particular Jobs 16 and
17.** Utilisations, governing checks and unbraced lengths reported before
this fix may be wrong; connections, base plates and stress contours used |M|
and are not affected.

PyNite reports `Mz` positive for TENSION on the member's local +y face (a
simply supported beam under gravity gives a NEGATIVE midspan moment).
`design.members.check_member` reads `Mz >= 0` as "TOP flange in compression",
and `analysis.frame._extract` (and the 3D `analysis.space._extract`) passed
PyNite's value through unchanged. So the compression flange - and with it the
brace set (Lb), `Sxc` and the flange slenderness - was taken from the wrong
side:

- under GRAVITY the haunch and knee (bottom / inner flange in compression)
  were checked with the purlin- / girt-braced top flange's short Lb -
  UNCONSERVATIVE;
- under UPLIFT the opposite error, on the conservative side.

Found by Manafe3 (Phase 4 step 1) while mapping restraint points.

**Fix:** one conversion at the PyNite boundary,
`analysis.frame.design_moment_factor(member_type, i, j, width)`, applied in
both `_extract`s: `StationForces.M` (and the 3D frame members' `Mz`) is now
positive when the member's TOP flange is in compression (rafter: top flange;
column: the outer flange; interior column: the -X flange, a convention). It
follows PyNite's own local-axis rule (`Member3D.T`, Y up), including the case
where it bites: an exactly vertical right-hand column, whose local y points
INTO the frame. Secondary members in the 3D model keep PyNite's sign (their
design does not read it).

**Tests** (`tests/test_moment_sign.py`, 9): PyNite's own sign on a simple
beam; the same beam through `FrameAnalysis` -> +wL^2/8 and the flexure check
on the TOP flange's bracing (sagging compresses the top flange); a pinned
portal under gravity -> negative at the haunch and at the top of BOTH
columns, and the knee checked on the BOTTOM flange's bracing; the factor
table. Both physics tests FAIL on the code before the fix. The other 976
tests are unchanged - none of them pinned which flange is compressed.

## 0.14.0 - 2026-10-29 - fabrication drawings from the IFC (stage 5, steps 3-4)

The fabrication drawings were drawn from `model.json` by the steel-project
skill's `fab_drawings.py`: one view, holes in a table instead of dimensioned,
no single-part sheets, no NC, and a hole table that ran over the title block
(JOB-018 R1a). They now come straight from the IFC, in the layout of the
Tekla reference set SH014 (A3 assembly / A4 single), behind the gates the
package docstring already promised.

**New in `bimsteelauto/fabdraw`** (relative imports only - also runs on its
own; the new `ifc-fab-drawings` skill carries a copy):

- `extract.py` - IFC -> assemblies, parts, holes, weights, allocation. World
  meshes from `ifcopenshell.geom`; a member is set out along its straight
  flange (vertical one on a column, top one on a rafter), its depth is the
  face group whose planes lie furthest apart (I, H, C, Z, RHS, tapered BU);
  a plate's normal is its largest face. Holes are the round openings, clipped
  to the part. Weight = Pset weight, else volume x 7850. Marks: `strict`
  (BSA_Fabrication only) or `any` (Fabrication, Pset_SteelAssembly, Tekla,
  AdvanceSteel, Identity Data, Tag, Name - the source is kept per part).
- `hlr.py`, `views.py` - hidden-line views without OpenCASCADE (feature
  edges + silhouettes, STRtree occlusion). FRONT, TOP when holes are on the
  depth axis, a DETAIL per part mark whose holes neither shows. End depths
  measured on a section through the solid, stepping past sloped cuts and
  fused end plates.
- `drafting.py`, `sheets.py` - sheets in paper mm: scale chosen from the
  standard list and packed around material list / title block; running
  dimensions (vertical text), hole gauges, balloons, plate details, hole
  call-outs, DIMLFAC = scale; NC DXF (CUT/HOLE/TEXT) and DSTV nc1 per plate.
- `gate_roundtrip.py` - **G3**: every sheet read back: R1 printed values vs
  their own geometry (running dims and DIMENSIONs), R2 every tick on a
  hole / part edge, R3 every hole drawn face-on, R4 tables = IFC qty and
  weight, R5 inside the border, R6 overlaps (warning). Mutation-tested.
- `run.py` - `python -m bimsteelauto.fabdraw.run MODEL.ifc OUT [--pdf]`:
  G1, G2, extract, sheets, NC, G3, register, PDF books. Every sheet is drawn
  NOT FOR ISSUE and re-stamped only when G1 + G3 (+ G2 under strict) pass.

**Results.** JOB-018 (v2 ModelChain IFC): 35 assembly + 46 single-part
sheets, 15 plate NC, two PDF books in 115 s; G1 PASS, G3 PASS (988 values,
438 holes); NOT FOR ISSUE because G2 S5 finds 106 parts with empty holes
(the bolts the model is short - a model finding, not a drawing one). Also
run on Sample_Job (IFC4 and IFC2X3, `Fabrication` Pset: FOR APPROVAL) and
ARMA_CPS (faceted breps, marks from Name). `tests/fabdraw/test_fabdraw_sheets.py`
15 tests on the fixture (weights, frames, plate holes, detail dimensions,
NC, G3 mutations); `tests/fabdraw` 70 passed.

**Break lines** (same day, user asked): `views.set_breaks` cuts a long
FRONT/TOP view between features (holes, attached parts, turns of the main
part's outline), keeping 10 mm paper each side, closing each run to a 6 mm
gap with a Z break line; runs under 30 mm are left and at most 75 % of a
length goes. The scale search tries each scale whole, then shortened, and
takes the first at which the views and every plate detail fit. Overall
lengths over a break are written as text; G3 maps paper back through the
breaks (`_unbreak`) before R1/R2/R3. JOB-018 R1a 1:25 -> 1:10, C1a 1:20 ->
1:15, purlin single sheet 1:40 -> 1:20. `tests/fabdraw` 72 passed.

**Not yet.** Slotted holes, weld symbols, grid-location table; DSTV face
suffixes; columns drawn upright.

**steel-project skill:** `run_chain.py` now calls the ifc-fab-drawings skill
on the job IFC (`--fab-legacy` keeps the old script; backups `*.bak-2026-10-29`).

## 0.13.0 - 2026-10-22 - the model is built by the v8 macro

0.12.0 made the model clash-free, but it was still the adapter's own
geometry: no holes in the members, plain anchors, girts and struts on
invented clips, no strut connections. All of that already exists in the
user's parametric macro `peb_building_param_v8` (10,000 lines, GUI-verified
shop detailing). `build_all` now DRIVES THAT MACRO from the `DesignResult`.

**New `bimsteelauto_fc/v8.py`.**

- `frame_inputs(result)` - every `PEB_Frame` input the design sets: span,
  eave level, bays, column and rafter plates (the rafter's straight taper
  goes in the macro's profile table), end plates and their bolt set-out
  (rows -40 / 46.0 / 607.2 / 693.2 on REF-2026-001, the same as EC1), base
  plate and anchors, cleat, purlin / girt / strut sections and set-out,
  braced bays, rods, gussets.
- `build_v8(result)` - runs the macro's own `build()` with those inputs
  applied as the frame is created. The macro is loaded WITHOUT its trailing
  `build()` call, which otherwise builds the 24 m demo building on import.
- `build_model_v8(result)` - splits the macro's per-side compounds into one
  Arch object per piece, turns them into Layer 2's axes (a quarter turn and
  a shift - never a mirror), and stamps the design's marks, weights and
  utilisations. The macro's document is saved as `<job>_param.FCStd` (the
  editable parametric model) and its report as `<job>_v8_report.txt`.
- `build_all(..., geometry=None)`: v8 when the macro is found and the frame
  is a single-span gable; the 0.12.0 builder otherwise, with a warning that
  says why. `BSA_GEOMETRY=builtin|v8` overrides.

**Where the design and the macro disagree** (approved 2026-10-21, printed in
every build report as `choice 1..6`): grid on the outer column flange (the
macro's span is base centreline to centreline); a built-up I eave strut is
drawn as the RHS of the same envelope on the macro's tube detail, weight
from the design; one roof X per slope to the design panel point; gussets
sized to the rod and not designed; straight anchors as checked; no endwall.
A roof-brace node is moved the least distance that clears the purlin
cleats (REF-2026-001: +77 mm), and the move is reported.

**Three changes in `peb_building_param_v8.py`** (with the user's OK;
backups `*.bak-20261022` beside both copies; every change marked
`BSA 2026-10-22`):

1. Right-slope (and right-wall) rails were built upside down: `prism()`
   takes the depth axis as axis x u and the right-hand bases are left
   handed. Every right-slope Z sat in its cleat's seat leg (64 clashes on
   REF-2026-001). Rails on a left-handed basis are now run from the far end.
2. Lapped Z purlins occupied the same steel at every lap (221,710 mm3 each,
   56 laps): the lap rule alternated the hand, which a Z cannot do. A lapped
   Z now keeps one hand and the alternate bay is drawn as the NESTING half -
   flange over the cleat 2t wider, the other 2t narrower, one thickness off
   - so the pair wraps with every face touching. Same area, same mass.

**Results.** REF-2026-001: 438 pieces, **0 clashes** from `clash_check.py`;
492 macro solids checked one by one, 0. Jobs 16, 17, DEMO-2026-014,
DEMO-2026-020, REF-2026-001-GF and the demo building: 0 each, nothing
skipped. Member masses identical to 0.12.0; the take-off changes only where
the macro's real plates replace the adapter's - REF-2026-001 8,762 -> 9,154
kg: strut cleats, fins and caps (246 kg), an apex plate on each rafter,
angle cleats and clips. Lengths are the built ones (rafter 6860, column
3066), measured on the tight bounding box - the quick one is loose round
the macro's lofted faces. `selftest` 69 -> 77 checks (v8: nothing skipped,
every frame built, no clashes, grid on the outer flange, design masses,
base plate and column flange drilled). Suite **1028 passed, 1 skipped,
3 xfailed**.

3. (2026-10-22, also with the user's OK) The column stopped at the eave, so
   an EXTENDED eaves plate - EC1 stands 64 mm above the flange with a bolt
   row 40 above it - had nothing behind its top row. A flush eaves column
   now runs up to the top of the plate (`EP_ext_top_eaves`), and every row
   is drilled through the inner flange. REF-2026-001: column 3066 -> 3130,
   holes at 2371 / 2457 / 3020 / 3106 matching the plate; still 0 clashes.
   A flush-topped plate builds exactly as before.

**Not modelled.** The macro draws a rod brace as a square bar of the rod
diameter; bolts are holes, not fasteners.

## 0.12.0 - 2026-10-21 - the FreeCAD model is clash-free

The adapter used to build a **centreline model**: every solid on its
analysis line. That is the right picture of the analysis and the wrong one
of the building - on job REF-2026-001 it had **652 hard clashes**: purlins
through the rafters, cleats inside the purlins, columns and rafters
overlapping at the knee, end plates inside the members, girts and eave
struts through the columns, and 500 x 600 x 12 gussets cutting through
everything. It now builds every piece where it is built.

`build_all` on `jobs/REF-2026-001/design_result.json`: **0 clashes** from
`REF-2026-001/03_FreeCAD_BIM/clash_check.py` (267 solids, > 50 mm3). Jobs 16,
17, DEMO-2026-014, DEMO-2026-020, REF-2026-001-GF and the demo building: 0 each.

**New `bimsteelauto_fc/fabrication.py`** - the placement rules, the same
ones the REF-2026-001 shop drawings and `ph_bim_model.py` use:

- Grid on the **outer face of the column flange**; the inner flange takes the
  taper. The rafter's top of steel lies on the analysis line, so the eave
  height is the outer column face x top of steel.
- **Constant-depth knee zone**: over the eave end plate (and 30 mm below it)
  the column's inner flange is vertical at its top depth. The end plate sits
  on that flange and the rafter is cut vertically and **starts at the plate
  face**. At the ridge one plate is centred on the ridge and each rafter
  stops at its face.
- **Purlins stand on their cleats**: cleat welded on the top flange square
  to the roof, purlin bottom flange on the top flange, web against the
  cleat, set out in slope coordinates. Cold-formed members are now the real
  lipped C / Z profile, not their bounding box.
- **Girts and eave struts outside the column on clips, web horizontal**, 5 mm
  clear of the outer flange. The clips are the purlin cleat plate (`PC1G`,
  as on the REF-2026-001 drawings), utilisation 0 - no check ran on them.
  Interior column lines carry no girts.
- **Rods stop at the web faces**; the two diagonals of an X are offset by a
  rod diameter + 1 mm so they pass. Each rod end bears on a **rod plate sized
  to the rod** (hole d + 2, edge 1.5 d, t = d/2 >= 6: M12 -> PL 6 x 50 x 50).
  The engine's GP1 is a 4 x M20 bolted gusset that a rod brace does not
  have; it is no longer drawn, and the build report says so in words.

**Schedule.** Every member mass is unchanged - the weights are still the
design's, on the analysis lengths. `Length_mm` is now the built length
(REF-2026-001: rafter 7537 -> 6866, column 3000 -> 3130). The take-off moves
only where plates changed: REF-2026-001 **9,646 -> 8,762 kg** - 32 gussets
899.7 kg out, 32 rod plates 3.5 kg and 36 girt / strut clips 11.7 kg in.

**One solid per member.** `Arch.makeStructure(base)` left the base
`Part::Feature` in the document, so every member was there twice and a
clash check over the document found each one clashing with its own base.
The structure is now made without a base and holds the solid itself
(`builder._structure`).

**Tests.** `selftest` 63 -> 69 checks. Eight new: the grid is on the outer
column face, the end plate is on the column's inner flange, the rafter
starts clear of the column, a purlin stands on the rafter, each rod's
plates are sized to it (RB1, WB1), no 500 x 600 gusset on a rod brace, and
**the whole model has no hard clashes** (new `bimsteelauto_fc/clash.py`,
the `clash_check.py` test with a bounding-box shortcut; ~20 s on the demo
building). The end-plate height check now measures from the top of steel
at the plate. The two gusset part-sheet checks no longer run - there is no
GP1 sheet - and they had been passing on a STALE `PART_GP1.svg` left in the
reused temp folder by an earlier run; the sheet checks now only look at
sheets drawn in the current run. Suite unchanged: **1028 passed, 1
skipped, 3 xfailed**.

**Not changed:** the design engine. Not modelled: bolts (holes in the
members are not cut), welds, stiffeners, laps and splices (each purlin,
girt and strut line is one full-length run), the hillside bevel of a rod
washer, the cap and valley connection of an interior column.

## 0.11.1 - 2026-09-07 - the demo building passes

`run_frame_example.py` now says **READY FOR ISSUE**, max utilisation 0.891.
Two inputs changed.  No check was loosened, no tolerance moved and no
capacity was re-derived - both are things an engineer chooses, and both
were found by sweeping the input rather than by guessing.

**Purlin P1, 1.075 -> 0.889** - purlin spacing 1.5 -> 1.3 m.

The Chapter 30 line load is the zone pressure times the tributary width, so
the spacing IS the lever.  It does not change the effective wind area:
Sect. 30.2 takes `A = span x max(span/3, spacing)`, and `span/3 = 2.5 m` is
larger than either spacing, so `GCp` is unchanged and only the width moves.
The dead-load purlin allowance went 0.080 -> 0.086 kN/m2 with it, because a
C200x60x20x2.5 is 5.38 kg/m and closing the spacing adds 0.0054 kN/m2 - a
dead load that does not follow the spacing it came from is the kind of
thing that stays wrong for the life of a job.

**Base plates BP1/BP2, 1.990 -> 0.890** - anchor edge distance 250 -> 500 mm.

The 113.8 kN of base thrust is UNCHANGED and the governing limit state is
still ACI 318-19 Sect. 17.7.2 concrete breakout in shear.  Anchor diameter,
embedment and a third column of anchors were all tried and moved it by
nothing: breakout scales on `ca1^1.5` and on nothing else available here.
It is a concrete edge problem, not a steel one.

500 mm with the 350 mm gauge is a **1350 mm square pedestal**, and the
assumption printed on the sheet now says so in those words - "THE DEMAND
THIS DESIGN PLACES ON THE FOUNDATION, and the number to hand the civil
engineer".  The detail a fabricator would actually use is a SHEAR LUG,
which the connection layer says outright that it does not check; that
remains open, and until it exists every pinned base on a long-span frame
will ask for a pedestal this size.

**Also**

- The pedestal assumption now states the implied PLAN SIZE, derived from
  the anchor gauge and edge distance, not just the depth and edge.
- `selftest`: the drawing-stamp check is renamed to say what it asserts.
  It tests `stamped == (not ready_for_issue)` both ways round, and until
  now only the "design fails, stamp present" half had ever run - the demo
  passing exercises the other half for the first time, and the sheets come
  out unstamped.

- Suite **948 passed**, 1 skipped, 3 xfailed - unchanged, no test pinned
  the old numbers. FreeCAD selftest **63 checks**.

## 0.11.0 - 2026-09-06 - biaxial flexure, weak-axis shear and torsion

``My`` and ``T`` stop being a picture.  ``design/members.py`` now grades a
station on all six components, so the 3D contour can be coloured by
UTILISATION and the number means what it says.

**New checks in `design/members.py`**

- **Sect. F6** minor-axis flexure, `Mn = min(Fy*Zy, 1.6*Fy*Sy)` with flange
  local buckling. Classified on the MORE SLENDER flange - minor-axis
  bending compresses part of both, so the flange the major-axis check
  picked from the sign of `Mz` is the wrong one.
- **Sect. G6** weak-axis shear, `Vn = 0.6*Fy*bf*tf*Cv2` summed over BOTH
  flanges (not one doubled - a PEB rafter is singly symmetric), at
  `phi_v = 0.90`; the G2.1(a) `phi = 1.00` exception is a web provision and
  does not reach G6.
- **Sect. H3.3** torsion, `tau = T*t/J` against `0.6*Fy`.
- **Eqs. H1-1a/b** now carry BOTH bending terms.

Each is emitted only when its action is present, so a plane-frame station
produces exactly the checks it always did and the 2D calculation sheet is
unchanged.

**Wired through**

- `build_space_contours(..., run=run)` grades every primary member and puts
  the ratio on the contour. Without `run=`, or for a secondary member, the
  ratio is ABSENT rather than zero - the viewer skips it and names it,
  because on a contour a zero utilisation is the calmest colour there is.
- The same rule was applied to the 2D contour, which previously wrote zeros
  when a member could not be graded.
- The GUI's **Result contours** dialog offers utilisation on a 3D file and
  says it is graded on all six components.

**DEFECT FOUND AND FIXED - singly-symmetric members could not be designed
at all.** Table B4.1b Case 16 limits `lam_p` to `lam_r`, and on a
singly-symmetric welded I with a stocky web and a modest `Mp/My` the
expression exceeds `lam_r`, is capped, and the two come back EQUAL.
`pyaisc360`'s validator reads equal limits as transposed arguments and
raises - taking the whole member design with it. Sweeping realistic PEB
plate girders, **40 of 120 designed; 120 of 120 after the fix.** It had
never shown up because the example building and every fixture use
`WeldedI.symmetric`, which takes the Case 15 branch - and a rafter with a
wide top flange over a narrow bottom one, which is completely ordinary,
takes Case 16. When the two limits coincide the classification is
degenerate rather than ambiguous: no noncompact band, compact up to the
limit and slender above it. The guard is narrow and re-raises anything
else.

**Verified** - four flags, each closed by longhand arithmetic on the clause
in `tests/test_design_biaxial.py`, in SI, in the test:

- `flexure_F6_minor` - and a NEGATIVE CONTROL for the `1.6*Fy*Sy` cap. It
  never bites on a real welded I (`Zy/Sy` lands near 1.5 because the
  flanges dominate and a rectangle has `Z/S = 1.5`), so a suite of real
  sections never reaches that branch; a deliberately unrealistic 40 mm web
  does.
- `shear_G6_weak` - with the singly-symmetric case where doubling one
  flange gives the wrong answer.
- `torsion_H3_stvenant`.
- `interaction_H1_biaxial` - closed by the control that matters: the SAME
  station passes at 0.668 without `My` and **fails at 1.047 with it**, and
  the difference is exactly the `Mry/Mcy` term. An implementation that
  computed a minor-axis capacity, printed the check, and then quietly left
  `Mry` out of H1-1 would pass every other test in the suite.

**Scope, stated** - torsion is UNIFORM (St Venant) shear only. The warping
normal and warping shear stresses need the AISC Design Guide 9 solution
with the member's own end conditions, which the analysis does not produce.
On an open section the warping term is usually the larger one, so **the
reported torsion ratio is a LOWER BOUND** - said in `COVERAGE_GAPS` and in
the note on every torsion check. Sect. H2's linear stress interaction is
not run as an alternative to H1.

- Suite **948 passed**, 1 skipped, 3 xfailed. FreeCAD selftest **63 checks**.
- Unverified relations 0.

## 0.10.0 - 2026-09-06 - the building in 3D

The whole PEB is now solved as one 3D model, not as a plane frame plus a
hand calculation. P, Vy, Vz, Mz, My, T and three-dimensional displacement
exist at every station of every member, for every combination.

**New**

- `bimsteelauto/analysis/space.py` - `build_space_layout` grows the whole
  building from the ONE designed frame line: every frame line, the purlins,
  the girts, the eave struts and TENSION-ONLY X bracing, in a PyNite
  `FEModel3D` with nothing artificially restrained. `SpaceAnalysis.solve`
  returns a six-component `SpaceEnvelope`.
- `build_space_from_run(run)` - the glue. Takes a solved `FrameRun` and
  replays the loads and combinations the plane frame was actually given,
  plus the endwall force as a `W_long` case. Nothing is re-derived.
- `run_space_example.py` - one command, writes `out/contours3d.json`.
- `analysis/results.build_space_contours` - the same `ContourSet` the 2D
  viewer already draws, carrying Z and the three out-of-plane components.
- `Quantity` gained the six force components, so "colour by moment" and
  "colour by torsion" are things the GUI can now do. The dialog offers
  them, and labels the ones a plane frame cannot produce.

**Changed**

- Contour schema `bimsteelauto.contours/1` -> `/2`. ADDITIVE: a `/1` file
  still loads, reports itself 2D, and answers a torsion question with zero
  - which IS a plane frame's answer.
- `FrameAnalysis` exposes `loads` and `combinations` (read-only copies) so
  the 3D model can replay them rather than re-derive them.
- The single-part view direction now looks along a part's THINNEST
  dimension, and a plate is viewed along its own normal and rotated
  upright.

**Verified** - four flags, each closed by a control whose answer is known
without running the module:

- `agrees_with_the_plane_frame` - 0.08% on P, V, M, both base reactions and
  the eave drift, against the verified plane frame.
- `symmetric_load_gives_no_torsion` - the negative control. Worst |T| under
  symmetric gravity is below 1e-3 kN.m.
- `longitudinal_path_matches_the_hand_calculation` - wall diagonal 30.52 kN
  against `bracing.py`'s 31.8 kN, and the model shows 96.2% of the applied
  force reaching the diagonals. The hand method assumes 100%, so it is
  conservative by exactly the amount the frames pick up.
- `compression_diagonal_drops_out` - of eight diagonals, four carry in
  tension and four are at zero.

**A defect this found in itself.** The first version modelled each tapered
member as ONE prismatic member on its start section. It assembled, solved
cleanly, looked entirely reasonable, and was **36% away** from the plane
frame - a tapered column on its shallow end is too flexible and a tapered
rafter on its deep end is too stiff, and the errors add rather than cancel.
Only the agreement control caught it.

**Scope, stated** - the 3D model DESIGNS NOTHING. `design/members.py`
grades on P, Vy and Mz, so the My, Vz and T this produces have nowhere to
go; the 3D contour therefore has no utilisation colour at all, because a
ratio from three of six components would read as a pass. Loads are the
plane frame's own, applied per frame line - not derived from a roof
surface, and the purlins carry none. Endwall framing is still not modelled.

- Suite **924 passed**, 1 skipped, 3 xfailed. FreeCAD selftest **60 checks**.
- Unverified relations 0.

## 0.9.0 - 2026-09-06 - result contours

The analysis result can now be SEEN, not just tabulated.

**New**

- `bimsteelauto/analysis/results.py` - samples the solved frame at every
  station of every member for every combination: deflected shape, resultant
  displacement, P/V/M, extreme fibre stress `|P|/A + |M|/S`, and the AISC
  360-16 station utilisation. Written as `contours.json` beside
  `design_result.json`.
- `bimsteelauto_fc/contours.py` - draws it in the 3D view: one coloured edge
  per station interval, undeformed frame behind, colour bar and title block.
  Draws only; it never solves.
- Workbench button **BIMSteelAuto -> Result contours** (`bsa_commands.py`,
  dialog in the new `bsa_dialogs.py`). Pick quantity, combination and
  deflection magnification.
- `design/members.station_utilisations()` - the sweep `check_member` runs,
  kept instead of collapsed to its governing station.
- `pipeline.FrameRun.analysis` - the solved `FrameAnalysis`, so the
  deflected shape can be read back out.

**Verified** (all three flags closed)

- `deflected_shape` - member-local deflection rotated by PyNite's own
  direction cosines, checked against the node's global DX/DY at both ends of
  every member, every combination, on every build.
- `station_forces` - station for station against the `Envelope` the members
  were designed on. Exact.
- `extreme_fibre_stress` - against `sectionproperties` (mesh integration).
  C1 at x = 6000: module read first at 196.813713 MPa; A and Zxx exact to
  0.0000%. Script: `_verify_stress.py`.

Station utilisation matches `check_member` to 0.000e+00 on every member.
The first version was 0.4% conservative because it dropped the duplicated
stations at segment boundaries, which changes Cb - the duplicates are kept.

**Suite**: 887 passed, 1 skipped, 3 xfailed. Unverified relations: 0.

**Known scope**

- One transverse frame line, 2D - the contour is a frame elevation, not a
  3D model contour.
- The solved combinations are FACTORED, so a displacement contour is not a
  serviceability check until service combinations are added.
