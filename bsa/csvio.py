"""
bsa.csvio - the job sheet: CSV in, engine objects out.

The shape of the file
---------------------
One flat CSV, five columns::

    block,key,value,unit,note

and not a folder of tables, because a PEB input is mostly SCALAR - one
eave height, one wind speed, one bolt grade.  Laid out as tables it becomes
six files with one data row each, and the failure mode is "which file did I
forget".  Flat, it is one file to email, one file to diff, one file to keep
beside the calc sheet as the record of what was run.

The ``note`` column is the point.  ``new_project.py``'s real value is its
comments - the warning that a slope over 0.1228 changes the C&C figure, the
reminder that Se_top is a catalogue value and not a derived one.  Those
comments cannot travel in a Python file that gets copied and edited.  Here
they travel in the sheet, in Excel, next to the number they are about.

ONE schema table drives everything
----------------------------------
:data:`SCHEMA` below is the single ordered list of every accepted row.  The
template writer, the parser's typo check and the exporter all read it, so a
key cannot exist in one and not the others.  Adding an input is one line.

Refusing rather than guessing
-----------------------------
Inherited from the engine, and the reason this module is longer than a
``csv.DictReader`` loop:

* a missing required cell is an error that names the row;
* an unknown block or key is an ERROR, not a silent skip - a typo that is
  ignored is a value that is silently wrong, which is worse than a crash;
* every error in the file is collected and reported together, because
  fixing a sheet one exception per run is how a person gives up on a tool;
* ``TODO-SUPPLY`` anywhere is refused - it is what :func:`export` writes
  where a value could not be recovered.

Units
-----
Geometry is in METRES (the sheet says so, and :meth:`PEBGeometry.from_m`
does the conversion).  Everything else is in the unit named in the ``unit``
column: mm for plate, MPa for steel, kN/m2 for load.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------

# (block, key, required, example, unit, note)
#
# ``required`` False means the engine has a default and the template still
# prints the row, with that default in it - a default the user cannot see
# is a default the user cannot check.
SCHEMA = (
    ("job", "project_name", True, "PEB Warehouse - 24x36", "", "Free text. Prints on every sheet and drawing."),
    ("job", "job_number", True, "DEMO-2026-015", "", "Names the output folder. Two jobs cannot overwrite each other."),
    ("job", "designer", False, "Ahmed Ali", "", "Prints in the calc sheet title block."),
    ("job", "checker", False, "", "", "Prints in the title block. Blank is allowed and shows as blank."),

    ("geometry", "span_widths", True, "30.0", "m", "Width of each span. ONE number for a clear span; 15|15 for multi-span (then set frame_type)."),
    ("geometry", "bay_spacings", True, "6|6|6|6|6|6", "m", "Bays along the building, left to right. NOT averaged - a 7.5 m bay carries 25% more than a 6 m one. 6 bays = 7 frame lines."),
    ("geometry", "eave_height", True, "6.0", "m", "Floor to underside of the eave."),
    ("geometry", "roof_slope", True, "0.10", "rise/run", "0.10 is 1:10. 1:12=0.0833, 0.5:12=0.0417. ABOVE 0.1228 (7 deg) the C&C coefficients change figure and the engine REFUSES rather than guess."),
    ("geometry", "purlin_spacing", True, "1.25", "m", "Purlin centres, up the roof slope."),
    ("geometry", "girt_spacing", True, "2.0", "m", "Girt centres, up the wall."),
    ("geometry", "frame_type", False, "clear_span", "", "clear_span | multi_span. multi_span needs 2+ span_widths AND an interior_column section."),
    ("geometry", "roof_type", False, "gable", "", "gable | monoslope. monoslope: the C&C check refuses."),
    ("geometry", "endwall_type", False, "post_and_beam", "", "post_and_beam | rigid_frame."),

    ("site", "basic_wind_speed", True, "50.0", "m/s", "ASCE 7-16 map, 3-sec gust at 10 m, Exposure C, for the SAME risk category below. mph x 0.44704 = m/s. Outside 10-100 m/s is refused (assumed mph typed by mistake)."),
    ("site", "exposure_category", True, "C", "", "B suburban/wooded | C open country (usual) | D flat unobstructed, water upwind. ASCE 7-16 Sect. 26.7."),
    ("site", "enclosure_classification", True, "enclosed", "", "enclosed (+/-0.18) | partially_enclosed (+/-0.55, THREE times the internal pressure) | partially_open | open. Sect. 26.12."),
    ("site", "risk_category", True, "II", "", "I minor | II ordinary (usual) | III assembly/schools | IV essential. Must match the wind map V was taken from."),
    ("site", "Kzt", False, "1.0", "-", "Topographic factor, Sect. 26.8. 1.0 unless on a hill/ridge/escarpment meeting 26.8.1."),
    ("site", "Ke", False, "1.0", "-", "Ground elevation factor, Sect. 26.9. 1.0 is sea level and always permitted."),

    ("materials", "Fy_builtup", True, "345.0", "MPa", "Plate for the tapered frame. S355 / A572-50 = 345."),
    ("materials", "Fu_builtup", True, "450.0", "MPa", ""),
    ("materials", "Fy_cold_formed", True, "345.0", "MPa", "Purlins and girts."),
    ("materials", "Fu_cold_formed", True, "450.0", "MPa", ""),
    ("materials", "bolt_grade", True, "A325", "", ""),
    ("materials", "anchor_grade", True, "F1554-36", "", ""),
    ("materials", "fc_concrete", True, "30.0", "MPa", ""),

    ("sections", "column", True, "BU-250/750x200x8/12", "mm", "Canonical mark: BU-<d start>/<d end>x<bf>x<tw>/<tf>. Column runs BASE -> EAVE. A taper may vary in DEPTH only. Omit the /d2 for a prismatic member."),
    ("sections", "rafter", True, "BU-750/250x200x8/12", "mm", "Rafter runs EAVE -> RIDGE, so a haunched rafter has its DEEP end first."),
    ("sections", "interior_column", False, "", "mm", "MULTI-SPAN ONLY. Leave blank for a clear span."),
    ("sections", "strut", True, "BU-250x150x6/8", "mm", "Eave strut, checked for its longitudinal drag force. Prismatic."),

    ("purlin", "name", True, "C200x60x20x1.5", "", "FreeCAD reads the C/Z HxBxDxt pattern to size the solid. A name that does not match is SKIPPED, never guessed."),
    ("purlin", "shape", True, "C", "", "C | Z"),
    ("purlin", "t", True, "1.5", "mm", "EVERY number in this block is a CATALOGUE value from your supplier's table. Nothing is derived."),
    ("purlin", "H", True, "200.0", "mm", ""),
    ("purlin", "B", True, "60.0", "mm", ""),
    ("purlin", "Dlip", True, "20.0", "mm", ""),
    ("purlin", "A", True, "685.0", "mm2", ""),
    ("purlin", "Ix", True, "4.02e6", "mm4", ""),
    ("purlin", "Sx", True, "40200.0", "mm3", ""),
    ("purlin", "Se_top", True, "34500.0", "mm3", "EFFECTIVE section modulus, not gross. A wrong one produces a sheet that is confidently wrong."),
    ("purlin", "Se_bot", True, "34500.0", "mm3", ""),
    ("purlin", "Iy", False, "0.30e6", "mm4", ""),
    ("purlin", "Sy", False, "7000.0", "mm3", ""),
    ("purlin", "J", False, "1430.0", "mm4", ""),
    ("purlin", "Cw", False, "2.1e9", "mm6", ""),
    ("purlin", "r", False, "3.0", "mm", "Inside bend radius."),
    ("purlin", "ho", False, "200.0", "mm", "Web depth between flange mid-lines."),

    ("girt", "same_as_purlin", False, "yes", "", "yes = reuse the purlin section and ignore the rest of this block. no = fill it in like the purlin block."),
    ("girt", "name", False, "", "", ""),
    ("girt", "shape", False, "", "", "C | Z"),
    ("girt", "t", False, "", "mm", ""),
    ("girt", "H", False, "", "mm", ""),
    ("girt", "B", False, "", "mm", ""),
    ("girt", "Dlip", False, "", "mm", ""),
    ("girt", "A", False, "", "mm2", ""),
    ("girt", "Ix", False, "", "mm4", ""),
    ("girt", "Sx", False, "", "mm3", ""),
    ("girt", "Se_top", False, "", "mm3", ""),
    ("girt", "Se_bot", False, "", "mm3", ""),
    ("girt", "Iy", False, "", "mm4", ""),
    ("girt", "Sy", False, "", "mm3", ""),
    ("girt", "J", False, "", "mm4", ""),
    ("girt", "Cw", False, "", "mm6", ""),
    ("girt", "r", False, "", "mm", ""),
    ("girt", "ho", False, "", "mm", ""),

    ("dead", "sheeting", True, "0.06", "kN/m2", "Of SLOPING roof. Every line prints on the sheet INCLUDING the zeros, so a zero reads as 'considered, and it is zero' - not 'forgotten'."),
    ("dead", "insulation", False, "0.01", "kN/m2", ""),
    ("dead", "liner", False, "0.04", "kN/m2", ""),
    ("dead", "purlins", False, "0.08", "kN/m2", "Secondary steel. The FRAME's own weight is NOT here - the analysis adds it from the section properties and putting it here counts it twice."),
    ("dead", "services", False, "0.10", "kN/m2", "Lighting, sprinklers, cable tray."),
    ("dead", "collateral", False, "0.15", "kN/m2", "Future / owner allowance."),
    ("dead", "sheeting_description", False, "0.5 mm trapezoidal, pre-painted", "", ""),

    ("wall_dead", "value", True, "0.09", "kN/m2", "Wall cladding and girts, hung off the columns."),
    ("wall_dead", "surface", False, "sloping", "", "sloping | projected"),
    ("wall_dead", "label", False, "Wall cladding and girts", "", ""),
    ("wall_dead", "reference", False, "Project build-up", "", ""),

    ("bracing", "braced_bays", True, "1|3", "bay no.", "Which bays carry longitudinal bracing, counted from 0 at the left end. 6 bays are 0..5. There is NO default: a building with no longitudinal bracing has no load path for endwall wind - a MISSING design, not a zero one, and the engine refuses it."),
    ("bracing", "roof_brace", True, "M20 rod", "", "Rod sizes: 12 16 20 22 24 27 30 36 42. Rods are the usual PEB detail and are exempt from the L/r<=300 advice."),
    ("bracing", "wall_brace", True, "M24 rod", "", ""),
    ("bracing", "n_braced_walls", False, "2", "", "2 normally; 1 if one side is built against."),
    ("bracing", "roof_panel_splits", False, "2", "", "X panels per roof slope."),

    ("detailing", "bolt_diameter", True, "24.0", "mm", "Shop decisions the analysis cannot make."),
    ("detailing", "bolt_grade", True, "A325", "", ""),
    ("detailing", "plate_grade", True, "S355JR", "", ""),
    ("detailing", "anchor_grade", True, "F1554-36", "", ""),
    ("detailing", "base_plate_N", True, "600.0", "mm", "N is along the frame."),
    ("detailing", "base_plate_B", True, "450.0", "mm", ""),
    ("detailing", "anchor_diameter", True, "30.0", "mm", ""),
    ("detailing", "anchor_embedment", True, "450.0", "mm", ""),
    ("detailing", "anchor_rows", False, "2", "", ""),
    ("detailing", "anchor_cols", False, "2", "", ""),
    ("detailing", "anchor_spacing_x", False, "350.0", "mm", ""),
    ("detailing", "anchor_spacing_y", False, "350.0", "mm", ""),
    ("detailing", "anchor_edge", False, "250.0", "mm", "Concrete edge distance - drives the anchor check."),
    ("detailing", "pedestal_depth", False, "1200.0", "mm", "ASSUMED so the anchor check has a member. The foundation is a separate civil package."),
    ("detailing", "A2_over_A1", False, "1.0", "-", ""),
    ("detailing", "fc_prime", False, "30.0", "MPa", ""),

    ("settings", "frame_index", False, "", "", "Which frame line to design. BLANK = the one with the widest tributary bay, which is the most heavily loaded interior frame. 0 is the left endwall frame."),
    ("settings", "design_method", False, "LRFD", "", "LRFD | ASD"),
    ("settings", "load_code", False, "ASCE 7-16", "", "ASCE 7-16 | SBC 301-2018"),
    ("settings", "include_p_delta", False, "yes", "", "yes | no. Second-order analysis."),
)

#: block -> ordered keys, built once from SCHEMA.
BLOCKS: dict = {}
for _b, _k, *_rest in SCHEMA:
    BLOCKS.setdefault(_b, []).append(_k)

SCHEMA_BY_KEY = {(b, k): (req, ex, un, nt) for b, k, req, ex, un, nt in SCHEMA}

HEADER = ("block", "key", "value", "unit", "note")

#: What :func:`export` writes where a value cannot be recovered.  The
#: loader refuses a sheet containing it, so a half-recovered job cannot be
#: run by accident.
TODO = "TODO-SUPPLY"


class CsvError(Exception):
    """Every problem in the sheet, collected.  ``.problems`` is the list."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(f"{len(self.problems)} problem(s) in the job sheet")

    def report(self) -> str:
        out = [f"{len(self.problems)} problem(s) in the job sheet:", ""]
        out += [f"  {p}" for p in sorted(self.problems, key=_problem_order)]
        out += ["", "Nothing was run.  No value here is guessed at."]
        return "\n".join(out)


