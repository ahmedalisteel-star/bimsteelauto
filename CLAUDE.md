# Building a project from text - instructions for Claude

This repo is BIMSteelAuto: a PEB / steel portal frame design engine plus a
FreeCAD workbench. When the user describes a building in words, turn it
into a designed, modelled and drawn job by following these steps.

## 1. Read the description into a job file

Copy `new_project.py` to `job_<JOB_NUMBER>.py` (job files are gitignored)
and fill its six `==== EDIT n of 6 ====` blocks. Change nothing outside
those blocks.

| Block | What goes in it |
|---|---|
| 1 THE JOB | project name, job number, designer, checker |
| 2 THE GEOMETRY | span(s), bay spacings, eave height, roof slope (rise/run), purlin and girt spacing, gable or monoslope |
| 3 THE 5 WIND INPUTS + SITE | V in **m/s**, exposure, enclosure, risk category, Kzt; materials |
| 4 THE SECTIONS | frame member sizes, purlins, girts |
| 5 THE LOADS | dead, collateral, live, cladding |
| 6 BRACING + DETAILING | braced bays, rod sizes, connection detailing |

Units are metres, kN and MPa. A slope of "1:10" is `0.10`. A "24x36
building" is a 24 m span and 36 m length - split the length into bays
(e.g. 6 x 6 m) and say which split you chose.

## 2. Never guess the wind inputs

The five wind inputs (V, exposure, enclosure, risk category, Kzt) change
the answer. If the user did not give one, **ask** - do not fill in a
default silently. If they give V in mph, convert (m/s = mph x 0.44704) and
say so. Everything else you assume (bay split, girt spacing, cladding,
loads) must be listed back to the user as an assumption.

## 3. Run the design and iterate the sections

```powershell
python job_<JOB_NUMBER>.py
```

This writes `jobs/<JOB_NUMBER>/calc_sheet.html` and `design_result.json`.
The program **checks** sections, it does not choose them. If a member
fails, increase it in EDIT 4 and run again until every member passes
(shop minimums: web 6 mm, flange 8 mm). Report what you changed.

## 4. Build the model, drawings and MTO in FreeCAD

If the `freecad` MCP server is connected (see `mcp/README.md`), call
`freecad_ping`, then `freecad_execute_python` with:

```python
import sys
REPO = r"<absolute path of this repo>"
for p in (REPO, REPO + r"\vendor"):
    sys.path.insert(0, p) if p not in sys.path else None
from bimsteelauto_fc.run import build_all
run = build_all(REPO + r"\jobs\<JOB_NUMBER>\design_result.json",
                REPO + r"\jobs\<JOB_NUMBER>\out")
print(run.summary())
```

The user sees the model appear live. Without MCP, run headless instead:
`freecadcmd bsa_build.py jobs/<JOB_NUMBER>`.

Outputs in `jobs/<JOB_NUMBER>/out/`: the `.FCStd` BIM model, `.ifc`,
`schedule.csv` (the MTO), GA sheets and single-part drawings as SVG and
DXF. The workbench's **Result contours** command draws displacement,
stress and utilisation on the model.

## 5. Report back

Give the user: the governing utilisation per member, total steel tonnage
from the MTO, clash count (must be 0), whether any sheet says
**NOT FOR ISSUE**, every assumption you made, and where the files are.

## Rules

- Never edit `bimsteelauto/`, `bimsteelauto_connections/` or
  `bimsteelauto_fc/` to make a member pass. Change the job file.
- Never set a `VERIFIED` flag to True. Those flags are closed only by a
  match against an independent tool, recorded in `tests/fixtures/`.
- If the program refuses an input, report the refusal; do not work around
  it.
- Results are engineering aids. A qualified engineer must check them.
