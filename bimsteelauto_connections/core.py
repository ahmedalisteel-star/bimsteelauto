"""
core.py — The one contract every connection check speaks.
=========================================================

Six modules grew independently and each returned its own result shape.
This collapses them onto a single protocol so that ONE renderer produces
every calculation sheet, and so a caller can loop over a whole frame's
connections without knowing which kind each one is.

    DesignForce       task #4: max(analysis force, % of member capacity)
    LimitState        one check: name, capacity, demand, clause
    ConnectionResult  the unified shape every designer returns
    Connection        the protocol a designer must satisfy

Units: SI — kN, mm, MPa, kNm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, Iterable, Protocol, runtime_checkable

from .bolts import ConnectionError_, GeometryError


# ======================================================================
# Task #4 — design force
# ======================================================================

# Project design basis, confirmed by Ahmed Ali 2026-08-30.
# A connection is designed for the LARGER of the analysis force and this
# fraction of the connected member's design capacity. It is a named
# constant, not a default buried in a signature, so that a project with a
# different spec changes it in one visible place -- and so a calculation
# sheet can state where the number came from.
CAPACITY_FRACTION: Final[float] = 0.50
CAPACITY_FRACTION_SOURCE: Final[str] = (
    "client project specification: connections to develop not less "
    "than 50% of the connected member's design capacity (confirmed "
    "2026-08-30)"
)


class ForceCriterion(str, Enum):
    ANALYSIS = "analysis force"
    CAPACITY = "% of member capacity"


@dataclass(frozen=True, slots=True)
class DesignForce:
    """The force a connection is designed for, and which rule set it.

    Per the agreed project decision the design force is the **larger** of
    the analysis force and a minimum percentage of the connected member's
    capacity.  Both are carried so the calculation sheet can show why.
    """

    value: float                 # kN or kNm
    analysis: float
    capacity_based: float | None
    criterion: ForceCriterion
    fraction: float | None       # e.g. 0.50
    label: str = ""              # "Mu", "Vu", "Pu"
    unit: str = "kN"

    def report(self) -> str:
        if self.capacity_based is None:
            return (f"  {self.label:<4s} = {self.value:9.2f} {self.unit}"
                    f"   (analysis only — no capacity minimum set)")
        pct = f"{self.fraction * 100:.0f}%" if self.fraction else "-"
        return (
            f"  {self.label:<4s} = {self.value:9.2f} {self.unit}   "
            f"[analysis {self.analysis:.2f} vs {pct} capacity "
            f"{self.capacity_based:.2f}] -> {self.criterion.value}"
        )


def design_force(
    *,
    analysis: float,
    member_capacity: float | None = None,
    fraction: float | None = None,
    label: str = "",
    unit: str = "kN",
) -> DesignForce:
    """Larger of the analysis force and a fraction of member capacity.

    Parameters
    ----------
    analysis:
        Force from the Pynite load-combination envelope at the node.
    member_capacity:
        Design capacity of the connected member in the same action
        (phi*Mn, phi*Vn, phi*Pn).
    fraction:
        Minimum fraction of member capacity the connection must carry.
        Pass :data:`CAPACITY_FRACTION` (0.50 on this project) for the
        standard basis, or an explicit value for a project with a
        different spec.  When ``None`` the analysis force is used alone
        and the result says so, rather than silently assuming a value —
        the constant exists so the number is stated, not guessed.

    Raises
    ------
    GeometryError
        Negative analysis force, or a fraction outside (0, 1].
    ConnectionError_
        A fraction given without a member capacity, or vice versa.
    """
    if analysis < 0:
        raise GeometryError(f"analysis force must be >= 0, got {analysis}")
    if fraction is not None and not 0.0 < fraction <= 1.0:
        raise GeometryError(f"fraction must be in (0, 1], got {fraction}")
    if (fraction is None) != (member_capacity is None):
        raise ConnectionError_(
            "give both member_capacity and fraction, or neither — "
            "a capacity minimum needs both halves"
        )

    if member_capacity is None:
        return DesignForce(analysis, analysis, None,
                           ForceCriterion.ANALYSIS, None, label, unit)

    if member_capacity < 0:
        raise GeometryError("member_capacity must be >= 0")

    cap_based = fraction * member_capacity        # type: ignore[operator]
    if cap_based > analysis:
        return DesignForce(cap_based, analysis, cap_based,
                           ForceCriterion.CAPACITY, fraction, label, unit)
    return DesignForce(analysis, analysis, cap_based,
                       ForceCriterion.ANALYSIS, fraction, label, unit)


# ======================================================================
# Unified result
# ======================================================================

@dataclass(frozen=True, slots=True)
class LimitState:
    """One check: what failed, how much it can take, how much it must."""

    name: str
    capacity: float          # kN or kNm
    demand: float
    clause: str = ""
    unit: str = "kN"

    @property
    def utilization(self) -> float:
        return self.demand / self.capacity if self.capacity > 0 else math.inf

    @property
    def passed(self) -> bool:
        return self.utilization <= 1.0


@dataclass(frozen=True, slots=True)
class ConnectionResult:
    """What every connection designer returns, whatever its type."""

    mark: str                       # links analytical / physical / design
    kind: str                       # "eave", "base plate", "purlin cleat", ...
    limit_states: tuple[LimitState, ...]
    forces: tuple[DesignForce, ...] = ()
    geometry: dict[str, Any] = field(default_factory=dict)
    selections: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    verified: bool = False
    citation: str = ""

    @property
    def governing(self) -> LimitState | None:
        return max(self.limit_states, key=lambda ls: ls.utilization,
                   default=None)

    @property
    def utilization(self) -> float:
        g = self.governing
        return g.utilization if g else 0.0

    @property
    def passed(self) -> bool:
        return all(ls.passed for ls in self.limit_states)

    def report(self) -> str:
        out = [f"{self.mark}  —  {self.kind}  "
               f"[{'PASS' if self.passed else 'FAIL'}]"]
        if self.citation:
            out.append(f"  {self.citation}")
        if not self.verified:
            out.append("  ** formulas UNVERIFIED — not for issue **")
        if self.forces:
            out.append("")
            out += [f.report() for f in self.forces]
        if self.selections:
            out.append("")
            out += [f"  {k:<26s} : {v}" for k, v in self.selections.items()]
        out.append("")
        for ls in sorted(self.limit_states,
                         key=lambda x: x.utilization, reverse=True):
            flag = "" if ls.passed else "   <-- FAIL"
            out.append(
                f"  {ls.name:<38s} {ls.capacity:9.2f} {ls.unit:<4s}"
                f" U = {ls.utilization:6.3f}{flag}"
            )
        g = self.governing
        if g:
            out.append(f"\n  GOVERNS: {g.name}   U = {g.utilization:.3f}")
        out += [f"  ! {w}" for w in self.warnings]
        return "\n".join(out)


@runtime_checkable
class Connection(Protocol):
    """Anything that designs a connection satisfies this."""

    def design(self) -> ConnectionResult:  # pragma: no cover
        ...


# ======================================================================
# Adapters — wrap the per-module results into ConnectionResult
# ======================================================================

def from_checks(
    *,
    mark: str,
    kind: str,
    checks: Iterable[tuple[str, float, float]],
    demand: float,
    unit: str = "kN",
    forces: tuple[DesignForce, ...] = (),
    geometry: dict[str, Any] | None = None,
    selections: dict[str, Any] | None = None,
    warnings: tuple[str, ...] = (),
    verified: bool = False,
    citation: str = "",
) -> ConnectionResult:
    """Build a :class:`ConnectionResult` from a module's ``(name, cap, U)`` list."""
    ls = tuple(
        LimitState(name=n, capacity=c, demand=demand, unit=unit)
        for n, c, _ in checks
    )
    return ConnectionResult(
        mark=mark, kind=kind, limit_states=ls, forces=forces,
        geometry=geometry or {}, selections=selections or {},
        warnings=warnings, verified=verified, citation=citation,
    )


def frame_summary(results: Iterable[ConnectionResult]) -> str:
    """One-line-per-connection table for a whole frame."""
    rows = list(results)
    if not rows:
        return "no connections"
    w = max(len(r.mark) for r in rows)
    out = [f"{'MARK':<{w}}  {'TYPE':<16} {'GOVERNING':<34} "
           f"{'U':>6}  {'':4} {'VERIFIED'}"]
    out.append("-" * (w + 74))
    for r in sorted(rows, key=lambda x: x.utilization, reverse=True):
        g = r.governing
        out.append(
            f"{r.mark:<{w}}  {r.kind:<16} {(g.name if g else '-'):<34} "
            f"{r.utilization:6.3f}  {'PASS' if r.passed else 'FAIL':4} "
            f"{'yes' if r.verified else 'NO'}"
        )
    n_fail = sum(1 for r in rows if not r.passed)
    n_unver = sum(1 for r in rows if not r.verified)
    out.append("-" * (w + 74))
    out.append(f"{len(rows)} connections · {n_fail} failing · "
               f"{n_unver} unverified")
    return "\n".join(out)