_ROW_RE = re.compile(r"^\[row (\d+)\]")


def _problem_order(msg: str):
    """Sort problems the way the file reads: top to bottom.

    Anything without a row number (a whole block the engine refused, a
    required key that never appeared at all) sorts to the end - it is a
    statement about the sheet rather than about a line of it.
    """
    m = _ROW_RE.match(msg)
    return (0, int(m.group(1)), msg) if m else (1, 0, msg)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@dataclass
class Cell:
    row: int
    value: str
    unit: str = ""


@dataclass
class Sheet:
    """The parsed file, plus the running list of what is wrong with it."""

    path: str
    cells: dict = field(default_factory=dict)     # (block, key) -> Cell
    problems: list = field(default_factory=list)
    used: set = field(default_factory=set)
    #: Keys whose row was READ but thrown away - a duplicate, or a
    #: TODO-SUPPLY marker.  The reason is already reported, so the later
    #: "required, but empty" would be the same fault said twice.  A key
    #: mistyped into nothing is NOT in here: there the second message
    #: ("dead.sheeting is required") is the one that says what to do.
    rejected: set = field(default_factory=set)

    # -- errors ------------------------------------------------------------
    def err(self, msg: str) -> None:
        self.problems.append(msg)

    def at(self, block: str, key: str, msg: str) -> None:
        c = self.cells.get((block, key))
        where = f"row {c.row}" if c else "missing row"
        self.problems.append(f"[{where}] {block}.{key}: {msg}")

    def _missing(self, block: str, key: str) -> None:
        if (block, key) in self.rejected:
            return
        self.at(block, key, "required, but the value cell is empty")

    # -- access ------------------------------------------------------------
    def raw(self, block: str, key: str) -> str:
        self.used.add((block, key))
        c = self.cells.get((block, key))
        return "" if c is None else c.value.strip()

    def has(self, block: str, key: str) -> bool:
        return bool(self.raw(block, key))

    def text(self, block: str, key: str, default: str = "", *,
             required: bool = False) -> str:
        v = self.raw(block, key)
        if not v:
            if required:
                self._missing(block, key)
            return default
        return v

    def number(self, block: str, key: str, default=None, *,
               required: bool = False, positive: bool = False):
        v = self.raw(block, key)
        if not v:
            if required:
                self._missing(block, key)
            return default
        try:
            f = float(v)
        except ValueError:
            self.at(block, key, f"{v!r} is not a number")
            return default
        if not math.isfinite(f):
            self.at(block, key, f"{v!r} is not a finite number")
            return default
        if positive and f <= 0:
            self.at(block, key, f"must be greater than zero, got {f:g}")
            return default
        return f

    def integer(self, block: str, key: str, default=None, *,
                required: bool = False):
        v = self.raw(block, key)
        if not v:
            if required:
                self._missing(block, key)
            return default
        try:
            return int(float(v))
        except ValueError:
            self.at(block, key, f"{v!r} is not a whole number")
            return default

    def flag(self, block: str, key: str, default: bool = False) -> bool:
        v = self.raw(block, key).lower()
        if not v:
            return default
        if v in ("yes", "y", "true", "1", "on"):
            return True
        if v in ("no", "n", "false", "0", "off"):
            return False
        self.at(block, key, f"{v!r} is not yes/no")
        return default

    def numbers(self, block: str, key: str, *, required: bool = False,
                positive: bool = False) -> list:
        """A ``|``-separated list.  Commas are also accepted - a person who
        types ``6, 6, 6`` has not made a mistake worth an error."""
        v = self.raw(block, key)
        if not v:
            if required:
                self._missing(block, key)
            return []
        parts = [p.strip() for p in re.split(r"[|,;]", v) if p.strip()]
        out = []
        for p in parts:
            try:
                f = float(p)
            except ValueError:
                self.at(block, key, f"{p!r} in the list is not a number")
                continue
            if positive and f <= 0:
                self.at(block, key, f"{f:g} in the list must be > 0")
                continue
            out.append(f)
        return out

    def choice(self, block: str, key: str, enum_cls, default=None, *,
               required: bool = False):
        """An enum, matched on value OR name, case-insensitively.

        ``clear_span``, ``CLEAR_SPAN`` and ``Clear Span`` all mean the same
        thing to a person, so they mean the same thing here.
        """
        v = self.raw(block, key)
        if not v:
            if required:
                self._missing(block, key)
            return default
        norm = v.strip().lower().replace(" ", "_").replace("-", "_")
        for e in enum_cls:
            if norm in (str(e.value).lower().replace(" ", "_").replace("-", "_"),
                        e.name.lower()):
                return e
        allowed = " | ".join(str(e.value) for e in enum_cls)
        self.at(block, key, f"{v!r} is not one of: {allowed}")
        return default


