"""
Solve the demo building in 3D and write the result file FreeCAD draws.

    python run_space_example.py

writes ``out/contours3d.json``.  Then, in the FreeCAD GUI:

    BIMSteelAuto  ->  Result contours  ->  pick out/contours3d.json

and choose what to colour by - displacement, stress, or any force
component: P, Vy, Vz, Mz, My, T.

Why this is a separate script from ``run_frame_example.py``
----------------------------------------------------------
Because it is a separate model, and it costs about forty seconds.  The
plane frame is what the MEMBERS ARE DESIGNED ON - it is verified,
second-order, and it runs in a second.  The 3D building is a second view of
the same design: every frame line, the purlins, the girts, the struts and
the tension-only bracing, solved together so the quantities a plane frame
cannot have - out-of-plane shear, minor-axis moment, torsion, and drift
ALONG the building - actually exist.

Every primary member IS graded, on all six components: AISC Sect. F6
minor-axis flexure, Sect. G6 weak-axis shear, Sect. H3.3 torsional shear
and Eqs. H1-1a/b with both bending terms.  So the contour can be coloured
by utilisation and the number means what it says.

What it does NOT do is change the DESIGN.  The calculation sheet is still
written by ``run_frame_example.py`` off the plane frame, which is the
verified, second-order model the members are sized on.  This is a second
opinion, not a replacement, and where the two differ that is information
rather than an error.

What to expect on the demo building
-----------------------------------
The out-of-plane numbers are SMALL - torsion around 0.01 kN.m, minor-axis
moment a couple of kN.m - so the biaxial and torsion terms barely move the
utilisation.  That is the correct answer for a properly braced PEB: the
purlins and the girts restrain the frames, which is what they are there
for.  A large torsion in a primary member would be a finding, not a
success, and an OPEN SECTION HAS ALMOST NO TORSIONAL CAPACITY - a rafter
picking up a few kN.m of torsion will fail on it.
"""

from __future__ import annotations

import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "vendor"))

from bimsteelauto.analysis import (  # noqa: E402
    build_space_contours,
    build_space_from_run,
    write_contours,
)
from bimsteelauto.pipeline import design_frame  # noqa: E402


def main(out_dir: pathlib.Path | None = None) -> pathlib.Path:
    # The same spec and inputs the plane-frame example uses - importing it
    # rather than restating it, so the two examples cannot describe
    # different buildings.
    import run_frame_example as ex

    out_dir = out_dir or (HERE / "out")
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    run = design_frame(ex.spec, ex.inputs)
    layout, analysis = build_space_from_run(run)

    print()
    print("=" * 70)
    print("3D BUILDING MODEL")
    print("=" * 70)
    print(f"  frame lines        : {len(layout.frame_lines)}")
    print(f"  nodes              : {len(layout.nodes)}")
    print(f"  members            : {len(layout.members)}")
    print(f"  design members     : {len(layout.segments)}")
    print(f"  braced bays        : {list(layout.braced_bays)}")
    for note in layout.notes:
        print(f"    - {note}")

    envelope = analysis.solve()

    print()
    print(f"  combinations       : {len(envelope.combos)}")
    print(f"  P-Delta            : {envelope.p_delta}")
    print(f"  tension-only bracing: {envelope.tension_only}")
    print()
    print("  WORST OF EACH COMPONENT, over the whole building:")
    units = {"P": "kN", "Vy": "kN", "Vz": "kN",
             "T": "kN.m", "My": "kN.m", "Mz": "kN.m"}
    for attr, unit in units.items():
        mark, combo, value = envelope.worst(attr)
        print(f"    {attr:3s} {value:12.3f} {unit:5s} {mark:16s} {combo}")

    worst_node = worst_val = None
    for node, per in envelope.displacements.items():
        for combo, d in per.items():
            mag = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
            if worst_val is None or mag > worst_val[0]:
                worst_val = (mag, combo)
                worst_node = node
    if worst_val is not None:
        print(f"    max resultant displacement {worst_val[0]:.1f} mm at "
              f"{worst_node} under {worst_val[1]}")

    contours = build_space_contours(
        layout, envelope,
        project_name=run.spec.project_name,
        job_number=run.spec.job_number,
        engine_version=getattr(run.result, "engine_version", ""),
        # run= is what lets every primary member be GRADED - on all six
        # components, not the three a plane frame has.
        run=run,
    )

    worst_u = ("", "", 0.0)
    for m in contours.members:
        for combo, vals in m.ratio.items():
            if vals and max(vals) > worst_u[2]:
                worst_u = (m.mark, combo, max(vals))
    if worst_u[0]:
        print()
        print(f"  WORST UTILISATION {worst_u[2]:.3f}  {worst_u[0]}  "
              f"{worst_u[1]}")
    path = write_contours(contours, out_dir / "contours3d.json")

    print()
    for note in contours.check_notes:
        print(f"  ! {note}")
    print()
    print(f"  {time.time() - t0:.1f} s")
    print(f"  wrote {path}")
    print()
    print("  FreeCAD -> BIMSteelAuto -> Result contours -> this file.")
    return pathlib.Path(path)


if __name__ == "__main__":
    main()
