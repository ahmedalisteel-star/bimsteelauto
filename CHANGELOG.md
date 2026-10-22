# BIMSteelAuto - changelog

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