def read_sheet(path: str) -> Sheet:
    """Read the file into a :class:`Sheet`, checking shape but not meaning."""
    sh = Sheet(path=path)
    if not os.path.isfile(path):
        raise CsvError([f"job sheet not found: {path}"])

    with io.open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))

    if not rows:
        raise CsvError([f"{path} is empty"])

    start = 0
    first = [c.strip().lower() for c in rows[0][:2]]
    if first[:2] == ["block", "key"]:
        start = 1
    else:
        sh.err("row 1 is not the header 'block,key,value,unit,note' - "
               "the first row is being read as data")

    for i, row in enumerate(rows[start:], start=start + 1):
        if not row or all(not c.strip() for c in row):
            continue
        if row[0].lstrip().startswith("#"):
            continue
        if len(row) < 3:
            sh.err(f"[row {i}] needs at least block,key,value - got {len(row)} "
                   f"column(s): {row}")
            continue
        block = row[0].strip().lower()
        key = row[1].strip()
        value = row[2]
        unit = row[3].strip() if len(row) > 3 else ""

        if block not in BLOCKS:
            sh.err(f"[row {i}] unknown block {block!r}. "
                   f"Blocks are: {', '.join(BLOCKS)}")
            continue
        if key not in BLOCKS[block]:
            near = [k for k in BLOCKS[block] if k.lower() == key.lower()]
            hint = (f" Did you mean {near[0]!r}?" if near
                    else f" Keys in {block} are: {', '.join(BLOCKS[block])}")
            sh.err(f"[row {i}] unknown key {block}.{key!r}.{hint}")
            continue
        if (block, key) in sh.cells:
            sh.err(f"[row {i}] {block}.{key} is set twice "
                   f"(also row {sh.cells[(block, key)].row})")
            sh.rejected.add((block, key))
            continue
        if TODO in value:
            sh.err(f"[row {i}] {block}.{key} is still {TODO} - "
                   "this value could not be recovered and must be typed in")
            sh.rejected.add((block, key))
            continue
        sh.cells[(block, key)] = Cell(row=i, value=value, unit=unit)

    return sh


