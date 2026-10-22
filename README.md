# BIMSteelAuto

**From building parameters to designed, detailed steel - inside FreeCAD.**

BIMSteelAuto is a Python engineering library and FreeCAD workbench for
Pre-Engineered Buildings (PEB) and steel portal frames. It runs the whole
chain in one pipeline:

1. **Member design** - AISC 360-16 hot-rolled frames (via
   [pyaisc360](https://github.com/ahmedalisteel-star/pyaisc360)) and
   AISI S100-16 cold-formed purlins and girts
2. **Load generation** - ASCE 7-16 load combinations and the Ch. 28
   envelope wind procedure from five site inputs (V, exposure, enclosure,
   Kzt, risk category)
3. **Connection design** - moment end plates (4E / 4ES / 8ES, AISC DG 4),
   base plates (AISC DG 1), anchors (ACI 318-19 Ch. 17), welds and
   column-side checks (AISC J2 / J10), purlin cleats and bracing gussets
4. **Calculation sheets** - every limit state with its clause reference
5. **Fabrication output** - 3D BIM model, part drawings, DXF general
   arrangement sheets, schedules and IFC export

Analysis is second-order (P-Delta) with PyNite.

## Verified against independent tools

A connection type is not marked ready for issue until it matches an
independent reference. The calc sheet prints **NOT FOR ISSUE** while any
check is still unverified.

| Module | Reference | Agreement |
|---|---|---|
| End plate 4E / 4ES / 8ES | CET.SteelConnDesign | within 0.02% (4E) |
| Anchors, all failure modes | Hilti PROFIS Engineering, ACI 318-19 | within 1.63% |
| Base plates | AISC Design Guide 1, 2nd ed. | direct |
| Wind, Ch. 28 envelope | Struware ASCE 7-16 | see tests |
| Frame analysis | anastruct (second solver) | see tests |
| Section properties | sectionproperties (FEM) | see tests |

The reference values live in `tests/fixtures/`.

## Layout

```
bimsteelauto/              engine: spec, loads, analysis, design, report (no FreeCAD import)
bimsteelauto_connections/  connection design: bolts, endplate, welds, baseplate, anchors, secondary
bimsteelauto_fc/           FreeCAD adapter: model builder, drawings, IFC, contours, clash check
BIMSteelAutoWB/            FreeCAD workbench (Build model, Design status, Output folder, Settings)
bsa/  bsa_dxf/             command line tool and DXF sheet writer
vendor/                    vendored cold-formed engine and verification references
tests/                     pytest suite, including verification against the tools above
```

## Install

Requires Python 3.12 and FreeCAD 1.1.

```powershell
git clone https://github.com/ahmedalisteel-star/bimsteelauto.git
cd bimsteelauto
python -m pip install -r requirements.txt
python -m pytest
```

To use the workbench, link `BIMSteelAutoWB` into FreeCAD's `Mod` folder and
point **Settings** at the repo folder. See `SETUP_NEW_PC.md` and
`USER_GUIDE.md`.

Quick start: copy `new_project.py`, edit the building inputs, run it.

## Status

Active development. Results are engineering aids and must be checked by a
qualified engineer before use on a real project.

## Author

**Ahmed Ali** - structural engineer and BIM specialist, AI automation for
steel structures.

## License

MIT - see `LICENSE`.
