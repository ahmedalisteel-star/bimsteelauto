"""
bimsteelauto_fc.schedule
========================

The member schedule / MTO, taken off the model that was actually built.

Why off the MODEL and not off the DesignResult
----------------------------------------------
A take-off has to describe what exists.  The design says "one purlin line";
the model has thirty-two of them, because that is how many fit on the roof.
Counting from the design would understate the building; counting from the
model cannot, and it also catches anything the builder SKIPPED - a member
that never got built does not appear in the tonnage, which is exactly right
and is the reason the schedule and the builder are separate steps.

The one trap this module exists to avoid
----------------------------------------
``MemberDesign`` reports a purlin's ``length`` as its governing SPAN but its
``weight`` as the whole RUN.  Two different bases on one object.  The
builder resolves it when it stamps ``Weight_kg`` on each object, and this
module reads that property rather than recomputing - so the mistake can only
be made once, in one place, and there is a test that pins it.

Density is 7850 kg/m3 throughout, which is what the engine uses.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass

import FreeCAD as App

__all__ = [
    "STEEL_DENSITY",
    "ScheduleRow",
    "member_schedule",
    "schedule_totals",
    "write_schedule_csv",
    "write_schedule_sheet",
    "format_schedule",
]

#: kg/m3.  The same value the engine uses, stated here so a reader does not
#: have to go and find it.
STEEL_DENSITY: float = 7850.0

#: Which member types count as MAIN steel.  Everything else is secondary.
#: The split matters commercially - main and secondary steel are priced
#: differently - so it is stated rather than inferred from a mark prefix.
_MAIN = {"column", "interior_column", "rafter", "endwall_post", "endwall_rafter"}


@dataclass(frozen=True)
class ScheduleRow:
    """One line of the MTO: a profile, a count and a mass."""

    member_type: str
    profile: str
    count: int
    length_mm: float          # each
    mass_kg: float            # total for the count
    utilisation: float        # worst of the group

    @property
    def mass_each(self) -> float:
        return self.mass_kg / self.count if self.count else 0.0

    @property
    def is_main(self) -> bool:
        return self.member_type in _MAIN


def _model_objects(source) -> list:
    """Accept a BuildReport, a document, or a plain list of objects."""
    objs = getattr(source, "objects", None)
    if objs is not None:
        return list(objs)
    doc_objs = getattr(source, "Objects", None)
    if doc_objs is not None:
        return [o for o in doc_objs if hasattr(o, "MemberType")]
    return [o for o in source if hasattr(o, "MemberType")]


def solid_mass(obj) -> float:
    """Mass from the object's own solid, kg.

    Used only as a CROSS-CHECK against the stamped ``Weight_kg``: for a
    lofted I-section the two should agree closely, and where they do not
    (a cold-formed member modelled as its bounding box) the difference is
    real information rather than an error.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or not shape.Solids:
        return 0.0
    return shape.Volume * 1e-9 * STEEL_DENSITY          # mm3 -> m3 -> kg


def member_schedule(source) -> tuple[ScheduleRow, ...]:
    """Group every built member by type and profile.

    Sorted heaviest first, because that is the order somebody buying steel
    reads it in.
    """
    groups: dict[tuple[str, str], dict] = {}
    for obj in _model_objects(source):
        key = (str(obj.MemberType), str(obj.Profile))
        g = groups.setdefault(
            key, {"count": 0, "mass": 0.0, "length": 0.0, "u": 0.0}
        )
        g["count"] += 1
        g["mass"] += float(getattr(obj, "Weight_kg", 0.0) or 0.0)
        g["length"] = float(getattr(obj, "Length_mm", 0.0) or 0.0)
        g["u"] = max(g["u"], float(getattr(obj, "Utilisation", 0.0) or 0.0))

    rows = [
        ScheduleRow(
            member_type=mt, profile=prof, count=g["count"],
            length_mm=g["length"], mass_kg=g["mass"], utilisation=g["u"],
        )
        for (mt, prof), g in groups.items()
    ]
    return tuple(sorted(rows, key=lambda r: -r.mass_kg))


