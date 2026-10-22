"""
bsa_dxf.standards
=================

The drawing standard: layers, linetypes, text styles and dimension styles.

Everything a sheet draws goes on a layer named here, and every piece of text
uses a style defined here.  Nothing downstream invents a layer name or a text
height, because a drawing whose layers are invented per-sheet cannot be
filtered, plotted or imported by anyone else.

Why the numbers look the way they do
------------------------------------
Text heights, arrow sizes and line weights are quoted as they appear ON THE
PLOTTED SHEET, in millimetres, to ISO 128 / BS 8888 practice::

    2.5 mm   dimensions, member marks, table body
    3.5 mm   view titles, table headings
    5.0 mm   sheet title, drawing title
    7.0 mm   the NOT FOR ISSUE stamp

The model is drawn at 1:1 in millimetres, so every one of those has to be
multiplied by the plot scale before it is written.  That is what
``Standard.paper()`` does, and it is the reason this module takes the scale
in its constructor rather than letting each caller remember to do it.

The alternative - drawing the sheet in paper space with a viewport - is more
correct on paper and worse in practice: viewport scaling is the first thing
to break when a DXF crosses between AutoCAD, BricsCAD, Tekla and a web
viewer.  Model space at 1:1 with scaled annotation survives all of them.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "LAYERS",
    "Standard",
    "SHEET_SIZES",
    "TEXT_ISO",
    "TEXT_ISO_BOLD",
]

#: Text style names created on every document.
TEXT_ISO = "BSA-ISO"
TEXT_ISO_BOLD = "BSA-ISO-B"

#: Trimmed ISO A-series, landscape, millimetres.
SHEET_SIZES: dict[str, tuple[float, float]] = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}

#: ``name -> (aci colour, linetype, lineweight in 1/100 mm, description)``
#:
#: Names follow the AIA/ISO 13567 pattern ``S-`` (structural discipline)
#: so the set drops into a consultant's layer filter without renaming.
LAYERS: dict[str, tuple[int, str, int, str]] = {
    "S-MEMB":        (7,   "CONTINUOUS", 35, "Member outlines, visible"),
    "S-MEMB-HDDN":   (8,   "HIDDEN",     18, "Member edges hidden behind"),
    "S-MEMB-CL":     (4,   "CENTER",     13, "Member centrelines / work lines"),
    "S-MEMB-SECN":   (7,   "CONTINUOUS", 25, "Cut faces in section"),
    "S-PLAT":        (7,   "CONTINUOUS", 35, "Plates"),
    "S-PLAT-STIF":   (7,   "CONTINUOUS", 25, "Stiffeners"),
    "S-BOLT":        (1,   "CONTINUOUS", 18, "Bolts and holes"),
    "S-BOLT-CL":     (1,   "CENTER",     13, "Bolt gauge and row lines"),
    "S-WELD":        (2,   "CONTINUOUS", 18, "Weld symbols"),
    "S-SECN-PATT":   (253, "CONTINUOUS", 13, "Section hatching"),
    "S-GRID":        (4,   "CENTER2",    13, "Grid lines"),
    "S-GRID-IDEN":   (4,   "CONTINUOUS", 18, "Grid bubbles"),
    "S-ANNO-DIMS":   (3,   "CONTINUOUS", 13, "Dimensions"),
    "S-ANNO-TEXT":   (7,   "CONTINUOUS", 18, "Notes"),
    "S-ANNO-MARK":   (5,   "CONTINUOUS", 25, "Member marks and section callouts"),
    "S-ANNO-SYMB":   (6,   "CONTINUOUS", 18, "Detail bubbles, section marks, levels"),
    "S-ANNO-TABL":   (7,   "CONTINUOUS", 18, "Schedule tables"),
    "S-ANNO-STMP":   (1,   "CONTINUOUS", 35, "Issue stamp"),
    "S-SHET":        (7,   "CONTINUOUS", 50, "Sheet border"),
    "S-SHET-TTLB":   (7,   "CONTINUOUS", 25, "Title block"),
    "S-CLAD":        (8,   "CONTINUOUS", 13, "Sheeting and cladding"),
    "S-DETL":        (7,   "CONTINUOUS", 25, "Detail view geometry"),
}

#: Plotted text heights, mm on paper.
_PAPER_TEXT = {
    "dim":    2.5,
    "note":   2.5,
    "mark":   2.5,
    "table":  2.5,
    "head":   3.5,
    "title":  5.0,
    "sheet":  7.0,
    "stamp": 12.0,
    "grid":   3.5,
}


@dataclass(frozen=True)
class Standard:
    """Everything that depends on the plot scale, resolved once.

    ``scale`` is the denominator of the plot scale: 50 means 1:50.  Model
    units are millimetres throughout, so a 2.5 mm character on a 1:50 sheet
    is written 125 mm tall.
    """

    scale: float = 50.0
    sheet: str = "A1"

    # -- scale helpers ----------------------------------------------------

    def paper(self, mm: float) -> float:
        """A paper millimetre, in model units."""
        return mm * self.scale

    def text(self, role: str = "note") -> float:
        """Model-unit character height for a text role."""
        try:
            return self.paper(_PAPER_TEXT[role])
        except KeyError:  # pragma: no cover - programmer error
            raise KeyError(
                f"unknown text role {role!r}; known: "
                + ", ".join(sorted(_PAPER_TEXT))
            ) from None

    @property
    def sheet_size(self) -> tuple[float, float]:
        """Sheet width and height in MODEL units."""
        try:
            w, h = SHEET_SIZES[self.sheet]
        except KeyError:  # pragma: no cover - programmer error
            raise KeyError(
                f"unknown sheet {self.sheet!r}; known: "
                + ", ".join(SHEET_SIZES)
            ) from None
        return self.paper(w), self.paper(h)

    @property
    def margin(self) -> float:
        """Border inset, model units.  20 mm binding edge, 10 mm elsewhere."""
        return self.paper(10.0)

    @property
    def binding(self) -> float:
        return self.paper(20.0)

    # -- named dimension style -------------------------------------------

    @property
    def dimstyle(self) -> str:
        return "BSA-DIM"

    def dimstyle_attribs(self) -> dict:
        """DIMSTYLE variables, already in model units.

        Structural practice: oblique ticks rather than arrowheads, whole
        millimetres with trailing zeros suppressed, text above an unbroken
        dimension line.
        """
        t = self.text("dim")
        return {
            "dimtxsty": TEXT_ISO,
            "dimtxt": t,              # text height
            "dimasz": self.paper(2.5),  # arrow size
            "dimtsz": self.paper(1.0),  # >0 => oblique ticks, not arrows
            "dimexe": self.paper(1.25),  # extension line beyond dim line
            "dimexo": self.paper(2.0),   # extension line offset from origin
            "dimgap": self.paper(1.0),   # gap between dim line and text
            "dimdli": self.paper(8.0),   # baseline spacing
            "dimscale": 1.0,          # already scaled - do not scale again
            "dimlfac": 1.0,
            "dimdec": 0,              # whole millimetres
            "dimlunit": 2,            # decimal
            "dimzin": 8,              # suppress trailing zeros
            "dimtad": 1,              # text above the dimension line
            "dimjust": 0,             # centred
            "dimtih": 0,              # text aligned with the dimension line
            "dimtoh": 0,
            "dimclrt": 256,           # text takes the layer colour
        }


def apply(doc, std: Standard) -> None:
    """Create every layer, text style and dimension style on ``doc``.

    Safe to call twice; anything already present is left alone.
    """
    # Text styles.  ISOCP is the ISO drafting font every CAD package ships;
    # a reader without it substitutes, which is a cosmetic loss, not a
    # broken file.
    for name, font in ((TEXT_ISO, "isocp.shx"), (TEXT_ISO_BOLD, "isocpeur.ttf")):
        if name not in doc.styles:
            doc.styles.add(name, font=font)

    # Linetypes the layer table refers to.  setup=True on ezdxf.new() brings
    # most of them; CENTER2 is added if it is missing.
    if "CENTER2" not in doc.linetypes:
        doc.linetypes.add(
            "CENTER2",
            pattern=[2.0, 1.25, -0.25, 0.25, -0.25],
            description="Center (.5x) ___ _ ___ _ ___",
        )

    for name, (color, ltype, lw, desc) in LAYERS.items():
        if name in doc.layers:
            continue
        if ltype not in doc.linetypes:
            ltype = "CONTINUOUS"
        layer = doc.layers.add(
            name,
            color=color,
            linetype=ltype,
            lineweight=lw,
        )
        # Layer descriptions live in an extension dictionary, not on the
        # table entry, so they are set after the layer exists.
        try:
            layer.description = desc
        except AttributeError:      # older ezdxf - the drawing is fine without
            pass

    if std.dimstyle not in doc.dimstyles:
        ds = doc.dimstyles.add(std.dimstyle)
        for key, value in std.dimstyle_attribs().items():
            ds.set_dxf_attrib(key, value)