# ---------------------------------------------------------------------------
# Building the engine objects
# ---------------------------------------------------------------------------

#: Only a taper's start and end are wanted, and ``parse_mark`` insists on a
#: length before it will hand back a TaperedMember.  MemberSectionSpec has
#: no length - the frame geometry supplies it - so a placeholder is passed
#: and discarded.  It never reaches the analysis.
_MARK_LENGTH_PLACEHOLDER = 1000.0


def _section_spec(sh: Sheet, key: str, *, required: bool):
    from bimsteelauto.analysis import MemberSectionSpec
    from bimsteelauto.sections import SectionError, WeldedI, parse_mark

    mark = sh.text("sections", key, required=required)
    if not mark:
        return None
    try:
        parsed = parse_mark(mark, length=_MARK_LENGTH_PLACEHOLDER)
    except SectionError as exc:
        sh.at("sections", key,
              f"{exc}. Expected BU-<d>x<bf>x<tw>/<tf> for a prismatic member "
              f"or BU-<d start>/<d end>x<bf>x<tw>/<tf> for a taper.")
        return None
    if isinstance(parsed, WeldedI):
        return MemberSectionSpec.prismatic(parsed)
    return MemberSectionSpec(start=parsed.start, end=parsed.end)


def _plain_section(sh: Sheet, key: str):
    from bimsteelauto.sections import SectionError, WeldedI, parse_mark

    mark = sh.text("sections", key, required=True)
    if not mark:
        return None
    try:
        parsed = parse_mark(mark, length=_MARK_LENGTH_PLACEHOLDER)
    except SectionError as exc:
        sh.at("sections", key, str(exc))
        return None
    if not isinstance(parsed, WeldedI):
        sh.at("sections", key,
              f"{mark!r} is tapered; this member must be prismatic")
        return None
    return parsed