def schedule_totals(rows) -> dict[str, float]:
    """Main / secondary / total mass, kg."""
    main = sum(r.mass_kg for r in rows if r.is_main)
    secondary = sum(r.mass_kg for r in rows if not r.is_main)
    return {
        "main_kg": main,
        "secondary_kg": secondary,
        "total_kg": main + secondary,
        "pieces": float(sum(r.count for r in rows)),
    }


def format_schedule(rows, *, plan_area_m2: float = 0.0) -> str:
    """A terminal-readable MTO."""
    t = schedule_totals(rows)
    w = 74
    out = ["=" * w, "MEMBER SCHEDULE / MTO", "=" * w,
           f"{'TYPE':16s} {'PROFILE':24s} {'No':>4s} {'EACH mm':>9s} "
           f"{'MASS kg':>10s} {'U':>6s}", "-" * w]
    for r in rows:
        out.append(
            f"{r.member_type:16s} {r.profile:24s} {r.count:4d} "
            f"{r.length_mm:9.0f} {r.mass_kg:10.1f} {r.utilisation:6.3f}"
        )
    out += ["-" * w,
            f"{'MAIN STEEL':41s} {t['main_kg']:20.1f} kg",
            f"{'SECONDARY STEEL':41s} {t['secondary_kg']:20.1f} kg",
            f"{'TOTAL':41s} {t['total_kg']:20.1f} kg",
            f"{'PIECES':41s} {int(t['pieces']):20d}"]
    if plan_area_m2 > 0:
        out.append(
            f"{'STEEL PER PLAN AREA':41s} "
            f"{t['total_kg'] / plan_area_m2:20.2f} kg/m2"
        )
    out.append("=" * w)
    return "\n".join(out)


def write_schedule_csv(rows, path: str) -> str:
    """The MTO as a CSV, for the estimating workbook."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "member_type", "profile", "count", "length_mm_each",
            "mass_kg_each", "mass_kg_total", "worst_utilisation",
        ])
        for r in rows:
            w.writerow([
                r.member_type, r.profile, r.count, f"{r.length_mm:.1f}",
                f"{r.mass_each:.2f}", f"{r.mass_kg:.2f}",
                f"{r.utilisation:.3f}",
            ])
        t = schedule_totals(rows)
        w.writerow([])
        w.writerow(["MAIN_kg", f"{t['main_kg']:.2f}"])
        w.writerow(["SECONDARY_kg", f"{t['secondary_kg']:.2f}"])
        w.writerow(["TOTAL_kg", f"{t['total_kg']:.2f}"])
    return path


def write_schedule_sheet(doc, rows, *, name: str = "MTO"):
    """The MTO as a FreeCAD spreadsheet, so it lives in the document.

    Addressed by NAME rather than by link: a spreadsheet written from inside
    a recompute would re-touch its own dependency and leave the document
    permanently dirty, which is a trap the v8 macro documents having hit.
    This is written once, by a caller, outside any recompute.
    """
    sheet = getattr(doc, name, None)
    if sheet is None:
        sheet = doc.addObject("Spreadsheet::Sheet", name)
        sheet.Label = "Member schedule"

    for col, head in zip("ABCDEF", (
        "Type", "Profile", "No", "Each mm", "Mass kg", "Worst U",
    )):
        sheet.set(f"{col}1", head)

    row = 2
    for r in rows:
        sheet.set(f"A{row}", r.member_type)
        sheet.set(f"B{row}", r.profile)
        sheet.set(f"C{row}", str(r.count))
        sheet.set(f"D{row}", f"{r.length_mm:.0f}")
        sheet.set(f"E{row}", f"{r.mass_kg:.2f}")
        sheet.set(f"F{row}", f"{r.utilisation:.3f}")
        row += 1

    t = schedule_totals(rows)
    row += 1
    for label, key in (
        ("MAIN STEEL kg", "main_kg"),
        ("SECONDARY STEEL kg", "secondary_kg"),
        ("TOTAL kg", "total_kg"),
    ):
        sheet.set(f"A{row}", label)
        sheet.set(f"E{row}", f"{t[key]:.2f}")
        row += 1

    doc.recompute()
    return sheet
