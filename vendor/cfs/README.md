# AISI S100-16 secondary-member (CFS) design module

Pure-Python (standard library only) design checks for cold-formed steel
**secondary members** — purlins and girts — in a Pre-Engineered
Building, per **AISI S100-16**. Written to mirror the calc-sheet
pattern of the AISC 360 skill already in this workspace, so both hot
-rolled primary frame members and cold-formed secondary members can
produce the same auditable calc-sheet format for a full PEB member set.

## What this is for

The GitHub repo you had open (`aac-cfs-estimator`) is a CFS **material
takeoff/estimation** engine — it selects assemblies and validates them
against AISI S100-16 as part of a BOM generator. This module is a
different thing: it's a **design/check calculator** that produces a
per-member calculation sheet (demand vs. capacity, every limit state,
governing ratio) for a purlin or girt — the kind of sheet you'd want
alongside your AISC primary-frame calc sheets for a complete PEB member
set.

## Files

- `scripts/aisi_s100.py` — Effective Width Method (EWM): flexure
  (section strength + LTB), shear, combined bending+shear, web
  crippling (equation engine), deflection. Read the module docstring's
  **VERIFICATION STATUS** block first — it states exactly which
  coefficients are confirmed vs. which need to be checked against your
  edition of the standard.
- `scripts/direct_strength.py` — Direct Strength Method (DSM), AISI
  S100-16 Appendix 1, for both columns and beams. The strength-curve
  coefficients (0.673, 0.776, 2.78, 0.56, 0.561, etc.) were cross
  -checked against an independent published source in this session.
- `scripts/calc_sheet.py` — renders a Markdown (or minimal HTML) calc
  sheet: assumptions, given values, full audit trail per check,
  limit-state summary table, verdict, and an explicit "NOT checked"
  scope list. It never says "safe" without saying what was and wasn't
  evaluated.
- `scripts/purlin_girt_example.py` — a worked example: a Z8x2.5 roof
  purlin checked both ways (EWM and DSM). Produces
  `purlin_check_EWM.md` and `purlin_check_DSM.md`.

## Important scope limits — read before using on a real project

1. **Section properties are not computed rigorously from raw geometry.**
   The included `flat_widths()` / `effective_Se_procedure_I()` helpers
   are a hand-check approximation (flat-width method, no corner-radius
   correction, single-pass "Procedure I" at f = Fy). For anything past
   preliminary sizing, get `A, Ix, Sx, Se_top, Se_bot, Aw`, etc. from a
   manufacturer catalog (SSMA Product Technical Guide) or a verified
   section-property tool (CUFSM, THIN-WALL) and pass them in directly —
   exactly the same discipline the AISC skill uses ("never [section
   properties] from memory").

2. **DSM requires elastic buckling values you supply.** `Pcrl/Pcrd/Pcre`
   and `Mcrl/Mcrd/Mcre` are inputs, not outputs, of `direct_strength.py`.
   Get them from a finite-strip analysis of the actual cross-section
   (CUFSM is free: https://www.ce.jhu.edu/cufsm/). This module
   deliberately does not invent a closed-form approximation for
   arbitrary C/Z geometry — that would be the least trustworthy part of
   a structural calculation if done by hand-rolled formula instead of a
   real eigenvalue solve.

3. **Lateral-torsional buckling (EWM) needs an explicit Mcr.** C and Z
   sections are singly-/point-symmetric, and AISI gives *different*
   closed-form elastic-buckling expressions for each — this module does
   not pick one for you. Either supply Mcr (from CUFSM or a verified
   hand formula for your section's actual symmetry) via
   `flexure_strength_ltb_from_Mcr()`, or confirm the member is
   continuously braced by deck/sheathing (AISI S100-16 Sec. D6) and
   skip the check — but say so on the sheet, don't silently omit it.

4. **Web crippling coefficients are not hard-coded.** `C, C_R, C_N, C_h`
   depend on support condition (end/interior, one-flange/two-flange,
   fastened/unfastened) — a large table (AISI Table C3.4.1-1 through
   -4). `web_crippling_strength()` is the equation engine; you supply
   the coefficients for your exact condition.

5. **Shear phi/Omega split by h/t range was not independently verified**
   in this session — a single conservative pair is used across all
   ranges. Confirm against Table C3.2.1-1 of your edition before a
   stamped submittal.

6. This is a **design aid, not a substitute for a licensed engineer's
   independent check and seal.** Every generated calc sheet says this
   explicitly in its footer, and lists what it did NOT evaluate.

## Relationship to AISC 360 (PEB primary frame)

A PEB's rigid-frame columns and rafters are governed by **AISC 360**
(hot-rolled/built-up), not AISI S100. If your "all members in PEB" calc
sheet set also needs primary frame members, use the `aisc-steel-design`
skill already available in this workspace (`Skill("aisc-steel-design")`)
— it has the same `Result`/`Check`/`CalcSheet` pattern, so a full PEB
package (primary frame + secondary CFS members + connections) can look
and read consistently across every member.

## Quick start

```bash
cd scripts
python3 purlin_girt_example.py
```

This prints two calc sheets to stdout and saves
`purlin_check_EWM.md` / `purlin_check_DSM.md` one level up. Edit the
`CFSSection(...)` block at the top with your actual catalog properties
and loads before trusting the output.

## Units

US customary throughout: kips, inches, ksi. `E = 29,500 ksi` (AISI's
value — note this differs from AISC's 29,000 ksi for hot-rolled).
Convert psf/plf loads to kips/in at the call site, as the example does.