def _cold_formed(sh: Sheet, block: str):
    from bimsteelauto.design import CFShape, ColdFormedSection

    kw = dict(
        name=sh.text(block, "name", required=True),
        shape=sh.choice(block, "shape", CFShape, required=True),
        t=sh.number(block, "t", required=True, positive=True),
        H=sh.number(block, "H", required=True, positive=True),
        B=sh.number(block, "B", required=True, positive=True),
        Dlip=sh.number(block, "Dlip", required=True),
        A=sh.number(block, "A", required=True, positive=True),
        Ix=sh.number(block, "Ix", required=True, positive=True),
        Sx=sh.number(block, "Sx", required=True, positive=True),
        Se_top=sh.number(block, "Se_top", required=True, positive=True),
        Se_bot=sh.number(block, "Se_bot", required=True, positive=True),
    )
    for opt in ("Iy", "Sy", "J", "Cw", "r", "ho"):
        v = sh.number(block, opt)
        if v is not None:
            kw[opt] = v
    if any(v is None for v in kw.values()):
        return None
    return ColdFormedSection(**kw)


_ROD_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _brace(sh: Sheet, key: str):
    """``M20 rod`` -> a rod BraceMember, with its root area looked up.

    Only rods are read from a sheet.  An angle needs Ag, An_effective and
    r_min from a catalogue and cannot be derived from a name, so it is
    refused with the reason rather than approximated.
    """
    from bimsteelauto.design.bracing import ROD_ROOT_AREA, BraceMember, BraceType

    text = sh.text("bracing", key, required=True)
    if not text:
        return None
    if "rod" not in text.lower():
        sh.at("bracing", key,
              f"{text!r} is not a rod. The job sheet reads rods only - an "
              f"angle or pipe brace needs Ag, An_effective and r_min from a "
              f"catalogue, which a name cannot supply.")
        return None
    m = _ROD_RE.search(text)
    if not m:
        sh.at("bracing", key, f"no diameter found in {text!r}, e.g. 'M20 rod'")
        return None
    d = float(m.group(1))
    if d not in ROD_ROOT_AREA:
        sizes = " ".join(f"{s:g}" for s in sorted(ROD_ROOT_AREA))
        sh.at("bracing", key,
              f"M{d:g} has no tabulated root area. Sizes are: {sizes}")
        return None
    return BraceMember(
        name=f"M{d:g} rod", brace_type=BraceType.ROD,
        Ag=math.pi / 4.0 * d ** 2, An_effective=ROD_ROOT_AREA[d],
        r_min=d / 4.0, diameter=d,
    )


@dataclass
class JobSheet:
    """What a sheet becomes: the two engine inputs, plus the title block."""

    spec: object
    inputs: object
    designer: str = ""
    checker: str = ""
    source: str = ""


