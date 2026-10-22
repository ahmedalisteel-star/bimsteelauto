"""
bimsteelauto.analysis.results
=============================

Post-processing: the deflected shape, the extreme-fibre stress and the
station utilisation of a solved frame, sampled along every member and
packaged for a viewer.

    FrameRun (layout + envelope + solved model)  ->  ContourSet  ->  JSON

Why this module exists
----------------------
``Envelope`` answers the design question - what is the worst force in each
member - and throws the rest away.  A contour answers a different question:
*where* along the frame is it happening, and what shape does the frame take
when it happens.  That is the STAAD-style picture, and it needs the results
at every station, not just the governing one.

Nothing here is a new analysis.  Every number plotted is produced by code
that is already verified elsewhere in the package:

    displacement    PyNite, through the member's own local deflection
                    function, rotated into global axes by the member's
                    direction-cosine matrix
    P, V, M         the same calls ``FrameAnalysis._extract`` makes, at the
                    same stations, and checked against the envelope
    utilisation     ``bimsteelauto.design.members``, the AISC 360-16 checks

What IS new, and therefore what is verified below, is the mapping: local to
global for the deflected shape, and force to stress at a station.

Sign and unit conventions
-------------------------
Lengths and displacements are in **mm**; forces in **kN**, moments in
**kN.m**; stresses in **MPa**.

``P`` is positive in COMPRESSION, following ``StationForces``.

``sigma`` is the magnitude of the largest normal stress at the station,

    sigma = |P| / A  +  |M| / S_min

with S_min the smaller of the two elastic section moduli.  It is a
magnitude, not a signed fibre stress, because that is the number a stress
contour is read for.  The signed parts are kept alongside it as ``f_a``
(compression positive) and ``f_b`` so nothing is lost.

Geometry
--------
The frame is planar: ``X`` runs across the building width, ``Y`` is up.
The FreeCAD model built by Layer 2 places a frame-line point at
``Vector(X, y_line, Y)``; ``ContourSet.frame_y`` carries that ``y_line`` so
the contour lands on the frame it belongs to and not on grid line 1.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

from ..sections import WeldedI
from ..spec import MemberType
from .frame import Envelope, FrameAnalysis, FrameLayout

__all__ = [
    "VERIFIED",
    "ContourError",
    "Quantity",
    "MemberContour",
    "ContourSet",
    "build_contours",
    "write_contours",
    "load_contours",
    "build_space_contours",
    "READABLE_SCHEMAS",
]

SCHEMA = "bimsteelauto.contours/2"

#: Schemas this module can still READ.  /2 added Z, uZ and the three force
#: components a plane frame does not have (Vz, T, My) so the same viewer can
#: draw the whole 3D building.  The change is ADDITIVE - a /1 file has no
#: Z column, and a member loaded from one sits in its frame's own plane at
#: ``frame_y`` with the extra components at zero, which is exactly what it
#: meant.
READABLE_SCHEMAS = ("bimsteelauto.contours/1", "bimsteelauto.contours/2")


class ContourError(RuntimeError):
    """The contour could not be assembled from the run."""


# ---------------------------------------------------------------------------
# Verification gate
# ---------------------------------------------------------------------------

#: False until matched by a source that is NOT this module.
#:
#: Two relations are genuinely new here and both are checked, at build time,
#: on every run - not once in a test and then trusted:
#:
#: ``deflected_shape``
#:     the global displacement of a station is obtained from the member's
#:     LOCAL deflection functions rotated by the member's direction-cosine
#:     matrix.  At the two member ends that quantity must equal the node's
#:     global DX/DY, which PyNite reports through a completely different
#:     route (the global displacement vector, no member transform in it).
#:     Agreement to 1e-6 mm is the check; a wrong axis, a transposed matrix
#:     or a swapped sign all fail it.
#:
#: ``station_forces``
#:     P, V and M recomputed here must equal the ``Envelope`` the design
#:     layer was handed, station for station.  This is what proves the
#:     contour is drawing the forces the members were actually designed for
#:     rather than a parallel set that has drifted.
#:
#: ``extreme_fibre_stress``
#:     CLOSED 2026-09-06 against ``sectionproperties``, which meshes the
#:     cross-section and integrates it numerically - the same role it plays
#:     for ``sections.py``, and nothing it shares with the closed forms in
#:     this package.  Job 16, member C1 at x = 6000 mm, section
#:     BU-750x200x8/12 under 1.2D+1.6Lr with P = 105.8899 kN and
#:     M = 452.7479 kN.m: the module was read FIRST at sigma = 196.813713
#:     MPa, then the mesh gave A = 10 608.0000 mm2 and Zxx = 2 423 293.824
#:     mm3 against 10 608.0000 and 2 423 293.824 from the closed forms -
#:     area and section modulus exact to 0.0000%, sigma exact to 0.0000%.
#:     The script is ``_verify_stress.py``.
VERIFIED: dict[str, bool] = {
    "deflected_shape": True,
    "station_forces": True,
    "extreme_fibre_stress": True,
}

#: Tolerance on the deflected-shape check: 1e-4 mm, or one part in 1e5 of
#: the movement itself, whichever is larger.  The two routes are not
#: bit-identical - the local route evaluates a shape function at the end of
#: the member where the global route reads the node directly - and the
#: residual is a few parts in 1e5 on the P-Delta combinations, where the
#: two routes read slightly different points of the same converged
#: iteration (the worst seen on job 16 is 4e-4 mm on a 31 mm movement).  The check is here to catch a wrong
#: axis, a transposed matrix or a flipped sign, all of which are wrong by
#: order one, not to police the last bit of a float.
_TOL_MM = 1.0e-4
_TOL_REL = 1.0e-4
#: Forces are recomputed by the same calls at the same stations, so they
#: are expected to agree exactly; the tolerance is there for the last bit.
_TOL_FORCE = 1.0e-9

_N_PER_KN = 1000.0
_MM_PER_M = 1000.0


class Quantity:
    """What a station can be coloured by.

    The first three are DERIVED - they need a section, a code check or a
    deflected shape.  The six after them are the raw force components, and
    they are here because "show me the moment diagram" is the question
    people actually ask of a model.

    ``TORSION``, ``SHEAR_Z`` and ``MOMENT_Y`` are identically zero in a
    plane frame.  Asking a 2D contour set for them gives a flat line rather
    than an error, because that IS the answer a plane frame gives - it is
    a statement about the model, not a missing feature.
    """

    DISPLACEMENT = "displacement"
    STRESS = "stress"
    UTILISATION = "utilisation"

    AXIAL = "axial"
    SHEAR = "shear"
    MOMENT = "moment"
    SHEAR_Z = "shear_z"
    TORSION = "torsion"
    MOMENT_Y = "moment_y"

    #: The force components, in the order a diagram is usually read.
    FORCES = (AXIAL, SHEAR, MOMENT, SHEAR_Z, TORSION, MOMENT_Y)
    ALL = (DISPLACEMENT, STRESS, UTILISATION) + FORCES

    #: Which station column each quantity reads.
    COMPONENT = {
        AXIAL: "P", SHEAR: "V", MOMENT: "M",
        SHEAR_Z: "Vz", TORSION: "T", MOMENT_Y: "My",
    }

    #: Legend title and unit, per quantity.
    LABEL = {
        DISPLACEMENT: ("Resultant displacement", "mm"),
        STRESS: ("Extreme fibre stress", "MPa"),
        UTILISATION: ("AISC 360-16 utilisation", "-"),
        AXIAL: ("Axial force P (+ compression)", "kN"),
        SHEAR: ("Major-axis shear Vy", "kN"),
        MOMENT: ("Major-axis moment Mz", "kN.m"),
        SHEAR_Z: ("Minor-axis shear Vz", "kN"),
        TORSION: ("Torsion T", "kN.m"),
        MOMENT_Y: ("Minor-axis moment My", "kN.m"),
    }


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberContour:
    """One design member, sampled.

    Every list is the same length and in the same order - station index is
    the join between them.  Column arrays rather than a list of objects:
    a 6-member frame at 21 stations and 20 combinations is 2 500 stations,
    and the JSON is read by a viewer, not by a person.
    """

    mark: str
    member_type: str
    section_id: str
    length: float
    #: mm from the i end.
    x: tuple[float, ...]
    #: Undeformed global position, mm.  X across the frame, Y up.
    X: tuple[float, ...]
    Y: tuple[float, ...]
    #: Section depth at the station, mm - a tapered member is not one shape.
    depth: tuple[float, ...]
    #: Per combination, keyed by combination name.  Displacement is in
    #: ``uX``/``uY`` (mm, global) and its magnitude in ``d``.
    uX: dict[str, tuple[float, ...]]
    uY: dict[str, tuple[float, ...]]
    P: dict[str, tuple[float, ...]]
    V: dict[str, tuple[float, ...]]
    M: dict[str, tuple[float, ...]]
    d: dict[str, tuple[float, ...]]
    sigma: dict[str, tuple[float, ...]]
    f_a: dict[str, tuple[float, ...]]
    f_b: dict[str, tuple[float, ...]]
    ratio: dict[str, tuple[float, ...]]
    #: Schema /2, and every one of these is OPTIONAL.
    #:
    #: A plane frame has no Z and no out-of-plane force, so a /1 file
    #: carries none of them and they default to empty.  ``Z`` empty means
    #: "this member lies in its frame's plane, at the set's frame_y"; it
    #: does NOT mean Z = 0, and a viewer that read it that way would pile
    #: the whole building onto grid line 1.  Use :meth:`Z_at`.
    Z: tuple[float, ...] = ()
    uZ: dict[str, tuple[float, ...]] = field(default_factory=dict)
    Vz: dict[str, tuple[float, ...]] = field(default_factory=dict)
    T: dict[str, tuple[float, ...]] = field(default_factory=dict)
    My: dict[str, tuple[float, ...]] = field(default_factory=dict)

    @property
    def is_3d(self) -> bool:
        return bool(self.Z)

    def Z_at(self, frame_y: float) -> tuple[float, ...]:
        """Z of every station, falling back to the frame line's own Z.

        The one place the "empty means in-plane" rule is applied, so no
        caller has to remember it.
        """
        return self.Z or tuple(frame_y for _ in self.x)

    def component(self, name: str, combo: str) -> tuple[float, ...]:
        """One force component by name, zero where this member has none.

        ``Vz``, ``T`` and ``My`` are identically zero in a plane frame, and
        a viewer asking for a torsion diagram of a 2D result should get a
        flat line rather than a KeyError - the answer IS zero.
        """
        src = {"P": self.P, "V": self.V, "Vy": self.V, "M": self.M,
               "Mz": self.M, "Vz": self.Vz, "T": self.T, "My": self.My,
               "d": self.d, "sigma": self.sigma}.get(name)
        if src is None:
            raise ContourError(f"unknown component {name!r}")
        got = src.get(combo)
        if got is None:
            if name in ("Vz", "T", "My"):
                return tuple(0.0 for _ in self.x)
            raise ContourError(f"no results for combination {combo!r}")
        return got

    @property
    def n_stations(self) -> int:
        return len(self.x)

    def values(self, quantity: str, combo: str) -> tuple[float, ...]:
        if quantity == Quantity.DISPLACEMENT:
            return self.d[combo]
        if quantity == Quantity.STRESS:
            return self.sigma[combo]
        if quantity == Quantity.UTILISATION:
            if combo not in self.ratio:
                # An empty ratio is NOT a zero utilisation. It means nobody
                # graded this member, and colouring it green would be a
                # lie of exactly the kind that gets a drawing issued.
                raise ContourError(
                    f"{self.mark} carries no utilisation - it was not "
                    f"graded. See the contour set's check_notes for why."
                )
            return self.ratio[combo]
        comp = Quantity.COMPONENT.get(quantity)
        if comp is not None:
            return self.component(comp, combo)
        raise ContourError(f"unknown quantity {quantity!r}")

    def deflected(self, combo: str, scale: float) -> tuple[tuple[float, float], ...]:
        """Station positions with the displacement exaggerated ``scale`` x.

        In-plane only, and kept for the 2D viewer path. A 3D member has a
        third component and :meth:`deflected3` is the one to use.
        """
        if combo not in self.uX:
            raise ContourError(f"no results for combination {combo!r}")
        return tuple(
            (self.X[i] + self.uX[combo][i] * scale,
             self.Y[i] + self.uY[combo][i] * scale)
            for i in range(self.n_stations)
        )

    def deflected3(
        self, combo: str, scale: float, frame_y: float = 0.0
    ) -> tuple[tuple[float, float, float], ...]:
        """The deflected shape in 3D, ``(X, Y, Z)``.

        A 2D member deflects only in its own plane, so its Z is the frame
        line's Z and does not move - which is a statement the plane frame
        makes, not an omission.
        """
        if combo not in self.uX:
            raise ContourError(f"no results for combination {combo!r}")
        Zs = self.Z_at(frame_y)
        uz = self.uZ.get(combo)
        return tuple(
            (self.X[i] + self.uX[combo][i] * scale,
             self.Y[i] + self.uY[combo][i] * scale,
             Zs[i] + (uz[i] * scale if uz else 0.0))
            for i in range(self.n_stations)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mark": self.mark,
            "member_type": self.member_type,
            "section_id": self.section_id,
            "length": self.length,
            "x": list(self.x),
            "X": list(self.X),
            "Y": list(self.Y),
            "depth": list(self.depth),
            "uX": {k: list(v) for k, v in self.uX.items()},
            "uY": {k: list(v) for k, v in self.uY.items()},
            "P": {k: list(v) for k, v in self.P.items()},
            "V": {k: list(v) for k, v in self.V.items()},
            "M": {k: list(v) for k, v in self.M.items()},
            "d": {k: list(v) for k, v in self.d.items()},
            "sigma": {k: list(v) for k, v in self.sigma.items()},
            "f_a": {k: list(v) for k, v in self.f_a.items()},
            "f_b": {k: list(v) for k, v in self.f_b.items()},
            "ratio": {k: list(v) for k, v in self.ratio.items()},
            **({"Z": list(self.Z)} if self.Z else {}),
            **({"uZ": {k: list(v) for k, v in self.uZ.items()}}
               if self.uZ else {}),
            **({"Vz": {k: list(v) for k, v in self.Vz.items()}}
               if self.Vz else {}),
            **({"T": {k: list(v) for k, v in self.T.items()}}
               if self.T else {}),
            **({"My": {k: list(v) for k, v in self.My.items()}}
               if self.My else {}),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemberContour":
        def cols(key: str) -> dict[str, tuple[float, ...]]:
            return {k: tuple(v) for k, v in d.get(key, {}).items()}

        return cls(
            mark=d["mark"],
            member_type=d["member_type"],
            section_id=d["section_id"],
            length=d["length"],
            x=tuple(d["x"]),
            X=tuple(d["X"]),
            Y=tuple(d["Y"]),
            depth=tuple(d["depth"]),
            uX=cols("uX"), uY=cols("uY"),
            P=cols("P"), V=cols("V"), M=cols("M"), d=cols("d"),
            sigma=cols("sigma"), f_a=cols("f_a"), f_b=cols("f_b"),
            ratio=cols("ratio"),
            Z=tuple(d.get("Z", ())),
            uZ=cols("uZ"), Vz=cols("Vz"), T=cols("T"), My=cols("My"),
        )


@dataclass(frozen=True)
class ContourSet:
    """Every member of one frame line, sampled, for every combination."""

    project_name: str
    job_number: str
    frame_index: int
    #: Position of this frame line along the building, mm - the FreeCAD Y.
    frame_y: float
    combos: tuple[str, ...]
    governing_combo: str
    p_delta: bool
    members: tuple[MemberContour, ...]
    #: Diagnostics from the build-time checks; empty when everything matched.
    check_notes: tuple[str, ...] = ()
    schema: str = SCHEMA
    engine_version: str = ""
    #: True when this set covers the WHOLE BUILDING rather than one frame
    #: line.  The viewer needs to know: a 2D set is drawn in the plane at
    #: ``frame_y`` and a 3D set carries its own Z on every station.
    is_3d: bool = False

    # -- ranges ------------------------------------------------------------

    def range(self, quantity: str, combo: str) -> tuple[float, float]:
        """(min, max) of ``quantity`` over the whole frame, one combination."""
        vals = [
            v
            for m in self.members
            for v in m.values(quantity, combo)
        ]
        if not vals:
            raise ContourError("the contour set is empty")
        return (min(vals), max(vals))

    def peak(self, quantity: str, combo: str) -> tuple[str, float, float]:
        """(member mark, x, value) where ``quantity`` is largest."""
        best = ("", 0.0, -math.inf)
        for m in self.members:
            for i, v in enumerate(m.values(quantity, combo)):
                if v > best[2]:
                    best = (m.mark, m.x[i], v)
        return best

    def auto_scale(self, combo: str, *, target_fraction: float = 0.06) -> float:
        """A displacement magnification that makes the shape readable.

        The peak movement is drawn as ``target_fraction`` of the frame's
        diagonal, so a stiff frame and a soft one both come out looking like
        a deflected frame rather than a straight line or a scribble.
        """
        _, dmax = self.range(Quantity.DISPLACEMENT, combo)
        if dmax <= 0.0:
            return 1.0
        xs = [v for m in self.members for v in m.X]
        ys = [v for m in self.members for v in m.Y]
        diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        return max(1.0, (target_fraction * diag) / dmax)

    def member(self, mark: str) -> MemberContour:
        for m in self.members:
            if m.mark == mark:
                return m
        raise KeyError(mark)

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "engine_version": self.engine_version,
            "project_name": self.project_name,
            "job_number": self.job_number,
            "frame_index": self.frame_index,
            "frame_y": self.frame_y,
            "combos": list(self.combos),
            "governing_combo": self.governing_combo,
            "p_delta": self.p_delta,
            "check_notes": list(self.check_notes),
            "is_3d": self.is_3d,
            "members": [m.to_dict() for m in self.members],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ContourSet":
        schema = d.get("schema", "")
        if schema not in READABLE_SCHEMAS:
            raise ContourError(
                f"this file is {schema or 'unlabelled'}, and this engine "
                f"reads {' or '.join(READABLE_SCHEMAS)} - it was written by "
                f"a different version of the engine"
            )
        return cls(
            project_name=d["project_name"],
            job_number=d["job_number"],
            frame_index=d["frame_index"],
            frame_y=d["frame_y"],
            combos=tuple(d["combos"]),
            governing_combo=d["governing_combo"],
            p_delta=d["p_delta"],
            members=tuple(MemberContour.from_dict(m) for m in d["members"]),
            check_notes=tuple(d.get("check_notes", ())),
            schema=schema,
            engine_version=d.get("engine_version", ""),
            is_3d=bool(d.get("is_3d", False)),
        )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _direction_cosines(member) -> tuple[tuple[float, float, float], ...]:
    """The 3x3 rotation block of PyNite's member transformation matrix.

    Rows are the member's local axes expressed in global coordinates, so the
    transpose takes a local vector to global.  Taken from PyNite rather than
    rebuilt from the geometry: rebuilding it is exactly the guess this
    module must not make.
    """
    T = member.T()
    return tuple(tuple(float(T[r][c]) for c in range(3)) for r in range(3))


def _local_to_global(dc, v: Sequence[float]) -> tuple[float, float, float]:
    """``dc^T . v`` - local vector to global."""
    return (
        dc[0][0] * v[0] + dc[1][0] * v[1] + dc[2][0] * v[2],
        dc[0][1] * v[0] + dc[1][1] * v[1] + dc[2][1] * v[2],
        dc[0][2] * v[0] + dc[1][2] * v[1] + dc[2][2] * v[2],
    )


def _stress(P: float, M: float, section: WeldedI) -> tuple[float, float, float]:
    """(sigma, f_a, f_b) at one station, MPa.

    ``f_a`` keeps the compression-positive sign of ``P``; ``f_b`` is the
    bending stress magnitude on the more highly stressed fibre.  ``sigma``
    is their sum in magnitude, which is the peak normal stress in the
    section and the number the contour is coloured by.
    """
    A = section.A
    S = min(section.Sx_top, section.Sx_bot)
    if A <= 0.0 or S <= 0.0:  # pragma: no cover - guarded by WeldedI
        raise ContourError("section has no area or no section modulus")
    f_a = P * _N_PER_KN / A                       # kN, mm^2 -> MPa
    f_b = abs(M) * _N_PER_KN * _MM_PER_M / S      # kN.m -> N.mm -> MPa
    return abs(f_a) + f_b, f_a, f_b


def _bracing_for(member_type, length: float, geometry):
    """The same bracing the design layer assumed - not a second opinion.

    Mirrors ``pipeline.design_frame`` step 6: purlins brace a rafter, girts
    brace a column, and the unbraced length falls back to a fraction of the
    member when neither is close enough to help.
    """
    from ..design.members import BracingSpec

    if member_type is MemberType.RAFTER:
        return BracingSpec.at_spacing(length, geometry.purlin_spacing, length / 3.0)
    return BracingSpec.at_spacing(length, geometry.girt_spacing, length / 2.0)


def build_contours(
    run,
    *,
    combos: Iterable[str] | None = None,
    with_utilisation: bool = True,
) -> ContourSet:
    """Sample a solved ``FrameRun`` into a :class:`ContourSet`.

    ``run`` is what :func:`bimsteelauto.pipeline.design_frame` returns.  It
    must carry its ``analysis`` - the solved :class:`FrameAnalysis` - because
    the deflected shape lives in the solver's member functions and nowhere
    else.

    ``with_utilisation=False`` skips the AISC checks, which is most of the
    time in here; the stress and displacement contours do not need them.
    """
    analysis: FrameAnalysis | None = getattr(run, "analysis", None)
    if analysis is None or analysis.model is None:
        raise ContourError(
            "this run carries no solved model, so there is no deflected "
            "shape to draw.  Re-run design_frame with an engine of 0.9 or "
            "later."
        )
    layout: FrameLayout = run.layout
    envelope: Envelope = run.envelope
    model = analysis.model
    segments = analysis.segments
    geometry = layout.geometry

    wanted = tuple(combos) if combos is not None else tuple(envelope.combos)
    unknown = [c for c in wanted if c not in envelope.combos]
    if unknown:
        raise ContourError(f"not solved for: {', '.join(unknown)}")

    notes: list[str] = []
    members: list[MemberContour] = []
    n_default = layout.settings.stations_per_member

    for fm in layout.members:
        xs: list[float] = []
        Xs: list[float] = []
        Ys: list[float] = []
        depths: list[float] = []
        per: dict[str, dict[str, list[float]]] = {
            c: {"P": [], "V": [], "M": [], "d": [], "sigma": [],
                "f_a": [], "f_b": [], "uX": [], "uY": []}
            for c in wanted
        }

        xi, yi = fm.i_point
        xj, yj = fm.j_point
        ex, ey = (xj - xi) / fm.length, (yj - yi) / fm.length

        for mem_name, x0, x1 in segments[fm.mark]:
            mem = model.members[mem_name]
            seg_L = mem.L() if callable(mem.L) else mem.L
            dc = _direction_cosines(mem)
            k = max(3, math.ceil(n_default * (x1 - x0) / fm.length))
            for i in range(k):
                xl = seg_L * i / (k - 1)
                x_along = x0 + xl
                # A station on a segment boundary is sampled TWICE, once
                # from each side, because that is what ``_extract`` does and
                # the two lists have to line up.  Cb is computed from the
                # station sequence, so dropping the duplicates here would
                # quietly change the flexural capacity and the contour would
                # disagree with the design by a few tenths of a percent -
                # which is exactly the kind of small, plausible difference
                # nobody chases.  The duplicate makes a zero-length edge;
                # the viewer skips it.
                xs.append(x_along)
                Xs.append(xi + ex * x_along)
                Ys.append(yi + ey * x_along)
                section = fm.taper.at(min(max(x_along, 0.0), fm.length))
                depths.append(section.d)

                # The station's position is fixed; everything below it moves
                # with the combination, the deflected shape included.
                for c in wanted:
                    dl = (
                        mem.deflection("dx", xl, c),
                        mem.deflection("dy", xl, c),
                        mem.deflection("dz", xl, c),
                    )
                    gx, gy, _gz = _local_to_global(dc, dl)
                    P = mem.axial(xl, c) / _N_PER_KN
                    V = mem.shear("Fy", xl, c) / _N_PER_KN
                    M = mem.moment("Mz", xl, c) / (_N_PER_KN * _MM_PER_M)
                    sig, fa, fb = _stress(P, M, section)
                    per[c]["P"].append(P)
                    per[c]["V"].append(V)
                    per[c]["M"].append(M)
                    per[c]["d"].append(math.hypot(gx, gy))
                    per[c]["sigma"].append(sig)
                    per[c]["f_a"].append(fa)
                    per[c]["f_b"].append(fb)
                    per[c]["uX"].append(gx)
                    per[c]["uY"].append(gy)

        ratios = {c: tuple([0.0] * len(xs)) for c in wanted}
        if with_utilisation:
            ratios = _utilisations(fm, xs, per, wanted, run, notes)

        members.append(
            MemberContour(
                mark=fm.mark,
                member_type=fm.member_type.value,
                section_id=fm.taper.mark,
                length=fm.length,
                x=tuple(xs), X=tuple(Xs), Y=tuple(Ys),
                depth=tuple(depths),
                uX={c: tuple(per[c]["uX"]) for c in wanted},
                uY={c: tuple(per[c]["uY"]) for c in wanted},
                P={c: tuple(per[c]["P"]) for c in wanted},
                V={c: tuple(per[c]["V"]) for c in wanted},
                M={c: tuple(per[c]["M"]) for c in wanted},
                d={c: tuple(per[c]["d"]) for c in wanted},
                sigma={c: tuple(per[c]["sigma"]) for c in wanted},
                f_a={c: tuple(per[c]["f_a"]) for c in wanted},
                f_b={c: tuple(per[c]["f_b"]) for c in wanted},
                ratio=ratios,
            )
        )

    cs = ContourSet(
        project_name=run.spec.project_name,
        job_number=run.spec.job_number,
        frame_index=run.frame_index,
        frame_y=_frame_y(geometry, run.frame_index),
        combos=wanted,
        governing_combo=_governing_combo(run, wanted),
        p_delta=envelope.p_delta,
        members=tuple(members),
        check_notes=(),
        engine_version=getattr(run.result, "engine_version", ""),
    )

    notes += _check_ends(cs, layout, envelope, wanted)
    notes += _check_forces(cs, envelope, wanted)
    if notes:
        cs = replace(cs, check_notes=tuple(notes))
    return cs


def build_space_contours(
    layout,
    envelope,
    *,
    project_name: str = "",
    job_number: str = "",
    combos: Iterable[str] | None = None,
    engine_version: str = "",
    run=None,
) -> ContourSet:
    """Sample a solved 3D building into a :class:`ContourSet`.

    The 3D twin of :func:`build_contours`, and deliberately the same OUTPUT
    type: one viewer, one file format, one set of colour rules.  A 3D set
    differs only in carrying a Z on every station and the three force
    components a plane frame does not have.

    ``layout`` is a
    :class:`~bimsteelauto.analysis.space.SpaceLayout` and ``envelope`` the
    :class:`~bimsteelauto.analysis.space.SpaceEnvelope` from solving it.

    What is and is not filled in, and why
    -------------------------------------
    ``sigma`` is the extreme-fibre stress and it needs a real cross-section.
    The primary members have one - a ``TaperedMember``, evaluated at the
    station - so their stress is computed the same way the 2D builder
    computes it, from ``P/A`` and ``M/S`` at the station's own depth.  The
    purlins, girts and rods do NOT have a ``WeldedI``: their section is a
    catalogue name and a set of properties, and the extreme fibre of a
    lipped C is not where ``d/2`` is.  Rather than print a stress that is
    wrong by the shape factor, theirs is left at zero and the note says so.

    ``ratio`` needs ``run`` - the solved ``FrameRun``, for the materials,
    the design method and the brace points.  Given it, every PRIMARY member
    is graded on ALL SIX components: AISC Sect. F6 minor-axis flexure,
    Sect. G6 weak-axis shear, Sect. H3.3 torsional shear and Eqs. H1-1a/b
    with BOTH bending terms.  That is what turns ``My`` and ``T`` from a
    picture into a utilisation.

    Without ``run`` - or for a secondary member, which has no ``WeldedI`` to
    check - the ratio is simply ABSENT for that member rather than zero.
    The viewer then skips it and names it, because a zero utilisation is
    the calmest colour on a contour and "not graded" must not look like
    "fine".
    """
    from .space import SpaceEnvelope, SpaceLayout

    if not isinstance(layout, SpaceLayout):
        raise ContourError("build_space_contours needs a SpaceLayout")
    if not isinstance(envelope, SpaceEnvelope):
        raise ContourError("build_space_contours needs a SpaceEnvelope")

    wanted = tuple(combos) if combos is not None else tuple(envelope.combos)
    unknown = [c for c in wanted if c not in envelope.combos]
    if unknown:
        raise ContourError(f"not solved for: {', '.join(unknown)}")

    notes: list[str] = list(envelope.notes)
    members: list[MemberContour] = []
    n_secondary = 0
    n_ungraded = 0

    for key, entries in layout.segments.items():
        first = layout.member(entries[0][0])
        last = layout.member(entries[-1][0])
        total = entries[-1][2]
        taper = None
        if first.plane_mark:
            taper = layout.plane.member(first.plane_mark).taper

        sts = envelope.stations[key][wanted[0]]
        xs = tuple(st.x for st in sts)

        # Undeformed position: interpolate along the member's own line. The
        # station x is measured from the design member's i end, which is
        # exactly what the slices were laid out against, so this cannot
        # drift from the model.
        i_pt, j_pt = first.i_point, last.j_point
        Xs, Ys, Zs, depths = [], [], [], []
        for x in xs:
            f = x / total if total else 0.0
            Xs.append(i_pt[0] + (j_pt[0] - i_pt[0]) * f)
            Ys.append(i_pt[1] + (j_pt[1] - i_pt[1]) * f)
            Zs.append(i_pt[2] + (j_pt[2] - i_pt[2]) * f)
            depths.append(taper.depth_at(x) if taper is not None else 0.0)

        # Displacement is taken at the SEGMENT END NODES and interpolated
        # between them. The solver's member deflection functions are the
        # exact source and the 2D builder uses them; here the members are
        # already sliced fine enough that the node values carry the shape,
        # and reading nodes keeps this function independent of whether the
        # model object is still alive.
        node_u = _space_node_displacements(layout, envelope, key, entries,
                                           wanted, total, xs)

        per_uX, per_uY, per_uZ, per_d = {}, {}, {}, {}
        per_P, per_V, per_Vz, per_T, per_My, per_M = {}, {}, {}, {}, {}, {}
        per_sigma, per_fa, per_fb = {}, {}, {}
        for c in wanted:
            row = envelope.stations[key][c]
            per_P[c] = tuple(st.P for st in row)
            per_V[c] = tuple(st.Vy for st in row)
            per_Vz[c] = tuple(st.Vz for st in row)
            per_T[c] = tuple(st.T for st in row)
            per_My[c] = tuple(st.My for st in row)
            per_M[c] = tuple(st.Mz for st in row)
            ux, uy, uz = node_u[c]
            per_uX[c], per_uY[c], per_uZ[c] = ux, uy, uz
            per_d[c] = tuple(
                math.sqrt(a * a + b * b + cc * cc)
                for a, b, cc in zip(ux, uy, uz)
            )
            if taper is not None:
                sig, fa, fb = [], [], []
                for i, x in enumerate(xs):
                    sec = taper.at(x)
                    a, b, t = _stress(per_P[c][i], per_M[c][i], sec)
                    fa.append(a)
                    fb.append(b)
                    sig.append(t)
                per_sigma[c], per_fa[c], per_fb[c] = (
                    tuple(sig), tuple(fa), tuple(fb)
                )
            else:
                zeros = tuple(0.0 for _ in xs)
                per_sigma[c] = per_fa[c] = per_fb[c] = zeros

        # The utilisation, on all six components. Only a primary member has
        # a WeldedI to check; a purlin is designed by design/purlins.py to
        # AISI S100 and a rod by design/bracing.py, and neither belongs in
        # an AISC 360 station sweep.
        ratios: dict[str, tuple[float, ...]] = {}
        if taper is not None and run is not None:
            ratios = _space_utilisations(
                key, first.member_type, taper, xs, wanted,
                per_P, per_V, per_M, per_Vz, per_T, per_My, run, notes,
            )
        elif taper is None:
            n_secondary += 1
        elif run is None:
            n_ungraded += 1

        members.append(
            MemberContour(
                mark=key,
                member_type=first.member_type.value,
                section_id=(taper.start.mark if taper is not None
                            else first.section.name),
                length=total,
                x=xs, X=tuple(Xs), Y=tuple(Ys), depth=tuple(depths),
                uX=per_uX, uY=per_uY,
                P=per_P, V=per_V, M=per_M, d=per_d,
                sigma=per_sigma, f_a=per_fa, f_b=per_fb,
                ratio=ratios,
                Z=tuple(Zs), uZ=per_uZ, Vz=per_Vz, T=per_T, My=per_My,
            )
        )

    if n_secondary:
        notes.append(
            f"{n_secondary} secondary members carry no stress contour: "
            f"their section is a catalogue record rather than a WeldedI, "
            f"and the extreme fibre of a lipped C is not at d/2. Zero here "
            f"means NOT COMPUTED, not zero stress."
        )
    if n_ungraded:
        notes.append(
            f"{n_ungraded} primary members are NOT graded: build_space_contours "
            f"was called without run=, so there are no materials, no design "
            f"method and no brace points to check them with. Their ratio is "
            f"absent rather than zero."
        )
    elif run is not None:
        notes.append(
            "Utilisation is graded on ALL SIX components: AISC Sect. F6 "
            "minor-axis flexure, Sect. G6 weak-axis shear, Sect. H3.3 "
            "torsional shear (ST VENANT ONLY - see "
            "design.members.COVERAGE_GAPS) and Eqs. H1-1a/b with both "
            "bending terms."
        )

    return ContourSet(
        project_name=project_name,
        job_number=job_number,
        frame_index=-1,
        frame_y=0.0,
        combos=wanted,
        governing_combo=wanted[0] if wanted else "",
        p_delta=envelope.p_delta,
        members=tuple(members),
        check_notes=tuple(notes),
        engine_version=engine_version,
        is_3d=True,
    )


def _space_utilisations(
    mark, member_type, taper, xs, wanted,
    per_P, per_V, per_M, per_Vz, per_T, per_My, run, notes,
):
    """AISC utilisation at every station of one 3D member, all six actions.

    The same ``station_utilisations`` the plane frame's contour uses - so
    the two pictures are graded by one code path and cannot disagree about
    what a ratio means.  The only difference is what is handed to it: the
    plane frame fills P, Vy and Mz, and this fills all six.
    """
    from ..design.members import MemberDesignInput, station_utilisations
    from ..spec import MemberForces

    geometry = run.layout.geometry
    out: dict[str, tuple[float, ...]] = {}
    for c in wanted:
        forces = [
            MemberForces(
                P=per_P[c][i], Vy=per_V[c][i], Mz=per_M[c][i],
                Vz=per_Vz[c][i], T=per_T[c][i], My=per_My[c][i],
                station=xs[i],
            )
            for i in range(len(xs))
        ]
        inp = MemberDesignInput(
            mark=mark,
            member_type=member_type,
            taper=taper,
            forces={c: forces},
            bracing=_bracing_for(member_type, taper.length, geometry),
            materials=run.spec.materials,
            method=run.spec.settings.design_method,
        )
        try:
            out[c] = station_utilisations(inp, c)
        except Exception as exc:                        # noqa: BLE001
            notes.append(f"{mark} / {c}: utilisation not available ({exc})")
    return out


def _space_node_displacements(layout, envelope, key, entries, wanted, total, xs):
    """Station displacements, interpolated between the slice end nodes.

    The slice boundaries are where the model actually knows the
    displacement.  Between them it is interpolated linearly, which is why
    the slicing matters: a member split at every taper break and every
    purlin has boundaries every metre or two, and a straight line between
    those is not a visible approximation at any drawing scale.  It IS an
    approximation, and it is named here rather than implied.
    """
    node_x: list[float] = []
    node_names: list[str] = []
    for mem_name, x0, x1 in entries:
        m = layout.member(mem_name)
        for pt, sx in ((m.i_point, x0), (m.j_point, x1)):
            if node_names and abs(sx - node_x[-1]) < 1e-6:
                continue
            name = next(
                (n for n, p in layout.nodes.items()
                 if all(abs(a - b) < 1e-6 for a, b in zip(p, pt))),
                None,
            )
            if name is None:                             # pragma: no cover
                raise ContourError(f"no node at {pt} on {key}")
            node_x.append(sx)
            node_names.append(name)

    out: dict[str, tuple[tuple[float, ...], ...]] = {}
    for c in wanted:
        ux = [envelope.displacements[n][c][0] for n in node_names]
        uy = [envelope.displacements[n][c][1] for n in node_names]
        uz = [envelope.displacements[n][c][2] for n in node_names]
        out[c] = (
            tuple(_interp(node_x, ux, x) for x in xs),
            tuple(_interp(node_x, uy, x) for x in xs),
            tuple(_interp(node_x, uz, x) for x in xs),
        )
    return out


def _interp(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    """Linear interpolation on a sorted table, flat outside it."""
    if not xs:
        return 0.0
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            span = xs[i] - xs[i - 1]
            if span <= 0.0:                              # pragma: no cover
                return ys[i]
            f = (x - xs[i - 1]) / span
            return ys[i - 1] + (ys[i] - ys[i - 1]) * f
    return ys[-1]                                        # pragma: no cover


def _frame_y(geometry, frame_index: int) -> float:
    """Distance from grid line 1 to the designed frame line, mm."""
    spacings = list(geometry.bay_spacings)
    idx = max(0, min(frame_index, len(spacings)))
    return float(sum(spacings[:idx]))


def _governing_combo(run, wanted: Sequence[str]) -> str:
    """The combination that governed the design, if it is in the set."""
    best = ""
    worst = -1.0
    for m in run.result.members:
        r = max((c.ratio for c in m.checks), default=0.0)
        if r > worst and m.governing_combination in wanted:
            worst = r
            best = m.governing_combination
    return best or (wanted[0] if wanted else "")


def _utilisations(fm, xs, per, wanted, run, notes) -> dict[str, tuple[float, ...]]:
    """AISC 360-16 utilisation at every station, per combination."""
    from ..design.members import MemberDesignInput, station_utilisations
    from ..spec import MemberForces

    geometry = run.layout.geometry
    out: dict[str, tuple[float, ...]] = {}
    for c in wanted:
        forces = [
            MemberForces(P=per[c]["P"][i], Vy=per[c]["V"][i],
                         Mz=per[c]["M"][i], station=xs[i])
            for i in range(len(xs))
        ]
        inp = MemberDesignInput(
            mark=fm.mark,
            member_type=fm.member_type,
            taper=fm.taper,
            forces={c: forces},
            bracing=_bracing_for(fm.member_type, fm.length, geometry),
            materials=run.spec.materials,
            method=run.spec.settings.design_method,
        )
        try:
            out[c] = station_utilisations(inp, c)
        except Exception as exc:                       # noqa: BLE001
            # NOT zero. Working agreement (d): an unevaluated check must
            # never read as a pass, and on a contour a zero utilisation is
            # the calmest colour on the picture. Leaving the combination
            # out makes the viewer skip that member and say why.
            notes.append(f"{fm.mark} / {c}: utilisation not available ({exc})")
    return out


# ---------------------------------------------------------------------------
# Build-time checks - the verification gate, run on every build
# ---------------------------------------------------------------------------


def _check_ends(cs: ContourSet, layout, envelope, wanted) -> list[str]:
    """Member-end displacement against the node's own global displacement.

    Independent of the transform being checked: ``Envelope.displacements``
    comes from PyNite's node displacement vector, with no member local
    system anywhere in it.
    """
    notes: list[str] = []
    for m in cs.members:
        fm = layout.member(m.mark)
        for point, idx in ((fm.i_point, 0), (fm.j_point, m.n_stations - 1)):
            node = None
            for name, p in layout.nodes.items():
                if math.isclose(p[0], point[0], abs_tol=1e-6) and math.isclose(
                    p[1], point[1], abs_tol=1e-6
                ):
                    node = name
                    break
            if node is None:                          # pragma: no cover
                continue
            for combo in wanted:
                dx, dy = envelope.displacements[node][combo]
                ux, uy = m.uX[combo][idx], m.uY[combo][idx]
                tol = max(_TOL_MM, _TOL_REL * math.hypot(dx, dy))
                if math.hypot(dx - ux, dy - uy) > tol:
                    notes.append(
                        f"{m.mark} / {combo}: deflected shape at node "
                        f"{node} is ({ux:.6f}, {uy:.6f}) mm but the node "
                        f"reports ({dx:.6f}, {dy:.6f}) mm"
                    )
    return notes


def _check_forces(cs: ContourSet, envelope, wanted) -> list[str]:
    """Contour forces against the envelope the members were designed on.

    Station for station, not peak for peak: the two lists are sampled by
    the same rule at the same points, so anything but equality means the
    contour has drifted off the results the members were checked against.
    """
    notes: list[str] = []
    for m in cs.members:
        for c in wanted:
            theirs = envelope.stations[m.mark][c]
            if len(theirs) != m.n_stations:
                notes.append(
                    f"{m.mark} / {c}: {m.n_stations} contour stations "
                    f"against {len(theirs)} in the envelope"
                )
                continue
            for key, attr in (("M", "M"), ("V", "V"), ("P", "P")):
                mine = getattr(m, key)[c]
                for i, st in enumerate(theirs):
                    a, b = mine[i], getattr(st, attr)
                    if abs(a - b) > max(_TOL_FORCE, _TOL_FORCE * abs(b)):
                        notes.append(
                            f"{m.mark} / {c}: {key} at station {i} is "
                            f"{a:.9g} on the contour and {b:.9g} in the "
                            "envelope"
                        )
                        break
    return notes


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def write_contours(cs: ContourSet, path) -> str:
    """Write ``contours.json`` beside the design result.  Returns the path."""
    import os

    text = cs.to_json()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return os.fspath(path)


def load_contours(path) -> ContourSet:
    with open(path, encoding="utf-8") as fh:
        return ContourSet.from_dict(json.load(fh))
