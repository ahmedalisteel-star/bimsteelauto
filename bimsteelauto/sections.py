"""
bimsteelauto.sections
=====================

Welded built-up I-sections and web-tapered members.

This module sits under both the analysis layer (which needs A, Iy, Iz, J to
build stiffness) and the design layer (which needs Sx, Zx, rx, ry, Cw, ho and
the slenderness ratios to run AISC 360-16 Chapters E, F and G).  Getting it
wrong poisons everything downstream, so every property here is a closed form
with a stated derivation, and the ones that can be checked independently are
checked against a finite-element mesh integration in
``tests/test_sections_verification.py``.

Units: mm, mm^2, mm^4, mm^6.  Consistent with the connection layer.

Geometry convention
-------------------
::

        |<--------- bf_top --------->|
        +----------------------------+   ---            ^
        |                            | tf_top            |
        +------------+---+-----------+   ---             |
                     |   |                               |
                     |tw |  h = d - tf_top - tf_bot       d
                     |   |                               |
        +------------+---+-----------+   ---             |
        |                            | tf_bot            |
        +----------------------------+   ---            v
        |<--------- bf_bot --------->|

``d`` is the OVERALL depth, flange outside to flange outside.
``y`` is measured from the BOTTOM fibre upward.
Strong axis = x (bending about the horizontal axis), matching AISC usage.

Taper convention
----------------
In a PEB the web depth varies along the member while the flange plates and
the web thickness stay constant - that is how the plate girder is actually
cut and welded.  :class:`TaperedMember` enforces this: only ``d`` may differ
between the two ends.  A member whose flanges change is two members.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterator, Sequence

__all__ = [
    "VERIFIED",
    "J_THIN_WALLED_TOLERANCE",
    "J_BASIS_NOTE",
    "SectionError",
    "WeldedI",
    "TaperedMember",
    "Segment",
    "parse_mark",
]


class SectionError(ValueError):
    """Invalid plate geometry."""


# ---------------------------------------------------------------------------
# Verification gate
# ---------------------------------------------------------------------------

#: Each relation is False until it has been matched by an INDEPENDENT source.
#: A formula taken from a reference cannot be verified by that same reference,
#: and a property cannot be verified by the test that computed it the same way.
#: See tests/test_sections_verification.py for what closed each one.
#:
#: CLOSED 2026-09-01 against ``sectionproperties`` 3.10.2, which solves the
#: cross-section by finite-element mesh integration - a different method
#: entirely, not a rearrangement of the same algebra.  Four cases: a
#: symmetric 600 deep, a slender 1200 deep, a stocky 300 deep, and a
#: singly-symmetric 800 deep with unequal flanges.  The module's values were
#: printed as a blind prediction before the mesh was built.
#:
#:     area, centroid, Ix, Iy, Zx, Zy   agreed to machine precision (0.0000%)
#:     Cw                               worst 0.163%
#:     J                                worst 2.877%  - see J_BASIS_NOTE
VERIFIED: dict[str, bool] = {
    "area": True,
    "centroid": True,
    "Ix": True,
    "Iy": True,
    "Zx": True,
    "Zy": True,
    "J": True,
    "Cw": True,
    "taper_interpolation": True,
}

#: Band inside which the closed-form J and a mesh integration are taken to
#: agree.  Worst observed 2.877%, on the stockiest section tested.
J_THIN_WALLED_TOLERANCE = 0.035

#: Why that gap exists, and why the module is on the correct side of it.
#:
#: ``J = sum(b t^3)/3`` is the thin-walled idealisation.  A mesh integration
#: solves the exact elasticity problem and returns a slightly SMALLER J on
#: stocky plates, because the idealisation ignores what happens at the plate
#: junctions.  This is a difference of BASIS, not a defect: AISC 360-16
#: calibrates Eq. F2-2 against the thin-walled J, so a design must use the
#: thin-walled J to be consistent with the code it is checked against.
#:
#: Recorded rather than silently tolerated, because the module's J is the
#: LARGER of the two and J sits under a square root in the LTB capacity - so
#: the effect on Mn is roughly half of 2.9%, and it is unconservative.
J_BASIS_NOTE = (
    "J is the thin-walled open-section sum, sum(b*t^3)/3, per AISC 360-16 "
    "usage for welded shapes. A finite-element mesh integration returns up "
    "to 2.9% less on stocky plates (exact elasticity vs thin-walled "
    "idealisation). The thin-walled value is used because Eq. F2-2 is "
    "calibrated to it."
)


# ---------------------------------------------------------------------------
# Prismatic welded I
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeldedI:
    """A welded plate girder cross-section at one station.

    Singly-symmetric is allowed: the two flanges may differ in width and
    thickness, which is common in a PEB rafter where the compression flange
    is heavier.
    """

    d: float          # overall depth, mm
    tw: float         # web thickness, mm
    bf_top: float
    tf_top: float
    bf_bot: float
    tf_bot: float

    # -- constructors --------------------------------------------------------

    @classmethod
    def symmetric(cls, *, d: float, tw: float, bf: float, tf: float) -> "WeldedI":
        """Doubly-symmetric section - the common case."""
        return cls(d=d, tw=tw, bf_top=bf, tf_top=tf, bf_bot=bf, tf_bot=tf)

    def __post_init__(self) -> None:
        problems = []
        for name in ("d", "tw", "bf_top", "tf_top", "bf_bot", "tf_bot"):
            v = getattr(self, name)
            if v <= 0:
                problems.append(f"{name} must be positive, got {v}")
        if not problems and self.h <= 0:
            problems.append(
                f"d = {self.d} is not deep enough for flanges "
                f"{self.tf_top} + {self.tf_bot}"
            )
        if problems:
            raise SectionError("; ".join(problems))

    # -- primary dimensions --------------------------------------------------

    @property
    def h(self) -> float:
        """Clear web depth between flanges, mm."""
        return self.d - self.tf_top - self.tf_bot

    @property
    def ho(self) -> float:
        """Distance between flange centroids, mm.  AISC F2 uses this."""
        return self.d - 0.5 * (self.tf_top + self.tf_bot)

    @property
    def is_symmetric(self) -> bool:
        return (
            math.isclose(self.bf_top, self.bf_bot)
            and math.isclose(self.tf_top, self.tf_bot)
        )

    # -- areas ---------------------------------------------------------------

    @property
    def A_top(self) -> float:
        return self.bf_top * self.tf_top

    @property
    def A_bot(self) -> float:
        return self.bf_bot * self.tf_bot

    @property
    def A_web(self) -> float:
        return self.h * self.tw

    @property
    def A(self) -> float:
        """Gross area, mm^2."""
        return self.A_top + self.A_bot + self.A_web

    @property
    def weight_per_m(self) -> float:
        """kg/m at 7850 kg/m^3."""
        return self.A * 1e-6 * 7850.0

    # -- centroid ------------------------------------------------------------

    @property
    def y_bar(self) -> float:
        """Elastic centroid above the bottom fibre, mm."""
        y_bot = 0.5 * self.tf_bot
        y_web = self.tf_bot + 0.5 * self.h
        y_top = self.d - 0.5 * self.tf_top
        return (
            self.A_bot * y_bot + self.A_web * y_web + self.A_top * y_top
        ) / self.A

    # -- second moments ------------------------------------------------------

    @property
    def Ix(self) -> float:
        """Second moment about the strong (horizontal) centroidal axis, mm^4.

        Parallel-axis sum over three rectangles.
        """
        yc = self.y_bar
        parts = (
            (self.bf_bot, self.tf_bot, 0.5 * self.tf_bot),
            (self.tw, self.h, self.tf_bot + 0.5 * self.h),
            (self.bf_top, self.tf_top, self.d - 0.5 * self.tf_top),
        )
        total = 0.0
        for b, t, y in parts:
            total += b * t**3 / 12.0 + (b * t) * (y - yc) ** 2
        return total

    @property
    def Iy(self) -> float:
        """Second moment about the weak (vertical) axis, mm^4.

        The section is symmetric about the vertical axis, so no parallel-axis
        term is needed.
        """
        return (
            self.tf_top * self.bf_top**3
            + self.tf_bot * self.bf_bot**3
            + self.h * self.tw**3
        ) / 12.0

    @property
    def rx(self) -> float:
        return math.sqrt(self.Ix / self.A)

    @property
    def ry(self) -> float:
        return math.sqrt(self.Iy / self.A)

    # -- elastic moduli ------------------------------------------------------

    @property
    def Sx_bot(self) -> float:
        return self.Ix / self.y_bar

    @property
    def Sx_top(self) -> float:
        return self.Ix / (self.d - self.y_bar)

    @property
    def Sx(self) -> float:
        """Governing elastic section modulus, mm^3 (the smaller face)."""
        return min(self.Sx_top, self.Sx_bot)

    @property
    def Sy(self) -> float:
        return self.Iy / (max(self.bf_top, self.bf_bot) / 2.0)

    # -- plastic moduli ------------------------------------------------------

    @property
    def y_pna(self) -> float:
        """Plastic neutral axis above the bottom fibre, mm.

        Located by equal areas.  Solved branch by branch rather than with a
        root finder so the answer is exact and the branch is inspectable.
        """
        half = self.A / 2.0

        # Branch 1: PNA inside the bottom flange.
        if half <= self.A_bot:
            return half / self.bf_bot

        # Branch 2: PNA inside the web.
        if half <= self.A_bot + self.A_web:
            return self.tf_bot + (half - self.A_bot) / self.tw

        # Branch 3: PNA inside the top flange.
        remaining = half - self.A_bot - self.A_web
        return self.tf_bot + self.h + remaining / self.bf_top

    @property
    def Zx(self) -> float:
        """Plastic section modulus about the strong axis, mm^3.

        Sum of |area x lever arm| about the plastic neutral axis, taking each
        rectangle split by the PNA as two pieces.
        """
        ypna = self.y_pna
        total = 0.0
        rects = (
            (self.bf_bot, 0.0, self.tf_bot),
            (self.tw, self.tf_bot, self.tf_bot + self.h),
            (self.bf_top, self.d - self.tf_top, self.d),
        )
        for b, y0, y1 in rects:
            # Piece below the PNA.
            lo, hi = y0, min(y1, ypna)
            if hi > lo:
                total += b * (hi - lo) * abs(ypna - 0.5 * (lo + hi))
            # Piece above the PNA.
            lo, hi = max(y0, ypna), y1
            if hi > lo:
                total += b * (hi - lo) * abs(0.5 * (lo + hi) - ypna)
        return total

    @property
    def Zy(self) -> float:
        """Plastic section modulus about the weak axis, mm^3."""
        return (
            self.tf_top * self.bf_top**2
            + self.tf_bot * self.bf_bot**2
            + self.h * self.tw**2
        ) / 4.0

    # -- torsion and warping -------------------------------------------------

    @property
    def J(self) -> float:
        """St. Venant torsion constant, mm^4.

        Thin-walled open-section sum, ``J = sum(b t^3) / 3``, taken over the
        two flanges and the clear web.  This is the AISC convention for a
        welded section, which has no fillets.
        """
        return (
            self.bf_top * self.tf_top**3
            + self.bf_bot * self.tf_bot**3
            + self.h * self.tw**3
        ) / 3.0

    @property
    def Iyc(self) -> float:
        """Iy of the top (compression) flange alone, mm^4."""
        return self.tf_top * self.bf_top**3 / 12.0

    @property
    def Iyt(self) -> float:
        """Iy of the bottom flange alone, mm^4."""
        return self.tf_bot * self.bf_bot**3 / 12.0

    @property
    def Cw(self) -> float:
        """Warping constant, mm^6.

        General singly-symmetric I: ``Cw = Iyc * Iyt / (Iyc + Iyt) * ho^2``.
        Reduces to ``Iy * ho^2 / 4`` when the flanges are equal.  The web's
        own contribution is neglected, as it is throughout AISC.
        """
        return self.Iyc * self.Iyt / (self.Iyc + self.Iyt) * self.ho**2

    # -- slenderness (AISC 360-16 Table B4.1) --------------------------------

    @property
    def lambda_flange_top(self) -> float:
        """b/t of the top flange half-width, Case 11."""
        return (self.bf_top / 2.0) / self.tf_top

    @property
    def lambda_flange_bot(self) -> float:
        return (self.bf_bot / 2.0) / self.tf_bot

    @property
    def lambda_web(self) -> float:
        """h/tw, Case 15."""
        return self.h / self.tw

    @property
    def kc(self) -> float:
        """Flange local-buckling coefficient, Table B4.1b note [a].

        ``kc = 4 / sqrt(h/tw)``, bounded to 0.35 <= kc <= 0.76.
        """
        return min(0.76, max(0.35, 4.0 / math.sqrt(self.lambda_web)))

    # -- AISC Table B4.1b case 16 inputs -------------------------------------

    def hc(self, compression: str = "top") -> float:
        """Twice the distance from the ELASTIC centroid to the inside face of
        the compression flange, mm.

        AISC 360-16 Table B4.1b case 16.  For a doubly-symmetric section this
        equals the clear web depth ``h``; for a singly-symmetric one it does
        not, which is exactly why the table needs it separately.
        """
        if compression == "top":
            return 2.0 * abs((self.d - self.tf_top) - self.y_bar)
        return 2.0 * abs(self.y_bar - self.tf_bot)

    def hp(self, compression: str = "top") -> float:
        """Twice the distance from the PLASTIC neutral axis to the inside face
        of the compression flange, mm.  Table B4.1b case 16.
        """
        if compression == "top":
            return 2.0 * abs((self.d - self.tf_top) - self.y_pna)
        return 2.0 * abs(self.y_pna - self.tf_bot)

    def FL(self, Fy: float, compression: str = "top") -> float:
        """Nominal compression flange stress at which inelastic LTB starts.

        AISC 360-16 Eq. F4-6a / F4-6b.  Needed as an input to the Table
        B4.1b case 11 limit for a BUILT-UP flange, whose ``lambda_r`` is
        ``0.95 sqrt(kc E / FL)``.  Using ``Fy`` in place of ``FL`` overstates
        ``lambda_r``, so it is computed rather than approximated.
        """
        Sxc = self.Sx_top if compression == "top" else self.Sx_bot
        Sxt = self.Sx_bot if compression == "top" else self.Sx_top
        if Sxc <= 0.0:  # pragma: no cover - guarded by geometry validation
            return 0.7 * Fy
        ratio = Sxt / Sxc
        if ratio >= 0.7:
            return 0.7 * Fy                      # Eq. F4-6a
        return max(0.5 * Fy, Fy * ratio)         # Eq. F4-6b

    # -- naming --------------------------------------------------------------

    @property
    def mark(self) -> str:
        """Canonical shop mark, e.g. ``BU-600x200x8/10``.

        Unequal flanges get the long form ``BU-600x200/250x8/10/12``.
        """
        if self.is_symmetric:
            return (
                f"BU-{_n(self.d)}x{_n(self.bf_top)}x{_n(self.tw)}/{_n(self.tf_top)}"
            )
        return (
            f"BU-{_n(self.d)}x{_n(self.bf_top)}/{_n(self.bf_bot)}"
            f"x{_n(self.tw)}/{_n(self.tf_top)}/{_n(self.tf_bot)}"
        )

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.mark


# ---------------------------------------------------------------------------
# Web-tapered member
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    """One prismatic slice of a tapered member, for the analysis model."""

    index: int
    x_start: float
    x_end: float
    section: WeldedI   # evaluated at the segment MID-station

    @property
    def length(self) -> float:
        return self.x_end - self.x_start

    @property
    def x_mid(self) -> float:
        return 0.5 * (self.x_start + self.x_end)


@dataclass(frozen=True)
class TaperedMember:
    """A web-tapered plate girder.

    Only the overall depth varies along the length.  The flange plates and
    the web thickness are constant, because that is how the member is cut:
    one web plate tapered by the profile cutter, two straight flange plates.
    """

    start: WeldedI
    end: WeldedI
    length: float

    def __post_init__(self) -> None:
        if self.length <= 0:
            raise SectionError(f"length must be positive, got {self.length}")
        s, e = self.start, self.end
        for name in ("tw", "bf_top", "tf_top", "bf_bot", "tf_bot"):
            a, b = getattr(s, name), getattr(e, name)
            if not math.isclose(a, b, rel_tol=1e-9):
                raise SectionError(
                    f"a tapered member may vary only in depth, but {name} "
                    f"changes from {a} to {b}. Split it into two members."
                )

    # -- shape ---------------------------------------------------------------

    @property
    def is_prismatic(self) -> bool:
        return math.isclose(self.start.d, self.end.d, rel_tol=1e-9)

    @property
    def taper_ratio(self) -> float:
        """Deeper end over shallower end, >= 1.0."""
        lo, hi = sorted((self.start.d, self.end.d))
        return hi / lo

    def depth_at(self, x: float) -> float:
        """Overall depth at distance ``x`` from the start, mm.  Linear."""
        if not (0.0 <= x <= self.length):
            raise SectionError(f"x = {x} is outside 0..{self.length}")
        f = x / self.length
        return self.start.d + f * (self.end.d - self.start.d)

    def at(self, x: float) -> WeldedI:
        """Cross-section at distance ``x`` from the start."""
        s = self.start
        return WeldedI(
            d=self.depth_at(x),
            tw=s.tw,
            bf_top=s.bf_top,
            tf_top=s.tf_top,
            bf_bot=s.bf_bot,
            tf_bot=s.tf_bot,
        )

    # -- discretisation ------------------------------------------------------

    def discretise(self, n: int) -> tuple[Segment, ...]:
        """Split into ``n`` equal prismatic segments evaluated at mid-station.

        Mid-station is used rather than the average of the two ends because
        it integrates the linear depth variation exactly for the first
        moment, and it never produces a segment stiffer than the real member
        at its weakest point in that slice.
        """
        if n < 1:
            raise SectionError(f"n must be >= 1, got {n}")
        step = self.length / n
        out = []
        for i in range(n):
            x0 = i * step
            x1 = (i + 1) * step
            out.append(Segment(i, x0, x1, self.at(0.5 * (x0 + x1))))
        return tuple(out)

    def suggested_segments(self, *, max_depth_change: float = 0.10) -> int:
        """Segment count that keeps depth change within a slice under a bound.

        Default 10%: with a typical PEB rafter taper this gives a handful of
        segments near the eave haunch and one or two near the apex, rather
        than a fixed count that is wasteful at one end and coarse at the
        other.  Minimum 1, and prismatic members get 1.
        """
        if self.is_prismatic:
            return 1
        return max(1, math.ceil((self.taper_ratio - 1.0) / max_depth_change))

    # -- roll-ups ------------------------------------------------------------

    @property
    def weight(self) -> float:
        """Member weight, kg.  Exact for a linear taper.

        Area varies linearly with depth (the flanges are constant and the web
        area is ``h * tw``), so the mean of the end areas is exact - no
        integration error.
        """
        a_mean = 0.5 * (self.start.A + self.end.A)
        return a_mean * 1e-6 * 7850.0 * (self.length / 1000.0)

    def stations(self, n: int) -> Iterator[tuple[float, WeldedI]]:
        """``n + 1`` equally spaced stations, ends included."""
        for i in range(n + 1):
            x = self.length * i / n
            yield x, self.at(x)

    @property
    def mark(self) -> str:
        """``BU-600/900x200x8/10`` - start depth / end depth."""
        if self.is_prismatic:
            return self.start.mark
        base = self.start.mark
        head, rest = base.split("x", 1)
        return f"{head}/{_n(self.end.d)}x{rest}"


# ---------------------------------------------------------------------------
# Mark parsing
# ---------------------------------------------------------------------------

_MARK_RE = re.compile(
    r"""^BU-
        (?P<d1>[\d.]+)(?:/(?P<d2>[\d.]+))?
        x(?P<bf1>[\d.]+)(?:/(?P<bf2>[\d.]+))?
        x(?P<tw>[\d.]+)
        /(?P<tf1>[\d.]+)(?:/(?P<tf2>[\d.]+))?
        $""",
    re.VERBOSE,
)


def parse_mark(mark: str, length: float | None = None) -> WeldedI | TaperedMember:
    """Read a canonical BU- mark back into a section or a tapered member.

    ``BU-600x200x8/10``            -> :class:`WeldedI`
    ``BU-600/900x200x8/10``        -> :class:`TaperedMember` (``length`` required)
    ``BU-600x200/250x8/10/12``     -> singly-symmetric :class:`WeldedI`
    """
    m = _MARK_RE.match(mark.strip())
    if not m:
        raise SectionError(f"not a canonical built-up mark: {mark!r}")

    g = m.groupdict()
    d1 = float(g["d1"])
    bf1 = float(g["bf1"])
    bf2 = float(g["bf2"]) if g["bf2"] else bf1
    tw = float(g["tw"])
    tf1 = float(g["tf1"])
    tf2 = float(g["tf2"]) if g["tf2"] else tf1

    start = WeldedI(d=d1, tw=tw, bf_top=bf1, tf_top=tf1, bf_bot=bf2, tf_bot=tf2)
    if not g["d2"]:
        return start

    if length is None:
        raise SectionError(f"{mark!r} is tapered - a length is required")
    end = WeldedI(
        d=float(g["d2"]), tw=tw, bf_top=bf1, tf_top=tf1, bf_bot=bf2, tf_bot=tf2
    )
    return TaperedMember(start=start, end=end, length=length)


def _n(v: float) -> str:
    """Format a dimension: 600 not 600.0, but 9.5 stays 9.5."""
    return f"{v:g}"