def load(path: str) -> JobSheet:
    """Read a job sheet and build the engine's inputs, or raise CsvError."""
    from bimsteelauto.connections import ConnectionDetailing
    from bimsteelauto.loads import AreaLoad, DeadLoadBuildUp, Surface
    from bimsteelauto.pipeline import BracingInputs, FrameInputs, SecondaryInputs
    from bimsteelauto.analysis import FrameSections
    from bimsteelauto.spec import (
        DesignMethod, DesignSettings, EnclosureClassification, EndwallType,
        ExposureCategory, FrameType, LoadCode, Materials, PEBGeometry,
        ProjectSite, ProjectSpec, RiskCategory, RoofType,
    )

    sh = read_sheet(path)

    # -- job ---------------------------------------------------------------
    project_name = sh.text("job", "project_name", required=True)
    job_number = sh.text("job", "job_number", required=True)
    designer = sh.text("job", "designer")
    checker = sh.text("job", "checker")

    # -- geometry (metres) -------------------------------------------------
    geometry = None
    spans = sh.numbers("geometry", "span_widths", required=True, positive=True)
    bays = sh.numbers("geometry", "bay_spacings", required=True, positive=True)
    frame_type = sh.choice("geometry", "frame_type", FrameType,
                           FrameType.CLEAR_SPAN)
    geo_kw = dict(
        eave_height=sh.number("geometry", "eave_height", required=True,
                              positive=True),
        roof_slope=sh.number("geometry", "roof_slope", required=True,
                             positive=True),
        purlin_spacing=sh.number("geometry", "purlin_spacing", required=True,
                                 positive=True),
        girt_spacing=sh.number("geometry", "girt_spacing", required=True,
                               positive=True),
        roof_type=sh.choice("geometry", "roof_type", RoofType, RoofType.GABLE),
        endwall_type=sh.choice("geometry", "endwall_type", EndwallType,
                               EndwallType.POST_AND_BEAM),
    )
    if len(spans) > 1 and frame_type is FrameType.CLEAR_SPAN:
        sh.at("geometry", "frame_type",
              f"{len(spans)} spans were given but frame_type is clear_span. "
              f"Set frame_type=multi_span and give sections.interior_column.")
    if spans and bays and all(v is not None for v in geo_kw.values()):
        try:
            geometry = PEBGeometry.from_m(
                span_widths=spans, bay_spacings=bays,
                frame_type=frame_type, **geo_kw,
            )
        except Exception as exc:                              # noqa: BLE001
            sh.err(f"[geometry] the engine refused these dimensions: {exc}")

    # -- site --------------------------------------------------------------
    site = None
    site_kw = dict(
        basic_wind_speed=sh.number("site", "basic_wind_speed", required=True,
                                   positive=True),
        exposure_category=sh.choice("site", "exposure_category",
                                    ExposureCategory, required=True),
        enclosure_classification=sh.choice("site", "enclosure_classification",
                                           EnclosureClassification,
                                           required=True),
        risk_category=sh.choice("site", "risk_category", RiskCategory,
                                required=True),
        Kzt=sh.number("site", "Kzt", 1.0),
    )
    Ke = sh.number("site", "Ke", 1.0)
    if all(v is not None for v in site_kw.values()):
        try:
            site = ProjectSite(**site_kw)
        except Exception as exc:                              # noqa: BLE001
            sh.err(f"[site] the engine refused these values: {exc}")

    # -- materials ---------------------------------------------------------
    materials = None
    mat_kw = dict(
        Fy_builtup=sh.number("materials", "Fy_builtup", required=True, positive=True),
        Fu_builtup=sh.number("materials", "Fu_builtup", required=True, positive=True),
        Fy_cold_formed=sh.number("materials", "Fy_cold_formed", required=True, positive=True),
        Fu_cold_formed=sh.number("materials", "Fu_cold_formed", required=True, positive=True),
        bolt_grade=sh.text("materials", "bolt_grade", required=True),
        anchor_grade=sh.text("materials", "anchor_grade", required=True),
        fc_concrete=sh.number("materials", "fc_concrete", required=True, positive=True),
    )
    if all(v not in (None, "") for v in mat_kw.values()):
        try:
            materials = Materials(**mat_kw)
        except Exception as exc:                              # noqa: BLE001
            sh.err(f"[materials] the engine refused these values: {exc}")

    # -- sections ----------------------------------------------------------
    column = _section_spec(sh, "column", required=True)
    rafter = _section_spec(sh, "rafter", required=True)
    interior = _section_spec(sh, "interior_column", required=False)
    strut = _plain_section(sh, "strut")
    if frame_type is FrameType.MULTI_SPAN and interior is None:
        sh.at("sections", "interior_column",
              "a multi_span frame needs an interior column section")
    sections = None
    if column is not None and rafter is not None:
        sections = FrameSections(column=column, rafter=rafter,
                                 interior_column=interior)

    # -- secondary ---------------------------------------------------------
    purlin = _cold_formed(sh, "purlin")
    if sh.flag("girt", "same_as_purlin", default=True):
        girt = purlin
        for k in BLOCKS["girt"]:
            sh.used.add(("girt", k))
    else:
        girt = _cold_formed(sh, "girt")
    secondary = (SecondaryInputs(purlin_section=purlin, girt_section=girt)
                 if purlin is not None and girt is not None else None)

    # -- loads -------------------------------------------------------------
    dead = None
    dead_kw = dict(sheeting=sh.number("dead", "sheeting", required=True))
    for k in ("insulation", "liner", "purlins", "services", "collateral"):
        v = sh.number("dead", k)
        if v is not None:
            dead_kw[k] = v
    desc = sh.text("dead", "sheeting_description")
    if desc:
        dead_kw["sheeting_description"] = desc
    if dead_kw.get("sheeting") is not None:
        try:
            dead = DeadLoadBuildUp(**dead_kw)
        except Exception as exc:                              # noqa: BLE001
            sh.err(f"[dead] the engine refused this build-up: {exc}")

    wall_dead = None
    wd_value = sh.number("wall_dead", "value", required=True)
    if wd_value is not None:
        wall_dead = AreaLoad(
            value=wd_value,
            surface=sh.choice("wall_dead", "surface", Surface, Surface.SLOPING),
            label=sh.text("wall_dead", "label", "Wall cladding and girts"),
            reference=sh.text("wall_dead", "reference", "Project build-up"),
        )

    # -- bracing -----------------------------------------------------------
    bracing = None
    bays_braced = [int(b) for b in
                   sh.numbers("bracing", "braced_bays", required=True)]
    roof_brace = _brace(sh, "roof_brace")
    wall_brace = _brace(sh, "wall_brace")
    n_walls = sh.integer("bracing", "n_braced_walls", 2)
    splits = sh.integer("bracing", "roof_panel_splits", 2)
    if bays and bays_braced:
        bad = [b for b in bays_braced if not 0 <= b < len(bays)]
        if bad:
            sh.at("bracing", "braced_bays",
                  f"bay {bad} does not exist - {len(bays)} bays are numbered "
                  f"0..{len(bays) - 1}")
            bays_braced = []
    if bays_braced and roof_brace and wall_brace and strut is not None:
        bracing = BracingInputs(
            braced_bays=tuple(bays_braced), roof_brace=roof_brace,
            wall_brace=wall_brace, strut_section=strut,
            n_braced_walls=n_walls, roof_panel_splits=splits,
        )

    # -- detailing ---------------------------------------------------------
    detailing = None
    det_kw = dict(
        bolt_diameter=sh.number("detailing", "bolt_diameter", required=True, positive=True),
        bolt_grade=sh.text("detailing", "bolt_grade", required=True),
        plate_grade=sh.text("detailing", "plate_grade", required=True),
        anchor_grade=sh.text("detailing", "anchor_grade", required=True),
        base_plate_N=sh.number("detailing", "base_plate_N", required=True, positive=True),
        base_plate_B=sh.number("detailing", "base_plate_B", required=True, positive=True),
        anchor_diameter=sh.number("detailing", "anchor_diameter", required=True, positive=True),
        anchor_embedment=sh.number("detailing", "anchor_embedment", required=True, positive=True),
    )
    for k in ("anchor_rows", "anchor_cols"):
        v = sh.integer("detailing", k)
        if v is not None:
            det_kw[k] = v
    for k in ("anchor_spacing_x", "anchor_spacing_y", "anchor_edge",
              "pedestal_depth", "A2_over_A1", "fc_prime"):
        v = sh.number("detailing", k)
        if v is not None:
            det_kw[k] = v
    if all(v not in (None, "") for v in det_kw.values()):
        try:
            detailing = ConnectionDetailing(**det_kw)
        except Exception as exc:                              # noqa: BLE001
            sh.err(f"[detailing] the engine refused these values: {exc}")

    # -- settings ----------------------------------------------------------
    frame_index = sh.integer("settings", "frame_index")
    set_kw = {}
    dm = sh.choice("settings", "design_method", DesignMethod)
    if dm is not None:
        set_kw["design_method"] = dm
    lc = sh.choice("settings", "load_code", LoadCode)
    if lc is not None:
        set_kw["load_code"] = lc
    if sh.has("settings", "include_p_delta"):
        set_kw["include_p_delta"] = sh.flag("settings", "include_p_delta", True)
    settings = DesignSettings(**set_kw)

    # -- anything in the file nobody read ----------------------------------
    # Not an error - SCHEMA is the whitelist and read_sheet already refused
    # anything outside it.  This catches a key that exists but that a branch
    # above skipped, which would be a bug in THIS module.
    for (b, k) in sh.cells:
        if (b, k) not in sh.used:
            sh.err(f"[row {sh.cells[(b, k)].row}] {b}.{k} was not read - "
                   f"this is a bug in bsa.csvio, please report it")

    if sh.problems:
        raise CsvError(sh.problems)

    spec = ProjectSpec(
        project_name=project_name, job_number=job_number,
        site=site, geometry=geometry, materials=materials, settings=settings,
    ).validate()

    inputs = FrameInputs(
        sections=sections, dead=dead, wall_dead=wall_dead,
        secondary=secondary, bracing=bracing, detailing=detailing,
        frame_index=frame_index, Ke=Ke,
    )
    return JobSheet(spec=spec, inputs=inputs, designer=designer,
                    checker=checker, source=os.path.abspath(path))


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _write(path: str, rows) -> None:
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)


