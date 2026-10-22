"""
bimsteelauto_fc.clash
=====================

Hard-clash check of a built model: every pair of solids whose bounding
boxes overlap is intersected, and a pair sharing more than ``tol_mm3`` of
steel is a clash.

The same test as ``REF-2026-001/03_FreeCAD_BIM/clash_check.py``, which is
the one the job was coordinated on, with one shortcut that cannot change
the answer: the shared volume can never exceed the volume of the two
bounding boxes' overlap, so a pair whose box overlap is already under the
tolerance - two parts that merely touch, a purlin flange on a rafter - is
passed without the Boolean.

The tolerance is there for touching faces and weld-size slivers. A part
that is bolted to another TOUCHES it, and a model where nothing touched
would be a model of parts floating in the air.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Clash", "TOL_MM3", "hard_clashes"]

#: mm3 - ignore touching faces and weld-size slivers.
TOL_MM3 = 50.0


@dataclass(frozen=True)
class Clash:
    a: str
    b: str
    volume_mm3: float
    at: tuple[float, float, float]

    def __str__(self) -> str:
        x, y, z = self.at
        return (f"{self.a} x {self.b}: {self.volume_mm3:.0f} mm3 at "
                f"({x:.0f}, {y:.0f}, {z:.0f})")


def _box_overlap(b1, b2) -> float:
    dx = min(b1.XMax, b2.XMax) - max(b1.XMin, b2.XMin)
    dy = min(b1.YMax, b2.YMax) - max(b1.YMin, b2.YMin)
    dz = min(b1.ZMax, b2.ZMax) - max(b1.ZMin, b2.ZMin)
    if dx <= 0 or dy <= 0 or dz <= 0:
        return 0.0
    return dx * dy * dz


def hard_clashes(objects, tol_mm3: float = TOL_MM3) -> list[Clash]:
    """Every pair of ``objects`` sharing more than ``tol_mm3`` of volume.

    ``objects`` are document objects with a ``Shape``; the label used is
    their ``Mark`` where they carry one.  A Boolean that fails is reported
    as a clash with volume -1 rather than passed: a pair the kernel could
    not intersect is a pair nobody has checked.
    """
    items = []
    for o in objects:
        sh = getattr(o, "Shape", None)
        if sh is None or sh.isNull() or not sh.Solids:
            continue
        items.append((str(getattr(o, "Mark", "") or o.Label), sh, sh.BoundBox))

    out: list[Clash] = []
    for i in range(len(items)):
        mi, si, bi = items[i]
        for j in range(i + 1, len(items)):
            mj, sj, bj = items[j]
            if _box_overlap(bi, bj) <= tol_mm3:
                continue
            try:
                common = si.common(sj)
                v = common.Volume
            except Exception:                           # noqa: BLE001
                common, v = None, -1.0
            if v > tol_mm3 or v < 0:
                c = (common.BoundBox.Center if common is not None and v > 0
                     else bi.Center)
                out.append(Clash(mi, mj, round(v, 1), (c.x, c.y, c.z)))
    return out
