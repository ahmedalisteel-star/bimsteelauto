# bsa - BIMSteelAuto from the command line

No GUI, no Python editing, one CSV in and the whole chain out.

```
job.csv --design--> design_result.json --build--> FCStd / IFC / DXF / MTO
```

## Start here

```
python -m bsa doctor
```

It reports which Python on this PC can run the engine, whether `freecadcmd`
is installed, and whether the workbench junction is alive. **Run it first on
any new machine** - it turns "it doesn't work here" into one table.

## The commands

| | |
|---|---|
| `python -m bsa doctor` | can this PC run it, and with which Python |
| `python -m bsa new job.csv` | write a starter sheet - every key, its default, its guidance note |
| `python -m bsa design job.csv` | CSV -> loads -> PyNite FEA -> AISC 360 -> calc sheet |
| `python -m bsa build <job folder>` | -> FCStd, IFC, GA + part drawings, DXF, MTO (headless) |
| `python -m bsa run job.csv` | both halves, one command |
| `python -m bsa export <job folder> job.csv` | recover a sheet from a past job |

Exit codes: `0` fine, `1` the sheet or the design was refused, `2` the
environment is wrong, `3` the design ran but is NOT FOR ISSUE.

## Why two environments

| | needs | does NOT need |
|---|---|---|
| engine `bimsteelauto/` | PyNiteFEA, pyaisc360, numpy, scipy | any FreeCAD |
| adapter `bimsteelauto_fc/` | FreeCAD only | any pip package |

`design` therefore runs on a laptop with no FreeCAD; `build` runs inside
`freecadcmd` with nothing installed into it. `design_result.json` is the
contract between them. `bsa build` finds `freecadcmd` and shells out, so
from the outside it is still one command.

## The job sheet

One flat CSV, five columns: `block,key,value,unit,note`.

```csv
block,key,value,unit,note
geometry,eave_height,6.0,m,Floor to underside of the eave.
geometry,roof_slope,0.10,rise/run,ABOVE 0.1228 (7 deg) the C&C coefficients change figure...
sections,column,BU-250/750x200x8/12,mm,BU-<d start>/<d end>x<bf>x<tw>/<tf>. Column runs BASE -> EAVE.
bracing,braced_bays,1|3,bay no.,Counted from 0 at the left end...
```

Notes worth knowing:

- **Geometry is in metres.** Everything else is in the unit named in the row.
- **Lists use `|`** (commas also work): `6|7.5|7.5|6`. Bays are never averaged.
- **Sections use the engine's own `BU-` mark**, the same string the calc
  sheet and the schedule print - so a section can be copied straight back
  out of a result.
- **The `note` column is guidance, not data.** Edit it freely; it is never read.
- `girt.same_as_purlin=yes` reuses the purlin and ignores the rest of that block.

## What it refuses

Inherited from the engine: a number it cannot justify is refused, never
guessed at.

- a missing required cell - **named by row number**
- an unknown block or key - a typo that is ignored is a value that is
  silently wrong (`dead.sheting` is an error, with a "did you mean")
- a section mark that is not canonical, or a tapered strut
- a braced bay that does not exist in the building
- an angle or pipe brace, because a name cannot supply `Ag`,
  `An_effective` and `r_min` - rods only
- `TODO-SUPPLY`, which is what `export` writes where a value could not be
  recovered

**Every problem in the file is reported at once, in file order.** Fixing a
sheet one exception per run is how a person gives up on a tool.

## export is lossy, and says so

A design result records what was **designed**, not what was **typed**. The
spec, geometry, materials and section marks come back exactly. The purlin's
catalogue properties, the dead load build-up and the detailing dimensions
were inputs and are not in it - those cells are marked `TODO-SUPPLY`, and
the loader refuses a sheet containing one.

A 90%-filled sheet that will not run is useful. One that runs on invented
numbers is dangerous.

## Every job keeps its own inputs

`design` copies the sheet it read into the job folder as `job.csv`, beside
the calc sheet it produced. A calc sheet whose inputs are on someone's
desktop is not a record of anything.

## Tests

```
python -m pytest tests/test_bsa_csvio.py -q
```

33 tests, most of them about refusals - each asserts the row number is in
the message.