def template(path: str) -> str:
    """Write a starter sheet: every key, its default, and its guidance."""
    rows = [[b, k, ex, un, nt] for b, k, req, ex, un, nt in SCHEMA]
    _write(path, rows)
    return os.path.abspath(path)


def export(result_json: str, path: str) -> tuple:
    """Recover a job sheet from a ``design_result.json``.

    LOSSY, and openly so.  The result carries the spec, the geometry, the
    materials and the section MARKS, so those come back exactly.  It does
    not carry the purlin's catalogue properties (only its name), the dead
    load build-up line by line, or the detailing dimensions - those were
    inputs, and the result records what was designed rather than what was
    typed.

    Every unrecoverable cell is written as ``TODO-SUPPLY``, and
    :func:`read_sheet` refuses a sheet containing one.  A 90%-filled sheet
    that will not run is useful; a 90%-filled sheet that runs on invented
    numbers is dangerous.

    Returns ``(path, n_recovered, n_todo)``.
    """
    with io.open(result_json, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    spec = data.get("spec") or {}
    geo = spec.get("geometry") or {}
    site = spec.get("site") or {}
    mats = spec.get("materials") or {}
    members = {m.get("mark"): m for m in data.get("members") or []}
    bracing = data.get("bracing") or {}

    def mm_to_m(v):
        return "" if v is None else f"{float(v) / 1000.0:g}"

    def joined(seq, div=1.0):
        if not seq:
            return ""
        return "|".join(f"{float(v) / div:g}" for v in seq)

    def sect(mark):
        m = members.get(mark) or {}
        sid = m.get("section_id") or ""
        return sid if sid.startswith("BU-") else (f"BU-{sid}" if sid else TODO)

    got = {
        ("job", "project_name"): spec.get("project_name", ""),
        ("job", "job_number"): spec.get("job_number", ""),
        ("geometry", "span_widths"): joined(geo.get("span_widths"), 1000.0),
        ("geometry", "bay_spacings"): joined(geo.get("bay_spacings"), 1000.0),
        ("geometry", "eave_height"): mm_to_m(geo.get("eave_height")),
        ("geometry", "roof_slope"): f"{geo.get('roof_slope', ''):g}"
                                    if geo.get("roof_slope") is not None else "",
        ("geometry", "purlin_spacing"): mm_to_m(geo.get("purlin_spacing")),
        ("geometry", "girt_spacing"): mm_to_m(geo.get("girt_spacing")),
        ("geometry", "frame_type"): geo.get("frame_type", ""),
        ("geometry", "roof_type"): geo.get("roof_type", ""),
        ("geometry", "endwall_type"): geo.get("endwall_type", ""),
        ("site", "basic_wind_speed"): site.get("basic_wind_speed", ""),
        ("site", "exposure_category"): site.get("exposure_category", ""),
        ("site", "enclosure_classification"): site.get("enclosure_classification", ""),
        ("site", "risk_category"): site.get("risk_category", ""),
        ("site", "Kzt"): site.get("Kzt", ""),
        ("materials", "Fy_builtup"): mats.get("Fy_builtup", ""),
        ("materials", "Fu_builtup"): mats.get("Fu_builtup", ""),
        ("materials", "Fy_cold_formed"): mats.get("Fy_cold_formed", ""),
        ("materials", "Fu_cold_formed"): mats.get("Fu_cold_formed", ""),
        ("materials", "bolt_grade"): mats.get("bolt_grade", ""),
        ("materials", "anchor_grade"): mats.get("anchor_grade", ""),
        ("materials", "fc_concrete"): mats.get("fc_concrete", ""),
        ("settings", "design_method"): (spec.get("settings") or {}).get("design_method", ""),
        ("settings", "load_code"): (spec.get("settings") or {}).get("load_code", ""),
        ("settings", "include_p_delta"): (
            "yes" if (spec.get("settings") or {}).get("include_p_delta") else "no"
        ),
        ("sections", "column"): sect("C1"),
        ("sections", "rafter"): sect("R1"),
        ("sections", "strut"): sect("ES1"),
        ("purlin", "name"): (members.get("P1") or {}).get("section_id", ""),
        ("girt", "name"): (members.get("G1") or {}).get("section_id", ""),
        ("bracing", "braced_bays"): joined(bracing.get("braced_bays")),
        ("bracing", "roof_brace"): (members.get("RB1") or {}).get("section_id", ""),
        ("bracing", "wall_brace"): (members.get("WB1") or {}).get("section_id", ""),
        ("bracing", "n_braced_walls"): bracing.get("n_braced_walls", ""),
        ("bracing", "roof_panel_splits"): bracing.get("roof_panel_splits", ""),
    }
    for k, v in list(got.items()):
        got[k] = "" if v is None else str(v)

    #: Recoverable-but-absent is different from not-recoverable.  These
    #: blocks were inputs and the result never carried them, so they are
    #: marked rather than left blank - blank would read as "no load".
    must_type = {"purlin", "dead", "wall_dead", "detailing"}

    # The result carries both section NAMES.  Equal names mean the job was
    # run with one section for both, which is the common case and is what
    # ``same_as_purlin`` exists for - so say yes and leave the girt block
    # alone, rather than demanding the same catalogue twice.
    same_girt = bool(got.get(("purlin", "name"))) and (
        got.get(("purlin", "name")) == got.get(("girt", "name"))
    )
    #: What :func:`_cold_formed` insists on.  Only these are marked in the
    #: girt block, and only when it has to be filled in separately.
    CF_REQUIRED = {"name", "shape", "t", "H", "B", "Dlip", "A", "Ix", "Sx",
                   "Se_top", "Se_bot"}

    rows, n_got, n_todo = [], 0, 0
    for b, k, req, ex, un, nt in SCHEMA:
        if b == "girt":
            if k == "same_as_purlin":
                rows.append([b, k, "yes" if same_girt else "no", un, nt])
                continue
            if same_girt:
                rows.append([b, k, "", un, nt])       # ignored, so left blank
                continue
            if k in CF_REQUIRED:
                have = got.get((b, k), "")
                rows.append([b, k, have or TODO, un, nt])
                n_got += 1 if have else 0
                n_todo += 0 if have else 1
                continue
            rows.append([b, k, "", un, nt])
            continue
        if (b, k) in got and got[(b, k)] != "":
            rows.append([b, k, got[(b, k)], un, nt])
            n_got += 1
            continue
        if req and b in must_type:
            rows.append([b, k, TODO, un, nt])
            n_todo += 1
            continue
        rows.append([b, k, ex if not req else TODO, un, nt])
        if req:
            n_todo += 1
    _write(path, rows)
    return os.path.abspath(path), n_got, n_todo
